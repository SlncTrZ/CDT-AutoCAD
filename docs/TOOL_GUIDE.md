# CDT-AutoCAD Tool Guide

> Contract: `autocad-generic-v1-rc1` · Provider: `0.4.0rc1` · Public tools: `86` · Updated: 2026-09-11

CDT-AutoCAD is a generic CAD execution engine for AutoCAD workflows. It owns CAD execution,
document state, persistent identity, fingerprints, transaction/recovery mechanics and bounded
metadata. Engineering rules, standards and domain meaning belong to the caller.

The recommended production execution model is **Feature-based Chunks Streaming**: send one complete
logical feature at a time instead of one enormous drawing mutation or thousands of isolated LINE
calls. A feature can span many native micro-chunks, but it owns one predecessor checkpoint. If the
current feature fails, only that feature is restored; previously accepted features remain intact.

## Start here

For a normal live AutoCAD workflow:

1. call `system_status` and `system_capabilities`;
2. call `native_integrity_status` before using strong-integrity native tools;
3. open or create the drawing;
4. configure units/layers as needed;
5. execute production work with `feature_execute`;
6. inspect state with query/measurement tools;
7. save, seal or export the accepted artifact.

For presentation/showcase workflows, successful feature receipts recommend a **300 ms pause between
features**. Native micro-chunks themselves are not artificially delayed; the bridge processes at most
one batch mutation per AutoCAD Idle tick so the application remains responsive while a feature grows
on screen.

## Feature-based Chunks Streaming

### `feature_execute`

`feature_execute(feature_id, feature_sequence, correlation_id, actions)` is the preferred production
entry point for Domain Agents.

A feature may mix these generic action families:

- `create_entities` — typed LINE/CIRCLE/ARC/simple-LWPOLYLINE batches;
- `insert_blocks` — referenced block insertion by persistent block-definition PID;
- `transform_entities` — typed translate, rotate-Z or uniform-scale operations over persistent entity PIDs.

Example shape:

```json
{
  "feature_id": "plaza.centerline.001",
  "feature_sequence": 1,
  "correlation_id": "job-2026-09-11-001",
  "actions": [
    {
      "operation": "create_entities",
      "entities": [
        {"kind": "line", "start": [0, 0, 0], "end": [100, 0, 0]}
      ]
    }
  ]
}
```

`feature_id` is correlation data only. CDT-AutoCAD does not interpret names such as road, kiosk,
manhole, beam or pipe and does not contain TCVN/ISO/ASME/domain rules.

Each feature may contain up to 10,000 generic items. Internally the native bridge keeps bounded
micro-chunks of at most 32 items and yields between chunks. On success the receipt includes feature
identity, pre/post document fingerprints, affected semantic PIDs, native chunk count, journal path
and `recommended_next_delay_ms=300`.

On failure the current feature returns `ROLLED_BACK_VERIFIED` only after exact predecessor recovery.
The receipt identifies the feature, action index and native chunk index that failed. The caller can
recompute that feature and submit it again from the returned predecessor state. Earlier features are
not rolled back.

## Strong-integrity native tools

These tools require the live Windows COM profile and the same-session Managed .NET bridge. They do
not silently fall back to a weaker COM mutation path.

- `native_integrity_status`
- `feature_execute`
- `batch_create_entities`
- `batch_insert_blocks`
- `batch_transform_entities`
- `metadata_get`
- `metadata_set`
- `metadata_query`

The three `batch_*` tools expose the underlying logical-batch primitive directly. They are useful for
infrastructure and tests; production Domain Agents should normally prefer `feature_execute`.

Native state uses persistent document/entity PIDs, fingerprint schema v3 and parent-fingerprint drift
protection. A mutation must end as `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`; uncertain state
blocks dependent work.

## Schema-agnostic metadata

Metadata is stored in AutoCAD entity ExtensionDictionary/XRecord storage and participates in document
fingerprinting.

- `metadata_get(semantic_pid, namespace)`
- `metadata_set(semantic_pid, namespace, value)`
- `metadata_query(namespace, path?, equals?, limit=200)`

Namespaces such as `customer.mechanical.v1` are allowed. Provider-owned prefixes `slnctrz.*`,
`cdt.*` and `provider.*` are reserved. JSON payloads are bounded for size, depth, key count and native
numeric range. CDT-AutoCAD stores and compares values but does not interpret their domain meaning.

## Identity and runtime

- `help` — this guide plus contract SHA-256.
- `system_status` — provider/runtime/build/certification state.
- `system_capabilities` — backend capability map.
- `native_integrity_status` — live bridge version, active document PID/fingerprint and G3 invariants.

Primary live certification target: **AutoCAD 2027 full / Windows x64 / `AutoCAD.Application.26`**.
Other AutoCAD releases require their own compatibility evidence before they are called certified.

## Documents and artifacts

- `document_new`
- `document_open(path)`
- `document_info`
- `document_configure_units(insertion_units?, measurement?, linear_format?, linear_precision?)`
- `document_dependencies`
- `document_save(path?)`
- `document_save_as(path)`
- `document_export_pdf(path, layout?)`
- `drawing_audit`
- `drawing_purge`
- `artifact_seal(destination_dir?)`

`artifact_seal` saves the active drawing, requires a clean persisted state, writes a content-addressed
accepted copy and SHA-256 manifest, and returns provenance for handoff/release evidence.

`ezdxf` supports DXF; native DWG operations require the COM backend. All file paths remain contained
to configured allowed roots.

## Query and object editing

Read:

- `object_list(type_filter?, layer_filter?, limit=200, offset=0)`
- `object_get(object_id)`
- `object_count(type_filter?, layer_filter?)`
- `object_query(type_filter?, layer_filter?, min_x?, min_y?, max_x?, max_y?, limit=200, offset=0)`
- `object_measure(object_id)`
- `drawing_extents`
- `object_intersections(first_id, second_id, extend_mode="none")`

Modify:

- `object_set_properties`
- `object_delete`
- `object_move`
- `object_copy`
- `object_rotate`
- `object_scale`

The exact analysis surface depends on backend capability. Unsupported intersection/geometry solvers
fail closed instead of returning approximations silently.

## Entity creation and dimensions

- `entity_create_line`
- `entity_create_circle`
- `entity_create_arc`
- `entity_create_polyline`
- `entity_create_text`
- `hatch_create`
- `dimension_linear`
- `dimension_aligned`
- `dimension_angular`
- `dimension_radial`
- `dimension_diametric`
- `dimension_ordinate`

LWPOLYLINE creation supports bulges, per-vertex start/end widths and elevation where the selected
backend supports them.

## Layers, blocks and XREFs

Layers:

- `layer_list`
- `layer_create`
- `layer_set_current`
- `layer_update_state`

Blocks:

- `block_list(include_xref_dependent=false, include_xrefs=true, name_filter?, limit=200, offset=0)`
- `block_create`
- `block_insert`

External references:

- `xref_list`
- `xref_attach`
- `xref_reload`
- `xref_unload`
- `xref_detach`

XREF source paths are canonicalized and checked against configured allowed roots before AutoCAD side
effects. Dependency reports distinguish contained/resolved/loaded state rather than hiding unresolved
or outside-root references.

## Layouts, viewports and view presentation

Layouts:

- `layout_list`
- `layout_create`
- `layout_set_current`

Viewports:

- `viewport_create`
- `viewport_list`
- `viewport_set_scale`
- `viewport_lock`
- `viewport_delete`

Views:

- `view_zoom_extents`
- `view_zoom_window`
- `view_screenshot`
- `view_set_direction`
- `view_set_preset`
- `view_set_visual_style`

The product preset registry keeps common presentation setup bounded. For 3D showcase work the default
workflow is **SE Isometric + Shades of Gray**. Arbitrary free-text AutoCAD command execution is not
exposed.

## Native ACIS 3D

- `solid_create_primitive`
- `solid_extrude`
- `solid_sweep`
- `solid_revolve`
- `solid_boolean`
- `solid_transform`
- `solid_inspect`
- `solid_export`

Verified native inspection includes volume, centroid and WCS bounding box. ActiveX does not provide a
deterministic typed face/edge topology API, so face/edge topology is not claimed. The verified export
path is **SAT**. STEP/STL export, solid edge fillet/chamfer and shell operations are explicitly refused
instead of being emulated with arbitrary command strings.

## Transactions and recovery

Legacy/common transaction tools remain available:

- `transaction_begin`
- `transaction_commit`
- `transaction_rollback`
- `undo`
- `redo`

For production native execution, `feature_execute`/G3 recovery is stronger than ordinary undo scope:
one immutable predecessor checkpoint protects the current feature while short native transactions are
committed between AutoCAD Idle yields. A failed feature restores that checkpoint exactly and leaves
previously accepted features alone.

## Configuration

```text
CDT_AUTOCAD_ALLOWED_PATHS       allowed CAD/PDF/SAT roots
CDT_AUTOCAD_MAX_DXF_BYTES       input CAD size boundary; default 50 MiB
CDT_AUTOCAD_CALL_TIMEOUT        headless call deadline; default 120 s
CDT_AUTOCAD_RENDER_TIMEOUT      render/PDF deadline; default 300 s
CDT_AUTOCAD_UNDO_DEPTH          headless snapshot undo depth; default 10
CDT_AUTOCAD_TRANSACTION_DEPTH   tracked transaction depth; default 8
CDT_AUTOCAD_BACKEND             ezdxf (default) | com
CDT_AUTOCAD_COM_PROGID          default AutoCAD.Application
CDT_AUTOCAD_COM_ATTACH_POLICY   attach_only (default) | attach_or_start
CDT_AUTOCAD_COM_TIMEOUT         live COM deadline; default 60 s
CDT_AUTOCAD_AUTH_TOKEN          required for HTTP transport
CDT_AUTOCAD_ALLOW_REMOTE_HTTP   explicit opt-in for non-loopback HTTP bind
```

`attach_only` is the safe default. A timed-out COM mutation may still complete inside AutoCAD, so the
provider treats unknown completion as a state-integrity event rather than blindly retrying it.

## Security and refusal policy

- no arbitrary AutoLISP, shell, macro or caller-provided AutoCAD command execution;
- allowed-root containment for file operations and XREF sources;
- bounded requests, metadata and native semantic work;
- persistent PID + fingerprint drift guards for native mutation;
- credentials are configuration only and are never returned by tools;
- unsupported capabilities fail explicitly;
- a state marked uncertain, rollback-failed or commit-integrity-failed blocks dependent mutation.

## Current verified scale

The G3 native lane has live AutoCAD 2027 graduation evidence at **100, 1,000, 5,000 and 10,000**
entities. Every tier includes beginning/middle/end failure injection with exact predecessor recovery,
zero pending recovery state and process-stability checks. Native micro-chunks remain capped at 32
entities; current semantic capacity is 12,288 entities for the graduated lane.

Feature-streaming acceptance additionally proves: Feature 1 commits, Feature 2 commits its first
micro-chunk then fails and rolls back only Feature 2, Feature 1 remains exact, and Feature 3 can
continue immediately afterward. The observed presentation delay in that live run was 300.295 ms for
the configured 300 ms feature pacing.

Canonical evidence artifacts are retained with the internal acceptance record under these file names and are not part of the published tree:

- `feature-stream-production-2026-09-11.json`
- `g23-live-2026-09-11.json`
- `g3-scale-100-2026-09-11.json`
- `g3-scale-1000-2026-09-11.json`
- `g3-scale-5000-2026-09-11.json`
- `g3-scale-10000-2026-09-11.json`
