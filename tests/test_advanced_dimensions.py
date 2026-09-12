"""A3.2 advanced-dimension backend parity and fail-closed validation tests.
Wing: code | Topic: autocad-a3-dimensions | Updated: 2026-09-09 22:36
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace

import pytest

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.backends.ezdxf_backend import EzdxfBackend

pytestmark = pytest.mark.asyncio


async def _inline_run(func):
    return func()


class _FakeDimension:
    _next_handle = 1

    def __init__(self, object_name: str):
        self.ObjectName = object_name
        self.Handle = f"D{_FakeDimension._next_handle:X}"
        _FakeDimension._next_handle += 1
        self.Layer = "0"
        self.Color = 256
        self.Linetype = "ByLayer"
        self.Visible = True
        self.TextOverride = ""


class _FakeDimensionSpace:
    def __init__(self):
        self.calls: list[tuple] = []

    def AddDimAngular(self, vertex, first, second, text):
        self.calls.append(("angular", vertex, first, second, text))
        return _FakeDimension("AcDb3PointAngularDimension")

    def AddDimRadial(self, center, chord, leader_length):
        self.calls.append(("radial", center, chord, leader_length))
        return _FakeDimension("AcDbRadialDimension")

    def AddDimDiametric(self, chord, far_chord, leader_length):
        self.calls.append(("diametric", chord, far_chord, leader_length))
        return _FakeDimension("AcDbDiametricDimension")

    def AddDimOrdinate(self, definition, leader_end, use_x_axis):
        self.calls.append(("ordinate", definition, leader_end, use_x_axis))
        return _FakeDimension("AcDbOrdinateDimension")


async def test_ezdxf_advanced_dimensions_create_real_dimension_entities(settings, tmp_path):
    backend = EzdxfBackend(settings)
    await backend.document_new()

    angular = await backend.dimension_angular(0, 0, 10, 0, 0, 10, 7, 7)
    radial = await backend.dimension_radial(30, 0, 38, 0, 5)
    diametric = await backend.dimension_diametric(60, 0, 68, 0, 5)
    ordinate_x = await backend.dimension_ordinate(90, 12, 100, 20, axis="x")
    ordinate_y = await backend.dimension_ordinate(110, 12, 120, 20, axis="y")

    assert {item.type for item in (angular, radial, diametric, ordinate_x, ordinate_y)} == {
        "DIMENSION"
    }
    assert await backend.object_count(type_filter="DIMENSION") == 5

    target = tmp_path / "advanced-dimensions.dxf"
    await backend.document_save_as(str(target))
    await backend.document_new()
    await backend.document_open(str(target))
    assert await backend.object_count(type_filter="DIMENSION") == 5


async def test_advanced_dimensions_are_promoted_with_runtime_truthfulness(settings):
    ezdxf = EzdxfBackend(settings).capabilities()["autocad.dimensions.advanced"]
    com = ComBackend(settings).capabilities()["autocad.dimensions.advanced"]

    assert ezdxf == {"supported": True, "mode": "ezdxf_native", "reason": None}
    if sys.platform == "win32":
        assert com["supported"] is True
    else:
        assert com["supported"] is False
        assert com["reason"] == "windows_required"


async def test_com_advanced_dimensions_use_typed_activex_methods(settings, monkeypatch):
    backend = ComBackend(settings)
    space = _FakeDimensionSpace()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_space", lambda: space)
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))

    angular = await backend.dimension_angular(0, 0, 10, 0, 0, 10, 7, 7)
    radial = await backend.dimension_radial(30, 0, 38, 0, 5)
    diametric = await backend.dimension_diametric(60, 0, 68, 0, 5)
    ordinate_x = await backend.dimension_ordinate(90, 12, 100, 20, axis="x")
    ordinate_y = await backend.dimension_ordinate(110, 12, 120, 20, axis="Y")

    assert {item.type for item in (angular, radial, diametric, ordinate_x, ordinate_y)} == {
        "DIMENSION"
    }
    assert space.calls == [
        ("angular", (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (0.0, 10.0, 0.0), (7.0, 7.0, 0.0)),
        ("radial", (30.0, 0.0, 0.0), (38.0, 0.0, 0.0), 5.0),
        ("diametric", (68.0, 0.0, 0.0), (52.0, 0.0, 0.0), 5.0),
        ("ordinate", (90.0, 12.0, 0.0), (100.0, 20.0, 0.0), True),
        ("ordinate", (110.0, 12.0, 0.0), (120.0, 20.0, 0.0), False),
    ]


@pytest.mark.parametrize("backend_cls", [EzdxfBackend, ComBackend])
async def test_advanced_dimension_validation_fails_before_backend_side_effects(
    settings, monkeypatch, backend_cls
):
    backend = backend_cls(settings)
    touched = False

    if isinstance(backend, ComBackend):
        async def fail_if_run(func):
            nonlocal touched
            touched = True
            return func()

        monkeypatch.setattr(backend, "_run", fail_if_run)
        monkeypatch.setattr(
            backend,
            "_space",
            lambda: (_ for _ in ()).throw(AssertionError("COM space must not be touched")),
        )
        monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))
    else:
        await backend.document_new()

    with pytest.raises(ValueError, match="distinct rays"):
        await backend.dimension_angular(0, 0, 10, 0, 20, 0, 5, 5)
    with pytest.raises(ValueError, match="chord point"):
        await backend.dimension_radial(0, 0, 0, 0, 5)
    with pytest.raises(ValueError, match="leader_length"):
        await backend.dimension_radial(0, 0, 5, 0, 0)
    with pytest.raises(ValueError, match="chord point"):
        await backend.dimension_diametric(0, 0, 0, 0, 5)
    with pytest.raises(ValueError, match="leader_length"):
        await backend.dimension_diametric(0, 0, 5, 0, -1)
    with pytest.raises(ValueError, match="axis"):
        await backend.dimension_ordinate(0, 0, 5, 5, axis="z")
    with pytest.raises(ValueError, match="leader endpoint"):
        await backend.dimension_ordinate(0, 0, 0, 0, axis="x")
    with pytest.raises(ValueError, match="finite"):
        await backend.dimension_angular(0, 0, 10, 0, 0, 10, float("nan"), 5)
    with pytest.raises(ValueError, match="finite"):
        await backend.dimension_radial(0, 0, float("nan"), 0, 5)
    with pytest.raises(ValueError, match="finite"):
        await backend.dimension_ordinate(0, 0, float("inf"), 5, axis="x")

    if isinstance(backend, ComBackend):
        assert touched is False


_LIVE_COM_ENABLED = sys.platform == "win32" and os.environ.get("CDT_AUTOCAD_LIVE_TEST") == "1"
_LIVE_COM_PROGID = os.environ.get("CDT_AUTOCAD_COM_PROGID", "AutoCAD.Application").strip() or "AutoCAD.Application"


@pytest.mark.skipif(
    not _LIVE_COM_ENABLED,
    reason="requires Windows + real AutoCAD + CDT_AUTOCAD_LIVE_TEST=1",
)
async def test_live_autocad_a3_2_advanced_dimensions(settings):
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
        angular = await backend.dimension_angular(0, 0, 20, 0, 0, 20, 12, 12)
        radial = await backend.dimension_radial(40, 0, 50, 0, 6)
        diametric = await backend.dimension_diametric(70, 0, 80, 0, 6)
        ordinate_x = await backend.dimension_ordinate(100, 15, 115, 25, axis="x")
        ordinate_y = await backend.dimension_ordinate(125, 15, 140, 25, axis="y")
        assert {item.type for item in (angular, radial, diametric, ordinate_x, ordinate_y)} == {
            "DIMENSION"
        }
        assert await backend.object_count(type_filter="DIMENSION") >= 5
    finally:
        if created_name and backend._executor is not None:
            try:
                await backend._run(lambda: backend._app().ActiveDocument.Close(False))
            except Exception:
                pass
        backend.shutdown()
