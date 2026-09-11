"""MP-2 subprocess integration — real stateless FastMCP worker replacement.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:42
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from cdt_autocad.contract_identity import CONTRACT_VERSION, PROTOCOL_VERSION, PUBLIC_TOOL_COUNT, contract_hash
from cdt_autocad.hot_reload import ReloadSupervisor, WorkerGeneration, WorkerHealth
from cdt_autocad.runtime_identity import policy_fingerprint
from cdt_autocad.supervisor import McpWorkerProbe, SubprocessWorkerLauncher, source_build_id
from scripts import run_hot_reload_acceptance as acceptance_runner


def test_windows_acceptance_cleanup_terminates_supervisor_process_tree(monkeypatch):
    calls: list[list[str]] = []

    class FakeProcess:
        pid = 4321
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout):
            assert timeout == 10
            self.returncode = 1
            return self.returncode

        def kill(self):
            raise AssertionError("fallback kill should not be required")

    def fake_run(args, **kwargs):
        calls.append(list(args))
        assert kwargs["timeout"] == 10
        assert kwargs["check"] is False
        return None

    monkeypatch.setattr(acceptance_runner.sys, "platform", "win32")
    monkeypatch.setattr(acceptance_runner.subprocess, "run", fake_run)

    acceptance_runner._stop_supervisor_process(FakeProcess())

    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]


def test_posix_acceptance_cleanup_terminates_supervisor_process_group(monkeypatch):
    calls: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 4321
        returncode = None

        def poll(self):
            return None

        def wait(self, timeout):
            assert timeout == 10
            self.returncode = -15
            return self.returncode

        def kill(self):
            raise AssertionError("fallback kill should not be required")

    def fake_killpg(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr(acceptance_runner.sys, "platform", "linux")
    monkeypatch.setattr(acceptance_runner.os, "killpg", fake_killpg, raising=False)

    acceptance_runner._stop_supervisor_process(FakeProcess())

    assert calls == [(4321, acceptance_runner.signal.SIGTERM)]


@pytest.mark.asyncio
async def test_startup_probe_bounds_each_attempt_and_retries_after_hung_snapshot(monkeypatch):
    probe = McpWorkerProbe(
        auth_token="",
        startup_timeout_seconds=0.2,
        retry_interval_seconds=0.001,
        attempt_timeout_seconds=0.02,
    )
    worker = WorkerGeneration(
        generation=1,
        generation_id="g000001",
        build_id="build-a",
        base_url="http://127.0.0.1:1",
    )
    expected = WorkerHealth(
        generation_id="g000001",
        build_id="build-a",
        protocol_version=PROTOCOL_VERSION,
        contract_version=CONTRACT_VERSION,
        contract_hash=contract_hash(),
        tool_count=PUBLIC_TOOL_COUNT,
        startup_ready=True,
        runtime_ready=False,
        transaction_depth=0,
        timeout_uncertain=False,
        pending_recovery_count=0,
        bridge_ready=True,
        policy_fingerprint="policy-fixed",
    )
    calls = 0

    async def fake_snapshot(_worker, *, activate_runtime, request_timeout_seconds):
        nonlocal calls
        calls += 1
        assert activate_runtime is False
        assert request_timeout_seconds <= 0.02
        if calls == 1:
            await asyncio.sleep(0.1)
        return expected

    monkeypatch.setattr(probe, "_snapshot", fake_snapshot)

    result = await probe.startup(worker)

    assert result is expected
    assert calls == 2


@pytest.mark.asyncio
async def test_real_worker_process_bootstrap_and_three_reload_generations(tmp_path: Path):
    repo_root = Path(__file__).resolve().parents[1]
    frozen_env = dict(os.environ)
    frozen_env.update(
        {
            "CDT_AUTOCAD_AUTH_TOKEN": "mp2-process-test-token",
            "CDT_AUTOCAD_ALLOWED_PATHS": str(tmp_path),
            "CDT_AUTOCAD_BACKEND": "ezdxf",
            "CDT_AUTOCAD_ALLOW_REMOTE_HTTP": "false",
        }
    )
    policy_fp = policy_fingerprint(frozen_env)
    launcher = SubprocessWorkerLauncher(
        repo_root=repo_root,
        log_dir=tmp_path / "worker-logs",
        port_start=18310,
        port_end=18330,
    )
    probe = McpWorkerProbe(
        auth_token=frozen_env["CDT_AUTOCAD_AUTH_TOKEN"],
        startup_timeout_seconds=15.0,
        retry_interval_seconds=0.05,
    )
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=probe,
        frozen_env=frozen_env,
        policy_fingerprint=policy_fp,
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=contract_hash(),
        expected_tool_count=PUBLIC_TOOL_COUNT,
        require_bridge=False,
        drain_timeout_seconds=2.0,
    )
    build_id = source_build_id(repo_root)

    try:
        initial = await supervisor.bootstrap(build_id)
        assert initial.generation_id == "g000001"
        assert launcher.is_alive(initial)
        initial_health = await probe.activation(initial)
        assert initial_health.runtime_ready is True
        assert initial_health.protocol_version == PROTOCOL_VERSION
        assert initial_health.contract_version == CONTRACT_VERSION
        assert initial_health.contract_hash == contract_hash()
        assert initial_health.tool_count == PUBLIC_TOOL_COUNT
        assert initial_health.policy_fingerprint == policy_fp

        seen_pids = {initial.process_id}
        for expected_generation in range(2, 5):
            result = await supervisor.reload(build_id)
            assert result.ok is True
            status = supervisor.status()
            assert status["active_generation"] == f"g{expected_generation:06d}"
            async with supervisor.lease() as current:
                assert current.process_id not in seen_pids
                seen_pids.add(current.process_id)
                current_health = await probe.activation(current)
                assert current_health.generation_id == current.generation_id
                assert current_health.build_id == build_id
                assert current_health.runtime_ready is True
                assert current_health.pending_recovery_count == 0
    finally:
        await supervisor.shutdown()

    assert len(seen_pids) == 4
