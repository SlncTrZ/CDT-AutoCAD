"""D9 existing-drawing workflow — offline persistence leg (DXF).

Wing: code | Topic: autocad-d9 | Updated: 2026-10-05 21:16

D9 closure trigger requires the concrete production workflow
existing-DWG reopen -> mutate -> save -> seal with same-document,
persistence and sealed-artifact verification. The DWG-reopen and seal
legs need live AutoCAD and stay DEFERRED (see report).

This module closes the offline persistence leg on the headless lane: a
pre-existing drawing file (not created by the backend under test) is
reopened, mutated, saved to the same path, and verified by an
independent reader plus a second backend instance. No live AutoCAD.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from cdt_autocad.backends.ezdxf_backend import EzdxfBackend

pytestmark = pytest.mark.asyncio


def _seed_existing_drawing(path: Path) -> None:
    doc = ezdxf.new(dxfversion="R2010")
    doc.layers.add("GEOMETRY", color=3)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "GEOMETRY"})
    msp.add_circle((5, 5), 2, dxfattribs={"layer": "GEOMETRY"})
    doc.saveas(path)


async def test_existing_drawing_reopen_mutate_save_same_path_persists(settings, tmp_path: Path):
    target = tmp_path / "existing.dxf"
    _seed_existing_drawing(target)

    backend = EzdxfBackend(settings)
    reopened = await backend.document_open(str(target))
    assert reopened["path"] == str(target)
    # Regression guard: the seed uses CRLF newlines like every real-world
    # DXF; the reopen must not silently degrade to a phantom empty document.
    assert backend._require_doc().dxfversion == "AC1024"
    assert await backend.object_count() == 2

    created = await backend.entity_create_line(1, 1, 9, 1, layer="GEOMETRY")
    assert created.type == "LINE"

    saved = await backend.document_save()
    assert saved["path"] == str(target)
    info = await backend.document_info()
    assert info["path"] == str(target)
    assert info["saved"] is True
    assert info["entity_count"] == 3

    # Independent read-back: the mutation reached disk on the same document.
    disk = ezdxf.readfile(target)
    disk_lines = [e for e in disk.modelspace() if e.dxftype() == "LINE"]
    assert len(disk_lines) == 2
    assert tuple(disk_lines[1].dxf.start)[:2] == pytest.approx((1, 1))

    # A second backend instance observes the same persisted state.
    verifier = EzdxfBackend(settings)
    await verifier.document_open(str(target))
    assert await verifier.object_count() == 3
    verifier_info = await verifier.document_info()
    assert verifier_info["path"] == str(target)
    assert verifier_info["entity_count"] == 3


async def test_reopen_after_save_as_keeps_same_bound_path(settings, tmp_path: Path):
    source = tmp_path / "source.dxf"
    renamed = tmp_path / "renamed.dxf"
    _seed_existing_drawing(source)

    backend = EzdxfBackend(settings)
    await backend.document_open(str(source))
    saved_as = await backend.document_save_as(str(renamed))
    assert saved_as["path"] == str(renamed)

    await backend.entity_create_text("D9", 0, 0, height=1.0)
    saved = await backend.document_save()
    assert saved["path"] == str(renamed)

    disk = ezdxf.readfile(renamed)
    assert len(disk.modelspace()) == 3
    # The source document is untouched by the rename + later mutation.
    assert len(ezdxf.readfile(source).modelspace()) == 2
