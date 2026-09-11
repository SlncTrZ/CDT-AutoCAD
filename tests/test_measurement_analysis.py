"""A3.3 measurement, extents and native-intersection staging tests.
Wing: code | Topic: autocad-a3-analysis | Updated: 2026-09-09 22:36
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import replace

import pytest

from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.errors import UnsupportedCapabilityError

pytestmark = pytest.mark.asyncio


async def _inline_run(func, **_kwargs):
    return func()


class _FakeEntity:
    def __init__(self, handle, object_name, bbox, **properties):
        self.Handle = handle
        self.ObjectName = object_name
        self.Layer = "0"
        self.Color = 256
        self.Linetype = "ByLayer"
        self.Visible = True
        self._bbox = bbox
        for key, value in properties.items():
            setattr(self, key, value)

    def GetBoundingBox(self):
        return self._bbox


async def test_ezdxf_object_measure_reports_exact_core_metrics_and_bbox(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()

    line = await backend.entity_create_line(1, 2, 4, 6)
    circle = await backend.entity_create_circle(10, 10, 2)
    arc = await backend.entity_create_arc(20, 20, 3, 0, 90)
    polyline = await backend.entity_create_polyline([[0, 0], [3, 0], [3, 4]], closed=True)

    line_measure = await backend.object_measure(line.id)
    circle_measure = await backend.object_measure(circle.id)
    arc_measure = await backend.object_measure(arc.id)
    polyline_measure = await backend.object_measure(polyline.id)

    assert line_measure["length"] == pytest.approx(5.0)
    assert line_measure["bounding_box"] == {"min": [1.0, 2.0, 0.0], "max": [4.0, 6.0, 0.0]}
    assert circle_measure["radius"] == pytest.approx(2.0)
    assert circle_measure["circumference"] == pytest.approx(4.0 * math.pi)
    assert circle_measure["area"] == pytest.approx(4.0 * math.pi)
    assert arc_measure["radius"] == pytest.approx(3.0)
    assert arc_measure["length"] == pytest.approx(1.5 * math.pi)
    assert polyline_measure["length"] == pytest.approx(12.0)
    assert polyline_measure["area"] == pytest.approx(6.0)


async def test_ezdxf_drawing_extents_returns_empty_or_combined_wcs_box(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    assert await backend.drawing_extents() == {"empty": True, "bounding_box": None, "coordinate_frame": "wcs"}

    await backend.entity_create_line(-2, 3, 4, 7)
    await backend.entity_create_circle(10, -1, 2)
    extents = await backend.drawing_extents()
    assert extents["empty"] is False
    assert extents["coordinate_frame"] == "wcs"
    assert extents["bounding_box"]["min"] == pytest.approx([-2.0, -3.0, 0.0])
    assert extents["bounding_box"]["max"] == pytest.approx([12.0, 7.0, 0.0])


async def test_ezdxf_generic_intersection_refuses_instead_of_approximating(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    first = await backend.entity_create_line(0, 0, 10, 10)
    second = await backend.entity_create_line(0, 10, 10, 0)

    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        await backend.object_intersections(first.id, second.id)
    assert excinfo.value.capability == "autocad.analysis.intersections"


async def test_com_measurement_uses_typed_properties_and_bounding_box(settings, monkeypatch):
    backend = ComBackend(settings)
    line = _FakeEntity("10", "AcDbLine", ((1.0, 2.0, 0.0), (4.0, 6.0, 0.0)), Length=5.0)
    circle = _FakeEntity(
        "11",
        "AcDbCircle",
        ((8.0, 8.0, 0.0), (12.0, 12.0, 0.0)),
        Radius=2.0,
        Circumference=12.566370614359172,
        Area=12.566370614359172,
    )
    objects = {"10": line, "11": circle}
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_entity_by_id", lambda key: objects[key])

    line_measure = await backend.object_measure("10")
    circle_measure = await backend.object_measure("11")
    assert line_measure["length"] == pytest.approx(5.0)
    assert line_measure["bounding_box"]["max"] == [4.0, 6.0, 0.0]
    assert circle_measure["radius"] == pytest.approx(2.0)
    assert circle_measure["area"] == pytest.approx(12.566370614359172)

    objects["12"] = _FakeEntity("12", "AcDbLine", ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0)))
    with pytest.raises(RuntimeError, match="Length"):
        await backend.object_measure("12")


async def test_com_drawing_extents_combines_every_entity_bbox(settings, monkeypatch):
    backend = ComBackend(settings)
    entities = [
        _FakeEntity("30", "AcDbLine", ((-5.0, 2.0, 0.0), (1.0, 8.0, 0.0)), Length=8.0),
        _FakeEntity("31", "AcDbCircle", ((8.0, -4.0, 0.0), (12.0, 0.0, 0.0)), Radius=2.0),
    ]

    class Space:
        Count = len(entities)

        @staticmethod
        def Item(index):
            return entities[index]

    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_space", lambda: Space())

    assert await backend.drawing_extents() == {
        "empty": False,
        "bounding_box": {"min": [-5.0, -4.0, 0.0], "max": [12.0, 8.0, 0.0]},
        "coordinate_frame": "wcs",
    }


async def test_com_intersections_map_extend_modes_and_parse_xyz_triples(settings, monkeypatch):
    backend = ComBackend(settings)
    calls = []

    class First(_FakeEntity):
        def IntersectWith(self, other, extend_option):
            calls.append((other.Handle, extend_option))
            return (5.0, 5.0, 0.0, 8.0, 8.0, 0.0)

    first = First("20", "AcDbLine", ((0, 0, 0), (10, 10, 0)), Length=14.0)
    second = _FakeEntity("21", "AcDbCircle", ((3, 3, 0), (9, 9, 0)), Radius=3.0)
    objects = {"20": first, "21": second}
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_entity_by_id", lambda key: objects[key])

    result = await backend.object_intersections("20", "21", extend_mode="both")
    assert calls == [("21", 3)]
    assert result == {
        "first_id": "20",
        "second_id": "21",
        "extend_mode": "both",
        "coordinate_frame": "wcs",
        "points": [[5.0, 5.0, 0.0], [8.0, 8.0, 0.0]],
        "count": 2,
    }

    with pytest.raises(ValueError, match="extend_mode"):
        await backend.object_intersections("20", "21", extend_mode="forever")
    with pytest.raises(ValueError, match="distinct"):
        await backend.object_intersections("20", "20")

    first.IntersectWith = lambda other, extend_option: (1.0, 2.0)
    with pytest.raises(RuntimeError, match="non-XYZ"):
        await backend.object_intersections("20", "21")

    first.IntersectWith = lambda other, extend_option: ("bad", 2.0, 0.0)
    with pytest.raises(RuntimeError, match="non-numeric"):
        await backend.object_intersections("20", "21")


async def test_analysis_capabilities_are_promoted_without_overclaiming_solver_parity(settings):
    ezdxf = EzdxfBackend(settings).capabilities()
    com = ComBackend(settings).capabilities()
    assert ezdxf["autocad.analysis.measurement"] == {
        "supported": True, "mode": "ezdxf_exact_core", "reason": None
    }
    assert ezdxf["autocad.analysis.intersections"]["supported"] is False
    assert ezdxf["autocad.analysis.intersections"]["reason"] == "generic_intersection_solver_not_implemented"
    if sys.platform == "win32":
        assert com["autocad.analysis.measurement"]["supported"] is True
        assert com["autocad.analysis.intersections"]["supported"] is True
    else:
        assert com["autocad.analysis.measurement"]["reason"] == "windows_required"
        assert com["autocad.analysis.intersections"]["reason"] == "windows_required"


_LIVE_COM_ENABLED = sys.platform == "win32" and os.environ.get("CDT_AUTOCAD_LIVE_TEST") == "1"
_LIVE_COM_PROGID = os.environ.get("CDT_AUTOCAD_COM_PROGID", "AutoCAD.Application").strip() or "AutoCAD.Application"


@pytest.mark.skipif(
    not _LIVE_COM_ENABLED,
    reason="requires Windows + real AutoCAD + CDT_AUTOCAD_LIVE_TEST=1",
)
async def test_live_autocad_a3_3_measurement_extents_and_intersections(settings):
    backend = ComBackend(
        replace(
            settings,
            backend="com",
            com_attach_policy="attach_only",
            com_progid=_LIVE_COM_PROGID,
            com_call_timeout_seconds=60.0,
        )
    )
    created_name = None
    try:
        created = await backend.document_new()
        created_name = created["name"]
        first = await backend.entity_create_line(0, 0, 20, 20)
        second = await backend.entity_create_line(0, 20, 20, 0)
        circle = await backend.entity_create_circle(40, 10, 5)

        line_measure = await backend.object_measure(first.id)
        circle_measure = await backend.object_measure(circle.id)
        assert line_measure["length"] == pytest.approx(math.sqrt(800.0))
        assert circle_measure["radius"] == pytest.approx(5.0)
        assert circle_measure["area"] == pytest.approx(25.0 * math.pi)

        extents = await backend.drawing_extents()
        assert extents["empty"] is False
        assert extents["coordinate_frame"] == "wcs"
        assert extents["bounding_box"]["min"] == pytest.approx([0.0, 0.0, 0.0])
        assert extents["bounding_box"]["max"] == pytest.approx([45.0, 20.0, 0.0])

        intersections = await backend.object_intersections(first.id, second.id)
        assert intersections["count"] == 1
        assert intersections["points"][0] == pytest.approx([10.0, 10.0, 0.0])
    finally:
        if created_name and backend._executor is not None:
            try:
                await backend._run(lambda: backend._app().ActiveDocument.Close(False))
            except Exception:
                pass
        backend.shutdown()
