"""MP-2 hot reload supervisor — generation fencing, drain and fail-safe fallback.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:10
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

import pytest

from cdt_autocad.contract_identity import CONTRACT_VERSION, PROTOCOL_VERSION
from cdt_autocad.hot_reload import (
    ReloadSupervisor,
    ReloadUnavailableError,
    WorkerGeneration,
    WorkerHealth,
    WorkerSpec,
)

_CONTRACT_HASH = "contract-hash-fixed"


@dataclass
class PlannedWorker:
    startup: WorkerHealth
    activation: WorkerHealth


class FakeLauncher:
    def __init__(self, plans: list[PlannedWorker]):
        self.plans = deque(plans)
        self.started: list[tuple[WorkerGeneration, dict[str, str]]] = []
        self.stopped: list[str] = []

    async def start(self, spec: WorkerSpec, env: dict[str, str]) -> WorkerGeneration:
        if not self.plans:
            raise RuntimeError("no planned worker")
        worker = WorkerGeneration(
            generation=spec.generation,
            generation_id=spec.generation_id,
            build_id=spec.build_id,
            base_url=f"http://worker-{spec.generation_id}",
            process_id=10_000 + spec.generation,
            opaque=self.plans.popleft(),
        )
        self.started.append((worker, dict(env)))
        return worker

    async def stop(self, worker: WorkerGeneration) -> None:
        self.stopped.append(worker.generation_id)

    def is_alive(self, worker: WorkerGeneration) -> bool:
        return worker.generation_id not in self.stopped


class FakeProbe:
    async def startup(self, worker: WorkerGeneration) -> WorkerHealth:
        return worker.opaque.startup

    async def activation(self, worker: WorkerGeneration) -> WorkerHealth:
        return worker.opaque.activation


def health(
    generation_id: str,
    build_id: str,
    *,
    startup_ready: bool = True,
    runtime_ready: bool = True,
    transaction_depth: int = 0,
    timeout_uncertain: bool = False,
    pending_recovery_count: int = 0,
    bridge_ready: bool = True,
    protocol_version: str = PROTOCOL_VERSION,
    contract_hash: str = _CONTRACT_HASH,
) -> WorkerHealth:
    return WorkerHealth(
        generation_id=generation_id,
        build_id=build_id,
        protocol_version=protocol_version,
        contract_version=CONTRACT_VERSION,
        contract_hash=contract_hash,
        tool_count=50,
        startup_ready=startup_ready,
        runtime_ready=runtime_ready,
        transaction_depth=transaction_depth,
        timeout_uncertain=timeout_uncertain,
        pending_recovery_count=pending_recovery_count,
        bridge_ready=bridge_ready,
        policy_fingerprint="policy-fixed",
    )


def planned(
    generation: int,
    build_id: str,
    *,
    startup_ready: bool = True,
    runtime_ready: bool = True,
    transaction_depth: int = 0,
    timeout_uncertain: bool = False,
    pending_recovery_count: int = 0,
    bridge_ready: bool = True,
    protocol_version: str = PROTOCOL_VERSION,
    contract_hash: str = _CONTRACT_HASH,
) -> PlannedWorker:
    generation_id = f"g{generation:06d}"
    return PlannedWorker(
        startup=health(
            generation_id,
            build_id,
            startup_ready=startup_ready,
            runtime_ready=False,
            bridge_ready=bridge_ready,
            protocol_version=protocol_version,
            contract_hash=contract_hash,
        ),
        activation=health(
            generation_id,
            build_id,
            startup_ready=startup_ready,
            runtime_ready=runtime_ready,
            transaction_depth=transaction_depth,
            timeout_uncertain=timeout_uncertain,
            pending_recovery_count=pending_recovery_count,
            bridge_ready=bridge_ready,
            protocol_version=protocol_version,
            contract_hash=contract_hash,
        ),
    )


@pytest.mark.asyncio
async def test_reload_fences_new_requests_and_drains_old_before_promotion():
    launcher = FakeLauncher([planned(1, "build-a"), planned(2, "build-b")])
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env={"CDT_AUTOCAD_AUTH_TOKEN": "secret"},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=False,
        drain_timeout_seconds=1.0,
    )
    await supervisor.bootstrap("build-a")

    lease = supervisor.lease()
    old = await lease.__aenter__()
    assert old.generation_id == "g000001"

    reload_task = asyncio.create_task(supervisor.reload("build-b"))
    await supervisor.wait_for_phase("draining", timeout=1.0)
    with pytest.raises(ReloadUnavailableError):
        async with supervisor.lease():
            pass

    await lease.__aexit__(None, None, None)
    result = await reload_task

    assert result.ok is True
    assert result.old_generation == "g000001"
    assert result.new_generation == "g000002"
    assert launcher.stopped == ["g000001"]
    async with supervisor.lease() as current:
        assert current.generation_id == "g000002"


@pytest.mark.asyncio
async def test_failed_candidate_startup_health_preserves_last_healthy_generation():
    launcher = FakeLauncher(
        [
            planned(1, "build-a"),
            planned(2, "build-b", startup_ready=False),
        ]
    )
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env={},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=False,
    )
    await supervisor.bootstrap("build-a")

    result = await supervisor.reload("build-b")

    assert result.ok is False
    assert result.error_code == "STARTUP_HEALTH_FAILED"
    assert supervisor.status()["active_generation"] == "g000001"
    assert launcher.stopped == ["g000002"]
    async with supervisor.lease() as current:
        assert current.build_id == "build-a"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("protocol_version", "contract_hash"),
    [
        ("MCP-MISMATCH", _CONTRACT_HASH),
        (PROTOCOL_VERSION, "contract-hash-mismatch"),
    ],
)
async def test_candidate_protocol_or_contract_hash_mismatch_blocks_promotion(
    protocol_version: str,
    contract_hash: str,
):
    launcher = FakeLauncher(
        [
            planned(1, "build-a"),
            planned(
                2,
                "build-b",
                protocol_version=protocol_version,
                contract_hash=contract_hash,
            ),
        ]
    )
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env={},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=False,
    )
    await supervisor.bootstrap("build-a")

    result = await supervisor.reload("build-b")

    assert result.ok is False
    assert result.error_code == "STARTUP_HEALTH_FAILED"
    assert supervisor.status()["active_generation"] == "g000001"
    assert launcher.stopped == ["g000002"]


@pytest.mark.asyncio
async def test_drain_timeout_preserves_old_generation_and_reopens_gate():
    launcher = FakeLauncher([planned(1, "build-a"), planned(2, "build-b")])
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env={},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=False,
        drain_timeout_seconds=0.02,
    )
    await supervisor.bootstrap("build-a")
    lease = supervisor.lease()
    await lease.__aenter__()

    result = await supervisor.reload("build-b")

    assert result.ok is False
    assert result.error_code == "DRAIN_TIMEOUT"
    assert supervisor.status()["active_generation"] == "g000001"
    await lease.__aexit__(None, None, None)
    async with supervisor.lease() as current:
        assert current.generation_id == "g000001"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transaction_depth", "pending_recovery_count", "timeout_uncertain", "error_code"),
    [
        (1, 0, False, "OLD_GENERATION_UNSAFE"),
        (0, 1, False, "OLD_GENERATION_UNSAFE"),
        (0, 0, True, "OLD_GENERATION_UNSAFE"),
    ],
)
async def test_pending_transaction_recovery_or_uncertainty_blocks_activation(
    transaction_depth: int,
    pending_recovery_count: int,
    timeout_uncertain: bool,
    error_code: str,
):
    launcher = FakeLauncher([planned(1, "build-a"), planned(2, "build-b")])
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env={},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=False,
    )
    await supervisor.bootstrap("build-a")
    old_worker = launcher.started[0][0]
    old_worker.opaque.activation = health(
        "g000001",
        "build-a",
        transaction_depth=transaction_depth,
        pending_recovery_count=pending_recovery_count,
        timeout_uncertain=timeout_uncertain,
    )

    result = await supervisor.reload("build-b")

    assert result.ok is False
    assert result.error_code == error_code
    assert supervisor.status()["active_generation"] == "g000001"
    assert "g000002" in launcher.stopped


@pytest.mark.asyncio
async def test_twenty_successes_and_ten_injected_failures_keep_one_authoritative_generation():
    plans = [planned(1, "build-0")]
    for attempt in range(1, 31):
        generation = attempt + 1
        plans.append(
            planned(
                generation,
                f"build-{attempt}",
                startup_ready=attempt % 3 != 0,
            )
        )
    launcher = FakeLauncher(plans)
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env={},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=False,
    )
    await supervisor.bootstrap("build-0")

    successes = 0
    failures = 0
    active_generations: list[str] = []
    for attempt in range(1, 31):
        result = await supervisor.reload(f"build-{attempt}")
        successes += int(result.ok)
        failures += int(not result.ok)
        status = supervisor.status()
        active_generations.append(status["active_generation"])
        assert status["accepting_requests"] is True
        assert status["active_request_count"] == 0
        async with supervisor.lease() as current:
            assert current.generation_id == status["active_generation"]

    assert successes == 20
    assert failures == 10
    assert supervisor.status()["reload_successes"] == 20
    assert supervisor.status()["reload_failures"] == 10
    assert len(active_generations) == 30


@pytest.mark.asyncio
async def test_telemetry_failure_cannot_change_reload_result():
    class FailingSink:
        def emit(self, _event):
            raise RuntimeError("telemetry unavailable")

    launcher = FakeLauncher([planned(1, "build-a"), planned(2, "build-b")])
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env={},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=False,
        event_sink=FailingSink(),
    )
    await supervisor.bootstrap("build-a")

    result = await supervisor.reload("build-b")

    assert result.ok is True
    assert supervisor.event_dropped_count > 0
    assert supervisor.status()["active_generation"] == "g000002"


@pytest.mark.asyncio
async def test_frozen_auth_and_roots_are_reused_for_every_generation():
    launcher = FakeLauncher([planned(1, "build-a"), planned(2, "build-b")])
    original_env = {
        "CDT_AUTOCAD_AUTH_TOKEN": "token-a",
        "CDT_AUTOCAD_ALLOWED_PATHS": "/safe/root",
        "CDT_AUTOCAD_BACKEND": "ezdxf",
    }
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env=original_env,
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=False,
    )
    original_env["CDT_AUTOCAD_AUTH_TOKEN"] = "token-b"
    original_env["CDT_AUTOCAD_ALLOWED_PATHS"] = "/unsafe/new-root"

    await supervisor.bootstrap("build-a")
    result = await supervisor.reload("build-b")

    assert result.ok is True
    assert len(launcher.started) == 2
    for _worker, env in launcher.started:
        assert env["CDT_AUTOCAD_AUTH_TOKEN"] == "token-a"
        assert env["CDT_AUTOCAD_ALLOWED_PATHS"] == "/safe/root"
        assert env["CDT_AUTOCAD_RUNTIME_POLICY_FP"] == "policy-fixed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pending_recovery_count", "bridge_ready", "error_code"),
    [
        (1, True, "ACTIVATION_HEALTH_FAILED"),
        (0, False, "ACTIVATION_HEALTH_FAILED"),
    ],
)
async def test_candidate_recovery_or_required_bridge_blocks_promotion(
    pending_recovery_count: int,
    bridge_ready: bool,
    error_code: str,
):
    launcher = FakeLauncher(
        [
            planned(1, "build-a", bridge_ready=True),
            planned(
                2,
                "build-b",
                pending_recovery_count=pending_recovery_count,
                bridge_ready=bridge_ready,
            ),
        ]
    )
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=FakeProbe(),
        frozen_env={},
        policy_fingerprint="policy-fixed",
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=_CONTRACT_HASH,
        expected_tool_count=50,
        require_bridge=True,
    )
    await supervisor.bootstrap("build-a")

    result = await supervisor.reload("build-b")

    assert result.ok is False
    assert result.error_code == error_code
    assert supervisor.status()["active_generation"] == "g000001"
    assert "g000002" in launcher.stopped
