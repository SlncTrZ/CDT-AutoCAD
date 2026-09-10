"""Semantic fingerprinting — domain-separated SHA-256 over canonical CAD state payloads.
Wing: code | Topic: semantic-state-n1 | Updated: 2026-09-10 13:40
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from .canonical import ToleranceProfile, canonical_json
from .identity import assert_unique_pids
from .models import EntitySemanticState, SemanticDelta, SemanticSnapshot

_FINGERPRINT_NAMESPACE = "cdt-autocad-semantic"
_FINGERPRINT_SCHEMA_VERSION = 1


def semantic_fingerprint(
    value: Any,
    *,
    domain: str,
    profile: ToleranceProfile | None = None,
    float_kind: str = "linear",
) -> str:
    """Hash one canonical semantic payload using an explicit domain separator."""

    if not domain or any(char.isspace() for char in domain):
        raise ValueError("fingerprint domain must be a non-empty whitespace-free token")
    selected = profile or ToleranceProfile()
    prefix = "\0".join(
        (
            _FINGERPRINT_NAMESPACE,
            f"v{_FINGERPRINT_SCHEMA_VERSION}",
            domain,
            selected.signature(),
            float_kind,
            "",
        )
    ).encode("utf-8")
    body = canonical_json(value, selected, float_kind=float_kind).encode("utf-8")
    return "sha256:" + hashlib.sha256(prefix + body).hexdigest()


def _normalize_geometry_mapping(
    entity_type: str, geometry: Mapping[str, Any], profile: ToleranceProfile
) -> dict[str, Any]:
    normalized = dict(geometry)
    if entity_type.upper() in {"LINE", "ACDBLINE"} and {"start", "end"} <= normalized.keys():
        start = normalized["start"]
        end = normalized["end"]
        start_key = canonical_json(start, profile, float_kind="linear")
        end_key = canonical_json(end, profile, float_kind="linear")
        if end_key < start_key:
            normalized["start"], normalized["end"] = end, start
    return normalized


def _geometry_payload(value: Any, profile: ToleranceProfile) -> Any:
    if isinstance(value, EntitySemanticState):
        return {
            "type": value.entity_type,
            **_normalize_geometry_mapping(value.entity_type, value.geometry, profile),
        }

    if isinstance(value, Mapping):
        normalized = dict(value)
        entity_type = str(normalized.get("type") or normalized.get("entity_type") or "")
        nested = normalized.get("geometry")
        if entity_type and isinstance(nested, Mapping):
            normalized["geometry"] = _normalize_geometry_mapping(entity_type, nested, profile)
            return normalized
        if entity_type:
            return _normalize_geometry_mapping(entity_type, normalized, profile)
        return normalized
    return value


def fingerprint_geometry(value: Any, profile: ToleranceProfile | None = None) -> str:
    selected = profile or ToleranceProfile()
    payload = _geometry_payload(value, selected)
    return semantic_fingerprint(payload, domain="geometry", profile=selected, float_kind="linear")


def fingerprint_style(value: Any, profile: ToleranceProfile | None = None) -> str:
    return semantic_fingerprint(value, domain="style", profile=profile, float_kind="scalar")


def fingerprint_topology(value: Any, profile: ToleranceProfile | None = None) -> str:
    return semantic_fingerprint(value, domain="topology", profile=profile, float_kind="linear")


def fingerprint_instance(value: Any, profile: ToleranceProfile | None = None) -> str:
    return semantic_fingerprint(value, domain="instance", profile=profile, float_kind="linear")


def fingerprint_scope(value: Any, profile: ToleranceProfile | None = None) -> str:
    return semantic_fingerprint(value, domain="scope", profile=profile, float_kind="linear")


def _snapshot_document_payload(
    snapshot: SemanticSnapshot, profile: ToleranceProfile
) -> dict[str, Any]:
    assert_unique_pids(snapshot.entities)
    entities = [entity.to_dict() for entity in snapshot.entities]
    for entity in entities:
        entity.pop("native_handle", None)
        entity.pop("fingerprints", None)
        entity["geometry"] = _normalize_geometry_mapping(
            entity["entity_type"], entity["geometry"], profile
        )
    entities.sort(key=lambda item: item["semantic_pid"])

    relations = [relation.to_dict() for relation in snapshot.relations]
    relations.sort(
        key=lambda item: (
            item["relation_type"],
            item["source_pid"],
            item["target_pid"],
            canonical_json(item.get("properties") or {}, profile, float_kind="linear"),
        )
    )
    styles = [dict(style) for style in snapshot.styles]
    styles.sort(key=lambda item: canonical_json(item, profile, float_kind="scalar"))

    return {
        "schema_version": snapshot.schema_version,
        "document_pid": snapshot.document_pid,
        "units": snapshot.units,
        "current_space": snapshot.current_space,
        "saved": snapshot.saved,
        "extents": snapshot.extents,
        "entities": entities,
        "relations": relations,
        "styles": styles,
    }


def fingerprint_document(value: Any, profile: ToleranceProfile | None = None) -> str:
    selected = profile or ToleranceProfile()
    payload = (
        _snapshot_document_payload(value, selected)
        if isinstance(value, SemanticSnapshot)
        else value
    )
    return semantic_fingerprint(payload, domain="document", profile=selected, float_kind="linear")


def fingerprint_action(value: Any, profile: ToleranceProfile | None = None) -> str:
    return semantic_fingerprint(value, domain="action", profile=profile, float_kind="linear")


def fingerprint_delta(value: Any, profile: ToleranceProfile | None = None) -> str:
    if isinstance(value, SemanticDelta):
        payload = {
            "created": sorted(value.created),
            "modified": sorted(value.modified),
            "deleted": sorted(value.deleted),
            "unchanged_scope": sorted(value.unchanged_scope),
            "unexpected_changes": sorted(value.unexpected_changes),
        }
    else:
        payload = value
    return semantic_fingerprint(payload, domain="delta", profile=profile, float_kind="linear")


def fingerprint_step(value: Any, profile: ToleranceProfile | None = None) -> str:
    return semantic_fingerprint(value, domain="step", profile=profile, float_kind="linear")


def fingerprint_artifact_bytes(data: bytes) -> str:
    """Hash opaque native artifact bytes without semantic canonicalization."""

    return "sha256:" + hashlib.sha256(data).hexdigest()
