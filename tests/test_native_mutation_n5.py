"""Native typed mutation — N5 strict line mutation contract and client surface.
Wing: code | Topic: native-bridge-n5 | Updated: 2026-09-10 13:50
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
    LineCreateParams,
    LineTargetParams,
)

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
DOCUMENT_PID = "doc:33333333-3333-4333-8333-333333333333"
ENTITY_PID = "pid:44444444-4444-4444-8444-444444444444"
PARENT_FP = "sha256:" + "a" * 64
POST_FP = "sha256:" + "b" * 64


def payload(operation: str, params: dict) -> dict:
    return {
        "protocol": NATIVE_PROTOCOL_VERSION,
        "request_id": REQUEST_ID,
        "operation": operation,
        "params": params,
    }


def binding(**extra) -> dict:
    return {
        "runtime_document_id": RUNTIME_DOCUMENT_ID,
        "document_pid": DOCUMENT_PID,
        "expected_parent_fp": PARENT_FP,
        **extra,
    }


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


def committed_result(operation: str, pid: str = ENTITY_PID) -> dict:
    return {
        "schema_version": 1,
        "operation": operation,
        "outcome": "COMMITTED_VERIFIED",
        "document_pid": DOCUMENT_PID,
        "pre_document_fp": PARENT_FP,
        "post_document_fp": POST_FP,
        "affected_semantic_pid": pid,
    }


def test_n5_create_line_requires_composite_parent_binding_and_fixed_geometry():
    request = BridgeRequest.from_dict(
        payload(
            "entity.create.line",
            binding(start=[0, 0, 0], end=[10.0, 0.0, 0.0]),
        )
    )
    assert isinstance(request.params, LineCreateParams)
    assert request.params.document_pid == DOCUMENT_PID
    assert request.params.expected_parent_fp == PARENT_FP
    assert request.params.start == (0.0, 0.0, 0.0)
    assert request.params.end == (10.0, 0.0, 0.0)


@pytest.mark.parametrize("missing", ["runtime_document_id", "document_pid", "expected_parent_fp"])
def test_n5_mutation_rejects_missing_composite_binding(missing):
    params = binding(start=[0, 0, 0], end=[1, 0, 0])
    params.pop(missing)
    with pytest.raises(BridgeProtocolError):
        BridgeRequest.from_dict(payload("entity.create.line", params))


def test_n5_line_update_and_delete_require_semantic_pid_not_handle():
    update = BridgeRequest.from_dict(
        payload(
            "entity.update.line",
            binding(semantic_pid=ENTITY_PID, start=[1, 2, 0], end=[3, 4, 0]),
        )
    )
    assert isinstance(update.params, LineTargetParams)
    assert update.params.semantic_pid == ENTITY_PID

    delete = BridgeRequest.from_dict(
        payload("entity.delete.line", binding(semantic_pid=ENTITY_PID))
    )
    assert isinstance(delete.params, LineTargetParams)

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload("entity.delete.line", binding(semantic_pid=ENTITY_PID, handle="2AF"))
        )


def test_n5_fault_injection_is_closed_enum_and_arbitrary_execution_stays_forbidden():
    request = BridgeRequest.from_dict(
        payload(
            "entity.create.line",
            binding(
                start=[0, 0, 0],
                end=[1, 0, 0],
                fault_stage="after_apply_before_commit",
            ),
        )
    )
    assert request.params.fault_stage == "after_apply_before_commit"

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload(
                "entity.create.line",
                binding(start=[0, 0, 0], end=[1, 0, 0], fault_stage="eval:anything"),
            )
        )
    for operation in ("command", "shell", "lisp.eval", "csharp.eval"):
        with pytest.raises(BridgeProtocolError, match="UNSUPPORTED_OPERATION"):
            BridgeRequest.from_dict(payload(operation, {}))


def test_n5_line_points_must_be_finite_three_coordinate_arrays_and_nonzero_length():
    for bad in ([0, 0], [0, 0, float("nan")], [0, 0, float("inf")], "0,0,0"):
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                payload("entity.create.line", binding(start=bad, end=[1, 0, 0]))
            )
    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload("entity.create.line", binding(start=[1, 2, 3], end=[1, 2, 3]))
        )


def test_n5_client_emits_strict_create_update_delete_requests():
    transport = FakeTransport(committed_result("entity.create.line"))
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)
    created = client.create_line(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        start=(0, 0, 0),
        end=(5, 0, 0),
    )
    assert created["outcome"] == "COMMITTED_VERIFIED"
    assert transport.requests[-1]["params"] == binding(start=[0.0, 0.0, 0.0], end=[5.0, 0.0, 0.0])

    transport.result = committed_result("entity.update.line")
    client.update_line(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pid=ENTITY_PID,
        start=(1, 0, 0),
        end=(6, 0, 0),
    )
    assert transport.requests[-1]["operation"] == "entity.update.line"
    assert transport.requests[-1]["params"]["semantic_pid"] == ENTITY_PID

    transport.result = committed_result("entity.delete.line")
    client.delete_line(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pid=ENTITY_PID,
    )
    assert transport.requests[-1]["operation"] == "entity.delete.line"


def test_n5_client_can_request_deterministic_precommit_fault_injection():
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.create.line",
            "outcome": "ROLLED_BACK_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "post_document_fp": PARENT_FP,
            "affected_semantic_pid": ENTITY_PID,
            "rollback": {
                "strategy": "R0_ABORT",
                "expected_restore_fp": PARENT_FP,
                "actual_restore_fp": PARENT_FP,
                "status": "ROLLED_BACK_VERIFIED",
            },
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)
    result = client.create_line(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        start=(0, 0, 0),
        end=(5, 0, 0),
        fault_stage="after_apply_before_commit",
    )
    assert result["outcome"] == "ROLLED_BACK_VERIFIED"
    assert transport.requests[-1]["params"]["fault_stage"] == "after_apply_before_commit"
