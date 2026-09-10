"""Native semantic adapter — validate N4 wire snapshots and reuse the N1 semantic core.
Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 12:44
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cdt_autocad.semantic.fingerprint import (
    fingerprint_document,
    fingerprint_geometry,
    fingerprint_instance,
    fingerprint_style,
)
from cdt_autocad.semantic.models import (
    EntitySemanticState,
    FingerprintSet,
    SemanticRelation,
    SemanticSnapshot,
)

from .protocol import BridgeProtocolError


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BridgeProtocolError("INVALID_SNAPSHOT", f"{field} must be an object")
    return value


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise BridgeProtocolError("INVALID_SNAPSHOT", f"{field} must be an array")
    return value


def _string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeProtocolError("INVALID_SNAPSHOT", f"{field} must be a non-empty string")
    return value


def _optional_mapping(value: Any, field: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    return _mapping(value, field)


def _entity_from_wire(value: Any, document_pid: str) -> EntitySemanticState:
    raw = _mapping(value, "entity")
    semantic_pid = _string(raw.get("semantic_pid"), "entity.semantic_pid")
    native_handle = raw.get("native_handle")
    if native_handle is not None and not isinstance(native_handle, str):
        raise BridgeProtocolError("INVALID_SNAPSHOT", "entity.native_handle must be a string or null")
    entity_type = _string(raw.get("entity_type"), "entity.entity_type")
    layer = _string(raw.get("layer"), "entity.layer")
    geometry = _mapping(raw.get("geometry"), "entity.geometry")
    bbox = _optional_mapping(raw.get("bbox"), "entity.bbox")
    metrics = _optional_mapping(raw.get("metrics"), "entity.metrics")
    style = _optional_mapping(raw.get("style"), "entity.style")
    hierarchy = _optional_mapping(raw.get("hierarchy"), "entity.hierarchy")

    provisional = EntitySemanticState(
        semantic_pid=semantic_pid,
        native_handle=native_handle,
        entity_type=entity_type,
        layer=layer,
        geometry=geometry,
        bbox=bbox,
        metrics=metrics,
        style=style,
        hierarchy=hierarchy,
    )
    geometry_fp = fingerprint_geometry(provisional)
    style_fp = fingerprint_style(style or {})
    instance_fp = fingerprint_instance(
        {
            "document_pid": document_pid,
            "semantic_pid": semantic_pid,
            "geometry_fp": geometry_fp,
            "style_fp": style_fp,
        }
    )
    return EntitySemanticState(
        semantic_pid=semantic_pid,
        native_handle=native_handle,
        entity_type=entity_type,
        layer=layer,
        geometry=geometry,
        bbox=bbox,
        metrics=metrics,
        style=style,
        hierarchy=hierarchy,
        fingerprints=FingerprintSet(
            geometry_fp=geometry_fp,
            style_fp=style_fp,
            instance_fp=instance_fp,
        ),
    )


def parse_native_snapshot(
    value: Mapping[str, Any],
    *,
    verify_fingerprint: bool = True,
) -> SemanticSnapshot:
    """Convert one N4 native snapshot into N1 models and verify native/Python fingerprint parity."""

    raw = _mapping(value, "snapshot")
    if raw.get("schema_version") != 1:
        raise BridgeProtocolError("INVALID_SNAPSHOT", "unsupported native snapshot schema_version")
    runtime_document_id = _string(raw.get("runtime_document_id"), "runtime_document_id")
    document_pid = _string(raw.get("document_pid"), "document_pid")
    document = _mapping(raw.get("document"), "document")
    units = _string(document.get("units"), "document.units")
    current_space = _string(document.get("current_space"), "document.current_space")
    saved = document.get("saved")
    if not isinstance(saved, bool):
        raise BridgeProtocolError("INVALID_SNAPSHOT", "document.saved must be a boolean")
    extents = _optional_mapping(document.get("extents"), "document.extents")

    entities = tuple(
        _entity_from_wire(item, document_pid)
        for item in _array(raw.get("entities"), "entities")
    )
    semantic_pids = [entity.semantic_pid for entity in entities]
    if len(semantic_pids) != len(set(semantic_pids)):
        raise BridgeProtocolError("INVALID_SNAPSHOT", "snapshot contains duplicate semantic_pid values")

    relations: list[SemanticRelation] = []
    for item in _array(raw.get("relations"), "relations"):
        relation = _mapping(item, "relation")
        relations.append(
            SemanticRelation(
                relation_type=_string(relation.get("relation_type"), "relation.relation_type"),
                source_pid=_string(relation.get("source_pid"), "relation.source_pid"),
                target_pid=_string(relation.get("target_pid"), "relation.target_pid"),
                properties=_optional_mapping(relation.get("properties"), "relation.properties"),
            )
        )

    styles: list[Mapping[str, Any]] = []
    for item in _array(raw.get("styles"), "styles"):
        styles.append(_mapping(item, "style"))

    snapshot = SemanticSnapshot(
        snapshot_id=f"native:{runtime_document_id}",
        document_pid=document_pid,
        units=units,
        current_space=current_space,
        saved=saved,
        entities=entities,
        relations=tuple(relations),
        styles=tuple(styles),
        extents=extents,
    )
    actual_fp = fingerprint_document(snapshot)
    native_fp = raw.get("document_fp")
    if verify_fingerprint:
        if not isinstance(native_fp, str) or native_fp != actual_fp:
            raise BridgeProtocolError(
                "SNAPSHOT_FINGERPRINT_MISMATCH",
                "native document fingerprint does not match N1 canonical fingerprint",
            )
    elif native_fp and not isinstance(native_fp, str):
        raise BridgeProtocolError("INVALID_SNAPSHOT", "document_fp must be a string")

    return SemanticSnapshot(
        snapshot_id=snapshot.snapshot_id,
        document_pid=snapshot.document_pid,
        units=snapshot.units,
        current_space=snapshot.current_space,
        saved=snapshot.saved,
        entities=snapshot.entities,
        relations=snapshot.relations,
        styles=snapshot.styles,
        extents=snapshot.extents,
        fingerprints=FingerprintSet(document_fp=actual_fp),
    )
