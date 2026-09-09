"""A2 COM backend regression and live-lane tests.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 14:16
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
    app = SimpleNamespace(Visible=False)
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
        assert backend.status()["connected"] is True
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


async def _inline_run(func):
    return func()


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
            com_call_timeout_seconds=30.0,
        )
    )
    created_name = None
    try:
        created = await backend.document_new()
        created_name = created["name"]
        line = await backend.entity_create_line(0, 0, 25, 0)
        assert line.type == "LINE"
        assert await backend.object_count(type_filter="LINE") >= 1

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

        target = tmp_path / "cdt-a2-live-smoke.dwg"
        saved = await backend.document_save_as(str(target))
        assert saved["format"] == "dwg"
        assert target.is_file()

        info = await backend.document_info()
        assert info["backend"] == "com"
        assert info["path"] == str(target)
        assert (await backend.viewport_delete(viewport["handle"]))["ok"] is True
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
