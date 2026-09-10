# PLAN — AutoCAD Provider

> Lane: A · Target repo: `CDT-AutoCAD` · Updated: 2026-09-10
> Governing docs: `MCP_PROVIDER_STANDARD.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`, `docs/DRAWING_QUALITY_ACCEPTANCE.md`, `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`

## 1. Objective

AutoCAD is Lane A and the first implemented provider. Its runtime has been extracted history-preserving into this independent `CDT-AutoCAD` repository; the governing control-plane specs remain pinned to `CDT_Engineer@643019c`. It remains a reference provider used to validate:

- SlncTrZ provider compliance;
- CDT common CAD contract;
- provider extension contract;
- dual-engine capability honesty;
- test strategy before shared runtime extraction.

Success means an MCP client can safely create/open a design, inspect geometry, create/modify entities, save/export, and understand exactly which capabilities the active engine supports.

## 2. Architecture

### 2.1 Current runtime baseline

```text
MCP tools
   ↓
Python AutoCAD provider contract
   ↓
backend abstraction
   ├── ezdxf backend — headless DXF, cross-platform
   └── COM backend   — live AutoCAD, Windows, native DWG
```

This remains the shipped/live-verified migration baseline.

### 2.2 Accepted target architecture

```text
MCP / SlncTrZ Gateway
        ↓
Python Provider / Semantic Core
  ActionSpec · SourceSemanticModel
  PID/Fingerprint · Diff · Validation · Rollback/Audit
        ↓ local typed IPC
C# AutoCAD Managed .NET Native Bridge (in acad.exe)
  Document/Database/ObjectId · Transactions · PID metadata
  Semantic extraction · Event-assisted delta · Read-back
        ↓
AutoCAD Database / DWG
```

The target is governed by `ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md` and `SEMANTIC_STATE_PROTOCOL.md`. Two pillars are mandatory: **Data Integrity / Rollback** and **Precise Identity / PID + Fingerprinting**.

The provider exposes bare MCP tools. SlncTrZ-MCP canonicalizes them to `autocad.<tool>`. No AutoCAD business logic belongs in SlncTrZ-MCP. The .NET bridge is an in-process native adapter, not the MCP server.

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
- advanced dimensions (A3.2 backend-staged);
- measurement/intersection analysis (A3.3 backend-staged);
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

### 8.1 Drawing-quality acceptance gate

Reference-driven and user-reviewed drawings are governed by `docs/DRAWING_QUALITY_ACCEPTANCE.md` in addition to provider/runtime gates.

The gate applies across architectural floor plans, site plans, parks, plazas, landscape plans, master/urban plans, parking/circulation plans, roads/access layouts, elevations, sections, details and source reproductions. Before drawing, select one or more drawing profiles and apply the profile-specific domain checks.

Drawing acceptance is fail-closed and advances only through:

```text
TECHNICAL_PASS -> GEOMETRY_PASS -> DOMAIN_PASS -> VISUAL_PASS -> USER_ACCEPTED
```

A successful COM/API call, entity count, valid DWG save or agent-readable screenshot is not sufficient for final drawing acceptance.

Linetypes and lineweights are semantic requirements. Continuous thick/medium/thin, dashed/hidden, dashed-overhead, center/chain, cutting-plane, phantom, break, hatch, property/boundary, setback/easement, underground utility, existing/proposed/removal, contour and demolition conventions must be used whenever the drawing condition requires them. A semantically required dashed/hidden/center/other line that is missing or visually collapses to continuous because of linetype scale is a drawing defect.

For live user-observed stress tests, preserve native DWG + screenshot evidence, verify the final displayed/plotted appearance, and keep the checkpoint pending until the reviewer can actually access the evidence and approves it.

Execution is step-gated by `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`: one small semantic change at a time; native semantic extraction, PID/fingerprinting, deterministic diff/validation and commit-or-verified-rollback after every step; append-only state-chain logs; Data-vs-Data source comparison at major gates. Screenshot/Vision is supplemental rather than the geometry oracle.

## 9. Security

- Never expose arbitrary AutoLISP, C#, shell, macro or free-text AutoCAD command execution.
- AutoLISP may remain an explicit compatibility/user-extension mechanism, but not the core execution backend and never a substitute for independent semantic read-back.
- Native bridge IPC accepts typed operations only and must be local/policy-constrained by default.
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

**A0 + A1 are CLOSED. A2 is release-candidate complete and live-verified on AutoCAD 2027; RC identity is retained pending explicit promotion. A3.1/A3.2/A3.3 are live-verified but staged. The next development lane is the N-series Native Bridge + Semantic State architecture upgrade.**

Current public runtime identity:

```text
provider_version: 0.3.0rc1
contract_version: autocad-a2-v1-rc1
default_backend: ezdxf
public MCP tools: 50
```


### N-series — Native Bridge + Semantic State architecture upgrade

The detailed plan is `docs/ARCHITECTURE_UPGRADE_PLAN.md`; acceptance is `docs/NATIVE_BRIDGE_ACCEPTANCE.md`.

- `N0` documentation/architecture freeze — **CLOSED**.
- `N1` typed semantic models + canonical fingerprint engine — **CLOSED / PASS** (`31` focused semantic tests; Windows full regression `113 passed, 5 skipped`).
- `N2` persistent document/entity PID prototype and clone policy — **CLOSED / LIVE PASS** (AutoCAD 2027 P0–P10; evidence `docs/evidence/n2-pid-native-2026-09-10.json`).
- `N3` C# Managed .NET bridge + local typed IPC skeleton — **NEXT**.
- `N4` native read-only semantic extractor.
- `N5` native transactional executor + verified rollback.
- `N6` deterministic validator + semantic delta + state-chain engine.
- `N7` two-phase commit integrity and independent post-commit read-back.
- `N8` incremental COM-to-.NET operation-family migration with parity evidence.
- `N9` reference-driven drawing workflow migration to Data-first Semantic State Loop.
- `N10` explicit public promotion/contract decision.

Non-negotiable N-series gates:

```text
mutation -> COMMITTED_VERIFIED
       or -> ROLLED_BACK_VERIFIED
anything else -> BLOCK

current_parent_fp must equal expected_parent_fp before every mutation
```

`ObjectId` is transient runtime identity and native Handle alone is not sufficient semantic identity. The target design uses provider-owned persistent document/entity PID plus versioned content fingerprints and explicit clone/duplicate policy.

### A2 release-candidate status

A2 now publicly exposes the A1 surface plus 8 COM/live extension tools:

- viewport create/list/set-scale/lock/delete;
- zoom extents/window;
- PNG screenshot.

The headless backend returns typed capability refusal for those live-only operations. The COM backend
has explicit ProgID + attach policy, a single STA executor, native DWG/DXF, native plotting, ActiveX
undo marks, active-document scope protection, timeout uncertainty semantics and conservative
pre-existing viewport deletion (`force=true`).

A2 native acceptance is **live-verified PASS** on full AutoCAD 2027 / Windows `.171` using
`AutoCAD.Application.26`. Runtime status identifies the attached ActiveX COM version/release and
reports the certification target. The provider remains `0.3.0rc1` until an explicit promotion/release
decision; live verification alone does not change public capability or tool surface.

### A3.1 — Native ACIS 3D solids staged

The COM backend now has a focused `SolidContract` implementation for:

- BOX / CYLINDER / SPHERE / CONE / TORUS / WEDGE primitives;
- closed planar profile → EXTRUDE;
- closed planar profile → REVOLVE around an arbitrary 3D axis;
- profile → SWEEP along Autodesk-supported Arc/Circle/Ellipse/Polyline/Spline paths;
- Boolean UNION / SUBTRACT / INTERSECT with validated distinct solid handles;
- 3D polyline sweep-path creation;
- 3D MOVE, `Rotate3D`, `ScaleEntity` and `Mirror3D`;
- solid inspection: type, layer, visibility, volume, centroid and bounding box;
- normalized arbitrary 3D view direction + zoom extents.

Temporary Regions used by extrusion/revolve/sweep are deleted on success and also cleaned after
operation failure without masking the primary modeling error. Sweep path types are validated before
COM to avoid opaque HRESULT failures. Loft is explicitly not claimed because ActiveX has no typed
Loft creation method in the supported API surface.

A3 methods are intentionally backend-staged, not public MCP tools yet. The A3.1 native lane is
live-verified on AutoCAD 2027, but `autocad.solid.acis` and `autocad.view.3d` remain capability-false
until an explicit promotion changes the public contract.

### A3.2 — Advanced dimensions staged

The backend contract now contains four additional dimension primitives with shared fail-closed
validation and no public tool promotion:

- angular dimension from vertex + two rays + text point;
- radial dimension from center + chord point + positive leader length;
- diametric dimension from center + chord point + positive leader length;
- X/Y ordinate dimension from definition point + leader endpoint.

The ezdxf backend uses native dimension builders and has a real DXF save/reopen correctness test.
The COM backend maps to typed ActiveX `AddDimAngular`, `AddDimRadial`, `AddDimDiametric` and
`AddDimOrdinate`; no `SendCommand` path is used. Generic COM object normalization treats all
`AcDb*Dimension` object types as common `DIMENSION` entities.

`autocad.dimensions.advanced` remains capability-false (`A3_2_staged_not_public` on ezdxf and
staged/non-public on COM) despite its AutoCAD 2027 native PASS. Public MCP surface remains exactly
50 tools; live evidence is retained in `tests/test_advanced_dimensions.py` JUnit runs.

### A3.3 — Measurement / intersection analysis staged

A focused `AnalysisContract` now provides three backend-internal verification primitives:

- `object_measure(object_id)` for exact typed metrics plus WCS bounding box;
- `drawing_extents()` for the combined WCS bounding box of the current space;
- `object_intersections(first_id, second_id, extend_mode)` for exact intersection points.

The ezdxf backend computes exact LINE/CIRCLE/ARC/LWPOLYLINE core metrics and uses `ezdxf.bbox` for
extents. LWPOLYLINE bulges are included in length/area calculations. Generic intersections are
intentionally refused on ezdxf because a complete exact solver is not implemented.

The COM backend reads typed ActiveX properties (`Length`, `Radius`, `Circumference`, `Area`,
`ArcLength`, `Volume`, `Centroid` where applicable), uses `GetBoundingBox`, and maps exact
`IntersectWith` output into normalized WCS XYZ triples. Invalid extension modes, duplicate handles
and malformed non-XYZ COM payloads fail before or at the analysis boundary rather than being guessed.

`autocad.analysis.measurement` and `autocad.analysis.intersections` remain capability-false and no
new MCP tools are published despite the AutoCAD 2027 native PASS. Live evidence is retained in
`tests/test_measurement_analysis.py` JUnit runs.

### Verification evidence

Current verification on the final hardening tree:

- AutoCAD 2027 hidden native A2: `30 passed, 1 skipped`;
- AutoCAD 2027 hidden native A3.1: `11 passed`;
- AutoCAD 2027 hidden native A3.2: `6 passed`;
- AutoCAD 2027 hidden native A3.3: `8 passed`;
- Linux full regression: `83 passed, 4 skipped`;
- Windows `.171` full Python regression after N1/N2 documentation closure: `113 passed, 5 skipped`; native N2 P0–P10 is a separate real-AutoCAD acceptance lane;
- `python -m compileall src tests` and `git diff --check` pass;
- application version parsing identifies AutoCAD 2027 as COM `26.0`; usable live ProgID is `AutoCAD.Application.26`;
- native PDF plotting retries bounded busy COM boundaries while restoring `BACKGROUNDPLOT` and prior layout state;
- `SaveAs` remains single-shot mutation; only post-save `Name`/`FullName` reads receive bounded busy retry;
- public MCP tool count remains exactly 50 and A3 capability promotion is still deferred.

### Next gates

1. Start `N3`: build a staged Managed .NET bridge skeleton with protocol/version identity and local typed IPC against the frozen N1 semantic contract.
2. Consume the N2 PID carrier/policy exactly: NOD/XRecord document-lineage PID, Extension-Dictionary/XRecord DBObject PID, explicit clone reconciliation, duplicate-PID fail-closed validation, and runtime-document + `expected_parent_fp` composite binding.
3. Prove local IPC user/session restriction, bounded request size/time, request correlation, serialized AutoCAD document-context dispatch and read-only health/document-identity response before adding mutation operations.
4. Resolve or explicitly document the current AutoCAD/.NET build-reference warning set before NB0 promotion.
5. Keep COM as live comparison/fallback until `NATIVE_BRIDGE_ACCEPTANCE.md` gates close.
6. Promote A3 or any new public semantic tools only through explicit contract/version decisions.

See `docs/LIVE_ACCEPTANCE.md` for the complete primary-certification runbook. Do not extract shared
runtime from this lane; reusable infrastructure still requires Rule-of-Two cross-provider evidence.
