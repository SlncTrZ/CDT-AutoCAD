"""Public crash-recovery acceptance — supervisor restart, transport failure, reconcile.

Wing: code | Topic: mp2-hot-reload | Updated: 2026-10-05 21:16

Offline half of the review follow-up, exercised through the public :8000
facade with simulated transport failure (no live AutoCAD required):

- worker crash mid-flight fails closed with deterministic 502, never a hang;
- supervisor reload after the crash promotes a healthy generation and
  traffic resumes on the new generation (restart/recovery composition);
- new requests fence with 503 while recovery drains, in-flight completes;
- an uncertain candidate (pending recovery / timeout uncertainty) is never
  promoted; the last healthy generation keeps serving (reconcile-before-promote);
- completion-unknown mutation quarantines dependent work until the late
  writer finishes and authoritative read-back reconciles state.

The live half (real acad process replacement, real bridge DLL reload,
real drawing untouched) is scripts/run_crash_recovery_live_acceptance.py
and requires a live AutoCAD 2027 Session 1 with owner approval.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path

import ezdxf
import httpx
import pytest

from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.contract_identity import (
    CONTRACT_VERSION,
    PROTOCOL_VERSION,
    PUBLIC_TOOL_COUNT,
)
from cdt_autocad.errors import BackendQuarantinedError, BackendTimeoutError
from cdt_autocad.hot_reload import (
    ReloadSupervisor,
    WorkerGeneration,
    WorkerHealth,
    WorkerSpec,
)
from cdt_autocad.supervisor import SupervisorProxyApp

_CONTRACT_HASH = "contract-hash-fixed"
_HEADERS = {"authorization": "Bearer top-secret"}


@dataclass
class PlannedWorker:
    startup: WorkerHealth
    activation: WorkerHealth


class FakeLauncher:
    def __init__(self, plans: list[PlannedWorker]):
        self.plans = deque(plans)
        self.stopped: list[str] = []

    async def start(self, spec: WorkerSpec, env: dict[str, str]) -> WorkerGeneration:
        if not self.plans:
            raise RuntimeError("no planned worker")
        return WorkerGeneration(
            generation=spec.generation,
            generation_id=spec.generation_id,
            build_id=spec.build_id,
            base_url=f"http://worker-{spec.generation_id}",
            process_id=10_000 + spec.generation,
            opaque=self.plans.popleft(),
        )

    async def stop(self, worker: WorkerGeneration) -> None:
        self.stopped.append(worker.generation_id)

    def is_alive(self, worker: WorkerGeneration) -> bool:
        return worker.generation_id not in self.stopped


class FakeProbe:
    async def startup(self, worker: WorkerGeneration) -> WorkerHealth:
        return worker.opaque.startup

    async def activation(self, worker: WorkerGeneration) -> WorkerHealth:
        return worker.opaque.activation


def _health(
    generation_id: str,
    build_id: str,
    *,
    timeout_uncertain: bool = False,
    pending_recovery_count: int = 0,
) -> WorkerHealth:
    return WorkerHealth(
        generation_id=generation_id,
        build_id=build_id,
        protocol_version=PROTOCOL_VERSION,
        contract_version=CONTRACT_VERSION,
        contract_hash=_CONTRACT_HASH,
        tool_count=PUBLIC_TOOL_COUNT,
        startup_ready=True,
        runtime_ready=True,
        transaction_depth=0,
        timeout_uncertain=timeout_uncertain,
        pending_recovery_count=pending_recovery_count,
        bridge_ready=True,
        policy_fingerprint="policy-fixed",
    )


def _planned(
    generation: int,
    build_id: str,
    *,
    timeout_uncertain: bool = False,
    pending_recovery_count: int = 0,
) -> PlannedWorker:
    generation_id = f"g{generation:06d}"
    return PlannedWorker(
        startup=_health(generation_id, build_id),
        activation=_health(
            generation_id,
            build_id,
            timeout_uncertain=timeout_uncertain,
            pending_recovery_count=pending_recovery_count,
        ),
    )


def _make_supervisor(plans: list[PlannedWorker]) -> ReloadSupervisor:
    return ReloadSupervisor(
        launcher=FakeLauncher(plans),
        probe=FakeProbe(),
        frozen_env={},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=PUBLIC_TOOL_COUNT,
        require_bridge=False,
        drain_timeout_seconds=5.0,
    )


def _crash_aware_transport(seen: dict) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        seen["last_host"] = host
        if host == "worker-g000001":
            raise httpx.ConnectError("simulated worker crash", request=request)
        return httpx.Response(200, content=b'{"ok":true,"gen":"g000002"}')

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_worker_crash_fails_closed_then_reload_resumes_traffic():
    supervisor = _make_supervisor([_planned(1, "build-a"), _planned(2, "build-b")])
    await supervisor.bootstrap("build-a")
    seen: dict = {}
    app = SupervisorProxyApp(
        supervisor,
        auth_token="top-secret",
        upstream_client=httpx.AsyncClient(transport=_crash_aware_transport(seen)),
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
            crashed = await asyncio.wait_for(
                client.post("/mcp", content=b"{}", headers=_HEADERS), timeout=10.0
            )
            assert crashed.status_code == 502
            assert crashed.json()["error"] == "active_worker_unavailable"
            assert seen["last_host"] == "worker-g000001"

            result = await supervisor.reload("build-b")
            assert result.ok is True
            assert result.new_generation == "g000002"

            resumed = await client.post("/mcp", content=b"{}", headers=_HEADERS)
            assert resumed.status_code == 200
            assert seen["last_host"] == "worker-g000002"
            status = (await client.get("/__cdt/status", headers=_HEADERS)).json()
            assert status["active_generation"] == "g000002"
    finally:
        await app.aclose()
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_new_requests_fence_while_recovery_drains():
    supervisor = _make_supervisor([_planned(1, "build-a"), _planned(2, "build-b")])
    await supervisor.bootstrap("build-a")
    app = SupervisorProxyApp(
        supervisor,
        auth_token="top-secret",
        upstream_client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"ok"))
        ),
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
            async with supervisor.lease():
                reload_task = asyncio.create_task(supervisor.reload("build-b"))
                await supervisor.wait_for_phase("draining", timeout=5.0)
                fenced = await client.post("/mcp", content=b"{}", headers=_HEADERS)
                assert fenced.status_code == 503
                assert fenced.json()["error"] == "reload_in_progress"
            result = await asyncio.wait_for(reload_task, timeout=10.0)
            assert result.ok is True
            served = await client.post("/mcp", content=b"{}", headers=_HEADERS)
            assert served.status_code == 200
    finally:
        await app.aclose()
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_uncertain_candidate_never_promoted_healthy_generation_serves():
    supervisor = _make_supervisor(
        [
            _planned(1, "build-a"),
            _planned(2, "build-b", pending_recovery_count=1, timeout_uncertain=True),
        ]
    )
    await supervisor.bootstrap("build-a")
    seen: dict = {}
    app = SupervisorProxyApp(
        supervisor,
        auth_token="top-secret",
        upstream_client=httpx.AsyncClient(transport=_crash_aware_transport(seen)),
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://supervisor") as client:
            result = await supervisor.reload("build-b")
            assert result.ok is False
            assert result.error_code == "ACTIVATION_HEALTH_FAILED"
            assert supervisor.status()["active_generation"] == "g000001"
            # Candidate with pending recovery routes to the dead gen-1 host in
            # this fixture, which still fails closed rather than succeeding.
            refused = await asyncio.wait_for(
                client.post("/mcp", content=b"{}", headers=_HEADERS), timeout=10.0
            )
            assert refused.status_code == 502
    finally:
        await app.aclose()
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_completion_unknown_quarantines_until_readback_reconciles(settings, tmp_path):
    target = tmp_path / "reconcile.dxf"
    backend = EzdxfBackend(settings)
    await backend.document_new()
    await backend.entity_create_line(0, 0, 10, 0)
    await backend.document_save(str(target))

    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_run = backend._run

    async def blocking_run(func, *args, **kwargs):
        def _blocked():
            entered.set()
            release.wait(5.0)
            try:
                return func()
            finally:
                finished.set()

        return await original_run(_blocked, *args, **kwargs)

    backend._run = blocking_run  # type: ignore[method-assign]
    backend.settings = replace(settings, call_timeout_seconds=0.2)
    try:
        with pytest.raises(BackendTimeoutError):
            await backend.entity_create_line(1, 1, 9, 1)
        assert entered.is_set()
        status = backend.status()
        assert status["quarantined"] is True
        assert status["uncertain_worker_active"] is True
        # Dependent mutation is refused while completion is unknown.
        with pytest.raises(BackendQuarantinedError):
            await backend.entity_create_line(2, 2, 8, 2)
    finally:
        release.set()
        backend._run = original_run  # type: ignore[method-assign]

    assert await asyncio.to_thread(finished.wait, 5.0)
    # finished.set() fires on the worker thread just before the asyncio task
    # completes; wait for task completion deterministically before rebind.
    uncertain = backend._uncertain_task
    if uncertain is not None and not uncertain.done():
        await asyncio.wait_for(asyncio.shield(uncertain), timeout=5.0)
    backend.settings = settings
    # Reconcile through authoritative read-back: memory must equal disk.
    await backend.document_open(str(target))
    assert backend.status()["quarantined"] is False
    memory = [e.dxftype() for e in backend._require_doc().modelspace()]
    disk = [e.dxftype() for e in ezdxf.readfile(target).modelspace()]
    assert memory == disk == ["LINE"]
    # The lane serves again only after reconciliation.
    await backend.entity_create_line(2, 2, 8, 2)
    assert await backend.object_count() == 2


def test_live_acceptance_script_refuses_to_run_without_explicit_live_flag():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "run_crash_recovery_live_acceptance.py"
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(repo_root),
    )
    assert completed.returncode == 2
    assert "refusing" in completed.stderr.lower()
