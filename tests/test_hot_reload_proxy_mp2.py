"""MP-2 stable endpoint proxy — auth invariants and deterministic reload refusal.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:28
"""

from __future__ import annotations

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


@pytest.mark.asyncio
async def test_proxy_enforces_bearer_before_forwarding_and_preserves_headers():
    seen = {}

    async def upstream(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["mcp_session_id"] = request.headers.get("mcp-session-id")
        seen["body"] = await request.aread()
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "x-worker": "g000007"},
            content=b'{"ok":true}',
        )

    upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    app = SupervisorProxyApp(
        FakeSupervisor(),
        auth_token="top-secret",
        upstream_client=upstream_client,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
        unauthorized = await client.post("/mcp", json={"x": 1})
        authorized = await client.post(
            "/mcp?trace=1",
            content=b'{"jsonrpc":"2.0"}',
            headers={
                "authorization": "Bearer top-secret",
                "mcp-session-id": "session-1",
                "content-type": "application/json",
            },
        )

    await app.aclose()
    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.headers["x-worker"] == "g000007"
    assert seen == {
        "url": "http://worker.internal:18107/mcp?trace=1",
        "authorization": "Bearer top-secret",
        "mcp_session_id": "session-1",
        "body": b'{"jsonrpc":"2.0"}',
    }


@pytest.mark.asyncio
async def test_proxy_refuses_new_request_during_reload_without_hitting_worker():
    calls = 0

    async def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=b"unexpected")

    app = SupervisorProxyApp(
        FakeSupervisor(accepting=False),
        auth_token="top-secret",
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
        response = await client.post(
            "/mcp",
            json={},
            headers={"authorization": "Bearer top-secret"},
        )

    await app.aclose()
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"] == "reload_in_progress"
    assert calls == 0


@pytest.mark.asyncio
async def test_supervisor_status_endpoint_is_authenticated_and_does_not_probe_upstream():
    app = SupervisorProxyApp(
        FakeSupervisor(),
        auth_token="top-secret",
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
        denied = await client.get("/__cdt/status")
        allowed = await client.get(
            "/__cdt/status",
            headers={"authorization": "Bearer top-secret"},
        )

    await app.aclose()
    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["active_generation"] == "g000007"
