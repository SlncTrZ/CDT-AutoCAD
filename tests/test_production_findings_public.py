"""Production findings closure — public CAD setup, reference and presentation contract.
Wing: code | Topic: production-findings | Updated: 2026-09-11 17:05
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.config import Settings
from cdt_autocad.server import create_mcp


class _NamedCollection:
    def __init__(self, items):
        self.items = list(items)

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index_or_name):
        if isinstance(index_or_name, int):
            return self.items[index_or_name]
        wanted = str(index_or_name).lower()
        for item in self.items:
            if str(item.Name).lower() == wanted:
                return item
        raise KeyError(index_or_name)


def _run_inline(monkeypatch, backend):
    async def inline(fn, *, may_mutate_document=True):
        return fn()

    monkeypatch.setattr(backend, "_run", inline)


@pytest.mark.asyncio
async def test_public_surface_contains_production_setup_reference_view_and_a3_tools(settings):
    app = create_mcp(replace(settings, backend="com"))
    async with Client(app) as client:
        names = {tool.name for tool in await client.list_tools()}

    expected = {
        "document_configure_units",
        "document_dependencies",
        "artifact_seal",
        "layer_update_state",
        "xref_list",
        "xref_attach",
        "xref_reload",
        "xref_unload",
        "xref_detach",
        "object_query",
        "view_set_direction",
        "view_set_preset",
        "view_set_visual_style",
        "dimension_angular",
        "dimension_radial",
        "dimension_diametric",
        "dimension_ordinate",
        "object_measure",
        "drawing_extents",
        "object_intersections",
        "solid_create_primitive",
        "solid_extrude",
        "solid_sweep",
        "solid_revolve",
        "solid_boolean",
        "solid_transform",
        "solid_inspect",
        "solid_export",
    }
    assert expected <= names


@pytest.mark.asyncio
async def test_document_configure_units_is_typed_and_reads_back(settings, monkeypatch):
    values = {"INSUNITS": 0, "MEASUREMENT": 0, "LUNITS": 2, "LUPREC": 4}

    class Doc:
        def SetVariable(self, name, value):
            values[name] = value

        def GetVariable(self, name):
            return values[name]

    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: Doc())
    _run_inline(monkeypatch, backend)

    result = await backend.document_configure_units(
        insertion_units="millimeters",
        measurement="metric",
        linear_format="decimal",
        linear_precision=3,
    )
    assert result == {
        "insertion_units": "millimeters",
        "measurement": "metric",
        "linear_format": "decimal",
        "linear_precision": 3,
        "raw": {"INSUNITS": 4, "MEASUREMENT": 1, "LUNITS": 2, "LUPREC": 3},
    }

    with pytest.raises(ValueError, match="insertion_units"):
        await backend.document_configure_units(insertion_units="parsecs")


@pytest.mark.asyncio
async def test_layer_update_state_reads_back_and_protects_current_layer(settings, monkeypatch):
    active = SimpleNamespace(Name="WORK")
    work = SimpleNamespace(
        Name="WORK", Color=7, Linetype="Continuous", LineWeight=-3,
        LayerOn=True, Freeze=False, Lock=False,
    )
    ref = SimpleNamespace(
        Name="REF", Color=8, Linetype="Continuous", LineWeight=-3,
        LayerOn=True, Freeze=False, Lock=False,
    )
    doc = SimpleNamespace(ActiveLayer=active, Layers=_NamedCollection([work, ref]))
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    _run_inline(monkeypatch, backend)

    updated = await backend.layer_update_state("REF", is_on=False, is_locked=True)
    assert updated.name == "REF"
    assert updated.is_on is False
    assert updated.is_locked is True

    with pytest.raises(ValueError, match="current layer"):
        await backend.layer_update_state("WORK", is_frozen=True)
    with pytest.raises(ValueError, match="current layer"):
        await backend.layer_update_state("WORK", is_on=False)


@pytest.mark.asyncio
async def test_block_list_filters_xref_dependent_noise_and_pages(settings, monkeypatch):
    def block(name, *, is_xref=False, count=1):
        return SimpleNamespace(
            Name=name,
            Origin=(0.0, 0.0, 0.0),
            Count=count,
            IsXRef=is_xref,
            Item=lambda _index: SimpleNamespace(ObjectName="AcDbLine"),
        )

    doc = SimpleNamespace(
        Blocks=_NamedCollection(
            [
                block("LOCAL_A"),
                block("REF"),
                block("REF|NOISY_1"),
                block("REF|NOISY_2"),
                block("XREF_ROOT", is_xref=True),
            ]
        )
    )
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    _run_inline(monkeypatch, backend)

    rows = await backend.block_list(include_xref_dependent=False, limit=2, offset=0)
    assert [row.name for row in rows] == ["LOCAL_A", "REF"]
    rows2 = await backend.block_list(include_xref_dependent=True, limit=2, offset=2)
    assert [row.name for row in rows2] == ["REF|NOISY_1", "REF|NOISY_2"]


@pytest.mark.asyncio
async def test_view_visual_style_uses_only_allowlisted_bounded_command(settings, monkeypatch):
    sent: list[str] = []

    class Doc:
        def SendCommand(self, command):
            sent.append(command)

        def GetVariable(self, name):
            assert name == "CMDNAMES"
            return ""

        def Regen(self, _mode):
            pass

    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: Doc())
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    _run_inline(monkeypatch, backend)

    result = await backend.view_set_visual_style("shades_of_gray")
    assert result["visual_style"] == "shades_of_gray"
    assert result["command_idle_verified"] is True
    assert sent == ["_.VSCURRENT\n_Shadesofgray\n"]

    with pytest.raises(ValueError, match="visual style"):
        await backend.view_set_visual_style("$(arbitrary-command)")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_view_preset_se_isometric_is_typed_direction(settings, monkeypatch):
    viewport = SimpleNamespace(Direction=None)
    doc = SimpleNamespace(ActiveViewport=viewport)
    app = SimpleNamespace(ZoomExtents=lambda: None)
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(backend, "_app", lambda: app)
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (x, y, z))
    _run_inline(monkeypatch, backend)

    result = await backend.view_set_preset("se_isometric")
    assert result["preset"] == "se_isometric"
    direction = result["direction"]
    assert direction[0] == pytest.approx(1 / math.sqrt(3))
    assert direction[1] == pytest.approx(-1 / math.sqrt(3))
    assert direction[2] == pytest.approx(1 / math.sqrt(3))


@pytest.mark.asyncio
async def test_xref_attach_validates_path_and_uses_overlay(settings, monkeypatch, tmp_path: Path):
    source = tmp_path / "source.dwg"
    source.write_bytes(b"dwg-placeholder")
    calls = []

    class Space:
        def AttachExternalReference(self, path, name, insertion, sx, sy, sz, rotation, overlay):
            calls.append((path, name, insertion, sx, sy, sz, rotation, overlay))
            return SimpleNamespace(Handle="A1", Name=name, Layer="0", ObjectName="AcDbBlockReference")

    backend = ComBackend(
        replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),))
    )
    monkeypatch.setattr(backend, "_space", lambda: Space())
    monkeypatch.setattr(backend, "_doc", lambda: SimpleNamespace(Blocks=_NamedCollection([])))
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (x, y, z))
    _run_inline(monkeypatch, backend)

    result = await backend.xref_attach(str(source), name="REF_A", overlay=True)
    assert result["name"] == "REF_A"
    assert result["overlay"] is True
    assert Path(result["path"]) == source.resolve()
    assert calls and calls[0][-1] is True

    outside = tmp_path.parent / "outside-source.dwg"
    outside.write_bytes(b"x")
    try:
        with pytest.raises(ValueError):
            await backend.xref_attach(str(outside), name="NOPE")
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_polyline_exact_bulges_are_applied_on_com(settings, monkeypatch):
    class Poly:
        ObjectName = "AcDbPolyline"
        Handle = "10"
        Layer = "0"
        Color = 256
        Linetype = "ByLayer"
        Visible = True
        Coordinates = [0.0, 0.0, 10.0, 0.0, 10.0, 10.0]
        Closed = False
        Elevation = 0.0

        def __init__(self):
            self.bulges = {}
            self.widths = {}

        def SetBulge(self, index, value):
            self.bulges[int(index)] = float(value)

        def SetWidth(self, index, start, end):
            self.widths[int(index)] = (float(start), float(end))

    poly = Poly()
    space = SimpleNamespace(AddLightWeightPolyline=lambda _coords: poly)
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_space", lambda: space)
    monkeypatch.setattr(backend, "_validate_layer", lambda layer: layer)
    monkeypatch.setattr(backend, "_validate_color", lambda color: color)
    monkeypatch.setattr(cb, "_double_array", lambda values: tuple(values))
    _run_inline(monkeypatch, backend)

    created = await backend.entity_create_polyline(
        [[0, 0], [10, 0], [10, 10]],
        closed=False,
        bulges=[0.5, 0.0, 0.0],
        widths=[[1.0, 2.0], [0.0, 0.0], [0.0, 0.0]],
        elevation=3.0,
    )
    assert poly.bulges == {0: 0.5, 1: 0.0, 2: 0.0}
    assert poly.widths[0] == (1.0, 2.0)
    assert poly.Elevation == 3.0
    assert created.type == "LWPOLYLINE"
