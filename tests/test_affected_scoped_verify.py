"""AC-P02 affected-scoped verify — client contract for the new receipt fields.
Covers entity.batch.create (AC-A01) plus entity.batch.insert_blocks and
entity.batch.transform (AC-P02a), with logical-batch rollback and the
single-finalize-per-boundary (AC-P02b) executor proof.
Wing: code | Topic: affected-scoped-verify | Updated: 2026-10-07 11:40
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.feature_stream import FeatureStreamExecutor
from cdt_autocad.native_bridge.logical_batch import NativeLogicalBatchExecutor
from cdt_autocad.native_bridge.protocol import NATIVE_PROTOCOL_VERSION

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
DOCUMENT_PID = "doc:33333333-3333-4333-8333-333333333333"
PARENT_FP = "sha256:" + "a" * 64
POST_FP = "sha256:" + "b" * 64
AFFECTED_FP = "sha256:" + "c" * 64
CHECKPOINT_ID = "cp:66666666-6666-4666-8666-666666666666"
CHECKPOINT_ART_FP = "sha256:" + "d" * 64
DEFINITION_PID = "pid:77777777-7777-4777-8777-777777777777"
INSERT_PID_A = "pid:44444444-4444-4444-8444-444444444444"
INSERT_PID_B = "pid:55555555-5555-4555-8555-555555555555"
TARGET_PID_A = "pid:88888888-8888-4888-8888-888888888888"
TARGET_PID_B = "pid:99999999-9999-4999-8999-999999999999"


class FakeTransport:
    def __init__(self, result: dict):
        self.result = result
        self.requests: list[dict] = []

    def round_trip(self, request: Mapping) -> Mapping:
        self.requests.append(deepcopy(dict(request)))
        return {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": request["request_id"],
            "ok": True,
            "result": deepcopy(self.result),
        }


def test_affected_scoped_receipt_fields_pass_through_client() -> None:
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.create",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "provisional_document_fp": POST_FP,
            "post_document_fp": POST_FP,
            "verification_mode": "affected_scoped",
            "affected_fp": AFFECTED_FP,
            "affected_verified_count": 2,
            "affected_expected_count": 2,
            "full_reconcile": "pending:recovery_finalize",
            "affected_semantic_pids": [
                "pid:44444444-4444-4444-8444-444444444444",
                "pid:55555555-5555-4555-8555-555555555555",
            ],
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.batch_create_chunk(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        entities=(
            {"kind": "line", "start": [0, 0, 0], "end": [10, 0, 0]},
            {"kind": "line", "start": [0, 5, 0], "end": [10, 5, 0]},
        ),
    )

    assert result["outcome"] == "COMMITTED_VERIFIED"
    assert result["post_document_fp"] == POST_FP
    assert result["verification_mode"] == "affected_scoped"
    assert result["affected_fp"] == AFFECTED_FP
    assert result["affected_verified_count"] == result["affected_expected_count"] == 2
    assert result["full_reconcile"] == "pending:recovery_finalize"


def test_affected_scoped_integrity_fail_still_surfaces() -> None:
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.create",
            "outcome": "COMMIT_INTEGRITY_FAIL",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "provisional_document_fp": POST_FP,
            "post_document_fp": POST_FP,
            "verification_mode": "affected_scoped",
            "affected_fp": "",
            "affected_verified_count": 1,
            "affected_expected_count": 2,
            "full_reconcile": "pending:recovery_finalize",
            "affected_semantic_pids": [
                "pid:44444444-4444-4444-8444-444444444444",
                "pid:55555555-5555-4555-8555-555555555555",
            ],
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.batch_create_chunk(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        entities=(
            {"kind": "line", "start": [0, 0, 0], "end": [10, 0, 0]},
            {"kind": "line", "start": [0, 5, 0], "end": [10, 5, 0]},
        ),
    )

    assert result["outcome"] == "COMMIT_INTEGRITY_FAIL"
    assert result["affected_verified_count"] != result["affected_expected_count"]


def _insert_payload() -> tuple[dict[str, object], ...]:
    return (
        {"definition_pid": DEFINITION_PID, "position": [10, 20, 0], "rotation": 0.0, "scale": 1.0},
        {"definition_pid": DEFINITION_PID, "position": [30, 40, 0], "rotation": 0.5, "scale": 1.25},
    )


def test_insert_blocks_affected_scoped_receipt_fields_pass_through_client() -> None:
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.insert_blocks",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "provisional_document_fp": POST_FP,
            "post_document_fp": POST_FP,
            "verification_mode": "affected_scoped",
            "affected_fp": AFFECTED_FP,
            "affected_verified_count": 2,
            "affected_expected_count": 2,
            "full_reconcile": "pending:recovery_finalize",
            "affected_semantic_pids": [INSERT_PID_A, INSERT_PID_B],
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.batch_insert_blocks_chunk(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        inserts=_insert_payload(),
    )

    assert result["outcome"] == "COMMITTED_VERIFIED"
    assert result["post_document_fp"] == POST_FP
    assert result["verification_mode"] == "affected_scoped"
    assert result["affected_fp"] == AFFECTED_FP
    assert result["affected_verified_count"] == result["affected_expected_count"] == 2
    assert result["full_reconcile"] == "pending:recovery_finalize"


def test_insert_blocks_affected_scoped_integrity_fail_still_surfaces() -> None:
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.insert_blocks",
            "outcome": "COMMIT_INTEGRITY_FAIL",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "provisional_document_fp": POST_FP,
            "post_document_fp": POST_FP,
            "verification_mode": "affected_scoped",
            "affected_fp": "",
            "affected_verified_count": 1,
            "affected_expected_count": 2,
            "full_reconcile": "pending:recovery_finalize",
            "affected_semantic_pids": [INSERT_PID_A, INSERT_PID_B],
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.batch_insert_blocks_chunk(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        inserts=_insert_payload(),
    )

    assert result["outcome"] == "COMMIT_INTEGRITY_FAIL"
    assert result["affected_verified_count"] != result["affected_expected_count"]


def test_transform_affected_scoped_receipt_fields_pass_through_client() -> None:
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.transform",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "provisional_document_fp": POST_FP,
            "post_document_fp": POST_FP,
            "verification_mode": "affected_scoped",
            "affected_fp": AFFECTED_FP,
            "affected_verified_count": 2,
            "affected_expected_count": 2,
            "full_reconcile": "pending:recovery_finalize",
            "affected_semantic_pids": [TARGET_PID_A, TARGET_PID_B],
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.batch_transform_chunk(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pids=(TARGET_PID_A, TARGET_PID_B),
        transform={"kind": "translate", "delta": [1.0, 0.0, 0.0]},
    )

    assert result["outcome"] == "COMMITTED_VERIFIED"
    assert result["post_document_fp"] == POST_FP
    assert result["verification_mode"] == "affected_scoped"
    assert result["affected_fp"] == AFFECTED_FP
    assert result["affected_verified_count"] == result["affected_expected_count"] == 2
    assert result["full_reconcile"] == "pending:recovery_finalize"


def test_transform_affected_scoped_integrity_fail_still_surfaces() -> None:
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.transform",
            "outcome": "COMMIT_INTEGRITY_FAIL",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "provisional_document_fp": POST_FP,
            "post_document_fp": POST_FP,
            "verification_mode": "affected_scoped",
            "affected_fp": "",
            "affected_verified_count": 1,
            "affected_expected_count": 2,
            "full_reconcile": "pending:recovery_finalize",
            "affected_semantic_pids": [TARGET_PID_A, TARGET_PID_B],
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.batch_transform_chunk(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pids=(TARGET_PID_A, TARGET_PID_B),
        transform={"kind": "translate", "delta": [1.0, 0.0, 0.0]},
    )

    assert result["outcome"] == "COMMIT_INTEGRITY_FAIL"
    assert result["affected_verified_count"] != result["affected_expected_count"]


def _chain_fp(index: int) -> str:
    return "sha256:" + f"{index + 1:064d}"


class _AffectedScopedLogicalClient:
    """Minimal logical-batch peer returning affected-scoped chunk receipts."""

    def __init__(self, *, fail_chunk: int | None = None):
        self.fail_chunk = fail_chunk
        self.begin_calls = 0
        self.chunk_calls: list[tuple[str, dict]] = []
        self.finalize_calls = 0
        self.recovery_calls = 0
        self.state_calls = 0
        self.current_fp = PARENT_FP
        self.owner_request_id: str | None = None

    def _binding(self) -> dict[str, str]:
        assert isinstance(self.owner_request_id, str)
        return {
            "checkpoint_id": CHECKPOINT_ID,
            "checkpoint_artifact_fp": CHECKPOINT_ART_FP,
            "expected_restore_fp": PARENT_FP,
            "owner_request_id": self.owner_request_id,
        }

    def begin_logical_batch(self, runtime_document_id: str, **kwargs: object) -> dict[str, object]:
        self.begin_calls += 1
        self.owner_request_id = str(kwargs["request_id"])
        return {
            "schema_version": 1,
            "status": "OPEN",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "logical_transaction": self._binding(),
        }

    def _receipt(self, operation: str, pids: list[str], index: int) -> dict[str, object]:
        if self.fail_chunk == index:
            return {
                "schema_version": 1,
                "operation": operation,
                "outcome": "COMMIT_INTEGRITY_FAIL",
                "document_pid": DOCUMENT_PID,
                "pre_document_fp": self.current_fp,
                "provisional_document_fp": POST_FP,
                "post_document_fp": POST_FP,
                "verification_mode": "affected_scoped",
                "affected_fp": "",
                "affected_verified_count": 0,
                "affected_expected_count": len(pids),
                "full_reconcile": "pending:recovery_finalize",
                "affected_semantic_pids": pids,
                "recovery_checkpoint": self._binding(),
            }
        post = _chain_fp(index)
        receipt = {
            "schema_version": 1,
            "operation": operation,
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": self.current_fp,
            "provisional_document_fp": post,
            "post_document_fp": post,
            "verification_mode": "affected_scoped",
            "affected_fp": AFFECTED_FP,
            "affected_verified_count": len(pids),
            "affected_expected_count": len(pids),
            "full_reconcile": "pending:recovery_finalize",
            "affected_semantic_pids": pids,
            "recovery_checkpoint": self._binding(),
        }
        self.current_fp = post
        return receipt

    def batch_insert_blocks_chunk(
        self, runtime_document_id: str, **kwargs: object
    ) -> dict[str, object]:
        inserts = list(kwargs["inserts"])  # type: ignore[union-attr]
        index = len(self.chunk_calls)
        self.chunk_calls.append((runtime_document_id, deepcopy(dict(kwargs))))
        pids = [f"pid:inserted-{index}-{n}" for n in range(len(inserts))]
        return self._receipt("entity.batch.insert_blocks", pids, index)

    def batch_transform_chunk(
        self, runtime_document_id: str, **kwargs: object
    ) -> dict[str, object]:
        pids = list(kwargs["semantic_pids"])  # type: ignore[union-attr]
        index = len(self.chunk_calls)
        self.chunk_calls.append((runtime_document_id, deepcopy(dict(kwargs))))
        return self._receipt("entity.batch.transform", [str(pid) for pid in pids], index)

    def document_state(self, runtime_document_id: str, *, document_pid: str) -> dict[str, object]:
        self.state_calls += 1
        return {
            "schema_version": 1,
            "runtime_document_id": runtime_document_id,
            "document_pid": document_pid,
            "document_fp_schema_version": 3,
            "document_fp": self.current_fp,
            "entity_count": 100,
        }

    def finalize_recovery(self, runtime_document_id: str, **kwargs: object) -> dict[str, object]:
        self.finalize_calls += 1
        assert kwargs["accepted_post_fp"] == self.current_fp
        return {"schema_version": 1, "status": "FINALIZED", "checkpoint_id": CHECKPOINT_ID}

    def recoveries_list(self) -> list[dict[str, object]]:
        return []

    def resolve_recovery(self, runtime_document_id: str, **kwargs: object) -> dict[str, object]:
        self.recovery_calls += 1
        assert kwargs["strategy"] == "R2_CHECKPOINT_RESTORE"
        assert kwargs["expected_restore_fp"] == PARENT_FP
        return {
            "schema_version": 1,
            "operation": "logical.batch",
            "outcome": "ROLLED_BACK_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "runtime_document_id": runtime_document_id,
            "checkpoint_id": CHECKPOINT_ID,
            "rollback": {
                "strategy": "R2_CHECKPOINT_RESTORE",
                "status": "ROLLED_BACK_VERIFIED",
                "expected_restore_fp": PARENT_FP,
                "actual_restore_fp": PARENT_FP,
            },
        }


def _inserts(count: int) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "definition_pid": DEFINITION_PID,
            "position": [float(index), 0.0, 0.0],
            "rotation": 0.0,
            "scale": 1.0,
        }
        for index in range(count)
    )


def test_logical_insert_blocks_rolls_back_on_affected_scoped_integrity_fail(tmp_path) -> None:
    client = _AffectedScopedLogicalClient(fail_chunk=1)
    executor = NativeLogicalBatchExecutor(client, tmp_path / "insert.jsonl")

    result = executor.insert_blocks(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        inserts=_inserts(40),
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert result.final_fp == PARENT_FP
    assert len(client.chunk_calls) == 2
    assert client.finalize_calls == 0
    assert client.recovery_calls == 1


def test_logical_transform_rolls_back_on_affected_scoped_integrity_fail(tmp_path) -> None:
    client = _AffectedScopedLogicalClient(fail_chunk=0)
    executor = NativeLogicalBatchExecutor(client, tmp_path / "transform.jsonl")
    pids = tuple(f"pid:target-{index}" for index in range(40))

    result = executor.transform_entities(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pids=pids,
        transform={"kind": "translate", "delta": [1.0, 0.0, 0.0]},
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert result.final_fp == PARENT_FP
    assert len(client.chunk_calls) == 1
    assert client.finalize_calls == 0
    assert client.recovery_calls == 1


def test_logical_multi_chunk_amortizes_single_finalize_boundary(tmp_path) -> None:
    """P02b: many affected-scoped chunks share one read-back + one finalize."""

    client = _AffectedScopedLogicalClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "amortized.jsonl")

    result = executor.insert_blocks(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        inserts=_inserts(40),
    )

    assert result.status == "COMMITTED"
    assert result.chunk_count == 2
    assert client.begin_calls == 1
    assert client.state_calls == 1
    assert client.finalize_calls == 1
    assert client.recovery_calls == 0
    for receipt in result.native_receipts:
        assert receipt["verification_mode"] == "affected_scoped"
        assert receipt["full_reconcile"] == "pending:recovery_finalize"


def test_feature_multi_chunk_amortizes_single_finalize_boundary(tmp_path) -> None:
    """P02b at feature level: mixed insert+transform chunks, one boundary."""

    client = _AffectedScopedLogicalClient()
    executor = FeatureStreamExecutor(client, tmp_path / "feature.jsonl")
    pids = tuple(f"pid:target-{index}" for index in range(33))

    result = executor.execute(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        feature_id="p02.feature",
        feature_sequence=1,
        correlation_id="p02.correlation",
        actions=(
            {"operation": "insert_blocks", "inserts": list(_inserts(33))},
            {
                "operation": "transform_entities",
                "semantic_pids": list(pids),
                "transform": {"kind": "translate", "delta": [1.0, 0.0, 0.0]},
            },
        ),
    )

    assert result.status == "COMMITTED"
    assert result.native_chunk_count == 4
    assert client.begin_calls == 1
    assert client.state_calls == 1
    assert client.finalize_calls == 1
    assert client.recovery_calls == 0
    assert len(result.affected_semantic_pids) == 33 + 33
