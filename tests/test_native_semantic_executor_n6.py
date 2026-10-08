"""N6 native semantic executor — state-chain, drift and uncertainty orchestration.
Wing: code | Topic: semantic-state-n6 | Updated: 2026-09-10 14:40
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from cdt_autocad.native_bridge.client import BridgeClientProtocolError, BridgeRemoteError
from cdt_autocad.native_bridge.semantic_executor import (
    NativeSemanticExecutor,
    SemanticStepError,
)
from cdt_autocad.semantic.fingerprint import fingerprint_document, fingerprint_geometry
from cdt_autocad.semantic.models import (
    ActionSpec,
    AllowedEffects,
    EntitySemanticState,
    FingerprintSet,
    MutationOutcome,
    SemanticSnapshot,
)
from cdt_autocad.semantic.state_chain import verify_state_chain

RUNTIME = "22222222-2222-4222-8222-222222222222"
DOC = "doc:test"


def entity(pid: str, start, end, handle: str) -> EntitySemanticState:
    provisional = EntitySemanticState(
        semantic_pid=pid,
        native_handle=handle,
        entity_type="LINE",
        layer="0",
        geometry={"start": start, "end": end},
        bbox={"min": start, "max": end},
        metrics={"length": abs(float(end[0]) - float(start[0]))},
        style={"linetype": "ByLayer", "lineweight": "ByLayer"},
        hierarchy={"owner_space": "Model"},
    )
    return EntitySemanticState(
        semantic_pid=pid,
        native_handle=handle,
        entity_type=provisional.entity_type,
        layer=provisional.layer,
        geometry=provisional.geometry,
        bbox=provisional.bbox,
        metrics=provisional.metrics,
        style=provisional.style,
        hierarchy=provisional.hierarchy,
        fingerprints=FingerprintSet(geometry_fp=fingerprint_geometry(provisional)),
    )


def shape_entity(pid: str, entity_type: str, geometry: dict, handle: str) -> EntitySemanticState:
    provisional = EntitySemanticState(
        semantic_pid=pid,
        native_handle=handle,
        entity_type=entity_type,
        layer="0",
        geometry=geometry,
        bbox=None,
        metrics={},
        style={"linetype": "ByLayer", "lineweight": "ByLayer"},
        hierarchy={"owner_space": "Model"},
    )
    return EntitySemanticState(
        semantic_pid=pid,
        native_handle=handle,
        entity_type=entity_type,
        layer="0",
        geometry=geometry,
        bbox=None,
        metrics={},
        style=provisional.style,
        hierarchy=provisional.hierarchy,
        fingerprints=FingerprintSet(geometry_fp=fingerprint_geometry(provisional)),
    )


def snapshot(*entities: EntitySemanticState, snapshot_id="snap:test") -> SemanticSnapshot:
    provisional = SemanticSnapshot(
        snapshot_id=snapshot_id,
        document_pid=DOC,
        units="millimeters",
        current_space="Model",
        saved=False,
        entities=entities,
    )
    return SemanticSnapshot(
        snapshot_id=provisional.snapshot_id,
        document_pid=provisional.document_pid,
        units=provisional.units,
        current_space=provisional.current_space,
        saved=provisional.saved,
        entities=provisional.entities,
        fingerprints=FingerprintSet(document_fp=fingerprint_document(provisional)),
    )


def action(operation, parent, *, pids=(), args=None, allowed=None, action_id="a1"):
    return ActionSpec(
        action_id=action_id,
        operation=operation,
        document_pid=DOC,
        expected_parent_fp=parent,
        semantic_pids=pids,
        args=args or {},
        allowed_effects=allowed or AllowedEffects(),
    )


class FakeClient:
    def __init__(self, snapshots, receipts):
        self.snapshots = list(snapshots)
        self.receipts = list(receipts)
        self.snapshot_calls = 0
        self.mutation_calls = []

    def document_snapshot(self, runtime_document_id, *, document_pid):
        assert runtime_document_id == RUNTIME
        assert document_pid == DOC
        index = min(self.snapshot_calls, len(self.snapshots) - 1)
        self.snapshot_calls += 1
        value = self.snapshots[index]
        if isinstance(value, BaseException):
            raise value
        return value

    def _receipt(self, operation, kwargs):
        self.mutation_calls.append((operation, deepcopy(kwargs)))
        value = self.receipts.pop(0)
        if isinstance(value, BaseException):
            raise value
        return deepcopy(value)

    def create_line(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.create.line", kwargs)

    def update_line(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.update.line", kwargs)

    def delete_line(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.delete.line", kwargs)

    def create_circle(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.create.circle", kwargs)

    def update_circle(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.update.circle", kwargs)

    def delete_circle(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.delete.circle", kwargs)

    def create_arc(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.create.arc", kwargs)

    def update_arc(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.update.arc", kwargs)

    def delete_arc(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.delete.arc", kwargs)

    def create_lwpolyline(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.create.lwpolyline", kwargs)

    def update_lwpolyline(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.update.lwpolyline", kwargs)

    def delete_lwpolyline(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        return self._receipt("entity.delete.lwpolyline", kwargs)


def receipt(operation, pre, post, pid, outcome="COMMITTED_VERIFIED"):
    result = {
        "schema_version": 1,
        "operation": operation,
        "outcome": outcome,
        "document_pid": DOC,
        "pre_document_fp": pre,
        "post_document_fp": post,
        "affected_semantic_pid": pid,
    }
    if outcome == "ROLLED_BACK_VERIFIED":
        result["rollback"] = {
            "strategy": "R0_ABORT",
            "expected_restore_fp": pre,
            "actual_restore_fp": post,
            "status": outcome,
        }
    return result


def test_committed_create_appends_exactly_one_valid_chain_entry(tmp_path):
    before = snapshot()
    created = snapshot(entity("pid:new", (0, 0, 0), (1, 0, 0), "10"))
    client = FakeClient(
        [before, created],
        [receipt("entity.create.line", before.fingerprints.document_fp, created.fingerprints.document_fp, "pid:new")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    result = executor.execute_step(RUNTIME, spec)

    assert result.outcome is MutationOutcome.COMMITTED_VERIFIED
    assert result.chain_entry is not None
    assert len(executor.entries) == 1
    assert verify_state_chain(executor.entries) == created.fingerprints.document_fp
    lines = (tmp_path / "state.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "committed_step"
    assert event["action"]["action_id"] == "a1"
    assert event["delta"]["created"] == ["pid:new"]


def test_two_committed_steps_link_parent_chain(tmp_path):
    s0 = snapshot()
    e1 = entity("pid:new", (0, 0, 0), (1, 0, 0), "10")
    s1 = snapshot(e1)
    e2 = entity("pid:new", (0, 0, 0), (2, 0, 0), "10")
    s2 = snapshot(e2)
    client = FakeClient(
        [s0, s1, s1, s2],
        [
            receipt("entity.create.line", s0.fingerprints.document_fp, s1.fingerprints.document_fp, "pid:new"),
            receipt("entity.update.line", s1.fingerprints.document_fp, s2.fingerprints.document_fp, "pid:new"),
        ],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    executor.execute_step(
        RUNTIME,
        action("entity.create.line", s0.fingerprints.document_fp, args={"start": [0,0,0], "end": [1,0,0]}, allowed=AllowedEffects(create=("LINE",)), action_id="a1"),
    )
    executor.execute_step(
        RUNTIME,
        action("entity.update.line", s1.fingerprints.document_fp, pids=("pid:new",), args={"start": [0,0,0], "end": [2,0,0]}, allowed=AllowedEffects(modify=("LINE",)), action_id="a2"),
    )

    assert len(executor.entries) == 2
    assert executor.entries[1].parent_step_fp == executor.entries[0].step_fp
    assert verify_state_chain(executor.entries) == s2.fingerprints.document_fp


def test_verified_rollback_does_not_advance_chain_and_allows_retry(tmp_path):
    before = snapshot()
    client = FakeClient(
        [before, before],
        [receipt("entity.create.line", before.fingerprints.document_fp, before.fingerprints.document_fp, "pid:attempt", "ROLLED_BACK_VERIFIED")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0,0,0], "end": [1,0,0], "fault_stage": "after_apply_before_commit"},
        allowed=AllowedEffects(create=("LINE",)),
    )

    result = executor.execute_step(RUNTIME, spec)

    assert result.outcome is MutationOutcome.ROLLED_BACK_VERIFIED
    assert result.chain_entry is None
    assert executor.entries == ()
    assert executor.blocked_reason is None
    event = json.loads((tmp_path / "state.jsonl").read_text(encoding="utf-8"))
    assert event["event"] == "rollback"


def test_manual_drift_blocks_before_native_mutation(tmp_path):
    parent = snapshot(entity("pid:1", (0,0,0), (1,0,0), "10"))
    drifted = snapshot(entity("pid:1", (0,0,0), (3,0,0), "10"))
    client = FakeClient([drifted], [])
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.update.line",
        parent.fingerprints.document_fp,
        pids=("pid:1",),
        args={"start": [0,0,0], "end": [2,0,0]},
        allowed=AllowedEffects(modify=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_DRIFT"
    assert client.mutation_calls == []
    assert executor.blocked_reason == "STATE_DRIFT"
    event = json.loads((tmp_path / "state.jsonl").read_text(encoding="utf-8"))
    assert event["event"] == "state_drift"
    assert event["actual_parent_fp"] == drifted.fingerprints.document_fp


def test_post_commit_allowed_effects_failure_latches_uncertain_and_is_reconstructable(tmp_path):
    before = snapshot()
    after = snapshot(entity("pid:new", (0,0,0), (1,0,0), "10"))
    client = FakeClient(
        [before, after],
        [receipt("entity.create.line", before.fingerprints.document_fp, after.fingerprints.document_fp, "pid:new")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0,0,0], "end": [1,0,0]},
        allowed=AllowedEffects(),
        action_id="bad-effects",
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)
    assert caught.value.code == "VALIDATION_FAILED"
    assert executor.entries == ()
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    assert len(client.mutation_calls) == 1

    event = json.loads((tmp_path / "state.jsonl").read_text(encoding="utf-8"))
    assert event["event"] == "validation_failed"
    assert event["action"]["action_id"] == "bad-effects"
    assert event["delta"]["created"] == ["pid:new"]
    assert event["validation"]["status"] == "FAIL"

    with pytest.raises(SemanticStepError) as blocked:
        executor.execute_step(RUNTIME, spec)
    assert blocked.value.code == "STATE_UNCERTAIN"
    assert len(client.mutation_calls) == 1


def test_native_receipt_fingerprint_mismatch_latches_uncertain(tmp_path):
    before = snapshot()
    after = snapshot(entity("pid:new", (0,0,0), (1,0,0), "10"))
    bad_post = "sha256:" + "f" * 64
    client = FakeClient(
        [before, after],
        [receipt("entity.create.line", before.fingerprints.document_fp, bad_post, "pid:new")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0,0,0], "end": [1,0,0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_UNCERTAIN"
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    assert executor.entries == ()


def test_malformed_native_receipt_after_dispatch_latches_uncertain(tmp_path):
    before = snapshot()
    malformed = receipt(
        "entity.update.line",
        before.fingerprints.document_fp,
        before.fingerprints.document_fp,
        "pid:new",
    )
    client = FakeClient([before], [malformed])
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_UNCERTAIN"
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    assert len(client.mutation_calls) == 1
    event = json.loads((tmp_path / "state.jsonl").read_text(encoding="utf-8"))
    assert event["event"] == "state_uncertain"
    assert event["cause_code"] == "INVALID_NATIVE_RECEIPT"


def test_native_commit_integrity_error_after_dispatch_latches_uncertain(tmp_path):
    before = snapshot()
    client = FakeClient(
        [before],
        [BridgeRemoteError("COMMIT_INTEGRITY_FAIL", "post-commit mismatch", "req:1")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_UNCERTAIN"
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    event = json.loads((tmp_path / "state.jsonl").read_text(encoding="utf-8"))
    assert event["event"] == "state_uncertain"
    assert event["cause_code"] == "COMMIT_INTEGRITY_FAIL"


def test_transport_failure_after_dispatch_latches_uncertain(tmp_path):
    before = snapshot()
    client = FakeClient([before], [RuntimeError("pipe response ended")])
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_UNCERTAIN"
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    assert len(client.mutation_calls) == 1


def test_independent_readback_failure_after_dispatch_latches_uncertain(tmp_path):
    before = snapshot()
    client = FakeClient(
        [before, BridgeClientProtocolError("INVALID_RESPONSE", "bad snapshot")],
        [receipt("entity.create.line", before.fingerprints.document_fp, before.fingerprints.document_fp, "pid:new")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_UNCERTAIN"
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    event = json.loads((tmp_path / "state.jsonl").read_text(encoding="utf-8"))
    assert event["event"] == "state_uncertain"
    assert event["cause_code"] == "READBACK_FAILED"


def test_known_prewrite_remote_refusal_does_not_latch_uncertain(tmp_path):
    before = snapshot(entity("pid:1", (0, 0, 0), (1, 0, 0), "10"))
    client = FakeClient(
        [before],
        [BridgeRemoteError("ENTITY_TYPE_MISMATCH", "target is not a LINE", "req:1")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.update.line",
        before.fingerprints.document_fp,
        pids=("pid:1",),
        args={"start": [0, 0, 0], "end": [2, 0, 0]},
        allowed=AllowedEffects(modify=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "ENTITY_TYPE_MISMATCH"
    assert executor.blocked_reason is None
    event = json.loads((tmp_path / "state.jsonl").read_text(encoding="utf-8"))
    assert event["event"] == "native_refusal"


def test_invalid_coordinate_shape_is_rejected_before_dispatch(tmp_path):
    before = snapshot()
    client = FakeClient([before], [])
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "INVALID_ACTION"
    assert client.snapshot_calls == 0
    assert client.mutation_calls == []


def test_created_pid_receipt_mismatch_latches_uncertain(tmp_path):
    before = snapshot()
    after = snapshot(entity("pid:new", (0, 0, 0), (1, 0, 0), "10"))
    client = FakeClient(
        [before, after],
        [
            receipt(
                "entity.create.line",
                before.fingerprints.document_fp,
                after.fingerprints.document_fp,
                "pid:wrong",
            )
        ],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_UNCERTAIN"
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    assert executor.entries == ()


def test_validation_engine_exception_after_commit_latches_uncertain(tmp_path, monkeypatch):
    before = snapshot()
    after = snapshot(entity("pid:new", (0, 0, 0), (1, 0, 0), "10"))
    client = FakeClient(
        [before, after],
        [receipt("entity.create.line", before.fingerprints.document_fp, after.fingerprints.document_fp, "pid:new")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    def fail_validation(*args, **kwargs):
        raise RuntimeError("validator crashed")

    monkeypatch.setattr(
        "cdt_autocad.native_bridge.semantic_executor.validate_action_delta",
        fail_validation,
    )
    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_UNCERTAIN"
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    assert executor.entries == ()


def test_state_chain_build_exception_after_commit_latches_uncertain(tmp_path, monkeypatch):
    before = snapshot()
    after = snapshot(entity("pid:new", (0, 0, 0), (1, 0, 0), "10"))
    client = FakeClient(
        [before, after],
        [receipt("entity.create.line", before.fingerprints.document_fp, after.fingerprints.document_fp, "pid:new")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    def fail_chain(*args, **kwargs):
        raise RuntimeError("chain build failed")

    monkeypatch.setattr(
        "cdt_autocad.native_bridge.semantic_executor.build_state_chain_entry",
        fail_chain,
    )
    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "STATE_UNCERTAIN"
    assert executor.blocked_reason == "STATE_UNCERTAIN"
    assert executor.entries == ()


def test_rollback_journal_failure_blocks_future_execution(tmp_path):
    before = snapshot()
    client = FakeClient(
        [before, before],
        [receipt("entity.create.line", before.fingerprints.document_fp, before.fingerprints.document_fp, "pid:attempt", "ROLLED_BACK_VERIFIED")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0, 0, 0], "end": [1, 0, 0], "fault_stage": "after_apply_before_commit"},
        allowed=AllowedEffects(create=("LINE",)),
    )

    def fail_journal(event):
        raise OSError("disk full")

    executor.journal.append = fail_journal
    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "JOURNAL_FAILED"
    assert executor.blocked_reason == "JOURNAL_FAILED"


def test_strict_action_shape_refuses_unknown_args_before_mutation(tmp_path):
    before = snapshot()
    client = FakeClient([before], [])
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.line",
        before.fingerprints.document_fp,
        args={"start": [0,0,0], "end": [1,0,0], "layer": "A-WALL"},
        allowed=AllowedEffects(create=("LINE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "INVALID_ACTION"
    assert client.mutation_calls == []


@pytest.mark.parametrize(
    ("operation", "entity_type", "args", "geometry", "method_name"),
    [
        (
            "entity.create.circle",
            "CIRCLE",
            {"center": [10, 20, 0], "radius": 5.0},
            {"center": [10, 20, 0], "normal": [0, 0, 1], "radius": 5.0},
            "entity.create.circle",
        ),
        (
            "entity.create.arc",
            "ARC",
            {"center": [30, 20, 0], "radius": 4.0, "start_angle": 0.25, "end_angle": 2.5},
            {
                "center": [30, 20, 0],
                "normal": [0, 0, 1],
                "radius": 4.0,
                "start_angle": 0.25,
                "end_angle": 2.5,
                "sweep_angle": 2.25,
            },
            "entity.create.arc",
        ),
        (
            "entity.create.lwpolyline",
            "LWPOLYLINE",
            {"points": [[0, 0], [5, 0], [5, 3]], "closed": True},
            {
                "vertices": [
                    {"point": [0, 0], "bulge": 0.0, "start_width": 0.0, "end_width": 0.0},
                    {"point": [5, 0], "bulge": 0.0, "start_width": 0.0, "end_width": 0.0},
                    {"point": [5, 3], "bulge": 0.0, "start_width": 0.0, "end_width": 0.0},
                ],
                "closed": True,
                "elevation": 0.0,
                "normal": [0, 0, 1],
            },
            "entity.create.lwpolyline",
        ),
    ],
)
def test_o1_new_shape_create_advances_n6_chain(
    tmp_path,
    operation,
    entity_type,
    args,
    geometry,
    method_name,
):
    before = snapshot()
    after = snapshot(shape_entity("pid:new", entity_type, geometry, "90"))
    client = FakeClient(
        [before, after],
        [receipt(operation, before.fingerprints.document_fp, after.fingerprints.document_fp, "pid:new")],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    result = executor.execute_step(
        RUNTIME,
        action(
            operation,
            before.fingerprints.document_fp,
            args=args,
            allowed=AllowedEffects(create=(entity_type,)),
        ),
    )

    assert result.outcome is MutationOutcome.COMMITTED_VERIFIED
    assert len(executor.entries) == 1
    assert client.mutation_calls[0][0] == method_name


def test_o1_invalid_circle_action_is_rejected_before_snapshot_or_dispatch(tmp_path):
    before = snapshot()
    client = FakeClient([before], [])
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")
    spec = action(
        "entity.create.circle",
        before.fingerprints.document_fp,
        args={"center": [0, 0, 0], "radius": 0},
        allowed=AllowedEffects(create=("CIRCLE",)),
    )

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, spec)

    assert caught.value.code == "INVALID_ACTION"
    assert client.snapshot_calls == 0
    assert client.mutation_calls == []


def test_existing_nonempty_journal_is_not_silently_resumed(tmp_path):
    path = tmp_path / "state.jsonl"
    path.write_text('{"event":"old"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="non-empty"):
        NativeSemanticExecutor(FakeClient([], []), path)
