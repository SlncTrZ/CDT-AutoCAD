"""MP-G05 native 3DSOLID read semantics — PID + deterministic mass-signature fingerprint.
Wing: code | Topic: native-solid-semantic | Updated: 2026-09-12
"""

from __future__ import annotations

from pathlib import Path

from cdt_autocad.semantic.fingerprint import fingerprint_geometry
from cdt_autocad.semantic.models import EntitySemanticState

ROOT = Path(__file__).resolve().parents[1]


def _solid_state(*, centroid: tuple[float, float, float]) -> EntitySemanticState:
    cx, cy, cz = centroid
    return EntitySemanticState(
        semantic_pid="pid:11111111-1111-4111-8111-111111111111",
        native_handle="7E",
        entity_type="3DSOLID",
        layer="0",
        geometry={
            "solid_fingerprint_schema_version": 1,
            "centroid": [cx, cy, cz],
            "volume": 480.0,
            "mass_extents": {
                "min": [cx - 5.0, cy - 4.0, cz - 3.0],
                "max": [cx + 5.0, cy + 4.0, cz + 3.0],
            },
            "moments_of_inertia": [4000.0, 5440.0, 6560.0],
            "products_of_inertia": [0.0, 0.0, 0.0],
            "principal_moments": [4000.0, 5440.0, 6560.0],
            "radii_of_gyration": [2.8867513459, 3.3665016461, 3.6968455021],
        },
        bbox={
            "min": [cx - 5.0, cy - 4.0, cz - 3.0],
            "max": [cx + 5.0, cy + 4.0, cz + 3.0],
        },
        metrics={
            "volume": 480.0,
            "verification_scope": "mass-properties-v1",
            "topology_verified": False,
        },
    )


def test_native_extractor_declares_versioned_3dsolid_mass_signature_without_topology_claim():
    source = (ROOT / "native/CDT.AutoCAD.Bridge/NativeEntityExtractor.cs").read_text(
        encoding="utf-8"
    )

    required = (
        "case Solid3d solid:",
        'entityType = "3DSOLID"',
        '"solid_fingerprint_schema_version"',
        "mass.Centroid",
        "mass.Volume",
        "mass.Extents.MinPoint",
        "mass.Extents.MaxPoint",
        "mass.MomentsOfIntertia",
        "mass.ProductsOfIntertia",
        "mass.PrincipalMoments",
        "mass.RadiiOfGyration",
        'metrics["verification_scope"] = "mass-properties-v1"',
        'metrics["topology_verified"] = false',
    )
    for marker in required:
        assert marker in source


def test_solid_geometry_fingerprint_is_deterministic_and_translation_sensitive():
    baseline = _solid_state(centroid=(5.0, 7.0, 3.0))
    identical = _solid_state(centroid=(5.0, 7.0, 3.0))
    moved = _solid_state(centroid=(7.0, 7.0, 3.0))

    baseline_fp = fingerprint_geometry(baseline)
    assert fingerprint_geometry(identical) == baseline_fp
    assert fingerprint_geometry(moved) != baseline_fp
