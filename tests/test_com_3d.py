"""A3 COM 3D-solid staged regression tests.
Wing: code | Topic: autocad-a3 | Updated: 2026-09-09 22:36
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.errors import UnsupportedCapabilityError


class _FakeRegion:
    ObjectName = "AcDbRegion"

    def __init__(self):
        self.deleted = False

    def Delete(self):
        self.deleted = True


class _FakeSolid:
    ObjectName = "AcDb3dSolid"

    def __init__(self, handle: str, volume: float = 100.0):
        self.Handle = handle
        self.Layer = "0"
        self.Visible = True
        self.Volume = volume
        self.Centroid = (1.0, 2.0, 3.0)
        self.SolidType = "Box"
        self.moves = []
        self.rotations = []
        self.scales = []
        self.mirrors = []
        self.boolean_calls = []

    def GetBoundingBox(self):
        return ((-1.0, -2.0, -3.0), (4.0, 5.0, 6.0))

    def Move(self, origin, target):
        self.moves.append((origin, target))

    def Rotate3D(self, p1, p2, angle):
        self.rotations.append((p1, p2, angle))

    def ScaleEntity(self, base, factor):
        self.scales.append((base, factor))

    def Mirror3D(self, p1, p2, p3):
        self.mirrors.append((p1, p2, p3))
        return _FakeSolid(f"M{self.Handle}", volume=self.Volume)

    def Boolean(self, operation, tool):
        self.boolean_calls.append((operation, tool.Handle))


class _FakeProfile:
    ObjectName = "AcDbPolyline"

    def __init__(self, handle: str):
        self.Handle = handle
        self.deleted = False

    def Copy(self):
        return _FakeProfile(f"{self.Handle}-COPY")

    def Delete(self):
        self.deleted = True


class _FakePath:
    ObjectName = "AcDb3dPolyline"

    def __init__(self, handle: str, coordinates=None):
        self.Handle = handle
        self.Coordinates = tuple(coordinates or ())
        self.Closed = False


class _FakeModelSpace:
    def __init__(self):
        self.calls = []
        self._next = 0x100
        self.last_region = None
        self.last_regions = []
        self.region_count = 1
        self.fail_extrude = False

    def _solid(self, kind, *args):
        self.calls.append((kind, *args))
        solid = _FakeSolid(f"{self._next:X}")
        self._next += 1
        return solid

    def AddBox(self, center, length, width, height):
        return self._solid("box", center, length, width, height)

    def AddCylinder(self, center, radius, height):
        return self._solid("cylinder", center, radius, height)

    def AddSphere(self, center, radius):
        return self._solid("sphere", center, radius)

    def AddCone(self, center, radius, height):
        return self._solid("cone", center, radius, height)

    def AddTorus(self, center, torus_radius, tube_radius):
        return self._solid("torus", center, torus_radius, tube_radius)

    def AddWedge(self, center, length, width, height):
        return self._solid("wedge", center, length, width, height)

    def Add3DPoly(self, points):
        self.calls.append(("3dpolyline", tuple(points)))
        path = _FakePath(f"{self._next:X}", points)
        self._next += 1
        return path

    def AddRegion(self, profiles):
        self.calls.append(("region", tuple(profiles)))
        for profile in profiles:
            profile.Delete()
        self.last_regions = [_FakeRegion() for _ in range(self.region_count)]
        self.last_region = self.last_regions[0] if self.last_regions else None
        return self.last_regions

    def AddExtrudedSolid(self, region, height, taper):
        self.calls.append(("extrude", region, height, taper))
        if self.fail_extrude:
            raise RuntimeError("extrude failed")
        return self._solid("extruded", height, taper)

    def AddExtrudedSolidAlongPath(self, region, path):
        self.calls.append(("sweep", region, path.Handle))
        return self._solid("swept", path.Handle)

    def AddRevolvedSolid(self, region, axis_point, axis_dir, angle):
        self.calls.append(("revolve", region, axis_point, axis_dir, angle))
        return self._solid("revolved", axis_point, axis_dir, angle)


class _FakeDoc:
    def __init__(self):
        self.ModelSpace = _FakeModelSpace()
        self.objects = {
            "P1": _FakeProfile("P1"),
            "PATH": _FakePath("PATH"),
            "S1": _FakeSolid("S1", volume=125.0),
            "S2": _FakeSolid("S2", volume=25.0),
        }
        self.ActiveViewport = SimpleNamespace(Direction=(0.0, 0.0, 1.0))

    def HandleToObject(self, handle):
        key = str(handle).upper()
        if key not in self.objects:
            raise KeyError(handle)
        return self.objects[key]


async def _inline_run(func):
    return func()


def _backend(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    doc = _FakeDoc()
    app = SimpleNamespace(ZoomExtents=lambda: None)
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(backend, "_app", lambda: app)
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))
    monkeypatch.setattr(cb, "_double_array", lambda values: tuple(float(value) for value in values))
    monkeypatch.setattr(cb, "_dispatch_array", lambda values: list(values))
    return backend, doc


@pytest.mark.asyncio
async def test_headless_backend_refuses_acis_solid_operations(settings):
    backend = EzdxfBackend(settings)
    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        await backend.solid_box(0, 0, 0, 10, 20, 30)
    assert exc_info.value.capability == "autocad.solid.acis"


def test_solid_info_treats_solid_type_as_optional_diagnostic():
    class LiveLikeSolid:
        Handle = "LIVE1"
        Layer = "0"
        Visible = True
        Volume = 125.0
        Centroid = (1.0, 2.0, 3.0)

        @property
        def SolidType(self):
            raise RuntimeError("AutoCAD 2027 SolidType getter failed")

        @staticmethod
        def GetBoundingBox():
            return ((-1.0, -2.0, -3.0), (4.0, 5.0, 6.0))

    info = ComBackend._solid_info(LiveLikeSolid())
    assert info["type"] == "3DSOLID"
    assert info["solid_type"] is None
    assert info["volume"] == pytest.approx(125.0)


@pytest.mark.asyncio
async def test_primitive_solid_creation_validates_and_returns_inspection(settings, monkeypatch):
    backend, doc = _backend(settings, monkeypatch)

    box = await backend.solid_box(1, 2, 3, 10, 20, 30)
    cylinder = await backend.solid_cylinder(0, 0, 0, 4, 12)
    sphere = await backend.solid_sphere(0, 0, 0, 5)
    cone = await backend.solid_cone(0, 0, 0, 6, 9)
    torus = await backend.solid_torus(0, 0, 0, 12, 3)
    wedge = await backend.solid_wedge(0, 0, 0, 10, 20, 30)

    assert box["type"] == "3DSOLID"
    assert box["bounding_box"] == {"min": [-1.0, -2.0, -3.0], "max": [4.0, 5.0, 6.0]}
    assert cylinder["handle"] != box["handle"]
    assert sphere["type"] == cone["type"] == "3DSOLID"
    assert torus["type"] == wedge["type"] == "3DSOLID"
    assert doc.ModelSpace.calls[0] == ("box", (1.0, 2.0, 3.0), 10.0, 20.0, 30.0)

    with pytest.raises(ValueError, match="length"):
        await backend.solid_box(0, 0, 0, 0, 1, 1)
    with pytest.raises(ValueError, match="radius"):
        await backend.solid_sphere(0, 0, 0, -1)
    with pytest.raises(ValueError, match="tube_radius"):
        await backend.solid_torus(0, 0, 0, 10, 0)


@pytest.mark.asyncio
async def test_3d_polyline_supporting_path_creation(settings, monkeypatch):
    backend, doc = _backend(settings, monkeypatch)

    path = await backend.entity_create_3d_polyline(
        [[0, 0, 0], [10, 5, 10], [20, 0, 20]], closed=True
    )
    assert path["type"] == "3DPOLYLINE"
    assert path["closed"] is True
    assert path["points"] == [[0.0, 0.0, 0.0], [10.0, 5.0, 10.0], [20.0, 0.0, 20.0]]
    assert doc.ModelSpace.calls[-1][0] == "3dpolyline"

    with pytest.raises(ValueError, match="at least two"):
        await backend.entity_create_3d_polyline([[0, 0, 0]])


@pytest.mark.asyncio
async def test_profile_extrude_revolve_and_sweep_cleanup_temporary_region(settings, monkeypatch):
    backend, doc = _backend(settings, monkeypatch)

    extruded = await backend.solid_extrude("P1", 20, taper_angle=2)
    assert extruded["type"] == "3DSOLID"
    assert doc.ModelSpace.last_region.deleted is True
    assert doc.objects["P1"].deleted is False, "AddRegion must only consume a temporary profile copy"

    revolved = await backend.solid_revolve(
        "P1", 0, 0, 0, 0, 10, 0, angle_deg=180
    )
    assert revolved["type"] == "3DSOLID"
    assert doc.ModelSpace.last_region.deleted is True

    swept = await backend.solid_sweep("P1", "PATH")
    assert swept["type"] == "3DSOLID"
    assert doc.ModelSpace.last_region.deleted is True

    doc.objects["BADPATH"] = SimpleNamespace(Handle="BADPATH", ObjectName="AcDbLine")
    with pytest.raises(ValueError, match="Arc, Circle, Ellipse, Polyline or Spline"):
        await backend.solid_sweep("P1", "BADPATH")


@pytest.mark.asyncio
async def test_ambiguous_profile_regions_are_all_cleaned_and_refused(settings, monkeypatch):
    backend, doc = _backend(settings, monkeypatch)
    doc.ModelSpace.region_count = 2

    with pytest.raises(ValueError, match="exactly one"):
        await backend.solid_extrude("P1", 20)
    assert all(region.deleted for region in doc.ModelSpace.last_regions)
    assert doc.objects["P1"].deleted is False


@pytest.mark.asyncio
async def test_failed_extrude_still_deletes_temporary_region(settings, monkeypatch):
    backend, doc = _backend(settings, monkeypatch)
    doc.ModelSpace.fail_extrude = True

    with pytest.raises(RuntimeError, match="extrude failed"):
        await backend.solid_extrude("P1", 20)
    assert doc.ModelSpace.last_region.deleted is True


@pytest.mark.asyncio
async def test_boolean_validates_solid_types_and_operation_before_mutation(settings, monkeypatch):
    backend, doc = _backend(settings, monkeypatch)

    result = await backend.solid_boolean("S1", "S2", "subtract")
    assert result["operation"] == "subtract"
    assert doc.objects["S1"].boolean_calls == [(2, "S2")]

    with pytest.raises(ValueError, match="different"):
        await backend.solid_boolean("S1", "S1", "union")
    with pytest.raises(ValueError, match="operation"):
        await backend.solid_boolean("S1", "S2", "xor")
    with pytest.raises(ValueError, match="3DSOLID"):
        await backend.solid_boolean("P1", "S2", "union")


@pytest.mark.asyncio
async def test_solid_move_rotate3d_and_inspect(settings, monkeypatch):
    backend, doc = _backend(settings, monkeypatch)

    moved = await backend.solid_move("S1", 4, 5, 6)
    assert moved["volume"] == pytest.approx(125.0)
    assert doc.objects["S1"].moves[-1] == ((0.0, 0.0, 0.0), (4.0, 5.0, 6.0))

    rotated = await backend.solid_rotate3d("S1", 0, 0, 0, 0, 0, 1, 90)
    assert rotated["type"] == "3DSOLID"
    assert doc.objects["S1"].rotations[-1][0] == (0.0, 0.0, 0.0)
    assert doc.objects["S1"].rotations[-1][1] == (0.0, 0.0, 1.0)

    scaled = await backend.solid_scale3d("S1", 1, 2, 3, 2.0)
    assert scaled["type"] == "3DSOLID"
    assert doc.objects["S1"].scales[-1] == ((1.0, 2.0, 3.0), 2.0)

    mirrored = await backend.solid_mirror3d(
        "S1", 0, 0, 0, 0, 10, 0, 0, 10, 10
    )
    assert mirrored["type"] == "3DSOLID"
    assert mirrored["handle"] == "MS1"

    inspected = await backend.solid_inspect("S1")
    assert inspected["centroid"] == [1.0, 2.0, 3.0]
    assert inspected["volume"] == pytest.approx(125.0)

    with pytest.raises(ValueError, match="scale factor"):
        await backend.solid_scale3d("S1", 0, 0, 0, 0)
    with pytest.raises(ValueError, match="collinear"):
        await backend.solid_mirror3d("S1", 0, 0, 0, 1, 0, 0, 2, 0, 0)
    with pytest.raises(ValueError, match="distinct"):
        await backend.solid_rotate3d("S1", 0, 0, 0, 0, 0, 0, 45)


@pytest.mark.asyncio
async def test_3d_view_direction_is_normalized_and_rejects_zero_vector(settings, monkeypatch):
    backend, doc = _backend(settings, monkeypatch)

    result = await backend.view_set_direction(1, 1, 1)
    assert result["direction"] == pytest.approx([1 / 3**0.5] * 3)
    assert doc.ActiveViewport.Direction == pytest.approx((1 / 3**0.5,) * 3)

    with pytest.raises(ValueError, match="non-zero"):
        await backend.view_set_direction(0, 0, 0)


_LIVE_COM_ENABLED = sys.platform == "win32" and os.environ.get("CDT_AUTOCAD_LIVE_TEST") == "1"
_LIVE_COM_PROGID = os.environ.get("CDT_AUTOCAD_COM_PROGID", "AutoCAD.Application").strip() or "AutoCAD.Application"


@pytest.mark.skipif(
    not _LIVE_COM_ENABLED,
    reason="requires Windows + running AutoCAD + CDT_AUTOCAD_LIVE_TEST=1",
)
@pytest.mark.asyncio
async def test_live_autocad_a3_solid_smoke(settings):
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
        box = await backend.solid_box(0, 0, 0, 40, 30, 20)
        cylinder = await backend.solid_cylinder(0, 0, 0, 5, 30)
        sphere = await backend.solid_sphere(60, 0, 0, 8)
        cone = await backend.solid_cone(80, 0, 0, 8, 20)
        torus = await backend.solid_torus(105, 0, 0, 12, 3)
        wedge = await backend.solid_wedge(135, 0, 0, 20, 15, 10)
        for primitive in (box, cylinder, sphere, cone, torus, wedge):
            assert primitive["type"] == "3DSOLID"
            assert primitive["volume"] > 0

        result = await backend.solid_boolean(box["handle"], cylinder["handle"], "subtract")
        assert result["volume"] > 0
        await backend.solid_move(box["handle"], 5, 0, 0)
        await backend.solid_rotate3d(box["handle"], 0, 0, 0, 0, 0, 1, 30)
        await backend.solid_scale3d(box["handle"], 0, 0, 0, 1.1)
        mirrored = await backend.solid_mirror3d(
            box["handle"], 0, 0, 0, 0, 10, 0, 0, 10, 10
        )
        assert mirrored["type"] == "3DSOLID"

        extrude_profile = await backend.entity_create_polyline(
            [[0, 50], [20, 50], [20, 70], [0, 70]], closed=True
        )
        extruded = await backend.solid_extrude(extrude_profile.id, 15)
        assert extruded["volume"] > 0

        revolve_profile = await backend.entity_create_polyline(
            [[5, 90], [12, 90], [12, 105], [5, 105]], closed=True
        )
        revolved = await backend.solid_revolve(
            revolve_profile.id, 0, 90, 0, 0, 105, 0, angle_deg=360
        )
        assert revolved["volume"] > 0

        sweep_profile = await backend.entity_create_circle(0, 130, 3)
        sweep_path = await backend.entity_create_3d_polyline(
            [[0, 130, 0], [0, 130, 15], [10, 130, 30]], closed=False
        )
        swept = await backend.solid_sweep(sweep_profile.id, sweep_path["handle"])
        assert swept["volume"] > 0

        inspected = await backend.solid_inspect(box["handle"])
        assert inspected["type"] == "3DSOLID"
        assert inspected["volume"] > 0
        assert (await backend.view_set_direction(1, -1, 1))["ok"] is True
        assert (await backend.view_screenshot()).startswith(b"\x89PNG\r\n\x1a\n")
    finally:
        if created_name and backend._executor is not None:
            try:
                await backend._run(
                    lambda: backend._app().Documents.Item(created_name).Close(False)
                )
            except Exception:
                pass
        backend.shutdown()
