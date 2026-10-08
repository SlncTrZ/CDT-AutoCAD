"""D17 save-family persistence — verified clean state on the same bound document.
Wing: code | Topic: d17-save-persistence | Updated: 2026-09-19 15:30
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.errors import StateConflictError


async def _inline_run(func, **_kwargs):
    return func()


class _SaveAsDoc:
    def __init__(self, initial_path, *, saved=True, dbmod=0):
        self.Name = initial_path.name
        self.FullName = str(initial_path)
        self.Saved = saved
        self.dbmod = dbmod
        self.save_as_calls = 0

    def SaveAs(self, path, _file_type):
        self.save_as_calls += 1
        self.FullName = str(path)
        self.Name = str(path).replace("\\", "/").rsplit("/", 1)[-1]

    def GetVariable(self, name):
        assert name == "DBMOD"
        return self.dbmod


@pytest.mark.asyncio
async def test_d17_save_as_returns_verified_persisted_clean_receipt(settings, monkeypatch, tmp_path):
    backend = ComBackend(
        replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),))
    )
    target = tmp_path / "verified-save-as.dwg"
    doc = _SaveAsDoc(tmp_path / "Drawing1.dwg", saved=True, dbmod=0)
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)

    result = await backend.document_save_as(str(target))

    assert result == {
        "ok": True,
        "path": str(target.resolve()),
        "format": "dwg",
        "command_completed": True,
        "persisted_path_verified": True,
        "saved": True,
        "dbmod": 0,
        "persisted_clean": True,
        "postcondition_verified": True,
    }
    assert doc.save_as_calls == 1
    assert backend._document_scope_key == (target.name, str(target.resolve()))
    assert backend.status()["integrity_uncertain"] is False
    assert backend._mutation_coordinator.status()["quarantined"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("saved", "dbmod"),
    [
        (False, 0),
        (True, 1),
        (False, 1),
    ],
)
async def test_d17_save_as_refuses_dirty_postcondition_and_shared_quarantines(
    settings, monkeypatch, tmp_path, saved, dbmod
):
    backend = ComBackend(
        replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),))
    )
    target = tmp_path / "dirty-save-as.dwg"
    doc = _SaveAsDoc(tmp_path / "Drawing1.dwg", saved=saved, dbmod=dbmod)
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)

    with pytest.raises(StateConflictError, match="persisted-clean"):
        await backend.document_save_as(str(target))

    assert doc.save_as_calls == 1
    assert backend.status()["integrity_uncertain"] is True
    assert backend._mutation_coordinator.status()["quarantined"] is True
    assert backend._mutation_coordinator.status()["quarantine_lane"] == "com"


@pytest.mark.asyncio
async def test_d17_save_as_quarantines_when_clean_state_cannot_be_read(
    settings, monkeypatch, tmp_path
):
    backend = ComBackend(
        replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),))
    )
    target = tmp_path / "unverifiable-save-as.dwg"

    class UnverifiableDoc(_SaveAsDoc):
        @property
        def Saved(self):
            raise RuntimeError("saved readback unavailable")

        @Saved.setter
        def Saved(self, _value):
            pass

    doc = UnverifiableDoc(tmp_path / "Drawing1.dwg", dbmod=0)
    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: doc)

    with pytest.raises(StateConflictError, match="could not be verified"):
        await backend.document_save_as(str(target))

    assert doc.save_as_calls == 1
    assert backend.status()["integrity_uncertain"] is True
    assert backend._mutation_coordinator.status()["quarantined"] is True


@pytest.mark.asyncio
async def test_d17_save_as_verifies_original_bound_document_if_active_document_switches(
    settings, monkeypatch, tmp_path
):
    backend = ComBackend(
        replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),))
    )
    target = tmp_path / "bound.dwg"
    other = tmp_path / "other.dwg"
    doc_a = _SaveAsDoc(tmp_path / "Drawing1.dwg", saved=True, dbmod=0)
    doc_b = _SaveAsDoc(other, saved=False, dbmod=1)
    active = {"doc": doc_a}
    original_resolver = cb.resolve_autocad_document_path

    monkeypatch.setattr(backend, "_run", _inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: active["doc"])

    def switch_after_target_validation(raw_path, current_settings, **kwargs):
        resolved = original_resolver(raw_path, current_settings, **kwargs)
        active["doc"] = doc_b
        return resolved

    monkeypatch.setattr(
        cb,
        "resolve_autocad_document_path",
        switch_after_target_validation,
    )

    result = await backend.document_save_as(str(target))

    assert result["persisted_clean"] is True
    assert result["path"] == str(target.resolve())
    assert doc_a.save_as_calls == 1
    assert doc_b.save_as_calls == 0
    assert doc_a.FullName == str(target.resolve())
    assert doc_b.FullName == str(other)
