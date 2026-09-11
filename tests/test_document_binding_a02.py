"""A-02 document binding — validate and save the same live COM document instance.
Wing: code | Topic: mp0-a02-document-binding | Updated: 2026-09-11 11:15
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace

import pytest

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.errors import StateConflictError

_LIVE_COM_ENABLED = sys.platform == "win32" and os.environ.get("CDT_AUTOCAD_LIVE_TEST") == "1"
_LIVE_COM_PROGID = (
    os.environ.get("CDT_AUTOCAD_COM_PROGID", "AutoCAD.Application").strip()
    or "AutoCAD.Application"
)


async def _inline_run(func, **_kwargs):
    return func()


class _SaveDoc:
    def __init__(self, name: str, full_name: str):
        self.Name = name
        self.FullName = full_name
        self.save_calls = 0

    def Save(self):
        self.save_calls += 1


@pytest.mark.asyncio
async def test_document_save_keeps_bound_doc_when_active_document_switches_during_validation(
    settings, monkeypatch, tmp_path
):
    backend = ComBackend(replace(settings, backend="com"))
    safe_path = tmp_path / "a.dwg"
    outside_path = tmp_path.parent / "outside.dwg"
    doc_a = _SaveDoc("a.dwg", str(safe_path))
    doc_b = _SaveDoc("outside.dwg", str(outside_path))
    active = {"doc": doc_a}
    original_resolver = cb.resolve_autocad_document_path

    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: active["doc"])

    def switch_after_validation(raw_path, current_settings, **kwargs):
        resolved = original_resolver(raw_path, current_settings, **kwargs)
        active["doc"] = doc_b
        return resolved

    monkeypatch.setattr(cb, "resolve_autocad_document_path", switch_after_validation)

    result = await backend.document_save()

    assert result["ok"] is True
    assert result["path"] == str(safe_path.resolve())
    assert doc_a.save_calls == 1
    assert doc_b.save_calls == 0


@pytest.mark.asyncio
async def test_document_save_refuses_success_if_bound_doc_path_changes_during_save(
    settings, monkeypatch, tmp_path
):
    backend = ComBackend(replace(settings, backend="com"))
    before = tmp_path / "before.dwg"
    after = tmp_path / "after.dwg"

    class DriftingDoc:
        Name = "before.dwg"

        def __init__(self):
            self.save_calls = 0
            self._saved = False

        @property
        def FullName(self):
            return str(after if self._saved else before)

        def Save(self):
            self.save_calls += 1
            self._saved = True

    doc = DriftingDoc()
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)

    with pytest.raises(StateConflictError, match="path changed during save"):
        await backend.document_save()

    assert doc.save_calls == 1


@pytest.mark.asyncio
async def test_document_save_rejects_bound_doc_outside_allowed_root_before_save(
    settings, monkeypatch, tmp_path
):
    backend = ComBackend(replace(settings, backend="com"))
    doc = _SaveDoc("outside.dwg", str(tmp_path.parent / "outside.dwg"))
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)

    with pytest.raises(ValueError, match="outside CDT_AUTOCAD_ALLOWED_PATHS"):
        await backend.document_save()

    assert doc.save_calls == 0


@pytest.mark.skipif(
    not _LIVE_COM_ENABLED,
    reason="requires Windows + running AutoCAD + CDT_AUTOCAD_LIVE_TEST=1",
)
@pytest.mark.asyncio
async def test_live_document_save_keeps_verified_fixture_path(settings, tmp_path):
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
    target = tmp_path / "a02-live-save-binding.dwg"
    try:
        created = await backend.document_new()
        created_name = created["name"]
        saved_as = await backend.document_save_as(str(target))
        assert saved_as["path"] == str(target)
        assert target.is_file()

        saved = await backend.document_save()
        assert saved["ok"] is True
        assert saved["path"] == str(target)
        assert saved["format"] == "dwg"
        info = await backend.document_info()
        assert info["path"] == str(target)
    finally:
        if created_name and backend._executor is not None:
            try:
                await backend._run(
                    lambda: backend._app().Documents.Item(created_name).Close(False)
                )
            except Exception:
                pass
        backend.shutdown()
