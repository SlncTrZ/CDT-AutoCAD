"""HTTP admission limits — bounded concurrency, request budget and upstream timeout.

Wing: code | Topic: mp2-hot-reload | Updated: 2026-10-05 21:16

Offline acceptance for the review follow-up: the :8000 facade must refuse
over-admission deterministically (503/413/504) instead of piling unbounded
work onto one worker or hanging on a dead worker. No live AutoCAD required.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from cdt_autocad.hot_reload import ReloadUnavailableError, WorkerGeneration
from cdt_autocad.supervisor import SupervisorProxyApp


class FakeSupervisor:
    def __init__(self, *, accepting: bool = True):
        self.accepting = accepting
        self.worker = WorkerGeneration(
            generation=7,
            generation_id="g000007",
            build_id="sha256:build",
            base_url="http://worker.internal:18107",
            process_id=707,
        )

    @asynccontextmanager
    async def lease(self):
        if not self.accepting:
            raise ReloadUnavailableError("draining")
        yield self.worker

    def status(self):
        return {
            "phase": "ready" if self.accepting else "draining",
            "active_generation": "g000007",
            "accepting_requests": self.accepting,
        }


def _authed(path: str = "/mcp") -> dict[str, str]:
    return {"authorization": "Bearer top-secret"}


@pytest.mark.asyncio
async def test_second_concurrent_request_refused_when_facade_is_full():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_upstream(_request: httpx.Request) -> httpx.Response:
        entered.set()
        await release.wait()
        return httpx.Response(200, content=b'{"ok":true}')

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(slow_upstream))
    app = SupervisorProxyApp(
        FakeSupervisor(),
        auth_token="top-secret",
        upstream_client=upstream_client,
        max_concurrent_requests=1,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
            first = asyncio.create_task(client.post("/mcp", content=b"{}", headers=_authed()))
            await asyncio.wait_for(entered.wait(), timeout=5.0)
            refused = await client.post("/mcp", content=b"{}", headers=_authed())
            assert refused.status_code == 503
            assert refused.json()["error"] == "concurrency_limit_exceeded"
            assert refused.headers["retry-after"] == "1"
            release.set()
            completed = await asyncio.wait_for(first, timeout=5.0)
            assert completed.status_code == 200
            # Slot is released: the facade serves again after drain.
            retry = await client.post("/mcp", content=b"{}", headers=_authed())
            assert retry.status_code == 200
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_oversize_body_refused_before_worker_lease():
    calls = 0

    async def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"unexpected")

    app = SupervisorProxyApp(
        FakeSupervisor(),
        auth_token="top-secret",
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
        max_request_body_bytes=16,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
            response = await client.post("/mcp", content=b"x" * 17, headers=_authed())
            assert response.status_code == 413
            assert response.json()["error"] == "request_too_large"
            assert calls == 0
            small = await client.post("/mcp", content=b"x" * 16, headers=_authed())
            assert small.status_code == 200
            assert calls == 1
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_dead_worker_fails_closed_with_upstream_timeout_not_hang():
    async def hung_upstream(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30.0)
        return httpx.Response(200, content=b"too-late")

    app = SupervisorProxyApp(
        FakeSupervisor(),
        auth_token="top-secret",
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(hung_upstream)),
        request_timeout_seconds=0.2,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
            response = await asyncio.wait_for(
                client.post("/mcp", content=b"{}", headers=_authed()),
                timeout=10.0,
            )
            assert response.status_code == 504
            assert response.json()["error"] == "upstream_timeout"
            assert app.admission_limits()["inflight_requests"] == 0
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_status_exposes_admission_limits_and_inflight():
    app = SupervisorProxyApp(
        FakeSupervisor(),
        auth_token="top-secret",
        upstream_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200))
        ),
        max_concurrent_requests=4,
        max_request_body_bytes=1024,
        request_timeout_seconds=30.0,
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
            response = await client.get("/__cdt/status", headers=_authed())
            assert response.status_code == 200
            admission = response.json()["admission"]
            assert admission == {
                "max_concurrent_requests": 4,
                "max_request_body_bytes": 1024,
                "request_timeout_seconds": 30.0,
                "inflight_requests": 0,
            }
            # Legacy status contract is preserved alongside admission.
            assert response.json()["active_generation"] == "g000007"
    finally:
        await app.aclose()


def test_admission_constructor_rejects_non_positive_bounds():
    supervisor = FakeSupervisor()
    for kwargs in (
        {"max_concurrent_requests": 0},
        {"max_request_body_bytes": -1},
        {"request_timeout_seconds": 0.0},
    ):
        with pytest.raises(ValueError):
            SupervisorProxyApp(supervisor, auth_token="x", **kwargs)
