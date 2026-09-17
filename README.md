# CDT-AutoCAD

> Provider `0.4.0rc3` · Contract `autocad-generic-v1-rc3` · 87 public MCP tools · AutoCAD 2027 primary certification lane · **Launch-ready / Operational RC**

CDT-AutoCAD is a **Generic CAD Execution Engine** for reliable automation of real AutoCAD drawings.

It combines a FastMCP provider, a Windows COM/ActiveX compatibility lane, an in-process C# Managed .NET bridge for strong-integrity operations, and an ezdxf headless lane. The provider executes generic CAD actions and returns structured evidence about resulting state.

It deliberately does **not** own engineering-domain rules. Civil, structural, mechanical, architectural and other domain systems decide what should be designed, calculated or checked; CDT-AutoCAD executes the required CAD operations.

## Current position

As of 2026-09-17, CDT-AutoCAD is **launch-ready for its intended execution-engine mission** with U1 core hardening live-accepted on AutoCAD 2027. New empty drawings now have an explicit verified production path to provider document lineage before native strong-integrity execution, and bounded native create batches cover LINE/CIRCLE/ARC/simple-LWPOLYLINE plus TEXT/MTEXT/aligned/linear dimensions with optional layer/color assignment.

Native strong-integrity writes require the caller's planned `document_pid` + predecessor fingerprint and refuse wrong-document/stale-state requests before mutation. `document_save` now requires an immediate persisted-clean postcondition, and cached live COM reuse repairs connection readiness metadata after successful probing. B1 filesystem containment remains closed with a split guarantee: provider-owned file I/O uses descriptor/handle-bound actual-I/O primitives against concurrent descendant namespace mutation, while AutoCAD APIs that accept pathname strings only remain explicitly bounded by pre/post verification rather than advertised as race-free. Future CAD capability is added only from a concrete blocked workflow plus its postcondition and verification invariant.

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
provider_version: 0.4.0rc3
contract_version: autocad-generic-v1-rc3
public MCP tools: 87
execution_model: feature-based-chunks-streaming-v1
native bridge candidate: 0.8.3-u1
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

The 87-tool catalog is intentionally hybrid. Tool presence does not imply every route has native strong-integrity guarantees; unsupported or unverifiable behavior must refuse explicitly.

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

At current release-provenance checkpoint `bcb5c66`:

- canonical exact-lock Linux reconstruction A/B: **405 passed / 11 skipped + Ruff PASS** in both fresh environments;
- canonical exact-lock Windows `.171` reconstruction A/B: **404 passed / 12 skipped + Ruff PASS** in both fresh environments;
- both reconstruction pairs bind the same clean Git HEAD and source-tree SHA-256 `3f4b45b13aa8ab4a60f92c02b99c97eff1aca8baf8a9ac84246b41e5d36db3d8`;
- Linux lock SHA-256 `e7fe668b159c56233be4d008600fe80dd0778689a58b48ab55cecc670c45bf06` with 88 locked packages; Windows lock SHA-256 `acbbc28bc07d26c2e07c76ab5d24f2e474945ba10cc14a0c9e94cca29867869e` with 89 locked packages;
- Windows provenance binds bridge DLL SHA-256 `18740fc6cc35a4e9117efed29fc98bd9c2d4836b77140d751e8d1628abd75a43` and observed AutoCAD PID 7888 / Session 1;
- `uv.lock` is explicitly non-canonical local resolver residue and is ignored; `pylock.linux.toml` + `pylock.windows.toml` remain the exact dependency authorities;
- B0/B1/B4 assurance evidence remains valid within its documented scope; `git diff --check` and clean release-tree verification pass.

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
