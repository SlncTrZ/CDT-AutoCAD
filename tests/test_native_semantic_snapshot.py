"""Native semantic snapshot — N4 wire-to-N1 canonical compatibility contract.
Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 12:40
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from cdt_autocad.native_bridge.client import (
    BridgeClientProtocolError,
    NativeBridgeClient,
)
from cdt_autocad.native_bridge.protocol import NATIVE_PROTOCOL_VERSION, BridgeRequest
from cdt_autocad.semantic.fingerprint import fingerprint_document

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"
DOCUMENT_PID = "doc:33333333-3333-4333-8333-333333333333"
ENTITY_PID = "pid:44444444-4444-4444-8444-444444444444"


def _snapshot_result() -> dict:
    return {
        "schema_version": 1,
        "runtime_document_id": RUNTIME_DOCUMENT_ID,
        "document_pid": DOCUMENT_PID,
        "document": {
            "units": "millimeters",
            "current_space": "Model",
            "saved": True,
            "extents": {"min": [0.0, 0.0, 0.0], "max": [10.0, 0.0, 0.0]},
        },
        "entities": [
            {
                "semantic_pid": ENTITY_PID,
                "native_handle": "2AF",
                "entity_type": "LINE",
                "layer": "0",
                "geometry": {"start": [0.0, 0.0, 0.0], "end": [10.0, 0.0, 0.0]},
                "bbox": {"min": [0.0, 0.0, 0.0], "max": [10.0, 0.0, 0.0]},
                "metrics": {"length": 10.0},
                "style": {"linetype": "ByLayer", "lineweight": "ByLayer"},
                "hierarchy": {"owner_space": "Model"},
            }
        ],
        "relations": [],
        "styles": [{"kind": "layer", "name": "0", "is_off": False}],
        "document_fp_schema_version": 2,
        "document_fp": "",
    }


class SnapshotTransport:
    def __init__(self, result: dict):
        self.result = result
        self.requests: list[dict] = []

    def round_trip(self, payload: dict) -> dict:
        self.requests.append(deepcopy(payload))
        return {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": payload["request_id"],
            "ok": True,
            "result": deepcopy(self.result),
        }


def _result_with_valid_fp() -> tuple[dict, object]:
    raw = _snapshot_result()
    transport = SnapshotTransport(raw)
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)
    snapshot = client.document_snapshot(RUNTIME_DOCUMENT_ID, document_pid=DOCUMENT_PID, verify_fingerprint=False)
    raw["document_fp"] = fingerprint_document(snapshot)
    return raw, snapshot


def test_n4_protocol_accepts_only_typed_snapshot_binding_params():
    request = BridgeRequest.from_dict(
        {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": REQUEST_ID,
            "operation": "bridge.document.snapshot",
            "params": {
                "runtime_document_id": RUNTIME_DOCUMENT_ID,
                "document_pid": DOCUMENT_PID,
            },
        }
    )
    assert request.operation == "bridge.document.snapshot"
    assert request.params.runtime_document_id == RUNTIME_DOCUMENT_ID


def test_document_snapshot_maps_native_wire_shape_into_n1_semantic_snapshot():
    raw, expected_without_native_fp = _result_with_valid_fp()
    transport = SnapshotTransport(raw)
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    snapshot = client.document_snapshot(RUNTIME_DOCUMENT_ID, document_pid=DOCUMENT_PID)

    assert snapshot.document_pid == DOCUMENT_PID
    assert snapshot.units == "millimeters"
    assert snapshot.current_space == "Model"
    assert snapshot.entities[0].semantic_pid == ENTITY_PID
    assert snapshot.entities[0].metrics["length"] == 10.0
    assert snapshot.fingerprints.document_fp == fingerprint_document(snapshot)
    assert fingerprint_document(snapshot) == fingerprint_document(expected_without_native_fp)
    assert transport.requests[0]["operation"] == "bridge.document.snapshot"


def test_document_snapshot_rejects_native_fingerprint_mismatch():
    raw, _ = _result_with_valid_fp()
    raw["document_fp"] = "sha256:" + "0" * 64
    client = NativeBridgeClient(
        SnapshotTransport(raw),
        request_id_factory=lambda: REQUEST_ID,
    )

    with pytest.raises(BridgeClientProtocolError, match="SNAPSHOT_FINGERPRINT_MISMATCH"):
        client.document_snapshot(RUNTIME_DOCUMENT_ID, document_pid=DOCUMENT_PID)


def test_document_snapshot_rejects_missing_or_duplicate_semantic_pid():
    raw, _ = _result_with_valid_fp()
    raw["entities"][0]["semantic_pid"] = None
    client = NativeBridgeClient(SnapshotTransport(raw), request_id_factory=lambda: REQUEST_ID)
    with pytest.raises(BridgeClientProtocolError, match="INVALID_SNAPSHOT"):
        client.document_snapshot(RUNTIME_DOCUMENT_ID, document_pid=DOCUMENT_PID, verify_fingerprint=False)

    raw = _snapshot_result()
    raw["entities"].append(deepcopy(raw["entities"][0]))
    client = NativeBridgeClient(SnapshotTransport(raw), request_id_factory=lambda: REQUEST_ID)
    with pytest.raises(BridgeClientProtocolError, match="INVALID_SNAPSHOT"):
        client.document_snapshot(RUNTIME_DOCUMENT_ID, document_pid=DOCUMENT_PID, verify_fingerprint=False)
