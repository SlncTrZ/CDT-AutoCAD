# PLAN — AutoCAD Provider

> Lane: A · Target repo: `CDT-AutoCAD` · Updated: 2026-09-09
> Governing docs: `MCP_PROVIDER_STANDARD.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`

## 1. Objective

AutoCAD is Lane A, the first implemented provider and the current migration-first runtime because it already contains active A2/A3 work. After a clean checkpoint, its runtime moves to independent repo `CDT-AutoCAD`. It remains a reference provider used to validate:

- SlncTrZ provider compliance;
- CDT common CAD contract;
- provider extension contract;
- dual-engine capability honesty;
- test strategy before shared runtime extraction.

Success means an MCP client can safely create/open a design, inspect geometry, create/modify entities, save/export, and understand exactly which capabilities the active engine supports.

## 2. Architecture

```text
MCP tools
   ↓
AutoCAD provider contract
   ↓
┌──────────────────────┐
│ backend abstraction  │
└─────────┬────────────┘
          ├── ezdxf backend — headless DXF, cross-platform
          └── COM backend   — live AutoCAD, Windows, native DWG
```

The provider exposes bare MCP tools. SlncTrZ-MCP canonicalizes them to `autocad.<tool>`.

No AutoCAD business logic belongs in SlncTrZ-MCP.

## 3. Contract Mapping

### 3.1 Common CAD contract mapping

| Common semantic | AutoCAD mapping |
| --- | --- |
| document lifecycle | drawing new/open/info/save/close |
| object query | entity list/get/count |
| transforms | move/copy/rotate/scale |
| organization | layers; blocks remain richer extension |
| units/coordinates | drawing units + WCS/OCS context |
| transactions | COM native undo or ezdxf snapshots |
| import/export | DXF/DWG/PDF according to backend capability |
| validation | audit/geometry/document checks |

### 3.2 AutoCAD extension

AutoCAD-specific families:

- DWG/DXF behavior;
- layers/linetypes;
- blocks/references;
- layouts/paperspace/viewports;
- dimensions;
- hatch;
- trim/offset/fillet;
- plotting/PDF;
- GDT/tolerance annotations;
- AutoCAD live application state;
- 3D solids where COM supports them.

## 4. Dual-Engine Capability Rules

| Capability | ezdxf | COM |
| --- | --- | --- |
| DXF read/write | native | via AutoCAD |
| DWG read/write | unsupported unless explicit converter layer exists | native |
| basic 2D entities | yes | yes |
| layers/blocks/layouts | yes, with library limits | native |
| live AutoCAD UI | no | yes |
| native viewport capture | no | yes |
| 3D ACIS solids | no | yes where API supports |
| transactions | snapshot mode | native application undo/transaction semantics |

No silent downgrade. Example: `drawing_save_as("x.dwg")` on ezdxf must refuse rather than write DXF bytes with a `.dwg` extension.

## 5. Reuse Strategy

Primary reference: `_private/reference/autocad/Autocad-MCP` (MIT).

Secondary reference: `_private/reference/autocad/autocad-mcp-ks40` (MIT).

### Reuse candidates

- typed backend contract pattern;
- COM + ezdxf split;
- capability/refusal pattern;
- entity conversion helpers;
- path/command validation ideas;
- timeout/quarantine behavior;
- correctness tests/golden DXF fixtures;
- backend parity tests.

### Must be normalized for CDT

- tool naming/surface;
- provider help payload;
- auth and lifecycle;
- common-vs-extension contract;
- error vocabulary;
- config ownership;
- observability;
- SlncTrZ namespace behavior.

Do not copy the upstream monolithic server surface wholesale.

## 6. Implementation Phases

### A0 — Headless Reference Baseline ✅ VERIFIED 2026-09-09

Goal: a small but real provider that runs without AutoCAD installed.

Required runtime:

- Python 3.11+;
- FastMCP;
- ezdxf;
- pydantic/schema validation.

Required MCP tools/capabilities:

```text
help
system_status
system_capabilities

document_new
document_open
document_info
document_save
document_save_as

object_list
object_get
object_count

entity_create_line
entity_create_circle
entity_create_arc
entity_create_polyline
entity_create_text

layer_list
layer_create
layer_set_current
```

Required controls:

- path allow-root validation;
- bounded input file size;
- bounded execution time;
- fail-closed network auth;
- structured errors;
- no secret logging;
- correct extension/format behavior.

Tests:

- backend unit tests;
- common contract tests;
- capability honesty tests;
- DXF golden roundtrip;
- path traversal rejection;
- HTTP auth/discovery/help smoke test.

### A1 — Headless Production Baseline ✅ VERIFIED 2026-09-09

Add:

- object properties/edit;
- delete/move/copy/rotate/scale;
- blocks;
- dimensions;
- layouts/paperspace;
- hatch;
- audit/purge;
- PDF export when rendering dependency is present;
- snapshot undo/rollback;
- filtering/pagination;
- result correctness tests.

### A2 — COM Live Backend

Windows + AutoCAD lane:

- attach/start policy as explicitly configured;
- active document lifecycle;
- native DWG;
- entity parity for A0/A1 common operations;
- viewport/screenshot;
- native layouts/plotting;
- native undo/transaction behavior;
- COM timeout and unavailable-app errors;
- version/progid handling.

COM tests should use mocks for generic CI and a real AutoCAD integration lane where infrastructure permits.

### A3 — Advanced Drafting/Engineering

- trim/offset/fillet;
- selection window/crossing/polygon;
- advanced dimensions;
- GDT;
- engineering validation;
- 3D solids via COM;
- batch tools only if atomicity/error semantics are clear.

### A4 — Surface/Scalability Hardening

Only after actual tool count and client latency are measured:

- lean/full/search profiles if needed;
- alias/discovery index;
- tool metadata optimization;
- benchmark latency and idle-token cost;
- contract/version fingerprint automation.

Do not add discovery complexity before measurements justify it.

## 7. Backend Contract Shape

Implementation should prefer focused interfaces, not one giant ABC.

Candidate domains:

```text
IdentityContract
DocumentContract
ObjectQueryContract
EntityCreationContract
EntityModificationContract
LayerContract
BlockContract
LayoutContract
DimensionContract
TransactionContract
AnalysisContract
ViewContract
SolidContract
```

Optional engine-specific behavior should use typed capability refusal rather than forcing every backend to fake an implementation.

## 8. Capability Honesty Gate

For every optional capability:

1. stable capability key exists;
2. active backend reports supported/unsupported;
3. supported path has a correctness test;
4. unsupported path raises typed refusal;
5. `system_capabilities` and runtime behavior cannot disagree.

Examples:

```text
common.document.open
autocad.dxf.write
autocad.dwg.write
autocad.viewport.capture
autocad.solid.acis
```

## 9. Security

- Never expose arbitrary AutoLISP/command execution in A0/A1.
- If scripting is later needed, it must be a separately authorized capability with strict input policy.
- All file paths resolve against configured roots.
- Write/destructive tools document side effects.
- Original files are not overwritten by default unless the operation explicitly targets the current document and policy permits it.
- Errors redact internal secrets/config.

## 10. Completion Gates

### A0 complete when

- provider starts cleanly;
- `/mcp` works with auth;
- `help` and capability map reflect actual runtime;
- basic DXF workflow passes end-to-end;
- tests/lint pass;
- docs match runtime.

### AutoCAD provider baseline complete when

- A0 + A1 pass headless;
- A2 COM parity passes on Windows with real AutoCAD;
- integration checklist in `MCP_PROVIDER_STANDARD.md` passes;
- no known capability declaration mismatch;
- no unsafe path/format behavior.

## 11. Current Status / Next Step

**A0 + A1 are CLOSED. A2 implementation is release-candidate complete; A2 live acceptance is blocked by environment. A3 3D ACIS implementation is now staged.**

Current public runtime identity:

```text
provider_version: 0.3.0rc1
contract_version: autocad-a2-v1-rc1
default_backend: ezdxf
public MCP tools: 50
```

### A2 release-candidate status

A2 now publicly exposes the A1 surface plus 8 COM/live extension tools:

- viewport create/list/set-scale/lock/delete;
- zoom extents/window;
- PNG screenshot.

The headless backend returns typed capability refusal for those live-only operations. The COM backend
has explicit ProgID + attach policy, a single STA executor, native DWG/DXF, native plotting, ActiveX
undo marks, active-document scope protection, timeout uncertainty semantics and conservative
pre-existing viewport deletion (`force=true`).

A2 **cannot honestly be marked acceptance-CLOSED yet**. The reachable Windows development machine
was checked directly and currently has no AutoCAD installation registered or present in standard
Autodesk install locations; therefore the real ActiveX integration lane cannot run. This is an
environmental blocker, not a substituted mock PASS.

### A3.1 — Native ACIS 3D solids staged

The COM backend now has a focused `SolidContract` implementation for:

- BOX / CYLINDER / SPHERE / CONE primitives;
- closed planar profile → EXTRUDE;
- closed planar profile → REVOLVE around an arbitrary 3D axis;
- profile → SWEEP along Autodesk-supported Arc/Circle/Ellipse/Polyline/Spline paths;
- Boolean UNION / SUBTRACT / INTERSECT with validated distinct solid handles;
- 3D MOVE and `Rotate3D`;
- solid inspection: type, layer, visibility, volume, centroid and bounding box;
- normalized arbitrary 3D view direction + zoom extents.

Temporary Regions used by extrusion/revolve/sweep are deleted on success and also cleaned after
operation failure without masking the primary modeling error. Sweep path types are validated before
COM to avoid opaque HRESULT failures. Loft is explicitly not claimed because ActiveX has no typed
Loft creation method in the supported API surface.

A3 methods are intentionally backend-staged, not public MCP tools yet. `autocad.solid.acis` and
`autocad.view.3d` remain capability-false with reason `A3_staged_pending_live_verification` until a
real AutoCAD lane proves the behavior.

### Verification evidence

Current generic Linux verification after A2 RC + A3.1:

- A2 contract/help gate: `11 passed`;
- A3 focused gate: `7 passed, 1 skipped`;
- combined A2+A3 targeted gate: `19 passed, 1 skipped`;
- full regression before final hygiene: `51 passed, 2 skipped`;
- the two skips are deliberate real-Windows/AutoCAD live lanes;
- provider runtime/package versions are locked by regression test (`pyproject.toml` == `__version__`);
- Ruff remains unavailable because the existing `.deps` has the Python wrapper but no native Ruff
  binary; no dependency was installed outside approved scope.

### Next gates

1. Install/restore AutoCAD on a reachable Windows lane.
2. Run `CDT_AUTOCAD_LIVE_TEST=1 pytest -q tests/test_com_backend.py tests/test_com_3d.py`.
3. Run full A0/A1 parity using `CDT_AUTOCAD_BACKEND=com`.
4. If A2 live passes, promote `0.3.0rc1 / autocad-a2-v1-rc1` to final A2 identity.
5. If A3 live passes, expose the staged solid/view methods as the next provider extension contract.
6. Continue A3 drafting/engineering families (trim/offset/fillet, selection, advanced dimensions,
   GDT) after the 3D lane is proven.

Do not extract shared runtime from this lane. After repo split, any reusable infrastructure must be proposed through Rule-of-Two evidence and may enter `CDT-Provider-Kit` only after cross-provider conformance proves equivalent semantics.
