"""R3 read parity + R4 mutation parity over the remote (loopback) path.
Wing: code | Topic: migration-r3r4-parity | Updated: 2026-10-07 17:40

Proves the split-process path (provider -> RemoteTransport loopback ->
WorkstationRuntimeAgent -> reused native implementation) preserves the
R1 single-host contract:

R3 — status, runtime identity, document_info, object queries, native
compact state, paged semantic snapshot and representative measurements are
semantically equivalent local-vs-remote within declared normalization; no
false availability; the provider side stays reachable when the runtime is
down and reports a typed dependency failure without provisioning.

R4 — PID/fingerprint refusal before mutation, normal COMMITTED_VERIFIED,
deterministic ROLLED_BACK_VERIFIED, injected timeout/disconnect ->
uncertain + quarantine + reconcile (no blind replay, no silent success),
duplicate prevention and persisted Saved/DBMOD read-back. Native
postcondition read-back still executes on the native (.171) host by
construction: the facade runs in the agent process in the same Windows
session as AutoCAD and the bridge.

Unit section runs anywhere (fake port, no CAD). Live section requires
Windows .171 + AutoCAD 2027 Session 1 + native bridge; it uses one
DISPOSABLE drawing per session (document_new + save_as under tmp +
close-without-save + file delete afterwards, ErrorReports untouched) and
skips with a DEFER reason when the native host is unavailable.
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from cdt_autocad.backends.local_runtime import LocalAutoCADRuntimeAdapter
from cdt_autocad.backends.remote_runtime import RemoteAutoCADRuntimeAdapter
from cdt_autocad.backends.runtime_port import AutoCADRuntimePort
from cdt_autocad.config import Settings
from cdt_autocad.contract_identity import PUBLIC_TOOL_COUNT
from cdt_autocad.errors import (
    BackendQuarantinedError,
    MutationCompletionUncertainError,
    StateConflictError,
)
from cdt_autocad.models import EntityInfo
from cdt_autocad.mutation_coordinator import MutationCoordinator
from cdt_autocad.runtime_transport import (
    LocalRuntimeTransport,
    RemoteRuntimeTransport,
    RuntimeTransportError,
    RuntimeUnavailableError,
)
from cdt_autocad.server import create_mcp
from cdt_autocad.workstation_agent import WorkstationAgentConfig, WorkstationRuntimeAgent

_TOKEN = "r3r4-test-token"

_FAKE_PID = "doc:fake-r3r4-0001"
_FAKE_FP_PRE = "sha256:" + "a" * 64
_FAKE_FP_POST = "sha256:" + "b" * 64


# --------------------------------------------------------------------------
# Fake execution side (unit only — no CAD semantics, scripted outcomes)
# --------------------------------------------------------------------------


class _FakeNativePort:
    """Port stand-in mirroring the real LocalAdapter shape: async backend-ish
    reads plus SYNC native_* facade delegations (the real facade is sync)."""

    def __init__(self, coordinator: MutationCoordinator) -> None:
        self._mutation_coordinator = coordinator
        self.calls: list[str] = []
        self.delays: dict[str, float] = {}
        self.rollback_next: bool = False
        self.lax_fp_ops: set[str] = set()
        self.state_fp = _FAKE_FP_PRE
        self.entity_count = 0

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

    # -- async backend-style ops --

    async def document_info(self) -> dict[str, Any]:
        self.calls.append("document_info")
        return {"backend": "com", "document": "fake", "units": "mm", "saved": True}

    async def object_count(self, *args: Any) -> int:
        self.calls.append("object_count")
        return 2

    async def object_list(self, *args: Any) -> list[EntityInfo]:
        self.calls.append("object_list")
        return [
            EntityInfo(
                id="AAA",
                type="LINE",
                layer="0",
                color=7,
                linetype="ByLayer",
                visible=True,
                properties={"length": 10.0},
            )
        ]

    async def object_measure(self, object_id: str) -> dict[str, Any]:
        self.calls.append("object_measure")
        return {"id": object_id, "length": 10.0,
                "bounding_box": {"min": [0, 0, 0], "max": [10, 0, 0]}}

    async def drawing_extents(self) -> dict[str, Any]:
        self.calls.append("drawing_extents")
        return {"min": [0, 0, 0], "max": [10, 0, 0]}

    # -- sync native_* facade shape --

    def _guard(self, op: str, document_pid: str, expected_parent_fp: str) -> None:
        self.calls.append(op)
        delay = self.delays.get(op, 0.0)
        if delay > 0:
            time.sleep(delay)
        if op not in self.lax_fp_ops and (
            document_pid != _FAKE_PID or expected_parent_fp != self.state_fp
        ):
            raise StateConflictError(
                f"fake native refusal for {op}: pid/fp mismatch (before mutation)"
            )

    def native_status(self) -> dict[str, Any]:
        self.calls.append("native_status")
        return {
            "available": True,
            "route": "native-managed-bridge",
            "fallback": False,
            "active_document": {
                "document_pid": _FAKE_PID,
                "document_fp": self.state_fp,
                "entity_count": self.entity_count,
            },
        }

    def native_document_state(self) -> dict[str, Any]:
        self.calls.append("native_document_state")
        return {
            "document_pid": _FAKE_PID,
            "document_fp": self.state_fp,
            "entity_count": self.entity_count,
        }

    def native_snapshot_page(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        self.calls.append("native_snapshot_page")
        return {
            "document_pid": _FAKE_PID,
            "offset": offset,
            "limit": limit,
            "envelope": {"document_fp": self.state_fp, "matched": self.entity_count},
        }

    def native_metadata_get(self, semantic_pid: str, namespace: str) -> dict[str, Any]:
        self.calls.append("native_metadata_get")
        return {"found": False, "semantic_pid": semantic_pid, "namespace": namespace}

    def native_metadata_query(self, namespace: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("native_metadata_query")
        return {"namespace": namespace, "items": [], "matched": 0}

    def native_feature_execute(
        self,
        document_pid: str,
        expected_parent_fp: str,
        feature_id: str,
        feature_sequence: int,
        correlation_id: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._guard("native_feature_execute", document_pid, expected_parent_fp)
        if self.rollback_next:
            self.rollback_next = False
            return {"status": "ROLLED_BACK_VERIFIED",
                    "initial_document_fp": self.state_fp,
                    "final_document_fp": self.state_fp}
        self.state_fp = _FAKE_FP_POST
        self.entity_count += 1
        return {"status": "COMMITTED_VERIFIED",
                "initial_document_fp": _FAKE_FP_PRE,
                "final_document_fp": self.state_fp, "feature_id": feature_id}

    def native_batch_create_entities(
        self, entities: list[dict[str, Any]], document_pid: str, expected_parent_fp: str
    ) -> dict[str, Any]:
        self._guard("native_batch_create_entities", document_pid, expected_parent_fp)
        if self.rollback_next:
            self.rollback_next = False
            return {"status": "ROLLED_BACK_VERIFIED",
                    "initial_document_fp": self.state_fp,
                    "final_document_fp": self.state_fp}
        self.state_fp = _FAKE_FP_POST
        self.entity_count += len(entities)
        return {"status": "COMMITTED_VERIFIED",
                "initial_document_fp": _FAKE_FP_PRE,
                "final_document_fp": self.state_fp}

    def native_batch_transform_entities(
        self, semantic_pids: list[str], transform: dict[str, Any],
        document_pid: str, expected_parent_fp: str,
    ) -> dict[str, Any]:
        self._guard("native_batch_transform_entities", document_pid, expected_parent_fp)
        self.state_fp = _FAKE_FP_POST
        return {"status": "COMMITTED_VERIFIED",
                "initial_document_fp": _FAKE_FP_PRE,
                "final_document_fp": self.state_fp}

    def native_metadata_set(
        self, semantic_pid: str, namespace: str, value: Any,
        document_pid: str, expected_parent_fp: str,
    ) -> dict[str, Any]:
        self._guard("native_metadata_set", document_pid, expected_parent_fp)
        self.state_fp = _FAKE_FP_POST
        return {"status": "COMMITTED_VERIFIED",
                "initial_document_fp": _FAKE_FP_PRE,
                "final_document_fp": self.state_fp,
                "independent_readback_verified": True}


@pytest.fixture
def unit_pair():
    coordinator = MutationCoordinator()
    port = _FakeNativePort(coordinator)
    assert isinstance(port, AutoCADRuntimePort)
    local_transport = LocalRuntimeTransport(port)
    agent = WorkstationRuntimeAgent(
        port, WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN)
    )
    base_url = agent.start()
    transport = RemoteRuntimeTransport(base_url, _TOKEN)
    remote = RemoteAutoCADRuntimeAdapter(transport, mutation_coordinator=coordinator)
    yield port, local_transport, agent, transport, remote, coordinator
    agent.stop()


# --------------------------------------------------------------------------
# R3 unit — read equivalence local-vs-remote over loopback
# --------------------------------------------------------------------------


_READ_OPS: tuple[tuple[str, tuple[Any, ...], dict[str, Any]], ...] = (
    ("status", (), {}),
    ("document_info", (), {}),
    ("object_count", (None, None), {}),
    ("object_list", (None, None, 200, 0), {}),
    ("object_measure", ("AAA",), {}),
    ("drawing_extents", (), {}),
    ("native_status", (), {}),
    ("native_document_state", (), {}),
    ("native_snapshot_page", (0, 50), {}),
    ("native_metadata_get", ("pid:x", "ns"), {}),
    ("native_metadata_query", ("ns",), {}),
)


def _norm(value: Any) -> Any:
    if isinstance(value, EntityInfo):
        return value.to_dict()
    if isinstance(value, list):
        return [_norm(item) for item in value]
    return value


def test_r3_read_equivalence_unit(unit_pair):
    """Every R3 read returns the identical payload local-vs-remote (unit)."""
    _, local_transport, _, transport, _, _ = unit_pair
    for op, args, kwargs in _READ_OPS:
        direct = asyncio.run(local_transport.call(op, args, kwargs))
        remote = asyncio.run(transport.call(op, args, kwargs))
        assert _norm(remote) == _norm(direct), f"read parity failed for {op}"


def test_r3_remote_entity_rehydration_unit(unit_pair):
    """Remote object rows rehydrate to typed EntityInfo (no shape drift)."""
    _, _, _, _, remote, _ = unit_pair
    rows = asyncio.run(remote.object_list(None, None, 200, 0))
    assert len(rows) == 1 and isinstance(rows[0], EntityInfo)
    assert (rows[0].id, rows[0].type) == ("AAA", "LINE")


def test_r3_unavailable_never_fabricates_success():
    """Dead runtime -> typed unavailable; transport object stays usable."""
    import socket as _socket

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        dead_port = int(sock.getsockname()[1])
    finally:
        sock.close()
    transport = RemoteRuntimeTransport(f"http://127.0.0.1:{dead_port}", _TOKEN)
    # Provider-side object is reachable (constructed, token redacted).
    assert "auth=<redacted>" in repr(transport)
    assert _TOKEN not in repr(transport)
    with pytest.raises(RuntimeUnavailableError):
        asyncio.run(transport.call("native_document_state", (), {}))
    with pytest.raises(RuntimeUnavailableError):
        asyncio.run(transport.health())


def test_r3_no_provisioning_static():
    """Boundary modules must not grow host-provisioning behavior (R3 gate)."""
    root = Path(__file__).resolve().parent.parent / "src" / "cdt_autocad"
    banned = ("wake-on-lan", "wake_on_lan", "wakeonlan", "provision(",
              "provision_machine", "install_autocad", "paramiko")
    for name in ("runtime_transport.py", "workstation_agent.py"):
        text = (root / name).read_text(encoding="utf-8").lower()
        hits = [token for token in banned if token in text]
        assert hits == [], f"{name} must not contain provisioning behavior: {hits}"


def test_r3_generation_pin_refuses_native_before_dispatch(unit_pair):
    port, _, agent, _, _, _ = unit_pair
    pinned = RemoteRuntimeTransport(agent.base_url, _TOKEN)
    before = list(port.calls)
    from cdt_autocad.runtime_transport import RuntimeGenerationMismatchError

    with pytest.raises(RuntimeGenerationMismatchError):
        asyncio.run(
            pinned.call("native_document_state", (), {}, expected_generation="gen-stale")
        )
    assert port.calls == before


# --------------------------------------------------------------------------
# R4 unit — mutation parity, faults, quarantine, no replay
# --------------------------------------------------------------------------


def test_r4_pid_fp_refusal_before_mutation_no_quarantine(unit_pair):
    """Wrong-doc / stale-parent refuse before CAD effect; coordinator clean."""
    port, _, _, _, remote, coordinator = unit_pair
    fp_before = port.state_fp
    with pytest.raises(RuntimeTransportError):
        asyncio.run(
            remote.native_feature_execute(
                "doc:wrong", fp_before, "f1", 1, "c1",
                [{"operation": "create_entities", "entities": []}],
            )
        )
    with pytest.raises(RuntimeTransportError):
        asyncio.run(
            remote.native_batch_create_entities(
                [{"kind": "line"}], _FAKE_PID, "sha256:" + "0" * 64
            )
        )
    assert port.state_fp == fp_before
    assert port.entity_count == 0
    assert coordinator.status()["quarantined"] is False
    # Reads stay available after refusals.
    assert asyncio.run(remote.native_document_state())["document_fp"] == fp_before


def test_r4_commit_and_rollback_passthrough_unit(unit_pair):
    _, _, _, _, remote, coordinator = unit_pair
    committed = asyncio.run(
        remote.native_batch_create_entities([{"kind": "line"}], _FAKE_PID, _FAKE_FP_PRE)
    )
    assert committed["status"] == "COMMITTED_VERIFIED"
    assert committed["final_document_fp"] == _FAKE_FP_POST
    assert coordinator.status()["quarantined"] is False

    unit_pair[0].rollback_next = True
    rolled = asyncio.run(
        remote.native_batch_create_entities([{"kind": "line"}], _FAKE_PID, _FAKE_FP_POST)
    )
    assert rolled["status"] == "ROLLED_BACK_VERIFIED"
    assert rolled["final_document_fp"] == rolled["initial_document_fp"]
    assert coordinator.status()["quarantined"] is False


def test_r4_timeout_uncertain_quarantines_blocks_replay(unit_pair):
    port, _, _, _, _, coordinator = unit_pair
    port.delays["native_batch_create_entities"] = 3.0
    slow = RemoteAutoCADRuntimeAdapter(
        unit_pair[3], mutation_coordinator=coordinator, default_deadline_ms=300
    )
    with pytest.raises(MutationCompletionUncertainError) as exc_info:
        asyncio.run(
            slow.native_batch_create_entities([{"kind": "line"}], _FAKE_PID, port.state_fp)
        )
    assert bool(getattr(exc_info.value, "completion_unknown", False)) is True
    assert coordinator.status()["quarantined"] is True
    # No blind replay: second mutation refused without a new dispatch.
    dispatched = len(port.calls)
    with pytest.raises(BackendQuarantinedError):
        asyncio.run(
            slow.native_batch_create_entities([{"kind": "line"}], _FAKE_PID, port.state_fp)
        )
    assert len(port.calls) == dispatched
    # Reads remain available while quarantined.
    assert asyncio.run(slow.native_document_state())["document_fp"] == port.state_fp


def test_r4_post_connect_loss_is_uncertain_no_replay():
    """TCP RST after dispatch started -> uncertain + quarantine, no replay.

    Deterministic disconnect stimulus: a killer socket accepts the preflight
    probe cleanly, then RSTs the in-flight POST (SO_LINGER 0). This drives the
    real production path (RemoteTransport.call + RemoteAdapter fencing)."""
    import socket as _socket
    import struct as _struct
    import threading as _threading

    listener = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    listener.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    killer_port = int(listener.getsockname()[1])
    stop = _threading.Event()

    def _serve() -> None:
        while not stop.is_set():
            try:
                listener.settimeout(0.5)
                conn, _ = listener.accept()
            except OSError:
                continue
            try:
                conn.settimeout(5.0)
                try:
                    head = conn.recv(65536)
                except OSError:
                    head = b""
                if not head:
                    continue  # preflight probe (connect + close, no bytes)
                time.sleep(0.3)  # client is now blocked reading the response
                try:
                    conn.setsockopt(
                        _socket.SOL_SOCKET, _socket.SO_LINGER,
                        _struct.pack("ii", 1, 0),
                    )
                except OSError:
                    pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    server = _threading.Thread(target=_serve, daemon=True)
    server.start()
    coordinator = MutationCoordinator()
    transport = RemoteRuntimeTransport(f"http://127.0.0.1:{killer_port}", _TOKEN)
    remote = RemoteAutoCADRuntimeAdapter(transport, mutation_coordinator=coordinator)
    try:
        with pytest.raises(MutationCompletionUncertainError) as exc_info:
            asyncio.run(
                remote.native_batch_create_entities(
                    [{"kind": "line"}], _FAKE_PID, _FAKE_FP_PRE
                )
            )
        assert bool(getattr(exc_info.value, "completion_unknown", False)) is True
        assert coordinator.status()["quarantined"] is True
        with pytest.raises(BackendQuarantinedError):
            asyncio.run(
                remote.native_batch_create_entities(
                    [{"kind": "line"}], _FAKE_PID, _FAKE_FP_PRE
                )
            )
    finally:
        stop.set()
        listener.close()
        server.join(timeout=5.0)


def test_r4_writer_lane_serializes_remote_mutations(unit_pair):
    """Concurrent remote mutations never overlap in native dispatch."""
    port, _, agent, transport, _, coordinator = unit_pair
    port.lax_fp_ops.add("native_metadata_set")
    active = {"n": 0, "peak": 0}
    real_guard = port._guard

    def _counting_guard(op: str, pid: str, fp: str) -> None:
        active["n"] += 1
        active["peak"] = max(active["peak"], active["n"])
        try:
            time.sleep(0.2)
            real_guard(op, pid, fp)
        finally:
            active["n"] -= 1

    port._guard = _counting_guard  # type: ignore[method-assign]
    remote = RemoteAutoCADRuntimeAdapter(transport, mutation_coordinator=coordinator)

    async def _main() -> list[dict[str, Any]]:
        calls = [
            remote.native_metadata_set(f"pid:{i}", "ns", {"v": i}, _FAKE_PID, "any-fp")
            for i in range(3)
        ]
        return list(await asyncio.gather(*calls))

    results = asyncio.run(_main())
    assert [r["status"] for r in results] == ["COMMITTED_VERIFIED"] * 3
    assert active["peak"] == 1


@pytest.mark.asyncio
async def test_public_tool_surface_still_87_after_r3r4(settings):
    from fastmcp import Client

    for backend_name in ("ezdxf", "com"):
        app = create_mcp(replace(settings, backend=backend_name))
        async with Client(app) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert len(names) == PUBLIC_TOOL_COUNT == 87


# --------------------------------------------------------------------------
# Live section — Windows .171 + AutoCAD 2027 + native bridge, disposable DWG
# --------------------------------------------------------------------------


def _live_settings(tmp_dir: Path) -> Settings:
    return Settings(
        allowed_paths=(tmp_dir.resolve(),),
        max_dxf_bytes=5 * 1024 * 1024,
        call_timeout_seconds=30.0,
        auth_token="",
        allow_remote_http=False,
        backend="com",
    )


def _probe_live(com_settings: Settings) -> dict[str, Any] | None:
    """Return live handles or None (caller skips with DEFER reason)."""
    from cdt_autocad.backends.com_backend import ComBackend
    from cdt_autocad.native_bridge.public_runtime import NativePublicFacade

    coordinator = MutationCoordinator()
    try:
        backend = ComBackend(com_settings, mutation_coordinator=coordinator)
        status = backend.status()
    except Exception:
        return None
    if not bool(status.get("runtime_available")):
        return None
    try:
        facade = NativePublicFacade(com_settings)
        bridge = facade._client().health()
    except Exception:
        return None
    if str(bridge.get("bridge_version", "")) != "0.8.6-d18":
        return None
    return {
        "settings": com_settings,
        "coordinator": coordinator,
        "backend": backend,
        "facade": facade,
        "bridge": bridge,
    }


def _close_active_doc_no_save(timeout_s: float = 90.0) -> None:
    """Close the AutoCAD active document without saving (disposable fixture)."""
    import win32com.client

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            app = win32com.client.GetActiveObject("AutoCAD.Application.26")
            if int(app.Documents.Count) == 0:
                return
            app.ActiveDocument.Close(False)
            time.sleep(2.0)
        except Exception:  # noqa: BLE001 — transient CALL_REJECTED while busy
            time.sleep(5.0)
            continue
    raise RuntimeError("live fixture teardown could not close the disposable drawing")


@pytest.fixture(scope="module")
def live_ctx(tmp_path_factory):
    """One disposable drawing: new -> save_as(tmp) -> bootstrap PID."""
    tmp_dir = tmp_path_factory.mktemp("r3r4_disposable")
    probe = _probe_live(_live_settings(tmp_dir))
    if probe is None:
        pytest.skip("DEFER: live AutoCAD 2027 + bridge 0.8.6-d18 unavailable")
    coordinator: MutationCoordinator = probe["coordinator"]
    backend = probe["backend"]
    facade = probe["facade"]
    local = LocalAutoCADRuntimeAdapter(
        backend, mutation_coordinator=coordinator, native_facade=facade
    )
    local_transport = LocalRuntimeTransport(local)
    doc = asyncio.run(local.document_new())
    save_path = str(tmp_dir / "r34_disposable.dwg")
    saved = asyncio.run(local.document_save_as(save_path))
    assert saved.get("persisted_path_verified") is True
    boot = facade.bootstrap_document_identity()
    assert boot.get("readback_verified") is True
    # Loopback topology mirrors split-host fencing: the agent side owns a
    # DISTINCT coordinator object (coord_agent) shared by its own backend +
    # adapter pair. Sharing the provider coordinator across the transport
    # boundary would double-enter the same asyncio writer lock (provider
    # RemoteAdapter.writer -> agent ComBackend._run.writer) and deadlock.
    # Uncertainty still quarantines BOTH sides: agent-side latches its backend
    # while the provider side quarantines on the uncertain wire outcome.
    from cdt_autocad.backends.com_backend import ComBackend as _ComBackend

    coord_agent = MutationCoordinator()
    backend_agent = _ComBackend(probe["settings"], mutation_coordinator=coord_agent)
    local_agent = LocalAutoCADRuntimeAdapter(
        backend_agent, mutation_coordinator=coord_agent, native_facade=facade
    )
    # Warm the agent-side application-metadata cache: ComBackend.status()
    # reports the cached inspection populated by document reads, so both
    # backend instances must have performed one before status parity holds.
    await_warm = asyncio.run(local_agent.document_info())
    assert await_warm.get("autocad_release") == "2027"
    agent = WorkstationRuntimeAgent(
        local_agent, WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN)
    )
    base_url = agent.start()
    transport = RemoteRuntimeTransport(base_url, _TOKEN)
    remote = RemoteAutoCADRuntimeAdapter(transport, mutation_coordinator=coordinator)
    try:
        remote.pin_generation(asyncio.run(transport.health())["generation"])
    except Exception:
        pass
    ctx = {
        "settings": probe["settings"],
        "coordinator": coordinator,
        "backend": backend,
        "facade": facade,
        "local": local,
        "local_agent": local_agent,
        "local_transport": local_transport,
        "agent": agent,
        "transport": transport,
        "remote": remote,
        "pid": boot["document_pid"],
        "save_path": save_path,
        "tmp_dir": tmp_dir,
        "doc_name": doc.get("name"),
    }
    yield ctx
    agent.stop()
    try:
        _close_active_doc_no_save()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _live_fp(local) -> tuple[str, int]:
    state = local.native_document_state()  # sync facade delegation (no loop)
    return state["document_fp"], int(state["entity_count"])


def _norm_status(value: Any) -> Any:
    """Declared R3 normalization: drop volatile coordinator lane activity,
    transport-envelope keys and the AutoCAD window caption (contains the
    active drawing title); compare backend identity + availability."""
    if isinstance(value, dict):
        return {
            key: _norm_status(item)
            for key, item in value.items()
            if key not in {"uptime_s", "transport", "agent", "active_lane", "caption"}
        }
    if isinstance(value, list):
        return [_norm_status(item) for item in value]
    return value


async def _settled_status_pair(live_ctx) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read local-vs-remote status alternately until BOTH sides report the
    full COM application inspection (a busy AutoCAD transiently refuses
    property probes on either path; alternating avoids one side hogging COM).
    """
    local_status: dict[str, Any] = {}
    remote_status: dict[str, Any] = {}

    def _ready(status: dict[str, Any]) -> bool:
        app = status.get("application")
        if not (isinstance(app, dict) and bool(app.get("version"))):
            return False
        cert = status.get("live_certification")
        return not isinstance(cert, dict) or cert.get("current_release_match") is not None

    for _ in range(18):
        local_status = await live_ctx["local_transport"].call("status", (), {})
        remote_status = await live_ctx["remote"].status_async()
        if _ready(local_status) and _ready(remote_status):
            return local_status, remote_status
        await asyncio.sleep(5.0)
    return local_status, remote_status


def test_r3_status_parity_live(live_ctx):
    local_status, remote_status = asyncio.run(_settled_status_pair(live_ctx))
    assert _norm_status(remote_status) == _norm_status(local_status)
    assert remote_status.get("backend") == "com"


def test_r3_runtime_identity_parity_live(live_ctx):
    from cdt_autocad.runtime_identity import RuntimeIdentity

    # The bridge probe subprocess uses a short timeout; settle while AutoCAD
    # drains Idle work left by rapid successive harnesses (assertions below
    # are unchanged — only transient busyness is retried).
    identity: dict[str, Any] = {}
    for _ in range(12):
        identity = RuntimeIdentity.from_env().status(backend_name="com")
        if bool(identity["native_bridge"]["ready"]) is True:
            break
        time.sleep(5.0)
    local_health = asyncio.run(live_ctx["local_transport"].call("health", (), {}))
    remote_health = asyncio.run(live_ctx["remote"].health())
    assert local_health["backend"] == remote_health["backend"] == "com"
    assert local_health["runtime_available"] is True
    assert remote_health["runtime_available"] is True
    assert local_health["quarantined"] is False
    assert remote_health["quarantined"] is False
    assert identity["supported_profile"] == "com-live"
    assert identity["native_bridge"]["ready"] is True
    assert identity["native_bridge"]["bridge_version"] == "0.8.6-d18"


def test_r3_document_info_parity_live(live_ctx):
    assert asyncio.run(live_ctx["remote"].document_info()) == asyncio.run(
        live_ctx["local"].document_info()
    )


def test_r3_object_query_parity_live(live_ctx):
    local, remote = live_ctx["local"], live_ctx["remote"]
    assert asyncio.run(remote.object_count(None, None)) == asyncio.run(
        local.object_count(None, None)
    )
    local_rows = asyncio.run(local.object_list(None, None, 200, 0))
    remote_rows = asyncio.run(remote.object_list(None, None, 200, 0))
    assert [row.to_dict() for row in remote_rows] == [row.to_dict() for row in local_rows]


def test_r3_native_state_snapshot_parity_live(live_ctx):
    local, remote = live_ctx["local"], live_ctx["remote"]
    assert asyncio.run(remote.native_status()) == local.native_status()
    assert asyncio.run(remote.native_document_state()) == local.native_document_state()
    assert asyncio.run(remote.native_snapshot_page(0, 50)) == local.native_snapshot_page(0, 50)
    assert asyncio.run(remote.native_metadata_query("test.r3r4")) == local.native_metadata_query(
        "test.r3r4"
    )


def test_r3_measure_parity_live(live_ctx):
    """Representative measurement: create via local COM, measure both paths."""
    local, remote = live_ctx["local"], live_ctx["remote"]
    created = asyncio.run(local.entity_create_line(x1=0.0, y1=0.0, x2=25.0, y2=0.0))
    try:
        assert asyncio.run(remote.object_measure(created.id)) == asyncio.run(
            local.object_measure(created.id)
        )
        assert asyncio.run(remote.drawing_extents()) == asyncio.run(local.drawing_extents())
        assert asyncio.run(remote.object_get(created.id)).to_dict() == created.to_dict()
    finally:
        asyncio.run(local.object_delete(created.id))


def test_r3_unavailable_reports_dependency_live(live_ctx):
    """Stopped agent -> typed unavailable; local provider path unaffected."""
    ghost = WorkstationRuntimeAgent(
        live_ctx["local_agent"],
        WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN),
    )
    base_url = ghost.start()
    ghost.stop()
    dead = RemoteRuntimeTransport(base_url, _TOKEN)
    with pytest.raises(RuntimeTransportError):
        asyncio.run(dead.call("native_document_state", (), {}))
    with pytest.raises(RuntimeUnavailableError):
        asyncio.run(dead.health())
    bad = RemoteRuntimeTransport(live_ctx["agent"].base_url, "wrong-token")
    from cdt_autocad.runtime_transport import RuntimeAuthError

    with pytest.raises(RuntimeAuthError):
        asyncio.run(bad.call("native_document_state", (), {}))
    # Provider-local path still answers while the remote copy is down.
    assert live_ctx["local"].native_document_state()["document_pid"] == live_ctx["pid"]


def _live_pre(live_ctx) -> tuple[str, int]:
    return _live_fp(live_ctx["local"])


def test_r4_refusal_wrong_doc_live(live_ctx):
    fp_before, _ = _live_pre(live_ctx)
    with pytest.raises(RuntimeTransportError):
        asyncio.run(
            live_ctx["remote"].native_feature_execute(
                "doc:does-not-exist", fp_before, "r4-wrong-doc", 1,
                f"corr-{uuid.uuid4().hex}",
                [{"operation": "create_entities",
                  "entities": [{"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]}]}],
            )
        )
    fp_after, _ = _live_pre(live_ctx)
    assert fp_after == fp_before
    assert live_ctx["coordinator"].status()["quarantined"] is False


def test_r4_refusal_stale_parent_live(live_ctx):
    fp_before, _ = _live_pre(live_ctx)
    with pytest.raises(RuntimeTransportError):
        asyncio.run(
            live_ctx["remote"].native_batch_create_entities(
                [{"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]}],
                live_ctx["pid"],
                "sha256:" + "0" * 64,
            )
        )
    fp_after, _ = _live_pre(live_ctx)
    assert fp_after == fp_before
    assert live_ctx["coordinator"].status()["quarantined"] is False


def test_r4_commit_live(live_ctx):
    fp_before, count_before = _live_pre(live_ctx)
    result = asyncio.run(
        live_ctx["remote"].native_batch_create_entities(
            [
                {"kind": "line", "start": [100.0, 0.0, 0.0], "end": [110.0, 0.0, 0.0]},
                {"kind": "line", "start": [100.0, 5.0, 0.0], "end": [110.0, 5.0, 0.0]},
            ],
            live_ctx["pid"],
            fp_before,
        )
    )
    # Logical/feature executors report "COMMITTED"; the VERIFIED half is the
    # independent read-back equality asserted below (final == fresh state).
    assert result["status"] == "COMMITTED"
    assert result["initial_document_fp"] == fp_before
    fp_after, count_after = _live_pre(live_ctx)
    assert result["final_document_fp"] == fp_after
    assert count_after == count_before + 2
    page = asyncio.run(live_ctx["remote"].native_snapshot_page(0, 50))
    assert page["envelope"]["document_fp"] == fp_after
    live_ctx["r4_commit_pids"] = list(result.get("affected_semantic_pids") or [])
    assert len(live_ctx["r4_commit_pids"]) == 2


def test_r4_rollback_live(live_ctx):
    fp_before, count_before = _live_pre(live_ctx)
    result = asyncio.run(
        live_ctx["remote"].native_batch_create_entities(
            [{"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]},
             {"kind": "BOGUS_KIND", "start": [0, 0, 0]}],
            live_ctx["pid"],
            fp_before,
        )
    )
    assert result["status"] == "ROLLED_BACK_VERIFIED"
    assert result["final_document_fp"] == result["initial_document_fp"] == fp_before
    fp_after, count_after = _live_pre(live_ctx)
    assert fp_after == fp_before
    assert count_after == count_before
    assert live_ctx["coordinator"].status()["quarantined"] is False


def test_r4_feature_execute_commit_live(live_ctx):
    fp_before, _ = _live_pre(live_ctx)
    result = asyncio.run(
        live_ctx["remote"].native_feature_execute(
            live_ctx["pid"], fp_before, "r4neau.e1", 1, f"corr-{uuid.uuid4().hex}",
            [{"operation": "create_entities",
              "entities": [{"kind": "line", "start": [200.0, 0.0, 0.0],
                            "end": [210.0, 0.0, 0.0]}]}],
        )
    )
    assert result["status"] == "COMMITTED"
    fp_after, _ = _live_pre(live_ctx)
    assert result["final_document_fp"] == fp_after != fp_before


def test_r4_metadata_set_readback_live(live_ctx):
    pids = live_ctx.get("r4_commit_pids") or []
    assert len(pids) == 2, "R4 commit must run before the metadata read-back row"
    fp_before, _ = _live_pre(live_ctx)
    result = asyncio.run(
        live_ctx["remote"].native_metadata_set(
            pids[0], "test.r3r4", {"row": "r4"}, live_ctx["pid"], fp_before
        )
    )
    assert result["outcome"] == "COMMITTED_VERIFIED"
    assert result.get("independent_readback_verified") is True
    fp_after, _ = _live_pre(live_ctx)
    assert fp_after == result["post_document_fp"]
    seen = asyncio.run(live_ctx["remote"].native_metadata_get(pids[0], "test.r3r4"))
    assert seen.get("found") is True
    assert seen.get("value") == {"row": "r4"}


def test_r4_persisted_save_readback_live(live_ctx):
    """Dirty native mutations persist clean: Saved=true, DBMOD=0 over remote."""
    info_before = asyncio.run(live_ctx["remote"].document_info())
    assert info_before.get("saved") is False  # native commits dirty the drawing
    saved = asyncio.run(live_ctx["remote"].document_save())
    assert saved.get("ok") is True
    assert saved.get("persisted_path_verified") is True
    assert saved.get("saved") is True
    assert int(saved.get("dbmod", -1)) == 0
    assert saved.get("persisted_clean") is True
    info_after = asyncio.run(live_ctx["remote"].document_info())
    assert info_after.get("saved") is True


class _CountingTransport(RemoteRuntimeTransport):
    def __init__(self, base_url: str, token: str, **kwargs: Any) -> None:
        super().__init__(base_url, token, **kwargs)
        self.dispatched = 0

    async def call(self, op: str, args: tuple[Any, ...] = (),
                   kwargs: dict[str, Any] | None = None, **rest: Any) -> Any:
        self.dispatched += 1
        return await super().call(op, args, kwargs, **rest)


def _wait_native_settled(facade, timeout_s: float = 120.0) -> dict[str, Any]:
    """Wait until the bridge answers twice in a row with identical compact
    state (lets an injected-timeout background tail finish server-side)."""
    deadline = time.time() + timeout_s
    last: tuple[str, int] | None = None
    stable = 0
    state: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            state = facade.document_state()
            key = (state["document_fp"], int(state["entity_count"]))
        except Exception:
            last, stable = None, 0
            time.sleep(5.0)
            continue
        if key == last:
            stable += 1
            if stable >= 2:
                return state
        else:
            last, stable = key, 0
        time.sleep(5.0)
    raise RuntimeError(f"native bridge did not settle within {timeout_s}s")


def test_r4_timeout_uncertain_quarantine_reconcile_live(live_ctx):
    """Injected timeout -> uncertain + sticky quarantine + read reconcile."""
    from cdt_autocad.backends.com_backend import ComBackend

    coord2 = MutationCoordinator()
    coord2_agent = MutationCoordinator()  # agent side never shares it (see live_ctx)
    backend2 = ComBackend(live_ctx["settings"], mutation_coordinator=coord2_agent)
    local2 = LocalAutoCADRuntimeAdapter(
        backend2, mutation_coordinator=coord2_agent, native_facade=live_ctx["facade"]
    )
    agent2 = WorkstationRuntimeAgent(
        local2, WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN)
    )
    agent2.start()
    try:
        transport2 = _CountingTransport(agent2.base_url, _TOKEN)
        remote2 = RemoteAutoCADRuntimeAdapter(
            transport2, mutation_coordinator=coord2, default_deadline_ms=150
        )
        fp_before, _ = _live_pre(live_ctx)
        # 64 lines = 2 native chunks with Idle yields: far slower than the
        # 150ms injected deadline, but a seconds-long (not minutes-long) tail.
        entities = [
            {"kind": "line", "start": [float(i), 50.0, 0.0], "end": [float(i), 51.0, 0.0]}
            for i in range(64)
        ]
        with pytest.raises(MutationCompletionUncertainError) as exc_info:
            asyncio.run(
                remote2.native_batch_create_entities(entities, live_ctx["pid"], fp_before)
            )
        assert bool(getattr(exc_info.value, "completion_unknown", False)) is True
        assert coord2.status()["quarantined"] is True
        dispatched = transport2.dispatched
        with pytest.raises(BackendQuarantinedError):
            asyncio.run(
                remote2.native_batch_create_entities(
                    [{"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]}],
                    live_ctx["pid"], fp_before,
                )
            )
        assert transport2.dispatched == dispatched  # no blind replay dispatched
        # Reads stay available (main clean coordinator); reconcile classifies
        # current native truth only after the background tail settles.
        settled = _wait_native_settled(live_ctx["facade"])
        assert settled["document_pid"] == live_ctx["pid"]
        live_ctx["r4_timeout_reconciled_fp"] = settled["document_fp"]
        assert settled["document_fp"] != fp_before  # tail committed server-side
        assert coord2.status()["quarantined"] is True  # sticky until restart
    finally:
        agent2.stop()


def test_r4_stopped_runtime_is_clean_unavailable_live(live_ctx):
    """Stopped agent before dispatch -> clean unavailable, nothing executed."""
    fp_before, count_before = _live_pre(live_ctx)
    ghost = WorkstationRuntimeAgent(
        live_ctx["local_agent"],
        WorkstationAgentConfig(host="127.0.0.1", port=0, auth_token=_TOKEN),
    )
    base_url = ghost.start()
    ghost.stop()
    dead = RemoteAutoCADRuntimeAdapter(
        RemoteRuntimeTransport(base_url, _TOKEN),
        mutation_coordinator=MutationCoordinator(),
    )
    with pytest.raises(RuntimeUnavailableError):
        asyncio.run(
            dead.native_batch_create_entities(
                [{"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]}],
                live_ctx["pid"], fp_before,
            )
        )
    fp_after, count_after = _live_pre(live_ctx)
    assert (fp_after, count_after) == (fp_before, count_before)
