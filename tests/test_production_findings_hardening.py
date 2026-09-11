"""Production findings closure — artifact, preset, DXF parity and 3D export hardening.
Wing: code | Topic: production-findings | Updated: 2026-09-11 17:25
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.command_presets import BOUNDED_COMMAND_PRESETS, VIEW_PRESETS, WORKFLOW_DEFAULTS


def _run_inline(monkeypatch, backend):
    async def inline(fn, *, may_mutate_document=True):
        return fn()

    monkeypatch.setattr(backend, "_run", inline)


def test_command_preset_registry_locks_video_friendly_defaults():
    assert WORKFLOW_DEFAULTS == {
        "step_delay_ms": 300,
        "default_3d_view": "se_isometric",
        "default_3d_visual_style": "shades_of_gray",
    }
    assert VIEW_PRESETS["se_isometric"] == (1.0, -1.0, 1.0)
    assert BOUNDED_COMMAND_PRESETS["visual.shades_of_gray"] == (
        "_.VSCURRENT\n_Shadesofgray\n"
    )
    assert "display.regen" in BOUNDED_COMMAND_PRESETS
    assert not any("LISP" in value.upper() for value in BOUNDED_COMMAND_PRESETS.values())


@pytest.mark.asyncio
async def test_ezdxf_units_polyline_curves_and_layer_state_roundtrip(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    units = await backend.document_configure_units(
        "millimeters", "metric", "decimal", 3
    )
    assert units["raw"] == {"INSUNITS": 4, "MEASUREMENT": 1, "LUNITS": 2, "LUPREC": 3}

    await backend.layer_create("CURVES", 3)
    layer = await backend.layer_update_state("CURVES", is_locked=True, color=4)
    assert layer.is_locked is True
    assert layer.color == 4

    entity = await backend.entity_create_polyline(
        [[0, 0], [10, 0], [10, 10]],
        closed=False,
        layer="CURVES",
        bulges=[0.5, 0.0, 0.0],
        widths=[[1.0, 2.0], [0.0, 0.0], [0.0, 0.0]],
        elevation=3.0,
    )
    measured = await backend.object_measure(entity.id)
    assert measured["type"] == "LWPOLYLINE"
    assert measured["length"] > 20.0


@pytest.mark.asyncio
async def test_artifact_seal_copies_content_addressed_clean_saved_drawing(settings, monkeypatch, tmp_path: Path):
    drawing = tmp_path / "part.dwg"
    drawing.write_bytes(b"trusted-dwg-bytes")

    class Doc:
        FullName = str(drawing)
        Saved = True

        def Save(self):
            self.Saved = True

    backend = ComBackend(replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),)))
    monkeypatch.setattr(backend, "_doc", lambda: Doc())
    _run_inline(monkeypatch, backend)

    sealed = await backend.artifact_seal()
    digest = hashlib.sha256(drawing.read_bytes()).hexdigest()
    assert sealed["status"] == "SEALED"
    assert sealed["sha256"] == digest
    sealed_path = Path(sealed["sealed_path"])
    assert sealed_path.name == f"part.{digest[:16]}.dwg"
    assert sealed_path.read_bytes() == drawing.read_bytes()
    assert Path(sealed["manifest_path"]).is_file()


@pytest.mark.asyncio
async def test_solid_export_is_sat_only_and_cleans_selection_set(settings, monkeypatch, tmp_path: Path):
    target = tmp_path / "part.sat"
    deleted = []
    added = []

    class Selection:
        def AddItems(self, objects):
            added.extend(list(objects))

        def Delete(self):
            deleted.append(True)

    class SelectionSets:
        def Add(self, _name):
            return Selection()

    class Doc:
        def __init__(self):
            self.SelectionSets = SelectionSets()

        def Export(self, base_name, extension, _selection):
            assert extension == "SAT"
            Path(base_name + ".sat").write_bytes(b"sat-data")

    backend = ComBackend(replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),)))
    monkeypatch.setattr(backend, "_doc", lambda: Doc())
    monkeypatch.setattr(backend, "_solid_by_id", lambda handle: SimpleNamespace(Handle=handle))
    monkeypatch.setattr(cb, "_dispatch_array", lambda objects: tuple(objects))
    _run_inline(monkeypatch, backend)

    result = await backend.solid_export(["A1", "B2"], str(target), "sat")
    assert result["format"] == "sat"
    assert result["solid_handles"] == ["A1", "B2"]
    assert target.read_bytes() == b"sat-data"
    assert len(added) == 2
    assert deleted == [True]

    with pytest.raises(Exception) as exc_info:
        await backend.solid_export(["A1"], str(tmp_path / "part.step"), "step")
    assert "SAT only" in str(exc_info.value)
