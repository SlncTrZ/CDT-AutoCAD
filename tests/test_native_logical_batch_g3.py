"""G3 logical batch atomicity — one durable checkpoint across yielded native chunks.
Wing: code | Topic: native-g3-logical-batch | Updated: 2026-09-11 18:25
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from cdt_autocad.native_bridge.logical_batch import NativeLogicalBatchExecutor
from cdt_autocad.native_bridge.protocol import (
    BatchCreateParams,
    BridgeProtocolError,
    LogicalBatchBinding,
    LogicalBeginParams,
)

RUNTIME = "11111111-1111-4111-8111-111111111111"
RUNTIME2 = "22222222-2222-4222-8222-222222222222"
DOC = "pid:33333333-3333-4333-8333-333333333333"
PRE = "sha256:" + "1" * 64
CP = "cp:44444444-4444-4444-8444-444444444444"
ART = "sha256:" + "2" * 64


def _fp(index: int) -> str:
    return "sha256:" + f"{index:x}" * 64


def _entities(count: int):
    return tuple(
        {"kind": "line", "start": [float(i), 0.0, 0.0], "end": [float(i), 1.0, 0.0]}
        for i in range(count)
    )


def test_logical_binding_is_all_or_nothing_and_exact():
    binding = LogicalBatchBinding.from_dict(
        {
            "checkpoint_id": CP,
            "checkpoint_artifact_fp": ART,
            "expected_restore_fp": PRE,
        }
    )
    assert binding.checkpoint_id == CP

    params = BatchCreateParams.from_dict(
        {
            "runtime_document_id": RUNTIME,
            "document_pid": DOC,
            "expected_parent_fp": PRE,
            "entities": list(_entities(1)),
            "logical_transaction": binding.to_dict(),
        }
    )
    assert params.logical_transaction == binding

    with pytest.raises(BridgeProtocolError):
        BatchCreateParams.from_dict(
            {
                "runtime_document_id": RUNTIME,
                "document_pid": DOC,
                "expected_parent_fp": PRE,
                "entities": list(_entities(1)),
                "logical_transaction": {"checkpoint_id": CP},
            }
        )


def test_logical_begin_uses_exact_integrity_binding():
    params = LogicalBeginParams.from_dict(
        {
            "runtime_document_id": RUNTIME,
            "document_pid": DOC,
            "expected_parent_fp": PRE,
        }
    )
    assert params.to_dict()["expected_parent_fp"] == PRE
    with pytest.raises(BridgeProtocolError):
        LogicalBeginParams.from_dict(
            {
                "runtime_document_id": RUNTIME,
                "document_pid": DOC,
                "expected_parent_fp": PRE,
                "domain": "mechanical",
            }
        )


class FakeLogicalClient:
    def __init__(self, *, fail_chunk: int | None = None):
        self.fail_chunk = fail_chunk
        self.begin_calls = []
        self.chunk_calls = []
        self.finalize_calls = []
        self.recovery_calls = []
        self.state_calls = []
        self.current_fp = PRE
        self.checkpoint_present = False

    def begin_logical_batch(self, runtime_document_id, **kwargs):
        self.begin_calls.append((runtime_document_id, deepcopy(kwargs)))
        assert kwargs["expected_parent_fp"] == self.current_fp
        self.checkpoint_present = True
        return {
            "schema_version": 1,
            "status": "OPEN",
            "document_pid": DOC,
            "pre_document_fp": PRE,
            "logical_transaction": {
                "checkpoint_id": CP,
                "checkpoint_artifact_fp": ART,
                "expected_restore_fp": PRE,
            },
        }

    def batch_create_chunk(self, runtime_document_id, **kwargs):
        index = len(self.chunk_calls)
        self.chunk_calls.append((runtime_document_id, deepcopy(kwargs)))
        assert kwargs["expected_parent_fp"] == self.current_fp
        assert kwargs["logical_transaction"]["checkpoint_id"] == CP
        if self.fail_chunk == index:
            return {
                "schema_version": 1,
                "operation": "entity.batch.create",
                "outcome": "ROLLED_BACK_VERIFIED",
                "document_pid": DOC,
                "pre_document_fp": self.current_fp,
                "post_document_fp": self.current_fp,
                "affected_semantic_pids": [],
                "recovery_checkpoint": {
                    "checkpoint_id": CP,
                    "checkpoint_artifact_fp": ART,
                    "expected_restore_fp": PRE,
                },
                "rollback": {
                    "status": "ROLLED_BACK_VERIFIED",
                    "expected_restore_fp": self.current_fp,
                    "actual_restore_fp": self.current_fp,
                },
            }
        post = _fp(index + 5)
        pids = [f"pid:aaaaaaaa-aaaa-4aaa-8aaa-{index:012d}{n:01d}"[-40:] for n in range(len(kwargs["entities"]))]
        # The executor only requires unique string PIDs from the native receipt.
        pids = [f"pid:created-{index}-{n}" for n in range(len(kwargs["entities"]))]
        receipt = {
            "schema_version": 1,
            "operation": "entity.batch.create",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOC,
            "pre_document_fp": self.current_fp,
            "provisional_document_fp": post,
            "post_document_fp": post,
            "affected_semantic_pids": pids,
            "recovery_checkpoint": {
                "checkpoint_id": CP,
                "checkpoint_artifact_fp": ART,
                "expected_restore_fp": PRE,
            },
        }
        self.current_fp = post
        return receipt

    def batch_insert_blocks_chunk(self, runtime_document_id, **kwargs):
        entities = kwargs.pop("inserts")
        kwargs["entities"] = entities
        receipt = self.batch_create_chunk(runtime_document_id, **kwargs)
        receipt["operation"] = "entity.batch.insert_blocks"
        return receipt

    def batch_transform_chunk(self, runtime_document_id, **kwargs):
        semantic_pids = list(kwargs.pop("semantic_pids"))
        index = len(self.chunk_calls)
        self.chunk_calls.append((runtime_document_id, {**deepcopy(kwargs), "semantic_pids": semantic_pids}))
        assert kwargs["expected_parent_fp"] == self.current_fp
        assert kwargs["logical_transaction"]["checkpoint_id"] == CP
        if self.fail_chunk == index:
            return {
                "schema_version": 1,
                "operation": "entity.batch.transform",
                "outcome": "ROLLED_BACK_VERIFIED",
                "document_pid": DOC,
                "pre_document_fp": self.current_fp,
                "post_document_fp": self.current_fp,
                "affected_semantic_pids": [],
                "recovery_checkpoint": {
                    "checkpoint_id": CP,
                    "checkpoint_artifact_fp": ART,
                    "expected_restore_fp": PRE,
                },
                "rollback": {
                    "status": "ROLLED_BACK_VERIFIED",
                    "expected_restore_fp": self.current_fp,
                    "actual_restore_fp": self.current_fp,
                },
            }
        post = _fp(index + 8)
        receipt = {
            "schema_version": 1,
            "operation": "entity.batch.transform",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOC,
            "pre_document_fp": self.current_fp,
            "provisional_document_fp": post,
            "post_document_fp": post,
            "affected_semantic_pids": semantic_pids,
            "recovery_checkpoint": {
                "checkpoint_id": CP,
                "checkpoint_artifact_fp": ART,
                "expected_restore_fp": PRE,
            },
        }
        self.current_fp = post
        return receipt

    def finalize_recovery(self, runtime_document_id, **kwargs):
        self.finalize_calls.append((runtime_document_id, deepcopy(kwargs)))
        assert kwargs["accepted_post_fp"] == self.current_fp
        self.checkpoint_present = False
        return {"schema_version": 1, "status": "FINALIZED", "checkpoint_id": CP}

    def document_state(self, runtime_document_id, *, document_pid):
        self.state_calls.append((runtime_document_id, document_pid))
        return {
            "schema_version": 1,
            "runtime_document_id": runtime_document_id,
            "document_pid": document_pid,
            "document_fp_schema_version": 3,
            "document_fp": self.current_fp,
            "entity_count": 100,
        }

    def recoveries_list(self):
        if not self.checkpoint_present:
            return []
        return [
            {
                "checkpoint_id": CP,
                "document_pid": DOC,
                "expected_restore_fp": PRE,
                "checkpoint_artifact_fp": ART,
                "operation": "logical.batch",
            }
        ]

    def resolve_recovery(self, runtime_document_id, **kwargs):
        self.recovery_calls.append((runtime_document_id, deepcopy(kwargs)))
        assert kwargs["strategy"] == "R2_CHECKPOINT_RESTORE"
        assert kwargs["expected_restore_fp"] == PRE
        self.current_fp = PRE
        self.checkpoint_present = False
        return {
            "schema_version": 1,
            "operation": "logical.batch",
            "outcome": "ROLLED_BACK_VERIFIED",
            "document_pid": DOC,
            "runtime_document_id": RUNTIME2,
            "checkpoint_id": CP,
            "rollback": {
                "strategy": "R2_CHECKPOINT_RESTORE",
                "status": "ROLLED_BACK_VERIFIED",
                "expected_restore_fp": PRE,
                "actual_restore_fp": PRE,
            },
        }


def test_g3_success_splits_70_entities_yields_chunks_and_finalizes_one_checkpoint(tmp_path):
    client = FakeLogicalClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "logical.jsonl")

    result = executor.create_entities(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=PRE,
        entities=_entities(70),
    )

    assert result.status == "COMMITTED"
    assert result.chunk_count == 3
    assert [len(call[1]["entities"]) for call in client.chunk_calls] == [32, 32, 6]
    assert len(client.begin_calls) == 1
    assert len(client.finalize_calls) == 1
    assert client.recovery_calls == []
    events = [json.loads(line)["event"] for line in (tmp_path / "logical.jsonl").read_text().splitlines()]
    assert events == [
        "logical_begin",
        "chunk_committed",
        "chunk_committed",
        "chunk_committed",
        "logical_finalize_pending",
        "logical_commit",
    ]


def test_g3_middle_chunk_failure_restores_entire_logical_predecessor_with_r2(tmp_path):
    client = FakeLogicalClient(fail_chunk=1)
    executor = NativeLogicalBatchExecutor(client, tmp_path / "logical.jsonl")

    result = executor.create_entities(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=PRE,
        entities=_entities(70),
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert result.final_fp == PRE
    assert result.runtime_document_id == RUNTIME2
    assert len(client.chunk_calls) == 2
    assert len(client.recovery_calls) == 1
    assert client.finalize_calls == []
    events = [json.loads(line)["event"] for line in (tmp_path / "logical.jsonl").read_text().splitlines()]
    assert events[-1] == "logical_rollback"


def test_g3_insert_blocks_uses_same_chunking_and_one_checkpoint(tmp_path):
    client = FakeLogicalClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "insert.jsonl")
    inserts = tuple(
        {
            "definition_pid": "pid:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "position": [float(i), 0.0, 0.0],
            "rotation": 0.0,
            "scale": 1.0,
        }
        for i in range(33)
    )

    result = executor.insert_blocks(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=PRE,
        inserts=inserts,
    )

    assert result.status == "COMMITTED"
    assert result.chunk_count == 2
    assert [len(call[1]["entities"]) for call in client.chunk_calls] == [32, 1]
    assert len(client.begin_calls) == 1
    assert len(client.finalize_calls) == 1


def test_g3_transform_chunks_pid_targets_and_preserves_exact_set(tmp_path):
    client = FakeLogicalClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "transform.jsonl")
    pids = tuple(f"pid:target-{i}" for i in range(34))

    result = executor.transform_entities(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=PRE,
        semantic_pids=pids,
        transform={"kind": "translate", "delta": [1.0, 0.0, 0.0]},
    )

    assert result.status == "COMMITTED"
    assert result.affected_semantic_pids == pids
    assert [len(call[1]["semantic_pids"]) for call in client.chunk_calls] == [32, 2]


def test_g3_unknown_chunk_completion_attempts_full_r2_restore(tmp_path):
    class UncertainClient(FakeLogicalClient):
        def batch_create_chunk(self, runtime_document_id, **kwargs):
            if len(self.chunk_calls) == 1:
                self.chunk_calls.append((runtime_document_id, deepcopy(kwargs)))
                raise TimeoutError("completion unknown")
            return super().batch_create_chunk(runtime_document_id, **kwargs)

    client = UncertainClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "uncertain.jsonl")

    result = executor.create_entities(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=PRE,
        entities=_entities(70),
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert result.final_fp == PRE
    assert len(client.recovery_calls) == 1
    events = [json.loads(line)["event"] for line in (tmp_path / "uncertain.jsonl").read_text().splitlines()]
    assert events[-1] == "logical_rollback"


def test_g3_begin_unknown_completion_discovers_checkpoint_and_restores_predecessor(tmp_path):
    class BeginTimeoutClient(FakeLogicalClient):
        def begin_logical_batch(self, runtime_document_id, **kwargs):
            self.begin_calls.append((runtime_document_id, deepcopy(kwargs)))
            self.checkpoint_present = True
            raise TimeoutError("begin completion unknown")

    client = BeginTimeoutClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "begin-timeout.jsonl")

    result = executor.create_entities(
        RUNTIME, document_pid=DOC, expected_parent_fp=PRE, entities=_entities(10)
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert result.final_fp == PRE
    assert client.chunk_calls == []
    assert len(client.recovery_calls) == 1
    events = [json.loads(line)["event"] for line in (tmp_path / "begin-timeout.jsonl").read_text().splitlines()]
    assert events[0] == "logical_begin_unknown"
    assert events[-1] == "logical_rollback"


def test_g3_independent_final_state_mismatch_rolls_back_before_finalize(tmp_path):
    class DriftClient(FakeLogicalClient):
        def document_state(self, runtime_document_id, *, document_pid):
            result = super().document_state(runtime_document_id, document_pid=document_pid)
            if self.chunk_calls:
                return {**result, "document_fp": "sha256:" + "9" * 64}
            return result

    client = DriftClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "readback-drift.jsonl")

    result = executor.create_entities(
        RUNTIME, document_pid=DOC, expected_parent_fp=PRE, entities=_entities(10)
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert client.finalize_calls == []
    assert len(client.recovery_calls) == 1


def test_g3_finalize_response_lost_after_server_finalized_is_proven_committed(tmp_path):
    class LostFinalizeResponseClient(FakeLogicalClient):
        def finalize_recovery(self, runtime_document_id, **kwargs):
            super().finalize_recovery(runtime_document_id, **kwargs)
            raise TimeoutError("finalize response lost")

    client = LostFinalizeResponseClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "finalize-lost.jsonl")

    result = executor.create_entities(
        RUNTIME, document_pid=DOC, expected_parent_fp=PRE, entities=_entities(10)
    )

    assert result.status == "COMMITTED"
    assert client.recovery_calls == []
    events = [json.loads(line)["event"] for line in (tmp_path / "finalize-lost.jsonl").read_text().splitlines()]
    assert events[-1] == "logical_commit_reconciled"


def test_g3_finalize_failure_with_checkpoint_still_present_rolls_back(tmp_path):
    class FailedFinalizeClient(FakeLogicalClient):
        def finalize_recovery(self, runtime_document_id, **kwargs):
            self.finalize_calls.append((runtime_document_id, deepcopy(kwargs)))
            raise TimeoutError("finalize never completed")

    client = FailedFinalizeClient()
    executor = NativeLogicalBatchExecutor(client, tmp_path / "finalize-failed.jsonl")

    result = executor.create_entities(
        RUNTIME, document_pid=DOC, expected_parent_fp=PRE, entities=_entities(10)
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert len(client.recovery_calls) == 1
