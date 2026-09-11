"""FastMCP entrypoint for the CDT_Engineer AutoCAD provider.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 16:13
"""

from __future__ import annotations

import argparse
import hashlib
from importlib import resources
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult
from fastmcp.utilities.types import Image

from . import __version__
from .backends.base import AutoCADBackend
from .backends.com_backend import ComBackend
from .backends.ezdxf_backend import EzdxfBackend
from .config import Settings
from .diagnostics import DiagnosticSink, ProviderDiagnosticMiddleware
from .errors import (
    BackendQuarantinedError,
    BackendTimeoutError,
    StateConflictError,
    UnsupportedCapabilityError,
)

_CONTRACT_VERSION = "autocad-a2-v1-rc1"
_COMMON_CONTRACT_VERSION = "cdt-common-v1-draft"
_UPDATED_AT = "2026-09-11"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_HTTP_TRANSPORTS = {"http", "sse", "streamable-http"}

_TOOL_DESCRIPTIONS = {
    "help": (
        "Return provider contract guidance, workflow rules, capability map, and refusal notes; "
        "this tool has no CAD side effects."
    ),
    "system_status": (
        "Report provider/backend runtime status and certification target metadata; availability "
        "here is not permission to invoke unsupported capabilities."
    ),
    "system_capabilities": (
        "Return the backend capability map used to plan calls before side effects; capability "
        "metadata is descriptive and never an authorization bypass."
    ),
    "document_new": (
        "Create a new active CAD document in the current backend session; refuses unsafe "
        "transaction state and changes session document state."
    ),
    "document_open": (
        "Open an existing allowed DWG/DXF path as the active document; this changes session state "
        "and must not be blindly retried after unknown completion."
    ),
    "document_info": (
        "Read verified metadata for the active document, including path, units, counts, current "
        "space, backend, and detected AutoCAD release when available."
    ),
    "document_save": (
        "Save the currently bound document to its verified existing path, or delegate to SaveAs "
        "when path is supplied; mutation timeout must not be blindly retried."
    ),
    "document_save_as": (
        "Save the active document to an allowed DWG/DXF destination and update its document "
        "scope; backend format capability and path policy are enforced."
    ),
    "document_export_pdf": (
        "Export the active document or selected layout to an allowed PDF path; this writes an "
        "artifact and may require live AutoCAD plotting capability."
    ),
    "drawing_audit": (
        "Run the backend drawing-audit operation on the active document; live AutoCAD AUDIT may "
        "repair content and does not expose a machine-readable repair count."
    ),
    "drawing_purge": (
        "Purge unused drawing resources from the active document; this mutates document state and "
        "should be used only when removal is intended."
    ),
    "object_list": (
        "List objects from the backend's current supported space with optional type/layer filters "
        "and bounded pagination; returned IDs are backend object identifiers."
    ),
    "object_get": (
        "Read one object by backend object identifier and return normalized type, layer, color, "
        "visibility, geometry properties, and current-space metadata."
    ),
    "object_count": (
        "Count objects in the backend's current supported space with optional type and layer "
        "filters; no document geometry is modified."
    ),
    "object_set_properties": (
        "Update selected properties of one object—layer, color, linetype, or visibility—while "
        "leaving unspecified properties unchanged."
    ),
    "object_delete": (
        "Delete one object by backend object identifier from the active document; this is "
        "destructive and timeout uncertainty must be reconciled before retry."
    ),
    "object_move": (
        "Move one object by WCS delta dx/dy/dz using drawing units and return the normalized "
        "updated entity; this mutates geometry."
    ),
    "object_copy": (
        "Copy one object by WCS delta dx/dy/dz using drawing units and return the new entity "
        "identifier without deleting the source."
    ),
    "object_rotate": (
        "Rotate one object around the supplied WCS base point by angle_deg in degrees; this "
        "mutates geometry in the active document."
    ),
    "object_scale": (
        "Scale one object uniformly about the supplied WCS base point by a positive factor; this "
        "mutates geometry in the active document."
    ),
    "entity_create_line": (
        "Create a LINE from two WCS endpoints in drawing units, optionally on a layer/color, and "
        "return the new backend object identifier."
    ),
    "entity_create_circle": (
        "Create a CIRCLE at a WCS center with positive radius in drawing units, optionally on a "
        "layer/color, and return the new object identifier."
    ),
    "entity_create_arc": (
        "Create an ARC at a WCS center with positive radius; start_angle and end_angle are degrees "
        "and the result is a new drawing object."
    ),
    "entity_create_polyline": (
        "Create a 2D lightweight polyline from WCS XY points in drawing units, optionally "
        "closed/layered/colored, and return the new object identifier."
    ),
    "entity_create_text": (
        "Create TEXT at a WCS insertion point with drawing-unit height and rotation in degrees, "
        "optionally on a layer/color, and return its identifier."
    ),
    "hatch_create": (
        "Create a hatch from a closed WCS XY boundary using pattern, scale, and angle in degrees; "
        "invalid or unsupported boundaries are refused."
    ),
    "dimension_linear": (
        "Create a linear dimension from WCS extension points and dimension-line point; rotation is "
        "degrees and placement uses drawing units."
    ),
    "dimension_aligned": (
        "Create an aligned dimension from WCS extension points and a WCS dimension-line point, "
        "using the active drawing's dimension style."
    ),
    "layer_list": (
        "List drawing layers with normalized names, colors, linetypes, visibility/frozen/locked "
        "state, and whether each layer is current."
    ),
    "layer_create": (
        "Create a new layer with the requested AutoCAD color index; existing names are refused and "
        "the active layer is not changed."
    ),
    "layer_set_current": (
        "Set an existing layer as the active document layer; this changes session drawing state but "
        "does not move existing objects between layers."
    ),
    "block_list": (
        "List named block definitions, excluding anonymous blocks, with base point, entity count, "
        "attribute-definition count, and xref status."
    ),
    "block_create": (
        "Create a named block definition by copying the specified existing objects around a WCS "
        "base point; source objects remain in their original space."
    ),
    "block_insert": (
        "Insert an existing named block at a WCS point with XY scale and rotation in degrees, "
        "optionally assigning the new reference to a layer."
    ),
    "layout_list": (
        "List document layouts and identify the current layout; this is read-only and does not "
        "change active model/paper space."
    ),
    "layout_create": (
        "Create a new paper-space layout by name; duplicate or empty names are refused and existing "
        "layouts are preserved."
    ),
    "layout_set_current": (
        "Switch the active document to an existing layout; this changes the operator's current "
        "model/paper-space context."
    ),
    "viewport_create": (
        "Create a paper-space viewport on a named layout with center/size in paper units, WCS view "
        "target, and positive custom scale."
    ),
    "viewport_list": (
        "List paper-space viewports for one or all layouts with handle, geometry, target, scale, "
        "lock state, and backend-created ownership hint."
    ),
    "viewport_set_scale": (
        "Set a paper-space viewport's positive custom scale by handle and return the verified scale "
        "and derived view height."
    ),
    "viewport_lock": (
        "Set or clear DisplayLocked for a paper-space viewport by handle and return the verified "
        "lock state."
    ),
    "viewport_delete": (
        "Delete a paper-space viewport by handle; pre-existing or unknown viewports require "
        "force=true because ActiveX cannot reliably identify the main viewport."
    ),
    "view_zoom_extents": (
        "Zoom the live AutoCAD view to drawing extents; this changes only the operator view and "
        "does not modify drawing geometry."
    ),
    "view_zoom_window": (
        "Zoom the live AutoCAD view to the supplied WCS XY window bounds; zero-area windows are "
        "refused and drawing geometry is unchanged."
    ),
    "view_screenshot": (
        "Capture the live AutoCAD application window as PNG bytes; this is read-only and requires "
        "the live COM screenshot capability."
    ),
    "transaction_begin": (
        "Begin a backend-tracked transaction or undo scope on the bound document; nested depth is "
        "limited and document switching is blocked while active."
    ),
    "transaction_commit": (
        "Commit the current backend-tracked transaction or undo scope for the bound document; "
        "refuses when no tracked transaction is active."
    ),
    "transaction_rollback": (
        "Rollback the current backend-tracked transaction or undo scope for the bound document; "
        "this intentionally reverts mutations in that scope."
    ),
    "undo": (
        "Undo the most recent available backend/document operation according to backend history "
        "semantics; refuses when no undoable state is available."
    ),
    "redo": (
        "Redo the most recent reverted backend/document operation according to backend history "
        "semantics; refuses when no redoable state is available."
    ),
}


def _guide_content() -> str:
    source_path = Path(__file__).resolve().parents[2] / "docs" / "TOOL_GUIDE.md"
    if source_path.is_file():
        return source_path.read_text(encoding="utf-8")
    packaged = resources.files("cdt_autocad").joinpath("docs").joinpath("TOOL_GUIDE.md")
    return packaged.read_text(encoding="utf-8")


def _help_payload(backend: AutoCADBackend) -> dict[str, Any]:
    content = _guide_content()
    return {
        "provider_name": "autocad",
        "provider_version": __version__,
        "protocol_version": "MCP",
        "contract_version": _CONTRACT_VERSION,
        "common_contract_version": _COMMON_CONTRACT_VERSION,
        "provider_extension_version": _CONTRACT_VERSION,
        "contract_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "updated_at": _UPDATED_AT,
        "authentication": "Bearer token required for HTTP transport; credentials are never returned",
        "capabilities": backend.capabilities(),
        "content": content,
    }


def _status_contract(backend: AutoCADBackend, backend_status: dict[str, Any]) -> dict[str, Any]:
    implementation = {
        "state": "release_candidate",
        "public_enabled": True,
    }
    if backend.name == "com":
        application = backend_status.get("application") or None
        current_release_match = (
            None
            if application is None
            else str(application.get("release") or "") == "2027"
        )
        runtime = {
            "platform_supported": bool(backend_status.get("runtime_available")),
            "connected": bool(backend_status.get("connected")),
            "ready": bool(
                backend_status.get("runtime_available")
                and backend_status.get("connected")
                and application is not None
            ),
        }
        certification = {
            "evidence_state": "historical_primary_target_pass",
            "current_process_certified": False,
            "current_release_match": current_release_match,
            "evidence_reference": "docs/LIVE_ACCEPTANCE.md",
        }
    else:
        runtime = {
            "platform_supported": True,
            "connected": True,
            "ready": True,
        }
        certification = {
            "evidence_state": "headless_regression_only",
            "current_process_certified": False,
            "current_release_match": None,
            "evidence_reference": None,
        }
    return {
        "implementation": implementation,
        "runtime": runtime,
        "certification": certification,
        "build_identity": {
            "provider_version": __version__,
            "contract_version": _CONTRACT_VERSION,
            "runtime_manifest_attached": False,
            "source_or_dll_claim_requires_manifest": True,
        },
    }


def _error_chain(exc: Exception) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _classify_error(exc: Exception) -> tuple[str, dict[str, Any], str] | None:
    chain = _error_chain(exc)
    for item in chain:
        if isinstance(item, UnsupportedCapabilityError):
            return (
                "unsupported_capability",
                {"capability": item.capability, "retryable": False},
                str(item),
            )
    for item in chain:
        if isinstance(item, BackendQuarantinedError):
            return (
                "conflict",
                {"reason": "document_quarantined", "retryable": False},
                str(item),
            )
        if isinstance(item, StateConflictError):
            return "conflict", {"retryable": False}, str(item)
        if isinstance(item, BackendTimeoutError):
            return (
                "timeout",
                {
                    "retryable": item.retryable,
                    "completion_unknown": item.completion_unknown,
                },
                str(item),
            )
        if isinstance(item, FileNotFoundError | KeyError):
            return "not_found", {"retryable": False}, str(item).strip("'")
        if isinstance(item, ValueError | TypeError):
            return "validation_error", {"retryable": False}, str(item)
    for item in chain:
        if isinstance(item, RuntimeError):
            return "provider_unavailable", {"retryable": False}, str(item)
    return None


class ProviderErrorMiddleware(Middleware):
    """Convert expected provider errors into stable machine-readable MCP errors."""

    def __init__(self, backend: AutoCADBackend):
        self.backend = backend

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        try:
            return await call_next(context)
        except Exception as exc:
            classified = _classify_error(exc)
            if classified is None:
                raise
            kind, extra, message = classified
            payload = {
                "ok": False,
                "kind": kind,
                "error": message,
                "tool": context.message.name,
                "backend": self.backend.name,
                **extra,
            }
            return ToolResult(content=message, structured_content=payload, is_error=True)


def create_mcp(
    settings: Settings | None = None,
    *,
    diagnostic_sink: DiagnosticSink | None = None,
) -> FastMCP:
    settings = settings or Settings.from_env()
    backend: AutoCADBackend
    if settings.backend == "com":
        backend = ComBackend(settings)
    else:
        backend = EzdxfBackend(settings)
    auth = None
    if settings.auth_token:
        auth = StaticTokenVerifier(
            tokens={
                settings.auth_token: {
                    "client_id": "cdt-autocad-client",
                    "scopes": ["mcp:read", "mcp:write"],
                }
            }
        )

    app = FastMCP(
        name="CDT AutoCAD",
        auth=auth,
        instructions=(
            "CDT_Engineer AutoCAD provider. Use system_capabilities before relying on "
            "backend-specific features. The ezdxf backend is headless/DXF-first; the COM backend "
            "controls a live Windows AutoCAD session and supports native DWG when available."
        ),
    )
    app.add_middleware(
        ProviderDiagnosticMiddleware(
            backend_name=backend.name,
            provider_version=__version__,
            sink=diagnostic_sink,
        )
    )
    app.add_middleware(ProviderErrorMiddleware(backend))

    def provider_tool(*, tags: set[str]):
        def decorate(fn):
            name = fn.__name__
            description = _TOOL_DESCRIPTIONS.get(name)
            if description is None:
                raise RuntimeError(f"Missing public tool description: {name}")
            read_only = "read" in tags and "write" not in tags and "export" not in tags
            return app.tool(
                tags=tags,
                description=description,
                annotations={"readOnlyHint": read_only},
            )(fn)

        return decorate

    @provider_tool(tags={"identity", "read"})
    async def help() -> dict[str, Any]:
        return _help_payload(backend)

    @provider_tool(tags={"identity", "read"})
    async def system_status() -> dict[str, Any]:
        backend_status = backend.status()
        return {
            "provider": "autocad",
            "provider_version": __version__,
            "contract_version": _CONTRACT_VERSION,
            **backend_status,
            **_status_contract(backend, backend_status),
        }

    @provider_tool(tags={"identity", "read"})
    async def system_capabilities() -> dict[str, Any]:
        return {
            "provider": "autocad",
            "backend": backend.name,
            "common_contract_version": _COMMON_CONTRACT_VERSION,
            "provider_extension_version": _CONTRACT_VERSION,
            "capabilities": backend.capabilities(),
        }

    @provider_tool(tags={"document", "write"})
    async def document_new() -> dict[str, Any]:
        return await backend.document_new()

    @provider_tool(tags={"document", "write"})
    async def document_open(path: str) -> dict[str, Any]:
        return await backend.document_open(path)

    @provider_tool(tags={"document", "read"})
    async def document_info() -> dict[str, Any]:
        return await backend.document_info()

    @provider_tool(tags={"document", "write"})
    async def document_save(path: str | None = None) -> dict[str, Any]:
        return await backend.document_save(path)

    @provider_tool(tags={"document", "write"})
    async def document_save_as(path: str) -> dict[str, Any]:
        return await backend.document_save_as(path)

    @provider_tool(tags={"document", "export"})
    async def document_export_pdf(path: str, layout: str | None = None) -> dict[str, Any]:
        return await backend.document_export_pdf(path, layout)

    @provider_tool(tags={"document", "write"})
    async def drawing_audit() -> dict[str, Any]:
        return await backend.drawing_audit()

    @provider_tool(tags={"document", "write"})
    async def drawing_purge() -> dict[str, Any]:
        return await backend.drawing_purge()

    @provider_tool(tags={"object", "read"})
    async def object_list(
        type_filter: str | None = None,
        layer_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await backend.object_list(type_filter, layer_filter, limit, offset)
        return [row.to_dict() for row in rows]

    @provider_tool(tags={"object", "read"})
    async def object_get(object_id: str) -> dict[str, Any]:
        return (await backend.object_get(object_id)).to_dict()

    @provider_tool(tags={"object", "read"})
    async def object_count(
        type_filter: str | None = None,
        layer_filter: str | None = None,
    ) -> int:
        return await backend.object_count(type_filter, layer_filter)

    @provider_tool(tags={"object", "write"})
    async def object_set_properties(
        object_id: str,
        layer: str | None = None,
        color: int | None = None,
        linetype: str | None = None,
        visible: bool | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.object_set_properties(object_id, layer, color, linetype, visible)
        ).to_dict()

    @provider_tool(tags={"object", "write"})
    async def object_delete(object_id: str) -> dict[str, Any]:
        return await backend.object_delete(object_id)

    @provider_tool(tags={"object", "write"})
    async def object_move(
        object_id: str, dx: float, dy: float, dz: float = 0.0
    ) -> dict[str, Any]:
        return (await backend.object_move(object_id, dx, dy, dz)).to_dict()

    @provider_tool(tags={"object", "write"})
    async def object_copy(
        object_id: str, dx: float, dy: float, dz: float = 0.0
    ) -> dict[str, Any]:
        return (await backend.object_copy(object_id, dx, dy, dz)).to_dict()

    @provider_tool(tags={"object", "write"})
    async def object_rotate(
        object_id: str, base_x: float, base_y: float, angle_deg: float
    ) -> dict[str, Any]:
        return (await backend.object_rotate(object_id, base_x, base_y, angle_deg)).to_dict()

    @provider_tool(tags={"object", "write"})
    async def object_scale(
        object_id: str, base_x: float, base_y: float, factor: float
    ) -> dict[str, Any]:
        return (await backend.object_scale(object_id, base_x, base_y, factor)).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_line(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        z1: float = 0.0,
        z2: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.entity_create_line(x1, y1, x2, y2, z1, z2, layer, color)
        ).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_circle(
        cx: float,
        cy: float,
        radius: float,
        layer: str | None = None,
        color: int | None = None,
    ) -> dict[str, Any]:
        return (await backend.entity_create_circle(cx, cy, radius, layer, color)).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_arc(
        cx: float,
        cy: float,
        radius: float,
        start_angle: float,
        end_angle: float,
        layer: str | None = None,
        color: int | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.entity_create_arc(
                cx, cy, radius, start_angle, end_angle, layer, color
            )
        ).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_polyline(
        points: list[list[float]],
        closed: bool = False,
        layer: str | None = None,
        color: int | None = None,
    ) -> dict[str, Any]:
        return (await backend.entity_create_polyline(points, closed, layer, color)).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_text(
        text: str,
        x: float,
        y: float,
        height: float = 2.5,
        rotation: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.entity_create_text(text, x, y, height, rotation, layer, color)
        ).to_dict()

    @provider_tool(tags={"hatch", "write"})
    async def hatch_create(
        boundary_points: list[list[float]],
        pattern: str = "SOLID",
        scale: float = 1.0,
        angle: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.hatch_create(boundary_points, pattern, scale, angle, layer, color)
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_linear(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        dim_x: float,
        dim_y: float,
        rotation: float = 0.0,
        layer: str | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_linear(x1, y1, x2, y2, dim_x, dim_y, rotation, layer)
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_aligned(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        dim_x: float,
        dim_y: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_aligned(x1, y1, x2, y2, dim_x, dim_y, layer)
        ).to_dict()

    @provider_tool(tags={"layer", "read"})
    async def layer_list() -> list[dict[str, Any]]:
        return [layer.to_dict() for layer in await backend.layer_list()]

    @provider_tool(tags={"layer", "write"})
    async def layer_create(name: str, color: int = 7) -> dict[str, Any]:
        return (await backend.layer_create(name, color)).to_dict()

    @provider_tool(tags={"layer", "write"})
    async def layer_set_current(name: str) -> dict[str, Any]:
        return await backend.layer_set_current(name)

    @provider_tool(tags={"block", "read"})
    async def block_list() -> list[dict[str, Any]]:
        return [block.to_dict() for block in await backend.block_list()]

    @provider_tool(tags={"block", "write"})
    async def block_create(
        name: str,
        object_ids: list[str],
        base_x: float = 0.0,
        base_y: float = 0.0,
    ) -> dict[str, Any]:
        return (await backend.block_create(name, object_ids, base_x, base_y)).to_dict()

    @provider_tool(tags={"block", "write"})
    async def block_insert(
        name: str,
        x: float,
        y: float,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        rotation: float = 0.0,
        layer: str | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.block_insert(name, x, y, scale_x, scale_y, rotation, layer)
        ).to_dict()

    @provider_tool(tags={"layout", "read"})
    async def layout_list() -> dict[str, Any]:
        return await backend.layout_list()

    @provider_tool(tags={"layout", "write"})
    async def layout_create(name: str) -> dict[str, Any]:
        return await backend.layout_create(name)

    @provider_tool(tags={"layout", "write"})
    async def layout_set_current(name: str) -> dict[str, Any]:
        return await backend.layout_set_current(name)

    @provider_tool(tags={"viewport", "write"})
    async def viewport_create(
        layout: str,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
        view_center_x: float,
        view_center_y: float,
        scale: float = 1.0,
    ) -> dict[str, Any]:
        return await backend.viewport_create(
            layout,
            center_x,
            center_y,
            width,
            height,
            view_center_x,
            view_center_y,
            scale,
        )

    @provider_tool(tags={"viewport", "read"})
    async def viewport_list(layout: str | None = None) -> dict[str, Any]:
        return await backend.viewport_list(layout)

    @provider_tool(tags={"viewport", "write"})
    async def viewport_set_scale(handle: str, scale: float) -> dict[str, Any]:
        return await backend.viewport_set_scale(handle, scale)

    @provider_tool(tags={"viewport", "write"})
    async def viewport_lock(handle: str, locked: bool = True) -> dict[str, Any]:
        return await backend.viewport_lock(handle, locked)

    @provider_tool(tags={"viewport", "write"})
    async def viewport_delete(handle: str, force: bool = False) -> dict[str, Any]:
        return await backend.viewport_delete(handle, force)

    @provider_tool(tags={"view", "write"})
    async def view_zoom_extents() -> dict[str, Any]:
        return await backend.view_zoom_extents()

    @provider_tool(tags={"view", "write"})
    async def view_zoom_window(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> dict[str, Any]:
        return await backend.view_zoom_window(x1, y1, x2, y2)

    @provider_tool(tags={"view", "read"})
    async def view_screenshot() -> Image:
        return Image(data=await backend.view_screenshot(), format="png")

    @provider_tool(tags={"transaction", "write"})
    async def transaction_begin() -> dict[str, Any]:
        return await backend.transaction_begin()

    @provider_tool(tags={"transaction", "write"})
    async def transaction_commit() -> dict[str, Any]:
        return await backend.transaction_commit()

    @provider_tool(tags={"transaction", "write"})
    async def transaction_rollback() -> dict[str, Any]:
        return await backend.transaction_rollback()

    @provider_tool(tags={"transaction", "write"})
    async def undo() -> dict[str, Any]:
        return await backend.undo()

    @provider_tool(tags={"transaction", "write"})
    async def redo() -> dict[str, Any]:
        return await backend.redo()

    app._cdt_backend = backend  # type: ignore[attr-defined]
    app._cdt_settings = settings  # type: ignore[attr-defined]
    return app


def _validate_http_launch(settings: Settings, host: str) -> None:
    if not settings.auth_token:
        raise SystemExit(
            "Refusing HTTP transport without CDT_AUTOCAD_AUTH_TOKEN; use stdio or configure auth"
        )
    if host not in _LOOPBACK_HOSTS and not settings.allow_remote_http:
        raise SystemExit(
            "Refusing non-loopback HTTP bind; set CDT_AUTOCAD_ALLOW_REMOTE_HTTP=true explicitly"
        )


mcp = create_mcp()
_original_run_async = mcp.run_async


async def _guarded_run_async(transport=None, *args, **kwargs):
    selected = transport or kwargs.get("transport")
    if selected in _HTTP_TRANSPORTS:
        host = str(kwargs.get("host") or "127.0.0.1")
        _validate_http_launch(mcp._cdt_settings, host)  # type: ignore[attr-defined]
    return await _original_run_async(transport, *args, **kwargs)


mcp.run_async = _guarded_run_async


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CDT AutoCAD MCP provider")
    parser.add_argument("--transport", default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport in _HTTP_TRANSPORTS:
        _validate_http_launch(mcp._cdt_settings, args.host)  # type: ignore[attr-defined]
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    else:
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
