"""Provider contract regression tests across AutoCAD backends.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 19:01
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from fastmcp import Client

from cdt_autocad import __version__
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.backends.ezdxf_backend import EzdxfBackend
from cdt_autocad.config import Settings
from cdt_autocad.contract_identity import (
    CONTRACT_VERSION,
    EXECUTION_MODEL,
    PUBLIC_TOOL_COUNT,
)
from cdt_autocad.server import _TOOL_DESCRIPTIONS, _validate_http_launch, create_mcp


def test_package_metadata_version_matches_runtime():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as stream:
        metadata = tomllib.load(stream)
    assert metadata["project"]["version"] == __version__


@pytest.mark.asyncio
async def test_generic_rc_tool_surface_is_bounded_and_explicit(settings):
    app = create_mcp(settings)
    async with Client(app) as client:
        names = {tool.name for tool in await client.list_tools()}

    assert names == set(_TOOL_DESCRIPTIONS)
    assert len(names) == PUBLIC_TOOL_COUNT == 86
    assert {
        "feature_execute",
        "native_integrity_status",
        "batch_create_entities",
        "batch_insert_blocks",
        "batch_transform_entities",
        "metadata_get",
        "metadata_set",
        "metadata_query",
        "document_dependencies",
        "artifact_seal",
        "xref_attach",
        "solid_export",
    } <= names


@pytest.mark.asyncio
async def test_public_tool_catalog_has_unique_semantic_descriptions_and_read_only_hints(settings):
    app = create_mcp(settings)
    async with Client(app) as client:
        tools = list(await client.list_tools())

    assert len(tools) == PUBLIC_TOOL_COUNT
    descriptions = {tool.name: tool.description for tool in tools}
    assert all(description and len(description) >= 40 for description in descriptions.values())
    assert len(set(descriptions.values())) == PUBLIC_TOOL_COUNT

    read_only_tools = {
        "help", "system_status", "system_capabilities", "document_info",
        "document_dependencies", "object_list", "object_get", "object_count",
        "object_query", "object_measure", "object_intersections", "drawing_extents",
        "layer_list", "block_list", "xref_list", "layout_list", "viewport_list",
        "view_screenshot", "solid_inspect", "native_integrity_status", "metadata_get",
        "metadata_query",
    }
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is (tool.name in read_only_tools)

    assert "verified" in descriptions["document_save"].lower()
    assert "wcs" in descriptions["entity_create_line"].lower()
    assert "degrees" in descriptions["object_rotate"].lower()
    assert "force" in descriptions["viewport_delete"].lower()
    assert "side effect" in descriptions["system_capabilities"].lower()


def test_backend_factory_selects_com(settings):
    app = create_mcp(replace(settings, backend="com"))
    assert isinstance(app._cdt_backend, ComBackend)
    assert app._cdt_backend.name == "com"


@pytest.mark.asyncio
async def test_com_system_status_exposes_primary_live_certification_target(settings):
    app = create_mcp(replace(settings, backend="com"))
    async with Client(app) as client:
        result = await client.call_tool("system_status", {})

    payload = result.structured_content or {}
    assert payload["live_certification"]["primary_release"] == "2027"
    assert payload["live_certification"]["primary_progid"] == "AutoCAD.Application.26"
    assert payload["application"] is None
    assert payload["implementation"] == {
        "state": "release_candidate",
        "public_enabled": True,
    }
    assert payload["runtime"] == {
        "platform_supported": sys.platform == "win32",
        "connected": False,
        "ready": False,
    }
    assert payload["certification"]["evidence_state"] == "historical_primary_target_pass"
    assert payload["certification"]["current_process_certified"] is False
    assert payload["certification"]["current_release_match"] is None
    assert payload["certification"]["evidence_reference"] == "docs/LIVE_ACCEPTANCE.md"
    assert payload["build_identity"] == {
        "provider_version": __version__,
        "contract_version": CONTRACT_VERSION,
        "public_tool_count": PUBLIC_TOOL_COUNT,
        "execution_model": EXECUTION_MODEL,
        "runtime_manifest_attached": False,
        "source_or_dll_claim_requires_manifest": True,
    }


def test_capability_keyset_is_stable_across_backends(settings):
    ezdxf_backend = EzdxfBackend(settings)
    com_backend = ComBackend(replace(settings, backend="com"))
    assert set(com_backend.capabilities()) == set(ezdxf_backend.capabilities())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "capability", "reason"),
    [
        ("fillet", "autocad.solid.edge_fillet", "no_deterministic_activex_subentity_api"),
        ("chamfer", "autocad.solid.edge_chamfer", "no_deterministic_activex_subentity_api"),
        ("shell", "autocad.solid.shell", "no_deterministic_activex_shell_api"),
    ],
)
async def test_com_solid_edge_refusals_are_locked_at_public_boundary(
    settings, operation, capability, reason
):
    app = create_mcp(replace(settings, backend="com"))
    async with Client(app) as client:
        capabilities_result = await client.call_tool("system_capabilities", {})
        payload = capabilities_result.structured_content or {}
        refusal = payload["capabilities"][capability]
        assert refusal["supported"] is False
        assert refusal["reason"] == reason

        result = await client.call_tool(
            "solid_transform",
            {"handle": "DO_NOT_TOUCH", "operation": operation, "parameters": {}},
            raise_on_error=False,
        )

    assert result.is_error is True
    error = result.structured_content or {}
    assert error["kind"] == "validation_error"
    assert error["retryable"] is False
    assert error["tool"] == "solid_transform"
    assert "move, rotate, scale, mirror" in error["error"]


@pytest.mark.asyncio
async def test_com_selection_keeps_the_same_bounded_generic_tool_surface(settings):
    app = create_mcp(replace(settings, backend="com"))
    async with Client(app) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert len(names) == PUBLIC_TOOL_COUNT
    assert "document_open" in names
    assert "undo" in names


@pytest.mark.asyncio
async def test_help_and_basic_workflow_over_real_mcp_client(settings, tmp_path: Path):
    app = create_mcp(settings)
    async with Client(app) as client:
        help_result = await client.call_tool("help", {})
        help_payload = help_result.structured_content or {}
        assert help_payload["provider_name"] == "autocad"
        assert help_payload["provider_version"] == "0.4.0rc1"
        assert help_payload["contract_version"] == CONTRACT_VERSION
        assert help_payload["public_tool_count"] == PUBLIC_TOOL_COUNT
        assert help_payload["execution_model"] == EXECUTION_MODEL
        assert len(help_payload["contract_hash"]) == 64
        assert "Feature-based Chunks Streaming" in help_payload["content"]

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
async def test_live_view_refusal_survives_mcp_boundary_on_ezdxf(settings):
    app = create_mcp(settings)
    async with Client(app) as client:
        result = await client.call_tool("viewport_list", {}, raise_on_error=False)

    assert result.is_error is True
    payload = result.structured_content or {}
    assert payload["kind"] == "unsupported_capability"
    assert payload["capability"] == "autocad.viewport.manage"
    assert payload["backend"] == "ezdxf"


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
