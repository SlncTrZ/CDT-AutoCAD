"""A0 headless backend contract and timeout-integrity tests.
Wing: code | Topic: autocad-a0 | Updated: 2026-09-09 19:19
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.config import Settings
from cdt_autocad.errors import (
    BackendQuarantinedError,
    BackendTimeoutError,
    UnsupportedCapabilityError,
)

pytestmark = pytest.mark.asyncio


async def test_headless_roundtrip(settings, tmp_path: Path):
    backend = EzdxfBackend(settings)

    created = await backend.document_new()
    assert created["backend"] == "ezdxf"

    layer = await backend.layer_create("GEOMETRY", color=3)
    assert layer.name == "GEOMETRY"
    await backend.layer_set_current("GEOMETRY")

    line = await backend.entity_create_line(0, 0, 10, 0)
    circle = await backend.entity_create_circle(5, 5, 2)
    polyline = await backend.entity_create_polyline([[0, 0], [5, 0], [5, 5]], closed=True)
    text = await backend.entity_create_text("A0", 1, 2, height=2.5)

    assert line.type == "LINE"
    assert circle.type == "CIRCLE"
    assert polyline.type == "LWPOLYLINE"
    assert text.type == "TEXT"
    assert {line.layer, circle.layer, polyline.layer, text.layer} == {"GEOMETRY"}
    assert await backend.object_count() == 4
    assert await backend.object_count(type_filter="line") == 1

    fetched = await backend.object_get(line.id)
    assert fetched.id == line.id
    assert fetched.properties["start"] == [0.0, 0.0, 0.0]

    page = await backend.object_list(limit=2)
    assert len(page) == 2
    second_page = await backend.object_list(limit=2, offset=2)
    assert len(second_page) == 2

    target = tmp_path / "roundtrip.dxf"
    saved = await backend.document_save_as(str(target))
    assert saved["format"] == "dxf"
    assert target.is_file()

    await backend.document_new()
    assert await backend.object_count() == 0

    reopened = await backend.document_open(str(target))
    assert reopened["name"] == "roundtrip.dxf"
    assert await backend.object_count() == 4
    info = await backend.document_info()
    assert info["saved"] is True
    assert info["entity_count"] == 4
    assert info["backend"] == "ezdxf"


async def test_invalid_geometry_and_layer_fail_before_success(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()

    with pytest.raises(ValueError, match="radius"):
        await backend.entity_create_circle(0, 0, 0)

    with pytest.raises(ValueError, match="layer does not exist"):
        await backend.entity_create_line(0, 0, 1, 1, layer="MISSING")

    assert await backend.object_count() == 0


async def test_dwg_requests_refuse_with_capability_key(settings, tmp_path: Path):
    backend = EzdxfBackend(settings)
    await backend.document_new()

    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        await backend.document_save_as(str(tmp_path / "part.dwg"))
    assert excinfo.value.capability == "autocad.dwg.write"
    assert not (tmp_path / "part.dwg").exists()

    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        await backend.document_open(str(tmp_path / "part.dwg"))
    assert excinfo.value.capability == "autocad.dwg.read"


async def test_capability_map_is_explicit(settings):
    backend = EzdxfBackend(settings)
    caps = backend.capabilities()

    assert caps["autocad.dxf.write"]["supported"] is True
    assert caps["autocad.dwg.write"]["supported"] is False
    assert caps["autocad.live_ui"]["supported"] is False
    assert caps["common.transaction.undo"]["supported"] is True
    assert caps["common.transaction.rollback"]["supported"] is True


async def test_timed_out_mutation_quarantines_until_document_rebind(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()

    with pytest.raises(BackendTimeoutError):
        await backend._run(
            lambda: time.sleep(0.05),
            may_mutate_document=True,
            timeout_seconds=0.01,
        )

    assert backend.status()["quarantined"] is True
    with pytest.raises(BackendQuarantinedError):
        await backend.object_count()

    await backend.document_new()
    assert backend.status()["quarantined"] is False
    assert await backend.object_count() == 0
