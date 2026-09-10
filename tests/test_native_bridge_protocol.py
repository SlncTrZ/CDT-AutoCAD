"""Native bridge protocol — N3 typed framing and read-only operation contract.
Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:40
"""

from __future__ import annotations

import io
import json
import struct

import pytest

from cdt_autocad.native_bridge.protocol import (
    MAX_FRAME_BYTES,
    NATIVE_PROTOCOL_VERSION,
    BridgeProtocolError,
    BridgeRequest,
    BridgeResponse,
    DocumentIdentityParams,
    decode_frame,
    encode_frame,
)


REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"


def request_payload(operation: str = "bridge.health", params: dict | None = None) -> dict:
    return {
        "protocol": NATIVE_PROTOCOL_VERSION,
        "request_id": REQUEST_ID,
        "operation": operation,
        "params": {} if params is None else params,
    }


def test_protocol_version_is_frozen_for_n3():
    assert NATIVE_PROTOCOL_VERSION == "cdt-autocad-native-v1"
    assert MAX_FRAME_BYTES == 65_536


def test_request_accepts_only_read_only_n3_operations():
    for operation in ("bridge.health", "bridge.documents.list"):
        request = BridgeRequest.from_dict(request_payload(operation))
        assert request.operation == operation

    identity = BridgeRequest.from_dict(
        request_payload(
            "bridge.document.identity",
            {"runtime_document_id": RUNTIME_DOCUMENT_ID},
        )
    )
    assert identity.operation == "bridge.document.identity"

    for forbidden in (
        "execute",
        "command",
        "shell",
        "lisp.eval",
        "csharp.eval",
        "entity.create.line",
    ):
        with pytest.raises(BridgeProtocolError, match="UNSUPPORTED_OPERATION"):
            BridgeRequest.from_dict(request_payload(forbidden))


def test_request_requires_exact_protocol_and_canonical_uuid_request_id():
    bad_protocol = request_payload()
    bad_protocol["protocol"] = "cdt-autocad-native-v2"
    with pytest.raises(BridgeProtocolError, match="PROTOCOL_MISMATCH"):
        BridgeRequest.from_dict(bad_protocol)

    for invalid_id in (
        "not-a-uuid",
        "{11111111-1111-4111-8111-111111111111}",
        "11111111-1111-4111-8111-11111111111A",
    ):
        payload = request_payload()
        payload["request_id"] = invalid_id
        with pytest.raises(BridgeProtocolError, match="INVALID_REQUEST_ID"):
            BridgeRequest.from_dict(payload)


def test_request_rejects_unknown_top_level_fields():
    payload = request_payload()
    payload["code"] = "anything"

    with pytest.raises(BridgeProtocolError, match="INVALID_REQUEST"):
        BridgeRequest.from_dict(payload)


def test_document_identity_requires_runtime_document_id_not_pid_only():
    with pytest.raises(BridgeProtocolError, match="RUNTIME_DOCUMENT_ID_REQUIRED"):
        BridgeRequest.from_dict(
            request_payload(
                "bridge.document.identity",
                {"document_pid": "doc:lineage-only"},
            )
        )

    request = BridgeRequest.from_dict(
        request_payload(
            "bridge.document.identity",
            {
                "runtime_document_id": RUNTIME_DOCUMENT_ID,
                "document_pid": "doc:expected-lineage",
            },
        )
    )
    assert isinstance(request.params, DocumentIdentityParams)
    assert request.params.runtime_document_id == RUNTIME_DOCUMENT_ID
    assert request.params.document_pid == "doc:expected-lineage"


def test_health_and_document_list_reject_parameters():
    for operation in ("bridge.health", "bridge.documents.list"):
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(request_payload(operation, {"unexpected": True}))


def test_document_identity_rejects_unknown_params_and_noncanonical_runtime_id():
    with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            request_payload(
                "bridge.document.identity",
                {"runtime_document_id": RUNTIME_DOCUMENT_ID, "extra": 1},
            )
        )

    with pytest.raises(BridgeProtocolError, match="INVALID_RUNTIME_DOCUMENT_ID"):
        BridgeRequest.from_dict(
            request_payload(
                "bridge.document.identity",
                {"runtime_document_id": "{22222222-2222-4222-8222-222222222222}"},
            )
        )


def test_encode_frame_uses_uint32_little_endian_and_compact_utf8_json():
    payload = request_payload()
    frame = encode_frame(payload)
    (length,) = struct.unpack("<I", frame[:4])
    body = frame[4:]

    assert length == len(body)
    assert length <= MAX_FRAME_BYTES
    assert json.loads(body.decode("utf-8")) == payload
    assert b"\n" not in body


def test_decode_frame_round_trips_request_payload():
    payload = request_payload()
    assert decode_frame(io.BytesIO(encode_frame(payload))) == payload


def test_decode_frame_rejects_zero_and_oversized_lengths_before_payload_read():
    with pytest.raises(BridgeProtocolError, match="INVALID_FRAME_LENGTH"):
        decode_frame(io.BytesIO(struct.pack("<I", 0)))

    stream = io.BytesIO(struct.pack("<I", MAX_FRAME_BYTES + 1))
    with pytest.raises(BridgeProtocolError, match="FRAME_TOO_LARGE"):
        decode_frame(stream)
    assert stream.tell() == 4


def test_decode_frame_rejects_truncated_header_and_body():
    with pytest.raises(BridgeProtocolError, match="TRUNCATED_FRAME"):
        decode_frame(io.BytesIO(b"\x01\x00"))

    with pytest.raises(BridgeProtocolError, match="TRUNCATED_FRAME"):
        decode_frame(io.BytesIO(struct.pack("<I", 10) + b"{}"))


def test_decode_frame_rejects_invalid_utf8_non_object_and_malformed_json():
    cases = (
        (b"\xff", "INVALID_UTF8"),
        (b"[]", "INVALID_JSON_OBJECT"),
        (b"{", "INVALID_JSON"),
    )
    for body, code in cases:
        with pytest.raises(BridgeProtocolError, match=code):
            decode_frame(io.BytesIO(struct.pack("<I", len(body)) + body))


def test_encode_frame_rejects_payload_over_limit():
    payload = {"data": "x" * MAX_FRAME_BYTES}
    with pytest.raises(BridgeProtocolError, match="FRAME_TOO_LARGE"):
        encode_frame(payload)


def test_typed_success_response_preserves_correlation_id():
    response = BridgeResponse.success(REQUEST_ID, {"mutation_enabled": False})
    assert response.to_dict() == {
        "protocol": NATIVE_PROTOCOL_VERSION,
        "request_id": REQUEST_ID,
        "ok": True,
        "result": {"mutation_enabled": False},
    }


def test_typed_error_response_bounds_message_and_never_serializes_exception_detail():
    response = BridgeResponse.error(
        REQUEST_ID,
        code="INVALID_REQUEST",
        message="x" * 2048,
    )
    exported = response.to_dict()
    assert exported["ok"] is False
    assert exported["error"]["code"] == "INVALID_REQUEST"
    assert len(exported["error"]["message"]) <= 512
    assert "traceback" not in exported["error"]
