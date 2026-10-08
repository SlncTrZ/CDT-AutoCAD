from __future__ import annotations

from pathlib import Path

import pytest

from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.config import Settings
from cdt_autocad.errors import StateConflictError, UnsupportedCapabilityError

pytestmark = pytest.mark.asyncio


async def test_object_mutations_and_undo_redo(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    await backend.layer_create("EDIT", color=2)

    line = await backend.entity_create_line(0, 0, 10, 0)
    moved = await backend.object_move(line.id, 2, 3)
    assert moved.properties["start"] == [2.0, 3.0, 0.0]

    styled = await backend.object_set_properties(line.id, layer="EDIT", color=4, visible=False)
    assert styled.layer == "EDIT"
    assert styled.color == 4
    assert styled.visible is False

    copied = await backend.object_copy(line.id, 5, 0)
    assert copied.id != line.id
    assert await backend.object_count() == 2

    rotated = await backend.object_rotate(copied.id, 0, 0, 90)
    assert rotated.type == "LINE"

    scaled = await backend.object_scale(line.id, 0, 0, 2)
    assert scaled.type == "LINE"

    await backend.object_delete(copied.id)
    assert await backend.object_count() == 1

    undo_result = await backend.undo()
    assert undo_result["redo_depth"] == 1
    assert await backend.object_count() == 2

    redo_result = await backend.redo()
    assert redo_result["redo_depth"] == 0
    assert await backend.object_count() == 1


async def test_transaction_rollback_restores_document(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()

    begun = await backend.transaction_begin()
    assert begun["transaction_depth"] == 1
    await backend.entity_create_circle(0, 0, 5)
    assert await backend.object_count() == 1

    rolled_back = await backend.transaction_rollback()
    assert rolled_back["rolled_back"] is True
    assert await backend.object_count() == 0
    assert backend.status()["transaction_depth"] == 0

    with pytest.raises(StateConflictError, match="No active transaction"):
        await backend.transaction_commit()


async def test_nested_transaction_commit_preserves_changes(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    await backend.transaction_begin()
    await backend.entity_create_line(0, 0, 1, 0)
    committed = await backend.transaction_commit()

    assert committed["transaction_depth"] == 0
    assert await backend.object_count() == 1


async def test_block_create_list_and_insert(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    line = await backend.entity_create_line(0, 0, 5, 0)

    block = await backend.block_create("PART_A", [line.id], base_x=0, base_y=0)
    assert block.name == "PART_A"
    assert block.entity_count == 1

    blocks = await backend.block_list()
    assert any(item.name == "PART_A" and item.entity_count == 1 for item in blocks)

    inserted = await backend.block_insert("PART_A", 10, 20, rotation=30)
    assert inserted.type == "INSERT"
    assert inserted.properties["block_name"] == "PART_A"
    assert inserted.properties["insert"][:2] == [10.0, 20.0]

    before = {item.name for item in await backend.block_list()}
    with pytest.raises(KeyError):
        await backend.block_create("BROKEN", ["NO_SUCH_HANDLE"])
    after = {item.name for item in await backend.block_list()}
    assert before == after


async def test_layout_current_space_routes_creation(settings, tmp_path: Path):
    backend = EzdxfBackend(settings)
    await backend.document_new()

    created = await backend.layout_create("Sheet-A")
    assert created["layout"] == "Sheet-A"
    await backend.layout_set_current("sheet-a")
    await backend.entity_create_text("SHEET", 1, 1)
    assert await backend.object_count() == 1

    await backend.layout_set_current("Model")
    assert await backend.object_count() == 0
    await backend.entity_create_line(0, 0, 5, 0)
    assert await backend.object_count() == 1

    await backend.layout_set_current("Sheet-A")
    assert await backend.object_count() == 1

    target = tmp_path / "layouts.dxf"
    await backend.document_save_as(str(target))
    await backend.document_open(str(target))
    assert backend.status()["current_space"] == "Sheet-A"
    assert await backend.object_count() == 1


async def test_dimensions_and_hatch_create_real_dxf_entities(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()

    linear = await backend.dimension_linear(0, 0, 10, 0, 0, 3)
    aligned = await backend.dimension_aligned(0, 0, 10, 10, 4, 8)
    hatch = await backend.hatch_create([[0, 0], [10, 0], [10, 10], [0, 10]])

    assert linear.type == "DIMENSION"
    assert aligned.type == "DIMENSION"
    assert hatch.type == "HATCH"
    assert await backend.object_count(type_filter="DIMENSION") == 2
    assert await backend.object_count(type_filter="HATCH") == 1


async def test_purge_removes_unused_resources_and_audit_reports(settings):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    await backend.layer_create("UNUSED_LAYER")
    line = await backend.entity_create_line(0, 0, 1, 0)
    await backend.block_create("UNUSED_BLOCK", [line.id])

    result = await backend.drawing_purge()
    assert result["ok"] is True
    assert "UNUSED_LAYER" in result["purged"]["layers"]
    assert "UNUSED_BLOCK" in result["purged"]["blocks"]

    audit = await backend.drawing_audit()
    assert audit["ok"] is True
    assert isinstance(audit["fixes"], list)
    assert isinstance(audit["errors"], list)


async def test_pdf_export_is_capability_gated(settings, tmp_path: Path):
    backend = EzdxfBackend(settings)
    await backend.document_new()
    await backend.entity_create_line(0, 0, 10, 0)
    target = tmp_path / "drawing.pdf"

    if backend.capabilities()["autocad.pdf.export"]["supported"]:
        result = await backend.document_export_pdf(str(target))
        assert result["ok"] is True
        assert target.is_file()
    else:
        with pytest.raises(UnsupportedCapabilityError) as excinfo:
            await backend.document_export_pdf(str(target))
        assert excinfo.value.capability == "autocad.pdf.export"
        assert not target.exists()


async def test_undo_can_be_disabled_explicitly(settings):
    disabled = Settings(
        allowed_paths=settings.allowed_paths,
        max_dxf_bytes=settings.max_dxf_bytes,
        call_timeout_seconds=settings.call_timeout_seconds,
        undo_depth=0,
    )
    backend = EzdxfBackend(disabled)
    await backend.document_new()
    await backend.entity_create_line(0, 0, 1, 0)

    assert backend.capabilities()["common.transaction.undo"]["supported"] is False
    with pytest.raises(UnsupportedCapabilityError) as excinfo:
        await backend.undo()
    assert excinfo.value.capability == "common.transaction.undo"
