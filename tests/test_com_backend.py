"""A2 COM backend regression and live-lane tests.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 23:06
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.errors import BackendTimeoutError, StateConflictError
from cdt_autocad.security import resolve_autocad_document_path

_LIVE_COM_ENABLED = sys.platform == "win32" and os.environ.get("CDT_AUTOCAD_LIVE_TEST") == "1"
_LIVE_COM_PROGID = os.environ.get("CDT_AUTOCAD_COM_PROGID", "AutoCAD.Application").strip() or "AutoCAD.Application"


def test_autocad_version_mapping_recognizes_official_release_series():
    assert cb._autocad_release_info("26.0s (LMS Tech)") == ("26.0", "2027")
    assert cb._autocad_release_info("25.1") == ("25.1", "2026")
    assert cb._autocad_release_info("25.0.58.0") == ("25.0", "2025")
    assert cb._autocad_release_info("99.9") == ("99.9", None)
    assert cb._autocad_release_info("") == (None, None)


def test_application_metadata_is_best_effort_when_optional_com_property_fails():
    class PartialApp:
        Name = "AutoCAD"
        Version = "26.0"
        FullName = r"C:\AutoCAD 2027\acad.exe"

        @property
        def Caption(self):
            raise RuntimeError("caption unavailable")

    metadata = cb._inspect_application(PartialApp())
    assert metadata["release"] == "2027"
    assert metadata["caption"] is None
    assert metadata["primary_target_match"] is True


def test_com_property_busy_retry_is_bounded_and_only_for_transient_hresult(monkeypatch):
    class BusyThenReady:
        def __init__(self):
            self.calls = 0

        @property
        def Name(self):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "Call was rejected by callee")
            return "Drawing1.dwg"

    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    obj = BusyThenReady()
    assert cb._com_property_with_busy_retry(obj, "Name", attempts=3, delay_seconds=0.01) == "Drawing1.dwg"
    assert obj.calls == 3

    class PermanentFailure:
        calls = 0

        @property
        def Name(self):
            type(self).calls += 1
            raise RuntimeError(-2147467259, "unspecified failure")

    with pytest.raises(RuntimeError, match="unspecified failure"):
        cb._com_property_with_busy_retry(PermanentFailure(), "Name", attempts=5, delay_seconds=0.01)
    assert PermanentFailure.calls == 1


def test_com_call_busy_retry_retries_only_the_rejected_call(monkeypatch):
    calls = 0

    def add_document():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError(cb._RPC_E_SERVERCALL_RETRYLATER, "server busy")
        return "doc"

    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    assert cb._com_call_with_busy_retry(add_document, attempts=3, delay_seconds=0.01) == "doc"
    assert calls == 3


def test_object_type_fails_closed_when_object_name_cannot_be_read(monkeypatch):
    class UnreadableType:
        @property
        def ObjectName(self):
            raise RuntimeError(-2147467259, "object type unavailable")

    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    with pytest.raises(RuntimeError, match="object type unavailable"):
        cb._object_type(UnreadableType())


def test_generated_wrapper_property_names_are_resolved_case_insensitively():
    class GeneratedLike:
        _prop_map_get_ = {"color": object(), "Layer": object()}
        _prop_map_put_ = {"color": object(), "Layer": object()}

        def __init__(self):
            object.__setattr__(self, "color", 256)
            object.__setattr__(self, "Layer", "0")

        def __setattr__(self, name, value):
            if name not in self._prop_map_put_:
                raise AttributeError(name)
            object.__setattr__(self, name, value)

    entity = GeneratedLike()
    assert cb._com_get_attr(entity, "Color") == 256
    cb._com_set_attr(entity, "Color", 3)
    assert entity.color == 3
    assert cb._com_get_attr(entity, "Layer") == "0"


def test_doc_retries_transient_busy_properties(settings, monkeypatch):
    doc = SimpleNamespace(Name="busy.dwg", FullName="C:/work/busy.dwg")

    class Docs:
        def __init__(self):
            self.calls = 0

        @property
        def Count(self):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy docs")
            return 1

    docs = Docs()

    class App:
        def __init__(self):
            self.documents_calls = 0
            self.active_calls = 0

        @property
        def Documents(self):
            self.documents_calls += 1
            if self.documents_calls < 2:
                raise RuntimeError(cb._RPC_E_SERVERCALL_RETRYLATER, "busy app")
            return docs

        @property
        def ActiveDocument(self):
            self.active_calls += 1
            if self.active_calls < 2:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy active document")
            return doc

    app = App()
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_app", lambda: app)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)

    assert backend._doc() is doc
    assert app.documents_calls == 2
    assert docs.calls == 3
    assert app.active_calls == 2


def test_collection_name_reads_retry_transient_busy_calls(monkeypatch):
    class Item:
        def __init__(self, name):
            self.name_calls = 0
            self.value = name

        @property
        def Name(self):
            self.name_calls += 1
            if self.name_calls == 1:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy item name")
            return self.value

    class Collection:
        def __init__(self):
            self.count_calls = 0
            self.item_calls = 0
            self.items = [Item("0"), Item("DIM")]

        @property
        def Count(self):
            self.count_calls += 1
            if self.count_calls == 1:
                raise RuntimeError(cb._RPC_E_SERVERCALL_RETRYLATER, "busy count")
            return len(self.items)

        def Item(self, index):
            self.item_calls += 1
            if self.item_calls == 1:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy item")
            return self.items[index]

    collection = Collection()
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    assert ComBackend._collection_names(collection) == ["0", "DIM"]
    assert collection.count_calls == 2
    assert collection.item_calls == 3


def test_generated_base_entity_can_be_rewrapped_for_subtype_properties(monkeypatch):
    ole = object()
    generic = SimpleNamespace(_oleobj_=ole, ObjectName="AcDbLine")
    dynamic = SimpleNamespace(
        _oleobj_=ole,
        ObjectName="AcDbLine",
        StartPoint=(0.0, 0.0, 0.0),
        EndPoint=(1.0, 1.0, 0.0),
    )
    calls = []

    class FakeDynamic:
        @staticmethod
        def DumbDispatch(value):
            calls.append(value)
            return dynamic

    fake_client = SimpleNamespace(dynamic=FakeDynamic)
    monkeypatch.setattr(cb, "_COM_IMPORTS_OK", True)
    monkeypatch.setattr(cb, "win32com", SimpleNamespace(client=fake_client), raising=False)

    assert cb._entity_property_view(generic) is dynamic
    assert calls == [ole]


def test_inspection_reads_typed_dispatch_case_insensitively_across_families():
    class TypedLike:
        def __init__(self, properties):
            self._prop_map_get_ = {name.lower(): object() for name in properties}
            for name, value in properties.items():
                object.__setattr__(self, name.lower(), value)

    common = {
        "Handle": "A1",
        "Layer": "0",
        "Color": 256,
        "Linetype": "ByLayer",
        "Visible": True,
    }
    cases = [
        (
            "line",
            TypedLike({**common, "ObjectName": "AcDbLine", "StartPoint": (0, 0, 0), "EndPoint": (5, 0, 0)}),
            "LINE",
        ),
        (
            "lwpolyline",
            TypedLike({**common, "ObjectName": "AcDbPolyline", "Coordinates": (0, 0, 5, 0), "Closed": False}),
            "LWPOLYLINE",
        ),
        (
            "insert",
            TypedLike({**common, "ObjectName": "AcDbBlockReference", "Name": "LOCAL_BLOCK", "InsertionPoint": (1, 2, 0), "XScaleFactor": 1.0, "YScaleFactor": 1.0, "Rotation": 0.0}),
            "INSERT",
        ),
        (
            "xref",
            TypedLike({**common, "ObjectName": "AcDbBlockReference", "Name": "SITE_XREF", "InsertionPoint": (3, 4, 0), "XScaleFactor": 1.0, "YScaleFactor": 1.0, "Rotation": 0.0}),
            "INSERT",
        ),
    ]

    for label, entity, expected_type in cases:
        info = cb._entity_info(entity)
        assert info.type == expected_type, label
        assert info.id == "A1", label
        if label in {"insert", "xref"}:
            assert info.properties["block_name"] in {"LOCAL_BLOCK", "SITE_XREF"}


def test_inspection_rewraps_generated_base_dispatch_across_families(monkeypatch):
    dynamics = {}
    cases = [
        ("line", "AcDbLine", {"StartPoint": (0, 0, 0), "EndPoint": (1, 0, 0)}),
        ("lwpolyline", "AcDbPolyline", {"Coordinates": (0, 0, 1, 0), "Closed": False}),
        ("insert", "AcDbBlockReference", {"Name": "LOCAL", "InsertionPoint": (0, 0, 0), "XScaleFactor": 1.0, "YScaleFactor": 1.0, "Rotation": 0.0}),
        ("xref", "AcDbBlockReference", {"Name": "REF", "InsertionPoint": (0, 0, 0), "XScaleFactor": 1.0, "YScaleFactor": 1.0, "Rotation": 0.0}),
    ]
    generics = []
    for index, (label, object_name, extra) in enumerate(cases):
        ole = object()
        generics.append((label, SimpleNamespace(_oleobj_=ole)))
        dynamics[ole] = SimpleNamespace(
            _oleobj_=ole,
            ObjectName=object_name,
            Handle=f"D{index}",
            Layer="0",
            Color=256,
            Linetype="ByLayer",
            Visible=True,
            **extra,
        )

    class FakeDynamic:
        @staticmethod
        def DumbDispatch(value):
            return dynamics[value]

    monkeypatch.setattr(cb, "_COM_IMPORTS_OK", True)
    monkeypatch.setattr(
        cb,
        "win32com",
        SimpleNamespace(client=SimpleNamespace(dynamic=FakeDynamic)),
        raising=False,
    )

    for label, generic in generics:
        info = cb._entity_info(generic)
        assert info.type in {"LINE", "LWPOLYLINE", "INSERT"}, label


def test_solid_inspection_supports_typed_and_dumb_dispatch_views(monkeypatch):
    class TypedSolid:
        _prop_map_get_ = {
            "handle": object(),
            "layer": object(),
            "visible": object(),
            "solidtype": object(),
            "volume": object(),
            "centroid": object(),
            "objectname": object(),
        }
        handle = "S1"
        layer = "0"
        visible = True
        solidtype = "Box"
        volume = 125.0
        centroid = (1.0, 2.0, 3.0)
        objectname = "AcDb3dSolid"

        @staticmethod
        def GetBoundingBox():
            return ((-1.0, -2.0, -3.0), (4.0, 5.0, 6.0))

    typed = ComBackend._solid_info(TypedSolid())
    assert typed["handle"] == "S1"
    assert typed["volume"] == pytest.approx(125.0)

    ole = object()
    dynamic = SimpleNamespace(
        _oleobj_=ole,
        ObjectName="AcDb3dSolid",
        Handle="S2",
        Layer="0",
        Visible=True,
        SolidType="Box",
        Volume=64.0,
        Centroid=(0.0, 0.0, 0.0),
        GetBoundingBox=lambda: ((0.0, 0.0, 0.0), (4.0, 4.0, 4.0)),
    )

    class FakeDynamic:
        @staticmethod
        def DumbDispatch(value):
            assert value is ole
            return dynamic

    monkeypatch.setattr(cb, "_COM_IMPORTS_OK", True)
    monkeypatch.setattr(
        cb,
        "win32com",
        SimpleNamespace(client=SimpleNamespace(dynamic=FakeDynamic)),
        raising=False,
    )
    inspected = ComBackend._solid_info(SimpleNamespace(_oleobj_=ole))
    assert inspected["handle"] == "S2"
    assert inspected["bounding_box"]["max"] == [4.0, 4.0, 4.0]


def test_com_inspection_property_helpers_fail_closed_on_missing_or_error():
    class Missing:
        _prop_map_get_ = {}

    class Broken:
        _prop_map_get_ = {"length": object()}

        @property
        def length(self):
            raise RuntimeError("getter failed")

    with pytest.raises(RuntimeError, match="AutoCAD property unavailable: Length"):
        cb._com_float_property(Missing(), "Length")
    with pytest.raises(RuntimeError, match="AutoCAD property unavailable: Length"):
        cb._com_float_property(Broken(), "Length")


def test_com_bounding_box_supports_byref_calling_convention(monkeypatch):
    class Variant:
        def __init__(self, *_args):
            self.value = None

    class ByRefBox:
        def GetBoundingBox(self, *args):
            if not args:
                raise TypeError("two output args required")
            args[0].value = (-2.0, -1.0, 0.0)
            args[1].value = (2.0, 3.0, 4.0)

    fake_pythoncom = SimpleNamespace(VT_BYREF=1, VT_VARIANT=2)
    fake_client = SimpleNamespace(VARIANT=Variant)
    monkeypatch.setattr(cb, "_COM_IMPORTS_OK", True)
    monkeypatch.setattr(cb, "pythoncom", fake_pythoncom, raising=False)
    monkeypatch.setattr(cb, "win32com", SimpleNamespace(client=fake_client), raising=False)

    assert cb._com_bounding_box(ByRefBox()) == {
        "min": [-2.0, -1.0, 0.0],
        "max": [2.0, 3.0, 4.0],
    }


@pytest.mark.asyncio
async def test_object_measure_rewraps_generated_dispatch_before_inspection(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    ole = object()
    generic = SimpleNamespace(_oleobj_=ole)
    dynamic = SimpleNamespace(
        _oleobj_=ole,
        ObjectName="AcDbLine",
        Handle="M1",
        Length=5.0,
        GetBoundingBox=lambda: ((0.0, 0.0, 0.0), (5.0, 0.0, 0.0)),
    )

    class FakeDynamic:
        @staticmethod
        def DumbDispatch(value):
            assert value is ole
            return dynamic

    monkeypatch.setattr(cb, "_COM_IMPORTS_OK", True)
    monkeypatch.setattr(
        cb,
        "win32com",
        SimpleNamespace(client=SimpleNamespace(dynamic=FakeDynamic)),
        raising=False,
    )
    monkeypatch.setattr(backend, "_entity_by_id", lambda _object_id: generic)
    monkeypatch.setattr(backend, "_run", _inline_run)

    measured = await backend.object_measure("M1")
    assert measured["type"] == "LINE"
    assert measured["length"] == pytest.approx(5.0)
    assert measured["bounding_box"]["max"] == [5.0, 0.0, 0.0]


def test_primary_type_library_bootstrap_uses_common_program_files(monkeypatch, tmp_path):
    tlb = tmp_path / "Autodesk Shared" / "acax26enu.tlb"
    tlb.parent.mkdir(parents=True)
    tlb.write_bytes(b"fake")
    calls = []

    class FakeTypeLib:
        @staticmethod
        def GetLibAttr():
            return ("{GUID}", 0, 3, 1, 0, 8)

    class FakePythonCom:
        @staticmethod
        def LoadTypeLib(path):
            calls.append(("load", path))
            return FakeTypeLib()

    class FakeGenCache:
        @staticmethod
        def EnsureModule(guid, lcid, major, minor):
            calls.append(("ensure", guid, lcid, major, minor))
            return object()

    monkeypatch.setattr(cb, "_COM_IMPORTS_OK", True)
    monkeypatch.setattr(cb, "pythoncom", FakePythonCom, raising=False)
    monkeypatch.setattr(
        cb,
        "win32com",
        SimpleNamespace(client=SimpleNamespace(gencache=FakeGenCache)),
        raising=False,
    )
    monkeypatch.setenv("CommonProgramFiles", str(tmp_path))

    assert cb._ensure_autocad_type_library() is True
    assert calls == [
        ("load", str(tlb)),
        ("ensure", "{GUID}", 0, 1, 0),
    ]


def test_status_declares_primary_2027_certification_target_before_connection(settings):
    backend = ComBackend(replace(settings, backend="com"))
    status = backend.status()

    assert status["application"] is None
    assert status["a2_live_verification"] == (
        "historical_primary_target_pass_current_process_unverified"
    )
    assert status["live_certification"] == {
        "state": "historical_primary_target_pass",
        "current_process_certified": False,
        "current_release_match": None,
        "evidence_reference": "docs/LIVE_ACCEPTANCE.md",
        "primary_release": "2027",
        "primary_com_version": "26.0",
        "primary_progid": "AutoCAD.Application.26",
        "required_edition": "full",
        "required_platform": "windows_x64",
        "compatibility_policy": "explicit_native_matrix_required",
    }


def test_matching_autocad_release_does_not_self_certify_current_process(settings):
    backend = ComBackend(replace(settings, backend="com"))
    backend._application_metadata = {
        "release": "2027",
        "version": "26.0",
        "com_version": "26.0",
    }

    status = backend.status()

    assert status["live_certification"]["current_release_match"] is True
    assert status["live_certification"]["current_process_certified"] is False
    assert status["live_certification"]["state"] == "historical_primary_target_pass"


def test_direct_settings_construction_rejects_unsafe_com_policy(settings):
    with pytest.raises(ValueError, match="com_attach_policy"):
        replace(settings, backend="com", com_attach_policy="typo_means_start")


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows capability honesty test")
def test_com_capabilities_fail_closed_when_windows_runtime_is_unavailable(settings):
    backend = ComBackend(replace(settings, backend="com"))
    capabilities = backend.capabilities()

    assert backend.runtime_available is False
    assert capabilities["autocad.dwg.read"]["supported"] is False
    assert capabilities["autocad.dwg.write"]["reason"] == "windows_required"
    assert capabilities["autocad.live_ui"]["supported"] is False
    assert backend.status()["connected"] is False


def test_live_document_path_accepts_dwg_without_weakening_allowed_roots(settings, tmp_path: Path):
    dwg = tmp_path / "drawing.dwg"
    resolved = resolve_autocad_document_path(
        str(dwg), settings, must_exist=False, for_write=True
    )
    assert resolved == dwg.resolve()

    outside = tmp_path.parent / "outside.dwg"
    with pytest.raises(ValueError, match="outside"):
        resolve_autocad_document_path(
            str(outside), settings, must_exist=False, for_write=True
        )


def _fake_com_runtime(monkeypatch, *, active_error: Exception | None, dispatched: object | None):
    calls: list[tuple[str, str]] = []

    class FakeClient:
        @staticmethod
        def GetActiveObject(progid: str):
            calls.append(("attach", progid))
            if active_error is not None:
                raise active_error
            return dispatched

        @staticmethod
        def Dispatch(progid: str):
            calls.append(("dispatch", progid))
            if dispatched is None:
                raise RuntimeError("dispatch failed")
            return dispatched

    monkeypatch.setattr(cb, "_WIN32", True)
    monkeypatch.setattr(cb, "_COM_IMPORTS_OK", True)
    monkeypatch.setattr(cb, "win32com", SimpleNamespace(client=FakeClient), raising=False)
    return calls


def test_attach_only_policy_never_starts_autocad(settings, monkeypatch):
    calls = _fake_com_runtime(
        monkeypatch,
        active_error=RuntimeError("not running"),
        dispatched=SimpleNamespace(Visible=False),
    )
    backend = ComBackend(
        replace(
            settings,
            backend="com",
            com_attach_policy="attach_only",
            com_progid="AutoCAD.Application.25",
        )
    )
    try:
        with pytest.raises(RuntimeError, match="forbids starting"):
            backend._app()
        assert calls == [("attach", "AutoCAD.Application.25")]
    finally:
        assert backend._executor is not None
        backend._executor.shutdown(wait=False)
        backend._executor = None


def test_attach_or_start_dispatches_only_after_attach_fails(settings, monkeypatch):
    app = SimpleNamespace(
        Visible=False,
        Name="AutoCAD",
        Version="26.0s (LMS Tech)",
        Caption="Autodesk AutoCAD 2027",
        FullName=r"C:\Program Files\Autodesk\AutoCAD 2027\acad.exe",
    )
    calls = _fake_com_runtime(
        monkeypatch,
        active_error=RuntimeError("not running"),
        dispatched=app,
    )
    backend = ComBackend(
        replace(
            settings,
            backend="com",
            com_attach_policy="attach_or_start",
            com_progid="AutoCAD.Application",
        )
    )
    try:
        assert backend._app() is app
        assert app.Visible is True
        assert calls == [
            ("attach", "AutoCAD.Application"),
            ("dispatch", "AutoCAD.Application"),
        ]
        status = backend.status()
        assert status["connected"] is True
        assert status["application"] == {
            "name": "AutoCAD",
            "version": "26.0s (LMS Tech)",
            "com_version": "26.0",
            "release": "2027",
            "caption": "Autodesk AutoCAD 2027",
            "full_name": r"C:\Program Files\Autodesk\AutoCAD 2027\acad.exe",
            "primary_target_match": True,
        }
    finally:
        assert backend._executor is not None
        backend._executor.shutdown(wait=False)
        backend._executor = None


@pytest.mark.asyncio
async def test_run_serializes_calls_on_one_sta_worker(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=2.0))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    active = 0
    max_active = 0
    lock = threading.Lock()

    def work(value: int) -> int:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return value

    try:
        result = await asyncio.gather(backend._run(lambda: work(1)), backend._run(lambda: work(2)))
        assert result == [1, 2]
        assert max_active == 1
    finally:
        executor.shutdown(wait=False)
        backend._executor = None


@pytest.mark.asyncio
async def test_callable_timeout_error_is_not_misclassified_as_com_deadline(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=2.0))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor

    def fails_immediately():
        raise TimeoutError("network share timeout")

    try:
        with pytest.raises(TimeoutError, match="network share"):
            await backend._run(fails_immediately)
        assert backend._executor is executor
        assert backend.status()["timeout_uncertain"] is False
    finally:
        executor.shutdown(wait=False)
        backend._executor = None


@pytest.mark.asyncio
async def test_genuine_deadline_marks_live_state_uncertain_and_warns_against_blind_retry(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=0.05))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor

    def hangs():
        time.sleep(0.15)

    with pytest.raises(BackendTimeoutError) as exc_info:
        await backend._run(hangs)

    message = str(exc_info.value).lower()
    assert "may still" in message
    assert "double-apply" in message
    assert backend.status()["timeout_uncertain"] is True
    assert backend.status()["connected"] is False


class _FakeCollection:
    def __init__(self, items):
        self.items = items

    @property
    def Count(self):
        return len(self.items)

    def Item(self, key):
        if isinstance(key, int):
            return self.items[key]
        return next(item for item in self.items if item.Name == key)


class _FakeBlock:
    def __init__(self):
        self.items = []

    @property
    def Count(self):
        return len(self.items)

    def Item(self, index):
        return self.items[index]


class _FakeLayout:
    def __init__(self, name):
        self.Name = name
        self.Block = _FakeBlock()


class _FakeViewport:
    ObjectName = "AcDbViewport"

    def __init__(self, handle, center, width, height, block):
        self.Handle = handle
        self.Center = center
        self.Width = width
        self.Height = height
        self.Target = (0.0, 0.0, 0.0)
        self.CustomScale = 1.0
        self.DisplayLocked = False
        self.ViewportOn = False
        self._block = block

    def Display(self, enabled):
        self.ViewportOn = bool(enabled)

    def Delete(self):
        self._block.items.remove(self)


class _FakeDoc:
    def __init__(self):
        self.Layouts = _FakeCollection([_FakeLayout("Model"), _FakeLayout("Layout1")])
        self.ActiveLayout = self.Layouts.Item("Model")
        self._next_handle = 100
        self.PaperSpace = SimpleNamespace(AddPViewport=self._add_viewport)

    def _add_viewport(self, center, width, height):
        block = self.ActiveLayout.Block
        viewport = _FakeViewport(
            f"{self._next_handle:X}", tuple(center), float(width), float(height), block
        )
        self._next_handle += 1
        block.items.append(viewport)
        return viewport

    def HandleToObject(self, handle):
        key = str(handle).upper()
        for layout in self.Layouts.items:
            for item in layout.Block.items:
                if str(item.Handle).upper() == key:
                    return item
        raise KeyError(handle)


async def _inline_run(func, **_kwargs):
    return func()


@pytest.mark.asyncio
async def test_document_new_retries_explicit_com_busy_rejection(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    calls = 0
    doc = SimpleNamespace(Name="Drawing1.dwg")

    class Documents:
        def Add(self):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy add")
            return doc

    app = SimpleNamespace(Documents=Documents())
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_app", lambda: app)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)

    result = await backend.document_new()
    assert result["name"] == "Drawing1.dwg"
    assert calls == 3


@pytest.mark.asyncio
async def test_document_open_does_not_retry_mutating_open_call(settings, monkeypatch, tmp_path):
    backend = ComBackend(replace(settings, backend="com"))
    calls = 0

    class Documents:
        def Open(self, _path):
            nonlocal calls
            calls += 1
            raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy open")

    app = SimpleNamespace(Documents=Documents())
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_app", lambda: app)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(cb, "resolve_autocad_document_path", lambda *_args, **_kwargs: tmp_path / "input.dwg")

    with pytest.raises(RuntimeError, match="busy open"):
        await backend.document_open(str(tmp_path / "input.dwg"))
    assert calls == 1


@pytest.mark.asyncio
async def test_document_info_reads_insunits_from_document_not_application(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    variable_calls = []

    class FakeDoc:
        Name = "live.dwg"
        FullName = ""
        Saved = True
        ActiveLayout = SimpleNamespace(Name="Model", Block=SimpleNamespace(Count=0))
        ModelSpace = SimpleNamespace(Count=0)
        Layers = SimpleNamespace(Count=1)
        Blocks = _FakeCollection([])
        Layouts = SimpleNamespace(Count=1)

        def GetVariable(self, name):
            variable_calls.append(name)
            return 4

    app = SimpleNamespace(
        Name="AutoCAD",
        Version="26.0",
        Caption="Autodesk AutoCAD 2027",
        FullName=r"C:\AutoCAD 2027\acad.exe",
    )
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: FakeDoc())
    monkeypatch.setattr(backend, "_app", lambda: app)

    info = await backend.document_info()
    assert variable_calls == ["INSUNITS"]
    assert info["units"] == "mm"
    assert info["autocad_release"] == "2027"


@pytest.mark.asyncio
async def test_document_save_as_does_not_retry_mutating_save_as_call(
    settings, monkeypatch, tmp_path
):
    backend = ComBackend(replace(settings, backend="com"))
    calls = 0

    class FakeDoc:
        Name = "safe.dwg"
        FullName = str(tmp_path / "safe.dwg")

        def SaveAs(self, _path, _file_type):
            nonlocal calls
            calls += 1
            raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy save")

    doc = FakeDoc()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="busy save"):
        await backend.document_save_as(str(tmp_path / "safe.dwg"))
    assert calls == 1


@pytest.mark.asyncio
async def test_document_save_as_retries_only_post_save_scope_reads(
    settings, monkeypatch, tmp_path
):
    backend = ComBackend(replace(settings, backend="com"))
    save_calls = 0
    full_name_reads = 0
    target = tmp_path / "safe.dwg"

    class FakeDoc:
        Name = "safe.dwg"

        @property
        def FullName(self):
            nonlocal full_name_reads
            full_name_reads += 1
            if full_name_reads < 3:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy full name")
            return str(target)

        def SaveAs(self, _path, _file_type):
            nonlocal save_calls
            save_calls += 1

    doc = FakeDoc()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)

    result = await backend.document_save_as(str(target))
    assert result["ok"] is True
    assert save_calls == 1
    assert full_name_reads == 3
    assert backend._document_scope_key == ("safe.dwg", str(target))


@pytest.mark.asyncio
async def test_native_pdf_plot_retries_busy_boundaries_and_restores_backgroundplot(
    settings, monkeypatch, tmp_path
):
    backend = ComBackend(replace(settings, backend="com"))
    calls = []
    state = {"backgroundplot": 2}
    get_attempts = 0
    set_attempts = {0: 0, 2: 0}
    plot_attempts = 0

    class FakePlot:
        def PlotToFile(self, path, config):
            nonlocal plot_attempts
            plot_attempts += 1
            if plot_attempts == 1:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy plot")
            calls.append(("plot", state["backgroundplot"], path, config))
            return True

    class FakeDoc:
        ActiveLayout = SimpleNamespace(Name="Model")
        Layouts = _FakeCollection([SimpleNamespace(Name="Model")])
        Plot = FakePlot()

        def GetVariable(self, name):
            nonlocal get_attempts
            assert name == "BACKGROUNDPLOT"
            get_attempts += 1
            if get_attempts == 1:
                raise RuntimeError(cb._RPC_E_SERVERCALL_RETRYLATER, "busy get")
            return state["backgroundplot"]

        def SetVariable(self, name, value):
            assert name == "BACKGROUNDPLOT"
            normalized = int(value)
            set_attempts[normalized] += 1
            if set_attempts[normalized] == 1:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy set")
            state["backgroundplot"] = normalized
            calls.append(("set", normalized))

    doc = FakeDoc()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)

    result = await backend.document_export_pdf(str(tmp_path / "plot.pdf"))
    assert result["ok"] is True
    assert get_attempts == 2
    assert set_attempts == {0: 2, 2: 2}
    assert plot_attempts == 2
    assert calls[0] == ("set", 0)
    assert calls[1][0:2] == ("plot", 0)
    assert calls[-1] == ("set", 2)
    assert state["backgroundplot"] == 2


@pytest.mark.asyncio
async def test_staged_viewport_roundtrip_and_safe_delete(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    doc = _FakeDoc()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))

    created = await backend.viewport_create("layout1", 100, 75, 80, 40, 10, 20, scale=0.5)
    assert created["layout"] == "Layout1"
    assert created["scale"] == pytest.approx(0.5)
    assert created["view_center"] == [10.0, 20.0]
    assert doc.ActiveLayout.Name == "Model", "viewport_create must restore the operator's tab"

    listed = await backend.viewport_list("LAYOUT1")
    assert listed["count"] == 1
    assert listed["viewports"][0]["created_by_backend"] is True
    assert listed["viewports"][0]["is_main"] is None

    scaled = await backend.viewport_set_scale(created["handle"], 0.25)
    assert scaled["scale"] == pytest.approx(0.25)
    assert scaled["view_height"] == pytest.approx(160.0)
    assert (await backend.viewport_lock(created["handle"], True))["locked"] is True

    deleted = await backend.viewport_delete(created["handle"])
    assert deleted["ok"] is True
    assert (await backend.viewport_list("Layout1"))["count"] == 0


@pytest.mark.asyncio
async def test_viewport_create_retries_busy_display_scale_and_target_writes(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    doc = _FakeDoc()
    layout = doc.Layouts.Item("Layout1")

    class BusyViewport:
        ObjectName = "AcDbViewport"

        def __init__(self):
            self.Handle = "B1"
            self.Center = (100.0, 75.0, 0.0)
            self.Width = 80.0
            self.Height = 40.0
            self.DisplayLocked = False
            self.ViewportOn = False
            self._scale = 1.0
            self._target = (0.0, 0.0, 0.0)
            self.display_attempts = 0
            self.scale_attempts = 0
            self.target_attempts = 0

        @property
        def CustomScale(self):
            return self._scale

        @CustomScale.setter
        def CustomScale(self, value):
            self.scale_attempts += 1
            if self.scale_attempts == 1:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy scale")
            self._scale = float(value)

        @property
        def Target(self):
            return self._target

        @Target.setter
        def Target(self, value):
            self.target_attempts += 1
            if self.target_attempts == 1:
                raise RuntimeError(cb._RPC_E_CALL_REJECTED, "busy target")
            self._target = tuple(value)

        def Display(self, enabled):
            self.display_attempts += 1
            if self.display_attempts == 1:
                raise RuntimeError(cb._RPC_E_SERVERCALL_RETRYLATER, "busy display")
            self.ViewportOn = bool(enabled)

        def Delete(self):
            layout.Block.items.remove(self)

    viewport = BusyViewport()

    def add_viewport(_center, _width, _height):
        layout.Block.items.append(viewport)
        return viewport

    doc.PaperSpace = SimpleNamespace(AddPViewport=add_viewport)
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))
    monkeypatch.setattr(cb.time, "sleep", lambda _seconds: None)

    created = await backend.viewport_create("Layout1", 100, 75, 80, 40, 10, 20, scale=0.5)

    assert created["scale"] == pytest.approx(0.5)
    assert created["view_center"] == [10.0, 20.0]
    assert viewport.display_attempts == 2
    assert viewport.scale_attempts == 2
    assert viewport.target_attempts == 2
    assert doc.ActiveLayout.Name == "Model"


@pytest.mark.asyncio
async def test_viewport_create_restore_failure_cleans_created_viewport_and_quarantines(
    settings, monkeypatch
):
    backend = ComBackend(replace(settings, backend="com"))

    class RestoreFailDoc:
        def __init__(self):
            self.Layouts = _FakeCollection([_FakeLayout("Model"), _FakeLayout("Layout1")])
            self._active_layout = self.Layouts.Item("Model")
            self._switched = False
            self.PaperSpace = SimpleNamespace(AddPViewport=self._add_viewport)

        @property
        def ActiveLayout(self):
            return self._active_layout

        @ActiveLayout.setter
        def ActiveLayout(self, value):
            if self._switched and value.Name == "Model":
                raise RuntimeError("injected active layout restore failure")
            self._active_layout = value
            if value.Name == "Layout1":
                self._switched = True

        def _add_viewport(self, center, width, height):
            block = self._active_layout.Block
            viewport = _FakeViewport("C1", tuple(center), float(width), float(height), block)
            block.items.append(viewport)
            return viewport

    doc = RestoreFailDoc()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))

    with pytest.raises(StateConflictError, match="restore"):
        await backend.viewport_create("Layout1", 100, 75, 80, 40, 10, 20, scale=0.5)
    assert doc.Layouts.Item("Layout1").Block.Count == 0
    assert backend.status()["integrity_uncertain"] is True


@pytest.mark.asyncio
async def test_preexisting_viewport_delete_requires_explicit_force(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    doc = _FakeDoc()
    layout = doc.Layouts.Item("Layout1")
    existing = _FakeViewport("AA", (0.0, 0.0, 0.0), 10.0, 10.0, layout.Block)
    layout.Block.items.append(existing)
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)

    with pytest.raises(StateConflictError, match="force=true"):
        await backend.viewport_delete("AA")
    assert layout.Block.Count == 1

    result = await backend.viewport_delete("AA", force=True)
    assert result["forced"] is True
    assert layout.Block.Count == 0


@pytest.mark.asyncio
async def test_staged_live_view_zoom_and_screenshot(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    calls = []

    class FakeApp:
        HWND = 123

        def ZoomExtents(self):
            calls.append(("extents",))

        def ZoomWindow(self, low, high):
            calls.append(("window", low, high))

    app = FakeApp()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_app", lambda: app)
    monkeypatch.setattr(backend, "_doc", lambda: object())
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))
    monkeypatch.setattr(cb, "_PIL_OK", True)
    monkeypatch.setattr(cb, "_capture_window_png", lambda hwnd: b"\x89PNG\r\n\x1a\nmock")

    assert (await backend.view_zoom_extents())["mode"] == "extents"
    window = await backend.view_zoom_window(10, 20, -5, 5)
    assert window["min"] == [-5.0, 5.0]
    assert window["max"] == [10.0, 20.0]
    assert calls[-1] == ("window", (-5.0, 5.0, 0.0), (10.0, 20.0, 0.0))
    assert (await backend.view_screenshot()).startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_view_operations_report_artifact_state_and_screenshot_fails_closed_on_dirty_change(
    settings, monkeypatch
):
    backend = ComBackend(replace(settings, backend="com"))

    class Doc:
        Saved = True
        dbmod = 0
        ActiveViewport = SimpleNamespace(Direction=None)

        def GetVariable(self, name):
            if name == "DBMOD":
                return self.dbmod
            raise KeyError(name)

    doc = Doc()
    app = SimpleNamespace(
        HWND=123,
        ZoomExtents=lambda: None,
        ZoomWindow=lambda _low, _high: None,
    )
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_app", lambda: app)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))
    monkeypatch.setattr(cb, "_PIL_OK", True)
    monkeypatch.setattr(cb, "_capture_window_png", lambda _hwnd: b"\x89PNG\r\n\x1a\nmock")

    for result in (
        await backend.view_zoom_extents(),
        await backend.view_zoom_window(0, 0, 10, 10),
        await backend.view_set_direction(1, -1, 1),
        await backend.view_set_preset("se_isometric"),
    ):
        assert result["artifact_state"] == {
            "saved_before": True,
            "saved_after": True,
            "dbmod_before": 0,
            "dbmod_after": 0,
            "dirty_changed": False,
        }

    def dirty_capture(_hwnd):
        doc.Saved = False
        doc.dbmod = 1
        return b"\x89PNG\r\n\x1a\nmock"

    monkeypatch.setattr(cb, "_capture_window_png", dirty_capture)
    with pytest.raises(StateConflictError, match="screenshot changed document artifact state"):
        await backend.view_screenshot()
    assert backend.status()["integrity_uncertain"] is True


@pytest.mark.asyncio
async def test_zoom_window_rejects_zero_area_before_touching_com(settings):
    backend = ComBackend(replace(settings, backend="com"))
    with pytest.raises(ValueError, match="non-zero"):
        await backend.view_zoom_window(1, 1, 1, 5)


def test_manual_active_document_switch_clears_document_scoped_viewport_authority(
    settings, monkeypatch
):
    backend = ComBackend(replace(settings, backend="com"))
    doc1 = SimpleNamespace(Name="one.dwg", FullName="C:/work/one.dwg")
    doc2 = SimpleNamespace(Name="two.dwg", FullName="C:/work/two.dwg")
    app = SimpleNamespace(Documents=SimpleNamespace(Count=1), ActiveDocument=doc1)
    monkeypatch.setattr(backend, "_app", lambda: app)

    assert backend._doc() is doc1
    backend._created_viewport_handles.add("AA")
    app.ActiveDocument = doc2

    assert backend._doc() is doc2
    assert backend._created_viewport_handles == set()


def test_manual_document_switch_is_refused_while_transaction_is_open(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    doc1 = SimpleNamespace(Name="one.dwg", FullName="C:/work/one.dwg")
    doc2 = SimpleNamespace(Name="two.dwg", FullName="C:/work/two.dwg")
    app = SimpleNamespace(Documents=SimpleNamespace(Count=1), ActiveDocument=doc1)
    monkeypatch.setattr(backend, "_app", lambda: app)

    assert backend._doc() is doc1
    backend._transaction_depth = 1
    app.ActiveDocument = doc2

    with pytest.raises(StateConflictError, match="Switch back"):
        backend._doc()
    assert backend._transaction_depth == 1
    assert backend._document_scope_key == ("one.dwg", "C:/work/one.dwg")


@pytest.mark.skipif(
    not _LIVE_COM_ENABLED,
    reason="requires Windows + running AutoCAD + CDT_AUTOCAD_LIVE_TEST=1",
)
@pytest.mark.asyncio
async def test_live_autocad_native_dwg_smoke(settings, tmp_path: Path):
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
        layer = await backend.layer_create("CDT-LIVE", color=3)
        assert layer.name == "CDT-LIVE"
        assert (await backend.layer_set_current("CDT-LIVE"))["current_layer"] == "CDT-LIVE"

        line = await backend.entity_create_line(0, 0, 25, 0, layer="CDT-LIVE")
        circle = await backend.entity_create_circle(40, 0, 8, layer="CDT-LIVE")
        arc = await backend.entity_create_arc(65, 0, 8, 0, 180, layer="CDT-LIVE")
        polyline = await backend.entity_create_polyline(
            [[0, 20], [20, 20], [20, 35], [0, 35]], closed=True, layer="CDT-LIVE"
        )
        text = await backend.entity_create_text("CDT A2 LIVE", 30, 25, layer="CDT-LIVE")
        hatch = await backend.hatch_create(
            [[50, 20], [70, 20], [70, 35], [50, 35]], layer="CDT-LIVE"
        )
        linear_dim = await backend.dimension_linear(0, 0, 25, 0, 12.5, -8, layer="CDT-LIVE")
        aligned_dim = await backend.dimension_aligned(0, 20, 20, 35, 10, 42, layer="CDT-LIVE")
        assert {entity.type for entity in (line, circle, arc, polyline, text, hatch)} == {
            "LINE",
            "CIRCLE",
            "ARC",
            "LWPOLYLINE",
            "TEXT",
            "HATCH",
        }
        assert linear_dim.type == aligned_dim.type == "DIMENSION"
        assert await backend.object_count(type_filter="LINE") >= 1
        assert (await backend.object_get(line.id)).id == line.id
        assert len(await backend.object_list(layer_filter="CDT-LIVE")) >= 8

        edited = await backend.object_set_properties(line.id, color=1, visible=True)
        assert edited.color == 1
        moved = await backend.object_move(line.id, 5, 5)
        assert moved.properties["start"] == pytest.approx([5.0, 5.0, 0.0])
        copied = await backend.object_copy(circle.id, 0, 20)
        assert copied.type == "CIRCLE"
        await backend.object_rotate(polyline.id, 0, 20, 15)
        await backend.object_scale(text.id, 30, 25, 1.2)

        block = await backend.block_create("CDT-LIVE-BLOCK", [line.id, circle.id], 0, 0)
        assert block.entity_count == 2
        inserted = await backend.block_insert("CDT-LIVE-BLOCK", 90, 25, layer="CDT-LIVE")
        assert inserted.type == "INSERT"

        assert (await backend.transaction_begin())["transaction_depth"] == 1
        await backend.entity_create_line(0, 50, 25, 50, layer="CDT-LIVE")
        assert (await backend.transaction_commit())["transaction_depth"] == 0
        assert (await backend.drawing_purge())["ok"] is True

        await backend.layout_create("CDT-A2-SHEET")
        viewport = await backend.viewport_create(
            "CDT-A2-SHEET", 100, 75, 80, 40, 12.5, 0, scale=0.5
        )
        assert viewport["scale"] == pytest.approx(0.5)
        assert (await backend.viewport_set_scale(viewport["handle"], 0.25))["scale"] == pytest.approx(
            0.25
        )
        assert (await backend.viewport_lock(viewport["handle"], True))["locked"] is True
        assert (await backend.viewport_list("CDT-A2-SHEET"))["count"] >= 1

        await backend.layout_set_current("Model")
        assert (await backend.view_zoom_extents())["ok"] is True
        assert (await backend.view_zoom_window(-10, -10, 40, 10))["ok"] is True
        screenshot = await backend.view_screenshot()
        assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")

        pdf_target = tmp_path / "cdt-a2-live-smoke.pdf"
        plotted = await backend.document_export_pdf(str(pdf_target), layout="CDT-A2-SHEET")
        assert plotted["ok"] is True
        assert pdf_target.is_file()

        target = tmp_path / "cdt-a2-live-smoke.dwg"
        saved = await backend.document_save_as(str(target))
        assert saved["format"] == "dwg"
        assert target.is_file()

        info = await backend.document_info()
        assert info["backend"] == "com"
        assert info["path"] == str(target)
        assert info["autocad_com_version"] is not None
        assert info["autocad_release"] is not None
        expected_release = os.environ.get("CDT_AUTOCAD_EXPECT_RELEASE", "").strip()
        if expected_release:
            assert info["autocad_release"] == expected_release
        status = backend.status()
        assert status["application"]["release"] == info["autocad_release"]
        assert (await backend.viewport_delete(viewport["handle"]))["ok"] is True

        await backend._run(lambda: backend._app().ActiveDocument.Close(False))
        created_name = None
        reopened = await backend.document_open(str(target))
        created_name = reopened["name"]
        assert reopened["path"] == str(target)
        reopened_info = await backend.document_info()
        assert reopened_info["path"] == str(target)
    finally:
        if created_name and backend._executor is not None:
            try:
                await backend._run(
                    lambda: backend._app().Documents.Item(created_name).Close(False)
                )
            except Exception:
                pass
        backend.shutdown()


@pytest.mark.asyncio
async def test_object_copy_cleanup_verifies_actual_document_owner(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))

    class Collection:
        def __init__(self, items=None):
            self.items = list(items or [])

        @property
        def Count(self):
            return len(self.items)

        def Item(self, index):
            return self.items[index]

    active_space = Collection()
    owner = Collection()

    class Duplicate:
        ObjectName = "AcDbLine"
        Handle = "D1"
        Layer = "0"
        Color = 256
        Linetype = "ByLayer"
        Visible = True
        StartPoint = (0.0, 0.0, 0.0)
        EndPoint = (1.0, 0.0, 0.0)

        def Move(self, _origin, _target):
            raise RuntimeError("injected copy move failure")

        def Delete(self):
            return None

    duplicate = Duplicate()

    class Source:
        def Copy(self):
            owner.items.append(duplicate)
            return duplicate

    blocks = SimpleNamespace(Count=2, Item=lambda index: (active_space, owner)[index])
    doc = SimpleNamespace(Blocks=blocks)
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    monkeypatch.setattr(backend, "_space", lambda: active_space)
    monkeypatch.setattr(backend, "_entity_by_id", lambda _object_id: Source())
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))

    with pytest.raises(StateConflictError, match="cleanup verification failed"):
        await backend.object_copy("S1", 1, 0)
    assert owner.items == [duplicate]
    assert backend.status()["integrity_uncertain"] is True


@pytest.mark.asyncio
async def test_created_entity_configuration_failure_removes_partial_entity(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))

    class Entity:
        ObjectName = "AcDbLine"
        Handle = "E1"
        StartPoint = (0.0, 0.0, 0.0)
        EndPoint = (1.0, 0.0, 0.0)
        Color = 256
        Linetype = "ByLayer"
        Visible = True

        def __init__(self, collection):
            self._collection = collection
            self._layer = "0"

        @property
        def Layer(self):
            return self._layer

        @Layer.setter
        def Layer(self, value):
            if value == "TARGET":
                raise RuntimeError("injected layer assignment failure")
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

        def AddLine(self, _start, _end):
            entity = Entity(self)
            self.items.append(entity)
            return entity

    space = Space()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_space", lambda: space)
    monkeypatch.setattr(backend, "_validate_layer", lambda _layer: "TARGET")
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))

    with pytest.raises(RuntimeError, match="layer assignment failure"):
        await backend.entity_create_line(0, 0, 1, 0, layer="TARGET")
    assert space.items == []
    assert backend.status()["integrity_uncertain"] is False


@pytest.mark.asyncio
async def test_created_entity_cleanup_mismatch_quarantines_backend(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))

    class Entity:
        ObjectName = "AcDbLine"
        Handle = "E1"
        StartPoint = (0.0, 0.0, 0.0)
        EndPoint = (1.0, 0.0, 0.0)
        Color = 256
        Linetype = "ByLayer"
        Visible = True

        def __init__(self, collection):
            self._collection = collection
            self._layer = "0"

        @property
        def Layer(self):
            return self._layer

        @Layer.setter
        def Layer(self, value):
            if value == "TARGET":
                raise RuntimeError("injected layer assignment failure")
            self._layer = value

        def Delete(self):
            return None

    class Space:
        def __init__(self):
            self.items = []

        @property
        def Count(self):
            return len(self.items)

        def Item(self, index):
            return self.items[index]

        def AddLine(self, _start, _end):
            entity = Entity(self)
            self.items.append(entity)
            return entity

    space = Space()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_space", lambda: space)
    monkeypatch.setattr(backend, "_validate_layer", lambda _layer: "TARGET")
    monkeypatch.setattr(cb, "_point", lambda x, y, z=0.0: (float(x), float(y), float(z)))

    with pytest.raises(StateConflictError, match="cleanup verification failed"):
        await backend.entity_create_line(0, 0, 1, 0, layer="TARGET")
    assert backend.status()["integrity_uncertain"] is True


@pytest.mark.asyncio
async def test_hatch_success_fails_closed_when_temporary_boundary_survives(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))

    class Item:
        def __init__(self, handle, kind, collection, delete_removes=True):
            self.Handle = handle
            self.ObjectName = kind
            self.Layer = "0"
            self.Color = 256
            self.Linetype = "ByLayer"
            self.Visible = True
            self.PatternName = "SOLID"
            self.Closed = False
            self._collection = collection
            self._delete_removes = delete_removes

        def AppendOuterLoop(self, _items):
            return None

        def Evaluate(self):
            return None

        def Delete(self):
            if self._delete_removes:
                self._collection.items.remove(self)

    class Space:
        def __init__(self):
            self.items = []

        @property
        def Count(self):
            return len(self.items)

        def Item(self, index):
            return self.items[index]

        def AddHatch(self, *_args):
            item = Item("H1", "AcDbHatch", self)
            self.items.append(item)
            return item

        def AddLightWeightPolyline(self, _values):
            item = Item("B1", "AcDbPolyline", self, delete_removes=False)
            self.items.append(item)
            return item

    space = Space()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_space", lambda: space)
    monkeypatch.setattr(backend, "_validate_layer", lambda _layer: None)
    monkeypatch.setattr(cb, "_double_array", lambda values: tuple(values))
    monkeypatch.setattr(cb, "_dispatch_array", lambda values: list(values))

    with pytest.raises(StateConflictError, match="cleanup verification failed"):
        await backend.hatch_create([[0, 0], [10, 0], [10, 10]])
    assert backend.status()["integrity_uncertain"] is True
    assert any(item.Handle == "B1" for item in space.items)


@pytest.mark.asyncio
async def test_viewport_delete_verifies_absence_before_discarding_ownership(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    doc = _FakeDoc()
    viewport = doc._add_viewport((100.0, 75.0, 0.0), 80.0, 40.0)
    key = viewport.Handle.upper()
    backend._created_viewport_handles.add(key)
    viewport.Delete = lambda: None
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)

    with pytest.raises(StateConflictError, match="still present"):
        await backend.viewport_delete(key)
    assert doc.HandleToObject(key) is viewport
    assert backend.status()["integrity_uncertain"] is True


@pytest.mark.asyncio
async def test_xref_membership_read_failure_is_not_reported_as_empty_complete_set(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))

    class BrokenXref:
        @property
        def IsXRef(self):
            raise RuntimeError("injected IsXRef read failure")

    blocks = _FakeCollection([BrokenXref()])
    doc = SimpleNamespace(Blocks=blocks)
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)

    with pytest.raises(RuntimeError, match="IsXRef read failure"):
        await backend.document_dependencies()


@pytest.mark.asyncio
async def test_com_transaction_rollback_receipt_does_not_claim_verified_restore(settings, monkeypatch):
    backend = ComBackend(replace(settings, backend="com"))
    calls = []

    class Doc:
        def EndUndoMark(self):
            calls.append("end")

        def SendCommand(self, command):
            calls.append(command)

    backend._transaction_depth = 1
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: Doc())

    result = await backend.transaction_rollback()
    assert result["queued"] is True
    assert result["rollback_command_queued"] is True
    assert result["rollback_verified"] is False
    assert result["rolled_back"] is False
    assert calls == ["end", "_.UNDO _1\n"]


@pytest.mark.asyncio
async def test_timed_out_transaction_begin_does_not_hide_possible_open_undo_mark(
    settings, monkeypatch
):
    backend = ComBackend(
        replace(
            settings,
            backend="com",
            transaction_depth=1,
            com_call_timeout_seconds=0.05,
        )
    )
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    release = threading.Event()
    entered = threading.Event()

    class FakeDoc:
        def StartUndoMark(self):
            entered.set()
            release.wait(1.0)

    monkeypatch.setattr(backend, "_doc", lambda: FakeDoc())

    try:
        with pytest.raises(BackendTimeoutError):
            await backend.transaction_begin()
        assert entered.is_set()
        assert backend.status()["transaction_depth"] == 1
        with pytest.raises(StateConflictError, match="depth limit"):
            await backend.transaction_begin()
    finally:
        release.set()
        executor.shutdown(wait=False)
        backend._executor = None
