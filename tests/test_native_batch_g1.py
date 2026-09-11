"""G1 generic batch geometry — strict typed batch-create chunk contract.
Wing: code | Topic: generic-batch-g1 | Updated: 2026-09-11 12:30
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

import pytest

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.protocol import (
    MAX_BATCH_CHUNK_ENTITIES,
    NATIVE_PROTOCOL_VERSION,
    BatchCreateParams,
    BatchInsertBlocksParams,
    BatchTransformParams,
    BridgeProtocolError,
    BridgeRequest,
    encode_frame,
)

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
DOCUMENT_PID = "doc:33333333-3333-4333-8333-333333333333"
PARENT_FP = "sha256:" + "a" * 64
POST_FP = "sha256:" + "b" * 64
PID_A = "pid:44444444-4444-4444-8444-444444444444"
PID_B = "pid:55555555-5555-4555-8555-555555555555"
DEFINITION_PID = "pid:66666666-6666-4666-8666-666666666666"


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


def test_g1_batch_create_contract_accepts_mixed_generic_primitives():
    request = BridgeRequest.from_dict(
        payload(
            "entity.batch.create",
            binding(
                entities=[
                    {"kind": "line", "start": [0, 0, 0], "end": [10, 0, 0]},
                    {"kind": "circle", "center": [5, 5, 0], "radius": 2},
                    {
                        "kind": "arc",
                        "center": [20, 5, 0],
                        "radius": 3,
                        "start_angle": 0.0,
                        "end_angle": 1.5,
                    },
                    {
                        "kind": "lwpolyline",
                        "points": [[0, 0], [5, 0], [5, 5]],
                        "closed": True,
                    },
                ]
            ),
        )
    )

    assert isinstance(request.params, BatchCreateParams)
    assert [item.kind for item in request.params.entities] == [
        "line",
        "circle",
        "arc",
        "lwpolyline",
    ]
    assert request.params.entities[0].start == (0.0, 0.0, 0.0)
    assert request.params.entities[1].radius == 2.0
    assert request.params.entities[3].closed is True


def test_g1_batch_create_is_bounded_and_rejects_domain_or_unknown_schema():
    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(payload("entity.batch.create", binding(entities=[])))

    too_many = [
        {"kind": "line", "start": [index, 0, 0], "end": [index + 1, 0, 0]}
        for index in range(MAX_BATCH_CHUNK_ENTITIES + 1)
    ]
    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(payload("entity.batch.create", binding(entities=too_many)))

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload(
                "entity.batch.create",
                binding(
                    entities=[
                        {
                            "kind": "line",
                            "start": [0, 0, 0],
                            "end": [1, 0, 0],
                            "road_class": "arterial",
                        }
                    ]
                ),
            )
        )

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload(
                "entity.batch.create",
                binding(entities=[{"kind": "road", "start": [0, 0, 0], "end": [1, 0, 0]}]),
            )
        )


def test_g1_batch_create_reuses_single_entity_geometry_validation():
    bad_entities = [
        {"kind": "line", "start": [0, 0, 0], "end": [0, 0, 0]},
        {"kind": "circle", "center": [0, 0, 0], "radius": 0},
        {
            "kind": "arc",
            "center": [0, 0, 0],
            "radius": 1,
            "start_angle": 1.0,
            "end_angle": 1.0,
        },
        {"kind": "lwpolyline", "points": [[0, 0]], "closed": False},
    ]
    for entity in bad_entities:
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                payload("entity.batch.create", binding(entities=[entity]))
            )


def test_g1_batch_create_fault_stage_is_precommit_only():
    entity = {"kind": "line", "start": [0, 0, 0], "end": [1, 0, 0]}
    valid = BridgeRequest.from_dict(
        payload(
            "entity.batch.create",
            binding(entities=[entity], fault_stage="after_apply_before_commit"),
        )
    )
    assert isinstance(valid.params, BatchCreateParams)
    assert valid.params.fault_stage == "after_apply_before_commit"

    for invalid in ("after_commit_add_stray", "after_commit_corrupt_target", "eval:anything"):
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                payload(
                    "entity.batch.create",
                    binding(entities=[entity], fault_stage=invalid),
                )
            )


def test_g1_schema_valid_max_chunk_still_obeys_wire_frame_limit_before_transport():
    points = [
        [1.234567890123456e300 + index * 1e285, -1.234567890123456e300 - index * 1e285]
        for index in range(128)
    ]
    entities = [
        {"kind": "lwpolyline", "points": points, "closed": False}
        for _ in range(MAX_BATCH_CHUNK_ENTITIES)
    ]
    request = BridgeRequest.from_dict(
        payload("entity.batch.create", binding(entities=entities))
    )

    with pytest.raises(BridgeProtocolError, match="FRAME_TOO_LARGE"):
        encode_frame(request.to_dict())


def test_g1_client_emits_one_strict_batch_create_chunk_request():
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.create",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "post_document_fp": POST_FP,
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
            {"kind": "circle", "center": [5, 5, 0], "radius": 2},
        ),
    )

    assert result["outcome"] == "COMMITTED_VERIFIED"
    assert transport.requests[-1]["operation"] == "entity.batch.create"
    assert len(transport.requests[-1]["params"]["entities"]) == 2
    assert transport.requests[-1]["params"]["entities"][0]["kind"] == "line"


@pytest.mark.parametrize(
    "transform",
    [
        {"kind": "translate", "delta": [2, -3, 0]},
        {"kind": "rotate_z", "center": [0, 0, 0], "angle": 1.25},
        {"kind": "scale_uniform", "center": [5, 5, 0], "factor": 2.0},
    ],
)
def test_g1_batch_transform_contract_accepts_planar_similarity_transforms(transform):
    request = BridgeRequest.from_dict(
        payload(
            "entity.batch.transform",
            binding(semantic_pids=[PID_A, PID_B], transform=transform),
        )
    )

    assert isinstance(request.params, BatchTransformParams)
    assert request.params.semantic_pids == (PID_A, PID_B)
    assert request.params.transform.kind == transform["kind"]


def test_g1_batch_transform_contract_rejects_unbounded_or_domain_schema():
    invalid_payloads = [
        binding(semantic_pids=[], transform={"kind": "translate", "delta": [1, 0, 0]}),
        binding(semantic_pids=[PID_A, PID_A], transform={"kind": "translate", "delta": [1, 0, 0]}),
        binding(semantic_pids=["handle:2A"], transform={"kind": "translate", "delta": [1, 0, 0]}),
        binding(semantic_pids=[PID_A], transform={"kind": "translate", "delta": [0, 0, 0]}),
        binding(semantic_pids=[PID_A], transform={"kind": "translate", "delta": [1, 0, 1]}),
        binding(semantic_pids=[PID_A], transform={"kind": "rotate_z", "center": [0, 0, 1], "angle": 1}),
        binding(semantic_pids=[PID_A], transform={"kind": "rotate_z", "center": [0, 0, 0], "angle": 0}),
        binding(semantic_pids=[PID_A], transform={"kind": "scale_uniform", "center": [0, 0, 0], "factor": 1}),
        binding(semantic_pids=[PID_A], transform={"kind": "shear", "xy": 0.5}),
        binding(
            semantic_pids=[PID_A],
            transform={"kind": "translate", "delta": [1, 0, 0]},
            road_class="arterial",
        ),
    ]
    invalid_payloads.append(
        binding(
            semantic_pids=[
                f"pid:{index:08x}-4444-4444-8444-{index:012x}"
                for index in range(MAX_BATCH_CHUNK_ENTITIES + 1)
            ],
            transform={"kind": "translate", "delta": [1, 0, 0]},
        )
    )

    for params in invalid_payloads:
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(payload("entity.batch.transform", params))


def test_g1_batch_transform_fault_stage_is_precommit_only():
    valid = BridgeRequest.from_dict(
        payload(
            "entity.batch.transform",
            binding(
                semantic_pids=[PID_A],
                transform={"kind": "translate", "delta": [1, 0, 0]},
                fault_stage="after_apply_before_commit",
            ),
        )
    )
    assert isinstance(valid.params, BatchTransformParams)
    assert valid.params.fault_stage == "after_apply_before_commit"

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload(
                "entity.batch.transform",
                binding(
                    semantic_pids=[PID_A],
                    transform={"kind": "translate", "delta": [1, 0, 0]},
                    fault_stage="after_commit_corrupt_target",
                ),
            )
        )


def test_g1_client_emits_one_strict_batch_transform_chunk_request():
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.transform",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "post_document_fp": POST_FP,
            "affected_semantic_pids": [PID_A, PID_B],
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.batch_transform_chunk(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pids=(PID_A, PID_B),
        transform={"kind": "rotate_z", "center": [0, 0, 0], "angle": 0.5},
    )

    assert result["outcome"] == "COMMITTED_VERIFIED"
    assert transport.requests[-1]["operation"] == "entity.batch.transform"
    assert transport.requests[-1]["params"]["semantic_pids"] == [PID_A, PID_B]
    assert transport.requests[-1]["params"]["transform"]["kind"] == "rotate_z"


def test_g1_batch_insert_blocks_contract_uses_definition_pid_not_block_name():
    request = BridgeRequest.from_dict(
        payload(
            "entity.batch.insert_blocks",
            binding(
                inserts=[
                    {
                        "definition_pid": DEFINITION_PID,
                        "position": [10, 20, 0],
                        "rotation": 0.0,
                        "scale": 1.0,
                    },
                    {
                        "definition_pid": DEFINITION_PID,
                        "position": [30, 40, 0],
                        "rotation": 1.25,
                        "scale": 2.0,
                    },
                ]
            ),
        )
    )

    assert isinstance(request.params, BatchInsertBlocksParams)
    assert len(request.params.inserts) == 2
    assert request.params.inserts[0].definition_pid == DEFINITION_PID
    assert request.params.inserts[1].scale == 2.0


def test_g1_batch_insert_blocks_contract_rejects_unsafe_or_domain_schema():
    good = {
        "definition_pid": DEFINITION_PID,
        "position": [10, 20, 0],
        "rotation": 0.0,
        "scale": 1.0,
    }
    invalid_inserts = [
        [],
        [{**good, "definition_name": "VALVE"}],
        [{**good, "definition_pid": "handle:AB"}],
        [{**good, "position": [10, 20, 1]}],
        [{**good, "rotation": 7.0}],
        [{**good, "scale": 0}],
        [{**good, "scale": 1e7}],
        [{**good, "road_class": "arterial"}],
        [good for _ in range(MAX_BATCH_CHUNK_ENTITIES + 1)],
    ]
    for inserts in invalid_inserts:
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                payload("entity.batch.insert_blocks", binding(inserts=inserts))
            )


def test_g1_batch_insert_blocks_fault_stage_is_precommit_only():
    insert = {
        "definition_pid": DEFINITION_PID,
        "position": [10, 20, 0],
        "rotation": 0.0,
        "scale": 1.0,
    }
    valid = BridgeRequest.from_dict(
        payload(
            "entity.batch.insert_blocks",
            binding(inserts=[insert], fault_stage="after_apply_before_commit"),
        )
    )
    assert isinstance(valid.params, BatchInsertBlocksParams)
    assert valid.params.fault_stage == "after_apply_before_commit"

    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            payload(
                "entity.batch.insert_blocks",
                binding(inserts=[insert], fault_stage="after_commit_add_stray"),
            )
        )


def test_g1_client_emits_one_strict_batch_insert_blocks_chunk_request():
    transport = FakeTransport(
        {
            "schema_version": 1,
            "operation": "entity.batch.insert_blocks",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "post_document_fp": POST_FP,
            "affected_semantic_pids": [PID_A],
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.batch_insert_blocks_chunk(
        RUNTIME_DOCUMENT_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        inserts=(
            {
                "definition_pid": DEFINITION_PID,
                "position": [10, 20, 0],
                "rotation": 0.5,
                "scale": 1.25,
            },
        ),
    )

    assert result["outcome"] == "COMMITTED_VERIFIED"
    assert transport.requests[-1]["operation"] == "entity.batch.insert_blocks"
    assert transport.requests[-1]["params"]["inserts"][0]["definition_pid"] == DEFINITION_PID
