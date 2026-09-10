"""Semantic models — immutable contracts for state, identity, validation and rollback.
Wing: code | Topic: semantic-state-n1 | Updated: 2026-09-10 13:30
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_FORBIDDEN_EXECUTION_FIELDS = frozenset(
    {
        "autolisp",
        "code",
        "csharp",
        "lisp",
        "macro",
        "script",
        "send_command",
        "shell",
        "command_text",
    }
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("semantic mapping keys must be strings")
            frozen[key] = _freeze(item)
        return MappingProxyType(frozen)
    if isinstance(value, tuple | list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


def _reject_arbitrary_execution_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_EXECUTION_FIELDS:
                raise ValueError(
                    f"ActionSpec arbitrary execution field is forbidden: {normalized}"
                )
            _reject_arbitrary_execution_fields(item)
    elif isinstance(value, tuple | list | set | frozenset):
        for item in value:
            _reject_arbitrary_execution_fields(item)


def _export(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _export(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _export(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_export(item) for item in value]
    if isinstance(value, set | frozenset):
        return sorted((_export(item) for item in value), key=repr)
    return value


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _validate_fingerprint(value: str | None, *, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or _FINGERPRINT_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase sha256:<64 hex> fingerprint")


class SerializableModel:
    """Common deterministic export surface for semantic dataclasses."""

    def to_dict(self) -> dict[str, Any]:
        return _export(self)


class Certainty(str, Enum):
    GROUND_TRUTH = "GROUND_TRUTH"
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"


class SourceType(str, Enum):
    RASTER = "raster"
    PDF_VECTOR = "pdf-vector"
    DWG = "dwg"
    DXF = "dxf"
    BRIEF = "brief"
    MIXED = "mixed"


class MutationOutcome(str, Enum):
    COMMITTED_VERIFIED = "COMMITTED_VERIFIED"
    ROLLED_BACK_VERIFIED = "ROLLED_BACK_VERIFIED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    COMMIT_INTEGRITY_FAIL = "COMMIT_INTEGRITY_FAIL"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    STATE_UNCERTAIN = "STATE_UNCERTAIN"
    STATE_DRIFT = "STATE_DRIFT"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    STATE_DRIFT = "STATE_DRIFT"
    STATE_UNCERTAIN = "STATE_UNCERTAIN"


class RollbackStrategy(str, Enum):
    R0_ABORT = "R0_ABORT"
    R1_COMPENSATE = "R1_COMPENSATE"
    R2_CHECKPOINT_RESTORE = "R2_CHECKPOINT_RESTORE"


@dataclass(frozen=True)
class SourceFeature(SerializableModel):
    semantic_pid: str
    feature_type: str
    properties: Mapping[str, Any]
    certainty: Certainty = Certainty.INFERRED
    source_ref: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("semantic_pid", self.semantic_pid)
        _require_non_empty("feature_type", self.feature_type)
        if not isinstance(self.certainty, Certainty):
            object.__setattr__(self, "certainty", Certainty(self.certainty))
        object.__setattr__(self, "properties", _freeze(self.properties))


@dataclass(frozen=True)
class SourceConstraint(SerializableModel):
    constraint_id: str
    constraint_type: str
    properties: Mapping[str, Any]
    certainty: Certainty = Certainty.GROUND_TRUTH

    def __post_init__(self) -> None:
        _require_non_empty("constraint_id", self.constraint_id)
        _require_non_empty("constraint_type", self.constraint_type)
        if not isinstance(self.certainty, Certainty):
            object.__setattr__(self, "certainty", Certainty(self.certainty))
        object.__setattr__(self, "properties", _freeze(self.properties))


@dataclass(frozen=True)
class SourceRelationship(SerializableModel):
    relationship_type: str
    source_pid: str
    target_pid: str
    properties: Mapping[str, Any] | None = None
    certainty: Certainty = Certainty.GROUND_TRUTH

    def __post_init__(self) -> None:
        _require_non_empty("relationship_type", self.relationship_type)
        _require_non_empty("source_pid", self.source_pid)
        _require_non_empty("target_pid", self.target_pid)
        if not isinstance(self.certainty, Certainty):
            object.__setattr__(self, "certainty", Certainty(self.certainty))
        if self.properties is not None:
            object.__setattr__(self, "properties", _freeze(self.properties))


@dataclass(frozen=True)
class SourceSemanticModel(SerializableModel):
    source_id: str
    source_type: SourceType | str
    units: str
    features: tuple[SourceFeature, ...] = ()
    constraints: tuple[SourceConstraint, ...] = ()
    relationships: tuple[SourceRelationship, ...] = ()
    source_fp: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_non_empty("source_id", self.source_id)
        _require_non_empty("units", self.units)
        if not isinstance(self.source_type, SourceType):
            object.__setattr__(self, "source_type", SourceType(self.source_type))
        _validate_fingerprint(self.source_fp, name="source_fp")
        object.__setattr__(self, "features", tuple(self.features))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "relationships", tuple(self.relationships))
        if self.schema_version != 1:
            raise ValueError("unsupported SourceSemanticModel schema_version")


@dataclass(frozen=True)
class AllowedEffects(SerializableModel):
    create: tuple[str, ...] = ()
    modify: tuple[str, ...] = ()
    delete: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "create", tuple(self.create))
        object.__setattr__(self, "modify", tuple(self.modify))
        object.__setattr__(self, "delete", tuple(self.delete))


@dataclass(frozen=True)
class ActionSpec(SerializableModel):
    action_id: str
    operation: str
    document_pid: str
    expected_parent_fp: str
    semantic_pids: tuple[str, ...] = ()
    args: Mapping[str, Any] | None = None
    allowed_effects: AllowedEffects = AllowedEffects()
    validation_ruleset_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_non_empty("action_id", self.action_id)
        _require_non_empty("document_pid", self.document_pid)
        if not isinstance(self.operation, str) or _OPERATION_RE.fullmatch(self.operation) is None:
            raise ValueError("operation must be a lowercase dotted typed operation name")
        _validate_fingerprint(self.expected_parent_fp, name="expected_parent_fp")
        args = {} if self.args is None else self.args
        _reject_arbitrary_execution_fields(args)
        object.__setattr__(self, "args", _freeze(args))
        object.__setattr__(self, "semantic_pids", tuple(self.semantic_pids))
        if not isinstance(self.allowed_effects, AllowedEffects):
            object.__setattr__(self, "allowed_effects", AllowedEffects(**self.allowed_effects))
        if self.schema_version != 1:
            raise ValueError("unsupported ActionSpec schema_version")


@dataclass(frozen=True)
class ValidationRule(SerializableModel):
    rule_type: str
    target: str | None = None
    expected: Any = None
    tolerance: float | None = None
    properties: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_non_empty("rule_type", self.rule_type)
        if self.tolerance is not None and self.tolerance < 0:
            raise ValueError("tolerance must be non-negative")
        if self.properties is not None:
            object.__setattr__(self, "properties", _freeze(self.properties))
        object.__setattr__(self, "expected", _freeze(self.expected))


@dataclass(frozen=True)
class ValidationRuleSet(SerializableModel):
    ruleset_id: str
    rules: tuple[ValidationRule, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_non_empty("ruleset_id", self.ruleset_id)
        object.__setattr__(self, "rules", tuple(self.rules))
        if self.schema_version != 1:
            raise ValueError("unsupported ValidationRuleSet schema_version")


@dataclass(frozen=True)
class FingerprintSet(SerializableModel):
    geometry_fp: str | None = None
    style_fp: str | None = None
    topology_fp: str | None = None
    instance_fp: str | None = None
    scope_fp: str | None = None
    document_fp: str | None = None
    step_fp: str | None = None
    artifact_fp: str | None = None

    def __post_init__(self) -> None:
        for field in fields(self):
            _validate_fingerprint(getattr(self, field.name), name=field.name)


@dataclass(frozen=True)
class EntitySemanticState(SerializableModel):
    semantic_pid: str
    native_handle: str | None
    entity_type: str
    layer: str
    geometry: Mapping[str, Any]
    bbox: Mapping[str, Any] | None = None
    metrics: Mapping[str, Any] | None = None
    style: Mapping[str, Any] | None = None
    hierarchy: Mapping[str, Any] | None = None
    fingerprints: FingerprintSet = FingerprintSet()

    def __post_init__(self) -> None:
        _require_non_empty("semantic_pid", self.semantic_pid)
        _require_non_empty("entity_type", self.entity_type)
        _require_non_empty("layer", self.layer)
        object.__setattr__(self, "geometry", _freeze(self.geometry))
        if not isinstance(self.fingerprints, FingerprintSet):
            object.__setattr__(self, "fingerprints", FingerprintSet(**self.fingerprints))
        for name in ("bbox", "metrics", "style", "hierarchy"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _freeze(value))


@dataclass(frozen=True)
class SemanticRelation(SerializableModel):
    relation_type: str
    source_pid: str
    target_pid: str
    properties: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        _require_non_empty("relation_type", self.relation_type)
        _require_non_empty("source_pid", self.source_pid)
        _require_non_empty("target_pid", self.target_pid)
        if self.properties is not None:
            object.__setattr__(self, "properties", _freeze(self.properties))


@dataclass(frozen=True)
class SemanticSnapshot(SerializableModel):
    snapshot_id: str
    document_pid: str
    units: str
    current_space: str
    saved: bool
    entities: tuple[EntitySemanticState, ...] = ()
    relations: tuple[SemanticRelation, ...] = ()
    styles: tuple[Mapping[str, Any], ...] = ()
    extents: Mapping[str, Any] | None = None
    fingerprints: FingerprintSet = FingerprintSet()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_non_empty("snapshot_id", self.snapshot_id)
        _require_non_empty("document_pid", self.document_pid)
        _require_non_empty("units", self.units)
        _require_non_empty("current_space", self.current_space)
        object.__setattr__(self, "entities", tuple(self.entities))
        object.__setattr__(self, "relations", tuple(self.relations))
        object.__setattr__(self, "styles", tuple(_freeze(style) for style in self.styles))
        if not isinstance(self.fingerprints, FingerprintSet):
            object.__setattr__(self, "fingerprints", FingerprintSet(**self.fingerprints))
        if self.extents is not None:
            object.__setattr__(self, "extents", _freeze(self.extents))
        if self.schema_version != 1:
            raise ValueError("unsupported SemanticSnapshot schema_version")


@dataclass(frozen=True)
class SemanticDelta(SerializableModel):
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    unchanged_scope: tuple[str, ...] = ()
    unexpected_changes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("created", "modified", "deleted", "unchanged_scope", "unexpected_changes"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class MutationReceipt(SerializableModel):
    action_id: str
    execution_status: str
    created_native_handles: tuple[str, ...] = ()
    modified_native_handles: tuple[str, ...] = ()
    deleted_native_handles: tuple[str, ...] = ()
    transaction_id: str | None = None
    provisional_snapshot: SemanticSnapshot | None = None
    native_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty("action_id", self.action_id)
        _require_non_empty("execution_status", self.execution_status)
        for name in (
            "created_native_handles",
            "modified_native_handles",
            "deleted_native_handles",
            "native_errors",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class ValidationCheck(SerializableModel):
    rule_type: str
    passed: bool
    target: str | None = None
    expected: Any = None
    actual: Any = None
    detail: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty("rule_type", self.rule_type)
        object.__setattr__(self, "expected", _freeze(self.expected))
        object.__setattr__(self, "actual", _freeze(self.actual))


@dataclass(frozen=True)
class ValidationResult(SerializableModel):
    status: ValidationStatus
    ruleset_id: str
    checks: tuple[ValidationCheck, ...] = ()
    expected_parent_fp: str | None = None
    actual_parent_fp: str | None = None
    post_state_fp: str | None = None
    unexpected_changes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, ValidationStatus):
            object.__setattr__(self, "status", ValidationStatus(self.status))
        _require_non_empty("ruleset_id", self.ruleset_id)
        _validate_fingerprint(self.expected_parent_fp, name="expected_parent_fp")
        _validate_fingerprint(self.actual_parent_fp, name="actual_parent_fp")
        _validate_fingerprint(self.post_state_fp, name="post_state_fp")
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "unexpected_changes", tuple(self.unexpected_changes))


@dataclass(frozen=True)
class RollbackReceipt(SerializableModel):
    rollback_id: str
    reason: str
    strategy: RollbackStrategy
    expected_restore_fp: str
    actual_restore_fp: str | None
    status: MutationOutcome

    def __post_init__(self) -> None:
        _require_non_empty("rollback_id", self.rollback_id)
        _require_non_empty("reason", self.reason)
        if not isinstance(self.strategy, RollbackStrategy):
            object.__setattr__(self, "strategy", RollbackStrategy(self.strategy))
        if not isinstance(self.status, MutationOutcome):
            object.__setattr__(self, "status", MutationOutcome(self.status))
        _validate_fingerprint(self.expected_restore_fp, name="expected_restore_fp")
        _validate_fingerprint(self.actual_restore_fp, name="actual_restore_fp")
        if self.status not in {
            MutationOutcome.ROLLED_BACK_VERIFIED,
            MutationOutcome.ROLLBACK_FAILED,
            MutationOutcome.STATE_UNCERTAIN,
        }:
            raise ValueError("rollback status must describe rollback verification outcome")
        if self.status is MutationOutcome.ROLLED_BACK_VERIFIED:
            if self.actual_restore_fp != self.expected_restore_fp:
                raise ValueError(
                    "ROLLED_BACK_VERIFIED requires actual restore fingerprint to equal expected restore fingerprint"
                )


@dataclass(frozen=True)
class StateChainEntry(SerializableModel):
    step_id: str
    parent_step_fp: str | None
    parent_state_fp: str
    action_fp: str
    delta_fp: str
    post_state_fp: str
    step_fp: str
    profile_signature: str
    status: MutationOutcome = MutationOutcome.COMMITTED_VERIFIED
    artifact_fp: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        _require_non_empty("step_id", self.step_id)
        if not isinstance(self.status, MutationOutcome):
            object.__setattr__(self, "status", MutationOutcome(self.status))
        for name in (
            "parent_step_fp",
            "parent_state_fp",
            "action_fp",
            "delta_fp",
            "post_state_fp",
            "step_fp",
            "artifact_fp",
        ):
            _validate_fingerprint(getattr(self, name), name=name)
        _require_non_empty("profile_signature", self.profile_signature)
        if self.status is not MutationOutcome.COMMITTED_VERIFIED:
            raise ValueError("StateChainEntry requires COMMITTED_VERIFIED status")
        if self.schema_version != 1:
            raise ValueError("unsupported StateChainEntry schema_version")
