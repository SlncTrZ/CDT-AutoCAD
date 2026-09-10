"""Native bridge protocol — bounded typed wire contract shared with the staged .NET bridge.
Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 12:45
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO
from uuid import UUID

NATIVE_PROTOCOL_VERSION = "cdt-autocad-native-v1"
MAX_FRAME_BYTES = 65_536
MAX_ERROR_MESSAGE_CHARS = 512

_READ_ONLY_OPERATIONS = frozenset(
    {
        "bridge.health",
        "bridge.documents.list",
        "bridge.document.identity",
        "bridge.document.snapshot",
    }
)
_MUTATION_OPERATIONS = frozenset(
    {"entity.create.line", "entity.update.line", "entity.delete.line"}
)
_ALLOWED_OPERATIONS = _READ_ONLY_OPERATIONS | _MUTATION_OPERATIONS
_REQUEST_FIELDS = frozenset({"protocol", "request_id", "operation", "params"})
_DOCUMENT_IDENTITY_FIELDS = frozenset({"runtime_document_id", "document_pid"})
_MUTATION_BINDING_FIELDS = frozenset(
    {"runtime_document_id", "document_pid", "expected_parent_fp", "fault_stage"}
)
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FAULT_STAGES = frozenset({"after_apply_before_commit"})


class BridgeProtocolError(ValueError):
    """Typed wire-contract refusal that is safe to serialize without exception details."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = str(message)[:MAX_ERROR_MESSAGE_CHARS]
        super().__init__(f"{code}: {self.safe_message}")


def _canonical_uuid(value: Any, *, code: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise BridgeProtocolError(code, f"{field_name} must be a canonical UUID string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise BridgeProtocolError(code, f"{field_name} must be a canonical UUID string") from exc
    canonical = str(parsed)
    if value != canonical:
        raise BridgeProtocolError(code, f"{field_name} must use lowercase canonical UUID form")
    return canonical


def _require_mapping(value: Any, *, code: str, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BridgeProtocolError(code, f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise BridgeProtocolError(code, f"{field_name} keys must be strings")
    return value


@dataclass(frozen=True)
class DocumentIdentityParams:
    """Read-only runtime-document selector; lineage PID is assertion only, never sole selector."""

    runtime_document_id: str
    document_pid: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DocumentIdentityParams:
        unknown = set(value) - _DOCUMENT_IDENTITY_FIELDS
        if unknown:
            raise BridgeProtocolError("INVALID_PARAMS", "document identity params contain unknown fields")
        runtime_id = value.get("runtime_document_id")
        if runtime_id is None:
            raise BridgeProtocolError(
                "RUNTIME_DOCUMENT_ID_REQUIRED",
                "runtime_document_id is required; document_pid alone cannot select a live document",
            )
        runtime_id = _canonical_uuid(
            runtime_id,
            code="INVALID_RUNTIME_DOCUMENT_ID",
            field_name="runtime_document_id",
        )
        document_pid = value.get("document_pid")
        if document_pid is not None:
            if not isinstance(document_pid, str) or not document_pid.strip():
                raise BridgeProtocolError("INVALID_PARAMS", "document_pid must be a non-empty string")
        return cls(runtime_document_id=runtime_id, document_pid=document_pid)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"runtime_document_id": self.runtime_document_id}
        if self.document_pid is not None:
            result["document_pid"] = self.document_pid
        return result


def _mutation_binding(value: Mapping[str, Any], allowed: frozenset[str]) -> tuple[str, str, str, str | None]:
    unknown = set(value) - allowed
    if unknown:
        raise BridgeProtocolError("INVALID_PARAMS", "mutation params contain unknown fields")
    runtime_id = _canonical_uuid(
        value.get("runtime_document_id"),
        code="INVALID_RUNTIME_DOCUMENT_ID",
        field_name="runtime_document_id",
    )
    document_pid = value.get("document_pid")
    if not isinstance(document_pid, str) or not document_pid.strip():
        raise BridgeProtocolError("INVALID_PARAMS", "document_pid is required for mutation")
    expected_parent_fp = value.get("expected_parent_fp")
    if not isinstance(expected_parent_fp, str) or _FINGERPRINT_RE.fullmatch(expected_parent_fp) is None:
        raise BridgeProtocolError("INVALID_PARAMS", "expected_parent_fp must be a canonical sha256 fingerprint")
    fault_stage = value.get("fault_stage")
    if fault_stage is not None and fault_stage not in _FAULT_STAGES:
        raise BridgeProtocolError("INVALID_PARAMS", "fault_stage is not enabled")
    return runtime_id, document_pid, expected_parent_fp, fault_stage


def _point3(value: Any, field_name: str) -> tuple[float, float, float]:
    if not isinstance(value, list | tuple) or len(value) != 3:
        raise BridgeProtocolError("INVALID_PARAMS", f"{field_name} must be a three-coordinate array")
    if any(isinstance(item, bool) or not isinstance(item, int | float) for item in value):
        raise BridgeProtocolError("INVALID_PARAMS", f"{field_name} coordinates must be numbers")
    result = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in result):
        raise BridgeProtocolError("INVALID_PARAMS", f"{field_name} coordinates must be finite")
    return result  # type: ignore[return-value]


@dataclass(frozen=True)
class LineCreateParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LineCreateParams:
        allowed = _MUTATION_BINDING_FIELDS | {"start", "end"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        start = _point3(value.get("start"), "start")
        end = _point3(value.get("end"), "end")
        if start == end:
            raise BridgeProtocolError("INVALID_PARAMS", "line start and end must differ")
        return cls(runtime_id, document_pid, parent_fp, start, end, fault_stage)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "start": list(self.start),
            "end": list(self.end),
        }
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class LineTargetParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    semantic_pid: str
    start: tuple[float, float, float] | None = None
    end: tuple[float, float, float] | None = None
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, require_geometry: bool) -> LineTargetParams:
        allowed = _MUTATION_BINDING_FIELDS | {"semantic_pid"}
        if require_geometry:
            allowed |= {"start", "end"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        semantic_pid = value.get("semantic_pid")
        if not isinstance(semantic_pid, str) or not semantic_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "semantic_pid is required for target mutation")
        start = end = None
        if require_geometry:
            start = _point3(value.get("start"), "start")
            end = _point3(value.get("end"), "end")
            if start == end:
                raise BridgeProtocolError("INVALID_PARAMS", "line start and end must differ")
        return cls(runtime_id, document_pid, parent_fp, semantic_pid, start, end, fault_stage)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "semantic_pid": self.semantic_pid,
        }
        if self.start is not None and self.end is not None:
            result["start"] = list(self.start)
            result["end"] = list(self.end)
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class BridgeRequest:
    """Strict typed request envelope for the staged native bridge."""

    protocol: str
    request_id: str
    operation: str
    params: DocumentIdentityParams | LineCreateParams | LineTargetParams | Mapping[str, Any]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BridgeRequest:
        request = _require_mapping(value, code="INVALID_REQUEST", field_name="request")
        if set(request) != _REQUEST_FIELDS:
            raise BridgeProtocolError(
                "INVALID_REQUEST",
                "request must contain exactly protocol, request_id, operation and params",
            )
        protocol = request.get("protocol")
        if protocol != NATIVE_PROTOCOL_VERSION:
            raise BridgeProtocolError("PROTOCOL_MISMATCH", "unsupported native bridge protocol")
        request_id = _canonical_uuid(
            request.get("request_id"),
            code="INVALID_REQUEST_ID",
            field_name="request_id",
        )
        operation = request.get("operation")
        if not isinstance(operation, str) or operation not in _ALLOWED_OPERATIONS:
            raise BridgeProtocolError("UNSUPPORTED_OPERATION", "operation is not enabled")
        raw_params = _require_mapping(
            request.get("params"),
            code="INVALID_PARAMS",
            field_name="params",
        )
        if operation in {"bridge.document.identity", "bridge.document.snapshot"}:
            params: DocumentIdentityParams | LineCreateParams | LineTargetParams | Mapping[str, Any] = (
                DocumentIdentityParams.from_dict(raw_params)
            )
        elif operation == "entity.create.line":
            params = LineCreateParams.from_dict(raw_params)
        elif operation == "entity.update.line":
            params = LineTargetParams.from_dict(raw_params, require_geometry=True)
        elif operation == "entity.delete.line":
            params = LineTargetParams.from_dict(raw_params, require_geometry=False)
        else:
            if raw_params:
                raise BridgeProtocolError("INVALID_PARAMS", f"{operation} does not accept parameters")
            params = {}
        return cls(
            protocol=NATIVE_PROTOCOL_VERSION,
            request_id=request_id,
            operation=operation,
            params=params,
        )

    def to_dict(self) -> dict[str, Any]:
        params = self.params.to_dict() if hasattr(self.params, "to_dict") else dict(self.params)
        return {
            "protocol": self.protocol,
            "request_id": self.request_id,
            "operation": self.operation,
            "params": params,
        }


@dataclass(frozen=True)
class BridgeResponse:
    """Typed correlated response with bounded safe error messages."""

    request_id: str | None
    ok: bool
    result: Mapping[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def success(cls, request_id: str, result: Mapping[str, Any]) -> BridgeResponse:
        canonical = _canonical_uuid(
            request_id,
            code="INVALID_REQUEST_ID",
            field_name="request_id",
        )
        return cls(request_id=canonical, ok=True, result=dict(result))

    @classmethod
    def error(
        cls,
        request_id: str | None,
        *,
        code: str,
        message: str,
    ) -> BridgeResponse:
        if request_id is not None:
            request_id = _canonical_uuid(
                request_id,
                code="INVALID_REQUEST_ID",
                field_name="request_id",
            )
        if not isinstance(code, str) or _ERROR_CODE_RE.fullmatch(code) is None:
            raise ValueError("error code must be an uppercase stable token")
        return cls(
            request_id=request_id,
            ok=False,
            error_code=code,
            error_message=str(message)[:MAX_ERROR_MESSAGE_CHARS],
        )

    def to_dict(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": self.request_id,
            "ok": self.ok,
        }
        if self.ok:
            base["result"] = dict(self.result or {})
        else:
            base["error"] = {
                "code": self.error_code,
                "message": self.error_message or "",
            }
        return base


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    try:
        text = json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise BridgeProtocolError("INVALID_JSON", "payload is not valid JSON data") from exc
    return text.encode("utf-8")


def encode_frame(payload: Mapping[str, Any]) -> bytes:
    """Encode one bounded little-endian length-prefixed UTF-8 JSON frame."""

    body = _json_bytes(payload)
    if not body:
        raise BridgeProtocolError("INVALID_FRAME_LENGTH", "frame payload must not be empty")
    if len(body) > MAX_FRAME_BYTES:
        raise BridgeProtocolError("FRAME_TOO_LARGE", "frame exceeds maximum payload size")
    return struct.pack("<I", len(body)) + body


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise BridgeProtocolError("TRUNCATED_FRAME", "frame ended before declared length")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def decode_frame(stream: BinaryIO) -> dict[str, Any]:
    """Decode one frame and reject invalid size before allocating/reading its body."""

    header = _read_exact(stream, 4)
    (length,) = struct.unpack("<I", header)
    if length == 0:
        raise BridgeProtocolError("INVALID_FRAME_LENGTH", "frame payload length must be positive")
    if length > MAX_FRAME_BYTES:
        raise BridgeProtocolError("FRAME_TOO_LARGE", "frame exceeds maximum payload size")
    body = _read_exact(stream, length)
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise BridgeProtocolError("INVALID_UTF8", "frame body is not valid UTF-8") from exc
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BridgeProtocolError("INVALID_JSON", "frame body is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise BridgeProtocolError("INVALID_JSON_OBJECT", "frame JSON root must be an object")
    return decoded
