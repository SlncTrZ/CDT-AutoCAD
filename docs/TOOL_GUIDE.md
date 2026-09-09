# AutoCAD Provider Tool Guide

> Contract version: `autocad-a2-v1-rc1` · Provider version: `0.3.0rc1` · Updated: 2026-09-09

## Runtime scope

This is the **A2 release-candidate 50-tool surface**. `ezdxf` remains the default backend; the `com`
backend is selected explicitly on Windows for live AutoCAD, native DWG, native plotting, viewport
management, live zoom and PNG capture.

A2 implementation is code-complete but **not acceptance-closed** until the primary AutoCAD 2027
Windows ActiveX lane passes. The provider reports this state explicitly rather than treating mock
coverage as live verification.

## Recommended workflow

1. `system_status` / `system_capabilities`
2. `document_new` or `document_open`
3. optional `transaction_begin`
4. organize with layers/layouts/blocks
5. create/query/modify geometry
6. `transaction_commit` or `transaction_rollback`
7. `document_save` / `document_save_as` / optional `document_export_pdf`

## Identity

- `help` — read-only provider guide and contract fingerprint.
- `system_status` — backend/runtime state, transaction state, primary live-certification target and,
  after COM attachment, detected AutoCAD application/version/release metadata.
- `system_capabilities` — machine-readable capability map for the selected backend.

## Documents

- `document_new`
- `document_open(path)`
- `document_info`
- `document_save(path?)`
- `document_save_as(path)`
- `document_export_pdf(path, layout?)`
- `drawing_audit`
- `drawing_purge`

Backend behavior:

- `ezdxf`: DXF read/write; native DWG is explicitly refused.
- `com`: native AutoCAD DWG/DXF lifecycle; paths still remain inside `CDT_AUTOCAD_ALLOWED_PATHS`.
- `ezdxf` PDF export requires the optional `render` dependency.
- `com` PDF export uses AutoCAD's native `DWG To PDF.pc3` plot path.

The COM backend uses AutoCAD 2018-format SaveAs constants for DWG/DXF, matching the native DWG
format generation used by modern AutoCAD releases. It never writes DXF bytes under a `.dwg` name.

## Query & modification

Read:

- `object_list(type_filter?, layer_filter?, limit=200, offset=0)`
- `object_get(object_id)`
- `object_count(type_filter?, layer_filter?)`

Modify:

- `object_set_properties(object_id, layer?, color?, linetype?, visible?)`
- `object_delete(object_id)`
- `object_move(object_id, dx, dy, dz=0)`
- `object_copy(object_id, dx, dy, dz=0)`
- `object_rotate(object_id, base_x, base_y, angle_deg)`
- `object_scale(object_id, base_x, base_y, factor)`

`object_id` is the native entity handle. List/count operate on the current Model/Paper-space layout.
Explicit layers/linetypes are validated before mutation where the contract requires them.

## Entity creation

- `entity_create_line`
- `entity_create_circle`
- `entity_create_arc`
- `entity_create_polyline`
- `entity_create_text`
- `hatch_create`
- `dimension_linear`
- `dimension_aligned`

Creation targets the current Model/Paper-space layout. COM calls are serialized through one STA
worker so ActiveX operations cannot race each other from concurrent MCP requests.

## Layers

- `layer_list`
- `layer_create(name, color=7)`
- `layer_set_current(name)`

## Blocks

- `block_list`
- `block_create(name, object_ids, base_x=0, base_y=0)`
- `block_insert(name, x, y, scale_x=1, scale_y=1, rotation=0, layer?)`

All source handles resolve before a new block definition is created. The COM path removes a partial
block definition if `CopyObjects` fails.

## Layouts / paper space

- `layout_list`
- `layout_create(name)`
- `layout_set_current(name)`

Layout names resolve case-insensitively. Creation/query operations follow the active layout.

A2 public tools additionally include:

- `viewport_create(layout, center_x, center_y, width, height, view_center_x, view_center_y, scale=1)`
- `viewport_list(layout?)`
- `viewport_set_scale(handle, scale)`
- `viewport_lock(handle, locked=true)`
- `viewport_delete(handle, force=false)`
- `view_zoom_extents`
- `view_zoom_window(x1, y1, x2, y2)`
- `view_screenshot` — PNG image content on the COM backend.

The headless backend refuses these with typed capability errors. Unknown/pre-existing live viewports
require `force=true` for deletion because ActiveX exposes no reliable main-viewport predicate.

## Transactions / undo

- `transaction_begin`
- `transaction_commit`
- `transaction_rollback`
- `undo`
- `redo`

`ezdxf` uses compressed bounded snapshots. `com` uses native AutoCAD undo marks. COM additionally
tracks the active document: switching documents while a tracked transaction is open is refused, so a
commit cannot accidentally close an undo mark in the wrong drawing.

A timed-out COM mutation is fundamentally different from a timed-out headless worker: the abandoned
STA call cannot be force-cancelled and **may still complete in AutoCAD**. The provider marks that state
uncertain and warns against blind retry because a retry can double-apply the mutation.

## Backend configuration

```text
CDT_AUTOCAD_BACKEND             ezdxf (default) | com
CDT_AUTOCAD_COM_PROGID          default AutoCAD.Application
CDT_AUTOCAD_COM_ATTACH_POLICY   attach_only (default) | attach_or_start
CDT_AUTOCAD_COM_TIMEOUT         COM deadline in seconds; default 60
```

`attach_only` is fail-closed: if no matching application is already running, the provider refuses
rather than starting AutoCAD. `attach_or_start` must be chosen explicitly.

Primary live certification targets **AutoCAD 2027 full / Windows x64**. Its versioned ProgID is
`AutoCAD.Application.26.0`; normal runtime remains unversioned by default. Other releases require an
explicit native compatibility matrix before they are called certified. See `docs/LIVE_ACCEPTANCE.md`.

The `com` optional dependency installs pywin32 plus Pillow; Pillow is used by native-window PNG
capture.

## Security / reliability

- File operations are contained to `CDT_AUTOCAD_ALLOWED_PATHS`.
- Input CAD size is bounded by `CDT_AUTOCAD_MAX_DXF_BYTES` at the current provider boundary.
- Headless calls use `CDT_AUTOCAD_CALL_TIMEOUT`; render/PDF calls use `CDT_AUTOCAD_RENDER_TIMEOUT`.
- Live COM calls use the separate `CDT_AUTOCAD_COM_TIMEOUT`.
- COM is lazy-attached and single-STA-thread serialized.
- Unknown/pre-existing live viewports are not deleted by staged A2 code unless `force=true`, because
  ActiveX exposes no reliable main-viewport predicate.
- HTTP transport requires `CDT_AUTOCAD_AUTH_TOKEN`; remote HTTP also requires explicit opt-in.
- Credentials are never returned by tools.
- No arbitrary AutoLISP, shell, macro or free-text command execution is exposed.

## Current explicit limitations

The A2 release candidate still does not publicly expose:

- staged A3.1 ACIS 3D solid/view tools;
- staged A3.2 angular/radial/diametric/ordinate dimension tools;
- staged A3.3 object measurement, current-space extents and native COM intersection analysis;
- advanced hatch editing/gradients;
- trim/offset/fillet;
- GDT.

A2 acceptance remains OPEN until the opt-in AutoCAD 2027 Windows lane validates native DWG,
A0/A1 parity, viewport operations, zoom, PNG capture and native PDF plotting. A3.1 is implemented
behind capability-false staging and has a broader native solid test lane. A3.2 is also implemented
backend-side for angular, radial, diametric and X/Y ordinate dimensions. A3.3 adds typed object
measurement/current-space WCS extents on both backends and exact `IntersectWith` analysis on COM;
ezdxf refuses generic intersections rather than approximating them. All A3.2/A3.3 capabilities stay
false and no additional MCP tools are exposed until their separate native AutoCAD gates pass.
