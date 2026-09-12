"""G2 schema-agnostic metadata — strict wire contract and client behavior.
Wing: code | Topic: native-g2-metadata | Updated: 2026-09-11 17:35
"""

from __future__ import annotations

import pytest

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.protocol import (
    BridgeProtocolError,
    BridgeRequest,
    MetadataGetParams,
    MetadataQueryParams,
    MetadataSetParams,
    encode_frame,
)

RUNTIME_ID = "11111111-1111-4111-8111-111111111111"
DOCUMENT_PID = "pid:22222222-2222-4222-8222-222222222222"
ENTITY_PID = "pid:33333333-3333-4333-8333-333333333333"
PARENT_FP = "sha256:" + "a" * 64


def test_metadata_set_is_schema_agnostic_but_reserved_namespaces_are_refused():
    params = MetadataSetParams.from_dict(
        {
            "runtime_document_id": RUNTIME_ID,
            "document_pid": DOCUMENT_PID,
            "expected_parent_fp": PARENT_FP,
            "semantic_pid": ENTITY_PID,
            "namespace": "customer.mechanical.v1",
            "value": {
                "part_no": "P-100",
                "revision": 3,
                "qa": {"approved": False, "tags": ["machined", "fixture"]},
            },
        }
    )
    payload = params.to_dict()
    assert payload["namespace"] == "customer.mechanical.v1"
    assert payload["value"]["qa"]["approved"] is False

    for reserved in ("slnctrz.internal", "cdt.identity", "provider.recovery"):
        with pytest.raises(BridgeProtocolError, match="reserved"):
            MetadataSetParams.from_dict(
                {
                    "runtime_document_id": RUNTIME_ID,
                    "document_pid": DOCUMENT_PID,
                    "expected_parent_fp": PARENT_FP,
                    "semantic_pid": ENTITY_PID,
                    "namespace": reserved,
                    "value": {"x": 1},
                }
            )


def test_metadata_payload_is_bounded_and_rejects_non_json_or_excessive_depth():
    deep = current = {}
    for index in range(10):
        child = {}
        current[f"k{index}"] = child
        current = child
    with pytest.raises(BridgeProtocolError):
        MetadataSetParams.from_dict(
            {
                "runtime_document_id": RUNTIME_ID,
                "document_pid": DOCUMENT_PID,
                "expected_parent_fp": PARENT_FP,
                "semantic_pid": ENTITY_PID,
                "namespace": "customer.deep.v1",
                "value": deep,
            }
        )

    with pytest.raises(BridgeProtocolError):
        MetadataSetParams.from_dict(
            {
                "runtime_document_id": RUNTIME_ID,
                "document_pid": DOCUMENT_PID,
                "expected_parent_fp": PARENT_FP,
                "semantic_pid": ENTITY_PID,
                "namespace": "customer.big.v1",
                "value": {"text": "x" * 9000},
            }
        )


def test_metadata_get_and_query_use_exact_bounded_schema():
    get_params = MetadataGetParams.from_dict(
        {
            "runtime_document_id": RUNTIME_ID,
            "document_pid": DOCUMENT_PID,
            "semantic_pid": ENTITY_PID,
            "namespace": "customer.mechanical.v1",
        }
    )
    assert get_params.namespace == "customer.mechanical.v1"

    query = MetadataQueryParams.from_dict(
        {
            "runtime_document_id": RUNTIME_ID,
            "document_pid": DOCUMENT_PID,
            "namespace": "customer.mechanical.v1",
            "path": "qa.approved",
            "equals": False,
            "limit": 100,
        }
    )
    assert query.path == "qa.approved"
    assert query.limit == 100

    with pytest.raises(BridgeProtocolError):
        MetadataQueryParams.from_dict(
            {
                "runtime_document_id": RUNTIME_ID,
                "document_pid": DOCUMENT_PID,
                "namespace": "customer.mechanical.v1",
                "path": "qa.approved",
                "equals": False,
                "limit": 1001,
            }
        )


def test_metadata_operations_roundtrip_bridge_request_and_frame():
    request = BridgeRequest.from_dict(
        {
            "protocol": "cdt-autocad-native-v1",
            "request_id": "44444444-4444-4444-8444-444444444444",
            "operation": "metadata.set",
            "params": {
                "runtime_document_id": RUNTIME_ID,
                "document_pid": DOCUMENT_PID,
                "expected_parent_fp": PARENT_FP,
                "semantic_pid": ENTITY_PID,
                "namespace": "customer.mechanical.v1",
                "value": {"hole_class": "H7"},
            },
        }
    )
    assert request.operation == "metadata.set"
    assert isinstance(request.params, MetadataSetParams)
    assert len(encode_frame(request.to_dict())) < 65_536


def test_native_client_exposes_metadata_get_set_query():
    calls = []

    class Transport:
        def round_trip(self, payload):
            calls.append(payload)
            return {
                "protocol": "cdt-autocad-native-v1",
                "request_id": payload["request_id"],
                "ok": True,
                "result": {"operation": payload["operation"]},
            }

    client = NativeBridgeClient(transport=Transport())

    got = client.metadata_get(
        runtime_document_id=RUNTIME_ID,
        document_pid=DOCUMENT_PID,
        semantic_pid=ENTITY_PID,
        namespace="customer.mechanical.v1",
    )
    set_result = client.metadata_set(
        runtime_document_id=RUNTIME_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        semantic_pid=ENTITY_PID,
        namespace="customer.mechanical.v1",
        value={"finish": "anodized"},
    )
    query = client.metadata_query(
        runtime_document_id=RUNTIME_ID,
        document_pid=DOCUMENT_PID,
        namespace="customer.mechanical.v1",
        path="finish",
        equals="anodized",
        limit=50,
    )

    assert [got["operation"], set_result["operation"], query["operation"]] == [
        "metadata.get",
        "metadata.set",
        "metadata.query",
    ]
    assert [call["operation"] for call in calls] == ["metadata.get", "metadata.set", "metadata.query"]



def test_metadata_numbers_are_bounded_to_native_decimal_range():
    base = {
        "runtime_document_id": RUNTIME_ID,
        "document_pid": DOCUMENT_PID,
        "expected_parent_fp": PARENT_FP,
        "semantic_pid": ENTITY_PID,
        "namespace": "customer.numeric.v1",
    }
    accepted = MetadataSetParams.from_dict({**base, "value": {"magnitude": 10**28}})
    assert accepted.value["magnitude"] == 10**28

    with pytest.raises(BridgeProtocolError, match="decimal range"):
        MetadataSetParams.from_dict({**base, "value": {"magnitude": 10**40}})
    with pytest.raises(BridgeProtocolError, match="decimal range"):
        MetadataSetParams.from_dict({**base, "value": {"magnitude": 1e100}})
