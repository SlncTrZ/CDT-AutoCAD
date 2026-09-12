"""Public native-integrity facade — active binding, no fallback, G2/G3 product behavior.
Wing: code | Topic: production-native-surface | Updated: 2026-09-11 19:05
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cdt_autocad.errors import UnsupportedCapabilityError
from cdt_autocad.native_bridge.public_runtime import NativePublicFacade

RUNTIME = "11111111-1111-4111-8111-111111111111"
DOC = "pid:22222222-2222-4222-8222-222222222222"
ENTITY = "pid:33333333-3333-4333-8333-333333333333"
PRE = "sha256:" + "1" * 64
POST = "sha256:" + "2" * 64
CP = "cp:44444444-4444-4444-8444-444444444444"
ART = "sha256:" + "3" * 64


def _snapshot(fp: str, *, metadata=None):
    entity = SimpleNamespace(
        semantic_pid=ENTITY,
        metadata=metadata or {},
    )
    return SimpleNamespace(
        fingerprints=SimpleNamespace(document_fp=fp),
        entities=(entity,),
    )


class FakeNativeClient:
    def __init__(self):
        self.fp = PRE
        self.metadata = {}
        self.begin_calls = []
        self.chunk_calls = []
        self.finalize_calls = []
        self.recovery_calls = []
        self.state_calls = []
        self.visual_style_calls = []

    def health(self):
        return {
            "bridge_version": "0.8.0-g3",
            "document_fp_schema_version": 3,
            "logical_batch_atomic": True,
        }

    def documents_list(self):
        return [
            {
                "runtime_document_id": RUNTIME,
                "document_pid": DOC,
                "name": "showcase.dwg",
                "file_name": r"C:\CAD\showcase.dwg",
                "is_active": True,
            }
        ]

    def document_state(self, runtime_document_id, *, document_pid):
        assert runtime_document_id == RUNTIME
        assert document_pid == DOC
        self.state_calls.append((runtime_document_id, document_pid))
        return {
            "schema_version": 1,
            "runtime_document_id": RUNTIME,
            "document_pid": DOC,
            "document_fp_schema_version": 3,
            "document_fp": self.fp,
            "entity_count": 4096,
        }

    def document_snapshot(self, runtime_document_id, *, document_pid):
        raise AssertionError("public native facade must not require the bounded full semantic snapshot")

    def viewport_visual_style_get(self, runtime_document_id, *, document_pid=None):
        self.visual_style_calls.append(("get", runtime_document_id, document_pid))
        return {
            "runtime_document_id": runtime_document_id,
            "document_pid": document_pid,
            "visual_style_handle": "A1",
            "visual_style_name": "2D Wireframe",
        }

    def viewport_visual_style_set(
        self,
        runtime_document_id,
        *,
        visual_style_handle,
        expected_current_handle,
        document_pid=None,
    ):
        self.visual_style_calls.append(
            (
                "set",
                runtime_document_id,
                document_pid,
                visual_style_handle,
                expected_current_handle,
            )
        )
        return {
            "runtime_document_id": runtime_document_id,
            "document_pid": document_pid,
            "previous_visual_style_handle": expected_current_handle,
            "visual_style_handle": visual_style_handle,
            "visual_style_name": "2D Wireframe",
            "readback_verified": True,
        }

    def begin_logical_batch(self, runtime_document_id, **kwargs):
        self.begin_calls.append((runtime_document_id, kwargs))
        return {
            "schema_version": 1,
            "status": "OPEN",
            "document_pid": DOC,
            "pre_document_fp": self.fp,
            "logical_transaction": {
                "checkpoint_id": CP,
                "checkpoint_artifact_fp": ART,
                "expected_restore_fp": PRE,
            },
        }

    def batch_create_chunk(self, runtime_document_id, **kwargs):
        self.chunk_calls.append((runtime_document_id, kwargs))
        pre = self.fp
        self.fp = POST
        return {
            "schema_version": 1,
            "operation": "entity.batch.create",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOC,
            "pre_document_fp": pre,
            "provisional_document_fp": POST,
            "post_document_fp": POST,
            "affected_semantic_pids": [f"pid:new-{len(self.chunk_calls)}-{i}" for i in range(len(kwargs["entities"]))],
            "recovery_checkpoint": {
                "checkpoint_id": CP,
                "checkpoint_artifact_fp": ART,
                "expected_restore_fp": PRE,
            },
        }

    def finalize_recovery(self, runtime_document_id, **kwargs):
        self.finalize_calls.append((runtime_document_id, kwargs))
        return {"schema_version": 1, "status": "FINALIZED", "checkpoint_id": CP}

    def metadata_get(self, runtime_document_id, **kwargs):
        value = self.metadata.get(kwargs["namespace"])
        return {
            "found": value is not None,
            "value": value,
            "semantic_pid": kwargs["semantic_pid"],
            "namespace": kwargs["namespace"],
        }

    def metadata_query(self, runtime_document_id, **kwargs):
        return {"count": 0, "items": [], "namespace": kwargs["namespace"]}

    def metadata_set(self, runtime_document_id, **kwargs):
        pre = self.fp
        self.metadata[kwargs["namespace"]] = kwargs["value"]
        self.fp = POST
        return {
            "schema_version": 1,
            "operation": "metadata.set",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOC,
            "pre_document_fp": pre,
            "provisional_document_fp": POST,
            "post_document_fp": POST,
            "affected_semantic_pid": kwargs["semantic_pid"],
            "namespace": kwargs["namespace"],
            "recovery_checkpoint": {
                "checkpoint_id": CP,
                "checkpoint_artifact_fp": ART,
                "expected_restore_fp": PRE,
            },
        }

    def recoveries_list(self):
        return [
            {
                "checkpoint_id": CP,
                "document_pid": DOC,
                "expected_restore_fp": PRE,
                "checkpoint_artifact_fp": ART,
                "operation": "metadata.set",
                "r1_context_available": True,
            }
        ]

    def resolve_recovery(self, runtime_document_id, **kwargs):
        self.recovery_calls.append((runtime_document_id, kwargs))
        self.fp = PRE
        self.metadata = {}
        return {
            "outcome": "ROLLED_BACK_VERIFIED",
            "runtime_document_id": RUNTIME,
            "rollback": {
                "status": "ROLLED_BACK_VERIFIED",
                "expected_restore_fp": PRE,
                "actual_restore_fp": PRE,
            },
        }


def test_native_facade_refuses_headless_backend_without_fallback(settings, tmp_path):
    facade = NativePublicFacade(
        settings,
        client_factory=lambda: FakeNativeClient(),
        journal_root=tmp_path,
    )
    with pytest.raises(UnsupportedCapabilityError, match="no headless fallback"):
        facade.status()


def test_native_status_binds_exactly_active_document(settings, tmp_path):
    client = FakeNativeClient()
    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=lambda: client,
        journal_root=tmp_path,
    )

    status = facade.status()

    assert status["fallback"] is False
    assert status["active_document"]["runtime_document_id"] == RUNTIME
    assert status["active_document"]["document_pid"] == DOC
    assert status["active_document"]["document_fp"] == PRE


def test_visual_style_facade_allows_runtime_bound_unsaved_document_and_guarded_restore(settings, tmp_path):
    class UnsavedViewClient(FakeNativeClient):
        def documents_list(self):
            return [
                {
                    "runtime_document_id": RUNTIME,
                    "document_pid": None,
                    "name": "Drawing1.dwg",
                    "file_name": "",
                    "is_active": True,
                }
            ]

    client = UnsavedViewClient()
    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=lambda: client,
        journal_root=tmp_path,
    )

    observed = facade.visual_style_get()
    restored = facade.visual_style_set("A1", expected_current_handle="B2")

    assert observed["visual_style_name"] == "2D Wireframe"
    assert restored["readback_verified"] is True
    assert client.visual_style_calls == [
        ("get", RUNTIME, None),
        ("set", RUNTIME, None, "A1", "B2"),
    ]
    assert client.state_calls == []


def test_public_batch_create_uses_g3_logical_checkpoint_and_durable_journal(settings, tmp_path):
    client = FakeNativeClient()
    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=lambda: client,
        journal_root=tmp_path,
    )

    result = facade.batch_create_entities(
        ({"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]},)
    )

    assert result["status"] == "COMMITTED"
    assert result["route"] == "native-managed-bridge"
    assert result["fallback"] is False
    assert len(client.begin_calls) == 1
    assert client.chunk_calls[0][1]["logical_transaction"]["checkpoint_id"] == CP
    assert len(client.finalize_calls) == 1
    assert result["journal_path"].endswith(".jsonl")


def test_public_metadata_set_independently_reads_back_and_finalizes(settings, tmp_path):
    client = FakeNativeClient()
    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=lambda: client,
        journal_root=tmp_path,
    )

    result = facade.metadata_set(
        ENTITY,
        "customer.mechanical.v1",
        {"part_no": "P-100", "revision": 3},
    )

    assert result["outcome"] == "COMMITTED_VERIFIED"
    assert result["independent_readback_verified"] is True
    assert result["fallback"] is False
    assert len(client.finalize_calls) == 1
    assert client.recovery_calls == []


def test_public_metadata_set_recovers_exact_predecessor_on_readback_mismatch(settings, tmp_path):
    class CorruptReadback(FakeNativeClient):
        def document_state(self, runtime_document_id, *, document_pid):
            result = super().document_state(runtime_document_id, document_pid=document_pid)
            if self.fp == POST:
                return {**result, "document_fp": "sha256:" + "9" * 64}
            return result

    client = CorruptReadback()
    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=lambda: client,
        journal_root=tmp_path,
    )

    result = facade.metadata_set(ENTITY, "customer.mechanical.v1", {"part_no": "P-100"})

    assert result["outcome"] == "ROLLED_BACK_VERIFIED"
    assert result["post_document_fp"] == PRE
    assert result["recovery_strategy"] == "R1_COMPENSATE"
    assert len(client.recovery_calls) == 1


def test_public_metadata_unknown_completion_discovers_checkpoint_and_recovers(settings, tmp_path):
    class TimeoutAfterNativeMutation(FakeNativeClient):
        def metadata_set(self, runtime_document_id, **kwargs):
            self.metadata[kwargs["namespace"]] = kwargs["value"]
            self.fp = POST
            raise TimeoutError("completion unknown")

    client = TimeoutAfterNativeMutation()
    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=lambda: client,
        journal_root=tmp_path,
    )

    result = facade.metadata_set(ENTITY, "customer.mechanical.v1", {"part_no": "P-100"})

    assert result["outcome"] == "ROLLED_BACK_VERIFIED"
    assert result["post_document_fp"] == PRE
    assert result["recovery_reason"] == "metadata dispatch completion became unknown"
    assert len(client.recovery_calls) == 1


def test_public_feature_execute_returns_feature_local_receipt_and_pacing(settings, tmp_path):
    client = FakeNativeClient()
    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=lambda: client,
        journal_root=tmp_path,
    )

    result = facade.feature_execute(
        feature_id="plaza.centerline.01",
        feature_sequence=1,
        correlation_id="showcase-01",
        actions=[
            {
                "operation": "create_entities",
                "entities": [
                    {"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]}
                ],
            }
        ],
    )

    assert result["status"] == "COMMITTED"
    assert result["feature_id"] == "plaza.centerline.01"
    assert result["feature_sequence"] == 1
    assert result["correlation_id"] == "showcase-01"
    assert result["recommended_next_delay_ms"] == 300
    assert result["native_chunk_count"] == 1
    assert result["route"] == "native-managed-bridge"
    assert result["fallback"] is False
    assert result["journal_path"].endswith(".jsonl")
