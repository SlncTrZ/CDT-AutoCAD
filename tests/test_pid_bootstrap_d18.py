"""D18 PID bootstrap — atomic/rollback-capable document-lineage initialization.
Wing: code | Topic: d18-pid-bootstrap | Updated: 2026-09-19 16:45
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from cdt_autocad.errors import BackendQuarantinedError, MutationCompletionUncertainError
from cdt_autocad.mutation_coordinator import MutationCoordinator
from cdt_autocad.native_bridge.client import BridgeRemoteError, NativeBridgeClient
from cdt_autocad.native_bridge.protocol import (
    NATIVE_PROTOCOL_VERSION,
    BridgeProtocolError,
    BridgeRequest,
)
from cdt_autocad.native_bridge.public_runtime import NativePublicFacade
from cdt_autocad.server import _run_native_mutation

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME = "22222222-2222-4222-8222-222222222222"
DOC = "doc:33333333-3333-4333-8333-333333333333"
FP = "sha256:" + "a" * 64


def _payload(params: dict) -> dict:
    return {
        "protocol": NATIVE_PROTOCOL_VERSION,
        "request_id": REQUEST_ID,
        "operation": "bridge.document.identity.initialize",
        "params": params,
    }


def _bootstrap_result() -> dict:
    return {
        "runtime_document_id": RUNTIME,
        "document_pid": DOC,
        "initialized": True,
        "readback_verified": True,
        "scope": "empty-current-space-only",
        "document_fp_schema_version": 3,
        "document_fp": FP,
        "entity_count": 0,
    }


def test_d18_protocol_allows_only_bounded_bootstrap_fault_stage():
    request = BridgeRequest.from_dict(
        _payload(
            {
                "runtime_document_id": RUNTIME,
                "fault_stage": "after_pid_before_verify",
            }
        )
    )

    assert request.params.runtime_document_id == RUNTIME
    assert request.params.fault_stage == "after_pid_before_verify"
    assert request.params.document_pid is None

    for invalid in ("anything", "after_commit", "eval:anything"):
        with pytest.raises(BridgeProtocolError, match="INVALID_PARAMS"):
            BridgeRequest.from_dict(
                _payload(
                    {
                        "runtime_document_id": RUNTIME,
                        "fault_stage": invalid,
                    }
                )
            )


def test_d18_client_can_inject_bootstrap_fault_only_for_direct_bridge_acceptance():
    class Transport:
        def __init__(self):
            self.requests = []

        def round_trip(self, payload):
            self.requests.append(dict(payload))
            return {
                "protocol": NATIVE_PROTOCOL_VERSION,
                "request_id": payload["request_id"],
                "ok": True,
                "result": _bootstrap_result(),
            }

    transport = Transport()
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.initialize_document_identity(
        RUNTIME,
        fault_stage="after_pid_before_verify",
    )

    assert result["document_pid"] == DOC
    assert transport.requests[0]["params"] == {
        "runtime_document_id": RUNTIME,
        "fault_stage": "after_pid_before_verify",
    }


@pytest.mark.asyncio
async def test_d18_active_switch_refusal_is_deterministic_and_does_not_quarantine(
    settings, tmp_path
):
    class Client:
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

        def initialize_document_identity(self, runtime_document_id):
            assert runtime_document_id == RUNTIME
            raise BridgeRemoteError(
                "DOCUMENT_NOT_ACTIVE",
                "bound document is no longer active",
                REQUEST_ID,
            )

    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=Client,
        journal_root=tmp_path,
    )

    coordinator = MutationCoordinator()
    with pytest.raises(BridgeRemoteError) as caught:
        await _run_native_mutation(coordinator, facade.bootstrap_document_identity)

    assert caught.value.code == "DOCUMENT_NOT_ACTIVE"
    assert coordinator.status()["quarantined"] is False
    coordinator.ensure_writable()


@pytest.mark.asyncio
async def test_d18_unknown_bootstrap_completion_quarantines_shared_writer(settings, tmp_path):
    class Client:
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

        def initialize_document_identity(self, runtime_document_id):
            assert runtime_document_id == RUNTIME
            raise TimeoutError("bridge response lost after bootstrap dispatch")

    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=Client,
        journal_root=tmp_path,
    )
    coordinator = MutationCoordinator()

    with pytest.raises(MutationCompletionUncertainError, match="completion is uncertain"):
        await _run_native_mutation(coordinator, facade.bootstrap_document_identity)

    assert coordinator.status()["quarantined"] is True
    assert coordinator.status()["quarantine_lane"] == "native"
    with pytest.raises(BackendQuarantinedError):
        async with coordinator.writer("com"):
            raise AssertionError("quarantined coordinator admitted a later writer")
