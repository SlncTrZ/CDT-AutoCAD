"""AC-A01 affected-scoped verify — client contract for the new receipt fields.
Wing: code | Topic: affected-scoped-verify | Updated: 2026-10-06 20:30
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.protocol import NATIVE_PROTOCOL_VERSION

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
DOCUMENT_PID = "doc:33333333-3333-4333-8333-333333333333"
PARENT_FP = "sha256:" + "a" * 64
POST_FP = "sha256:" + "b" * 64
AFFECTED_FP = "sha256:" + "c" * 64


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
