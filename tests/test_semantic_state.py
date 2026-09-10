"""Semantic State N1 tests — deterministic contracts, identity and state-chain invariants.
Wing: code | Topic: semantic-state-n1 | Updated: 2026-09-10 13:20
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from cdt_autocad.errors import StateConflictError
from cdt_autocad.semantic.canonical import (
    CanonicalizationError,
    ToleranceProfile,
    canonical_json,
)
from cdt_autocad.semantic.fingerprint import (
    fingerprint_action,
    fingerprint_delta,
    fingerprint_document,
    fingerprint_geometry,
    fingerprint_style,
)
from cdt_autocad.semantic.identity import assert_unique_pids, duplicate_geometry_groups
from cdt_autocad.semantic.models import (
    ActionSpec,
    AllowedEffects,
    Certainty,
    EntitySemanticState,
    FingerprintSet,
    MutationOutcome,
    RollbackReceipt,
    RollbackStrategy,
    SemanticDelta,
    SemanticSnapshot,
    SourceFeature,
    SourceSemanticModel,
    StateChainEntry,
)
from cdt_autocad.semantic.state_chain import (
    build_state_chain_entry,
    verify_parent_state,
    verify_state_chain,
)


DEFAULT = ToleranceProfile()


def test_canonical_json_is_independent_of_mapping_insertion_order():
    left = {"b": 2, "a": {"z": 1.25, "x": True}}
    right = {"a": {"x": True, "z": 1.25}, "b": 2}

    assert canonical_json(left, DEFAULT) == canonical_json(right, DEFAULT)


def test_canonical_json_treats_integer_and_equivalent_float_as_same_number():
    assert canonical_json({"x": 11}, DEFAULT) == canonical_json({"x": 11.0}, DEFAULT)


def test_canonical_json_quantizes_float_noise_within_linear_tolerance():
    left = {"point": [11.00000000002, 5.0, -0.0]}
    right = {"point": [10.99999999998, 5.0, 0.0]}

    assert canonical_json(left, DEFAULT, float_kind="linear") == canonical_json(
        right, DEFAULT, float_kind="linear"
    )
    assert fingerprint_geometry(left, DEFAULT) == fingerprint_geometry(right, DEFAULT)


def test_line_geometry_fingerprint_is_direction_independent():
    forward = {"type": "LINE", "start": [0.0, 0.0, 0.0], "end": [11.0, 2.0, 0.0]}
    reversed_line = {"type": "LINE", "start": [11.0, 2.0, 0.0], "end": [0.0, 0.0, 0.0]}

    assert fingerprint_geometry(forward, DEFAULT) == fingerprint_geometry(reversed_line, DEFAULT)


def test_geometry_fingerprint_changes_outside_tolerance():
    left = {"point": [11.0, 5.0, 0.0]}
    right = {"point": [11.00001, 5.0, 0.0]}

    assert fingerprint_geometry(left, DEFAULT) != fingerprint_geometry(right, DEFAULT)


def test_canonical_json_preserves_list_order_but_normalizes_set_order():
    first = {"vertices": [[0.0, 0.0], [1.0, 0.0]], "tags": {"wall", "exterior"}}
    same = {"vertices": [[0.0, 0.0], [1.0, 0.0]], "tags": {"exterior", "wall"}}
    reversed_vertices = {
        "vertices": [[1.0, 0.0], [0.0, 0.0]],
        "tags": {"wall", "exterior"},
    }

    assert canonical_json(first, DEFAULT) == canonical_json(same, DEFAULT)
    assert canonical_json(first, DEFAULT) != canonical_json(reversed_vertices, DEFAULT)


def test_non_finite_numbers_are_rejected_before_hashing():
    with pytest.raises(CanonicalizationError, match="finite"):
        canonical_json({"x": float("nan")}, DEFAULT)

    with pytest.raises(CanonicalizationError, match="finite"):
        canonical_json({"x": float("inf")}, DEFAULT)


def test_tolerance_profile_is_part_of_fingerprint_identity():
    payload = {"value": 1.0}
    coarse = ToleranceProfile(profile_id="coarse", linear_quantum=1e-3)
    fine = ToleranceProfile(profile_id="fine", linear_quantum=1e-6)

    assert fingerprint_geometry(payload, coarse) != fingerprint_geometry(payload, fine)


def test_fingerprint_domain_separation_prevents_cross_family_aliasing():
    payload = {"value": 1.0}

    assert fingerprint_geometry(payload, DEFAULT) != fingerprint_style(payload, DEFAULT)


def test_dataclass_payloads_are_canonicalizable():
    @dataclass(frozen=True)
    class Payload:
        x: float
        name: str

    assert canonical_json(Payload(x=1.0, name="A"), DEFAULT) == canonical_json(
        {"x": 1.0, "name": "A"}, DEFAULT
    )


def test_source_model_preserves_ground_truth_derived_and_inferred_certainty():
    model = SourceSemanticModel(
        source_id="src:house-01",
        source_type="raster",
        units="feet",
        features=(
            SourceFeature(
                semantic_pid="pid:room.bedroom2",
                feature_type="ROOM",
                properties={"width": 11.0, "height": 11.0},
                certainty=Certainty.GROUND_TRUTH,
            ),
            SourceFeature(
                semantic_pid="pid:grid.x1",
                feature_type="DATUM",
                properties={"x": 11.0},
                certainty=Certainty.DERIVED,
            ),
            SourceFeature(
                semantic_pid="pid:window.north.1",
                feature_type="WINDOW",
                properties={"offset": 3.2},
                certainty=Certainty.INFERRED,
            ),
        ),
    )

    exported = model.to_dict()
    assert [item["certainty"] for item in exported["features"]] == [
        "GROUND_TRUTH",
        "DERIVED",
        "INFERRED",
    ]


def test_action_spec_rejects_malformed_operation_names():
    with pytest.raises(ValueError, match="operation"):
        ActionSpec(
            action_id="a1",
            operation="execute arbitrary lisp",
            document_pid="doc:1",
            expected_parent_fp="sha256:" + "0" * 64,
        )


def test_action_fingerprint_is_stable_for_equivalent_action_payloads():
    parent = "sha256:" + "1" * 64
    left = ActionSpec(
        action_id="s01-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp=parent,
        semantic_pids=("pid:wall.1",),
        args={"end": [1.0, 0.0, 0.0], "start": [0.0, 0.0, 0.0]},
        allowed_effects=AllowedEffects(create=("LINE",)),
        validation_ruleset_id="line-create-v1",
    )
    right = ActionSpec(
        action_id="s01-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp=parent,
        semantic_pids=("pid:wall.1",),
        args={"start": [0.0, 0.0, 0.0], "end": [1.0, 0.0, 0.0]},
        allowed_effects=AllowedEffects(create=("LINE",)),
        validation_ruleset_id="line-create-v1",
    )

    assert fingerprint_action(left, DEFAULT) == fingerprint_action(right, DEFAULT)


def test_action_fingerprint_has_stable_golden_value():
    action = ActionSpec(
        action_id="s01-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp="sha256:" + "1" * 64,
        semantic_pids=("pid:wall.1",),
        args={"start": [0, 0, 0], "end": [11, 0, 0]},
    )

    assert fingerprint_action(action, DEFAULT) == (
        "sha256:53e7880fae57a2bed34097b5735c5e5b8a30e0b1e5fe4ad67bad6cb0f9ac03ea"
    )


def test_semantic_snapshot_export_contains_pid_and_fingerprint_layers():
    fps = FingerprintSet(
        geometry_fp="sha256:" + "2" * 64,
        style_fp="sha256:" + "3" * 64,
        topology_fp="sha256:" + "4" * 64,
        instance_fp="sha256:" + "5" * 64,
    )
    snapshot = SemanticSnapshot(
        snapshot_id="snap:1",
        document_pid="doc:house",
        units="feet",
        current_space="Model",
        saved=True,
        entities=(
            EntitySemanticState(
                semantic_pid="pid:wall.1",
                native_handle="2AF",
                entity_type="LWPOLYLINE",
                layer="A-WALL-INT",
                geometry={"vertices": [[0.0, 0.0], [1.0, 0.0]], "closed": False},
                fingerprints=fps,
            ),
        ),
        fingerprints=FingerprintSet(document_fp="sha256:" + "6" * 64),
    )

    exported = snapshot.to_dict()
    assert exported["entities"][0]["semantic_pid"] == "pid:wall.1"
    assert exported["entities"][0]["fingerprints"]["geometry_fp"].startswith("sha256:")
    assert exported["fingerprints"]["document_fp"] == "sha256:" + "6" * 64


def test_semantic_delta_detects_distinct_effect_categories_in_contract():
    delta = SemanticDelta(
        created=("pid:new",),
        modified=("pid:changed",),
        deleted=("pid:gone",),
        unexpected_changes=("pid:unexpected",),
    )

    assert delta.to_dict() == {
        "created": ["pid:new"],
        "modified": ["pid:changed"],
        "deleted": ["pid:gone"],
        "unchanged_scope": [],
        "unexpected_changes": ["pid:unexpected"],
    }


def test_verify_parent_state_blocks_state_drift():
    expected = "sha256:" + "a" * 64
    actual = "sha256:" + "b" * 64

    with pytest.raises(StateConflictError, match="STATE_DRIFT"):
        verify_parent_state(expected, actual)


def test_build_state_chain_entry_is_deterministic_and_parent_sensitive():
    parent_state = "sha256:" + "a" * 64
    action = ActionSpec(
        action_id="s01-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp=parent_state,
        args={"start": [0.0, 0.0, 0.0], "end": [1.0, 0.0, 0.0]},
    )
    delta = SemanticDelta(created=("pid:line.1",))
    post_state = {"document_pid": "doc:house", "entities": ["pid:line.1"]}

    first = build_state_chain_entry(
        step_id="s01",
        parent_step_fp=None,
        parent_state_fp=parent_state,
        action=action,
        delta=delta,
        post_state=post_state,
        profile=DEFAULT,
    )
    again = build_state_chain_entry(
        step_id="s01",
        parent_step_fp=None,
        parent_state_fp=parent_state,
        action=action,
        delta=delta,
        post_state=post_state,
        profile=DEFAULT,
    )
    changed_parent_fp = "sha256:" + "c" * 64
    changed_parent_action = ActionSpec(
        action_id="s01-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp=changed_parent_fp,
        args={"start": [0.0, 0.0, 0.0], "end": [1.0, 0.0, 0.0]},
    )
    changed_parent = build_state_chain_entry(
        step_id="s01",
        parent_step_fp=None,
        parent_state_fp=changed_parent_fp,
        action=changed_parent_action,
        delta=delta,
        post_state=post_state,
        profile=DEFAULT,
    )

    assert isinstance(first, StateChainEntry)
    assert first.status is MutationOutcome.COMMITTED_VERIFIED
    assert first == again
    assert first.step_fp != changed_parent.step_fp


def test_build_state_chain_refuses_action_parent_mismatch():
    action = ActionSpec(
        action_id="s02-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp="sha256:" + "a" * 64,
    )

    with pytest.raises(StateConflictError, match="STATE_DRIFT"):
        build_state_chain_entry(
            step_id="s02",
            parent_step_fp="sha256:" + "9" * 64,
            parent_state_fp="sha256:" + "b" * 64,
            action=action,
            delta=SemanticDelta(),
            post_state={"document_pid": "doc:house"},
            profile=DEFAULT,
        )


def test_rollback_receipt_rejects_non_rollback_outcome_status():
    expected = "sha256:" + "d" * 64
    with pytest.raises(ValueError, match="rollback status"):
        RollbackReceipt(
            rollback_id="rb:bad-status",
            reason="VALIDATION_FAILED",
            strategy=RollbackStrategy.R0_ABORT,
            expected_restore_fp=expected,
            actual_restore_fp=expected,
            status=MutationOutcome.COMMITTED_VERIFIED,
        )


def test_rollback_receipt_requires_verified_restore_fingerprint_for_verified_status():
    expected = "sha256:" + "d" * 64

    with pytest.raises(ValueError, match="restore fingerprint"):
        RollbackReceipt(
            rollback_id="rb:1",
            reason="VALIDATION_FAILED",
            strategy=RollbackStrategy.R0_ABORT,
            expected_restore_fp=expected,
            actual_restore_fp="sha256:" + "e" * 64,
            status=MutationOutcome.ROLLED_BACK_VERIFIED,
        )

    receipt = RollbackReceipt(
        rollback_id="rb:2",
        reason="VALIDATION_FAILED",
        strategy=RollbackStrategy.R0_ABORT,
        expected_restore_fp=expected,
        actual_restore_fp=expected,
        status=MutationOutcome.ROLLED_BACK_VERIFIED,
    )
    assert receipt.status is MutationOutcome.ROLLED_BACK_VERIFIED


def test_geometry_fingerprint_has_stable_golden_value():
    payload = {"type": "LINE", "start": [0.0, 0.0, 0.0], "end": [11.0, 0.0, 0.0]}

    assert fingerprint_geometry(payload, DEFAULT) == (
        "sha256:01cdb32f1650693aed7049742176f136add6858e3da0140046361ea025d8a67b"
    )


def test_document_fingerprint_is_independent_of_entity_enumeration_order():
    first = EntitySemanticState(
        semantic_pid="pid:1",
        native_handle="10",
        entity_type="LINE",
        layer="A-WALL",
        geometry={"start": [0.0, 0.0], "end": [1.0, 0.0]},
    )
    second = EntitySemanticState(
        semantic_pid="pid:2",
        native_handle="11",
        entity_type="LINE",
        layer="A-WALL",
        geometry={"start": [2.0, 0.0], "end": [3.0, 0.0]},
    )
    left = SemanticSnapshot(
        snapshot_id="snap:left",
        document_pid="doc:1",
        units="feet",
        current_space="Model",
        saved=True,
        entities=(first, second),
    )
    right = SemanticSnapshot(
        snapshot_id="snap:right",
        document_pid="doc:1",
        units="feet",
        current_space="Model",
        saved=True,
        entities=(second, first),
    )

    assert fingerprint_document(left, DEFAULT) == fingerprint_document(right, DEFAULT)


def test_delta_fingerprint_treats_effect_lists_as_sets():
    left = SemanticDelta(created=("pid:2", "pid:1"), modified=("pid:4", "pid:3"))
    right = SemanticDelta(created=("pid:1", "pid:2"), modified=("pid:3", "pid:4"))

    assert fingerprint_delta(left, DEFAULT) == fingerprint_delta(right, DEFAULT)


def test_action_payload_is_deeply_immutable_after_construction():
    action = ActionSpec(
        action_id="a1",
        operation="entity.create.line",
        document_pid="doc:1",
        expected_parent_fp="sha256:" + "0" * 64,
        args={"geometry": {"vertices": [[0.0, 0.0], [1.0, 0.0]]}},
    )

    with pytest.raises(TypeError):
        action.args["geometry"] = {}
    with pytest.raises(TypeError):
        action.args["geometry"]["vertices"][0] = (5.0, 5.0)


def test_action_spec_rejects_arbitrary_execution_payload_fields():
    with pytest.raises(ValueError, match="arbitrary execution"):
        ActionSpec(
            action_id="a1",
            operation="entity.create.line",
            document_pid="doc:1",
            expected_parent_fp="sha256:" + "0" * 64,
            args={"code": "(command \"LINE\" 0,0 1,1 \"\")"},
        )


def test_document_fingerprint_refuses_duplicate_semantic_pids():
    entities = (
        EntitySemanticState(
            semantic_pid="pid:wall.1",
            native_handle="10",
            entity_type="LINE",
            layer="A-WALL",
            geometry={"start": [0.0, 0.0], "end": [1.0, 0.0]},
        ),
        EntitySemanticState(
            semantic_pid="pid:wall.1",
            native_handle="11",
            entity_type="LINE",
            layer="A-WALL",
            geometry={"start": [2.0, 0.0], "end": [3.0, 0.0]},
        ),
    )
    snapshot = SemanticSnapshot(
        snapshot_id="snap:dupe",
        document_pid="doc:1",
        units="feet",
        current_space="Model",
        saved=True,
        entities=entities,
    )

    with pytest.raises(StateConflictError, match="DUPLICATE_PID"):
        fingerprint_document(snapshot, DEFAULT)


def test_duplicate_pid_detection_fails_closed():
    entities = (
        EntitySemanticState(
            semantic_pid="pid:wall.1",
            native_handle="10",
            entity_type="LINE",
            layer="A-WALL",
            geometry={"start": [0.0, 0.0], "end": [1.0, 0.0]},
        ),
        EntitySemanticState(
            semantic_pid="pid:wall.1",
            native_handle="11",
            entity_type="LINE",
            layer="A-WALL",
            geometry={"start": [2.0, 0.0], "end": [3.0, 0.0]},
        ),
    )

    with pytest.raises(StateConflictError, match="DUPLICATE_PID"):
        assert_unique_pids(entities)


def test_duplicate_geometry_groups_distinguish_content_identity_from_pid_identity():
    shared_fp = fingerprint_geometry(
        {"type": "LINE", "start": [0.0, 0.0], "end": [1.0, 0.0]}, DEFAULT
    )
    entities = (
        EntitySemanticState(
            semantic_pid="pid:wall.1",
            native_handle="10",
            entity_type="LINE",
            layer="A-WALL",
            geometry={},
            fingerprints=FingerprintSet(geometry_fp=shared_fp),
        ),
        EntitySemanticState(
            semantic_pid="pid:wall.2",
            native_handle="11",
            entity_type="LINE",
            layer="A-WALL",
            geometry={},
            fingerprints=FingerprintSet(geometry_fp=shared_fp),
        ),
        EntitySemanticState(
            semantic_pid="pid:wall.3",
            native_handle="12",
            entity_type="LINE",
            layer="A-WALL",
            geometry={},
            fingerprints=FingerprintSet(
                geometry_fp=fingerprint_geometry(
                    {"type": "LINE", "start": [2.0, 0.0], "end": [3.0, 0.0]}, DEFAULT
                )
            ),
        ),
    )

    groups = duplicate_geometry_groups(entities)
    assert groups == {shared_fp: ("pid:wall.1", "pid:wall.2")}


def test_verify_state_chain_detects_broken_parent_link():
    parent_state = "sha256:" + "a" * 64
    action1 = ActionSpec(
        action_id="s01-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp=parent_state,
    )
    first = build_state_chain_entry(
        step_id="s01",
        parent_step_fp=None,
        parent_state_fp=parent_state,
        action=action1,
        delta=SemanticDelta(created=("pid:1",)),
        post_state={"entities": ["pid:1"]},
        profile=DEFAULT,
    )
    action2 = ActionSpec(
        action_id="s02-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp=first.post_state_fp,
    )
    second = build_state_chain_entry(
        step_id="s02",
        parent_step_fp=first.step_fp,
        parent_state_fp=first.post_state_fp,
        action=action2,
        delta=SemanticDelta(created=("pid:2",)),
        post_state={"entities": ["pid:1", "pid:2"]},
        profile=DEFAULT,
    )

    assert verify_state_chain((first, second)) == second.post_state_fp

    broken = StateChainEntry(
        step_id=second.step_id,
        parent_step_fp="sha256:" + "f" * 64,
        parent_state_fp=second.parent_state_fp,
        action_fp=second.action_fp,
        delta_fp=second.delta_fp,
        post_state_fp=second.post_state_fp,
        step_fp=second.step_fp,
        profile_signature=second.profile_signature,
    )
    with pytest.raises(StateConflictError, match="STATE_CHAIN_BROKEN"):
        verify_state_chain((first, broken))


def test_verify_state_chain_detects_tampered_entry_fingerprint():
    parent_state = "sha256:" + "a" * 64
    action = ActionSpec(
        action_id="s01-a01",
        operation="entity.create.line",
        document_pid="doc:house",
        expected_parent_fp=parent_state,
    )
    entry = build_state_chain_entry(
        step_id="s01",
        parent_step_fp=None,
        parent_state_fp=parent_state,
        action=action,
        delta=SemanticDelta(created=("pid:1",)),
        post_state={"entities": ["pid:1"]},
        profile=DEFAULT,
    )
    tampered = StateChainEntry(
        step_id=entry.step_id,
        parent_step_fp=entry.parent_step_fp,
        parent_state_fp=entry.parent_state_fp,
        action_fp=entry.action_fp,
        delta_fp=entry.delta_fp,
        post_state_fp="sha256:" + "e" * 64,
        step_fp=entry.step_fp,
        profile_signature=entry.profile_signature,
    )

    with pytest.raises(StateConflictError, match="STATE_CHAIN_BROKEN"):
        verify_state_chain((tampered,), profile=DEFAULT)
