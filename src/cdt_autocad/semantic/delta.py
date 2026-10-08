"""Semantic delta + allowed-effects validation for the N6 state loop.
Wing: code | Topic: semantic-state-n6 | Updated: 2026-09-10 14:35
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from .canonical import ToleranceProfile, canonical_json
from .fingerprint import fingerprint_geometry
from .models import (
    ActionSpec,
    EntitySemanticState,
    SemanticDelta,
    SemanticSnapshot,
    ValidationCheck,
    ValidationResult,
    ValidationStatus,
)


def _entity_semantic_key(entity: EntitySemanticState, profile: ToleranceProfile) -> str:
    payload = entity.to_dict()
    payload.pop("native_handle", None)
    payload.pop("fingerprints", None)
    return canonical_json(payload, profile, float_kind="linear")


def compute_semantic_delta(
    before: SemanticSnapshot,
    after: SemanticSnapshot,
    profile: ToleranceProfile | None = None,
) -> SemanticDelta:
    """Compute deterministic PID effects while ignoring transient native handles/fingerprint caches."""

    selected = profile or ToleranceProfile()
    before_by_pid = {entity.semantic_pid: entity for entity in before.entities}
    after_by_pid = {entity.semantic_pid: entity for entity in after.entities}

    created = tuple(sorted(after_by_pid.keys() - before_by_pid.keys()))
    deleted = tuple(sorted(before_by_pid.keys() - after_by_pid.keys()))
    modified: list[str] = []
    unchanged: list[str] = []
    for pid in sorted(before_by_pid.keys() & after_by_pid.keys()):
        if _entity_semantic_key(before_by_pid[pid], selected) != _entity_semantic_key(
            after_by_pid[pid], selected
        ):
            modified.append(pid)
        else:
            unchanged.append(pid)
    return SemanticDelta(
        created=created,
        modified=tuple(modified),
        deleted=deleted,
        unchanged_scope=tuple(unchanged),
    )


def _type_by_pid(snapshot: SemanticSnapshot) -> dict[str, str]:
    return {entity.semantic_pid: entity.entity_type.upper() for entity in snapshot.entities}


def _geometry_groups(
    entities: Iterable[EntitySemanticState], profile: ToleranceProfile
) -> dict[str, set[str]]:
    grouped: dict[str, set[str]] = defaultdict(set)
    for entity in entities:
        geometry_fp = entity.fingerprints.geometry_fp or fingerprint_geometry(entity, profile)
        grouped[geometry_fp].add(entity.semantic_pid)
    return dict(grouped)


def _new_duplicate_members(
    before: SemanticSnapshot,
    after: SemanticSnapshot,
    profile: ToleranceProfile,
) -> tuple[str, ...]:
    before_groups = _geometry_groups(before.entities, profile)
    after_groups = _geometry_groups(after.entities, profile)
    new_members: set[str] = set()
    for geometry_fp, after_pids in after_groups.items():
        if len(after_pids) < 2:
            continue
        before_pids = before_groups.get(geometry_fp, set())
        new_members.update(after_pids - before_pids)
    return tuple(sorted(new_members))


def _effect_scope_unexpected(
    action: ActionSpec,
    before: SemanticSnapshot,
    after: SemanticSnapshot,
    delta: SemanticDelta,
) -> tuple[str, ...]:
    before_types = _type_by_pid(before)
    after_types = _type_by_pid(after)
    allowed_create = {item.upper() for item in action.allowed_effects.create}
    allowed_modify = {item.upper() for item in action.allowed_effects.modify}
    allowed_delete = {item.upper() for item in action.allowed_effects.delete}
    target_scope = set(action.semantic_pids)
    unexpected: set[str] = set()

    for pid in delta.created:
        if after_types.get(pid) not in allowed_create:
            unexpected.add(pid)
    for pid in delta.modified:
        if after_types.get(pid) not in allowed_modify or (target_scope and pid not in target_scope):
            unexpected.add(pid)
    for pid in delta.deleted:
        if before_types.get(pid) not in allowed_delete or (target_scope and pid not in target_scope):
            unexpected.add(pid)
    return tuple(sorted(unexpected))


def _operation_shape(operation: str) -> tuple[str, str] | None:
    parts = operation.split(".")
    if len(parts) != 3 or parts[0] != "entity" or parts[1] not in {"create", "update", "delete"}:
        return None
    entity_type = {
        "line": "LINE",
        "circle": "CIRCLE",
        "arc": "ARC",
        "lwpolyline": "LWPOLYLINE",
    }.get(parts[2])
    return (parts[1], entity_type) if entity_type is not None else None


def _simple_polyline_geometry(
    points: Any,
    closed: Any,
    *,
    normal: Any,
    elevation: Any,
) -> dict[str, Any] | None:
    if not isinstance(points, (list, tuple)) or not isinstance(closed, bool):
        return None
    vertices = [
        {
            "point": list(point),
            "bulge": 0.0,
            "start_width": 0.0,
            "end_width": 0.0,
        }
        for point in points
    ]
    return {
        "vertices": vertices,
        "closed": closed,
        "elevation": elevation,
        "normal": normal,
    }


def _is_simple_polyline_geometry(geometry: Mapping[str, Any], profile: ToleranceProfile) -> bool:
    vertices = geometry.get("vertices")
    if not isinstance(vertices, (list, tuple)):
        return False
    zero = canonical_json(0.0, profile, float_kind="linear")
    for vertex in vertices:
        if not isinstance(vertex, Mapping):
            return False
        for field_name in ("bulge", "start_width", "end_width"):
            if canonical_json(vertex.get(field_name), profile, float_kind="linear") != zero:
                return False
    return (
        canonical_json(geometry.get("elevation"), profile, float_kind="linear") == zero
        and canonical_json(geometry.get("normal"), profile, float_kind="linear")
        == canonical_json([0.0, 0.0, 1.0], profile, float_kind="linear")
    )


def _requested_geometry_check(
    action: ActionSpec,
    before: SemanticSnapshot,
    after: SemanticSnapshot,
    delta: SemanticDelta,
    profile: ToleranceProfile,
) -> tuple[bool, Any, Any, tuple[str, ...]]:
    shape = _operation_shape(action.operation)
    if shape is None or shape[0] == "delete":
        return True, None, None, ()

    verb, entity_type = shape
    target_pid = (
        delta.created[0]
        if verb == "create" and len(delta.created) == 1
        else action.semantic_pids[0]
        if verb == "update" and len(action.semantic_pids) == 1
        else None
    )
    before_by_pid = {entity.semantic_pid: entity for entity in before.entities}
    after_by_pid = {entity.semantic_pid: entity for entity in after.entities}
    before_entity = before_by_pid.get(target_pid) if target_pid is not None else None
    entity = after_by_pid.get(target_pid) if target_pid is not None else None
    args = action.args or {}

    expected_payload: dict[str, Any] | None = None
    input_contract_passed = True
    if entity_type == "LINE":
        expected_payload = {"type": "LINE", "start": args.get("start"), "end": args.get("end")}
    elif entity_type == "CIRCLE":
        normal = [0.0, 0.0, 1.0] if verb == "create" else (
            before_entity.geometry.get("normal") if before_entity is not None else None
        )
        expected_payload = {
            "type": "CIRCLE",
            "center": args.get("center"),
            "normal": normal,
            "radius": args.get("radius"),
        }
    elif entity_type == "ARC":
        start_angle = args.get("start_angle")
        end_angle = args.get("end_angle")
        sweep_angle = None
        if isinstance(start_angle, (int, float)) and isinstance(end_angle, (int, float)):
            sweep_angle = (float(end_angle) - float(start_angle)) % math.tau
        normal = [0.0, 0.0, 1.0] if verb == "create" else (
            before_entity.geometry.get("normal") if before_entity is not None else None
        )
        expected_payload = {
            "type": "ARC",
            "center": args.get("center"),
            "normal": normal,
            "radius": args.get("radius"),
            "start_angle": start_angle,
            "end_angle": end_angle,
            "sweep_angle": sweep_angle,
        }
    elif entity_type == "LWPOLYLINE":
        if verb == "update" and before_entity is not None:
            input_contract_passed = _is_simple_polyline_geometry(before_entity.geometry, profile)
        normal = [0.0, 0.0, 1.0]
        elevation = 0.0
        geometry = _simple_polyline_geometry(
            args.get("points"),
            args.get("closed"),
            normal=normal,
            elevation=elevation,
        )
        expected_payload = {"type": "LWPOLYLINE", **geometry} if geometry is not None else None

    expected_fp = fingerprint_geometry(expected_payload, profile) if expected_payload is not None else None
    actual_fp = fingerprint_geometry(entity, profile) if entity is not None else None
    passed = entity is not None and expected_fp is not None and actual_fp == expected_fp and input_contract_passed
    unexpected = () if passed or target_pid is None else (target_pid,)
    return (
        passed,
        {"geometry_fp": expected_fp, "geometry": expected_payload},
        {"geometry_fp": actual_fp, "semantic_pid": target_pid},
        unexpected,
    )


def _target_envelope_check(
    action: ActionSpec,
    before: SemanticSnapshot,
    after: SemanticSnapshot,
    profile: ToleranceProfile,
) -> tuple[bool, Any, Any, tuple[str, ...]]:
    shape = _operation_shape(action.operation)
    if shape is None or shape[0] != "update" or len(action.semantic_pids) != 1:
        return True, None, None, ()

    target_pid = action.semantic_pids[0]
    before_by_pid = {entity.semantic_pid: entity for entity in before.entities}
    after_by_pid = {entity.semantic_pid: entity for entity in after.entities}
    before_entity = before_by_pid.get(target_pid)
    after_entity = after_by_pid.get(target_pid)
    if before_entity is None or after_entity is None:
        return False, {"semantic_pid": target_pid}, {"semantic_pid": target_pid}, (target_pid,)

    def envelope(entity: EntitySemanticState) -> dict[str, Any]:
        return {
            "entity_type": entity.entity_type,
            "layer": entity.layer,
            "style": entity.style,
            "hierarchy": entity.hierarchy,
        }

    before_envelope = envelope(before_entity)
    after_envelope = envelope(after_entity)
    passed = canonical_json(before_envelope, profile, float_kind="scalar") == canonical_json(
        after_envelope,
        profile,
        float_kind="scalar",
    )
    return passed, before_envelope, after_envelope, () if passed else (target_pid,)


def _document_invariant_check(
    before: SemanticSnapshot,
    after: SemanticSnapshot,
    profile: ToleranceProfile,
) -> tuple[bool, Any, Any, tuple[str, ...]]:
    expected = {
        "schema_version": before.schema_version,
        "document_pid": before.document_pid,
        "units": before.units,
        "current_space": before.current_space,
        "styles": sorted(
            canonical_json(dict(style), profile, float_kind="scalar") for style in before.styles
        ),
    }
    actual = {
        "schema_version": after.schema_version,
        "document_pid": after.document_pid,
        "units": after.units,
        "current_space": after.current_space,
        "styles": sorted(
            canonical_json(dict(style), profile, float_kind="scalar") for style in after.styles
        ),
    }
    unexpected: list[str] = []
    for field_name in ("schema_version", "document_pid", "units", "current_space", "styles"):
        if expected[field_name] != actual[field_name]:
            unexpected.append(f"document:{field_name}")
    return not unexpected, expected, actual, tuple(unexpected)


def _expected_operation_effect(action: ActionSpec, delta: SemanticDelta) -> tuple[bool, Any, Any]:
    shape = _operation_shape(action.operation)
    if shape is None:
        return False, {"operation": "supported typed mutation"}, {"operation": action.operation}
    verb, _entity_type = shape
    if verb == "create":
        expected = {"created_count": 1, "modified": [], "deleted": []}
        actual = {
            "created_count": len(delta.created),
            "modified": list(delta.modified),
            "deleted": list(delta.deleted),
        }
        return len(delta.created) == 1 and not delta.modified and not delta.deleted, expected, actual

    if verb == "update":
        expected_pid = action.semantic_pids[0] if len(action.semantic_pids) == 1 else None
        expected = {"modified": [expected_pid] if expected_pid else ["<exactly-one-target-pid>"]}
        actual = {
            "created": list(delta.created),
            "modified": list(delta.modified),
            "deleted": list(delta.deleted),
        }
        passed = (
            expected_pid is not None
            and delta.modified == (expected_pid,)
            and not delta.created
            and not delta.deleted
        )
        return passed, expected, actual

    if verb == "delete":
        expected_pid = action.semantic_pids[0] if len(action.semantic_pids) == 1 else None
        expected = {"deleted": [expected_pid] if expected_pid else ["<exactly-one-target-pid>"]}
        actual = {
            "created": list(delta.created),
            "modified": list(delta.modified),
            "deleted": list(delta.deleted),
        }
        passed = (
            expected_pid is not None
            and delta.deleted == (expected_pid,)
            and not delta.created
            and not delta.modified
        )
        return passed, expected, actual

    return False, {"operation": "supported typed mutation"}, {"operation": action.operation}


def validate_action_delta(
    action: ActionSpec,
    before: SemanticSnapshot,
    after: SemanticSnapshot,
    delta: SemanticDelta | None = None,
    profile: ToleranceProfile | None = None,
) -> tuple[SemanticDelta, ValidationResult]:
    """Validate one N5 mutation outcome against N6 identity scope and allowed effects."""

    selected = profile or ToleranceProfile()
    observed = delta or compute_semantic_delta(before, after, selected)
    scope_unexpected = set(_effect_scope_unexpected(action, before, after, observed))
    duplicate_members = set(_new_duplicate_members(before, after, selected))
    geometry_passed, geometry_expected, geometry_actual, geometry_unexpected = (
        _requested_geometry_check(action, before, after, observed, selected)
    )
    envelope_passed, envelope_expected, envelope_actual, envelope_unexpected = (
        _target_envelope_check(action, before, after, selected)
    )
    document_passed, document_expected, document_actual, document_unexpected = (
        _document_invariant_check(before, after, selected)
    )
    unexpected = tuple(
        sorted(
            scope_unexpected
            | duplicate_members
            | set(geometry_unexpected)
            | set(envelope_unexpected)
            | set(document_unexpected)
        )
    )
    validated_delta = SemanticDelta(
        created=observed.created,
        modified=observed.modified,
        deleted=observed.deleted,
        unchanged_scope=observed.unchanged_scope,
        unexpected_changes=unexpected,
    )

    effect_scope_passed = not scope_unexpected
    shape_passed, shape_expected, shape_actual = _expected_operation_effect(action, observed)
    duplicate_passed = not duplicate_members
    checks = (
        ValidationCheck(
            rule_type="allowed_effects",
            passed=effect_scope_passed,
            expected=action.allowed_effects.to_dict(),
            actual={
                "created": list(observed.created),
                "modified": list(observed.modified),
                "deleted": list(observed.deleted),
            },
            detail=None if effect_scope_passed else "semantic delta contains effects outside allowed type/identity scope",
        ),
        ValidationCheck(
            rule_type="operation_effect_shape",
            passed=shape_passed,
            expected=shape_expected,
            actual=shape_actual,
        ),
        ValidationCheck(
            rule_type="requested_geometry",
            passed=geometry_passed,
            expected=geometry_expected,
            actual=geometry_actual,
            detail=None if geometry_passed else "native geometry does not match the ActionSpec request",
        ),
        ValidationCheck(
            rule_type="target_envelope_unchanged",
            passed=envelope_passed,
            expected=envelope_expected,
            actual=envelope_actual,
            detail=None if envelope_passed else "geometry-only update changed target layer/style/hierarchy/type",
        ),
        ValidationCheck(
            rule_type="document_invariants",
            passed=document_passed,
            expected=document_expected,
            actual=document_actual,
            detail=None if document_passed else "typed mutation changed invariant document/resource state",
        ),
        ValidationCheck(
            rule_type="new_duplicate_geometry",
            passed=duplicate_passed,
            expected=[],
            actual=list(sorted(duplicate_members)),
            detail=None if duplicate_passed else "mutation introduced a new exact geometry duplicate",
        ),
    )
    status = ValidationStatus.PASS if all(check.passed for check in checks) else ValidationStatus.FAIL
    return validated_delta, ValidationResult(
        status=status,
        ruleset_id=action.validation_ruleset_id or f"n6:{action.operation}",
        checks=checks,
        expected_parent_fp=action.expected_parent_fp,
        actual_parent_fp=before.fingerprints.document_fp,
        post_state_fp=after.fingerprints.document_fp,
        unexpected_changes=unexpected,
    )
