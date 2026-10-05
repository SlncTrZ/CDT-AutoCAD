"""Regression coverage for cancellation, recovery, durable evidence and numeric safety."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastmcp import Client

import cdt_autocad.supervisor as supervisor_module
from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.errors import BackendQuarantinedError, MutationCompletionUncertainError
from cdt_autocad.hot_reload import ReloadSupervisor, WorkerGeneration
from cdt_autocad.mutation_coordinator import MutationCoordinator
from cdt_autocad.native_bridge.feature_stream import FeatureStreamExecutor
from cdt_autocad.native_bridge.logical_batch import (
    LogicalBatchError,
    NativeLogicalBatchExecutor,
)
from cdt_autocad.native_bridge.protocol import LogicalBatchBinding
from cdt_autocad.native_bridge.public_runtime import NativePublicFacade
from cdt_autocad.server import _run_native_mutation, create_mcp

PRE = "sha256:" + "1" * 64
ART = "sha256:" + "2" * 64
CP = "cp:44444444-4444-4444-8444-444444444444"
OWNER = "55555555-5555-4555-8555-555555555555"


@pytest.mark.asyncio
async def test_cancelled_headless_writer_blocks_mutation_and_rebind_until_completion(settings):
    backend = EzdxfBackend(replace(settings, undo_depth=0))
    await backend.document_new()
    started, release, ended = threading.Event(), threading.Event(), threading.Event()

    def late_writer():
        started.set()
        release.wait(5)
        ended.set()

    task = asyncio.create_task(backend._run(late_writer, integrity_sensitive=True))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.status()["quarantined"]
        assert backend.status()["uncertain_worker_active"]
        with pytest.raises(BackendQuarantinedError):
            await backend._run(lambda: None, integrity_sensitive=True)
        with pytest.raises(BackendQuarantinedError):
            await backend.document_new()
    finally:
        release.set()
        assert await asyncio.to_thread(ended.wait, 2)
        if backend._uncertain_task is not None:
            await asyncio.shield(backend._uncertain_task)
    assert backend.status()["quarantined"]
    await backend.document_new()
    assert not backend.status()["quarantined"]


@pytest.mark.asyncio
async def test_cancelled_headless_lock_waiter_does_not_quarantine(settings):
    backend = EzdxfBackend(settings)
    async with backend._lock:
        task = asyncio.create_task(backend._run(lambda: None, integrity_sensitive=True))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not backend.status()["quarantined"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["discovery", "recovery", "receipt", "invalid_receipt", "invalid_recovery"])
async def test_metadata_secondary_failure_quarantines_shared_writer(settings, monkeypatch, failure):
    class Bridge:
        def metadata_set(self, *args, **kwargs):
            if failure == "invalid_receipt":
                return None
            if failure == "receipt":
                return {"outcome": "COMMITTED_VERIFIED"}
            raise TimeoutError("dispatch response lost")

        def recoveries_list(self):
            raise RuntimeError("recovery discovery unavailable")

        def resolve_recovery(self, *args, **kwargs):
            if failure == "invalid_recovery":
                return None
            raise TimeoutError("recovery response lost")

    facade = NativePublicFacade(replace(settings, backend="com"), client_factory=Bridge)
    monkeypatch.setattr(facade, "_bind_expected_document", lambda *a, **k: SimpleNamespace(
        runtime_document_id="runtime", document_pid="doc", document_fp=PRE
    ))
    if failure in {"recovery", "invalid_recovery"}:
        monkeypatch.setattr(facade, "_discover_metadata_checkpoint", lambda *a: {
            "checkpoint_id": CP, "checkpoint_artifact_fp": ART, "owner_request_id": OWNER
        })
    coordinator = MutationCoordinator()
    with pytest.raises(MutationCompletionUncertainError):
        await _run_native_mutation(
            coordinator, facade.metadata_set, "entity", "app.test", {"v": 1},
            document_pid="doc", expected_parent_fp=PRE,
        )
    with pytest.raises(BackendQuarantinedError):
        async with coordinator.writer("com"):
            pytest.fail("dependent writer must not enter")


class FailedJournal:
    def append(self, event):
        raise OSError("simulated ENOSPC")


@pytest.mark.asyncio
@pytest.mark.parametrize("executor_type", [NativeLogicalBatchExecutor, FeatureStreamExecutor])
@pytest.mark.parametrize("phase", ["begin_unknown", "begin_record", "rollback"])
async def test_journal_failure_cannot_mask_unknown_native_state(executor_type, phase):
    class Bridge:
        def begin_logical_batch(self, *args, **kwargs):
            if phase == "begin_unknown":
                raise TimeoutError("begin response lost")
            return {
                "status": "OPEN", "document_pid": "doc", "pre_document_fp": PRE,
                "logical_transaction": {
                    "checkpoint_id": CP, "checkpoint_artifact_fp": ART,
                    "expected_restore_fp": PRE, "owner_request_id": kwargs["request_id"],
                },
            }

        def recoveries_list(self):
            raise TimeoutError("discovery unavailable")

        def resolve_recovery(self, *args, **kwargs):
            raise TimeoutError("restore response lost")

    executor = object.__new__(executor_type)
    executor.client = Bridge()
    executor.journal = FailedJournal()
    if phase == "rollback":
        func = executor._rollback
        kwargs = dict(
            document_pid="doc", operation="entity.batch.create", expected_parent_fp=PRE,
            binding=LogicalBatchBinding(CP, ART, PRE, OWNER), receipts=(), affected=(),
            committed_chunks=1, cause="post-commit mismatch",
        )
    elif executor_type is FeatureStreamExecutor:
        func = executor.execute
        kwargs = dict(
            document_pid="doc", expected_parent_fp=PRE, feature_id="f1",
            feature_sequence=0, correlation_id="c1",
            actions=[{"operation": "create_entities", "entities": [
                {"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]}
            ]}],
        )
    else:
        func = executor.create_entities
        kwargs = dict(document_pid="doc", expected_parent_fp=PRE, entities=[{}])
    coordinator = MutationCoordinator()
    with pytest.raises(MutationCompletionUncertainError):
        await _run_native_mutation(coordinator, func, "runtime", **kwargs)
    with pytest.raises(BackendQuarantinedError):
        async with coordinator.writer("com"):
            pytest.fail("journal failure must not reopen dependent mutation")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend,override", [
    ("com", {}),
    ("com", {"integrity_uncertain": True}),
    ("com", {"mutation_coordinator": {"quarantined": True}}),
    ("com", {"integrity_uncertain": None}),
    ("com", {"mutation_coordinator": {}}),
    ("com", {"uncertain_call_running": True}),
    ("com", {"timeout_uncertain": None}),
    ("ezdxf", {"quarantined": True}),
    ("ezdxf", {"uncertain_worker_active": True}),
    ("ezdxf", {"quarantined": None}),
    ("ezdxf", {}),
])
async def test_worker_probe_rejects_quarantined_or_unknown_safety(monkeypatch, backend, override):
    status = {
        "provider": "autocad", "backend": backend, "runtime": {"ready": True},
        "transaction_depth": 0, "uncertain_call_running": False, "timeout_uncertain": False, "pending_recovery_count": 0,
        "native_bridge": {"ready": True}, "integrity_uncertain": False,
        "mutation_coordinator": {"quarantined": False},
        "quarantined": False, "uncertain_worker_active": False,
        **override,
    }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def list_tools(self):
            return [object()]

        async def call_tool(self, *a, **k):
            return SimpleNamespace(structured_content=status, is_error=False)

    monkeypatch.setattr(supervisor_module, "Client", FakeClient)
    probe = supervisor_module.McpWorkerProbe(auth_token="")
    health = await probe.activation(WorkerGeneration(1, "g1", "b1", "http://localhost"))
    supervisor = object.__new__(ReloadSupervisor)
    supervisor._require_bridge = True
    assert supervisor._safe_state(health) is (not override)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("entity_create_line", {"x1": "NaN", "y1": 0, "x2": 1, "y2": 1}),
    ("entity_create_circle", {"cx": 0, "cy": 0, "radius": "Infinity"}),
    ("entity_create_arc", {"cx": 0, "cy": 0, "radius": 1, "start_angle": "-Infinity", "end_angle": 90}),
    ("entity_create_polyline", {"points": [[0, 0], [1, "NaN"]]}),
    ("entity_create_text", {"text": "test", "x": 0, "y": 0, "height": "NaN"}),
    ("hatch_create", {"boundary_points": [[0, 0], [1, 0], [1, 1]], "scale": "NaN"}),
    ("dimension_linear", {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "dim_x": "NaN", "dim_y": 2}),
])
async def test_nonfinite_public_geometry_is_rejected_before_mutation(settings, tool, args):
    app = create_mcp(replace(settings, undo_depth=0))
    async with Client(app) as client:
        await client.call_tool("document_new", {})
        result = await client.call_tool(tool, args, raise_on_error=False)
        assert result.is_error
    assert len(app._cdt_backend._doc.modelspace()) == 0
    assert not app._cdt_backend.status()["quarantined"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args,method", [
    ("solid_create_primitive", {"kind": "box", "parameters": {
        "cx": 0, "cy": 0, "cz": 0, "length": 1, "width": 1, "height": "NaN"
    }}, "solid_box"),
    ("solid_transform", {"handle": "A1", "operation": "move",
                         "parameters": {"dx": 0, "dy": 0, "dz": "Infinity"}}, "solid_move"),
    ("viewport_set_scale", {"handle": "A1", "scale": "NaN"}, "viewport_set_scale"),
    ("view_set_direction", {"dx": 1, "dy": 0, "dz": "NaN"}, "view_set_direction"),
])
async def test_nonfinite_com_parameters_never_dispatch(settings, monkeypatch, tool, args, method):
    app = create_mcp(replace(settings, backend="com"))
    calls = []

    async def dispatch(*a, **k):
        calls.append((a, k))
        return {}

    monkeypatch.setattr(app._cdt_backend, method, dispatch)
    async with Client(app) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
        assert result.is_error
    assert calls == []


@pytest.mark.asyncio
async def test_cancelled_headless_reader_does_not_quarantine(settings):
    backend = EzdxfBackend(settings)
    started, release, ended = threading.Event(), threading.Event(), threading.Event()

    def reader():
        started.set()
        release.wait(5)
        ended.set()

    task = asyncio.create_task(backend._run(reader))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not backend.status()["quarantined"]
    finally:
        release.set()
        assert await asyncio.to_thread(ended.wait, 2)


@pytest.mark.asyncio
async def test_metadata_r2_can_verify_restore_after_r1_transport_failure(settings, monkeypatch):
    strategies = []

    class Bridge:
        def metadata_set(self, *a, **k):
            raise TimeoutError("dispatch response lost")

        def resolve_recovery(self, *a, **k):
            strategies.append(k["strategy"])
            if k["strategy"] == "R1_COMPENSATE":
                raise TimeoutError("R1 response lost")
            return {
                "outcome": "ROLLED_BACK_VERIFIED",
                "rollback": {
                    "status": "ROLLED_BACK_VERIFIED",
                    "expected_restore_fp": PRE, "actual_restore_fp": PRE,
                },
            }

    facade = NativePublicFacade(replace(settings, backend="com"), client_factory=Bridge)
    monkeypatch.setattr(facade, "_bind_expected_document", lambda *a, **k: SimpleNamespace(
        runtime_document_id="runtime", document_pid="doc", document_fp=PRE
    ))
    monkeypatch.setattr(facade, "_discover_metadata_checkpoint", lambda *a: {
        "checkpoint_id": CP, "checkpoint_artifact_fp": ART, "owner_request_id": OWNER
    })
    coordinator = MutationCoordinator()
    receipt = await _run_native_mutation(
        coordinator, facade.metadata_set, "entity", "app.test", {"v": 1},
        document_pid="doc", expected_parent_fp=PRE,
    )
    assert receipt["outcome"] == "ROLLED_BACK_VERIFIED"
    assert strategies == ["R1_COMPENSATE", "R2_CHECKPOINT_RESTORE"]
    async with coordinator.writer("com"):
        pass


def test_real_journal_fsync_failure_remains_fail_closed(tmp_path, monkeypatch):
    import cdt_autocad.native_bridge.logical_batch as logical_module

    class Bridge:
        def begin_logical_batch(self, *a, **k):
            return {
                "status": "OPEN", "document_pid": "doc", "pre_document_fp": PRE,
                "logical_transaction": {
                    "checkpoint_id": CP, "checkpoint_artifact_fp": ART,
                    "expected_restore_fp": PRE, "owner_request_id": k["request_id"],
                },
            }

    executor = NativeLogicalBatchExecutor(Bridge(), tmp_path / "journal.jsonl")

    def fail_fsync(fd):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(logical_module.os, "fsync", fail_fsync)
    with pytest.raises(LogicalBatchError) as failure:
        executor.create_entities(
            "runtime", document_pid="doc", expected_parent_fp=PRE,
            entities=[{"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]}],
        )
    assert failure.value.completion_unknown
    assert failure.value.code == "LOGICAL_JOURNAL_FAILED"
