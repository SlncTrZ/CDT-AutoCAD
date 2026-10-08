"""O1 native shape mutation contract — CIRCLE, ARC and simple LWPOLYLINE.
Wing: code | Topic: native-bridge-o1 | Updated: 2026-09-10 15:30
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

import pytest

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.protocol import (
    NATIVE_PROTOCOL_VERSION,
    ArcCreateParams,
    ArcTargetParams,
    BridgeProtocolError,
    BridgeRequest,
    CircleCreateParams,
    CircleTargetParams,
    PolylineCreateParams,
    PolylineTargetParams,
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


def test_o1_circle_contract_requires_positive_radius_and_three_coordinate_center():
    request = BridgeRequest.from_dict(
        payload("entity.create.circle", binding(center=[10, 20, 0], radius=5))
    )
    assert isinstance(request.params, CircleCreateParams)
    assert request.params.center == (10.0, 20.0, 0.0)
    assert request.params.radius == 5.0

    update = BridgeRequest.from_dict(
        payload(
            "entity.update.circle",
            binding(semantic_pid=ENTITY_PID, center=[12, 22, 0], radius=7.5),
        )
    )
    assert isinstance(update.params, CircleTargetParams)
    assert update.params.semantic_pid == ENTITY_PID

    delete = BridgeRequest.from_dict(
        payload("entity.delete.circle", binding(semantic_pid=ENTITY_PID))
    )
    assert isinstance(delete.params, CircleTargetParams)

    for bad_radius in (0, -1, float("nan"), float("inf"), True, "5"):
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                payload("entity.create.circle", binding(center=[0, 0, 0], radius=bad_radius))
            )
    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload("entity.create.circle", binding(center=[0, 0], radius=1))
        )


def test_o1_arc_contract_uses_bounded_radian_angles_and_positive_radius():
    request = BridgeRequest.from_dict(
        payload(
            "entity.create.arc",
            binding(center=[0, 0, 0], radius=8, start_angle=0.25, end_angle=2.5),
        )
    )
    assert isinstance(request.params, ArcCreateParams)
    assert request.params.start_angle == 0.25
    assert request.params.end_angle == 2.5

    update = BridgeRequest.from_dict(
        payload(
            "entity.update.arc",
            binding(
                semantic_pid=ENTITY_PID,
                center=[1, 2, 0],
                radius=9,
                start_angle=0.5,
                end_angle=3.0,
            ),
        )
    )
    assert isinstance(update.params, ArcTargetParams)

    delete = BridgeRequest.from_dict(payload("entity.delete.arc", binding(semantic_pid=ENTITY_PID)))
    assert isinstance(delete.params, ArcTargetParams)

    bad_angles = [
        (-0.1, 1.0),
        (0.0, 2 * 3.141592653589793),
        (1.0, 1.0),
        (1.0, 1.0 + 5e-13),
        (float("nan"), 1.0),
        (0.0, float("inf")),
    ]
    for start_angle, end_angle in bad_angles:
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                payload(
                    "entity.create.arc",
                    binding(
                        center=[0, 0, 0],
                        radius=1,
                        start_angle=start_angle,
                        end_angle=end_angle,
                    ),
                )
            )


def test_o1_simple_lwpolyline_contract_is_bounded_and_rejects_degenerate_input():
    request = BridgeRequest.from_dict(
        payload(
            "entity.create.lwpolyline",
            binding(points=[[0, 0], [10, 0], [10, 5]], closed=True),
        )
    )
    assert isinstance(request.params, PolylineCreateParams)
    assert request.params.points == ((0.0, 0.0), (10.0, 0.0), (10.0, 5.0))
    assert request.params.closed is True

    update = BridgeRequest.from_dict(
        payload(
            "entity.update.lwpolyline",
            binding(
                semantic_pid=ENTITY_PID,
                points=[[0, 0], [12, 0], [12, 6]],
                closed=False,
            ),
        )
    )
    assert isinstance(update.params, PolylineTargetParams)

    delete = BridgeRequest.from_dict(
        payload("entity.delete.lwpolyline", binding(semantic_pid=ENTITY_PID))
    )
    assert isinstance(delete.params, PolylineTargetParams)

    bad = [
        ([], False),
        ([[0, 0]], False),
        ([[0, 0], [0, 0]], False),
        ([[0, 0], [1, 0]], True),
        ([[0, 0], [1, 0], [0, 0]], True),
        ([[0, 0, 1], [1, 0]], False),
    ]
    for points, closed in bad:
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                payload(
                    "entity.create.lwpolyline",
                    binding(points=points, closed=closed),
                )
            )


def test_o1_fault_stage_remains_closed_enum_for_new_shape_families():
    for operation, extra in (
        ("entity.create.circle", {"center": [0, 0, 0], "radius": 2}),
        (
            "entity.create.arc",
            {"center": [0, 0, 0], "radius": 2, "start_angle": 0.0, "end_angle": 1.0},
        ),
        (
            "entity.create.lwpolyline",
            {"points": [[0, 0], [2, 0], [2, 2]], "closed": True},
        ),
    ):
        valid = BridgeRequest.from_dict(
            payload(operation, binding(**extra, fault_stage="after_apply_before_commit"))
        )
        assert valid.params.fault_stage == "after_apply_before_commit"
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(payload(operation, binding(**extra, fault_stage="eval:anything")))


def test_o1_client_emits_strict_circle_arc_polyline_requests():
    transport = FakeTransport(committed_result("entity.create.circle"))
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    client.create_circle(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        center=(10, 20, 0),
        radius=5,
    )
    assert transport.requests[-1]["operation"] == "entity.create.circle"
    assert transport.requests[-1]["params"]["center"] == [10.0, 20.0, 0.0]

    transport.result = committed_result("entity.update.arc")
    client.update_arc(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pid=ENTITY_PID,
        center=(0, 0, 0),
        radius=6,
        start_angle=0.2,
        end_angle=2.0,
    )
    assert transport.requests[-1]["operation"] == "entity.update.arc"
    assert transport.requests[-1]["params"]["semantic_pid"] == ENTITY_PID

    transport.result = committed_result("entity.delete.lwpolyline")
    client.delete_lwpolyline(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pid=ENTITY_PID,
    )
    assert transport.requests[-1]["operation"] == "entity.delete.lwpolyline"
