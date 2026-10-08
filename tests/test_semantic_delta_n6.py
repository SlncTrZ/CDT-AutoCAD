"""Semantic delta/allowed-effects validator — N6 deterministic contract.
Wing: code | Topic: semantic-state-n6 | Updated: 2026-09-10 15:05
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cdt_autocad.semantic.delta import compute_semantic_delta, validate_action_delta
from cdt_autocad.semantic.fingerprint import fingerprint_document, fingerprint_geometry
from cdt_autocad.semantic.models import (
    ActionSpec,
    AllowedEffects,
    EntitySemanticState,
    FingerprintSet,
    SemanticSnapshot,
    ValidationStatus,
)

PARENT = "sha256:" + "a" * 64


def entity(
    pid: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    *,
    handle: str,
    layer: str = "0",
    style: Mapping[str, Any] | None = None,
) -> EntitySemanticState:
    provisional = EntitySemanticState(
        semantic_pid=pid,
        native_handle=handle,
        entity_type="LINE",
        layer=layer,
        geometry={"start": start, "end": end},
        bbox={"min": start, "max": end},
        metrics={"length": abs(end[0] - start[0])},
        style=style or {"linetype": "ByLayer", "lineweight": "ByLayer"},
        hierarchy={"owner_space": "Model"},
    )
    return EntitySemanticState(
        semantic_pid=pid,
        native_handle=handle,
        entity_type=provisional.entity_type,
        layer=layer,
        geometry=provisional.geometry,
        bbox=provisional.bbox,
        metrics=provisional.metrics,
        style=provisional.style,
        hierarchy=provisional.hierarchy,
        fingerprints=FingerprintSet(geometry_fp=fingerprint_geometry(provisional)),
    )


def shape_entity(
    pid: str,
    entity_type: str,
    geometry: Mapping[str, Any],
    *,
    handle: str,
) -> EntitySemanticState:
    provisional = EntitySemanticState(
        semantic_pid=pid,
        native_handle=handle,
        entity_type=entity_type,
        layer="0",
        geometry=geometry,
        bbox=None,
        metrics={},
        style={"linetype": "ByLayer", "lineweight": "ByLayer"},
        hierarchy={"owner_space": "Model"},
    )
    return EntitySemanticState(
        semantic_pid=pid,
        native_handle=handle,
        entity_type=entity_type,
        layer="0",
        geometry=geometry,
        bbox=None,
        metrics={},
        style=provisional.style,
        hierarchy=provisional.hierarchy,
        fingerprints=FingerprintSet(geometry_fp=fingerprint_geometry(provisional)),
    )


def snapshot(
    *entities: EntitySemanticState,
    saved: bool = True,
    styles: tuple[Mapping[str, Any], ...] = (),
) -> SemanticSnapshot:
    provisional = SemanticSnapshot(
        snapshot_id="snap:test",
        document_pid="doc:test",
        units="millimeters",
        current_space="Model",
        saved=saved,
        entities=entities,
        styles=styles,
    )
    return SemanticSnapshot(
        snapshot_id=provisional.snapshot_id,
        document_pid=provisional.document_pid,
        units=provisional.units,
        current_space=provisional.current_space,
        saved=provisional.saved,
        entities=provisional.entities,
        styles=provisional.styles,
        fingerprints=FingerprintSet(document_fp=fingerprint_document(provisional)),
    )


def action(
    operation: str,
    *,
    parent: str = PARENT,
    pids: tuple[str, ...] = (),
    args: Mapping[str, Any] | None = None,
    allowed: AllowedEffects,
) -> ActionSpec:
    return ActionSpec(
        action_id="step-1",
        operation=operation,
        document_pid="doc:test",
        expected_parent_fp=parent,
        semantic_pids=pids,
        args=args or {},
        allowed_effects=allowed,
    )


def test_delta_detects_create_modify_delete_and_sorts_pids():
    before = snapshot(
        entity("pid:b", (0, 0, 0), (1, 0, 0), handle="10"),
        entity("pid:gone", (2, 0, 0), (3, 0, 0), handle="11"),
    )
    after = snapshot(
        entity("pid:b", (0, 0, 0), (2, 0, 0), handle="99"),
        entity("pid:new", (4, 0, 0), (5, 0, 0), handle="12"),
    )

    delta = compute_semantic_delta(before, after)

    assert delta.created == ("pid:new",)
    assert delta.modified == ("pid:b",)
    assert delta.deleted == ("pid:gone",)


def test_delta_ignores_native_handle_and_saved_flag_only_changes():
    before = snapshot(entity("pid:1", (0, 0, 0), (1, 0, 0), handle="10"), saved=True)
    after = snapshot(entity("pid:1", (0, 0, 0), (1, 0, 0), handle="FF"), saved=False)

    delta = compute_semantic_delta(before, after)

    assert delta.created == ()
    assert delta.modified == ()
    assert delta.deleted == ()
    assert delta.unchanged_scope == ("pid:1",)


def test_allowed_create_line_passes_with_exact_operation_shape():
    before = snapshot()
    after = snapshot(entity("pid:new", (0, 0, 0), (1, 0, 0), handle="10"))
    spec = action(
        "entity.create.line",
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.PASS
    assert delta.created == ("pid:new",)
    assert delta.unexpected_changes == ()


def test_unexpected_sibling_modification_fails_allowed_effects():
    target_before = entity("pid:target", (0, 0, 0), (1, 0, 0), handle="10")
    sibling_before = entity("pid:sibling", (2, 0, 0), (3, 0, 0), handle="11")
    before = snapshot(target_before, sibling_before)
    after = snapshot(
        entity("pid:target", (0, 0, 0), (2, 0, 0), handle="10"),
        entity("pid:sibling", (2, 0, 0), (4, 0, 0), handle="11"),
    )
    spec = action(
        "entity.update.line",
        pids=("pid:target",),
        args={"start": [0, 0, 0], "end": [2, 0, 0]},
        allowed=AllowedEffects(modify=("LINE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.FAIL
    assert "pid:sibling" in result.unexpected_changes
    assert "pid:sibling" in delta.unexpected_changes


def test_update_requires_exact_target_effect_not_noop_or_wrong_pid():
    before = snapshot(
        entity("pid:target", (0, 0, 0), (1, 0, 0), handle="10"),
        entity("pid:other", (2, 0, 0), (3, 0, 0), handle="11"),
    )
    after = snapshot(
        entity("pid:target", (0, 0, 0), (1, 0, 0), handle="10"),
        entity("pid:other", (2, 0, 0), (4, 0, 0), handle="11"),
    )
    spec = action(
        "entity.update.line",
        pids=("pid:target",),
        args={"start": [0, 0, 0], "end": [2, 0, 0]},
        allowed=AllowedEffects(modify=("LINE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.FAIL
    assert delta.modified == ("pid:other",)


def test_new_duplicate_geometry_is_validation_failure_but_preexisting_group_is_not():
    first = entity("pid:1", (0, 0, 0), (1, 0, 0), handle="10")
    second = entity("pid:2", (0, 0, 0), (1, 0, 0), handle="11")
    before = snapshot(first, second)
    after_same_duplicate = snapshot(
        first,
        second,
        entity("pid:3", (2, 0, 0), (3, 0, 0), handle="12"),
    )
    spec = action(
        "entity.create.line",
        args={"start": [2, 0, 0], "end": [3, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    delta, result = validate_action_delta(spec, before, after_same_duplicate)
    assert result.status is ValidationStatus.PASS
    assert delta.unexpected_changes == ()

    after_new_duplicate = snapshot(
        first,
        second,
        entity("pid:3", (0, 0, 0), (1, 0, 0), handle="12"),
    )
    duplicate_spec = action(
        "entity.create.line",
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )
    delta, result = validate_action_delta(duplicate_spec, before, after_new_duplicate)
    assert result.status is ValidationStatus.FAIL
    assert "pid:3" in result.unexpected_changes
    assert "pid:3" in delta.unexpected_changes


def test_create_requires_geometry_to_match_action_spec():
    before = snapshot()
    after = snapshot(entity("pid:new", (0, 0, 0), (2, 0, 0), handle="10"))
    spec = action(
        "entity.create.line",
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.FAIL
    assert "pid:new" in delta.unexpected_changes
    check = next(item for item in result.checks if item.rule_type == "requested_geometry")
    assert check.passed is False


def test_update_rejects_unrequested_style_change_on_target():
    before = snapshot(entity("pid:target", (0, 0, 0), (1, 0, 0), handle="10"))
    after = snapshot(
        entity(
            "pid:target",
            (0, 0, 0),
            (2, 0, 0),
            handle="10",
            style={"linetype": "DASHED", "lineweight": "ByLayer"},
        )
    )
    spec = action(
        "entity.update.line",
        pids=("pid:target",),
        args={"start": [0, 0, 0], "end": [2, 0, 0]},
        allowed=AllowedEffects(modify=("LINE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.FAIL
    assert "pid:target" in delta.unexpected_changes
    check = next(item for item in result.checks if item.rule_type == "target_envelope_unchanged")
    assert check.passed is False


def test_o1_circle_requested_geometry_and_update_envelope_are_validated():
    before = snapshot(
        shape_entity(
            "pid:c",
            "CIRCLE",
            {"center": [0, 0, 0], "normal": [0, 0, 1], "radius": 2.0},
            handle="20",
        )
    )
    after = snapshot(
        shape_entity(
            "pid:c",
            "CIRCLE",
            {"center": [3, 4, 0], "normal": [0, 0, 1], "radius": 5.0},
            handle="20",
        )
    )
    spec = action(
        "entity.update.circle",
        pids=("pid:c",),
        args={"center": [3, 4, 0], "radius": 5.0},
        allowed=AllowedEffects(modify=("CIRCLE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.PASS
    assert delta.modified == ("pid:c",)


def test_o1_circle_wrong_normal_fails_requested_geometry():
    before = snapshot()
    after = snapshot(
        shape_entity(
            "pid:c",
            "CIRCLE",
            {"center": [3, 4, 0], "normal": [0, 1, 0], "radius": 5.0},
            handle="20",
        )
    )
    spec = action(
        "entity.create.circle",
        args={"center": [3, 4, 0], "radius": 5.0},
        allowed=AllowedEffects(create=("CIRCLE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.FAIL
    assert "pid:c" in delta.unexpected_changes


def test_o1_arc_requested_geometry_matches_native_semantics():
    before = snapshot()
    after = snapshot(
        shape_entity(
            "pid:a",
            "ARC",
            {
                "center": [10, 10, 0],
                "normal": [0, 0, 1],
                "radius": 4.0,
                "start_angle": 0.25,
                "end_angle": 2.5,
                "sweep_angle": 2.25,
            },
            handle="21",
        )
    )
    spec = action(
        "entity.create.arc",
        args={"center": [10, 10, 0], "radius": 4.0, "start_angle": 0.25, "end_angle": 2.5},
        allowed=AllowedEffects(create=("ARC",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.PASS
    assert delta.created == ("pid:a",)


def test_o1_simple_lwpolyline_requested_geometry_matches_zero_bulge_width_contract():
    before = snapshot()
    after = snapshot(
        shape_entity(
            "pid:p",
            "LWPOLYLINE",
            {
                "vertices": [
                    {"point": [0, 0], "bulge": 0.0, "start_width": 0.0, "end_width": 0.0},
                    {"point": [5, 0], "bulge": 0.0, "start_width": 0.0, "end_width": 0.0},
                    {"point": [5, 3], "bulge": 0.0, "start_width": 0.0, "end_width": 0.0},
                ],
                "closed": True,
                "elevation": 0.0,
                "normal": [0, 0, 1],
            },
            handle="22",
        )
    )
    spec = action(
        "entity.create.lwpolyline",
        args={"points": [[0, 0], [5, 0], [5, 3]], "closed": True},
        allowed=AllowedEffects(create=("LWPOLYLINE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.PASS
    assert delta.created == ("pid:p",)


def test_line_mutation_rejects_document_style_resource_change():
    before = snapshot(styles=({"kind": "layer", "name": "0", "lineweight": "ByLayer"},))
    after = snapshot(
        entity("pid:new", (0, 0, 0), (1, 0, 0), handle="10"),
        styles=({"kind": "layer", "name": "0", "lineweight": "0.50"},),
    )
    spec = action(
        "entity.create.line",
        args={"start": [0, 0, 0], "end": [1, 0, 0]},
        allowed=AllowedEffects(create=("LINE",)),
    )

    delta, result = validate_action_delta(spec, before, after)

    assert result.status is ValidationStatus.FAIL
    assert "document:styles" in delta.unexpected_changes
    check = next(item for item in result.checks if item.rule_type == "document_invariants")
    assert check.passed is False
