"""N7 two-phase commit integrity — recovery protocol/client/executor contract.
Wing: code | Topic: native-bridge-n7 | Updated: 2026-09-10 20:25
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

import pytest

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.protocol import (
    NATIVE_PROTOCOL_VERSION,
    BridgeProtocolError,
    BridgeRequest,
    RecoveryFinalizeParams,
    RecoveryResolveParams,
)

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
RESTORED_RUNTIME_ID = "33333333-3333-4333-8333-333333333333"
DOCUMENT_PID = "doc:44444444-4444-4444-8444-444444444444"
ENTITY_PID = "pid:55555555-5555-4555-8555-555555555555"
CHECKPOINT_ID = "cp:66666666-6666-4666-8666-666666666666"
PARENT_FP = "sha256:" + "a" * 64
POST_FP = "sha256:" + "b" * 64
ARTIFACT_FP = "sha256:" + "c" * 64


def payload(operation: str, params: dict) -> dict:
    return {
        "protocol": NATIVE_PROTOCOL_VERSION,
        "request_id": REQUEST_ID,
        "operation": operation,
        "params": params,
    }


class FakeTransport:
    def __init__(self, results: list[dict]):
        self.results = list(results)
        self.requests: list[dict] = []

    def round_trip(self, request: Mapping) -> Mapping:
        self.requests.append(deepcopy(dict(request)))
        result = deepcopy(self.results.pop(0))
        return {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": request["request_id"],
            "ok": True,
            "result": result,
        }


def test_n7_closed_fault_enum_includes_precommit_and_two_postcommit_boundaries():
    base = {
        "runtime_document_id": RUNTIME_DOCUMENT_ID,
        "document_pid": DOCUMENT_PID,
        "expected_parent_fp": PARENT_FP,
        "start": [0, 0, 0],
        "end": [5, 0, 0],
    }
    for fault_stage in (
        "after_apply_before_commit",
        "before_commit_add_stray",
        "after_commit_corrupt_target",
        "after_commit_add_stray",
    ):
        request = BridgeRequest.from_dict(
            payload("entity.create.line", {**base, "fault_stage": fault_stage})
        )
        assert request.params.fault_stage == fault_stage

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload("entity.create.line", {**base, "fault_stage": "after_commit_eval:anything"})
        )


def test_n7_recovery_resolve_contract_is_strict_and_binds_checkpoint_artifact_parent():
    request = BridgeRequest.from_dict(
        payload(
            "bridge.recovery.resolve",
            {
                "runtime_document_id": RUNTIME_DOCUMENT_ID,
                "document_pid": DOCUMENT_PID,
                "checkpoint_id": CHECKPOINT_ID,
                "checkpoint_artifact_fp": ARTIFACT_FP,
                "expected_restore_fp": PARENT_FP,
                "strategy": "R1_COMPENSATE",
            },
        )
    )
    assert isinstance(request.params, RecoveryResolveParams)
    assert request.params.strategy == "R1_COMPENSATE"
    assert request.params.checkpoint_id == CHECKPOINT_ID

    for bad_strategy in ("UNDO", "R0_ABORT", "eval:anything"):
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                payload(
                    "bridge.recovery.resolve",
                    {
                        "runtime_document_id": RUNTIME_DOCUMENT_ID,
                        "document_pid": DOCUMENT_PID,
                        "checkpoint_id": CHECKPOINT_ID,
                        "checkpoint_artifact_fp": ARTIFACT_FP,
                        "expected_restore_fp": PARENT_FP,
                        "strategy": bad_strategy,
                    },
                )
            )

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload(
                "bridge.recovery.resolve",
                {
                    "runtime_document_id": RUNTIME_DOCUMENT_ID,
                    "document_pid": DOCUMENT_PID,
                    "checkpoint_id": CHECKPOINT_ID,
                    "checkpoint_artifact_fp": ARTIFACT_FP,
                    "expected_restore_fp": PARENT_FP,
                    "strategy": "R2_CHECKPOINT_RESTORE",
                    "path": r"C:\arbitrary\file.dwg",
                },
            )
        )


def test_n7_recovery_finalize_contract_requires_accepted_post_fingerprint():
    request = BridgeRequest.from_dict(
        payload(
            "bridge.recovery.finalize",
            {
                "runtime_document_id": RUNTIME_DOCUMENT_ID,
                "document_pid": DOCUMENT_PID,
                "checkpoint_id": CHECKPOINT_ID,
                "checkpoint_artifact_fp": ARTIFACT_FP,
                "accepted_post_fp": POST_FP,
            },
        )
    )
    assert isinstance(request.params, RecoveryFinalizeParams)
    assert request.params.accepted_post_fp == POST_FP

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload(
                "bridge.recovery.finalize",
                {
                    "runtime_document_id": RUNTIME_DOCUMENT_ID,
                    "document_pid": DOCUMENT_PID,
                    "checkpoint_id": CHECKPOINT_ID,
                    "checkpoint_artifact_fp": "sha256:" + "A" * 64,
                    "accepted_post_fp": POST_FP,
                },
            )
        )


def test_n7_recovery_list_accepts_no_params_and_arbitrary_exec_remains_forbidden():
    request = BridgeRequest.from_dict(payload("bridge.recovery.list", {}))
    assert request.params == {}

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(payload("bridge.recovery.list", {"document_pid": DOCUMENT_PID}))

    for operation in ("bridge.recovery.exec", "bridge.recovery.command", "shell", "csharp.eval"):
        with pytest.raises(BridgeProtocolError, match="UNSUPPORTED_OPERATION"):
            BridgeRequest.from_dict(payload(operation, {}))


def test_n7_client_emits_strict_recovery_resolve_finalize_and_list_requests():
    transport = FakeTransport(
        [
            {
                "schema_version": 1,
                "outcome": "ROLLED_BACK_VERIFIED",
                "document_pid": DOCUMENT_PID,
                "runtime_document_id": RUNTIME_DOCUMENT_ID,
                "checkpoint_id": CHECKPOINT_ID,
                "rollback": {
                    "rollback_id": "rb:77777777-7777-4777-8777-777777777777",
                    "reason": "COMMIT_INTEGRITY_FAIL",
                    "strategy": "R1_COMPENSATE",
                    "expected_restore_fp": PARENT_FP,
                    "actual_restore_fp": PARENT_FP,
                    "status": "ROLLED_BACK_VERIFIED",
                },
            },
            {
                "schema_version": 1,
                "status": "FINALIZED",
                "checkpoint_id": CHECKPOINT_ID,
                "document_pid": DOCUMENT_PID,
                "accepted_post_fp": POST_FP,
            },
            {
                "schema_version": 1,
                "recoveries": [],
            },
        ]
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    recovered = client.resolve_recovery(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        checkpoint_id=CHECKPOINT_ID,
        checkpoint_artifact_fp=ARTIFACT_FP,
        expected_restore_fp=PARENT_FP,
        strategy="R1_COMPENSATE",
    )
    assert recovered["outcome"] == "ROLLED_BACK_VERIFIED"
    assert transport.requests[-1]["operation"] == "bridge.recovery.resolve"
    assert "path" not in transport.requests[-1]["params"]

    finalized = client.finalize_recovery(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        checkpoint_id=CHECKPOINT_ID,
        checkpoint_artifact_fp=ARTIFACT_FP,
        accepted_post_fp=POST_FP,
    )
    assert finalized["status"] == "FINALIZED"
    assert transport.requests[-1]["operation"] == "bridge.recovery.finalize"

    assert client.recoveries_list() == []
    assert transport.requests[-1]["operation"] == "bridge.recovery.list"
