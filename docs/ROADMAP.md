# Roadmap — CDT-AutoCAD

> Updated: 2026-09-12 08:19 +07:00
> Current authority: `docs/CURRENT_CHECKPOINT.md`
> Architecture decisions: `docs/ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md`, `docs/ADR-002-GENERIC-CAD-EXECUTION-ENGINE.md`

## 1. Product direction

CDT-AutoCAD is the AutoCAD execution provider in the CDT toolchain. Its long-term role is not to become a civil/mechanical/architecture application. It is a reusable **Generic CAD Execution Engine** that gives Domain Agents a safe, typed and recoverable way to operate AutoCAD.

The product boundary is:

```text
Domain Agent
  engineering rules / standards / calculations / design intent
        ↓
CDT-AutoCAD
  typed CAD execution / PID / fingerprint / validation / recovery
        ↓
Managed .NET Bridge
        ↓
AutoCAD Database / DWG
```

No TCVN/ISO/ASME/customer business rule belongs in the provider. No arbitrary C#, AutoLISP, shell, macro or free-text command execution surface is introduced.

## 2. Runtime architecture

The product keeps two useful execution lanes:

```text
Python MCP Provider
   ├── ezdxf — cross-platform/headless DXF
   └── Windows live AutoCAD
         ├── COM/ActiveX — broad existing product surface
         └── Managed .NET Bridge — strong-integrity native lane
```

The native lane is bound by persistent document/entity PIDs, expected-parent fingerprints, short native transactions, independent semantic read-back and exact recovery. COM remains important for broad AutoCAD coverage, including functions not yet represented by the native typed bridge.

## 3. Completed foundation

### A0/A1 — Headless provider

**CLOSED.** Document lifecycle, entity creation/editing, layers, blocks, dimensions, layouts, hatch, audit/purge, export, queries, snapshots and security controls are established on the headless lane.

### A2/A3 — Live AutoCAD product capabilities

The COM lane is live-verified on full AutoCAD 2027 / Windows x64. Advanced dimensions, measurement/analysis, native ACIS 3D, view control, XREF/dependency handling, artifact sealing and related product capabilities are part of the published `0.4.0rc1` public contract.

Primary live target:

```text
AutoCAD 2027 full
COM 26.0 / AutoCAD.Application.26
Windows x64
```

### MP0 + A-01/A-02/A-03/A-06

**CLOSED.** Reproducible baseline, COM timeout/unknown-completion fencing, document binding, tool metadata/status truthfulness and early production-hardening findings have accepted evidence.

### N0–N7 + O1

**CLOSED / LIVE PASS.** The native architecture now has:

- persistent document/entity PID carriers;
- typed same-session local Named Pipe IPC;
- semantic extraction and deterministic fingerprinting;
- typed LINE/CIRCLE/ARC/simple-LWPOLYLINE mutation;
- parent-state drift guard;
- deterministic semantic validation/state-chain handling;
- immutable checkpoints;
- R0 abort, R1 compensation and activation-safe R2 checkpoint restore;
- exact predecessor proof after real AutoCAD restart.

### MP-2 — Hot reload runtime

**CLOSED / LIVE PASS.** Stable authenticated supervisor, replaceable stateless FastMCP workers, generation fence/drain/fallback, protocol/contract identity and bounded native-bridge readiness are accepted.

## 4. Generic CAD Execution Engine program

### G1 — Generic Batch Geometry

**CLOSED / LIVE PASS.**

- generic batch create for LINE/CIRCLE/ARC/simple LWPOLYLINE;
- persistent-PID transform: translate, rotate-Z, uniform scale;
- persistent block-definition-PID insertion;
- native micro-chunk limit: 32 entities;
- one batch mutation per AutoCAD Idle tick;
- exact R0/R1/R2 recovery.

### G2 — Schema-Agnostic Metadata

**CLOSED / LIVE PASS.**

- `metadata.get`, `metadata.set`, `metadata.query`;
- ExtensionDictionary/XRecord storage;
- namespaced arbitrary JSON from the provider's point of view;
- provider-owned PID/recovery namespaces reserved;
- bounded encoded size, nesting, key/string counts, native decimal range and query scan work;
- metadata participates in document fingerprint schema v3;
- committed metadata is independently read back before checkpoint finalization.

### G3 — Chunked Logical Atomicity

**CLOSED / LIVE PASS.**

G3 does not keep one AutoCAD transaction open across thousands of entities. It owns one immutable predecessor checkpoint, executes short native chunks, yields through AutoCAD Idle, verifies the final semantic state and either finalizes the accepted state or restores the exact predecessor.

The bridge currently uses:

```text
native chunk max       32
semantic capacity      12,288
logical item max       10,000
document FP schema     v3
bridge candidate       0.8.1-g3
```

### Scale graduation

**CLOSED / LIVE PASS through 10,000 entities.**

| Tier | Live result | Fault injection |
| ---: | --- | --- |
| 100 | PASS | beginning / middle / end exact restore |
| 1,000 | PASS | beginning / middle / end exact restore |
| 5,000 | PASS | beginning / middle / end exact restore |
| 10,000 | PASS | beginning / middle / end exact restore |

The 10,000-entity accepted run used 313 bounded native chunks, produced 10,000 unique persistent PIDs, left zero pending recoveries and preserved the same AutoCAD/bridge process identity.

## 5. Production Domains — Feature-based Chunks Streaming

**APPROVED / IMPLEMENTED / LIVE PASS.**

This is the preferred production orchestration model.

A Domain Agent sends a complete logical feature, not the entire drawing and not thousands of isolated primitive calls. Each feature owns one predecessor checkpoint and can contain many native micro-chunks.

```text
Feature 1 -> commit
300 ms presentation pause
Feature 2 -> commit
300 ms presentation pause
Feature 3 -> fail -> rollback Feature 3 only
Feature 1 + Feature 2 remain committed
recompute Feature 3 -> retry
Feature 4 -> continue
```

Public entry point:

```text
feature_execute(feature_id, feature_sequence, correlation_id, actions)
```

`feature_id` is correlation metadata only. The provider never interprets domain concepts such as centerline, curb, manhole, kiosk, beam or pipe.

Current generic feature actions:

- create typed entities;
- insert persistent-PID-bound block references;
- transform persistent entity PIDs.

Presentation pacing defaults to **300 ms between completed features**. Native micro-chunks are not artificially delayed; they retain Idle-yield responsiveness.

Live evidence proves Feature 1 commit → Feature 2 failure at its second native chunk → exact Feature-2-only restore → Feature 3 successful continuation. The observed 300 ms pause was 300.295 ms.

## 6. Public promotion — CLOSED

The reviewed promotion published this public identity at commit `0516fe3`:

```text
provider_version     0.4.0rc1
contract_version     autocad-generic-v1-rc1
public MCP tools     86
execution_model      feature-based-chunks-streaming-v1
```

The promotion is **closed and pushed to `main`**. It combines the broader product surface with the strong-integrity native tools and `feature_execute`.

The reviewed tree passed:

1. FastMCP catalog identity/tool-count check;
2. full Linux regression;
3. full Windows `.171` regression;
4. C# Release/x64 build with 0 errors;
5. compile/hygiene and `git diff --check`;
6. code/security review;
7. docs/help/contract identity agreement;
8. surgical stage/commit/push.

Final promotion results: Linux **316 passed / 5 skipped**; Windows `.171` **315 passed / 6 skipped**; C# Release/x64 **0 errors / 3 known MSB3277 warning families**; commit `0516fe3` pushed to `origin/main`. Historical pre-close regression numbers remain in earlier evidence/handoffs only.

## 7. Next development after promotion

With `0.4.0rc1` green and published, the next product work should be driven by real Production Domain use rather than speculative surface growth.

Priorities:

1. use `feature_execute` in real Domain Agent workflows and collect feature-size/latency/failure telemetry;
2. expand native typed entity/action families only where production features require them;
3. preserve the provider/domain boundary while adding generic primitives;
4. build parity/migration evidence before moving any remaining COM operation into the native lane;
5. keep drawing-quality acceptance separate from execution-integrity acceptance;
6. derive reusable CDT provider infrastructure only after Rule-of-Two evidence across another provider.

## 8. Evidence

Current production evidence:

- `docs/evidence/g23-live-2026-09-11.json`;
- `docs/evidence/g3-scale-100-2026-09-11.json`;
- `docs/evidence/g3-scale-1000-2026-09-11.json`;
- `docs/evidence/g3-scale-5000-2026-09-11.json`;
- `docs/evidence/g3-scale-10000-2026-09-11.json`;
- `docs/evidence/feature-stream-production-2026-09-11.json`.

Earlier N-series, MP-2 and G1 evidence remains under `docs/evidence/` and is historical proof for those checkpoints.

## 9. Session continuity

Current status authority is `docs/CURRENT_CHECKPOINT.md`. `docs/SESSION_HANDOFF_2026-09-11_FEATURE_STREAMING_PRODUCTION.md` is retained as a historical pre-close handoff.

Operational use begins 2026-09-12 under the existing RC contract. Use `docs/OPERATIONS_RUNBOOK.md` for live operation and `docs/RELEASE_CHECKLIST.md` for future publication gates. `_private/`, `_test_workspace/`, local artifacts/runtime output and `specs/**` remain outside unrelated product commits.
