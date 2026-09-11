"""N7 semantic executor recovery — post-commit failure must restore exact predecessor.
Wing: code | Topic: native-bridge-n7 | Updated: 2026-09-10 20:55
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from cdt_autocad.native_bridge.semantic_executor import NativeSemanticExecutor, SemanticStepError
from cdt_autocad.semantic.fingerprint import fingerprint_document, fingerprint_geometry
from cdt_autocad.semantic.models import (
    ActionSpec,
    AllowedEffects,
    EntitySemanticState,
    FingerprintSet,
    MutationOutcome,
    SemanticSnapshot,
)

RUNTIME = "22222222-2222-4222-8222-222222222222"
RUNTIME2 = "33333333-3333-4333-8333-333333333333"
DOC = "doc:test"
CP = "cp:66666666-6666-4666-8666-666666666666"
ARTIFACT = "sha256:" + "c" * 64


def entity(pid: str, end_x: float) -> EntitySemanticState:
    provisional = EntitySemanticState(
        semantic_pid=pid,
        native_handle="10",
        entity_type="LINE",
        layer="0",
        geometry={"start": (0.0, 0.0, 0.0), "end": (end_x, 0.0, 0.0)},
        bbox=None,
        metrics={"length": end_x},
        style={"linetype": "ByLayer", "lineweight": "ByLayer"},
        hierarchy={"owner_space": "Model"},
    )
    return EntitySemanticState(
        **{**provisional.to_dict(), "fingerprints": FingerprintSet(geometry_fp=fingerprint_geometry(provisional))}
    )


def snapshot(*entities: EntitySemanticState) -> SemanticSnapshot:
    provisional = SemanticSnapshot(
        snapshot_id="snap:test",
        document_pid=DOC,
        units="millimeters",
        current_space="Model",
        saved=False,
        entities=entities,
    )
    return SemanticSnapshot(
        snapshot_id=provisional.snapshot_id,
        document_pid=DOC,
        units=provisional.units,
        current_space=provisional.current_space,
        saved=False,
        entities=entities,
        fingerprints=FingerprintSet(document_fp=fingerprint_document(provisional)),
    )


def action(parent: str, *, end_x: float = 1.0) -> ActionSpec:
    return ActionSpec(
        action_id="n7:create",
        operation="entity.create.line",
        document_pid=DOC,
        expected_parent_fp=parent,
        args={"start": [0, 0, 0], "end": [end_x, 0, 0]},
        allowed_effects=AllowedEffects(create=("LINE",)),
    )


def checkpoint(parent: str) -> dict:
    return {
        "checkpoint_id": CP,
        "checkpoint_artifact_fp": ARTIFACT,
        "expected_restore_fp": parent,
    }


def mutation_receipt(outcome: str, before: SemanticSnapshot, after: SemanticSnapshot, *, pid="pid:new") -> dict:
    post = after.fingerprints.document_fp
    return {
        "schema_version": 1,
        "operation": "entity.create.line",
        "outcome": outcome,
        "document_pid": DOC,
        "pre_document_fp": before.fingerprints.document_fp,
        "provisional_document_fp": post,
        "post_document_fp": post,
        "affected_semantic_pid": pid,
        "recovery_checkpoint": checkpoint(before.fingerprints.document_fp),
    }


def recovery_receipt(strategy: str, parent: str, *, restored: bool, runtime=RUNTIME) -> dict:
    actual = parent if restored else "sha256:" + "f" * 64
    return {
        "schema_version": 1,
        "operation": "entity.create.line",
        "outcome": "ROLLED_BACK_VERIFIED" if restored else "ROLLBACK_FAILED",
        "document_pid": DOC,
        "runtime_document_id": runtime,
        "checkpoint_id": CP,
        "affected_semantic_pid": "pid:new",
        "rollback": {
            "rollback_id": "rb:77777777-7777-4777-8777-777777777777",
            "reason": "COMMIT_INTEGRITY_FAIL",
            "strategy": strategy,
            "expected_restore_fp": parent,
            "actual_restore_fp": actual,
            "status": "ROLLED_BACK_VERIFIED" if restored else "ROLLBACK_FAILED",
        },
    }


class FakeClient:
    def __init__(self, snapshots, mutation, recoveries=(), *, finalize_error: Exception | None = None):
        self.snapshots = list(snapshots)
        self.mutation = deepcopy(mutation)
        self.recoveries = [deepcopy(value) for value in recoveries]
        self.finalize_error = finalize_error
        self.recovery_calls = []
        self.finalize_calls = []
        self.mutation_calls = 0

    def document_snapshot(self, runtime_document_id, *, document_pid):
        assert document_pid == DOC
        value = self.snapshots.pop(0)
        return value

    def create_line(self, runtime_document_id, **kwargs):
        self.mutation_calls += 1
        return deepcopy(self.mutation)

    def resolve_recovery(self, runtime_document_id, **kwargs):
        self.recovery_calls.append((runtime_document_id, deepcopy(kwargs)))
        return self.recoveries.pop(0)

    def finalize_recovery(self, runtime_document_id, **kwargs):
        self.finalize_calls.append((runtime_document_id, deepcopy(kwargs)))
        if self.finalize_error is not None:
            raise self.finalize_error
        return {"schema_version": 1, "status": "FINALIZED", "checkpoint_id": CP}


def test_n7_accepted_commit_is_journaled_then_checkpoint_finalized(tmp_path):
    before = snapshot()
    after = snapshot(entity("pid:new", 1.0))
    client = FakeClient([before, after], mutation_receipt("COMMITTED_VERIFIED", before, after))
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")

    result = executor.execute_step(RUNTIME, action(before.fingerprints.document_fp))

    assert result.outcome is MutationOutcome.COMMITTED_VERIFIED
    assert len(executor.entries) == 1
    assert len(client.finalize_calls) == 1
    assert client.finalize_calls[0][1]["accepted_post_fp"] == after.fingerprints.document_fp
    assert executor.blocked_reason is None


def test_n7_native_postcommit_integrity_failure_recovers_with_r1_and_does_not_advance_chain(tmp_path):
    before = snapshot()
    corrupted = snapshot(entity("pid:new", 2.0))
    receipt = mutation_receipt("COMMIT_INTEGRITY_FAIL", before, corrupted)
    receipt["provisional_document_fp"] = snapshot(entity("pid:new", 1.0)).fingerprints.document_fp
    client = FakeClient(
        [before, corrupted, before],
        receipt,
        [recovery_receipt("R1_COMPENSATE", before.fingerprints.document_fp, restored=True)],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")

    result = executor.execute_step(RUNTIME, action(before.fingerprints.document_fp))

    assert result.outcome is MutationOutcome.ROLLED_BACK_VERIFIED
    assert result.runtime_document_id == RUNTIME
    assert executor.entries == ()
    assert executor.blocked_reason is None
    assert [call[1]["strategy"] for call in client.recovery_calls] == ["R1_COMPENSATE"]
    assert client.finalize_calls == []


def test_n7_r1_failure_falls_back_to_r2_and_rebinds_runtime(tmp_path):
    before = snapshot()
    corrupted = snapshot(entity("pid:new", 3.0))
    receipt = mutation_receipt("COMMIT_INTEGRITY_FAIL", before, corrupted)
    receipt["provisional_document_fp"] = snapshot(entity("pid:new", 1.0)).fingerprints.document_fp
    client = FakeClient(
        [before, corrupted, before],
        receipt,
        [
            recovery_receipt("R1_COMPENSATE", before.fingerprints.document_fp, restored=False),
            recovery_receipt("R2_CHECKPOINT_RESTORE", before.fingerprints.document_fp, restored=True, runtime=RUNTIME2),
        ],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")

    result = executor.execute_step(RUNTIME, action(before.fingerprints.document_fp))

    assert result.outcome is MutationOutcome.ROLLED_BACK_VERIFIED
    assert result.runtime_document_id == RUNTIME2
    assert executor.entries == ()
    assert [call[1]["strategy"] for call in client.recovery_calls] == [
        "R1_COMPENSATE",
        "R2_CHECKPOINT_RESTORE",
    ]


def test_n7_python_validation_failure_after_commit_is_recovered_not_left_uncertain(tmp_path):
    before = snapshot()
    wrong = snapshot(entity("pid:new", 2.0))
    receipt = mutation_receipt("COMMITTED_VERIFIED", before, wrong)
    client = FakeClient(
        [before, wrong, before],
        receipt,
        [recovery_receipt("R1_COMPENSATE", before.fingerprints.document_fp, restored=True)],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")

    result = executor.execute_step(RUNTIME, action(before.fingerprints.document_fp, end_x=1.0))

    assert result.outcome is MutationOutcome.ROLLED_BACK_VERIFIED
    assert result.validation is not None and result.validation.status.value == "FAIL"
    assert executor.entries == ()
    assert executor.blocked_reason is None


def test_n7_failed_r1_and_r2_latches_rollback_failed(tmp_path):
    before = snapshot()
    corrupted = snapshot(entity("pid:new", 3.0))
    receipt = mutation_receipt("COMMIT_INTEGRITY_FAIL", before, corrupted)
    client = FakeClient(
        [before, corrupted, snapshot(entity("pid:new", 4.0))],
        receipt,
        [
            recovery_receipt("R1_COMPENSATE", before.fingerprints.document_fp, restored=False),
            recovery_receipt("R2_CHECKPOINT_RESTORE", before.fingerprints.document_fp, restored=False, runtime=RUNTIME2),
        ],
    )
    executor = NativeSemanticExecutor(client, tmp_path / "state.jsonl")

    with pytest.raises(SemanticStepError) as caught:
        executor.execute_step(RUNTIME, action(before.fingerprints.document_fp))

    assert caught.value.code == "ROLLBACK_FAILED"
    assert executor.blocked_reason == "ROLLBACK_FAILED"
    assert executor.entries == ()
