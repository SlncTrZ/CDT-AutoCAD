# CDT-AutoCAD

> Provider `0.4.0rc1` · Contract `autocad-generic-v1-rc1` · 86 public MCP tools · AutoCAD 2027 primary certification lane · **Launch-ready / Operational RC**

CDT-AutoCAD is a **Generic CAD Execution Engine** for reliable automation of real AutoCAD drawings.

It combines a FastMCP provider, a Windows COM/ActiveX compatibility lane, an in-process C# Managed .NET bridge for strong-integrity operations, and an ezdxf headless lane. The provider executes generic CAD actions and returns structured evidence about resulting state.

It deliberately does **not** own engineering-domain rules. Civil, structural, mechanical, architectural and other domain systems decide what should be designed, calculated or checked; CDT-AutoCAD executes the required CAD operations.

## Current position

As of 2026-09-12, CDT-AutoCAD is **launch-ready for its intended execution-engine mission**.

There is no known top-level architecture or integrity blocker that must be closed before CDT-Engineer work begins. Future AutoCAD capability is added only when CDT-Engineer or another production domain identifies a concrete blocked workflow plus the postcondition and verification invariant required to prove success.

This is not a claim of full AutoCAD API parity, and the project is not pursuing parity as an independent roadmap.

Canonical documentation:

- architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- current public status: [`docs/CURRENT_CHECKPOINT.md`](docs/CURRENT_CHECKPOINT.md)
- documentation map/governance: [`docs/README.md`](docs/README.md)
- semantic integrity protocol: [`docs/SEMANTIC_STATE_PROTOCOL.md`](docs/SEMANTIC_STATE_PROTOCOL.md)
- live acceptance: [`docs/LIVE_ACCEPTANCE.md`](docs/LIVE_ACCEPTANCE.md)
- operations: [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md)
- public tool contract: [`docs/TOOL_GUIDE.md`](docs/TOOL_GUIDE.md)

This README is a product landing page and summary, not a competing architecture or current-state authority.

## Public identity

```text
provider_version: 0.4.0rc1
contract_version: autocad-generic-v1-rc1
public MCP tools: 86
execution_model: feature-based-chunks-streaming-v1
native bridge candidate: 0.8.2-mp7
```

## Execution model

Production domains should use **Feature-based Chunks Streaming**.

A higher-level system submits one meaningful feature at a time. CDT-AutoCAD may execute the feature through bounded native micro-chunks while preserving feature-level predecessor state and recovery.

```text
Feature 01 -> execute -> verify -> commit
Feature 02 -> execute -> verify -> commit
Feature 03 -> partial failure -> restore Feature 03 predecessor only
Feature 01 + Feature 02 remain committed
Feature 03 -> recompute/retry
Feature 04 -> continue
```

The public orchestration entry point is `feature_execute`. The current logical feature/batch cap is 10,000 items; the native bridge keeps internal micro-chunks bounded to 32 items and yields through AutoCAD Idle processing rather than holding one giant long-lived transaction.

## What is included

- FastMCP over stdio or authenticated Streamable HTTP.
- `ezdxf` headless backend for DXF workflows.
- Windows COM/ActiveX backend for broad live AutoCAD/DWG compatibility.
- C# Managed .NET bridge loaded inside `acad.exe` for strong-integrity scopes.
- Persistent provider-owned document/entity PIDs for supported native scopes.
- Versioned semantic/document fingerprints and expected-parent drift protection.
- Bounded native transactions and feature-local logical recovery.
- Generic batch create, block insertion and transforms.
- Schema-agnostic bounded entity metadata.
- Units, layers, XREF lifecycle/dependency inspection and document operations.
- Dimensions, measurements, analysis and broad drafting utilities.
- ACIS/3D compatibility operations on the live AutoCAD lane.
- Bounded native `3DSOLID` semantic integrity for **planar translate only** through `solid-semantic-v2`, persisted read-back, stale-parent refusal, R0 abort and exact R2 predecessor restore.
- SAT export with provenance.
- Content-addressed accepted-artifact sealing.
- Bounded view presets and visual styles for presentation workflows.

The 86-tool catalog is intentionally hybrid. Tool presence does not imply every route has native strong-integrity guarantees; unsupported or unverifiable behavior must refuse explicitly.

## Integrity model

Two principles are launch-critical:

1. **Data Integrity / Rollback** — strong-integrity mutation paths prove `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`; uncertain state blocks dependent work.
2. **Precise Identity / PID + Fingerprinting** — strong-integrity state uses provider-owned persistent identity and versioned fingerprints instead of relying only on AutoCAD ObjectId/Handle.

The canonical architecture is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); normative state/recovery behavior is in [`docs/SEMANTIC_STATE_PROTOCOL.md`](docs/SEMANTIC_STATE_PROTOCOL.md).

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

`attach_only` is the safe default: the provider refuses instead of silently launching another AutoCAD process.

For HTTP transport, configure `CDT_AUTOCAD_AUTH_TOKEN`. Non-loopback binding additionally requires `CDT_AUTOCAD_ALLOW_REMOTE_HTTP=true`.

## Recommended production workflow

1. `system_status`
2. `system_capabilities`
3. `native_integrity_status` when strong-integrity execution is required
4. `document_open` or `document_new`
5. configure units/layers/references
6. execute one logical feature with `feature_execute`
7. inspect/query/measure resulting state
8. continue only from verified state
9. save and optionally `artifact_seal` the accepted drawing

For lower-level infrastructure/testing, `batch_create_entities`, `batch_insert_blocks` and `batch_transform_entities` expose the underlying logical-batch primitives directly.

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

## Verified scale and current gates

Current accepted scale tiers on real AutoCAD 2027:

| Tier | Result | Failure injection | Pending recovery |
| ---: | --- | --- | ---: |
| 100 | PASS | beginning / middle / end | 0 |
| 1,000 | PASS | beginning / middle / end | 0 |
| 5,000 | PASS | beginning / middle / end | 0 |
| 10,000 | PASS | beginning / middle / end | 0 |

At current runtime/code checkpoint `911ba09`:

- Linux full regression: **386 passed / 6 skipped**;
- Windows `.171` full regression: **385 passed / 7 skipped**;
- GitHub Headless CI exact-head: **4/4 matrix jobs PASS**;
- Ruff and Python compile: **PASS**;
- C# Release/x64: **0 errors / 3 inherited warning families**;
- MP-G05 AutoCAD 2027 live commit/drift/R0/post-commit-fault/R2 gate: **PASS**.

See [`docs/CURRENT_CHECKPOINT.md`](docs/CURRENT_CHECKPOINT.md) and [`docs/LIVE_ACCEPTANCE.md`](docs/LIVE_ACCEPTANCE.md) for authoritative status/evidence scope.

## Security and reliability

- no arbitrary shell, AutoLISP, macro, caller-supplied C# or free-text AutoCAD command surface;
- file/XREF paths are bounded to configured roots/policy;
- native requests, metadata and semantic extraction are bounded;
- strong-integrity mutation is fenced by runtime/document identity and predecessor state;
- unknown completion is not blindly retried;
- credentials are never returned by tools;
- unsupported/unproven capability fails explicitly rather than widening claims.

Security reports and incident handling are defined in [`SECURITY.md`](SECURITY.md), [`SUPPORT.md`](SUPPORT.md) and [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md).

## Development direction

There is no standing “finish the rest of AutoCAD” phase.

Future work starts from a production need:

```text
engineering workflow
-> blocked CAD step
-> required capability
-> expected postcondition
-> verification invariant
-> smallest safe implementation
-> live acceptance when required
```

This keeps CDT-AutoCAD focused as infrastructure and prevents speculative over-engineering.

## License and ownership

Copyright (c) 2026 **Trương Công Định (SlncTrZ)**. CDT-AutoCAD is proprietary source-available software under [`LICENSE`](LICENSE); public repository visibility does not grant an open-source license. Third-party dependency/API boundaries are documented in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and source provenance in [`SOURCE_PROVENANCE.md`](SOURCE_PROVENANCE.md).
