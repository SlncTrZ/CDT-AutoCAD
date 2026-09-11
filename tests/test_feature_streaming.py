"""Production feature streaming — feature-local atomicity over generic native CAD chunks.
Wing: code | Topic: production-feature-streaming | Updated: 2026-09-11 20:10
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from cdt_autocad.native_bridge.feature_stream import (
    DEFAULT_FEATURE_PACING_MS,
    FeatureExecutionError,
    FeatureStreamExecutor,
    validate_feature_request,
)

RUNTIME = "11111111-1111-4111-8111-111111111111"
DOC = "doc:22222222-2222-4222-8222-222222222222"
PRE = "sha256:" + "1" * 64
FP1 = "sha256:" + "2" * 64
FP2 = "sha256:" + "3" * 64
CP1 = "cp:33333333-3333-4333-8333-333333333333"
CP2 = "cp:44444444-4444-4444-8444-444444444444"
ART1 = "sha256:" + "4" * 64
ART2 = "sha256:" + "5" * 64


def _lines(count: int):
    return [
        {"kind": "line", "start": [float(i), 0.0, 0.0], "end": [float(i), 1.0, 0.0]}
        for i in range(count)
    ]


def test_feature_request_is_generic_strict_and_bounded():
    request = validate_feature_request(
        feature_id="road.centerline.001",
        feature_sequence=3,
        correlation_id="job-2026-09-11-001",
        actions=[{"operation": "create_entities", "entities": _lines(2)}],
    )
    assert request.feature_id == "road.centerline.001"
    assert request.feature_sequence == 3
    assert request.total_items == 2
    assert request.recommended_next_delay_ms == DEFAULT_FEATURE_PACING_MS == 300

    with pytest.raises(FeatureExecutionError, match="unsupported feature action"):
        validate_feature_request(
            feature_id="f1",
            feature_sequence=1,
            correlation_id="c1",
            actions=[{"operation": "draw_road", "road_class": "A1"}],
        )

    with pytest.raises(FeatureExecutionError, match="exact typed schema"):
        validate_feature_request(
            feature_id="f1",
            feature_sequence=1,
            correlation_id="c1",
            actions=[{"operation": "create_entities", "entities": _lines(1), "domain": "road"}],
        )


class FakeClient:
    def __init__(self):
        self.fp = PRE
        self.next_checkpoint = 0
        self.pending = None
        self.fail_next_chunk = False
        self.calls = []

    def begin_logical_batch(self, runtime_document_id, **kwargs):
        assert runtime_document_id == RUNTIME
        assert kwargs["expected_parent_fp"] == self.fp
        self.next_checkpoint += 1
        cp = CP1 if self.next_checkpoint == 1 else CP2
        art = ART1 if self.next_checkpoint == 1 else ART2
        self.pending = {
            "checkpoint_id": cp,
            "checkpoint_artifact_fp": art,
            "expected_restore_fp": self.fp,
            "document_pid": DOC,
        }
        return {
            "status": "OPEN",
            "document_pid": DOC,
            "pre_document_fp": self.fp,
            "logical_transaction": {
                "checkpoint_id": cp,
                "checkpoint_artifact_fp": art,
                "expected_restore_fp": self.fp,
            },
        }

    def _chunk(self, operation, kwargs, size, affected):
        self.calls.append((operation, deepcopy(kwargs)))
        pre = self.fp
        if self.fail_next_chunk:
            self.fail_next_chunk = False
            return {
                "operation": operation,
                "outcome": "ROLLED_BACK_VERIFIED",
                "document_pid": DOC,
                "pre_document_fp": pre,
                "post_document_fp": pre,
                "affected_semantic_pids": [],
                "recovery_checkpoint": {
                    "checkpoint_id": self.pending["checkpoint_id"],
                    "checkpoint_artifact_fp": self.pending["checkpoint_artifact_fp"],
                    "expected_restore_fp": self.pending["expected_restore_fp"],
                },
                "rollback": {"status": "ROLLED_BACK_VERIFIED"},
            }
        self.fp = FP1 if pre == PRE else FP2
        return {
            "operation": operation,
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOC,
            "pre_document_fp": pre,
            "provisional_document_fp": self.fp,
            "post_document_fp": self.fp,
            "affected_semantic_pids": affected,
            "recovery_checkpoint": {
                "checkpoint_id": self.pending["checkpoint_id"],
                "checkpoint_artifact_fp": self.pending["checkpoint_artifact_fp"],
                "expected_restore_fp": self.pending["expected_restore_fp"],
            },
        }

    def batch_create_chunk(self, runtime_document_id, **kwargs):
        return self._chunk(
            "entity.batch.create",
            kwargs,
            len(kwargs["entities"]),
            [f"pid:create-{len(self.calls)}-{i}" for i in range(len(kwargs["entities"]))],
        )

    def batch_insert_blocks_chunk(self, runtime_document_id, **kwargs):
        return self._chunk(
            "entity.batch.insert_blocks",
            kwargs,
            len(kwargs["inserts"]),
            [f"pid:insert-{len(self.calls)}-{i}" for i in range(len(kwargs["inserts"]))],
        )

    def batch_transform_chunk(self, runtime_document_id, **kwargs):
        return self._chunk(
            "entity.batch.transform",
            kwargs,
            len(kwargs["semantic_pids"]),
            list(kwargs["semantic_pids"]),
        )

    def document_state(self, runtime_document_id, *, document_pid):
        return {"document_fp": self.fp, "entity_count": 10}

    def finalize_recovery(self, runtime_document_id, **kwargs):
        assert kwargs["accepted_post_fp"] == self.fp
        self.pending = None
        return {"status": "FINALIZED"}

    def recoveries_list(self):
        if self.pending is None:
            return []
        return [{**self.pending, "operation": "logical.batch"}]

    def resolve_recovery(self, runtime_document_id, **kwargs):
        assert self.pending is not None
        self.fp = self.pending["expected_restore_fp"]
        self.pending = None
        return {
            "outcome": "ROLLED_BACK_VERIFIED",
            "runtime_document_id": RUNTIME,
            "rollback": {
                "status": "ROLLED_BACK_VERIFIED",
                "expected_restore_fp": self.fp,
                "actual_restore_fp": self.fp,
            },
        }


def test_feature_may_mix_generic_action_families_under_one_checkpoint(tmp_path):
    client = FakeClient()
    executor = FeatureStreamExecutor(client, tmp_path / "feature.jsonl")
    result = executor.execute(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=PRE,
        feature_id="plaza.kiosk-row.01",
        feature_sequence=1,
        correlation_id="showcase-01",
        actions=[
            {"operation": "create_entities", "entities": _lines(33)},
            {
                "operation": "insert_blocks",
                "inserts": [
                    {
                        "definition_pid": "pid:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                        "position": [0.0, 0.0, 0.0],
                        "rotation": 0.0,
                        "scale": 1.0,
                    }
                ],
            },
            {
                "operation": "transform_entities",
                "semantic_pids": ["pid:target-1", "pid:target-2"],
                "transform": {"kind": "translate", "delta": [1.0, 0.0, 0.0]},
            },
        ],
    )

    assert result.status == "COMMITTED"
    assert result.feature_id == "plaza.kiosk-row.01"
    assert result.action_count == 3
    assert result.native_chunk_count == 4
    assert result.recommended_next_delay_ms == 300
    assert [call[0] for call in client.calls] == [
        "entity.batch.create",
        "entity.batch.create",
        "entity.batch.insert_blocks",
        "entity.batch.transform",
    ]
    bindings = {call[1]["logical_transaction"]["checkpoint_id"] for call in client.calls}
    assert len(bindings) == 1


def test_failed_feature_rolls_back_only_that_feature_and_preserves_prior_feature(tmp_path):
    client = FakeClient()
    first = FeatureStreamExecutor(client, tmp_path / "feature-1.jsonl").execute(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=PRE,
        feature_id="feature-1",
        feature_sequence=1,
        correlation_id="job-1",
        actions=[{"operation": "create_entities", "entities": _lines(1)}],
    )
    assert first.status == "COMMITTED"
    assert client.fp == FP1

    client.fail_next_chunk = True
    second = FeatureStreamExecutor(client, tmp_path / "feature-2.jsonl").execute(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=FP1,
        feature_id="feature-2",
        feature_sequence=2,
        correlation_id="job-1",
        actions=[{"operation": "create_entities", "entities": _lines(1)}],
    )

    assert second.status == "ROLLED_BACK_VERIFIED"
    assert second.initial_fp == FP1
    assert second.final_fp == FP1
    assert second.failure is not None
    assert second.failure["feature_id"] == "feature-2"
    assert second.failure["action_index"] == 0
    assert second.failure["native_chunk_index"] == 0
    assert client.fp == FP1


def test_feature_journal_carries_correlation_and_feature_receipts(tmp_path):
    client = FakeClient()
    journal = tmp_path / "feature.jsonl"
    FeatureStreamExecutor(client, journal).execute(
        RUNTIME,
        document_pid=DOC,
        expected_parent_fp=PRE,
        feature_id="feature-a",
        feature_sequence=7,
        correlation_id="corr-abc",
        actions=[{"operation": "create_entities", "entities": _lines(1)}],
    )
    text = journal.read_text(encoding="utf-8")
    assert '"feature_id":"feature-a"' in text
    assert '"feature_sequence":7' in text
    assert '"correlation_id":"corr-abc"' in text
    assert '"event":"feature_commit"' in text
