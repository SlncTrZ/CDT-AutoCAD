"""Native bridge client — staged Python-side request/correlation and semantic adapter layer.
Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 12:46
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from cdt_autocad.semantic.models import SemanticSnapshot

from .protocol import (
    NATIVE_PROTOCOL_VERSION,
    ArcCreateParams,
    ArcTargetParams,
    BridgeProtocolError,
    BridgeRequest,
    CircleCreateParams,
    CircleTargetParams,
    DocumentIdentityParams,
    LineCreateParams,
    LineTargetParams,
    PolylineCreateParams,
    PolylineTargetParams,
    RecoveryFinalizeParams,
    RecoveryResolveParams,
)
from .semantic import parse_native_snapshot


class BridgeTransport(Protocol):
    """Minimal transport contract; concrete Named Pipe transport is Windows-only."""

    def round_trip(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class BridgeClientProtocolError(RuntimeError):
    """Raised when the remote response violates the native bridge contract."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class BridgeRemoteError(RuntimeError):
    """Typed error returned by the native bridge."""

    def __init__(self, code: str, message: str, request_id: str):
        self.code = code
        self.request_id = request_id
        self.remote_message = message
        super().__init__(f"{code}: {message}")


@dataclass
class NativeBridgeClient:
    """Strict request builder that validates correlation and typed remote responses."""

    transport: BridgeTransport
    request_id_factory: Callable[[], str] = lambda: str(uuid4())

    def health(self) -> dict[str, Any]:
        return self._request("bridge.health", {})

    def documents_list(self) -> list[dict[str, Any]]:
        result = self._request("bridge.documents.list", {})
        documents = result.get("documents")
        if not isinstance(documents, list) or not all(isinstance(item, dict) for item in documents):
            raise BridgeClientProtocolError(
                "INVALID_RESPONSE",
                "bridge.documents.list result.documents must be an array of objects",
            )
        return documents

    def document_identity(
        self,
        runtime_document_id: str,
        *,
        document_pid: str | None = None,
    ) -> dict[str, Any]:
        params = DocumentIdentityParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                **({"document_pid": document_pid} if document_pid is not None else {}),
            }
        )
        return self._request("bridge.document.identity", params.to_dict())

    def document_snapshot(
        self,
        runtime_document_id: str,
        *,
        document_pid: str | None = None,
        verify_fingerprint: bool = True,
    ) -> SemanticSnapshot:
        """Read one authoritative native snapshot and verify N1 fingerprint compatibility."""

        params = DocumentIdentityParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                **({"document_pid": document_pid} if document_pid is not None else {}),
            }
        )
        result = self._request("bridge.document.snapshot", params.to_dict())
        try:
            return parse_native_snapshot(result, verify_fingerprint=verify_fingerprint)
        except BridgeProtocolError as exc:
            raise BridgeClientProtocolError(exc.code, exc.safe_message) from exc

    def create_line(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = LineCreateParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "start": list(start),
                "end": list(end),
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            }
        )
        return self._request("entity.create.line", params.to_dict())

    def update_line(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pid: str,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = LineTargetParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "semantic_pid": semantic_pid,
                "start": list(start),
                "end": list(end),
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            },
            require_geometry=True,
        )
        return self._request("entity.update.line", params.to_dict())

    def delete_line(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pid: str,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = LineTargetParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "semantic_pid": semantic_pid,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            },
            require_geometry=False,
        )
        return self._request("entity.delete.line", params.to_dict())

    def create_circle(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        center: tuple[float, float, float],
        radius: float,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = CircleCreateParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "center": list(center),
                "radius": radius,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            }
        )
        return self._request("entity.create.circle", params.to_dict())

    def update_circle(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pid: str,
        center: tuple[float, float, float],
        radius: float,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = CircleTargetParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "semantic_pid": semantic_pid,
                "center": list(center),
                "radius": radius,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            },
            require_geometry=True,
        )
        return self._request("entity.update.circle", params.to_dict())

    def delete_circle(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pid: str,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = CircleTargetParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "semantic_pid": semantic_pid,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            },
            require_geometry=False,
        )
        return self._request("entity.delete.circle", params.to_dict())

    def create_arc(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        center: tuple[float, float, float],
        radius: float,
        start_angle: float,
        end_angle: float,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = ArcCreateParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "center": list(center),
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            }
        )
        return self._request("entity.create.arc", params.to_dict())

    def update_arc(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pid: str,
        center: tuple[float, float, float],
        radius: float,
        start_angle: float,
        end_angle: float,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = ArcTargetParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "semantic_pid": semantic_pid,
                "center": list(center),
                "radius": radius,
                "start_angle": start_angle,
                "end_angle": end_angle,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            },
            require_geometry=True,
        )
        return self._request("entity.update.arc", params.to_dict())

    def delete_arc(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pid: str,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = ArcTargetParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "semantic_pid": semantic_pid,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            },
            require_geometry=False,
        )
        return self._request("entity.delete.arc", params.to_dict())

    def create_lwpolyline(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        points: tuple[tuple[float, float], ...],
        closed: bool,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = PolylineCreateParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "points": [list(point) for point in points],
                "closed": closed,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            }
        )
        return self._request("entity.create.lwpolyline", params.to_dict())

    def update_lwpolyline(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pid: str,
        points: tuple[tuple[float, float], ...],
        closed: bool,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = PolylineTargetParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "semantic_pid": semantic_pid,
                "points": [list(point) for point in points],
                "closed": closed,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            },
            require_geometry=True,
        )
        return self._request("entity.update.lwpolyline", params.to_dict())

    def delete_lwpolyline(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pid: str,
        fault_stage: str | None = None,
    ) -> dict[str, Any]:
        params = PolylineTargetParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "expected_parent_fp": expected_parent_fp,
                "semantic_pid": semantic_pid,
                **({"fault_stage": fault_stage} if fault_stage is not None else {}),
            },
            require_geometry=False,
        )
        return self._request("entity.delete.lwpolyline", params.to_dict())

    def resolve_recovery(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        checkpoint_id: str,
        checkpoint_artifact_fp: str,
        expected_restore_fp: str,
        strategy: str,
    ) -> dict[str, Any]:
        params = RecoveryResolveParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "checkpoint_id": checkpoint_id,
                "checkpoint_artifact_fp": checkpoint_artifact_fp,
                "expected_restore_fp": expected_restore_fp,
                "strategy": strategy,
            }
        )
        return self._request("bridge.recovery.resolve", params.to_dict())

    def finalize_recovery(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        checkpoint_id: str,
        checkpoint_artifact_fp: str,
        accepted_post_fp: str,
    ) -> dict[str, Any]:
        params = RecoveryFinalizeParams.from_dict(
            {
                "runtime_document_id": runtime_document_id,
                "document_pid": document_pid,
                "checkpoint_id": checkpoint_id,
                "checkpoint_artifact_fp": checkpoint_artifact_fp,
                "accepted_post_fp": accepted_post_fp,
            }
        )
        return self._request("bridge.recovery.finalize", params.to_dict())

    def recoveries_list(self) -> list[dict[str, Any]]:
        result = self._request("bridge.recovery.list", {})
        recoveries = result.get("recoveries")
        if not isinstance(recoveries, list) or not all(isinstance(item, dict) for item in recoveries):
            raise BridgeClientProtocolError(
                "INVALID_RESPONSE",
                "bridge.recovery.list result.recoveries must be an array of objects",
            )
        return recoveries

    def _request(self, operation: str, params: Mapping[str, Any]) -> dict[str, Any]:
        request_id = self.request_id_factory()
        request = BridgeRequest.from_dict(
            {
                "protocol": NATIVE_PROTOCOL_VERSION,
                "request_id": request_id,
                "operation": operation,
                "params": dict(params),
            }
        )
        raw_response = self.transport.round_trip(request.to_dict())
        return self._validate_response(raw_response, request.request_id)

    @staticmethod
    def _validate_response(
        response: Mapping[str, Any],
        expected_request_id: str,
    ) -> dict[str, Any]:
        if not isinstance(response, Mapping):
            raise BridgeClientProtocolError("INVALID_RESPONSE", "response must be an object")
        allowed_success = {"protocol", "request_id", "ok", "result"}
        allowed_error = {"protocol", "request_id", "ok", "error"}
        protocol = response.get("protocol")
        if protocol != NATIVE_PROTOCOL_VERSION:
            raise BridgeClientProtocolError(
                "RESPONSE_PROTOCOL_MISMATCH",
                "native bridge response protocol does not match request protocol",
            )
        if response.get("request_id") != expected_request_id:
            raise BridgeClientProtocolError(
                "RESPONSE_CORRELATION_MISMATCH",
                "native bridge response request_id does not match request",
            )
        ok = response.get("ok")
        if ok is True:
            if set(response) != allowed_success or not isinstance(response.get("result"), Mapping):
                raise BridgeClientProtocolError(
                    "INVALID_RESPONSE",
                    "successful response must contain exactly protocol, request_id, ok and result",
                )
            return dict(response["result"])
        if ok is False:
            if set(response) != allowed_error or not isinstance(response.get("error"), Mapping):
                raise BridgeClientProtocolError(
                    "INVALID_RESPONSE",
                    "error response must contain exactly protocol, request_id, ok and error",
                )
            error = response["error"]
            if set(error) != {"code", "message"}:
                raise BridgeClientProtocolError(
                    "INVALID_RESPONSE",
                    "error payload must contain exactly code and message",
                )
            code = error.get("code")
            message = error.get("message")
            if not isinstance(code, str) or not code or not isinstance(message, str):
                raise BridgeClientProtocolError(
                    "INVALID_RESPONSE",
                    "error code/message must be strings",
                )
            raise BridgeRemoteError(code, message, expected_request_id)
        raise BridgeClientProtocolError("INVALID_RESPONSE", "response ok must be a boolean")
