# CDT-AutoCAD

> Provider `0.4.0rc1` · Contract `autocad-generic-v1-rc1` · 86 public MCP tools · AutoCAD 2027 primary certification lane

CDT-AutoCAD is a generic CAD execution engine built for reliable automation of real AutoCAD drawings.
It combines a FastMCP provider, a live Windows COM backend and an in-process C# Managed .NET bridge
for persistent identity, semantic fingerprints, bounded native transactions and verified recovery.

The product deliberately does **not** contain civil, mechanical, landscape or architectural business
rules. Domain Agents decide what to draw and what standards apply; CDT-AutoCAD executes generic CAD
actions and proves what happened.

## Production execution model

Production Domains should use **Feature-based Chunks Streaming**.

Instead of sending one enormous mutation or thousands of isolated LINE calls, submit one complete
logical feature at a time:

```text
Feature 01: centerline        -> commit
300 ms presentation pause
Feature 02: curb edge         -> commit
300 ms presentation pause
Feature 03: corner detail     -> fails -> rollback Feature 03 only
Feature 01 + Feature 02 remain intact
recompute Feature 03          -> retry
Feature 04: block group       -> continue
```

The public entry point is `feature_execute`. One feature may contain multiple generic action families
and up to 10,000 items. The native bridge keeps internal micro-chunks at 32 items and yields between
AutoCAD Idle ticks. A failed feature is restored to its own predecessor fingerprint; previously
accepted features are not rolled back.

Live AutoCAD 2027 acceptance proves exactly this behavior: Feature 1 committed, Feature 2 committed
its first native micro-chunk then failed in micro-chunk 2 and rolled back only itself, and Feature 3
continued successfully. The observed presentation pause was 300.295 ms for the configured 300 ms
default.

## What is included

- FastMCP over stdio or authenticated Streamable HTTP.
- `ezdxf` headless backend for DXF workflows.
- Windows COM backend for live AutoCAD/DWG workflows.
- C# Managed .NET bridge loaded inside `acad.exe`.
- Persistent document/entity PIDs stored in DWG metadata.
- Document fingerprint schema v3 with metadata participation.
- Parent-fingerprint drift protection before native mutation.
- R0 transaction abort, R1 compensation and R2 checkpoint restore.
- Feature-local logical atomicity across many yielded native chunks.
- Generic batch create, block insertion and entity transforms.
- Schema-agnostic bounded entity metadata.
- Units, layer state, XREF lifecycle and dependency inspection.
- Advanced dimensions and measurement/analysis tools.
- Native ACIS 3D primitives, extrude/sweep/revolve/boolean/transform/inspect.
- SAT export with SHA-256 provenance.
- Content-addressed accepted-artifact sealing.
- Bounded view presets and visual styles for presentation workflows.

The public MCP surface contains 86 tools. Runtime `help` is generated from
[`docs/TOOL_GUIDE.md`](docs/TOOL_GUIDE.md), which is also the canonical contract guide hashed into
provider identity.

## Architecture

```text
Domain Agent / MCP client
        |
        | Feature plan + generic CAD ActionSpecs
        v
Python MCP Provider / Semantic Core
  - policy and orchestration
  - feature-local transaction scope
  - PID/fingerprint state chain
  - recovery journal
        |
        | same-user / same-session typed Named Pipe IPC
        v
C# AutoCAD Managed .NET Bridge
  - runs inside acad.exe
  - short native transactions
  - persistent PID + metadata storage
  - semantic extraction + verification
        |
        v
AutoCAD Document / Database / ACIS
```

Two invariants are non-negotiable:

1. **Data Integrity / Rollback** — a native mutation ends as `COMMITTED_VERIFIED` or
   `ROLLED_BACK_VERIFIED`; uncertain state blocks dependent work.
2. **Precise Identity / PID + Fingerprinting** — ObjectId/Handle are not treated as sufficient
   semantic identity; persistent PIDs and versioned fingerprints bind the state chain.

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[`docs/ADR-002-GENERIC-CAD-EXECUTION-ENGINE.md`](docs/ADR-002-GENERIC-CAD-EXECUTION-ENGINE.md).

## Install

Python 3.11+ is required.

```text
pip install -e .
```

For live Windows AutoCAD automation:

```text
pip install -e '.[com]'
```

For optional headless PDF rendering:

```text
pip install -e '.[render]'
```

## Run

### Headless DXF

```text
set CDT_AUTOCAD_BACKEND=ezdxf
cdt-autocad --transport stdio
```

### Live AutoCAD

Start AutoCAD first, then run the provider in the same Windows user/session:

```text
set CDT_AUTOCAD_BACKEND=com
set CDT_AUTOCAD_COM_PROGID=AutoCAD.Application.26
set CDT_AUTOCAD_COM_ATTACH_POLICY=attach_only
cdt-autocad --transport stdio
```

`attach_only` is the safe default: the provider refuses instead of silently launching another AutoCAD
process.

For HTTP transport, configure `CDT_AUTOCAD_AUTH_TOKEN`. Non-loopback binding additionally requires
`CDT_AUTOCAD_ALLOW_REMOTE_HTTP=true`.

## Recommended production workflow

1. `system_status`
2. `system_capabilities`
3. `native_integrity_status`
4. `document_open` or `document_new`
5. `document_configure_units`
6. set up layers/XREFs/blocks
7. execute one logical feature with `feature_execute`
8. inspect/query/measure the result
9. wait the recommended 300 ms before the next visual feature when presentation pacing is wanted
10. save and optionally `artifact_seal` the accepted drawing

For raw infrastructure/testing, `batch_create_entities`, `batch_insert_blocks` and
`batch_transform_entities` expose the underlying G3 logical-batch primitive directly.

## Showcase defaults

The bounded command-preset registry is in `config/autocad_command_presets.json`.

Current presentation defaults:

```text
feature pacing      300 ms between completed features
3D view             SE Isometric
3D visual style     Shades of Gray
```

The delay belongs between logical features, not between every native micro-chunk. This keeps drawing
formation visible without sacrificing throughput.

## Runtime configuration

```text
CDT_AUTOCAD_ALLOWED_PATHS       allowed CAD/PDF/SAT/XREF roots
CDT_AUTOCAD_MAX_DXF_BYTES       maximum input CAD size; default 50 MiB
CDT_AUTOCAD_CALL_TIMEOUT        headless call deadline; default 120 s
CDT_AUTOCAD_RENDER_TIMEOUT      render/PDF deadline; default 300 s
CDT_AUTOCAD_UNDO_DEPTH          headless snapshot undo depth; default 10
CDT_AUTOCAD_TRANSACTION_DEPTH   tracked transaction depth; default 8
CDT_AUTOCAD_BACKEND             ezdxf | com
CDT_AUTOCAD_COM_PROGID          default AutoCAD.Application
CDT_AUTOCAD_COM_ATTACH_POLICY   attach_only | attach_or_start
CDT_AUTOCAD_COM_TIMEOUT         live COM deadline; default 60 s
CDT_AUTOCAD_AUTH_TOKEN          required for HTTP transport
CDT_AUTOCAD_ALLOW_REMOTE_HTTP   explicit opt-in for non-loopback HTTP
```

## Verified native scale

The current G3 lane has live AutoCAD 2027 graduation evidence at:

| Tier | Result | Failure injection | Pending recovery |
| ---: | --- | --- | ---: |
| 100 | PASS | beginning / middle / end | 0 |
| 1,000 | PASS | beginning / middle / end | 0 |
| 5,000 | PASS | beginning / middle / end | 0 |
| 10,000 | PASS | beginning / middle / end | 0 |

At 10,000 entities the accepted run used 313 native chunks, preserved the same AutoCAD/bridge process,
restored the exact predecessor at beginning/middle/end injected failures and finished with 10,000
unique persistent entity PIDs. The bridge remains bounded at 32 entities per native micro-chunk.

Canonical evidence is under [`docs/evidence/`](docs/evidence/), including
`g3-scale-10000-2026-09-11.json` and `feature-stream-production-2026-09-11.json`.

## 2D and 3D workflow notes

For 2D/3D drawing production, build the drawing by meaningful layers/features instead of creating the
whole model in one burst. This improves reviewability, recovery scope and presentation quality.

For 3D showcase work use `view_set_preset("se_isometric")` and
`view_set_visual_style("shades_of_gray")`. Screenshots are optional visual evidence; semantic state,
PID/fingerprint readback and deterministic geometry/measurement checks remain the geometry oracle.

## Security and reliability

- no arbitrary shell, AutoLISP, macro or caller-supplied AutoCAD command surface;
- file/XREF paths are contained to configured roots;
- native requests, metadata and semantic extraction are bounded;
- native mutation is fenced by runtime document ID, document PID and expected parent fingerprint;
- timeouts with unknown completion are not blindly retried;
- credentials are never returned by tools;
- unsupported STEP/STL, ACIS edge fillet/chamfer/shell and other unproven capabilities fail explicitly.

## Development verification

Linux/headless regression:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.deps:src python3 -m pytest -q -p no:cacheprovider
```

The primary native certification lane is Windows x64 with full AutoCAD 2027. C# bridge changes must
also build Release/x64 with zero errors before live acceptance.

Current implementation status and release evidence are tracked in
[`docs/CURRENT_CHECKPOINT.md`](docs/CURRENT_CHECKPOINT.md).
