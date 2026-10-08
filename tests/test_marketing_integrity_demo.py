"""Marketing integrity demo — synthetic scene contract tests.
Wing: code | Topic: marketing-demo | Updated: 2026-09-11 21:52
"""

from __future__ import annotations

from cdt_autocad.native_bridge.protocol import MAX_BATCH_CHUNK_ENTITIES
from scripts.run_marketing_integrity_demo import (
    DEFAULT_RECORDING_DELAY_MS,
    PRODUCT_PRESENTATION_DELAY_MS,
    SCENE_BOUNDS,
    feature_1_entities,
    feature_2_rollback_entities,
    feature_3_entities,
)


def _geometry_key(entity: dict[str, object]) -> str:
    return repr(sorted(entity.items()))


def test_marketing_scene_is_synthetic_bounded_and_camera_friendly():
    feature_1 = feature_1_entities()
    feature_2 = feature_2_rollback_entities()
    feature_3 = feature_3_entities()

    assert len(feature_1) == 40
    assert len(feature_2) == MAX_BATCH_CHUNK_ENTITIES + 1 == 33
    assert len(feature_3) == 16
    assert all(entity["kind"] == "line" for entity in feature_1)
    assert all(entity["kind"] == "circle" for entity in feature_2)
    assert all(entity["kind"] == "circle" for entity in feature_3)
    assert len({_geometry_key(entity) for entity in feature_1}) == len(feature_1)
    assert len({_geometry_key(entity) for entity in feature_3}) == len(feature_3)
    assert SCENE_BOUNDS == (950.0, 950.0, 1850.0, 1450.0)


def test_feature_2_fails_only_after_one_full_native_micro_chunk():
    entities = feature_2_rollback_entities()
    first_chunk = entities[:MAX_BATCH_CHUNK_ENTITIES]

    assert len({_geometry_key(entity) for entity in first_chunk}) == MAX_BATCH_CHUNK_ENTITIES
    assert entities[MAX_BATCH_CHUNK_ENTITIES] == first_chunk[0]


def test_recording_pause_is_distinct_from_product_recommendation():
    assert PRODUCT_PRESENTATION_DELAY_MS == 300
    assert DEFAULT_RECORDING_DELAY_MS >= PRODUCT_PRESENTATION_DELAY_MS
