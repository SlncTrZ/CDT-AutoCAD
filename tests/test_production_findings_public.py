"""Production findings closure — public CAD setup, reference and presentation contract.
Wing: code | Topic: production-findings | Updated: 2026-09-11 17:05
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import Client

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.errors import StateConflictError
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

    class VisualFacade:
        state = {"visual_style_handle": "A1", "visual_style_name": "2D Wireframe"}

        def visual_style_get(self):
            return dict(self.state)

        def visual_style_set(self, *_args, **_kwargs):
            raise AssertionError("success path must not restore predecessor style")

    facade = VisualFacade()

    class Doc:
        Saved = True
        dbmod = 0

        def SendCommand(self, command):
            sent.append(command)
            facade.state = {
                "visual_style_handle": "B2",
                "visual_style_name": "Shades of Gray",
            }

        def GetVariable(self, name):
            if name == "CMDNAMES":
                return ""
            if name == "DBMOD":
                return self.dbmod
            raise KeyError(name)

        def Regen(self, _mode):
            pass

    doc = Doc()
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(backend, "_native_view_facade", lambda: facade)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    _run_inline(monkeypatch, backend)

    result = await backend.view_set_visual_style("shades_of_gray")
    assert result["visual_style"] == "shades_of_gray"
    assert result["visual_style_readback"] == "Shades of Gray"
    assert result["visual_style_handle"] == "B2"
    assert result["previous_visual_style"] == "2D Wireframe"
    assert result["previous_visual_style_handle"] == "A1"
    assert result["state_verified"] is True
    assert result["verification_route"] == "native-managed-bridge"
    assert result["command_idle_verified"] is True
    assert result["artifact_state"]["dirty_changed"] is False
    assert sent == ["_.VSCURRENT\n_Shadesofgray\n"]

    with pytest.raises(ValueError, match="visual style"):
        await backend.view_set_visual_style("$(arbitrary-command)")
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_view_visual_style_mismatch_restores_previous_style(settings, monkeypatch):
    class VisualFacade:
        def __init__(self):
            self.state = {"visual_style_handle": "A1", "visual_style_name": "2D Wireframe"}
            self.restores = []

        def visual_style_get(self):
            return dict(self.state)

        def visual_style_set(self, handle, *, expected_current_handle):
            self.restores.append((handle, expected_current_handle))
            assert expected_current_handle == self.state["visual_style_handle"]
            self.state = {"visual_style_handle": handle, "visual_style_name": "2D Wireframe"}
            return {**self.state, "readback_verified": True}

    facade = VisualFacade()

    class Doc:
        Saved = True
        dbmod = 0

        def SendCommand(self, _command):
            facade.state = {"visual_style_handle": "C3", "visual_style_name": "Realistic"}

        def GetVariable(self, name):
            if name == "CMDNAMES":
                return ""
            if name == "DBMOD":
                return self.dbmod
            raise KeyError(name)

        def Regen(self, _mode):
            pass

    doc = Doc()
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(backend, "_native_view_facade", lambda: facade)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    _run_inline(monkeypatch, backend)

    with pytest.raises(Exception, match="visual-style read-back mismatch"):
        await backend.view_set_visual_style("shades_of_gray")
    assert facade.state == {"visual_style_handle": "A1", "visual_style_name": "2D Wireframe"}
    assert facade.restores == [("A1", "C3")]
    assert backend.status()["integrity_uncertain"] is False


@pytest.mark.asyncio
async def test_view_visual_style_restore_failure_quarantines_backend(settings, monkeypatch):
    class VisualFacade:
        def __init__(self):
            self.state = {"visual_style_handle": "A1", "visual_style_name": "2D Wireframe"}

        def visual_style_get(self):
            return dict(self.state)

        def visual_style_set(self, _handle, *, expected_current_handle):
            assert expected_current_handle == "C3"
            raise RuntimeError("restore failed")

    facade = VisualFacade()

    class Doc:
        Saved = True
        dbmod = 0

        def SendCommand(self, _command):
            facade.state = {"visual_style_handle": "C3", "visual_style_name": "Realistic"}

        def GetVariable(self, name):
            if name == "CMDNAMES":
                return ""
            if name == "DBMOD":
                return self.dbmod
            raise KeyError(name)

        def Regen(self, _mode):
            pass

    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: Doc())
    monkeypatch.setattr(backend, "_native_view_facade", lambda: facade)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    _run_inline(monkeypatch, backend)

    with pytest.raises(Exception, match="visual-style restore verification failed"):
        await backend.view_set_visual_style("shades_of_gray")
    assert backend.status()["integrity_uncertain"] is True


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
    assert result["source_sha256"] == __import__("hashlib").sha256(b"dwg-placeholder").hexdigest()
    assert result["source_size"] == len(b"dwg-placeholder")
    assert calls and calls[0][-1] is True

    outside = tmp_path.parent / "outside-source.dwg"
    outside.write_bytes(b"x")
    try:
        with pytest.raises(ValueError):
            await backend.xref_attach(str(outside), name="NOPE")
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_xref_attach_quarantines_if_source_changes_during_attach(
    settings, monkeypatch, tmp_path: Path
):
    source = tmp_path / "source-drift.dwg"
    source.write_bytes(b"before")
    blocks = _NamedCollection([])

    class XrefBlock:
        IsXRef = True

        def __init__(self, name):
            self.Name = name

        def Detach(self):
            blocks.items.remove(self)

    class Reference:
        Handle = "A2"
        Layer = "0"
        ObjectName = "AcDbBlockReference"

        def __init__(self, name, collection):
            self.Name = name
            self._collection = collection

        def Delete(self):
            self._collection.items.remove(self)

    class Space:
        def __init__(self):
            self.items = []

        @property
        def Count(self):
            return len(self.items)

        def Item(self, index):
            return self.items[index]

        def AttachExternalReference(self, _path, name, *_args):
            blocks.items.append(XrefBlock(name))
            reference = Reference(name, self)
            self.items.append(reference)
            return reference

    space = Space()
    doc = SimpleNamespace(Blocks=blocks)
    backend = ComBackend(
        replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),))
    )
    monkeypatch.setattr(backend, "_space", lambda: space)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (x, y, z))

    async def _run_with_source_drift(func, **_kwargs):
        source.write_bytes(b"after")
        return func()

    monkeypatch.setattr(backend, "_run", _run_with_source_drift)

    with pytest.raises(StateConflictError, match="source changed during attach"):
        await backend.xref_attach(str(source), name="REF_DRIFT")
    assert backend.status()["integrity_uncertain"] is True


@pytest.mark.asyncio
async def test_xref_attach_configuration_failure_removes_partial_reference_and_definition(
    settings, monkeypatch, tmp_path: Path
):
    source = tmp_path / "source-config-fail.dwg"
    source.write_bytes(b"stable")
    blocks = _NamedCollection([])

    class XrefBlock:
        IsXRef = True

        def __init__(self, name):
            self.Name = name

        def Detach(self):
            blocks.items.remove(self)

    class Reference:
        Handle = "A3"
        Name = "REF_FAIL"
        ObjectName = "AcDbBlockReference"

        def __init__(self, collection):
            self._collection = collection
            self._layer = "0"

        @property
        def Layer(self):
            return self._layer

        @Layer.setter
        def Layer(self, value):
            if value == "TARGET":
                raise RuntimeError("injected xref layer assignment failure")
            self._layer = value

        def Delete(self):
            self._collection.items.remove(self)

    class Space:
        def __init__(self):
            self.items = []

        @property
        def Count(self):
            return len(self.items)

        def Item(self, index):
            return self.items[index]

        def AttachExternalReference(self, _path, name, *_args):
            blocks.items.append(XrefBlock(name))
            reference = Reference(self)
            self.items.append(reference)
            return reference

    space = Space()
    doc = SimpleNamespace(Blocks=blocks)
    backend = ComBackend(
        replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),))
    )
    monkeypatch.setattr(backend, "_space", lambda: space)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(backend, "_validate_layer", lambda _layer: "TARGET")
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (x, y, z))
    _run_inline(monkeypatch, backend)

    with pytest.raises(RuntimeError, match="xref layer assignment failure"):
        await backend.xref_attach(str(source), name="REF_FAIL", layer="TARGET")
    assert space.items == []
    assert blocks.items == []
    assert backend.status()["integrity_uncertain"] is False


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


_LIVE_COM_ENABLED = sys.platform == "win32" and os.environ.get("CDT_AUTOCAD_LIVE_TEST") == "1"
_LIVE_COM_PROGID = os.environ.get("CDT_AUTOCAD_COM_PROGID", "AutoCAD.Application.26")


def _tool_payload(result):
    content = result.structured_content or {}
    if isinstance(content, dict) and set(content) == {"result"}:
        return content["result"]
    return content


def _live_com_retry(func):
    return cb._com_call_with_busy_retry(func, attempts=20, delay_seconds=0.05)


@pytest.mark.skipif(
    not _LIVE_COM_ENABLED,
    reason="requires Windows + real AutoCAD + CDT_AUTOCAD_LIVE_TEST=1",
)
@pytest.mark.asyncio
async def test_live_public_units_layers_and_dispatch_inspection(settings, tmp_path: Path):
    import win32com.client
    import win32com.client.dynamic

    app = create_mcp(
        replace(
            settings,
            allowed_paths=(tmp_path.resolve(),),
            backend="com",
            com_progid=_LIVE_COM_PROGID,
            com_attach_policy="attach_only",
            com_call_timeout_seconds=60.0,
        )
    )
    created_names: set[str] = set()
    fixture_path = tmp_path / "cdt-public-xref-fixture.dwg"

    def dynamic_layer_snapshot(name: str) -> dict[str, object]:
        acad = _live_com_retry(lambda: win32com.client.GetActiveObject(_LIVE_COM_PROGID))
        doc = _live_com_retry(lambda: acad.ActiveDocument)
        layers = _live_com_retry(lambda: doc.Layers)
        raw = _live_com_retry(lambda: layers.Item(name))
        layer = win32com.client.dynamic.DumbDispatch(raw._oleobj_)
        return {
            "LayerOn": bool(_live_com_retry(lambda: layer.LayerOn)),
            "Freeze": bool(_live_com_retry(lambda: layer.Freeze)),
            "Lock": bool(_live_com_retry(lambda: layer.Lock)),
            "Color": int(_live_com_retry(lambda: layer.Color)),
            "Linetype": str(_live_com_retry(lambda: layer.Linetype)),
            "LineWeight": int(_live_com_retry(lambda: layer.LineWeight)),
        }

    def raw_units() -> dict[str, int]:
        acad = _live_com_retry(lambda: win32com.client.GetActiveObject(_LIVE_COM_PROGID))
        doc = _live_com_retry(lambda: acad.ActiveDocument)
        return {
            name: int(_live_com_retry(lambda name=name: doc.GetVariable(name)))
            for name in ("INSUNITS", "MEASUREMENT", "LUNITS", "LUPREC")
        }

    def close_active_without_save() -> None:
        acad = _live_com_retry(lambda: win32com.client.GetActiveObject(_LIVE_COM_PROGID))
        documents = _live_com_retry(lambda: acad.Documents)
        if int(_live_com_retry(lambda: documents.Count)) > 0:
            doc = _live_com_retry(lambda: acad.ActiveDocument)
            _live_com_retry(lambda: doc.Close(False))

    try:
        async with Client(app) as client:
            fixture_doc = _tool_payload(await client.call_tool("document_new", {}))
            created_names.add(str(fixture_doc["name"]))
            await client.call_tool("layer_create", {"name": "ROAD", "color": 2})
            await client.call_tool(
                "entity_create_line",
                {"x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 0.0, "layer": "ROAD"},
            )
            saved_fixture = _tool_payload(
                await client.call_tool("document_save_as", {"path": str(fixture_path)})
            )
            assert Path(saved_fixture["path"]) == fixture_path.resolve()
            assert fixture_path.is_file()
            close_active_without_save()

            main_doc = _tool_payload(await client.call_tool("document_new", {}))
            created_names.add(str(main_doc["name"]))

            units = _tool_payload(
                await client.call_tool(
                    "document_configure_units",
                    {
                        "insertion_units": "millimeters",
                        "measurement": "metric",
                        "linear_format": "decimal",
                        "linear_precision": 3,
                    },
                )
            )
            expected_units = {"INSUNITS": 4, "MEASUREMENT": 1, "LUNITS": 2, "LUPREC": 3}
            assert units["raw"] == expected_units
            assert raw_units() == expected_units
            before_invalid_units = raw_units()
            invalid_units = await client.call_tool(
                "document_configure_units",
                {"linear_precision": 9},
                raise_on_error=False,
            )
            assert invalid_units.is_error is True
            assert raw_units() == before_invalid_units

            await client.call_tool("layer_create", {"name": "CDT-ATOMIC", "color": 7})
            updated = _tool_payload(
                await client.call_tool(
                    "layer_update_state",
                    {
                        "name": "CDT-ATOMIC",
                        "is_on": True,
                        "is_frozen": False,
                        "is_locked": False,
                        "color": 3,
                        "linetype": "Continuous",
                        "lineweight": 25,
                    },
                )
            )
            assert updated["color"] == 3
            before_invalid_layer = dynamic_layer_snapshot("CDT-ATOMIC")
            invalid_layer = await client.call_tool(
                "layer_update_state",
                {"name": "CDT-ATOMIC", "is_locked": True, "linetype": "__MISSING__"},
                raise_on_error=False,
            )
            assert invalid_layer.is_error is True
            assert dynamic_layer_snapshot("CDT-ATOMIC") == before_invalid_layer

            await client.call_tool("layer_set_current", {"name": "CDT-ATOMIC"})
            current_refusal = await client.call_tool(
                "layer_update_state",
                {"name": "CDT-ATOMIC", "is_frozen": True},
                raise_on_error=False,
            )
            assert current_refusal.is_error is True
            assert dynamic_layer_snapshot("CDT-ATOMIC")["Freeze"] is False
            await client.call_tool("layer_set_current", {"name": "0"})

            xref = _tool_payload(
                await client.call_tool(
                    "xref_attach",
                    {"path": str(fixture_path), "name": "CDT_XREF", "overlay": True},
                )
            )
            assert xref["ok"] is True
            xref_object = _tool_payload(
                await client.call_tool("object_get", {"object_id": xref["handle"]})
            )
            assert xref_object["type"] == "INSERT"
            assert xref_object["properties"]["block_name"] == "CDT_XREF"
            acad = _live_com_retry(lambda: win32com.client.GetActiveObject(_LIVE_COM_PROGID))
            doc = _live_com_retry(lambda: acad.ActiveDocument)
            layers = _live_com_retry(lambda: doc.Layers)
            dependent_names = []
            for index in range(int(_live_com_retry(lambda: layers.Count))):
                item = _live_com_retry(lambda index=index: layers.Item(index))
                item_name = str(_live_com_retry(lambda item=item: item.Name))
                if "|ROAD" in item_name:
                    dependent_names.append(item_name)
            assert len(dependent_names) == 1
            dependent = dependent_names[0]
            before_xref_layer = dynamic_layer_snapshot(dependent)
            xref_refusal = await client.call_tool(
                "layer_update_state",
                {"name": dependent, "is_locked": True},
                raise_on_error=False,
            )
            assert xref_refusal.is_error is True
            assert dynamic_layer_snapshot(dependent) == before_xref_layer

            line = _tool_payload(
                await client.call_tool(
                    "entity_create_line",
                    {"x1": 0.0, "y1": 10.0, "x2": 25.0, "y2": 10.0},
                )
            )
            measured_line = _tool_payload(
                await client.call_tool("object_measure", {"object_id": line["id"]})
            )
            assert measured_line["type"] == "LINE"
            assert measured_line["length"] == pytest.approx(25.0)

            polyline = _tool_payload(
                await client.call_tool(
                    "entity_create_polyline",
                    {"points": [[0.0, 20.0], [10.0, 20.0], [10.0, 30.0]], "closed": False},
                )
            )
            measured_polyline = _tool_payload(
                await client.call_tool("object_measure", {"object_id": polyline["id"]})
            )
            assert measured_polyline["type"] == "LWPOLYLINE"
            assert measured_polyline["length"] == pytest.approx(20.0)

            block_source = _tool_payload(
                await client.call_tool(
                    "entity_create_line",
                    {"x1": 40.0, "y1": 0.0, "x2": 45.0, "y2": 0.0},
                )
            )
            await client.call_tool(
                "block_create",
                {"name": "CDT_INSPECT_BLOCK", "object_ids": [block_source["id"]]},
            )
            inserted = _tool_payload(
                await client.call_tool(
                    "block_insert", {"name": "CDT_INSPECT_BLOCK", "x": 50.0, "y": 10.0}
                )
            )
            measured_insert = _tool_payload(
                await client.call_tool("object_measure", {"object_id": inserted["id"]})
            )
            assert measured_insert["type"] == "INSERT"

            solid = _tool_payload(
                await client.call_tool(
                    "solid_create_primitive",
                    {
                        "kind": "box",
                        "parameters": {
                            "cx": 0.0,
                            "cy": 0.0,
                            "cz": 0.0,
                            "length": 10.0,
                            "width": 8.0,
                            "height": 6.0,
                        },
                    },
                )
            )
            inspected = _tool_payload(
                await client.call_tool("solid_inspect", {"handle": solid["handle"]})
            )
            assert inspected["volume"] == pytest.approx(480.0)
            assert inspected["verification_capabilities"]["face_topology"] is False
            assert inspected["verification_capabilities"]["edge_topology"] is False
    finally:
        try:
            acad = win32com.client.GetActiveObject(_LIVE_COM_PROGID)
            for index in range(int(acad.Documents.Count) - 1, -1, -1):
                doc = acad.Documents.Item(index)
                full_name = str(getattr(doc, "FullName", "") or "")
                if str(doc.Name) in created_names or (full_name and Path(full_name).parent == tmp_path.resolve()):
                    doc.Close(False)
        except Exception:
            pass
