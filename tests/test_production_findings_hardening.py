"""Production findings closure — artifact, preset, DXF parity and 3D export hardening.
Wing: code | Topic: production-findings | Updated: 2026-09-11 17:25
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.command_presets import BOUNDED_COMMAND_PRESETS, VIEW_PRESETS, WORKFLOW_DEFAULTS
from cdt_autocad.errors import BackendQuarantinedError, StateConflictError


def _run_inline(monkeypatch, backend):
    async def inline(fn, *, may_mutate_document=True):
        return fn()

    monkeypatch.setattr(backend, "_run", inline)


def test_command_preset_registry_locks_video_friendly_defaults():
    assert WORKFLOW_DEFAULTS == {
        "step_delay_ms": 300,
        "default_3d_view": "se_isometric",
        "default_3d_visual_style": "shades_of_gray",
    }
    assert VIEW_PRESETS["se_isometric"] == (1.0, -1.0, 1.0)
    assert BOUNDED_COMMAND_PRESETS["visual.shades_of_gray"] == (
        "_.VSCURRENT\n_Shadesofgray\n"
    )
    assert "display.regen" in BOUNDED_COMMAND_PRESETS
    assert not any("LISP" in value.upper() for value in BOUNDED_COMMAND_PRESETS.values())


class _AtomicUnitsDoc:
    def __init__(
        self,
        *,
        fail_apply_index: int | None = None,
        ignore_apply_name: str | None = None,
        fail_restore_name: str | None = None,
    ):
        self.initial = {"INSUNITS": 0, "MEASUREMENT": 0, "LUNITS": 2, "LUPREC": 4}
        self.values = dict(self.initial)
        self.fail_apply_index = fail_apply_index
        self.ignore_apply_name = ignore_apply_name
        self.fail_restore_name = fail_restore_name
        self.set_count = 0
        self.apply_failed = False
        self.restore_failure_fired = False

    def SetVariable(self, name, value):
        self.set_count += 1
        if self.fail_apply_index is not None and self.set_count == self.fail_apply_index:
            self.apply_failed = True
            raise RuntimeError(f"injected set failure #{self.fail_apply_index}")
        if (
            self.apply_failed
            and self.fail_restore_name == name
            and value == self.initial[name]
            and not self.restore_failure_fired
        ):
            self.restore_failure_fired = True
            raise RuntimeError(f"injected restore failure: {name}")
        if not self.apply_failed and self.ignore_apply_name == name and value != self.initial[name]:
            return
        self.values[name] = value

    def GetVariable(self, name):
        return self.values[name]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_apply_index", [1, 2, 3, 4])
async def test_com_units_atomicity_restores_exact_predecessor_after_each_set_failure(
    settings, monkeypatch, fail_apply_index
):
    doc = _AtomicUnitsDoc(fail_apply_index=fail_apply_index)
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    _run_inline(monkeypatch, backend)

    with pytest.raises(RuntimeError, match="injected set failure"):
        await backend.document_configure_units(
            "millimeters", "metric", "architectural", 3
        )

    assert doc.values == doc.initial
    assert backend.status()["integrity_uncertain"] is False


@pytest.mark.asyncio
async def test_com_units_atomicity_rolls_back_readback_mismatch(settings, monkeypatch):
    doc = _AtomicUnitsDoc(ignore_apply_name="LUNITS")
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    _run_inline(monkeypatch, backend)

    with pytest.raises(StateConflictError, match="units read-back mismatch"):
        await backend.document_configure_units(
            "millimeters", "metric", "architectural", 3
        )

    assert doc.values == doc.initial
    assert backend.status()["integrity_uncertain"] is False


@pytest.mark.asyncio
async def test_com_units_restore_failure_quarantines_later_mutations(settings, monkeypatch):
    doc = _AtomicUnitsDoc(fail_apply_index=3, fail_restore_name="INSUNITS")
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: doc)
    _run_inline(monkeypatch, backend)

    with pytest.raises(StateConflictError, match="restore verification failed"):
        await backend.document_configure_units(
            "millimeters", "metric", "architectural", 3
        )

    status = backend.status()
    assert status["integrity_uncertain"] is True
    assert "document_configure_units" in status["integrity_uncertain_reason"]

    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    try:
        with pytest.raises(BackendQuarantinedError, match="integrity verification failure"):
            await backend._run_after_mutation_gate(lambda: None, may_mutate_document=True)
        assert (
            await backend._run_after_mutation_gate(
                lambda: "read-ok", may_mutate_document=False
            )
            == "read-ok"
        )
    finally:
        executor.shutdown(wait=False)
        backend._executor = None


class _AtomicNamedCollection:
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


class _AtomicLayer:
    _FIELDS = ("LayerOn", "Freeze", "Lock", "Color", "Linetype", "LineWeight")

    def __init__(
        self,
        name="WORK",
        *,
        fail_apply_index: int | None = None,
        ignore_apply_field: str | None = None,
        fail_restore_field: str | None = None,
    ):
        object.__setattr__(self, "Name", name)
        initial = {
            "LayerOn": True,
            "Freeze": False,
            "Lock": False,
            "Color": 7,
            "Linetype": "Continuous",
            "LineWeight": -3,
        }
        object.__setattr__(self, "initial", initial)
        for field, value in initial.items():
            object.__setattr__(self, field, value)
        object.__setattr__(self, "fail_apply_index", fail_apply_index)
        object.__setattr__(self, "ignore_apply_field", ignore_apply_field)
        object.__setattr__(self, "fail_restore_field", fail_restore_field)
        object.__setattr__(self, "set_count", 0)
        object.__setattr__(self, "apply_failed", False)
        object.__setattr__(self, "restore_failure_fired", False)
        object.__setattr__(self, "tracking", True)

    def __setattr__(self, name, value):
        if name not in self._FIELDS or not getattr(self, "tracking", False):
            object.__setattr__(self, name, value)
            return
        object.__setattr__(self, "set_count", self.set_count + 1)
        if self.fail_apply_index is not None and self.set_count == self.fail_apply_index:
            object.__setattr__(self, "apply_failed", True)
            raise RuntimeError(f"injected layer setter failure #{self.fail_apply_index}")
        if (
            self.apply_failed
            and self.fail_restore_field == name
            and value == self.initial[name]
            and not self.restore_failure_fired
        ):
            object.__setattr__(self, "restore_failure_fired", True)
            raise RuntimeError(f"injected layer restore failure: {name}")
        if (
            not self.apply_failed
            and self.ignore_apply_field == name
            and value != self.initial[name]
        ):
            return
        object.__setattr__(self, name, value)

    def snapshot(self):
        return {field: getattr(self, field) for field in self._FIELDS}


def _atomic_layer_doc(layer):
    return SimpleNamespace(
        ActiveLayer=SimpleNamespace(Name="CURRENT"),
        Layers=_AtomicNamedCollection([SimpleNamespace(Name="CURRENT"), layer]),
        Linetypes=_AtomicNamedCollection(
            [SimpleNamespace(Name="Continuous"), SimpleNamespace(Name="Dashed")]
        ),
    )


@pytest.mark.asyncio
async def test_com_layer_rejects_invalid_linetype_before_any_write(settings, monkeypatch):
    layer = _AtomicLayer()
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: _atomic_layer_doc(layer))
    _run_inline(monkeypatch, backend)

    with pytest.raises(ValueError, match="linetype does not exist"):
        await backend.layer_update_state(
            "WORK", is_on=False, is_locked=True, color=3, linetype="MISSING"
        )

    assert layer.snapshot() == layer.initial
    assert layer.set_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_apply_index", [1, 2, 3, 4, 5, 6])
async def test_com_layer_atomicity_restores_exact_predecessor_after_each_setter_failure(
    settings, monkeypatch, fail_apply_index
):
    layer = _AtomicLayer(fail_apply_index=fail_apply_index)
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: _atomic_layer_doc(layer))
    _run_inline(monkeypatch, backend)

    with pytest.raises(RuntimeError, match="injected layer setter failure"):
        await backend.layer_update_state(
            "WORK",
            is_on=False,
            is_frozen=True,
            is_locked=True,
            color=3,
            linetype="Dashed",
            lineweight=25,
        )

    assert layer.snapshot() == layer.initial
    assert backend.status()["integrity_uncertain"] is False


@pytest.mark.asyncio
async def test_com_layer_atomicity_rolls_back_readback_mismatch(settings, monkeypatch):
    layer = _AtomicLayer(ignore_apply_field="Color")
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: _atomic_layer_doc(layer))
    _run_inline(monkeypatch, backend)

    with pytest.raises(StateConflictError, match="layer state read-back mismatch"):
        await backend.layer_update_state(
            "WORK",
            is_on=False,
            is_frozen=True,
            is_locked=True,
            color=3,
            linetype="Dashed",
            lineweight=25,
        )

    assert layer.snapshot() == layer.initial
    assert backend.status()["integrity_uncertain"] is False


@pytest.mark.asyncio
async def test_com_layer_restore_failure_quarantines_later_mutations(settings, monkeypatch):
    layer = _AtomicLayer(fail_apply_index=4, fail_restore_field="LayerOn")
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: _atomic_layer_doc(layer))
    _run_inline(monkeypatch, backend)

    with pytest.raises(StateConflictError, match="layer_update_state restore verification failed"):
        await backend.layer_update_state(
            "WORK",
            is_on=False,
            is_frozen=True,
            is_locked=True,
            color=3,
            linetype="Dashed",
            lineweight=25,
        )

    status = backend.status()
    assert status["integrity_uncertain"] is True
    assert "layer_update_state" in status["integrity_uncertain_reason"]


@pytest.mark.asyncio
async def test_com_layer_rejects_xref_dependent_layer_before_write(settings, monkeypatch):
    layer = _AtomicLayer(name="SITE|ROAD")
    backend = ComBackend(replace(settings, backend="com"))
    monkeypatch.setattr(backend, "_doc", lambda: _atomic_layer_doc(layer))
    _run_inline(monkeypatch, backend)

    with pytest.raises(ValueError, match="XREF-dependent"):
        await backend.layer_update_state("site|road", is_locked=True)

    assert layer.snapshot() == layer.initial
    assert layer.set_count == 0


@pytest.mark.asyncio
async def test_ezdxf_units_polyline_curves_and_layer_state_roundtrip(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    units = await backend.document_configure_units(
        "millimeters", "metric", "decimal", 3
    )
    assert units["raw"] == {"INSUNITS": 4, "MEASUREMENT": 1, "LUNITS": 2, "LUPREC": 3}

    await backend.layer_create("CURVES", 3)
    layer = await backend.layer_update_state("CURVES", is_locked=True, color=4)
    assert layer.is_locked is True
    assert layer.color == 4

    entity = await backend.entity_create_polyline(
        [[0, 0], [10, 0], [10, 10]],
        closed=False,
        layer="CURVES",
        bulges=[0.5, 0.0, 0.0],
        widths=[[1.0, 2.0], [0.0, 0.0], [0.0, 0.0]],
        elevation=3.0,
    )
    measured = await backend.object_measure(entity.id)
    assert measured["type"] == "LWPOLYLINE"
    assert measured["length"] > 20.0


@pytest.mark.asyncio
async def test_artifact_seal_copies_content_addressed_clean_saved_drawing(settings, monkeypatch, tmp_path: Path):
    drawing = tmp_path / "part.dwg"
    drawing.write_bytes(b"trusted-dwg-bytes")

    class Doc:
        FullName = str(drawing)
        Saved = True

        def Save(self):
            self.Saved = True

    backend = ComBackend(replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),)))
    monkeypatch.setattr(backend, "_doc", lambda: Doc())
    _run_inline(monkeypatch, backend)

    sealed = await backend.artifact_seal()
    digest = hashlib.sha256(drawing.read_bytes()).hexdigest()
    assert sealed["status"] == "SEALED"
    assert sealed["sha256"] == digest
    sealed_path = Path(sealed["sealed_path"])
    assert sealed_path.name == f"part.{digest[:16]}.dwg"
    assert sealed_path.read_bytes() == drawing.read_bytes()
    assert Path(sealed["manifest_path"]).is_file()


@pytest.mark.asyncio
async def test_solid_export_is_sat_only_and_cleans_selection_set(settings, monkeypatch, tmp_path: Path):
    target = tmp_path / "part.sat"
    deleted = []
    added = []

    class Selection:
        def AddItems(self, objects):
            added.extend(list(objects))

        def Delete(self):
            deleted.append(True)

    class SelectionSets:
        def Add(self, _name):
            return Selection()

    class Doc:
        def __init__(self):
            self.SelectionSets = SelectionSets()

        def Export(self, base_name, extension, _selection):
            assert extension == "SAT"
            Path(base_name + ".sat").write_bytes(b"sat-data")

    backend = ComBackend(replace(settings, backend="com", allowed_paths=(tmp_path.resolve(),)))
    monkeypatch.setattr(backend, "_doc", lambda: Doc())
    monkeypatch.setattr(backend, "_solid_by_id", lambda handle: SimpleNamespace(Handle=handle))
    monkeypatch.setattr(cb, "_dispatch_array", lambda objects: tuple(objects))
    _run_inline(monkeypatch, backend)

    result = await backend.solid_export(["A1", "B2"], str(target), "sat")
    assert result["format"] == "sat"
    assert result["solid_handles"] == ["A1", "B2"]
    assert target.read_bytes() == b"sat-data"
    assert len(added) == 2
    assert deleted == [True]

    with pytest.raises(Exception) as exc_info:
        await backend.solid_export(["A1"], str(tmp_path / "part.step"), "step")
    assert "SAT only" in str(exc_info.value)
