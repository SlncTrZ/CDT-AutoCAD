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
from decimal import Decimal, InvalidOperation
from typing import Any, BinaryIO
from uuid import UUID

NATIVE_PROTOCOL_VERSION = "cdt-autocad-native-v1"
MAX_FRAME_BYTES = 65_536
MAX_ERROR_MESSAGE_CHARS = 512
MAX_SIMPLE_POLYLINE_VERTICES = 128
MAX_BATCH_CHUNK_ENTITIES = 32
MAX_METADATA_JSON_BYTES = 8_192
MAX_METADATA_DEPTH = 8
MAX_METADATA_KEYS = 256
MAX_METADATA_STRING_CHARS = 2_048
MAX_METADATA_QUERY_RESULTS = 1_000
MAX_METADATA_DECIMAL_ABS = Decimal("79228162514264337593543950335")
MAX_LOGICAL_BATCH_ITEMS = 10_000

_READ_ONLY_OPERATIONS = frozenset(
    {
        "bridge.health",
        "bridge.documents.list",
        "bridge.document.identity",
        "bridge.document.snapshot",
        "bridge.document.state",
        "viewport.visual_style.get",
        "bridge.recovery.list",
        "metadata.get",
        "metadata.query",
    }
)
_MUTATION_OPERATIONS = frozenset(
    {
        "entity.batch.create",
        "entity.batch.insert_blocks",
        "entity.batch.transform",
        "entity.create.line",
        "entity.update.line",
        "entity.delete.line",
        "entity.create.circle",
        "entity.update.circle",
        "entity.delete.circle",
        "entity.create.arc",
        "entity.update.arc",
        "entity.delete.arc",
        "entity.create.lwpolyline",
        "entity.update.lwpolyline",
        "entity.delete.lwpolyline",
        "metadata.set",
        "viewport.visual_style.set",
    }
)
_RECOVERY_OPERATIONS = frozenset(
    {
        "bridge.recovery.resolve",
        "bridge.recovery.finalize",
        "bridge.logical.begin",
    }
)
_ALLOWED_OPERATIONS = _READ_ONLY_OPERATIONS | _MUTATION_OPERATIONS | _RECOVERY_OPERATIONS
_REQUEST_FIELDS = frozenset({"protocol", "request_id", "operation", "params"})
_DOCUMENT_IDENTITY_FIELDS = frozenset({"runtime_document_id", "document_pid"})
_VISUAL_STYLE_SET_FIELDS = frozenset(
    {
        "runtime_document_id",
        "document_pid",
        "visual_style_handle",
        "expected_current_handle",
    }
)
_MUTATION_BINDING_FIELDS = frozenset(
    {"runtime_document_id", "document_pid", "expected_parent_fp", "fault_stage"}
)
_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ERROR_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_CHECKPOINT_ID_RE = re.compile(r"^cp:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_ENTITY_PID_RE = re.compile(r"^pid:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_OBJECT_HANDLE_RE = re.compile(r"^[0-9A-F]+$")
_METADATA_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}(?:\.[a-z0-9][a-z0-9_-]{0,31}){1,7}$")
_METADATA_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){0,7}$")
_RESERVED_METADATA_PREFIXES = ("slnctrz.", "cdt.", "provider.")
_FAULT_STAGES = frozenset(
    {
        "after_apply_before_commit",
        "before_commit_add_stray",
        "after_commit_corrupt_target",
        "after_commit_add_stray",
    }
)
_RECOVERY_STRATEGIES = frozenset({"R1_COMPENSATE", "R2_CHECKPOINT_RESTORE"})


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


@dataclass(frozen=True)
class ViewportVisualStyleSetParams:
    """Transient current-viewport visual-style restore guarded by exact predecessor state."""

    runtime_document_id: str
    document_pid: str | None
    visual_style_handle: str
    expected_current_handle: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ViewportVisualStyleSetParams:
        if set(value) - _VISUAL_STYLE_SET_FIELDS:
            raise BridgeProtocolError("INVALID_PARAMS", "visual style params contain unknown fields")
        if not {"runtime_document_id", "visual_style_handle", "expected_current_handle"}.issubset(value):
            raise BridgeProtocolError("INVALID_PARAMS", "visual style params are incomplete")
        identity = DocumentIdentityParams.from_dict(
            {
                "runtime_document_id": value.get("runtime_document_id"),
                **({"document_pid": value.get("document_pid")} if "document_pid" in value else {}),
            }
        )
        style_handle = value.get("visual_style_handle")
        current_handle = value.get("expected_current_handle")
        if not isinstance(style_handle, str) or _OBJECT_HANDLE_RE.fullmatch(style_handle) is None:
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                "visual_style_handle must be an uppercase hexadecimal AutoCAD handle",
            )
        if not isinstance(current_handle, str) or _OBJECT_HANDLE_RE.fullmatch(current_handle) is None:
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                "expected_current_handle must be an uppercase hexadecimal AutoCAD handle",
            )
        return cls(
            identity.runtime_document_id,
            identity.document_pid,
            style_handle,
            current_handle,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "visual_style_handle": self.visual_style_handle,
            "expected_current_handle": self.expected_current_handle,
        }
        if self.document_pid is not None:
            result["document_pid"] = self.document_pid
        return result


def _canonical_fingerprint(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _FINGERPRINT_RE.fullmatch(value) is None:
        raise BridgeProtocolError("INVALID_PARAMS", f"{field_name} must be a canonical sha256 fingerprint")
    return value


def _canonical_checkpoint_id(value: Any) -> str:
    if not isinstance(value, str) or _CHECKPOINT_ID_RE.fullmatch(value) is None:
        raise BridgeProtocolError("INVALID_PARAMS", "checkpoint_id must be cp:<canonical-v4-uuid>")
    return value


@dataclass(frozen=True)
class RecoveryResolveParams:
    runtime_document_id: str
    document_pid: str
    checkpoint_id: str
    checkpoint_artifact_fp: str
    expected_restore_fp: str
    strategy: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecoveryResolveParams:
        allowed = {
            "runtime_document_id",
            "document_pid",
            "checkpoint_id",
            "checkpoint_artifact_fp",
            "expected_restore_fp",
            "strategy",
        }
        if set(value) != allowed:
            raise BridgeProtocolError("INVALID_PARAMS", "recovery resolve params must use the exact typed schema")
        runtime_id = _canonical_uuid(
            value.get("runtime_document_id"),
            code="INVALID_RUNTIME_DOCUMENT_ID",
            field_name="runtime_document_id",
        )
        document_pid = value.get("document_pid")
        if not isinstance(document_pid, str) or not document_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "document_pid is required for recovery")
        strategy = value.get("strategy")
        if strategy not in _RECOVERY_STRATEGIES:
            raise BridgeProtocolError("INVALID_PARAMS", "recovery strategy is not enabled")
        return cls(
            runtime_document_id=runtime_id,
            document_pid=document_pid,
            checkpoint_id=_canonical_checkpoint_id(value.get("checkpoint_id")),
            checkpoint_artifact_fp=_canonical_fingerprint(
                value.get("checkpoint_artifact_fp"), "checkpoint_artifact_fp"
            ),
            expected_restore_fp=_canonical_fingerprint(
                value.get("expected_restore_fp"), "expected_restore_fp"
            ),
            strategy=strategy,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_artifact_fp": self.checkpoint_artifact_fp,
            "expected_restore_fp": self.expected_restore_fp,
            "strategy": self.strategy,
        }


@dataclass(frozen=True)
class RecoveryFinalizeParams:
    runtime_document_id: str
    document_pid: str
    checkpoint_id: str
    checkpoint_artifact_fp: str
    accepted_post_fp: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RecoveryFinalizeParams:
        allowed = {
            "runtime_document_id",
            "document_pid",
            "checkpoint_id",
            "checkpoint_artifact_fp",
            "accepted_post_fp",
        }
        if set(value) != allowed:
            raise BridgeProtocolError("INVALID_PARAMS", "recovery finalize params must use the exact typed schema")
        runtime_id = _canonical_uuid(
            value.get("runtime_document_id"),
            code="INVALID_RUNTIME_DOCUMENT_ID",
            field_name="runtime_document_id",
        )
        document_pid = value.get("document_pid")
        if not isinstance(document_pid, str) or not document_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "document_pid is required for recovery")
        return cls(
            runtime_document_id=runtime_id,
            document_pid=document_pid,
            checkpoint_id=_canonical_checkpoint_id(value.get("checkpoint_id")),
            checkpoint_artifact_fp=_canonical_fingerprint(
                value.get("checkpoint_artifact_fp"), "checkpoint_artifact_fp"
            ),
            accepted_post_fp=_canonical_fingerprint(value.get("accepted_post_fp"), "accepted_post_fp"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_artifact_fp": self.checkpoint_artifact_fp,
            "accepted_post_fp": self.accepted_post_fp,
        }


@dataclass(frozen=True)
class LogicalBeginParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LogicalBeginParams:
        allowed = {"runtime_document_id", "document_pid", "expected_parent_fp"}
        if set(value) != allowed:
            raise BridgeProtocolError("INVALID_PARAMS", "logical begin params must use the exact typed schema")
        runtime_id = _canonical_uuid(
            value.get("runtime_document_id"),
            code="INVALID_RUNTIME_DOCUMENT_ID",
            field_name="runtime_document_id",
        )
        document_pid = value.get("document_pid")
        if not isinstance(document_pid, str) or not document_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "document_pid is required for logical batch")
        return cls(
            runtime_id,
            document_pid,
            _canonical_fingerprint(value.get("expected_parent_fp"), "expected_parent_fp"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
        }


@dataclass(frozen=True)
class LogicalBatchBinding:
    checkpoint_id: str
    checkpoint_artifact_fp: str
    expected_restore_fp: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> LogicalBatchBinding:
        allowed = {"checkpoint_id", "checkpoint_artifact_fp", "expected_restore_fp"}
        if set(value) != allowed:
            raise BridgeProtocolError("INVALID_PARAMS", "logical_transaction must use the exact checkpoint binding schema")
        return cls(
            _canonical_checkpoint_id(value.get("checkpoint_id")),
            _canonical_fingerprint(value.get("checkpoint_artifact_fp"), "checkpoint_artifact_fp"),
            _canonical_fingerprint(value.get("expected_restore_fp"), "expected_restore_fp"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "checkpoint_artifact_fp": self.checkpoint_artifact_fp,
            "expected_restore_fp": self.expected_restore_fp,
        }


def _logical_binding(value: Any) -> LogicalBatchBinding | None:
    if value is None:
        return None
    mapping = _require_mapping(value, code="INVALID_PARAMS", field_name="logical_transaction")
    return LogicalBatchBinding.from_dict(mapping)


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


def _positive_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BridgeProtocolError("INVALID_PARAMS", f"{field_name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise BridgeProtocolError("INVALID_PARAMS", f"{field_name} must be a positive finite number")
    return result


def _arc_angle(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise BridgeProtocolError("INVALID_PARAMS", f"{field_name} must be a finite radian angle")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result < math.tau:
        raise BridgeProtocolError(
            "INVALID_PARAMS", f"{field_name} must be in the half-open range [0, 2π) radians"
        )
    return result


def _canonical_entity_pid(value: Any) -> str:
    if not isinstance(value, str) or _ENTITY_PID_RE.fullmatch(value) is None:
        raise BridgeProtocolError("INVALID_PARAMS", "semantic_pids must contain canonical pid:<uuid> values")
    return value


def _metadata_namespace(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 128 or _METADATA_NAMESPACE_RE.fullmatch(value) is None:
        raise BridgeProtocolError(
            "INVALID_PARAMS",
            "metadata namespace must be a lowercase dotted identifier such as customer.mechanical.v1",
        )
    if value.startswith(_RESERVED_METADATA_PREFIXES):
        raise BridgeProtocolError("INVALID_PARAMS", "metadata namespace is reserved by the provider")
    return value


def _metadata_path(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _METADATA_PATH_RE.fullmatch(value) is None:
        raise BridgeProtocolError("INVALID_PARAMS", "metadata query path must be a dotted JSON object path")
    return value


def _canonical_metadata_value(value: Any) -> Any:
    key_count = 0

    def normalize(item: Any, depth: int) -> Any:
        nonlocal key_count
        if depth > MAX_METADATA_DEPTH:
            raise BridgeProtocolError("INVALID_PARAMS", "metadata JSON exceeds maximum nesting depth")
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, int | float):
            if isinstance(item, float) and not math.isfinite(item):
                raise BridgeProtocolError("INVALID_PARAMS", "metadata JSON numbers must be finite")
            try:
                decimal_value = Decimal(str(item))
            except (InvalidOperation, ValueError) as exc:
                raise BridgeProtocolError("INVALID_PARAMS", "metadata JSON numbers must be decimal-compatible") from exc
            if not decimal_value.is_finite() or abs(decimal_value) > MAX_METADATA_DECIMAL_ABS:
                raise BridgeProtocolError("INVALID_PARAMS", "metadata JSON numbers exceed native decimal range")
            return item
        if isinstance(item, str):
            if len(item) > MAX_METADATA_STRING_CHARS:
                raise BridgeProtocolError("INVALID_PARAMS", "metadata JSON string exceeds maximum length")
            return item
        if isinstance(item, list | tuple):
            return [normalize(child, depth + 1) for child in item]
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str) or not key or len(key) > 128:
                    raise BridgeProtocolError("INVALID_PARAMS", "metadata JSON object keys must be non-empty strings <=128 chars")
                key_count += 1
                if key_count > MAX_METADATA_KEYS:
                    raise BridgeProtocolError("INVALID_PARAMS", "metadata JSON exceeds maximum key count")
                result[key] = normalize(child, depth + 1)
            return result
        raise BridgeProtocolError("INVALID_PARAMS", "metadata value must be JSON-compatible")

    normalized = normalize(value, 1)
    try:
        encoded = json.dumps(
            normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BridgeProtocolError("INVALID_PARAMS", "metadata value must be valid JSON") from exc
    if len(encoded) > MAX_METADATA_JSON_BYTES:
        raise BridgeProtocolError("INVALID_PARAMS", "metadata JSON exceeds encoded-size limit")
    return json.loads(encoded.decode("utf-8"))


@dataclass(frozen=True)
class MetadataGetParams:
    runtime_document_id: str
    document_pid: str
    semantic_pid: str
    namespace: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MetadataGetParams:
        allowed = {"runtime_document_id", "document_pid", "semantic_pid", "namespace"}
        if set(value) != allowed:
            raise BridgeProtocolError("INVALID_PARAMS", "metadata get params must use the exact typed schema")
        runtime_id = _canonical_uuid(
            value.get("runtime_document_id"), code="INVALID_RUNTIME_DOCUMENT_ID", field_name="runtime_document_id"
        )
        document_pid = value.get("document_pid")
        if not isinstance(document_pid, str) or not document_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "document_pid is required for metadata")
        semantic_pid = _canonical_entity_pid(value.get("semantic_pid"))
        return cls(runtime_id, document_pid, semantic_pid, _metadata_namespace(value.get("namespace")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "semantic_pid": self.semantic_pid,
            "namespace": self.namespace,
        }


@dataclass(frozen=True)
class MetadataSetParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    semantic_pid: str
    namespace: str
    value: Any
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MetadataSetParams:
        allowed = _MUTATION_BINDING_FIELDS | {"semantic_pid", "namespace", "value"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        if fault_stage is not None and fault_stage != "after_apply_before_commit":
            raise BridgeProtocolError("INVALID_PARAMS", "metadata set only enables the pre-commit fault stage")
        return cls(
            runtime_id,
            document_pid,
            parent_fp,
            _canonical_entity_pid(value.get("semantic_pid")),
            _metadata_namespace(value.get("namespace")),
            _canonical_metadata_value(value.get("value")),
            fault_stage,
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "semantic_pid": self.semantic_pid,
            "namespace": self.namespace,
            "value": self.value,
        }
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class MetadataQueryParams:
    runtime_document_id: str
    document_pid: str
    namespace: str
    path: str | None = None
    equals: Any = None
    has_equals: bool = False
    limit: int = 200

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MetadataQueryParams:
        allowed = {"runtime_document_id", "document_pid", "namespace", "path", "equals", "limit"}
        unknown = set(value) - allowed
        if unknown:
            raise BridgeProtocolError("INVALID_PARAMS", "metadata query params contain unknown fields")
        required = {"runtime_document_id", "document_pid", "namespace"}
        if not required <= set(value):
            raise BridgeProtocolError("INVALID_PARAMS", "metadata query requires runtime_document_id, document_pid and namespace")
        if ("path" in value) != ("equals" in value):
            raise BridgeProtocolError("INVALID_PARAMS", "metadata query path and equals must be supplied together")
        runtime_id = _canonical_uuid(
            value.get("runtime_document_id"), code="INVALID_RUNTIME_DOCUMENT_ID", field_name="runtime_document_id"
        )
        document_pid = value.get("document_pid")
        if not isinstance(document_pid, str) or not document_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "document_pid is required for metadata")
        raw_limit = value.get("limit", 200)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or not 1 <= raw_limit <= MAX_METADATA_QUERY_RESULTS:
            raise BridgeProtocolError("INVALID_PARAMS", f"metadata query limit must be 1..{MAX_METADATA_QUERY_RESULTS}")
        has_equals = "equals" in value
        equals = _canonical_metadata_value(value.get("equals")) if has_equals else None
        return cls(
            runtime_id,
            document_pid,
            _metadata_namespace(value.get("namespace")),
            _metadata_path(value.get("path")),
            equals,
            has_equals,
            raw_limit,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "namespace": self.namespace,
            "limit": self.limit,
        }
        if self.path is not None:
            result["path"] = self.path
            result["equals"] = self.equals
        return result


def _points2(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, list | tuple):
        raise BridgeProtocolError("INVALID_PARAMS", "points must be an array of [x, y] pairs")
    if not 2 <= len(value) <= MAX_SIMPLE_POLYLINE_VERTICES:
        raise BridgeProtocolError(
            "INVALID_PARAMS",
            f"points must contain 2..{MAX_SIMPLE_POLYLINE_VERTICES} vertices",
        )
    points: list[tuple[float, float]] = []
    for raw_point in value:
        if not isinstance(raw_point, list | tuple) or len(raw_point) != 2:
            raise BridgeProtocolError("INVALID_PARAMS", "each polyline point must be exactly [x, y]")
        if any(isinstance(item, bool) or not isinstance(item, int | float) for item in raw_point):
            raise BridgeProtocolError("INVALID_PARAMS", "polyline coordinates must be finite numbers")
        point = (float(raw_point[0]), float(raw_point[1]))
        if not all(math.isfinite(item) for item in point):
            raise BridgeProtocolError("INVALID_PARAMS", "polyline coordinates must be finite numbers")
        if points and point == points[-1]:
            raise BridgeProtocolError("INVALID_PARAMS", "consecutive polyline points must differ")
        points.append(point)
    return tuple(points)


@dataclass(frozen=True)
class BatchCreateEntitySpec:
    """One generic create-only CAD primitive inside a bounded native batch chunk."""

    kind: str
    start: tuple[float, float, float] | None = None
    end: tuple[float, float, float] | None = None
    center: tuple[float, float, float] | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    points: tuple[tuple[float, float], ...] | None = None
    closed: bool | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BatchCreateEntitySpec:
        item = _require_mapping(value, code="INVALID_PARAMS", field_name="batch entity")
        kind = item.get("kind")
        if kind == "line":
            if set(item) != {"kind", "start", "end"}:
                raise BridgeProtocolError("INVALID_PARAMS", "batch line must use the exact typed schema")
            start = _point3(item.get("start"), "start")
            end = _point3(item.get("end"), "end")
            if start == end:
                raise BridgeProtocolError("INVALID_PARAMS", "line start and end must differ")
            return cls(kind="line", start=start, end=end)
        if kind == "circle":
            if set(item) != {"kind", "center", "radius"}:
                raise BridgeProtocolError("INVALID_PARAMS", "batch circle must use the exact typed schema")
            return cls(
                kind="circle",
                center=_point3(item.get("center"), "center"),
                radius=_positive_float(item.get("radius"), "radius"),
            )
        if kind == "arc":
            if set(item) != {"kind", "center", "radius", "start_angle", "end_angle"}:
                raise BridgeProtocolError("INVALID_PARAMS", "batch arc must use the exact typed schema")
            start_angle = _arc_angle(item.get("start_angle"), "start_angle")
            end_angle = _arc_angle(item.get("end_angle"), "end_angle")
            if abs(start_angle - end_angle) <= 1e-12:
                raise BridgeProtocolError("INVALID_PARAMS", "arc start_angle and end_angle must differ")
            return cls(
                kind="arc",
                center=_point3(item.get("center"), "center"),
                radius=_positive_float(item.get("radius"), "radius"),
                start_angle=start_angle,
                end_angle=end_angle,
            )
        if kind == "lwpolyline":
            if set(item) != {"kind", "points", "closed"}:
                raise BridgeProtocolError(
                    "INVALID_PARAMS", "batch lwpolyline must use the exact typed schema"
                )
            points = _points2(item.get("points"))
            closed = item.get("closed")
            if not isinstance(closed, bool):
                raise BridgeProtocolError("INVALID_PARAMS", "closed must be a boolean")
            if closed and (len(points) < 3 or points[0] == points[-1]):
                raise BridgeProtocolError(
                    "INVALID_PARAMS",
                    "closed polyline requires at least three vertices and must not repeat the first point",
                )
            return cls(kind="lwpolyline", points=points, closed=closed)
        raise BridgeProtocolError("INVALID_PARAMS", "batch entity kind is not enabled")

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "line":
            return {"kind": self.kind, "start": list(self.start or ()), "end": list(self.end or ())}
        if self.kind == "circle":
            return {"kind": self.kind, "center": list(self.center or ()), "radius": self.radius}
        if self.kind == "arc":
            return {
                "kind": self.kind,
                "center": list(self.center or ()),
                "radius": self.radius,
                "start_angle": self.start_angle,
                "end_angle": self.end_angle,
            }
        return {
            "kind": self.kind,
            "points": [list(point) for point in self.points or ()],
            "closed": self.closed,
        }


@dataclass(frozen=True)
class BatchCreateParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    entities: tuple[BatchCreateEntitySpec, ...]
    fault_stage: str | None = None
    logical_transaction: LogicalBatchBinding | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BatchCreateParams:
        allowed = _MUTATION_BINDING_FIELDS | {"entities", "logical_transaction"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        if fault_stage is not None and fault_stage != "after_apply_before_commit":
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                "batch create only enables the pre-commit fault stage",
            )
        raw_entities = value.get("entities")
        if not isinstance(raw_entities, list | tuple) or not 1 <= len(raw_entities) <= MAX_BATCH_CHUNK_ENTITIES:
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                f"entities must contain 1..{MAX_BATCH_CHUNK_ENTITIES} typed primitives",
            )
        entities = tuple(
            BatchCreateEntitySpec.from_dict(
                _require_mapping(item, code="INVALID_PARAMS", field_name="batch entity")
            )
            for item in raw_entities
        )
        return cls(
            runtime_id,
            document_pid,
            parent_fp,
            entities,
            fault_stage,
            _logical_binding(value.get("logical_transaction")),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "entities": [item.to_dict() for item in self.entities],
        }
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        if self.logical_transaction is not None:
            result["logical_transaction"] = self.logical_transaction.to_dict()
        return result


@dataclass(frozen=True)
class BatchTransformSpec:
    """One bounded planar transform that preserves the staged G1 entity families."""

    kind: str
    delta: tuple[float, float, float] | None = None
    center: tuple[float, float, float] | None = None
    angle: float | None = None
    factor: float | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BatchTransformSpec:
        item = _require_mapping(value, code="INVALID_PARAMS", field_name="transform")
        kind = item.get("kind")
        if kind == "translate":
            if set(item) != {"kind", "delta"}:
                raise BridgeProtocolError("INVALID_PARAMS", "translate transform must use the exact typed schema")
            delta = _point3(item.get("delta"), "delta")
            if delta[2] != 0.0 or (delta[0] == 0.0 and delta[1] == 0.0):
                raise BridgeProtocolError(
                    "INVALID_PARAMS",
                    "translate must be a non-zero planar XY displacement with z=0",
                )
            return cls(kind=kind, delta=delta)
        if kind == "rotate_z":
            if set(item) != {"kind", "center", "angle"}:
                raise BridgeProtocolError("INVALID_PARAMS", "rotate_z transform must use the exact typed schema")
            center = _point3(item.get("center"), "center")
            angle = item.get("angle")
            if (
                center[2] != 0.0
                or isinstance(angle, bool)
                or not isinstance(angle, int | float)
                or not math.isfinite(float(angle))
                or abs(float(angle)) <= 1e-12
                or abs(float(angle)) > math.tau
            ):
                raise BridgeProtocolError(
                    "INVALID_PARAMS",
                    "rotate_z requires center.z=0 and a non-zero finite angle within [-2π, 2π]",
                )
            return cls(kind=kind, center=center, angle=float(angle))
        if kind == "scale_uniform":
            if set(item) != {"kind", "center", "factor"}:
                raise BridgeProtocolError(
                    "INVALID_PARAMS", "scale_uniform transform must use the exact typed schema"
                )
            center = _point3(item.get("center"), "center")
            factor = item.get("factor")
            if (
                center[2] != 0.0
                or isinstance(factor, bool)
                or not isinstance(factor, int | float)
                or not math.isfinite(float(factor))
                or not 1e-6 <= float(factor) <= 1e6
                or abs(float(factor) - 1.0) <= 1e-12
            ):
                raise BridgeProtocolError(
                    "INVALID_PARAMS",
                    "scale_uniform requires center.z=0 and factor in [1e-6, 1e6] excluding 1",
                )
            return cls(kind=kind, center=center, factor=float(factor))
        raise BridgeProtocolError("INVALID_PARAMS", "transform kind is not enabled")

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "translate":
            return {"kind": self.kind, "delta": list(self.delta or ())}
        if self.kind == "rotate_z":
            return {"kind": self.kind, "center": list(self.center or ()), "angle": self.angle}
        return {"kind": self.kind, "center": list(self.center or ()), "factor": self.factor}


@dataclass(frozen=True)
class BatchTransformParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    semantic_pids: tuple[str, ...]
    transform: BatchTransformSpec
    fault_stage: str | None = None
    logical_transaction: LogicalBatchBinding | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BatchTransformParams:
        allowed = _MUTATION_BINDING_FIELDS | {"semantic_pids", "transform", "logical_transaction"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        if fault_stage not in (None, "after_apply_before_commit", "after_commit_add_stray"):
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                "batch transform enables only bounded pre-commit abort or post-commit stray fault stages",
            )
        raw_pids = value.get("semantic_pids")
        if not isinstance(raw_pids, list | tuple) or not 1 <= len(raw_pids) <= MAX_BATCH_CHUNK_ENTITIES:
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                f"semantic_pids must contain 1..{MAX_BATCH_CHUNK_ENTITIES} targets",
            )
        semantic_pids = tuple(_canonical_entity_pid(item) for item in raw_pids)
        if len(set(semantic_pids)) != len(semantic_pids):
            raise BridgeProtocolError("INVALID_PARAMS", "semantic_pids must be unique")
        transform = BatchTransformSpec.from_dict(
            _require_mapping(value.get("transform"), code="INVALID_PARAMS", field_name="transform")
        )
        return cls(
            runtime_id, document_pid, parent_fp, semantic_pids, transform, fault_stage,
            _logical_binding(value.get("logical_transaction")),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "semantic_pids": list(self.semantic_pids),
            "transform": self.transform.to_dict(),
        }
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        if self.logical_transaction is not None:
            result["logical_transaction"] = self.logical_transaction.to_dict()
        return result


@dataclass(frozen=True)
class BatchInsertBlockSpec:
    """One provider-PID-bound insertion of an already-visible block definition."""

    definition_pid: str
    position: tuple[float, float, float]
    rotation: float
    scale: float

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BatchInsertBlockSpec:
        item = _require_mapping(value, code="INVALID_PARAMS", field_name="block insert")
        if set(item) != {"definition_pid", "position", "rotation", "scale"}:
            raise BridgeProtocolError("INVALID_PARAMS", "block insert must use the exact typed schema")
        definition_pid = _canonical_entity_pid(item.get("definition_pid"))
        position = _point3(item.get("position"), "position")
        if position[2] != 0.0:
            raise BridgeProtocolError("INVALID_PARAMS", "block insert position must be planar with z=0")
        rotation = item.get("rotation")
        if (
            isinstance(rotation, bool)
            or not isinstance(rotation, int | float)
            or not math.isfinite(float(rotation))
            or abs(float(rotation)) > math.tau
        ):
            raise BridgeProtocolError(
                "INVALID_PARAMS", "block insert rotation must be finite within [-2π, 2π]"
            )
        scale = item.get("scale")
        if (
            isinstance(scale, bool)
            or not isinstance(scale, int | float)
            or not math.isfinite(float(scale))
            or not 1e-6 <= float(scale) <= 1e6
        ):
            raise BridgeProtocolError(
                "INVALID_PARAMS", "block insert scale must be finite within [1e-6, 1e6]"
            )
        return cls(definition_pid, position, float(rotation), float(scale))

    def to_dict(self) -> dict[str, Any]:
        return {
            "definition_pid": self.definition_pid,
            "position": list(self.position),
            "rotation": self.rotation,
            "scale": self.scale,
        }


@dataclass(frozen=True)
class BatchInsertBlocksParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    inserts: tuple[BatchInsertBlockSpec, ...]
    fault_stage: str | None = None
    logical_transaction: LogicalBatchBinding | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BatchInsertBlocksParams:
        allowed = _MUTATION_BINDING_FIELDS | {"inserts", "logical_transaction"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        if fault_stage is not None and fault_stage != "after_apply_before_commit":
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                "batch block insert only enables the pre-commit fault stage",
            )
        raw_inserts = value.get("inserts")
        if not isinstance(raw_inserts, list | tuple) or not 1 <= len(raw_inserts) <= MAX_BATCH_CHUNK_ENTITIES:
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                f"inserts must contain 1..{MAX_BATCH_CHUNK_ENTITIES} block insert specs",
            )
        inserts = tuple(
            BatchInsertBlockSpec.from_dict(
                _require_mapping(item, code="INVALID_PARAMS", field_name="block insert")
            )
            for item in raw_inserts
        )
        return cls(
            runtime_id, document_pid, parent_fp, inserts, fault_stage,
            _logical_binding(value.get("logical_transaction")),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "inserts": [item.to_dict() for item in self.inserts],
        }
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        if self.logical_transaction is not None:
            result["logical_transaction"] = self.logical_transaction.to_dict()
        return result


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
class CircleCreateParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    center: tuple[float, float, float]
    radius: float
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CircleCreateParams:
        allowed = _MUTATION_BINDING_FIELDS | {"center", "radius"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        return cls(
            runtime_id,
            document_pid,
            parent_fp,
            _point3(value.get("center"), "center"),
            _positive_float(value.get("radius"), "radius"),
            fault_stage,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "center": list(self.center),
            "radius": self.radius,
        }
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class CircleTargetParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    semantic_pid: str
    center: tuple[float, float, float] | None = None
    radius: float | None = None
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, require_geometry: bool) -> CircleTargetParams:
        allowed = _MUTATION_BINDING_FIELDS | {"semantic_pid"}
        if require_geometry:
            allowed |= {"center", "radius"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        semantic_pid = value.get("semantic_pid")
        if not isinstance(semantic_pid, str) or not semantic_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "semantic_pid is required for target mutation")
        center = radius = None
        if require_geometry:
            center = _point3(value.get("center"), "center")
            radius = _positive_float(value.get("radius"), "radius")
        return cls(runtime_id, document_pid, parent_fp, semantic_pid, center, radius, fault_stage)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "semantic_pid": self.semantic_pid,
        }
        if self.center is not None and self.radius is not None:
            result["center"] = list(self.center)
            result["radius"] = self.radius
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class ArcCreateParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    center: tuple[float, float, float]
    radius: float
    start_angle: float
    end_angle: float
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ArcCreateParams:
        allowed = _MUTATION_BINDING_FIELDS | {"center", "radius", "start_angle", "end_angle"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        start_angle = _arc_angle(value.get("start_angle"), "start_angle")
        end_angle = _arc_angle(value.get("end_angle"), "end_angle")
        if abs(start_angle - end_angle) <= 1e-12:
            raise BridgeProtocolError("INVALID_PARAMS", "arc start_angle and end_angle must differ")
        return cls(
            runtime_id,
            document_pid,
            parent_fp,
            _point3(value.get("center"), "center"),
            _positive_float(value.get("radius"), "radius"),
            start_angle,
            end_angle,
            fault_stage,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "center": list(self.center),
            "radius": self.radius,
            "start_angle": self.start_angle,
            "end_angle": self.end_angle,
        }
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class ArcTargetParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    semantic_pid: str
    center: tuple[float, float, float] | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, require_geometry: bool) -> ArcTargetParams:
        allowed = _MUTATION_BINDING_FIELDS | {"semantic_pid"}
        if require_geometry:
            allowed |= {"center", "radius", "start_angle", "end_angle"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        semantic_pid = value.get("semantic_pid")
        if not isinstance(semantic_pid, str) or not semantic_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "semantic_pid is required for target mutation")
        center = radius = start_angle = end_angle = None
        if require_geometry:
            center = _point3(value.get("center"), "center")
            radius = _positive_float(value.get("radius"), "radius")
            start_angle = _arc_angle(value.get("start_angle"), "start_angle")
            end_angle = _arc_angle(value.get("end_angle"), "end_angle")
            if abs(start_angle - end_angle) <= 1e-12:
                raise BridgeProtocolError("INVALID_PARAMS", "arc start_angle and end_angle must differ")
        return cls(
            runtime_id,
            document_pid,
            parent_fp,
            semantic_pid,
            center,
            radius,
            start_angle,
            end_angle,
            fault_stage,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "semantic_pid": self.semantic_pid,
        }
        if None not in (self.center, self.radius, self.start_angle, self.end_angle):
            result["center"] = list(self.center or ())
            result["radius"] = self.radius
            result["start_angle"] = self.start_angle
            result["end_angle"] = self.end_angle
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class PolylineCreateParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    points: tuple[tuple[float, float], ...]
    closed: bool
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PolylineCreateParams:
        allowed = _MUTATION_BINDING_FIELDS | {"points", "closed"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        points = _points2(value.get("points"))
        closed = value.get("closed")
        if not isinstance(closed, bool):
            raise BridgeProtocolError("INVALID_PARAMS", "closed must be a boolean")
        if closed and (len(points) < 3 or points[0] == points[-1]):
            raise BridgeProtocolError(
                "INVALID_PARAMS",
                "closed polyline requires at least three vertices and must not repeat the first point",
            )
        return cls(runtime_id, document_pid, parent_fp, points, closed, fault_stage)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "points": [list(point) for point in self.points],
            "closed": self.closed,
        }
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class PolylineTargetParams:
    runtime_document_id: str
    document_pid: str
    expected_parent_fp: str
    semantic_pid: str
    points: tuple[tuple[float, float], ...] | None = None
    closed: bool | None = None
    fault_stage: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], *, require_geometry: bool) -> PolylineTargetParams:
        allowed = _MUTATION_BINDING_FIELDS | {"semantic_pid"}
        if require_geometry:
            allowed |= {"points", "closed"}
        runtime_id, document_pid, parent_fp, fault_stage = _mutation_binding(value, allowed)
        semantic_pid = value.get("semantic_pid")
        if not isinstance(semantic_pid, str) or not semantic_pid.strip():
            raise BridgeProtocolError("INVALID_PARAMS", "semantic_pid is required for target mutation")
        points = closed = None
        if require_geometry:
            points = _points2(value.get("points"))
            closed = value.get("closed")
            if not isinstance(closed, bool):
                raise BridgeProtocolError("INVALID_PARAMS", "closed must be a boolean")
            if closed and (len(points) < 3 or points[0] == points[-1]):
                raise BridgeProtocolError(
                    "INVALID_PARAMS",
                    "closed polyline requires at least three vertices and must not repeat the first point",
                )
        return cls(runtime_id, document_pid, parent_fp, semantic_pid, points, closed, fault_stage)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "runtime_document_id": self.runtime_document_id,
            "document_pid": self.document_pid,
            "expected_parent_fp": self.expected_parent_fp,
            "semantic_pid": self.semantic_pid,
        }
        if self.points is not None and self.closed is not None:
            result["points"] = [list(point) for point in self.points]
            result["closed"] = self.closed
        if self.fault_stage is not None:
            result["fault_stage"] = self.fault_stage
        return result


@dataclass(frozen=True)
class BridgeRequest:
    """Strict typed request envelope for the staged native bridge."""

    protocol: str
    request_id: str
    operation: str
    params: (
        DocumentIdentityParams
        | LogicalBeginParams
        | MetadataGetParams
        | MetadataSetParams
        | MetadataQueryParams
        | BatchCreateParams
        | BatchInsertBlocksParams
        | BatchTransformParams
        | LineCreateParams
        | LineTargetParams
        | CircleCreateParams
        | CircleTargetParams
        | ArcCreateParams
        | ArcTargetParams
        | PolylineCreateParams
        | PolylineTargetParams
        | RecoveryResolveParams
        | RecoveryFinalizeParams
        | ViewportVisualStyleSetParams
        | Mapping[str, Any]
    )

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
        if operation in {
            "bridge.document.identity",
            "bridge.document.snapshot",
            "bridge.document.state",
            "viewport.visual_style.get",
        }:
            params: (
                DocumentIdentityParams
                | BatchCreateParams
                | BatchInsertBlocksParams
                | BatchTransformParams
                | LineCreateParams
                | LineTargetParams
                | CircleCreateParams
                | CircleTargetParams
                | ArcCreateParams
                | ArcTargetParams
                | PolylineCreateParams
                | PolylineTargetParams
                | RecoveryResolveParams
                | RecoveryFinalizeParams
                | Mapping[str, Any]
            ) = DocumentIdentityParams.from_dict(raw_params)
        elif operation == "viewport.visual_style.set":
            params = ViewportVisualStyleSetParams.from_dict(raw_params)
        elif operation == "bridge.logical.begin":
            params = LogicalBeginParams.from_dict(raw_params)
        elif operation == "metadata.get":
            params = MetadataGetParams.from_dict(raw_params)
        elif operation == "metadata.set":
            params = MetadataSetParams.from_dict(raw_params)
        elif operation == "metadata.query":
            params = MetadataQueryParams.from_dict(raw_params)
        elif operation == "entity.batch.create":
            params = BatchCreateParams.from_dict(raw_params)
        elif operation == "entity.batch.insert_blocks":
            params = BatchInsertBlocksParams.from_dict(raw_params)
        elif operation == "entity.batch.transform":
            params = BatchTransformParams.from_dict(raw_params)
        elif operation == "entity.create.line":
            params = LineCreateParams.from_dict(raw_params)
        elif operation == "entity.update.line":
            params = LineTargetParams.from_dict(raw_params, require_geometry=True)
        elif operation == "entity.delete.line":
            params = LineTargetParams.from_dict(raw_params, require_geometry=False)
        elif operation == "entity.create.circle":
            params = CircleCreateParams.from_dict(raw_params)
        elif operation == "entity.update.circle":
            params = CircleTargetParams.from_dict(raw_params, require_geometry=True)
        elif operation == "entity.delete.circle":
            params = CircleTargetParams.from_dict(raw_params, require_geometry=False)
        elif operation == "entity.create.arc":
            params = ArcCreateParams.from_dict(raw_params)
        elif operation == "entity.update.arc":
            params = ArcTargetParams.from_dict(raw_params, require_geometry=True)
        elif operation == "entity.delete.arc":
            params = ArcTargetParams.from_dict(raw_params, require_geometry=False)
        elif operation == "entity.create.lwpolyline":
            params = PolylineCreateParams.from_dict(raw_params)
        elif operation == "entity.update.lwpolyline":
            params = PolylineTargetParams.from_dict(raw_params, require_geometry=True)
        elif operation == "entity.delete.lwpolyline":
            params = PolylineTargetParams.from_dict(raw_params, require_geometry=False)
        elif operation == "bridge.recovery.resolve":
            params = RecoveryResolveParams.from_dict(raw_params)
        elif operation == "bridge.recovery.finalize":
            params = RecoveryFinalizeParams.from_dict(raw_params)
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
