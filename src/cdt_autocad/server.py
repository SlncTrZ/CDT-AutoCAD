"""FastMCP entrypoint for the CDT_Engineer AutoCAD provider.
Wing: code | Topic: autocad-a2 | Updated: 2026-10-03 20:45
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, cast

from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.tool import ToolResult  # type: ignore[import-not-found,import-untyped]
from fastmcp.utilities.types import Image
from pydantic import Field, FiniteFloat

from . import __version__
from .backends.base import AutoCADBackend
from .backends.ezdxf_backend import EzdxfBackend
from .config import Settings
from .contract_identity import (
    COMMON_CONTRACT_VERSION as _COMMON_CONTRACT_VERSION,
)
from .contract_identity import (
    CONTRACT_VERSION as _CONTRACT_VERSION,
)
from .contract_identity import (
    EXECUTION_MODEL as _EXECUTION_MODEL,
)
from .contract_identity import (
    PROTOCOL_VERSION as _PROTOCOL_VERSION,
)
from .contract_identity import (
    PUBLIC_TOOL_COUNT as _PUBLIC_TOOL_COUNT,
)
from .contract_identity import (
    UPDATED_AT as _UPDATED_AT,
)
from .contract_identity import (
    contract_hash as _contract_hash,
)
from .contract_identity import (
    contract_material as _contract_material,
)
from .diagnostics import DiagnosticSink, ProviderDiagnosticMiddleware
from .envelope import (
    ENVELOPE_VERSION,
    DeltaOversizedError,
    SnapshotOversizedError,
    canonical_byte_size,
    estimated_tokens,
)
from .errors import (
    BackendQuarantinedError,
    BackendTimeoutError,
    MutationCompletionUncertainError,
    StateConflictError,
    UnsupportedCapabilityError,
)
from .mutation_coordinator import MutationCoordinator
from .runtime_identity import RuntimeIdentity

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_HTTP_TRANSPORTS = {"http", "sse", "streamable-http"}

_TOOL_DESCRIPTIONS = {
    "help": (
        "Return provider contract guidance, workflow rules, capability map, and refusal notes.\n\n"
        "Side-effects: None. Read-only guidance query.\n"
        "Disambiguation: Use help for human/agent operating guidance; use system_capabilities for machine-readable JSON capability flags."
    ),
    "system_status": (
        "Report provider runtime status, AutoCAD version/progid, transport, and generation metadata.\n\n"
        "Side-effects: None. Read-only status query.\n"
        "Disambiguation: Use system_status to verify CAD connection and process health; use system_capabilities to inspect supported operations."
    ),
    "system_capabilities": (
        "Return the backend capability map used to plan calls before side effects; this tool has no CAD side effects.\n\n"
        "Side-effects: None. Read-only capability query.\n"
        "Disambiguation: Use system_capabilities before calling mutations to check supported CAD operations."
    ),
    "native_integrity_status": (
        "Inspect the same-session managed native bridge and the exact active AutoCAD document binding, including persistent document PID and semantic fingerprint.\n\n"
        "Side-effects: None. Read-only integrity query.\n"
        "Disambiguation: Use native_integrity_status to verify native C# bridge liveness and optimistic lock state; use system_status for overall provider status."
    ),
    "native_document_identity_initialize": (
        "Explicitly initialize provider-owned persistent document lineage on the active empty drawing space, returning verified document PID and semantic fingerprint.\n\n"
        "Side-effects: Writes persistent dictionary lineage to drawing, advances document fingerprint.\n"
        "Disambiguation: Use once on an empty drawing to bootstrap native tracking; refuses non-empty drawings."
    ),
    "feature_execute": (
        "Execute one complete generic feature as a feature-local logical transaction bound to caller-supplied document PID and predecessor fingerprint.\n\n"
        "Side-effects: Mutates active drawing state atomically; rolls back on failure.\n"
        "Disambiguation: Preferred route for multi-step feature creation; rolls back cleanly if any action fails."
    ),
    "batch_create_entities": (
        "Create 1..10000 generic typed CAD primitives through the managed native bridge using caller-supplied document PID and predecessor fingerprint binding.\n\n"
        "Side-effects: Creates multiple entities in active space with idle yields.\n"
        "Disambiguation: Use for high-throughput batch primitive creation; for single entities use entity_create_*."
    ),
    "batch_insert_blocks": (
        "Insert 1..10000 persistent-PID-bound block references through the managed native bridge only when caller document PID and predecessor fingerprint still match.\n\n"
        "Side-effects: Inserts block references across the drawing.\n"
        "Disambiguation: Use for bulk component placement; for single placement use block_insert."
    ),
    "batch_transform_entities": (
        "Transform 1..10000 persistent semantic PIDs with typed translate, rotate-Z, or uniform scale after caller document PID and predecessor fingerprint binding.\n\n"
        "Side-effects: Mutates entity transforms in active drawing.\n"
        "Disambiguation: Use for bulk updates to existing entities; for single objects use object_move, object_rotate, or object_scale."
    ),
    "metadata_get": (
        "Read one schema-agnostic metadata namespace from a persistent semantic PID through the native bridge.\n\n"
        "Side-effects: None. Read-only metadata query.\n"
        "Disambiguation: Use to retrieve engineering attributes attached to entities."
    ),
    "metadata_set": (
        "Set one bounded schema-agnostic JSON metadata namespace only when caller document PID and predecessor fingerprint still match.\n\n"
        "Side-effects: Attaches XData/extension dictionary metadata to entity.\n"
        "Disambiguation: Use to store engineering properties (part numbers, materials) on entities."
    ),
    "metadata_query": (
        "Query a bounded schema-agnostic metadata namespace across the active native document, optionally matching one dotted JSON path to an exact JSON value.\n\n"
        "Side-effects: None. Read-only metadata query.\n"
        "Disambiguation: Use to locate entities by engineering metadata rather than visual layers."
    ),
    "document_new": (
        "Create a new active CAD document in the current backend session; refuses unsafe transaction state.\n\n"
        "Side-effects: Changes active document session, creates unsaved drawing.\n"
        "Disambiguation: Use to start a clean drawing; use document_open to load existing DWG/DXF files."
    ),
    "document_open": (
        "Open an existing allowed DWG/DXF path as the active document; changes session state.\n\n"
        "Side-effects: Changes active document session.\n"
        "Disambiguation: Use to load an existing drawing; use document_new to create a blank drawing."
    ),
    "document_info": (
        "Read verified metadata for the active document, including path, units, counts, current space, backend, and detected AutoCAD release when available.\n\n"
        "Side-effects: None. Read-only document query.\n"
        "Disambiguation: Primary document query tool for inspecting active file state."
    ),
    "document_configure_units": (
        "Configure drawing insertion units, measurement system, linear format, and precision using a bounded typed schema.\n\n"
        "Side-effects: Updates drawing system variables (INSUNITS, MEASUREMENT, LUNITS, LUPREC).\n"
        "Disambiguation: Use during drawing setup to guarantee unit consistency (e.g. millimeters)."
    ),
    "document_dependencies": (
        "Inspect external-reference dependencies for the active drawing and report contained, resolved, loaded, and completeness state.\n\n"
        "Side-effects: None. Read-only dependency audit.\n"
        "Disambiguation: Use to audit required file dependencies (XREFs, images) before archiving or sealing."
    ),
    "artifact_seal": (
        "Save the active drawing, require a clean persisted state, copy it to a content-addressed accepted artifact, and write a SHA-256 manifest for stable handoff provenance.\n\n"
        "Side-effects: Saves document, writes artifact file and provenance manifest to disk.\n"
        "Disambiguation: Use at completion of milestone or deliverable handoff."
    ),
    "document_save": (
        "Save the currently bound document to its verified existing path, or delegate to SaveAs when path is supplied; success requires immediate persisted-clean readback.\n\n"
        "Side-effects: Persists unsaved drawing changes to disk and clears dirty state.\n"
        "Disambiguation: Use document_save to persist changes in-place; use document_save_as to write to a new destination path."
    ),
    "document_save_as": (
        "Save the active document to an allowed DWG/DXF destination, verify the same bound path and immediate persisted-clean state, then update its document scope.\n\n"
        "Side-effects: Writes drawing to new file path, updates document binding.\n"
        "Disambiguation: Use to fork or save drawing under a new name; use document_save for in-place save."
    ),
    "document_export_pdf": (
        "Export the active document or selected layout to an allowed PDF path; this writes an artifact and may require live AutoCAD plotting capability.\n\n"
        "Side-effects: Writes PDF artifact to disk.\n"
        "Disambiguation: Use for vector deliverable export; use view_screenshot for quick raster inspection."
    ),
    "drawing_audit": (
        "Run the backend drawing-audit operation on the active document; live AutoCAD AUDIT may repair content and does not expose a machine-readable repair count.\n\n"
        "Side-effects: Runs AutoCAD AUDIT command, fixes corrupted entity pointers.\n"
        "Disambiguation: Use after heavy edits or imports; use drawing_purge to remove unused definitions."
    ),
    "drawing_purge": (
        "Purge unused drawing resources from the active document; this mutates document state and should be used only when removal is intended.\n\n"
        "Side-effects: Deletes unreferenced layers, blocks, and styles from document database.\n"
        "Disambiguation: Use to clean up drawing before export; destructive to unused resources."
    ),
    "object_list": (
        "List objects from the backend's current supported space with optional type/layer filters and bounded pagination; returned IDs are backend object identifiers.\n\n"
        "Side-effects: None. Read-only entity query.\n"
        "Disambiguation: Use for sequential browsing of objects; use object_query when spatial filtering or bounding box measurements are needed."
    ),
    "object_get": (
        "Read one object by backend object identifier and return normalized type, layer, color, visibility, geometry properties, and current-space metadata.\n\n"
        "Side-effects: None. Read-only entity query.\n"
        "Disambiguation: Use for single entity inspection; use object_measure for calculated metrics."
    ),
    "object_count": (
        "Count objects in the backend's current supported space with optional type and layer filters; no document geometry is modified.\n\n"
        "Side-effects: None. Read-only entity count query.\n"
        "Disambiguation: Lightweight count check before fetching full object lists."
    ),
    "object_query": (
        "Query a bounded object set by type/layer and optional WCS bounding window, returning normalized objects plus measured bounds so source-registration workflows do not need side-channel DXF export.\n\n"
        "Side-effects: None. Read-only spatial query.\n"
        "Disambiguation: Preferred spatial query tool to find objects inside a bounding box."
    ),
    "object_set_properties": (
        "Update selected properties of one object—layer, color, linetype, or visibility—while leaving unspecified properties unchanged.\n\n"
        "Side-effects: Modifies entity visual properties.\n"
        "Disambiguation: Use to style entities; for spatial changes use object_move, object_rotate, or object_scale."
    ),
    "object_delete": (
        "Delete one object by backend object identifier from the active document; this is destructive and timeout uncertainty must be reconciled before retry.\n\n"
        "Side-effects: Destructive deletion of entity from active drawing.\n"
        "Disambiguation: Destructive removal; use object_set_properties(visible=False) if temporary hiding is preferred."
    ),
    "object_move": (
        "Move one object by WCS delta dx/dy/dz using drawing units and return the normalized updated entity; this mutates geometry.\n\n"
        "Side-effects: Shifts entity coordinates in active drawing.\n"
        "Disambiguation: Use for translation; use object_rotate or object_scale for other transforms."
    ),
    "object_copy": (
        "Copy one object by WCS delta dx/dy/dz using drawing units and return the new entity identifier without deleting the source.\n\n"
        "Side-effects: Creates a duplicate entity at the offset location.\n"
        "Disambiguation: Creates new object; use object_move to translate without duplication."
    ),
    "object_rotate": (
        "Rotate one object around the supplied WCS base point by angle_deg in degrees; this mutates geometry in the active document.\n\n"
        "Side-effects: Mutates entity geometry in active drawing.\n"
        "Disambiguation: Rotates geometry counter-clockwise in degrees; use object_scale to resize or object_move to translate."
    ),
    "object_scale": (
        "Scale one object uniformly about the supplied WCS base point by a positive factor; this mutates geometry in the active document.\n\n"
        "Side-effects: Mutates entity scale in active drawing.\n"
        "Disambiguation: Modifies entity size; use object_move to reposition."
    ),
    "entity_create_line": (
        "Create a LINE from two WCS endpoints in drawing units, optionally on a layer/color, and return the new backend object identifier.\n\n"
        "Side-effects: Mutates active drawing state, advances document fingerprint.\n"
        "Disambiguation: Use entity_create_line for discrete linear segments in WCS; use entity_create_polyline for joined multi-segment paths."
    ),
    "entity_create_circle": (
        "Create a CIRCLE at a WCS center with positive radius in drawing units, optionally on a layer/color, and return the new object identifier.\n\n"
        "Side-effects: Adds CIRCLE entity to active space.\n"
        "Disambiguation: Use for full 360-degree circles; for circular segments use entity_create_arc."
    ),
    "entity_create_arc": (
        "Create an ARC at a WCS center with positive radius; start_angle and end_angle are degrees and the result is a new drawing object.\n\n"
        "Side-effects: Adds ARC entity to active space.\n"
        "Disambiguation: Use for circular arcs in degrees; for full circles use entity_create_circle."
    ),
    "entity_create_polyline": (
        "Create a 2D lightweight polyline from WCS XY points in drawing units, optionally closed/layered/colored, and return the new object identifier.\n\n"
        "Side-effects: Adds LWPOLYLINE entity to active space.\n"
        "Disambiguation: Use for continuous multi-segment paths, contours, or closed boundaries."
    ),
    "entity_create_text": (
        "Create TEXT at a WCS insertion point with drawing-unit height and rotation in degrees, optionally on a layer/color, and return its identifier.\n\n"
        "Side-effects: Adds TEXT entity to active space.\n"
        "Disambiguation: Use for labels, callouts, and notes."
    ),
    "hatch_create": (
        "Create a hatch from a closed WCS XY boundary using pattern, scale, and angle in degrees; invalid or unsupported boundaries are refused.\n\n"
        "Side-effects: Adds HATCH entity to active space.\n"
        "Disambiguation: Use to fill closed contours; boundary must be closed and coplanar."
    ),
    "dimension_linear": (
        "Create a linear dimension from WCS extension points and dimension-line point; rotation is degrees and placement uses drawing units.\n\n"
        "Side-effects: Adds linear DIMENSION entity.\n"
        "Disambiguation: Use for orthogonal (0 or 90 deg) dimensions; for angled geometry use dimension_aligned."
    ),
    "dimension_aligned": (
        "Create an aligned dimension from WCS extension points and a WCS dimension-line point, using the active drawing's dimension style.\n\n"
        "Side-effects: Adds aligned DIMENSION entity.\n"
        "Disambiguation: Dimension line stays parallel to extension points regardless of angle."
    ),
    "dimension_angular": (
        "Create a typed angular dimension from one vertex, two WCS ray points, and a text point; invalid collinear rays are rejected before mutation.\n\n"
        "Side-effects: Adds angular DIMENSION entity.\n"
        "Disambiguation: Use to dimension angles in degrees; rays must not be collinear."
    ),
    "dimension_radial": (
        "Create a typed radial dimension from center/chord geometry and positive leader length using the active drawing dimension style.\n\n"
        "Side-effects: Adds radial DIMENSION entity.\n"
        "Disambiguation: Displays radius (R); for full circles prefer dimension_diametric."
    ),
    "dimension_diametric": (
        "Create a typed diametric dimension from center/chord geometry and positive leader length using the active drawing dimension style.\n\n"
        "Side-effects: Adds diametric DIMENSION entity.\n"
        "Disambiguation: Displays diameter; for arc radii use dimension_radial."
    ),
    "dimension_ordinate": (
        "Create an X- or Y-ordinate dimension from a WCS definition point and leader endpoint with strict axis validation.\n\n"
        "Side-effects: Adds ordinate DIMENSION entity.\n"
        "Disambiguation: Measures absolute distance from drawing origin or datum."
    ),
    "layer_list": (
        "List drawing layers with normalized names, colors, linetypes, visibility/frozen/locked state, and whether each layer is current.\n\n"
        "Side-effects: None. Read-only layer query.\n"
        "Disambiguation: Use to inspect existing layers before placing entities."
    ),
    "layer_create": (
        "Create a new layer with the requested AutoCAD color index; existing names are refused and the active layer is not changed.\n\n"
        "Side-effects: Adds layer record to drawing layer table.\n"
        "Disambiguation: Refuses if layer already exists; use layer_update_state to modify existing layers."
    ),
    "layer_set_current": (
        "Set an existing layer as the active document layer; this changes session drawing state but does not move existing objects between layers.\n\n"
        "Side-effects: Changes current working layer for subsequent entity creation.\n"
        "Disambiguation: Does not modify existing entities; only sets active context."
    ),
    "layer_update_state": (
        "Update allowlisted layer state such as on/off, frozen, locked, color, linetype, or lineweight; unsafe changes to the current layer are refused before mutation.\n\n"
        "Side-effects: Modifies layer table properties.\n"
        "Disambiguation: Use to change layer visibility or styling across all entities on that layer."
    ),
    "block_list": (
        "List named block definitions, excluding anonymous blocks, with base point, entity count, attribute-definition count, and xref status.\n\n"
        "Side-effects: None. Read-only block definition query.\n"
        "Disambiguation: Use to discover available blocks before inserting."
    ),
    "block_create": (
        "Create a named block definition by copying the specified existing objects around a WCS base point; source objects remain in their original space.\n\n"
        "Side-effects: Creates BLOCK definition; original objects remain in space.\n"
        "Disambiguation: Creates reusable definition; does not insert an instance."
    ),
    "block_insert": (
        "Insert an existing named block at a WCS point with XY scale and rotation in degrees, optionally assigning the new reference to a layer.\n\n"
        "Side-effects: Adds INSERT entity to active space.\n"
        "Disambiguation: Inserts instance of existing block; use batch_insert_blocks for bulk placement."
    ),
    "xref_list": (
        "List live AutoCAD external references with contained/resolved/loaded state while redacting paths that fall outside configured allowed roots.\n\n"
        "Side-effects: None. Read-only XREF query.\n"
        "Disambiguation: Use to inspect external DWG attachments."
    ),
    "xref_attach": (
        "Attach or overlay one allowed DWG/DXF external reference with a bounded WCS transform and optional layer; source paths are canonicalized before AutoCAD side effects.\n\n"
        "Side-effects: Attaches external drawing file to active document.\n"
        "Disambiguation: Use to reference external drawings; use block_insert for internal blocks."
    ),
    "xref_reload": (
        "Reload one existing XREF only when its resolved source remains inside configured allowed roots.\n\n"
        "Side-effects: Updates referenced geometry from disk.\n"
        "Disambiguation: Use when an external file has been updated on disk."
    ),
    "xref_unload": (
        "Unload one existing XREF definition without deleting it, preserving a reversible reference state.\n\n"
        "Side-effects: Unloads XREF memory representation.\n"
        "Disambiguation: Reversible; use xref_detach for permanent removal."
    ),
    "xref_detach": (
        "Detach one existing XREF definition from the active document; this is destructive to reference state.\n\n"
        "Side-effects: Destructive removal of XREF definition and references.\n"
        "Disambiguation: Destructive; removes dependency from drawing."
    ),
    "layout_list": (
        "List document layouts and identify the current layout; this is read-only and does not change active model/paper space.\n\n"
        "Side-effects: None. Read-only layout query.\n"
        "Disambiguation: Use to discover printable sheets."
    ),
    "layout_create": (
        "Create a new paper-space layout by name; duplicate or empty names are refused and existing layouts are preserved.\n\n"
        "Side-effects: Adds layout to drawing.\n"
        "Disambiguation: Creates blank sheet; viewports must be added separately with viewport_create."
    ),
    "layout_set_current": (
        "Switch the active document to an existing layout; this changes the operator's current model/paper-space context.\n\n"
        "Side-effects: Changes operator active space context.\n"
        "Disambiguation: Switch between Model and paper layouts."
    ),
    "viewport_create": (
        "Create a paper-space viewport on a named layout with center/size in paper units, WCS view target, and positive custom scale.\n\n"
        "Side-effects: Adds VIEWPORT entity to active layout.\n"
        "Disambiguation: Requires active paper-space layout."
    ),
    "viewport_list": (
        "List paper-space viewports for one or all layouts with handle, geometry, target, scale, lock state, and backend-created ownership hint.\n\n"
        "Side-effects: None. Read-only viewport query.\n"
        "Disambiguation: Inspect viewport handles, scales, and bounding boxes."
    ),
    "viewport_set_scale": (
        "Set a paper-space viewport's positive custom scale by handle and return the verified scale and derived view height.\n\n"
        "Side-effects: Updates viewport magnification and view height.\n"
        "Disambiguation: Adjusts architectural/engineering scale (e.g. 1:50, 1:100)."
    ),
    "viewport_lock": (
        "Set or clear DisplayLocked for a paper-space viewport by handle and return the verified lock state.\n\n"
        "Side-effects: Sets DisplayLocked property on viewport.\n"
        "Disambiguation: Lock viewports to prevent accidental zoom changes during drafting."
    ),
    "viewport_delete": (
        "Delete a paper-space viewport by handle; pre-existing or unknown viewports require force=true because ActiveX cannot reliably identify the main viewport.\n\n"
        "Side-effects: Destructive deletion of viewport from paper space.\n"
        "Disambiguation: Deletes viewport layout frame; pass force=True for unmanaged or preexisting viewports."
    ),
    "view_zoom_extents": (
        "Zoom the live AutoCAD view to drawing extents; this changes only the operator view and does not modify drawing geometry.\n\n"
        "Side-effects: Changes current display camera; drawing geometry unmodified.\n"
        "Disambiguation: Frames full drawing; use view_zoom_window to zoom to a specific area."
    ),
    "view_zoom_window": (
        "Zoom the live AutoCAD view to the supplied WCS XY window bounds; zero-area windows are refused and drawing geometry is unchanged.\n\n"
        "Side-effects: Changes display window; drawing geometry unmodified.\n"
        "Disambiguation: Zooms to specific bounding area."
    ),
    "view_screenshot": (
        "Capture the live AutoCAD application window as PNG bytes; this is read-only and requires the live COM screenshot capability.\n\n"
        "Side-effects: None. Returns raster PNG bytes.\n"
        "Disambiguation: Quick visual verification; use document_export_pdf for vector deliverables."
    ),
    "view_set_direction": (
        "Set a normalized 3D model-space view direction from a WCS vector and zoom to extents; this may alter persisted view state even though it does not modify CAD geometry.\n\n"
        "Side-effects: Rotates 3D viewpoint; drawing geometry unmodified.\n"
        "Disambiguation: Arbitrary 3D view angle; use view_set_preset for standard orthogonal/isometric views."
    ),
    "view_set_preset": (
        "Apply an allowlisted orthographic or isometric 3D view preset such as se_isometric using typed direction vectors rather than free-text AutoCAD commands.\n\n"
        "Side-effects: Changes viewpoint camera to preset angle.\n"
        "Disambiguation: Fast standard view alignment."
    ),
    "view_set_visual_style": (
        "Apply an allowlisted model-space visual style such as shades_of_gray through a bounded internal AutoCAD command mapping; arbitrary command text is never accepted from callers.\n\n"
        "Side-effects: Modifies viewport rendering mode.\n"
        "Disambiguation: Changes shading and display mode."
    ),
    "object_measure": (
        "Measure one object using the active backend's exact supported geometry API and return type-specific metrics plus WCS bounding box for deterministic verification.\n\n"
        "Side-effects: None. Read-only measurement query.\n"
        "Disambiguation: Returns deterministic type-specific mathematical metrics."
    ),
    "drawing_extents": (
        "Measure the current drawing-space WCS extents using the backend geometry engine without changing document objects.\n\n"
        "Side-effects: None. Read-only bounding box query.\n"
        "Disambiguation: Returns WCS coordinates [min_x, min_y, max_x, max_y]; does not change view."
    ),
    "object_intersections": (
        "Compute exact supported intersections between two distinct objects with an explicit extension mode; unsupported solvers fail closed rather than approximate silently.\n\n"
        "Side-effects: None. Read-only intersection solver.\n"
        "Disambiguation: Computes geometric intersection points with extend options."
    ),
    "solid_create_primitive": (
        "Create a native ACIS BOX, CYLINDER, SPHERE, CONE, TORUS, or WEDGE from typed numeric parameters; the live AutoCAD backend is required and an optional output layer is applied explicitly.\n\n"
        "Side-effects: Adds 3DSOLID entity to drawing.\n"
        "Disambiguation: Primary tool for baseline solid shapes; use boolean operations to combine."
    ),
    "solid_extrude": (
        "Create an ACIS 3DSOLID by extruding a closed planar profile by non-zero height and bounded taper angle; temporary regions are cleaned deterministically.\n\n"
        "Side-effects: Creates 3DSOLID from profile; removes temp region.\n"
        "Disambiguation: Linear extrusion; use solid_revolve for rotational solids."
    ),
    "solid_sweep": (
        "Create an ACIS 3DSOLID by sweeping a closed profile along a supported typed CAD curve path; unsupported path types are refused before opaque AutoCAD failures.\n\n"
        "Side-effects: Creates 3DSOLID along trajectory.\n"
        "Disambiguation: Path-driven extrusion for pipes, ducts, rails."
    ),
    "solid_revolve": (
        "Create an ACIS 3DSOLID by revolving a closed planar profile around a non-degenerate WCS 3D axis by a non-zero angle within plus/minus 360 degrees.\n\n"
        "Side-effects: Creates 3DSOLID by rotation.\n"
        "Disambiguation: Use for axisymmetric shapes (shafts, pulleys)."
    ),
    "solid_boolean": (
        "Apply UNION, SUBTRACT, or INTERSECT between two distinct ACIS 3DSOLIDs and return post-operation volume, centroid, bounding box, and tool-consumption state.\n\n"
        "Side-effects: Combines or cuts solids; consumes tool body.\n"
        "Disambiguation: Constructive solid geometry operation."
    ),
    "solid_transform": (
        "Apply one typed 3D move, rotate, uniform-scale, or mirror operation to an ACIS solid; free-form matrices and arbitrary commands are not accepted.\n\n"
        "Side-effects: Modifies solid position/orientation.\n"
        "Disambiguation: 3D spatial transformation for ACIS bodies."
    ),
    "solid_inspect": (
        "Inspect an ACIS 3DSOLID by handle and return layer, visibility, volume, centroid, and WCS bounding box; face-level topology is reported as unsupported when it cannot be proven through ActiveX.\n\n"
        "Side-effects: None. Read-only solid inspection.\n"
        "Disambiguation: Physical and spatial verification of solids."
    ),
    "solid_export": (
        "Export selected native ACIS solids to a contained SAT artifact using AutoCAD's typed Export API and return SHA-256 provenance; unsupported STEP/STL requests are refused rather than emulated unsafely.\n\n"
        "Side-effects: Writes SAT file to disk.\n"
        "Disambiguation: CAD interchange for solid geometry."
    ),
    "transaction_begin": (
        "Begin a backend-tracked transaction or undo scope on the bound document; nested depth is limited and document switching is blocked while active.\n\n"
        "Side-effects: Starts undo group; blocks document switching.\n"
        "Disambiguation: Group multiple modifications into a single undoable unit."
    ),
    "transaction_commit": (
        "Commit the current backend-tracked transaction or undo scope for the bound document; refuses when no tracked transaction is active.\n\n"
        "Side-effects: Finalizes undo group.\n"
        "Disambiguation: Completes transaction started by transaction_begin."
    ),
    "transaction_rollback": (
        "Rollback the current backend-tracked transaction or undo scope for the bound document; this intentionally reverts mutations in that scope.\n\n"
        "Side-effects: Destructive reversal of mutations within active transaction.\n"
        "Disambiguation: Reverts state back to transaction_begin."
    ),
    "undo": (
        "Undo the most recent available backend/document operation according to backend history semantics; refuses when no undoable state is available.\n\n"
        "Side-effects: Reverts last modification.\n"
        "Disambiguation: Single-step backward history navigation."
    ),
    "redo": (
        "Redo the most recent reverted backend/document operation according to backend history semantics; refuses when no redoable state is available.\n\n"
        "Side-effects: Re-applies reverted modification.\n"
        "Disambiguation: Single-step forward history navigation."
    ),
}


def _help_payload(
    backend: AutoCADBackend, capabilities: dict[str, Any] | None = None
) -> dict[str, Any]:
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
        "capabilities": backend.capabilities() if capabilities is None else capabilities,
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
            None if application is None else str(application.get("release") or "") == "2027"
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
        if isinstance(item, SnapshotOversizedError | DeltaOversizedError):
            return (
                "oversized",
                {"code": item.code, "retryable": False},
                str(item),
            )
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
        if isinstance(item, MutationCompletionUncertainError):
            return (
                "conflict",
                {
                    "reason": "mutation_completion_uncertain",
                    "retryable": False,
                    "completion_unknown": True,
                },
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


def _consume_background_mutation(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except BaseException:
        pass


async def _run_native_mutation(
    coordinator: MutationCoordinator,
    func: Any,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run one synchronous native writer behind the shared provider mutation authority."""

    # Remote async facade enters the same coordinator via its adapter; do not double-lock.
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    async with coordinator.writer("native"):
        task = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            coordinator.quarantine(
                "native",
                "native mutation request was cancelled after dispatch; completion is unknown",
            )
            task.add_done_callback(_consume_background_mutation)
            raise
        except Exception as exc:
            if bool(getattr(exc, "completion_unknown", False)):
                coordinator.quarantine("native", str(exc))
                if isinstance(exc, MutationCompletionUncertainError):
                    raise
                raise MutationCompletionUncertainError(str(exc)) from exc
            raise


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
    mutation_coordinator = MutationCoordinator()
    backend: AutoCADBackend
    local_runtime = None
    if settings.runtime_endpoint:
        from .backends.remote_facade import RemoteNativeFacade
        from .backends.remote_runtime import RemoteAutoCADRuntimeAdapter
        from .runtime_transport import RemoteRuntimeTransport

        assert settings.runtime_token_file is not None
        token = settings.runtime_token_file.read_text().strip()
        local_runtime = RemoteAutoCADRuntimeAdapter(
            RemoteRuntimeTransport(settings.runtime_endpoint, token),
            mutation_coordinator=mutation_coordinator,
            settings=settings,
            require_generation=True,
            state_file=settings.runtime_state_file,
        )
        backend = local_runtime
        native_facade = RemoteNativeFacade(local_runtime)
    elif settings.backend == "com":
        from .backends.com_backend import ComBackend
        from .backends.local_runtime import LocalAutoCADRuntimeAdapter
        from .native_bridge.public_runtime import NativePublicFacade

        com_backend = ComBackend(settings, mutation_coordinator=mutation_coordinator)
        local_runtime = LocalAutoCADRuntimeAdapter(
            com_backend,
            mutation_coordinator=mutation_coordinator,
            native_facade=NativePublicFacade(settings),
        )
        backend = local_runtime
        native_facade = local_runtime.native_facade
    else:
        from .native_bridge.public_runtime import NativePublicFacade

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
    app._mcp_server.version = __version__
    app.add_middleware(
        ProviderDiagnosticMiddleware(
            backend_name=backend.name,
            provider_version=__version__,
            sink=diagnostic_sink,
            generation=runtime_identity.generation,
        )
    )
    app.add_middleware(ProviderErrorMiddleware(backend))

    async def identity_read(name: str) -> dict[str, Any]:
        if not settings.runtime_endpoint:
            return getattr(backend, name)()
        from .runtime_transport import RuntimeTransportError

        try:
            result = await getattr(backend, name + "_async")()
            if name == "status":
                from .runtime_transport import RuntimeOpRefusedError

                try:
                    call_fn = cast(
                        Callable[[str], Awaitable[dict[str, Any]]] | None,
                        getattr(backend, "_call", None),
                    )
                    if call_fn is not None:
                        execution_identity = await call_fn("runtime_identity_status")
                    else:
                        execution_identity = {
                            "native_bridge": {"ready": False, "reason": "identity_unavailable"}
                        }
                except RuntimeOpRefusedError:
                    execution_identity = {
                        "native_bridge": {"ready": False, "reason": "identity_unavailable"}
                    }
                expected_gen = getattr(local_runtime, "expected_generation", None)
                result = {
                    **result,
                    "execution_identity": execution_identity,
                    "runtime_generation": expected_gen,
                }
            return result
        except RuntimeTransportError as exc:
            if name == "capabilities":
                return {}
            expected_gen = getattr(local_runtime, "expected_generation", None)
            return {
                "backend": backend.name,
                "runtime_available": False,
                "connected": False,
                "dependency_error": str(exc),
                "transport": "remote",
                "expected_runtime_generation": expected_gen,
            }

    async def native_read(func: Any, *args: Any, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return await asyncio.to_thread(func, *args, **kwargs)

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
        return _help_payload(backend, await identity_read("capabilities"))

    @provider_tool(tags={"identity", "read"})
    async def system_status() -> dict[str, Any]:
        backend_status = await identity_read("status")
        contract_hash = _contract_hash()
        return {
            "provider": "autocad",
            "provider_version": __version__,
            "protocol_version": _PROTOCOL_VERSION,
            "contract_version": _CONTRACT_VERSION,
            "contract_hash": contract_hash,
            **backend_status,
            **_status_contract(backend, backend_status),
            **(
                {
                    "supported_profile": "com-live-remote",
                    "generation": runtime_identity.generation,
                    "expected_runtime_generation": getattr(
                        local_runtime, "expected_generation", None
                    ),
                    "execution_host": "workstation",
                    "transport": "remote",
                }
                if settings.runtime_endpoint
                else runtime_identity.status(backend_name=backend.name)
            ),
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
            "capabilities": await identity_read("capabilities"),
        }

    @provider_tool(tags={"native", "read"})
    async def native_integrity_status() -> dict[str, Any]:
        status = await native_read(native_facade.status)
        return {**status, "mutation_coordinator": mutation_coordinator.status()}

    @provider_tool(tags={"native", "document", "write"})
    async def native_document_identity_initialize() -> dict[str, Any]:
        return await _run_native_mutation(
            mutation_coordinator,
            native_facade.bootstrap_document_identity,
        )

    @provider_tool(tags={"native", "feature", "write"})
    async def feature_execute(
        document_pid: Annotated[str, Field(description="Persistent document identifier (PID) matching active CAD drawing")],
        expected_parent_fp: Annotated[str, Field(description="Expected predecessor semantic fingerprint for optimistic locking")],
        feature_id: Annotated[str, Field(description="Unique feature identifier for this logical transaction")],
        feature_sequence: Annotated[int, Field(description="Monotonic sequence number within the feature stream")],
        correlation_id: Annotated[str, Field(description="Caller correlation ID for tracing and idempotency")],
        actions: Annotated[list[dict[str, Any]], Field(description="Ordered list of typed micro-chunk actions (create, insert, transform)")],
    ) -> dict[str, Any]:
        return await _run_native_mutation(
            mutation_coordinator,
            native_facade.feature_execute,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
            feature_id=feature_id,
            feature_sequence=feature_sequence,
            correlation_id=correlation_id,
            actions=actions,
        )

    @provider_tool(tags={"native", "entity", "write"})
    async def batch_create_entities(
        document_pid: Annotated[str, Field(description="Persistent document identifier matching active CAD drawing")],
        expected_parent_fp: Annotated[str, Field(description="Expected predecessor semantic fingerprint for optimistic locking")],
        entities: Annotated[list[dict[str, Any]], Field(description="List of generic typed CAD entity creation payloads (line, circle, arc, polyline, text)")],
    ) -> dict[str, Any]:
        return await _run_native_mutation(
            mutation_coordinator,
            native_facade.batch_create_entities,
            entities,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
        )

    @provider_tool(tags={"native", "block", "write"})
    async def batch_insert_blocks(
        document_pid: Annotated[str, Field(description="Persistent document identifier matching active CAD drawing")],
        expected_parent_fp: Annotated[str, Field(description="Expected predecessor semantic fingerprint for optimistic locking")],
        inserts: Annotated[list[dict[str, Any]], Field(description="List of persistent-PID-bound block insert specifications (name, point, scale, rotation)")],
    ) -> dict[str, Any]:
        return await _run_native_mutation(
            mutation_coordinator,
            native_facade.batch_insert_blocks,
            inserts,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
        )

    @provider_tool(tags={"native", "entity", "write"})
    async def batch_transform_entities(
        document_pid: Annotated[str, Field(description="Persistent document identifier matching active CAD drawing")],
        expected_parent_fp: Annotated[str, Field(description="Expected predecessor semantic fingerprint for optimistic locking")],
        semantic_pids: Annotated[list[str], Field(description="List of persistent semantic PIDs to transform")],
        transform: Annotated[dict[str, Any], Field(description="Typed transform specification with translate, rotate-Z, or uniform scale parameters")],
    ) -> dict[str, Any]:
        return await _run_native_mutation(
            mutation_coordinator,
            native_facade.batch_transform_entities,
            semantic_pids,
            transform,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
        )

    @provider_tool(tags={"native", "metadata", "read"})
    async def metadata_get(
        semantic_pid: Annotated[str, Field(description="Persistent semantic PID of the entity")],
        namespace: Annotated[str, Field(description="Schema-agnostic metadata namespace key to read")],
    ) -> dict[str, Any]:
        return await native_read(native_facade.metadata_get, semantic_pid, namespace)

    @provider_tool(tags={"native", "metadata", "write"})
    async def metadata_set(
        document_pid: Annotated[str, Field(description="Persistent document identifier matching active CAD drawing")],
        expected_parent_fp: Annotated[str, Field(description="Expected predecessor semantic fingerprint for optimistic locking")],
        semantic_pid: Annotated[str, Field(description="Persistent semantic PID of the entity")],
        namespace: Annotated[str, Field(description="Schema-agnostic metadata namespace key to set")],
        value: Annotated[Any, Field(description="JSON-serializable metadata payload")],
    ) -> dict[str, Any]:
        return await _run_native_mutation(
            mutation_coordinator,
            native_facade.metadata_set,
            semantic_pid,
            namespace,
            value,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
        )

    @provider_tool(tags={"native", "metadata", "read"})
    async def metadata_query(
        namespace: Annotated[str, Field(description="Schema-agnostic metadata namespace key to query")],
        path: Annotated[str | None, Field(description="Optional dotted JSON path within namespace to match")] = None,
        equals: Annotated[Any, Field(description="Optional exact JSON value to match at dotted path")] = None,
        limit: Annotated[int, Field(description="Maximum number of matched entities to return (default: 200)")] = 200,
        offset: Annotated[int, Field(description="Pagination starting offset index (default: 0)")] = 0,
    ) -> dict[str, Any]:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be an integer >= 0")
        return await native_read(
            native_facade.metadata_query,
            namespace,
            path=path,
            equals=equals,
            limit=limit,
            offset=offset,
        )

    @provider_tool(tags={"document", "write"})
    async def document_new() -> dict[str, Any]:
        return await backend.document_new()

    @provider_tool(tags={"document", "write"})
    async def document_open(
        path: Annotated[str, Field(description="Filesystem path to the existing DWG or DXF file to open")],
    ) -> dict[str, Any]:
        return await backend.document_open(path)

    @provider_tool(tags={"document", "read"})
    async def document_info() -> dict[str, Any]:
        return await backend.document_info()

    @provider_tool(tags={"document", "write"})
    async def document_configure_units(
        insertion_units: Annotated[str | None, Field(description="Drawing insertion units (e.g. 'Millimeters', 'Inches', 'Unitless')")] = None,
        measurement: Annotated[str | None, Field(description="AutoCAD measurement system ('Metric' or 'Imperial')")] = None,
        linear_format: Annotated[str | None, Field(description="Linear format ('Decimal', 'Scientific', 'Architectural', 'Engineering', 'Fractional')")] = None,
        linear_precision: Annotated[int | None, Field(description="Linear display precision decimal places (0 to 8)")] = None,
    ) -> dict[str, Any]:
        return await backend.document_configure_units(
            insertion_units, measurement, linear_format, linear_precision
        )

    @provider_tool(tags={"document", "read"})
    async def document_dependencies() -> dict[str, Any]:
        return await backend.document_dependencies()

    @provider_tool(tags={"document", "write"})
    async def artifact_seal(
        destination_dir: Annotated[str | None, Field(description="Optional destination directory for sealed artifact; uses allowed root if omitted")] = None,
    ) -> dict[str, Any]:
        return await backend.artifact_seal(destination_dir)

    @provider_tool(tags={"document", "write"})
    async def document_save(
        path: Annotated[str | None, Field(description="Optional file destination path; if omitted saves in-place to currently bound path")] = None,
    ) -> dict[str, Any]:
        return await backend.document_save(path)

    @provider_tool(tags={"document", "write"})
    async def document_save_as(
        path: Annotated[str, Field(description="Allowed destination filesystem path (.dwg or .dxf) for the saved drawing")],
    ) -> dict[str, Any]:
        return await backend.document_save_as(path)

    @provider_tool(tags={"document", "export"})
    async def document_export_pdf(
        path: Annotated[str, Field(description="Allowed destination filesystem path (.pdf) for exported drawing")],
        layout: Annotated[str | None, Field(description="Optional layout name to plot (e.g. 'Model' or 'Layout1'); defaults to active")] = None,
    ) -> dict[str, Any]:
        return await backend.document_export_pdf(path, layout)

    @provider_tool(tags={"document", "write"})
    async def drawing_audit() -> dict[str, Any]:
        return await backend.drawing_audit()

    @provider_tool(tags={"document", "write"})
    async def drawing_purge() -> dict[str, Any]:
        return await backend.drawing_purge()

    @provider_tool(tags={"object", "read"})
    async def object_list(
        type_filter: Annotated[str | None, Field(description="Optional entity type filter (e.g. 'LINE', 'CIRCLE', 'LWPOLYLINE', 'INSERT')")] = None,
        layer_filter: Annotated[str | None, Field(description="Optional layer name filter")] = None,
        limit: Annotated[int, Field(description="Maximum number of objects to return per page (default: 200)")] = 200,
        offset: Annotated[int, Field(description="Starting index offset for pagination (default: 0)")] = 0,
    ) -> list[dict[str, Any]]:
        rows = await backend.object_list(type_filter, layer_filter, limit, offset)
        return [row.to_dict() for row in rows]

    @provider_tool(tags={"object", "read"})
    async def object_get(
        object_id: Annotated[str, Field(description="Backend entity identifier or persistent CAD handle")],
    ) -> dict[str, Any]:
        return (await backend.object_get(object_id)).to_dict()

    @provider_tool(tags={"object", "read"})
    async def object_count(
        type_filter: Annotated[str | None, Field(description="Optional entity type filter (e.g. 'LINE', 'CIRCLE')")] = None,
        layer_filter: Annotated[str | None, Field(description="Optional layer name filter")] = None,
    ) -> int:
        return await backend.object_count(type_filter, layer_filter)

    @provider_tool(tags={"object", "read"})
    async def object_query(
        type_filter: Annotated[str | None, Field(description="Optional entity type filter (e.g. 'LINE', 'CIRCLE', 'INSERT')")] = None,
        layer_filter: Annotated[str | None, Field(description="Optional layer name filter")] = None,
        min_x: Annotated[FiniteFloat | None, Field(description="WCS bounding query window lower-left corner X")] = None,
        min_y: Annotated[FiniteFloat | None, Field(description="WCS bounding query window lower-left corner Y")] = None,
        max_x: Annotated[FiniteFloat | None, Field(description="WCS bounding query window upper-right corner X")] = None,
        max_y: Annotated[FiniteFloat | None, Field(description="WCS bounding query window upper-right corner Y")] = None,
        limit: Annotated[int, Field(description="Maximum objects to return (default: 200)")] = 200,
        offset: Annotated[int, Field(description="Pagination starting index (default: 0)")] = 0,
    ) -> dict[str, Any]:
        if not 1 <= int(limit) <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if int(offset) < 0:
            raise ValueError("offset must be >= 0")
        window_values = (min_x, min_y, max_x, max_y)
        has_window = any(value is not None for value in window_values)
        win_min_x: float = 0.0
        win_min_y: float = 0.0
        win_max_x: float = 0.0
        win_max_y: float = 0.0
        if has_window:
            if min_x is None or min_y is None or max_x is None or max_y is None:
                raise ValueError("spatial query requires min_x, min_y, max_x and max_y together")
            win_min_x = float(min_x)
            win_min_y = float(min_y)
            win_max_x = float(max_x)
            win_max_y = float(max_y)
            if win_min_x >= win_max_x or win_min_y >= win_max_y:
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
                if (
                    not isinstance(low, list)
                    or not isinstance(high, list)
                    or len(low) < 2
                    or len(high) < 2
                ):
                    continue
                intersects = not (
                    float(high[0]) < win_min_x
                    or float(low[0]) > win_max_x
                    or float(high[1]) < win_min_y
                    or float(low[1]) > win_max_y
                )
                if not intersects:
                    continue
                payload["measurement"] = measured
            matched.append(payload)
        page = matched[int(offset) : int(offset) + int(limit)]
        next_offset = int(offset) + len(page) if int(offset) + len(page) < len(matched) else None
        page_bytes = canonical_byte_size(page)
        return {
            "items": page,
            "count": len(page),
            "matched_count": len(matched),
            "source_count": total,
            "offset": int(offset),
            "limit": int(limit),
            "complete": total <= scan_limit,
            "scan_limit": scan_limit,
            "envelope_version": ENVELOPE_VERSION,
            "matched": len(matched),
            "source": total,
            "next_offset": next_offset,
            "byte_size": page_bytes,
            "estimated_tokens": estimated_tokens(page_bytes),
        }

    @provider_tool(tags={"object", "write"})
    async def object_set_properties(
        object_id: Annotated[str, Field(description="Backend entity identifier or persistent CAD handle")],
        layer: Annotated[str | None, Field(description="Target layer name. Layer must exist")] = None,
        color: Annotated[int | None, Field(description="AutoCAD Color Index (ACI 0-256; 0=ByBlock, 256=ByLayer)")] = None,
        linetype: Annotated[str | None, Field(description="Linetype name to assign (e.g. 'Continuous', 'DASHED')")] = None,
        visible: Annotated[bool | None, Field(description="Entity visibility flag (True=visible, False=hidden)")] = None,
    ) -> dict[str, Any]:
        return (
            await backend.object_set_properties(
                object_id, layer=layer, color=color, linetype=linetype, visible=visible
            )
        ).to_dict()

    @provider_tool(tags={"object", "write"})
    async def object_delete(
        object_id: Annotated[str, Field(description="Backend entity identifier or persistent CAD handle of object to delete")],
    ) -> dict[str, Any]:
        return await backend.object_delete(object_id)

    @provider_tool(tags={"object", "write"})
    async def object_move(
        object_id: Annotated[str, Field(description="Backend entity identifier or persistent CAD handle of object to move")],
        dx: Annotated[FiniteFloat, Field(description="WCS delta X translation in drawing units")],
        dy: Annotated[FiniteFloat, Field(description="WCS delta Y translation in drawing units")],
        dz: Annotated[FiniteFloat, Field(description="WCS delta Z translation in drawing units (default: 0.0)")] = 0.0,
    ) -> dict[str, Any]:
        return (await backend.object_move(object_id, dx, dy, dz)).to_dict()

    @provider_tool(tags={"object", "write"})
    async def object_copy(
        object_id: Annotated[str, Field(description="Backend entity identifier or persistent CAD handle of object to copy")],
        dx: Annotated[FiniteFloat, Field(description="WCS delta X offset in drawing units")],
        dy: Annotated[FiniteFloat, Field(description="WCS delta Y offset in drawing units")],
        dz: Annotated[FiniteFloat, Field(description="WCS delta Z offset in drawing units (default: 0.0)")] = 0.0,
    ) -> dict[str, Any]:
        return (await backend.object_copy(object_id, dx, dy, dz)).to_dict()

    @provider_tool(tags={"object", "write"})
    async def object_rotate(
        object_id: Annotated[str, Field(description="Backend entity identifier or persistent CAD handle of object to rotate")],
        base_x: Annotated[FiniteFloat, Field(description="WCS X coordinate of rotation center base point")],
        base_y: Annotated[FiniteFloat, Field(description="WCS Y coordinate of rotation center base point")],
        angle_deg: Annotated[FiniteFloat, Field(description="Rotation angle in degrees (counter-clockwise)")],
    ) -> dict[str, Any]:
        return (await backend.object_rotate(object_id, base_x, base_y, angle_deg)).to_dict()

    @provider_tool(tags={"object", "write"})
    async def object_scale(
        object_id: Annotated[str, Field(description="Backend entity identifier or persistent CAD handle of object to scale")],
        base_x: Annotated[FiniteFloat, Field(description="WCS X coordinate of scaling center base point")],
        base_y: Annotated[FiniteFloat, Field(description="WCS Y coordinate of scaling center base point")],
        factor: Annotated[FiniteFloat, Field(description="Positive uniform scaling multiplier (e.g. 2.0 = double size, 0.5 = half size)")],
    ) -> dict[str, Any]:
        return (await backend.object_scale(object_id, base_x, base_y, factor)).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_line(
        x1: Annotated[FiniteFloat, Field(description="Start X coordinate in WCS drawing units")],
        y1: Annotated[FiniteFloat, Field(description="Start Y coordinate in WCS drawing units")],
        x2: Annotated[FiniteFloat, Field(description="End X coordinate in WCS drawing units")],
        y2: Annotated[FiniteFloat, Field(description="End Y coordinate in WCS drawing units")],
        z1: Annotated[FiniteFloat, Field(description="Start Z coordinate in WCS drawing units (default: 0.0)")] = 0.0,
        z2: Annotated[FiniteFloat, Field(description="End Z coordinate in WCS drawing units (default: 0.0)")] = 0.0,
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
        color: Annotated[int | None, Field(description="AutoCAD Color Index (ACI 1-255). Uses ByLayer if omitted")] = None,
    ) -> dict[str, Any]:
        return (await backend.entity_create_line(x1, y1, x2, y2, z1, z2, layer, color)).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_circle(
        cx: Annotated[FiniteFloat, Field(description="Center X coordinate in WCS drawing units")],
        cy: Annotated[FiniteFloat, Field(description="Center Y coordinate in WCS drawing units")],
        radius: Annotated[FiniteFloat, Field(description="Positive circle radius in drawing units")],
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
        color: Annotated[int | None, Field(description="AutoCAD Color Index (ACI 1-255). Uses ByLayer if omitted")] = None,
    ) -> dict[str, Any]:
        return (await backend.entity_create_circle(cx, cy, radius, layer, color)).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_arc(
        cx: Annotated[FiniteFloat, Field(description="Center X coordinate in WCS drawing units")],
        cy: Annotated[FiniteFloat, Field(description="Center Y coordinate in WCS drawing units")],
        radius: Annotated[FiniteFloat, Field(description="Positive arc radius in drawing units")],
        start_angle: Annotated[FiniteFloat, Field(description="Start angle in degrees (counter-clockwise from +X axis)")],
        end_angle: Annotated[FiniteFloat, Field(description="End angle in degrees (counter-clockwise from +X axis)")],
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
        color: Annotated[int | None, Field(description="AutoCAD Color Index (ACI 1-255). Uses ByLayer if omitted")] = None,
    ) -> dict[str, Any]:
        return (
            await backend.entity_create_arc(cx, cy, radius, start_angle, end_angle, layer, color)
        ).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_polyline(
        points: Annotated[list[list[FiniteFloat]], Field(description="List of [X, Y] vertex coordinates in WCS drawing units")],
        closed: Annotated[bool, Field(description="Whether to close polyline connecting last vertex back to first (default: False)")] = False,
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
        color: Annotated[int | None, Field(description="AutoCAD Color Index (ACI 1-255). Uses ByLayer if omitted")] = None,
        bulges: Annotated[list[FiniteFloat] | None, Field(description="Optional list of bulge values for arc segments (bulge = tan(included_angle/4))")] = None,
        widths: Annotated[list[list[FiniteFloat]] | None, Field(description="Optional list of [start_width, end_width] for each segment")] = None,
        elevation: Annotated[FiniteFloat, Field(description="Z elevation of the 2D polyline plane (default: 0.0)")] = 0.0,
    ) -> dict[str, Any]:
        return (
            await backend.entity_create_polyline(
                points, closed, layer, color, bulges, widths, elevation
            )
        ).to_dict()

    @provider_tool(tags={"entity", "write"})
    async def entity_create_text(
        text: Annotated[str, Field(description="Text string content to display")],
        x: Annotated[FiniteFloat, Field(description="Insertion point X coordinate in WCS drawing units")],
        y: Annotated[FiniteFloat, Field(description="Insertion point Y coordinate in WCS drawing units")],
        height: Annotated[FiniteFloat, Field(description="Text height in drawing units (default: 2.5)")] = 2.5,
        rotation: Annotated[FiniteFloat, Field(description="Text rotation angle in degrees counter-clockwise (default: 0.0)")] = 0.0,
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
        color: Annotated[int | None, Field(description="AutoCAD Color Index (ACI 1-255). Uses ByLayer if omitted")] = None,
    ) -> dict[str, Any]:
        return (
            await backend.entity_create_text(text, x, y, height, rotation, layer, color)
        ).to_dict()

    @provider_tool(tags={"hatch", "write"})
    async def hatch_create(
        boundary_points: Annotated[list[list[FiniteFloat]], Field(description="List of [X, Y] coordinates defining a closed planar polygon boundary")],
        pattern: Annotated[str, Field(description="Hatch pattern name (e.g. 'SOLID', 'ANSI31', 'ANSI32', 'BRICK') (default: 'SOLID')")] = "SOLID",
        scale: Annotated[FiniteFloat, Field(description="Hatch pattern scale factor (default: 1.0)")] = 1.0,
        angle: Annotated[FiniteFloat, Field(description="Hatch pattern rotation angle in degrees (default: 0.0)")] = 0.0,
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
        color: Annotated[int | None, Field(description="AutoCAD Color Index (ACI 1-255). Uses ByLayer if omitted")] = None,
    ) -> dict[str, Any]:
        return (
            await backend.hatch_create(boundary_points, pattern, scale, angle, layer, color)
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_linear(
        x1: Annotated[FiniteFloat, Field(description="First extension line origin X coordinate in WCS")],
        y1: Annotated[FiniteFloat, Field(description="First extension line origin Y coordinate in WCS")],
        x2: Annotated[FiniteFloat, Field(description="Second extension line origin X coordinate in WCS")],
        y2: Annotated[FiniteFloat, Field(description="Second extension line origin Y coordinate in WCS")],
        dim_x: Annotated[FiniteFloat, Field(description="Dimension line definition point X coordinate in WCS")],
        dim_y: Annotated[FiniteFloat, Field(description="Dimension line definition point Y coordinate in WCS")],
        rotation: Annotated[FiniteFloat, Field(description="Dimension line angle in degrees (0 for horizontal, 90 for vertical)")] = 0.0,
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_linear(x1, y1, x2, y2, dim_x, dim_y, rotation, layer)
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_aligned(
        x1: Annotated[FiniteFloat, Field(description="First extension line origin X coordinate in WCS")],
        y1: Annotated[FiniteFloat, Field(description="First extension line origin Y coordinate in WCS")],
        x2: Annotated[FiniteFloat, Field(description="Second extension line origin X coordinate in WCS")],
        y2: Annotated[FiniteFloat, Field(description="Second extension line origin Y coordinate in WCS")],
        dim_x: Annotated[FiniteFloat, Field(description="Dimension line definition point X coordinate in WCS")],
        dim_y: Annotated[FiniteFloat, Field(description="Dimension line definition point Y coordinate in WCS")],
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
    ) -> dict[str, Any]:
        return (await backend.dimension_aligned(x1, y1, x2, y2, dim_x, dim_y, layer)).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_angular(
        vertex_x: Annotated[FiniteFloat, Field(description="Angle vertex point X coordinate in WCS")],
        vertex_y: Annotated[FiniteFloat, Field(description="Angle vertex point Y coordinate in WCS")],
        first_x: Annotated[FiniteFloat, Field(description="First ray endpoint X coordinate in WCS")],
        first_y: Annotated[FiniteFloat, Field(description="First ray endpoint Y coordinate in WCS")],
        second_x: Annotated[FiniteFloat, Field(description="Second ray endpoint X coordinate in WCS")],
        second_y: Annotated[FiniteFloat, Field(description="Second ray endpoint Y coordinate in WCS")],
        text_x: Annotated[FiniteFloat, Field(description="Dimension text and arc placement point X in WCS")],
        text_y: Annotated[FiniteFloat, Field(description="Dimension text and arc placement point Y in WCS")],
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_angular(
                vertex_x, vertex_y, first_x, first_y, second_x, second_y, text_x, text_y, layer
            )
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_radial(
        center_x: Annotated[FiniteFloat, Field(description="Arc or circle center point X coordinate in WCS")],
        center_y: Annotated[FiniteFloat, Field(description="Arc or circle center point Y coordinate in WCS")],
        chord_x: Annotated[FiniteFloat, Field(description="Curve chord contact point X coordinate in WCS")],
        chord_y: Annotated[FiniteFloat, Field(description="Curve chord contact point Y coordinate in WCS")],
        leader_length: Annotated[FiniteFloat, Field(description="Leader line length in drawing units")],
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_radial(
                center_x, center_y, chord_x, chord_y, leader_length, layer
            )
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_diametric(
        center_x: Annotated[FiniteFloat, Field(description="First chord point on circle diameter X coordinate in WCS")],
        center_y: Annotated[FiniteFloat, Field(description="First chord point on circle diameter Y coordinate in WCS")],
        chord_x: Annotated[FiniteFloat, Field(description="Opposite chord point on circle diameter X coordinate in WCS")],
        chord_y: Annotated[FiniteFloat, Field(description="Opposite chord point on circle diameter Y coordinate in WCS")],
        leader_length: Annotated[FiniteFloat, Field(description="Leader line length in drawing units")],
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
    ) -> dict[str, Any]:
        return (
            await backend.dimension_diametric(
                center_x, center_y, chord_x, chord_y, leader_length, layer
            )
        ).to_dict()

    @provider_tool(tags={"dimension", "write"})
    async def dimension_ordinate(
        definition_x: Annotated[FiniteFloat, Field(description="Feature definition point X coordinate in WCS")],
        definition_y: Annotated[FiniteFloat, Field(description="Feature definition point Y coordinate in WCS")],
        leader_x: Annotated[FiniteFloat, Field(description="Leader endpoint X coordinate in WCS")],
        leader_y: Annotated[FiniteFloat, Field(description="Leader endpoint Y coordinate in WCS")],
        axis: Annotated[str, Field(description="Measurement axis: 'x' or 'y' (default: 'x')")] = "x",
        layer: Annotated[str | None, Field(description="Target layer name. Uses active layer if omitted")] = None,
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
    async def layer_create(
        name: Annotated[str, Field(description="New layer name to create (must be unique in drawing)")],
        color: Annotated[int, Field(description="AutoCAD Color Index (ACI 1-255) (default: 7 - White/Black)")] = 7,
    ) -> dict[str, Any]:
        return (await backend.layer_create(name, color)).to_dict()

    @provider_tool(tags={"layer", "write"})
    async def layer_set_current(
        name: Annotated[str, Field(description="Name of existing layer to set as current active layer")],
    ) -> dict[str, Any]:
        return await backend.layer_set_current(name)

    @provider_tool(tags={"layer", "write"})
    async def layer_update_state(
        name: Annotated[str, Field(description="Name of existing layer to update")],
        is_on: Annotated[bool | None, Field(description="Layer visibility on/off flag")] = None,
        is_frozen: Annotated[bool | None, Field(description="Layer frozen state (cannot freeze current layer)")] = None,
        is_locked: Annotated[bool | None, Field(description="Layer locked state against edits")] = None,
        color: Annotated[int | None, Field(description="AutoCAD Color Index (ACI 1-255)")] = None,
        linetype: Annotated[str | None, Field(description="Linetype name to assign (e.g. 'Continuous', 'DASHED')")] = None,
        lineweight: Annotated[int | None, Field(description="Lineweight in 1/100 mm (e.g. 25 = 0.25mm)")] = None,
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
        include_xref_dependent: Annotated[bool, Field(description="Whether to include XREF-dependent blocks (default: False)")] = False,
        include_xrefs: Annotated[bool, Field(description="Whether to include external reference blocks (default: True)")] = True,
        name_filter: Annotated[str | None, Field(description="Optional substring filter on block name")] = None,
        limit: Annotated[int, Field(description="Maximum block definitions to return (default: 200)")] = 200,
        offset: Annotated[int, Field(description="Pagination offset index (default: 0)")] = 0,
    ) -> list[dict[str, Any]]:
        return [
            block.to_dict()
            for block in await backend.block_list(
                include_xref_dependent, include_xrefs, name_filter, limit, offset
            )
        ]

    @provider_tool(tags={"block", "write"})
    async def block_create(
        name: Annotated[str, Field(description="Unique block definition name")],
        object_ids: Annotated[list[str], Field(description="List of existing drawing entity IDs to copy into the block")],
        base_x: Annotated[FiniteFloat, Field(description="Block origin base point X in WCS drawing units (default: 0.0)")] = 0.0,
        base_y: Annotated[FiniteFloat, Field(description="Block origin base point Y in WCS drawing units (default: 0.0)")] = 0.0,
    ) -> dict[str, Any]:
        return (await backend.block_create(name, object_ids, base_x, base_y)).to_dict()

    @provider_tool(tags={"block", "write"})
    async def block_insert(
        name: Annotated[str, Field(description="Name of existing block definition to insert")],
        x: Annotated[FiniteFloat, Field(description="Insertion point X coordinate in WCS")],
        y: Annotated[FiniteFloat, Field(description="Insertion point Y coordinate in WCS")],
        scale_x: Annotated[FiniteFloat, Field(description="X scale multiplier (default: 1.0)")] = 1.0,
        scale_y: Annotated[FiniteFloat, Field(description="Y scale multiplier (default: 1.0)")] = 1.0,
        rotation: Annotated[FiniteFloat, Field(description="Insertion rotation angle in degrees (default: 0.0)")] = 0.0,
        layer: Annotated[str | None, Field(description="Target layer for the block reference. Uses active layer if omitted")] = None,
    ) -> dict[str, Any]:
        return (await backend.block_insert(name, x, y, scale_x, scale_y, rotation, layer)).to_dict()

    @provider_tool(tags={"xref", "read"})
    async def xref_list() -> list[dict[str, Any]]:
        return await backend.xref_list()

    @provider_tool(tags={"xref", "write"})
    async def xref_attach(
        path: Annotated[str, Field(description="Filesystem path to the external DWG/DXF reference file")],
        name: Annotated[str | None, Field(description="Block name for the XREF. Defaults to filename stem if omitted")] = None,
        overlay: Annotated[bool, Field(description="True for Overlay reference (not nested), False for Attach reference (default: True)")] = True,
        x: Annotated[FiniteFloat, Field(description="Insertion point X in WCS (default: 0.0)")] = 0.0,
        y: Annotated[FiniteFloat, Field(description="Insertion point Y in WCS (default: 0.0)")] = 0.0,
        z: Annotated[FiniteFloat, Field(description="Insertion point Z in WCS (default: 0.0)")] = 0.0,
        scale_x: Annotated[FiniteFloat, Field(description="X scale factor (default: 1.0)")] = 1.0,
        scale_y: Annotated[FiniteFloat, Field(description="Y scale factor (default: 1.0)")] = 1.0,
        scale_z: Annotated[FiniteFloat, Field(description="Z scale factor (default: 1.0)")] = 1.0,
        rotation: Annotated[FiniteFloat, Field(description="Rotation angle in degrees (default: 0.0)")] = 0.0,
        layer: Annotated[str | None, Field(description="Target layer name for XREF reference")] = None,
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
    async def xref_reload(
        name: Annotated[str, Field(description="Name of attached XREF to reload from disk")],
    ) -> dict[str, Any]:
        return await backend.xref_reload(name)

    @provider_tool(tags={"xref", "write"})
    async def xref_unload(
        name: Annotated[str, Field(description="Name of attached XREF to unload from memory")],
    ) -> dict[str, Any]:
        return await backend.xref_unload(name)

    @provider_tool(tags={"xref", "write"})
    async def xref_detach(
        name: Annotated[str, Field(description="Name of attached XREF to permanently detach and remove")],
    ) -> dict[str, Any]:
        return await backend.xref_detach(name)

    @provider_tool(tags={"layout", "read"})
    async def layout_list() -> dict[str, Any]:
        return await backend.layout_list()

    @provider_tool(tags={"layout", "write"})
    async def layout_create(
        name: Annotated[str, Field(description="Name for the new paper-space layout sheet")],
    ) -> dict[str, Any]:
        return await backend.layout_create(name)

    @provider_tool(tags={"layout", "write"})
    async def layout_set_current(
        name: Annotated[str, Field(description="Name of existing layout to activate (e.g. 'Model' or 'Layout1')")],
    ) -> dict[str, Any]:
        return await backend.layout_set_current(name)

    @provider_tool(tags={"viewport", "write"})
    async def viewport_create(
        layout: Annotated[str, Field(description="Name of paper-space layout to place the viewport on")],
        center_x: Annotated[FiniteFloat, Field(description="Viewport center X coordinate on paper sheet in paper units")],
        center_y: Annotated[FiniteFloat, Field(description="Viewport center Y coordinate on paper sheet in paper units")],
        width: Annotated[FiniteFloat, Field(description="Viewport width in paper units (mm or inches)")],
        height: Annotated[FiniteFloat, Field(description="Viewport height in paper units (mm or inches)")],
        view_center_x: Annotated[FiniteFloat, Field(description="Target model-space center X visible in viewport")],
        view_center_y: Annotated[FiniteFloat, Field(description="Target model-space center Y visible in viewport")],
        scale: Annotated[FiniteFloat, Field(description="Viewport scale factor (e.g. 0.02 for 1:50 scale) (default: 1.0)")] = 1.0,
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
    async def viewport_list(
        layout: Annotated[str | None, Field(description="Optional layout name filter. Lists viewports across all layouts if omitted")] = None,
    ) -> dict[str, Any]:
        return await backend.viewport_list(layout)

    @provider_tool(tags={"viewport", "write"})
    async def viewport_set_scale(
        handle: Annotated[str, Field(description="Persistent CAD handle of the viewport entity")],
        scale: Annotated[FiniteFloat, Field(description="Positive scale factor (paper units / model units)")],
    ) -> dict[str, Any]:
        return await backend.viewport_set_scale(handle, scale)

    @provider_tool(tags={"viewport", "write"})
    async def viewport_lock(
        handle: Annotated[str, Field(description="Persistent CAD handle of the viewport entity")],
        locked: Annotated[bool, Field(description="True to lock viewport display scale against zoom/pan, False to unlock (default: True)")] = True,
    ) -> dict[str, Any]:
        return await backend.viewport_lock(handle, locked)

    @provider_tool(tags={"viewport", "write"})
    async def viewport_delete(
        handle: Annotated[str, Field(description="Persistent CAD handle of the viewport entity to delete")],
        force: Annotated[bool, Field(description="Required force confirmation flag to delete non-provider viewports (default: False)")] = False,
    ) -> dict[str, Any]:
        return await backend.viewport_delete(handle, force)

    @provider_tool(tags={"view", "write"})
    async def view_zoom_extents() -> dict[str, Any]:
        return await backend.view_zoom_extents()

    @provider_tool(tags={"view", "write"})
    async def view_zoom_window(
        x1: Annotated[FiniteFloat, Field(description="Lower-left corner X coordinate in WCS")],
        y1: Annotated[FiniteFloat, Field(description="Lower-left corner Y coordinate in WCS")],
        x2: Annotated[FiniteFloat, Field(description="Upper-right corner X coordinate in WCS")],
        y2: Annotated[FiniteFloat, Field(description="Upper-right corner Y coordinate in WCS")],
    ) -> dict[str, Any]:
        return await backend.view_zoom_window(x1, y1, x2, y2)

    @provider_tool(tags={"view", "read"})
    async def view_screenshot() -> Image:
        return Image(data=await backend.view_screenshot(), format="png")

    @provider_tool(tags={"view", "write"})
    async def view_set_direction(
        dx: Annotated[FiniteFloat, Field(description="View direction vector X component from target to camera")],
        dy: Annotated[FiniteFloat, Field(description="View direction vector Y component from target to camera")],
        dz: Annotated[FiniteFloat, Field(description="View direction vector Z component from target to camera")],
    ) -> dict[str, Any]:
        return await backend.view_set_direction(dx, dy, dz)

    @provider_tool(tags={"view", "write"})
    async def view_set_preset(
        preset: Annotated[str, Field(description="Standard 3D view preset: 'top', 'bottom', 'left', 'right', 'front', 'back', 'sw_isometric', 'se_isometric', 'ne_isometric', 'nw_isometric' (default: 'se_isometric')")] = "se_isometric",
    ) -> dict[str, Any]:
        return await backend.view_set_preset(preset)

    @provider_tool(tags={"view", "write"})
    async def view_set_visual_style(
        style: Annotated[str, Field(description="Allowlisted visual style: '2d_wireframe', 'conceptual', 'hidden', 'realistic', 'shades_of_gray', 'sketchy', 'wireframe', 'x_ray' (default: 'shades_of_gray')")] = "shades_of_gray",
    ) -> dict[str, Any]:
        return await backend.view_set_visual_style(style)

    @provider_tool(tags={"analysis", "read"})
    async def object_measure(
        object_id: Annotated[str, Field(description="Backend entity identifier or persistent CAD handle to measure")],
    ) -> dict[str, Any]:
        return await backend.object_measure(object_id)

    @provider_tool(tags={"analysis", "read"})
    async def drawing_extents() -> dict[str, Any]:
        return await backend.drawing_extents()

    @provider_tool(tags={"analysis", "read"})
    async def object_intersections(
        first_id: Annotated[str, Field(description="First entity backend identifier or handle")],
        second_id: Annotated[str, Field(description="Second entity backend identifier or handle")],
        extend_mode: Annotated[str, Field(description="Intersection solver extension mode: 'none', 'extend_first', 'extend_second', 'extend_both' (default: 'none')")] = "none",
    ) -> dict[str, Any]:
        return await backend.object_intersections(first_id, second_id, extend_mode)

    @provider_tool(tags={"solid", "write"})
    async def solid_create_primitive(
        kind: Annotated[str, Field(description="3D solid primitive type: 'box', 'cylinder', 'sphere', 'cone', 'torus', 'wedge'")],
        parameters: Annotated[dict[str, FiniteFloat], Field(description="Primitive dimensions (e.g. length/width/height for box; radius/height for cylinder)")],
        layer: Annotated[str | None, Field(description="Target layer name for the solid entity")] = None,
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
            raise ValueError(
                "solid primitive kind must be one of: box, cylinder, sphere, cone, torus, wedge"
            )
        fields, method = spec
        supplied = set(parameters)
        required = set(fields)
        if supplied != required:
            raise ValueError(f"{normalized} parameters must contain exactly: " + ", ".join(fields))
        values = [float(parameters[field]) for field in fields]
        result = await method(*values, layer=layer)
        return {**result, "primitive": normalized}

    @provider_tool(tags={"solid", "write"})
    async def solid_extrude(
        profile_object_id: Annotated[str, Field(description="Backend entity ID of closed planar 2D profile to extrude")],
        height: Annotated[FiniteFloat, Field(description="Extrusion height in drawing units (must be non-zero)")],
        taper_angle: Annotated[FiniteFloat, Field(description="Taper angle in degrees between -89 and +89 (default: 0.0)")] = 0.0,
        layer: Annotated[str | None, Field(description="Target layer name for the resulting solid")] = None,
    ) -> dict[str, Any]:
        return await backend.solid_extrude(
            profile_object_id,
            height,
            taper_angle,
            layer=layer,
        )

    @provider_tool(tags={"solid", "write"})
    async def solid_sweep(
        profile_object_id: Annotated[str, Field(description="Backend entity ID of closed planar profile to sweep")],
        path_object_id: Annotated[str, Field(description="Backend entity ID of 2D/3D curve path (line, polyline, arc, spline)")],
        layer: Annotated[str | None, Field(description="Target layer name for the resulting solid")] = None,
    ) -> dict[str, Any]:
        return await backend.solid_sweep(
            profile_object_id,
            path_object_id,
            layer=layer,
        )

    @provider_tool(tags={"solid", "write"})
    async def solid_revolve(
        profile_object_id: Annotated[str, Field(description="Backend entity ID of closed planar profile to revolve")],
        axis_x1: Annotated[FiniteFloat, Field(description="Axis of revolution start point X in WCS")],
        axis_y1: Annotated[FiniteFloat, Field(description="Axis of revolution start point Y in WCS")],
        axis_z1: Annotated[FiniteFloat, Field(description="Axis of revolution start point Z in WCS")],
        axis_x2: Annotated[FiniteFloat, Field(description="Axis of revolution end point X in WCS")],
        axis_y2: Annotated[FiniteFloat, Field(description="Axis of revolution end point Y in WCS")],
        axis_z2: Annotated[FiniteFloat, Field(description="Axis of revolution end point Z in WCS")],
        angle_deg: Annotated[FiniteFloat, Field(description="Revolution angle in degrees (default: 360.0)")] = 360.0,
        layer: Annotated[str | None, Field(description="Target layer name for the resulting solid")] = None,
    ) -> dict[str, Any]:
        return await backend.solid_revolve(
            profile_object_id,
            axis_x1,
            axis_y1,
            axis_z1,
            axis_x2,
            axis_y2,
            axis_z2,
            angle_deg,
            layer=layer,
        )

    @provider_tool(tags={"solid", "write"})
    async def solid_boolean(
        target_handle: Annotated[str, Field(description="Handle of base ACIS 3DSOLID (modified in-place)")],
        tool_handle: Annotated[str, Field(description="Handle of tool ACIS 3DSOLID (consumed by operation)")],
        operation: Annotated[str, Field(description="Boolean operation: 'union', 'subtract', 'intersect'")],
    ) -> dict[str, Any]:
        return await backend.solid_boolean(target_handle, tool_handle, operation)

    @provider_tool(tags={"solid", "write"})
    async def solid_transform(
        handle: Annotated[str, Field(description="Handle of the ACIS 3DSOLID entity to transform")],
        operation: Annotated[str, Field(description="Transform operation: 'move', 'rotate', 'scale', 'mirror'")],
        parameters: Annotated[dict[str, FiniteFloat], Field(description="Operation parameters (e.g. dx,dy,dz for move; axis/angle for rotate; factor for scale; plane for mirror)")],
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
                raise ValueError(
                    "rotate parameters must contain exactly axis endpoints and angle_deg"
                )
            return await backend.solid_rotate3d(handle, *(float(parameters[key]) for key in fields))
        if normalized == "scale":
            fields = ("base_x", "base_y", "base_z", "factor")
            if set(parameters) != set(fields):
                raise ValueError(
                    "scale parameters must contain exactly base_x, base_y, base_z, factor"
                )
            return await backend.solid_scale3d(handle, *(float(parameters[key]) for key in fields))
        if normalized == "mirror":
            fields = ("x1", "y1", "z1", "x2", "y2", "z2", "x3", "y3", "z3")
            if set(parameters) != set(fields):
                raise ValueError("mirror parameters must contain exactly three WCS plane points")
            return await backend.solid_mirror3d(handle, *(float(parameters[key]) for key in fields))
        raise ValueError("solid transform operation must be one of: move, rotate, scale, mirror")

    @provider_tool(tags={"solid", "read"})
    async def solid_inspect(
        handle: Annotated[str, Field(description="Handle of the ACIS 3DSOLID entity to inspect")],
    ) -> dict[str, Any]:
        return await backend.solid_inspect(handle)

    @provider_tool(tags={"solid", "export"})
    async def solid_export(
        handles: Annotated[list[str], Field(description="List of ACIS 3DSOLID handles to export")],
        path: Annotated[str, Field(description="Allowed destination filesystem path (.sat) for ACIS export")],
        format: Annotated[str, Field(description="Export file format: 'sat' (default: 'sat')")] = "sat",
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
    app._cdt_mutation_coordinator = mutation_coordinator  # type: ignore[attr-defined]
    app._cdt_runtime = local_runtime  # type: ignore[attr-defined]
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
