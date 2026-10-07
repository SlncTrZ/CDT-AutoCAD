"""R2 remote path — transport/agent/remote-adapter gates.
Wing: code | Topic: migration-r2-remote | Updated: 2026-10-07 15:45
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from cdt_autocad.backends.local_runtime import LocalAutoCADRuntimeAdapter
from cdt_autocad.backends.remote_runtime import RemoteAutoCADRuntimeAdapter
from cdt_autocad.backends.runtime_port import AutoCADRuntimePort
from cdt_autocad.contract_identity import PUBLIC_TOOL_COUNT
from cdt_autocad.errors import (
    BackendQuarantinedError,
    MutationCompletionUncertainError,
    StateConflictError,
)
from cdt_autocad.models import EntityInfo
from cdt_autocad.mutation_coordinator import MutationCoordinator
from cdt_autocad.runtime_transport import (
    ALLOWED_OPS,
    LocalRuntimeTransport,
    RemoteRuntimeTransport,
    RuntimeAuthError,
    RuntimeGenerationMismatchError,
    RuntimeOpRefusedError,
    RuntimeUnavailableError,
    RuntimeUncertainError,
)
from cdt_autocad.server import create_mcp
from cdt_autocad.workstation_agent import WorkstationAgentConfig, WorkstationRuntimeAgent

_TOKEN = "r2-test-token"


class _FakePort:
    """Minimal port stand-in: records dispatch, injects delay, no CAD semantics."""

    def __init__(self, coordinator: MutationCoordinator, settings: Any = None) -> None:
        self._mutation_coordinator = coordinator
        self.settings = settings
        self.calls: list[str] = []
        self.delays: dict[str, float] = {}

    @property
    def backend(self) -> Any:
        return None

    @property
    def mutation_coordinator(self) -> MutationCoordinator:
        return self._mutation_coordinator

    @property
    def name(self) -> str:
        return "com"

    @property
    def runtime_available(self) -> bool:
        return True

    def capabilities(self) -> dict[str, dict[str, Any]]:
        return {"common.object.query": {"supported": True}}

    def status(self) -> dict[str, Any]:
        return {"backend": "com", "runtime_available": True, "connected": True}

    def runtime_status(self) -> dict[str, Any]:
        return self.status()

    def health(self) -> dict[str, Any]:
        return {"backend": "com", "runtime_available": True, "quarantined": False}

    async def _maybe_slow(self, op: str) -> None:
        delay = self.delays.get(op, 0.0)
        if delay > 0:
            await asyncio.sleep(delay)
        self.calls.append(op)

    async def document_info(self) -> dict[str, Any]:
        await self._maybe_slow("document_info")
        return {"backend": "com", "document": "fake", "units": "mm"}

    async def object_count(self, *args: Any) -> int:
        await self._maybe_slow("object_count")
        return 3

    async def drawing_extents(self) -> dict[str, Any]:
        await self._maybe_slow("drawing_extents")
        return {"ok": True}

    async def object_list(self, *args: Any) -> list[EntityInfo]:
        await self._maybe_slow("object_list")
        return [
            EntityInfo(
                id="AAA",
                type="LINE",
                layer="0",
                color=7,
                linetype="ByLayer",
                visible=True,
                properties={},
            )
        ]

    async def object_delete(self, object_id: str) -> dict[str, Any]:
        await self._maybe_slow("object_delete")
        return {"ok": True, "deleted": object_id}


@pytest.fixture
def live_pair(settings):
    """Agent over a fake port + remote transport client, loopback, started/stopped."""
    coordinator = MutationCoordinator()
    port = _FakePort(coordinator, settings)
    assert isinstance(port, AutoCADRuntimePort)
    agent = WorkstationRuntimeAgent(
        port, WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN)
    )
    base_url = agent.start()
    transport = RemoteRuntimeTransport(base_url, _TOKEN)
    yield agent, port, transport, coordinator
    agent.stop()


def test_allowlist_refused_before_dispatch(settings):
    coordinator = MutationCoordinator()
    port = _FakePort(coordinator, settings)
    local = LocalRuntimeTransport(port)
    with pytest.raises(RuntimeOpRefusedError):
        asyncio.run(local.call("rm -rf", (), {}))
    agent = WorkstationRuntimeAgent(
        port, WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN)
    )
    envelope = agent.dispatch("rm -rf", [], {})
    assert envelope["ok"] is False
    assert envelope["error_code"] == "unknown_op"
    assert port.calls == []


def test_oversized_request_and_deadline_bounds_refused(settings):
    coordinator = MutationCoordinator()
    port = _FakePort(coordinator, settings)
    local = LocalRuntimeTransport(port)
    # Oversized payloads are refused at wire encoding, before any I/O —
    # no listener is needed because to_wire() runs before connect.
    remote = RemoteRuntimeTransport(_unused_loopback_url(), _TOKEN)
    with pytest.raises(RuntimeOpRefusedError, match="oversized"):
        asyncio.run(remote.call("object_count", ("x" * 300_000,), {}))
    with pytest.raises(ValueError, match="deadline_ms"):
        asyncio.run(local.call("document_info", (), {}, deadline_ms=1))
    with pytest.raises(ValueError, match="deadline_ms"):
        asyncio.run(local.call("document_info", (), {}, deadline_ms=999_999_999))


def _unused_loopback_url() -> str:
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    finally:
        sock.close()
    return f"http://127.0.0.1:{port}"


def test_remote_unavailable_is_typed(monkeypatch, settings):
    import cdt_autocad.runtime_transport as transport_mod

    # Provably-nothing-listening (pre-flight refused) must surface as typed
    # unavailable BEFORE any dispatch — never success, never uncertain.
    monkeypatch.setattr(transport_mod, "_preflight_connect", lambda *a: "refused")
    transport = RemoteRuntimeTransport(_unused_loopback_url(), _TOKEN)
    with pytest.raises(RuntimeUnavailableError):
        asyncio.run(transport.call("document_info", (), {}, deadline_ms=2000))
    with pytest.raises(RuntimeUnavailableError):
        asyncio.run(transport.health())


def test_dead_endpoint_never_returns_success(settings):
    # Live stopped-agent probe: whatever the host TCP behavior (RST or
    # blackhole), the client must raise a typed transport error and must
    # never fabricate a successful result.
    from cdt_autocad.runtime_transport import RuntimeTransportError

    coordinator = MutationCoordinator()
    port = _FakePort(coordinator, settings)
    agent = WorkstationRuntimeAgent(
        port, WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN)
    )
    base_url = agent.start()
    agent.stop()
    transport = RemoteRuntimeTransport(base_url, _TOKEN)
    with pytest.raises(RuntimeTransportError):
        asyncio.run(transport.call("document_info", (), {}, deadline_ms=1500))


def test_non_loopback_refused():
    with pytest.raises(RuntimeOpRefusedError, match="non-loopback"):
        RemoteRuntimeTransport("http://192.168.1.227:8000", _TOKEN)
    with pytest.raises(ValueError, match="non-loopback"):
        WorkstationAgentConfig(host="192.168.1.227", port=0, auth_token=_TOKEN)
    with pytest.raises(ValueError, match="auth_token"):
        WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token="")
    with pytest.raises(ValueError, match="auth_token"):
        RemoteRuntimeTransport("http://127.0.0.1:9", "")


def test_auth_refused_typed(live_pair):
    agent, _port, _transport, _coordinator = live_pair
    bad = RemoteRuntimeTransport(agent.base_url, "wrong-token")
    with pytest.raises(RuntimeAuthError):
        asyncio.run(bad.call("document_info", (), {}))
    with pytest.raises(RuntimeAuthError):
        asyncio.run(bad.health())


def test_health_heartbeat_generation(live_pair):
    _agent, _port, transport, _coordinator = live_pair
    health = asyncio.run(transport.health())
    assert health["generation"].startswith("gen-")
    assert health["session"]["pid"] > 0
    assert health["adapter"]["backend"] == "com"


def test_read_parity_over_loopback(live_pair):
    agent, port, transport, _coordinator = live_pair
    direct = asyncio.run(port.document_info())
    remote = asyncio.run(transport.call("document_info", (), {}))
    assert remote == direct
    assert asyncio.run(transport.call("object_count", (None, None), {})) == 3


def test_generation_mismatch_discards_result(live_pair):
    agent, port, transport, _coordinator = live_pair
    pinned = RemoteRuntimeTransport(agent.base_url, _TOKEN)
    before = list(port.calls)
    with pytest.raises(RuntimeGenerationMismatchError):
        asyncio.run(
            pinned.call("document_info", (), {}, expected_generation="gen-stale")
        )
    assert port.calls == before  # refused BEFORE adapter dispatch
    assert isinstance(RuntimeGenerationMismatchError("x"), StateConflictError)


def test_timeout_uncertain_quarantines_and_blocks_replay(live_pair):
    agent, port, transport, coordinator = live_pair
    port.delays["object_delete"] = 2.0
    adapter = RemoteAutoCADRuntimeAdapter(
        transport, mutation_coordinator=coordinator, default_deadline_ms=300
    )
    assert adapter.mutation_coordinator is coordinator
    with pytest.raises(MutationCompletionUncertainError) as exc_info:
        asyncio.run(adapter.object_delete("AAA"))
    assert bool(getattr(exc_info.value, "completion_unknown", False)) is True
    assert coordinator.status()["quarantined"] is True
    # No blind replay: later mutation is refused WITHOUT a new dispatch.
    calls_after_uncertain = list(port.calls)
    with pytest.raises(BackendQuarantinedError):
        asyncio.run(adapter.object_delete("AAA"))
    time.sleep(0.1)
    assert port.calls == calls_after_uncertain
    # Reads remain available after quarantine.
    port.delays["document_info"] = 0.0
    assert asyncio.run(adapter.document_info())["backend"] == "com"


def test_cancellation_after_dispatch_quarantines_coordinator_and_blocks_replay(live_pair):
    agent, port, transport, coordinator = live_pair
    port.delays["object_delete"] = 1.0
    adapter = RemoteAutoCADRuntimeAdapter(
        transport, mutation_coordinator=coordinator, default_deadline_ms=5000
    )

    async def _cancel_run():
        task = asyncio.create_task(adapter.object_delete("AAA"))
        await asyncio.sleep(0.05)  # allow writer lane and dispatch to start
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_cancel_run())
    assert coordinator.status()["quarantined"] is True
    assert coordinator.status()["quarantine_lane"] == "remote"

    # Second mutation refused by quarantine without dispatching
    calls_after_quarantine = list(port.calls)
    with pytest.raises(BackendQuarantinedError):
        asyncio.run(adapter.object_delete("AAA"))
    assert port.calls == calls_after_quarantine



def test_no_duplicate_coordinator_in_agent():
    agent_file = Path(__file__).resolve().parent.parent / "src" / "cdt_autocad" / "workstation_agent.py"
    lines = agent_file.read_text(encoding="utf-8").splitlines()
    imports = [
        line
        for line in lines
        if "MutationCoordinator" in line
        and line.strip().startswith(("import ", "from "))
    ]
    constructions = [line for line in lines if "MutationCoordinator(" in line]
    assert imports == [], f"agent must not import MutationCoordinator: {imports}"
    assert constructions == [], f"agent must not construct MutationCoordinator: {constructions}"
    assert not hasattr(WorkstationRuntimeAgent, "mutation_coordinator")


def test_agent_restart_creates_new_generation(settings):
    coordinator = MutationCoordinator()
    port = _FakePort(coordinator, settings)
    first = WorkstationRuntimeAgent(
        port, WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN)
    )
    second = WorkstationRuntimeAgent(
        port, WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN)
    )
    try:
        first.start()
        second.start()
        assert first.generation != second.generation
    finally:
        first.stop()
        second.stop()


def test_remote_adapter_entity_rehydration_and_fails_closed_backend(live_pair):
    _agent, _port, transport, coordinator = live_pair
    adapter = RemoteAutoCADRuntimeAdapter(transport, mutation_coordinator=coordinator)
    entities = asyncio.run(adapter.object_list(None, None, 200, 0))
    assert len(entities) == 1
    assert isinstance(entities[0], EntityInfo)
    assert entities[0].id == "AAA"
    assert entities[0].type == "LINE"
    with pytest.raises(RuntimeUnavailableError):
        _ = adapter.backend


def test_local_transport_generation_and_uncertain(settings):
    coordinator = MutationCoordinator()
    port = _FakePort(coordinator, settings)
    port.delays["document_info"] = 2.0
    local = LocalRuntimeTransport(port, generation="local-1")
    with pytest.raises(RuntimeGenerationMismatchError):
        asyncio.run(local.call("document_info", (), {}, expected_generation="other"))
    with pytest.raises(RuntimeUncertainError) as exc_info:
        asyncio.run(local.call("document_info", (), {}, deadline_ms=200))
    assert bool(getattr(exc_info.value, "completion_unknown", False)) is True


@pytest.mark.asyncio
async def test_public_tool_surface_still_87_after_r2(settings):
    from dataclasses import replace

    for backend_name in ("ezdxf", "com"):
        app = create_mcp(replace(settings, backend=backend_name))
        async with Client(app) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert len(names) == PUBLIC_TOOL_COUNT == 87


def test_allowlist_covers_port_identity_surface():
    assert ALLOWED_OPS.issuperset({"capabilities", "status", "runtime_status", "health"})
    assert ALLOWED_OPS.issuperset({"document_info", "object_list", "object_count"})
    assert isinstance(LocalAutoCADRuntimeAdapter, type)
    assert isinstance(RemoteAutoCADRuntimeAdapter, type)
