"""Provider contract regression tests across AutoCAD backends.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 14:18
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastmcp import Client

from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.config import Settings
from cdt_autocad.server import _validate_http_launch, create_mcp


@pytest.mark.asyncio
async def test_a1_tool_surface_is_bounded_and_explicit(settings):
    app = create_mcp(settings)
    async with Client(app) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names == {
        "help",
        "system_status",
        "system_capabilities",
        "document_new",
        "document_open",
        "document_info",
        "document_save",
        "document_save_as",
        "document_export_pdf",
        "drawing_audit",
        "drawing_purge",
        "object_list",
        "object_get",
        "object_count",
        "object_set_properties",
        "object_delete",
        "object_move",
        "object_copy",
        "object_rotate",
        "object_scale",
        "entity_create_line",
        "entity_create_circle",
        "entity_create_arc",
        "entity_create_polyline",
        "entity_create_text",
        "hatch_create",
        "dimension_linear",
        "dimension_aligned",
        "layer_list",
        "layer_create",
        "layer_set_current",
        "block_list",
        "block_create",
        "block_insert",
        "layout_list",
        "layout_create",
        "layout_set_current",
        "transaction_begin",
        "transaction_commit",
        "transaction_rollback",
        "undo",
        "redo",
    }
    assert len(names) == 42


def test_backend_factory_selects_com(settings):
    app = create_mcp(replace(settings, backend="com"))
    assert isinstance(app._cdt_backend, ComBackend)
    assert app._cdt_backend.name == "com"


def test_capability_keyset_is_stable_across_backends(settings):
    ezdxf_backend = EzdxfBackend(settings)
    com_backend = ComBackend(replace(settings, backend="com"))
    assert set(com_backend.capabilities()) == set(ezdxf_backend.capabilities())


@pytest.mark.asyncio
async def test_com_selection_keeps_the_same_bounded_42_tool_surface(settings):
    app = create_mcp(replace(settings, backend="com"))
    async with Client(app) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert len(names) == 42
    assert "document_open" in names
    assert "undo" in names


@pytest.mark.asyncio
async def test_help_and_basic_workflow_over_real_mcp_client(settings, tmp_path: Path):
    app = create_mcp(settings)
    async with Client(app) as client:
        help_result = await client.call_tool("help", {})
        help_payload = help_result.structured_content or {}
        assert help_payload["provider_name"] == "autocad"
        assert help_payload["provider_version"] == "0.2.0"
        assert help_payload["contract_version"] == "autocad-a1-v1"
        assert len(help_payload["contract_hash"]) == 64
        assert "A1 42-tool surface" in help_payload["content"]

        await client.call_tool("document_new", {})
        created = await client.call_tool(
            "entity_create_line", {"x1": 0, "y1": 0, "x2": 10, "y2": 0}
        )
        assert created.is_error is False
        assert created.structured_content["type"] == "LINE"

        moved = await client.call_tool(
            "object_move",
            {"object_id": created.structured_content["id"], "dx": 3, "dy": 4},
        )
        assert moved.is_error is False
        assert moved.structured_content["properties"]["start"] == [3.0, 4.0, 0.0]

        count = await client.call_tool("object_count", {})
        assert (count.structured_content or {}).get("result") == 1

        target = tmp_path / "client-roundtrip.dxf"
        saved = await client.call_tool("document_save_as", {"path": str(target)})
        assert saved.is_error is False
        assert target.exists()


@pytest.mark.asyncio
async def test_dwg_refusal_survives_mcp_boundary(settings, tmp_path: Path):
    app = create_mcp(settings)
    async with Client(app) as client:
        await client.call_tool("document_new", {})
        result = await client.call_tool(
            "document_save_as",
            {"path": str(tmp_path / "forbidden.dwg")},
            raise_on_error=False,
        )

    assert result.is_error is True
    payload = result.structured_content or {}
    assert payload["kind"] == "unsupported_capability"
    assert payload["capability"] == "autocad.dwg.write"
    assert payload["backend"] == "ezdxf"
    assert not (tmp_path / "forbidden.dwg").exists()


@pytest.mark.asyncio
async def test_state_conflict_survives_mcp_boundary(settings):
    app = create_mcp(settings)
    async with Client(app) as client:
        result = await client.call_tool("undo", {}, raise_on_error=False)

    assert result.is_error is True
    payload = result.structured_content or {}
    assert payload["kind"] == "conflict"
    assert "Nothing to undo" in payload["error"] or "No document" in payload["error"]


def test_http_transport_fails_closed_without_token(tmp_path: Path):
    settings = Settings(allowed_paths=(tmp_path,), auth_token="")
    with pytest.raises(SystemExit, match="AUTH_TOKEN"):
        _validate_http_launch(settings, "127.0.0.1")


def test_remote_http_requires_explicit_opt_in(tmp_path: Path):
    settings = Settings(allowed_paths=(tmp_path,), auth_token="configured-at-runtime")
    with pytest.raises(SystemExit, match="non-loopback"):
        _validate_http_launch(settings, "0.0.0.0")


def test_remote_http_allowed_when_both_controls_are_present(tmp_path: Path):
    settings = Settings(
        allowed_paths=(tmp_path,),
        auth_token="configured-at-runtime",
        allow_remote_http=True,
    )
    _validate_http_launch(settings, "0.0.0.0")
