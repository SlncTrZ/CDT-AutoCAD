"""FastMCP entrypoint for the CDT_Engineer AutoCAD provider.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 16:13
"""

from __future__ import annotations

import argparse
import asyncio
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
from .contract_identity import (
    COMMON_CONTRACT_VERSION as _COMMON_CONTRACT_VERSION,
    CONTRACT_VERSION as _CONTRACT_VERSION,
    EXECUTION_MODEL as _EXECUTION_MODEL,
    PROTOCOL_VERSION as _PROTOCOL_VERSION,
    PUBLIC_TOOL_COUNT as _PUBLIC_TOOL_COUNT,
    UPDATED_AT as _UPDATED_AT,
    contract_hash as _contract_hash,
    contract_material as _contract_material,
)
from .diagnostics import DiagnosticSink, ProviderDiagnosticMiddleware
from .errors import (
    BackendQuarantinedError,
    BackendTimeoutError,
    StateConflictError,
    UnsupportedCapabilityError,
)
from .native_bridge.public_runtime import NativePublicFacade
from .runtime_identity import RuntimeIdentity

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
    "document_configure_units": (
        "Configure drawing insertion units, measurement system, linear format, and precision using "
        "a bounded typed schema instead of arbitrary AutoCAD system-variable access."
    ),
    "document_dependencies": (
        "Inspect external-reference dependencies for the active drawing and report contained, "
        "resolved, loaded, and completeness state without silently following outside-root paths."
    ),
    "artifact_seal": (
        "Save the active drawing, require a clean persisted state, copy it to a content-addressed "
        "accepted artifact, and write a SHA-256 manifest for stable handoff provenance."
    ),
    "object_query": (
        "Query a bounded object set by type/layer and optional WCS bounding window, returning normalized "
        "objects plus measured bounds so source-registration workflows do not need side-channel DXF export."
    ),
    "layer_update_state": (
        "Update allowlisted layer state such as on/off, frozen, locked, color, linetype, or lineweight; "
        "unsafe changes to the current layer are refused before mutation."
    ),
    "xref_list": (
        "List live AutoCAD external references with contained/resolved/loaded state while redacting paths "
        "that fall outside configured allowed roots."
    ),
    "xref_attach": (
        "Attach or overlay one allowed DWG/DXF external reference with a bounded WCS transform and optional "
        "layer; source paths are canonicalized before AutoCAD side effects."
    ),
    "xref_reload": (
        "Reload one existing XREF only when its resolved source remains inside configured allowed roots."
    ),
    "xref_unload": (
        "Unload one existing XREF definition without deleting it, preserving a reversible reference state."
    ),
    "xref_detach": (
        "Detach one existing XREF definition from the active document; this is destructive to reference state."
    ),
    "view_set_direction": (
        "Set a normalized 3D model-space view direction from a WCS vector and zoom to extents; this may alter "
        "persisted view state even though it does not modify CAD geometry."
    ),
    "view_set_preset": (
        "Apply an allowlisted orthographic or isometric 3D view preset such as se_isometric using typed "
        "direction vectors rather than free-text AutoCAD commands."
    ),
    "view_set_visual_style": (
        "Apply an allowlisted model-space visual style such as shades_of_gray through a bounded internal "
        "AutoCAD command mapping; arbitrary command text is never accepted from callers."
    ),
    "dimension_angular": (
        "Create a typed angular dimension from one vertex, two WCS ray points, and a text point; invalid "
        "collinear rays are rejected before mutation."
    ),
    "dimension_radial": (
        "Create a typed radial dimension from center/chord geometry and positive leader length using the "
        "active drawing dimension style."
    ),
    "dimension_diametric": (
        "Create a typed diametric dimension from center/chord geometry and positive leader length using the "
        "active drawing dimension style."
    ),
    "dimension_ordinate": (
        "Create an X- or Y-ordinate dimension from a WCS definition point and leader endpoint with strict "
        "axis validation."
    ),
    "object_measure": (
        "Measure one object using the active backend's exact supported geometry API and return type-specific "
        "metrics plus WCS bounding box for deterministic verification."
    ),
    "drawing_extents": (
        "Measure the current drawing-space WCS extents using the backend geometry engine without changing "
        "document objects."
    ),
    "object_intersections": (
        "Compute exact supported intersections between two distinct objects with an explicit extension mode; "
        "unsupported solvers fail closed rather than approximate silently."
    ),
    "solid_create_primitive": (
        "Create a native ACIS BOX, CYLINDER, SPHERE, CONE, TORUS, or WEDGE from typed numeric parameters; "
        "the live AutoCAD backend is required and an optional output layer is applied explicitly."
    ),
    "solid_extrude": (
        "Create an ACIS 3DSOLID by extruding a closed planar profile by non-zero height and bounded taper angle; "
        "temporary regions are cleaned deterministically."
    ),
    "solid_sweep": (
        "Create an ACIS 3DSOLID by sweeping a closed profile along a supported typed CAD curve path; unsupported "
        "path types are refused before opaque AutoCAD failures."
    ),
    "solid_revolve": (
        "Create an ACIS 3DSOLID by revolving a closed planar profile around a non-degenerate WCS 3D axis by a "
        "non-zero angle within plus/minus 360 degrees."
    ),
    "solid_boolean": (
        "Apply UNION, SUBTRACT, or INTERSECT between two distinct ACIS 3DSOLIDs and return post-operation volume, "
        "centroid, bounding box, and tool-consumption state."
    ),
    "solid_transform": (
        "Apply one typed 3D move, rotate, uniform-scale, or mirror operation to an ACIS solid; free-form matrices "
        "and arbitrary commands are not accepted."
    ),
    "solid_inspect": (
        "Inspect an ACIS 3DSOLID by handle and return layer, visibility, volume, centroid, and WCS bounding box; "
        "face-level topology is reported as unsupported when it cannot be proven through ActiveX."
    ),
    "solid_export": (
        "Export selected native ACIS solids to a contained SAT artifact using AutoCAD's typed Export API and "
        "return SHA-256 provenance; unsupported STEP/STL requests are refused rather than emulated unsafely."
    ),
    "native_integrity_status": (
        "Inspect the same-session managed native bridge and the exact active AutoCAD document binding, including "
        "persistent document PID and semantic fingerprint; this route never falls back to ordinary COM mutation."
    ),
    "feature_execute": (
        "Execute one complete generic feature as a feature-local logical transaction. The feature may mix typed create, "
        "block-insert, and transform actions; native micro-chunks remain bounded and only the current feature rolls back "
        "on failure. Domain meaning stays outside CDT-AutoCAD and successful receipts recommend 300 ms presentation pacing."
    ),
    "batch_create_entities": (
        "Create 1..10000 generic typed CAD primitives through the managed native bridge using bounded chunks, one "
        "AutoCAD Idle yield between chunks, and one logical predecessor checkpoint for all-or-nothing G3 recovery."
    ),
    "batch_insert_blocks": (
        "Insert 1..10000 persistent-PID-bound block references through the managed native bridge using bounded chunks "
        "and one G3 logical predecessor checkpoint; no weaker COM fallback is permitted."
    ),
    "batch_transform_entities": (
        "Transform 1..10000 persistent semantic PIDs with one typed translate, rotate-Z, or uniform-scale operation through "
        "the managed native bridge; bounded chunks remain logically atomic through exact predecessor recovery."
    ),
    "metadata_get": (
        "Read one schema-agnostic metadata namespace from a persistent semantic PID through the native bridge. Provider "
        "identity/recovery namespaces are reserved and domain schemas remain outside CDT-AutoCAD."
    ),
    "metadata_set": (
        "Set one bounded schema-agnostic JSON metadata namespace on a persistent semantic PID with fingerprint guard, "
        "provisional validation, independent read-back, and exact recovery; no COM fallback is used."
    ),
    "metadata_query": (
        "Query a bounded schema-agnostic metadata namespace across the active native document, optionally matching one "
        "dotted JSON path to an exact JSON value; results are capped and never interpreted as domain rules."
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


def _help_payload(backend: AutoCADBackend) -> dict[str, Any]:
    content, contract_hash = _contract_material()
    return {
        "provider_name": "autocad",
        "provider_version": __version__,
        "protocol_version": _PROTOCOL_VERSION,
        "contract_version": _CONTRACT_VERSION,
        "common_contract_version": _COMMON_CONTRACT_VERSION,
        "provider_extension_version": _CONTRACT_VERSION,
        "contract_hash": contract_hash,
        "public_tool_count": _PUBLIC_TOOL_COUNT,
        "execution_model": _EXECUTION_MODEL,
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
            "public_tool_count": _PUBLIC_TOOL_COUNT,
            "execution_model": _EXECUTION_MODEL,
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
    runtime_identity = RuntimeIdentity.from_env()
    backend: AutoCADBackend
    if settings.backend == "com":
        backend = ComBackend(settings)
    else:
        backend = EzdxfBackend(settings)
    native_facade = NativePublicFacade(settings)
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
            "controls a live Windows AutoCAD session and supports native DWG when available. For production domain "
            "workflows, prefer feature_execute so each logical feature commits or rolls back independently while "
            "previously accepted features remain intact."
        ),
    )
    app.add_middleware(
        ProviderDiagnosticMiddleware(
            backend_name=backend.name,
            provider_version=__version__,
            sink=diagnostic_sink,
            generation=runtime_identity.generation,
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
        contract_hash = _contract_hash()
        return {
            "provider": "autocad",
            "provider_version": __version__,
            "protocol_version": _PROTOCOL_VERSION,
            "contract_version": _CONTRACT_VERSION,
            "contract_hash": contract_hash,
            **backend_status,
            **_status_contract(backend, backend_status),
            **runtime_identity.status(backend_name=backend.name),
        }

    @provider_tool(tags={"identity", "read"})
    async def system_capabilities() -> dict[str, Any]:
        return {
            "provider": "autocad",
            "backend": backend.name,
            "common_contract_version": _COMMON_CONTRACT_VERSION,
            "provider_extension_version": _CONTRACT_VERSION,
            "public_tool_count": _PUBLIC_TOOL_COUNT,
            "execution_model": _EXECUTION_MODEL,
            "capabilities": backend.capabilities(),
        }

    @provider_tool(tags={"native", "read"})
    async def native_integrity_status() -> dict[str, Any]:
        return await asyncio.to_thread(native_facade.status)

    @provider_tool(tags={"native", "feature", "write"})
    async def feature_execute(
        feature_id: str,
        feature_sequence: int,
        correlation_id: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            native_facade.feature_execute,
            feature_id=feature_id,
            feature_sequence=feature_sequence,
            correlation_id=correlation_id,
            actions=actions,
        )

    @provider_tool(tags={"native", "entity", "write"})
    async def batch_create_entities(entities: list[dict[str, Any]]) -> dict[str, Any]:
        return await asyncio.to_thread(native_facade.batch_create_entities, entities)

    @provider_tool(tags={"native", "block", "write"})
    async def batch_insert_blocks(inserts: list[dict[str, Any]]) -> dict[str, Any]:
        return await asyncio.to_thread(native_facade.batch_insert_blocks, inserts)

    @provider_tool(tags={"native", "entity", "write"})
    async def batch_transform_entities(
        semantic_pids: list[str],
        transform: dict[str, Any],
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            native_facade.batch_transform_entities,
            semantic_pids,
            transform,
        )

    @provider_tool(tags={"native", "metadata", "read"})
    async def metadata_get(semantic_pid: str, namespace: str) -> dict[str, Any]:
        return await asyncio.to_thread(native_facade.metadata_get, semantic_pid, namespace)

    @provider_tool(tags={"native", "metadata", "write"})
    async def metadata_set(
        semantic_pid: str,
        namespace: str,
        value: Any,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            native_facade.metadata_set,
            semantic_pid,
            namespace,
            value,
        )

    @provider_tool(tags={"native", "metadata", "read"})
    async def metadata_query(
        namespace: str,
        path: str | None = None,
        equals: Any = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            native_facade.metadata_query,
            namespace,
            path=path,
            equals=equals,
            limit=limit,
        )

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
    async def document_configure_units(
        insertion_units: str | None = None,
        measurement: str | None = None,
        linear_format: str | None = None,
        linear_precision: int | None = None,
    ) -> dict[str, Any]:
        return await backend.document_configure_units(
            insertion_units, measurement, linear_format, linear_precision
        )

    @provider_tool(tags={"document", "read"})
    async def document_dependencies() -> dict[str, Any]:
        return await backend.document_dependencies()

    @provider_tool(tags={"document", "write"})
    async def artifact_seal(destination_dir: str | None = None) -> dict[str, Any]:
        return await backend.artifact_seal(destination_dir)

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

    @provider_tool(tags={"object", "read"})
    async def object_query(
        type_filter: str | None = None,
        layer_filter: str | None = None,
        min_x: float | None = None,
        min_y: float | None = None,
        max_x: float | None = None,
        max_y: float | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        if not 1 <= int(limit) <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if int(offset) < 0:
            raise ValueError("offset must be >= 0")
        window_values = (min_x, min_y, max_x, max_y)
        has_window = any(value is not None for value in window_values)
        if has_window and not all(value is not None for value in window_values):
            raise ValueError("spatial query requires min_x, min_y, max_x and max_y together")
        if has_window and (float(min_x) >= float(max_x) or float(min_y) >= float(max_y)):
            raise ValueError("spatial query window must have positive width and height")

        total = await backend.object_count(type_filter, layer_filter)
        scan_limit = min(total, 5000)
        candidates = []
        scan_offset = 0
        while scan_offset < scan_limit:
            page_size = min(1000, scan_limit - scan_offset)
            page = await backend.object_list(type_filter, layer_filter, page_size, scan_offset)
            if not page:
                break
            candidates.extend(page)
            scan_offset += len(page)
            if len(page) < page_size:
                break
        matched: list[dict[str, Any]] = []
        for row in candidates:
            payload = row.to_dict()
            if has_window:
                measured = await backend.object_measure(row.id)
                bbox = measured.get("bounding_box")
                if not isinstance(bbox, dict):
                    continue
                low = bbox.get("min")
                high = bbox.get("max")
                if not isinstance(low, list) or not isinstance(high, list) or len(low) < 2 or len(high) < 2:
                    continue
                intersects = not (
                    float(high[0]) < float(min_x)
                    or float(low[0]) > float(max_x)
                    or float(high[1]) < float(min_y)
                    or float(low[1]) > float(max_y)
                )
                if not intersects:
                    continue
                payload["measurement"] = measured
            matched.append(payload)
        page = matched[int(offset): int(offset) + int(limit)]
        return {
            "items": page,
            "count": len(page),
            "matched_count": len(matched),
            "source_count": total,
            "offset": int(offset),
            "limit": int(limit),
            "complete": total <= scan_limit,
            "scan_limit": scan_limit,
        }

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
        bulges: list[float] | None = None,
        widths: list[list[float]] | None = None,
        elevation: float = 0.0,
    ) -> dict[str, Any]:
        return (
            await backend.entity_create_polyline(
                points, closed, layer, color, bulges, widths, elevation
            )
        ).to_dict()

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

    @provider_tool(tags={"dimension", "write"})
    async def dimension_angular(
        vertex_x: float,
        vertex_y: float,
        first_x: float,
        first_y: float,
        second_x: float,
        second_y: float,
        text_x: float,
        text_y: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_angular(
                vertex_x, vertex_y, first_x, first_y, second_x, second_y, text_x, text_y, layer
            )
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_radial(
        center_x: float,
        center_y: float,
        chord_x: float,
        chord_y: float,
        leader_length: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_radial(
                center_x, center_y, chord_x, chord_y, leader_length, layer
            )
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_diametric(
        center_x: float,
        center_y: float,
        chord_x: float,
        chord_y: float,
        leader_length: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_diametric(
                center_x, center_y, chord_x, chord_y, leader_length, layer
            )
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_ordinate(
        definition_x: float,
        definition_y: float,
        leader_x: float,
        leader_y: float,
        axis: str = "x",
        layer: str | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_ordinate(
                definition_x, definition_y, leader_x, leader_y, axis, layer
            )
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

    @provider_tool(tags={"layer", "write"})
    async def layer_update_state(
        name: str,
        is_on: bool | None = None,
        is_frozen: bool | None = None,
        is_locked: bool | None = None,
        color: int | None = None,
        linetype: str | None = None,
        lineweight: int | None = None,
    ) -> dict[str, Any]:
        return (
            await backend.layer_update_state(
                name,
                is_on=is_on,
                is_frozen=is_frozen,
                is_locked=is_locked,
                color=color,
                linetype=linetype,
                lineweight=lineweight,
            )
        ).to_dict()

    @provider_tool(tags={"block", "read"})
    async def block_list(
        include_xref_dependent: bool = False,
        include_xrefs: bool = True,
        name_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return [
            block.to_dict()
            for block in await backend.block_list(
                include_xref_dependent, include_xrefs, name_filter, limit, offset
            )
        ]

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

    @provider_tool(tags={"xref", "read"})
    async def xref_list() -> list[dict[str, Any]]:
        return await backend.xref_list()

    @provider_tool(tags={"xref", "write"})
    async def xref_attach(
        path: str,
        name: str | None = None,
        overlay: bool = True,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        scale_z: float = 1.0,
        rotation: float = 0.0,
        layer: str | None = None,
    ) -> dict[str, Any]:
        return await backend.xref_attach(
            path,
            name=name,
            overlay=overlay,
            x=x,
            y=y,
            z=z,
            scale_x=scale_x,
            scale_y=scale_y,
            scale_z=scale_z,
            rotation=rotation,
            layer=layer,
        )

    @provider_tool(tags={"xref", "write"})
    async def xref_reload(name: str) -> dict[str, Any]:
        return await backend.xref_reload(name)

    @provider_tool(tags={"xref", "write"})
    async def xref_unload(name: str) -> dict[str, Any]:
        return await backend.xref_unload(name)

    @provider_tool(tags={"xref", "write"})
    async def xref_detach(name: str) -> dict[str, Any]:
        return await backend.xref_detach(name)

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

    @provider_tool(tags={"view", "write"})
    async def view_set_direction(dx: float, dy: float, dz: float) -> dict[str, Any]:
        return await backend.view_set_direction(dx, dy, dz)

    @provider_tool(tags={"view", "write"})
    async def view_set_preset(preset: str = "se_isometric") -> dict[str, Any]:
        return await backend.view_set_preset(preset)

    @provider_tool(tags={"view", "write"})
    async def view_set_visual_style(style: str = "shades_of_gray") -> dict[str, Any]:
        return await backend.view_set_visual_style(style)

    @provider_tool(tags={"analysis", "read"})
    async def object_measure(object_id: str) -> dict[str, Any]:
        return await backend.object_measure(object_id)

    @provider_tool(tags={"analysis", "read"})
    async def drawing_extents() -> dict[str, Any]:
        return await backend.drawing_extents()

    @provider_tool(tags={"analysis", "read"})
    async def object_intersections(
        first_id: str,
        second_id: str,
        extend_mode: str = "none",
    ) -> dict[str, Any]:
        return await backend.object_intersections(first_id, second_id, extend_mode)

    @provider_tool(tags={"solid", "write"})
    async def solid_create_primitive(
        kind: str,
        parameters: dict[str, float],
        layer: str | None = None,
    ) -> dict[str, Any]:
        normalized = str(kind).strip().lower()
        specs: dict[str, tuple[tuple[str, ...], Any]] = {
            "box": (("cx", "cy", "cz", "length", "width", "height"), backend.solid_box),
            "cylinder": (("cx", "cy", "cz", "radius", "height"), backend.solid_cylinder),
            "sphere": (("cx", "cy", "cz", "radius"), backend.solid_sphere),
            "cone": (("cx", "cy", "cz", "radius", "height"), backend.solid_cone),
            "torus": (("cx", "cy", "cz", "torus_radius", "tube_radius"), backend.solid_torus),
            "wedge": (("cx", "cy", "cz", "length", "width", "height"), backend.solid_wedge),
        }
        spec = specs.get(normalized)
        if spec is None:
            raise ValueError("solid primitive kind must be one of: box, cylinder, sphere, cone, torus, wedge")
        fields, method = spec
        supplied = set(parameters)
        required = set(fields)
        if supplied != required:
            raise ValueError(
                f"{normalized} parameters must contain exactly: " + ", ".join(fields)
            )
        values = [float(parameters[field]) for field in fields]
        result = await method(*values)
        if layer is not None:
            await backend.object_set_properties(result["handle"], layer=layer)
            result = await backend.solid_inspect(result["handle"])
        return {**result, "primitive": normalized}

    @provider_tool(tags={"solid", "write"})
    async def solid_extrude(
        profile_object_id: str,
        height: float,
        taper_angle: float = 0.0,
        layer: str | None = None,
    ) -> dict[str, Any]:
        result = await backend.solid_extrude(profile_object_id, height, taper_angle)
        if layer is not None:
            await backend.object_set_properties(result["handle"], layer=layer)
            result = await backend.solid_inspect(result["handle"])
        return result

    @provider_tool(tags={"solid", "write"})
    async def solid_sweep(
        profile_object_id: str,
        path_object_id: str,
        layer: str | None = None,
    ) -> dict[str, Any]:
        result = await backend.solid_sweep(profile_object_id, path_object_id)
        if layer is not None:
            await backend.object_set_properties(result["handle"], layer=layer)
            result = await backend.solid_inspect(result["handle"])
        return result

    @provider_tool(tags={"solid", "write"})
    async def solid_revolve(
        profile_object_id: str,
        axis_x1: float,
        axis_y1: float,
        axis_z1: float,
        axis_x2: float,
        axis_y2: float,
        axis_z2: float,
        angle_deg: float = 360.0,
        layer: str | None = None,
    ) -> dict[str, Any]:
        result = await backend.solid_revolve(
            profile_object_id,
            axis_x1, axis_y1, axis_z1, axis_x2, axis_y2, axis_z2,
            angle_deg,
        )
        if layer is not None:
            await backend.object_set_properties(result["handle"], layer=layer)
            result = await backend.solid_inspect(result["handle"])
        return result

    @provider_tool(tags={"solid", "write"})
    async def solid_boolean(
        target_handle: str,
        tool_handle: str,
        operation: str,
    ) -> dict[str, Any]:
        return await backend.solid_boolean(target_handle, tool_handle, operation)

    @provider_tool(tags={"solid", "write"})
    async def solid_transform(
        handle: str,
        operation: str,
        parameters: dict[str, float],
    ) -> dict[str, Any]:
        normalized = str(operation).strip().lower()
        if normalized == "move":
            fields = ("dx", "dy", "dz")
            if set(parameters) != set(fields):
                raise ValueError("move parameters must contain exactly dx, dy, dz")
            return await backend.solid_move(handle, *(float(parameters[key]) for key in fields))
        if normalized == "rotate":
            fields = ("axis_x1", "axis_y1", "axis_z1", "axis_x2", "axis_y2", "axis_z2", "angle_deg")
            if set(parameters) != set(fields):
                raise ValueError("rotate parameters must contain exactly axis endpoints and angle_deg")
            return await backend.solid_rotate3d(handle, *(float(parameters[key]) for key in fields))
        if normalized == "scale":
            fields = ("base_x", "base_y", "base_z", "factor")
            if set(parameters) != set(fields):
                raise ValueError("scale parameters must contain exactly base_x, base_y, base_z, factor")
            return await backend.solid_scale3d(handle, *(float(parameters[key]) for key in fields))
        if normalized == "mirror":
            fields = ("x1", "y1", "z1", "x2", "y2", "z2", "x3", "y3", "z3")
            if set(parameters) != set(fields):
                raise ValueError("mirror parameters must contain exactly three WCS plane points")
            return await backend.solid_mirror3d(handle, *(float(parameters[key]) for key in fields))
        raise ValueError("solid transform operation must be one of: move, rotate, scale, mirror")

    @provider_tool(tags={"solid", "read"})
    async def solid_inspect(handle: str) -> dict[str, Any]:
        return await backend.solid_inspect(handle)

    @provider_tool(tags={"solid", "export"})
    async def solid_export(
        handles: list[str],
        path: str,
        format: str = "sat",
    ) -> dict[str, Any]:
        return await backend.solid_export(handles, path, format)

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
