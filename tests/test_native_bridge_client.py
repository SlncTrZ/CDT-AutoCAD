"""Native bridge client — N3 correlation, typed remote errors and document binding.
Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:50
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from cdt_autocad.native_bridge.client import (
    BridgeClientProtocolError,
    BridgeRemoteError,
    NativeBridgeClient,
)
from cdt_autocad.native_bridge.protocol import NATIVE_PROTOCOL_VERSION

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME_DOCUMENT_ID = "22222222-2222-4222-8222-222222222222"


class FakeTransport:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.requests: list[dict] = []

    def round_trip(self, payload: Mapping) -> Mapping:
        request = dict(payload)
        self.requests.append(request)
        return self.response_factory(request)


def success(request: dict, result: dict) -> dict:
    return {
        "protocol": NATIVE_PROTOCOL_VERSION,
        "request_id": request["request_id"],
        "ok": True,
        "result": result,
    }


def test_health_sends_strict_read_only_request_and_returns_result():
    transport = FakeTransport(lambda req: success(req, {"mutation_enabled": False}))
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.health()

    assert result == {"mutation_enabled": False}
    assert transport.requests == [
        {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": REQUEST_ID,
            "operation": "bridge.health",
            "params": {},
        }
    ]


def test_document_identity_binds_runtime_id_and_optional_lineage_assertion():
    transport = FakeTransport(
        lambda req: success(
            req,
            {
                "runtime_document_id": RUNTIME_DOCUMENT_ID,
                "document_pid": "doc:lineage",
            },
        )
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.document_identity(RUNTIME_DOCUMENT_ID, document_pid="doc:lineage")

    assert result["runtime_document_id"] == RUNTIME_DOCUMENT_ID
    assert transport.requests[0]["params"] == {
        "runtime_document_id": RUNTIME_DOCUMENT_ID,
        "document_pid": "doc:lineage",
    }


def test_documents_list_uses_no_selector_and_returns_documents():
    docs = [{"runtime_document_id": RUNTIME_DOCUMENT_ID, "document_pid": None}]
    transport = FakeTransport(lambda req: success(req, {"documents": docs}))
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    assert client.documents_list() == docs
    assert transport.requests[0]["operation"] == "bridge.documents.list"
    assert transport.requests[0]["params"] == {}


def test_remote_typed_error_is_raised_without_losing_code():
    def response(req):
        return {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": req["request_id"],
            "ok": False,
            "error": {
                "code": "DOCUMENT_BINDING_MISMATCH",
                "message": "lineage assertion does not match runtime document",
            },
        }

    client = NativeBridgeClient(FakeTransport(response), request_id_factory=lambda: REQUEST_ID)

    with pytest.raises(BridgeRemoteError) as caught:
        client.document_identity(RUNTIME_DOCUMENT_ID, document_pid="doc:wrong")
    assert caught.value.code == "DOCUMENT_BINDING_MISMATCH"


def test_response_correlation_mismatch_fails_closed():
    transport = FakeTransport(
        lambda req: {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": "33333333-3333-4333-8333-333333333333",
            "ok": True,
            "result": {},
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    with pytest.raises(BridgeClientProtocolError, match="RESPONSE_CORRELATION_MISMATCH"):
        client.health()


def test_response_protocol_mismatch_fails_closed():
    transport = FakeTransport(
        lambda req: {
            "protocol": "cdt-autocad-native-v2",
            "request_id": req["request_id"],
            "ok": True,
            "result": {},
        }
    )
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    with pytest.raises(BridgeClientProtocolError, match="RESPONSE_PROTOCOL_MISMATCH"):
        client.health()


def test_malformed_success_and_error_responses_fail_closed():
    bad_responses = (
        lambda req: {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": req["request_id"],
            "ok": True,
        },
        lambda req: {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": req["request_id"],
            "ok": False,
            "error": {"message": "missing code"},
        },
    )
    for response in bad_responses:
        client = NativeBridgeClient(FakeTransport(response), request_id_factory=lambda: REQUEST_ID)
        with pytest.raises(BridgeClientProtocolError, match="INVALID_RESPONSE"):
            client.health()


def test_client_validates_request_locally_before_transport():
    transport = FakeTransport(lambda req: success(req, {}))
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    with pytest.raises(Exception, match="INVALID_RUNTIME_DOCUMENT_ID"):
        client.document_identity("not-a-runtime-id")
    assert transport.requests == []
