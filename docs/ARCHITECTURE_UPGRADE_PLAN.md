# Architecture Upgrade Plan — Native Bridge + Semantic State Loop

> Updated: 2026-09-10 13:25 +07:00
> Status: IMPLEMENTATION IN PROGRESS · N0–N4 CLOSED · N5 NEXT
> Current runtime remains `ezdxf + COM` until migration gates close.
> Acceptance companion: `docs/NATIVE_BRIDGE_ACCEPTANCE.md`

## 1. Objective

Upgrade CDT-AutoCAD from reliable remote CAD automation into a state-verified engineering automation system.

The target system is built around two mandatory pillars:

1. **Data Integrity / Rollback** — no step may leave the workflow in an unknown or partially trusted state.
2. **Precise Identity / PID + Fingerprinting** — every managed object and every verified state must be deterministically identifiable across steps and, where designed, across save/reopen.

These pillars are implemented through a mandatory **Semantic State Loop** and a new **AutoCAD Managed .NET native bridge**.

## 2. Current vs target architecture

### Current baseline

```text
MCP
 -> Python provider
 -> backend abstraction
    -> ezdxf headless
    -> ActiveX/COM live AutoCAD
```

This baseline remains supported and live-verified. It is the migration comparison lane.

### Target architecture

```text
MCP / SlncTrZ Gateway
        |
        v
Python Provider / Semantic Core
  - policy/auth
  - ActionSpec
  - SourceSemanticModel
  - canonicalization
  - fingerprint/diff
  - deterministic validator
  - state-chain/audit
        |
        | local typed IPC
        v
AutoCAD Native Bridge (C# / Managed .NET)
  - in acad.exe
  - execution dispatcher
  - document locking
  - native transactions
  - PID metadata
  - semantic extraction
  - event-assisted delta capture
  - post-commit read-back
        |
        v
AutoCAD Database / DWG
```

COM becomes migration/bootstrap/compatibility/fallback rather than the long-term authoritative mutation/extraction path.

## 3. Upgrade principles

- No big-bang rewrite.
- Preserve current 50-tool public MCP contract unless a separate promotion changes it.
- Introduce the semantic core internally before changing public surface.
- New .NET implementation must run side-by-side with COM on disposable drawings for parity tests.
- No capability promotion merely because code exists.
- Native verification on AutoCAD 2027 remains mandatory.
- Pinned `specs/` remain unchanged until `CDT_Engineer` explicitly accepts a common-contract update.
- Reusable common semantic abstractions are proposed upstream only after Rule-of-Two evidence from another provider.

## 4. Upgrade phases

### N0 — Documentation / architecture freeze

Deliverables:

- ADR for .NET native bridge + Semantic State Loop;
- Semantic State Protocol;
- updated execution QA workflow;
- updated acceptance, roadmap, handoff, README and live-acceptance docs;
- explicit two-pillar invariants in `AGENTS.md`.

Gate:

- docs contain no ambiguity that screenshot/command success can close a semantic step;
- current vs target architecture clearly distinguished;
- no implementation state falsely claimed.

### N1 — Semantic contract models in Python

**Status: IMPLEMENTED / GATE PASS — 2026-09-10.**

Implemented under `src/cdt_autocad/semantic/`. Historical N1 closure evidence was `31 passed` focused semantic tests and Windows `.171` full suite `113 passed, 5 skipped`. Those numbers belong to the N1 checkpoint; the current N4-closure regression is Linux `145 passed, 4 skipped`, Windows `.171` `144 passed, 5 skipped`, and focused N4/N1 semantic regression `58 passed`. See `docs/CURRENT_CHECKPOINT.md`.

Implement internal typed models for:

- `SourceSemanticModel`;
- `ActionSpec`;
- `ValidationRuleSet`;
- `SemanticSnapshot`;
- `SemanticDelta`;
- `FingerprintSet`;
- `ValidationResult`;
- `RollbackReceipt`;
- `StateChainEntry`.

Implement versioned canonical JSON serialization and tolerance profiles.

Gate — **PASS**:

- deterministic serialization;
- stable golden geometry/action fingerprints;
- integer/float normalization and tolerance quantization tests;
- domain-separated fingerprints and tolerance-profile identity;
- duplicate PID and duplicate-geometry detection tests;
- deep immutable action payloads and arbitrary-execution-field guard;
- entity-enumeration-independent document fingerprints;
- semantic-delta set-order independence;
- tamper-evident state-chain verification and parent drift guard;
- rollback receipt invariant tests;
- no AutoCAD dependency required for the semantic core itself.

### N2 — PID design prototype

**Status: CLOSED / LIVE PASS — 2026-09-10.**

Native P0–P10 probes executed inside real AutoCAD 2027 (`26.0s`) on Windows `.171` and closed the persistent-identity storage prototype. Canonical evidence: `docs/evidence/n2-pid-native-2026-09-10.json`; detailed runbook/result: `docs/N2_PID_ACCEPTANCE.md`.

Selected carrier for N3+: document lineage PID in `SLNCTRZ_CDT/DOCUMENT_PID` XRecord under the Named Objects Dictionary; managed DBObject PID in `SLNCTRZ_CDT_PID` XRecord under the object's Extension Dictionary. This carrier is valid only together with clone reconciliation, duplicate-PID fail-closed policy, and composite runtime-document + parent-state binding because raw file copies preserve lineage PID.

Prototype persistent identity on real AutoCAD 2027.

Evaluate:

- document PID via Named Objects Dictionary/XRecord;
- entity PID via Extension Dictionary/XRecord and/or another native metadata carrier;
- save/reopen survival;
- COPY/Clone/DeepClone/WBLOCK/INSERT behavior;
- undo/redo interaction;
- erase/unerase behavior;
- block definition/reference behavior;
- cross-document clone remapping.

Gate — **PASS**:

- documented clone/PID policy;
- no duplicate PID ambiguity after reconciliation;
- native save/cold-reopen roundtrip proves document/entity persistence;
- ordinary edit, erase/unerase and real UNDO/REDO preserve conceptual identity;
- shallow clone receives fresh PID; deep clone, WblockClone, WBLOCK and INSERT are proven to require clone-result PID reconciliation in measured paths;
- BlockTableRecord, block-definition content and BlockReference can carry distinct persistent PIDs;
- native `Transaction.Abort()` restores an overwritten PID XRecord, geometry and entity count in the measured rollback probe;
- selected carrier has explicit AutoCAD 2027 compatibility evidence;
- P10 proves raw file copies preserve lineage PID, so N3 document targeting must be composite and fail closed on duplicate open lineage identity.

This N2 evidence does **not** close NB4 full Semantic State rollback/fingerprint integration; that remains an N5 gate.

### N3 — .NET bridge skeleton + IPC

**Status: CLOSED / LIVE PASS — 2026-09-10.** Canonical evidence: `docs/evidence/n3-native-bridge-readonly-2026-09-10.json`.

A version-pinned C# bridge project for AutoCAD 2027 Managed .NET is implemented and staged internally.

Responsibilities:

- load/unload health;
- bridge protocol version;
- request correlation ID;
- local IPC endpoint;
- ACL/user/session constraints;
- bounded request size/time;
- execution queue/dispatcher into valid AutoCAD document context;
- no arbitrary code/text-command endpoint.

Selected IPC: Windows Named Pipe with `CurrentUserOnly`, explicit local-computer validation and same-Windows-session validation. Background I/O hands bounded typed requests to an `Application.Idle` dispatcher; no background thread touches AutoCAD API.

Gate — **PASS**:

- bridge answers health/version/document identity from the interactive AutoCAD 2027 process;
- one-request-per-connection transport serializes AutoCAD dispatch and bounds queued work/time/frame size;
- malformed/oversized/unsupported requests fail closed and the bridge remains alive;
- same-user client in Windows Session 0 is rejected; Session 1 succeeds;
- duplicate open lineage PIDs are disambiguated by bridge-instance runtime document IDs, while PID-only targeting is rejected;
- tested read-only calls do not dirty drawings;
- no arbitrary C#/AutoLISP/shell/free-text AutoCAD command surface and no native mutation endpoint exists.

Known build debt: three Autodesk product-reference `MSB3277` warning families remain documented and unsuppressed. Build succeeds with 0 errors and the final binary passes live AutoCAD 2027 acceptance.

### N4 — Native semantic extractor

**Status: CLOSED / LIVE PASS — 2026-09-10.** Canonical evidence: `docs/evidence/n4-native-semantic-2026-09-10.json`.

Read-only `bridge.document.snapshot` is implemented and live-verified on full AutoCAD 2027 Session 1. The native bridge remains mutation-disabled. N4 is intentionally bounded to the active bound document's current space plus referenced block definitions/content, with a 32-object semantic cap; this is not yet a whole-DWG/all-layout extractor.

Baseline semantic families:

- document/units/current space/extents;
- layers/linetypes/styles;
- LINE/CIRCLE/ARC/LWPOLYLINE/TEXT/MTEXT;
- blocks/references, including referenced block definitions and PID-bearing definition content;
- dimensions/hatches;
- geometry metrics/bounding boxes;
- relations/intersections where deterministic;
- document/entity PIDs;
- canonical snapshot export.

Gate — **PASS**:

- snapshot stability across repeated reads;
- save/reopen semantic fingerprint stability where semantics are unchanged;
- COM vs .NET measurement parity on current supported entities;
- read-only path cannot mutate drawing;
- persistent entity PID is mandatory and duplicate/missing PID fails closed;
- native document fingerprint matches the N1 canonical Python fingerprint for the accepted fixture;
- a saved referenced-block-definition geometry mutation changes `document_fp` while both compared snapshots are clean (`DBMOD=0`), proving definition content participates in parent-state identity;
- oversized serialized snapshots fail as correlated `RESPONSE_TOO_LARGE` without dirtying the document or killing the bridge.

### N5 — Native transactional executor + rollback

**Status: NEXT / NOT STARTED.**

Implement typed mutations behind native transactions. N5 owns the first authoritative mutation-time `expected_parent_fp` guard: every request must bind runtime document + persistent document PID + expected parent document fingerprint and re-read N4 state before any write.

Start with:

- create basic entities;
- property edits;
- move/copy/rotate/scale;
- layer operations;
- delete;
- document-safe transaction scope.

Required precondition / rollback behavior:

- reject stale/mismatched `expected_parent_fp` before opening the write transaction;
- resolve entity targets by persistent semantic PID, not Handle/ObjectId alone;

- R0 transaction abort before commit;
- R1 verified compensation only where deterministic;
- R2 immutable-checkpoint restore for uncertain/non-invertible committed state;
- rollback read-back must exactly restore expected predecessor fingerprint before workflow resumes.

Gate:

- intentionally injected validation failures leave predecessor semantic fingerprint unchanged;
- timeout/fault injection never permits next step from unknown state;
- rollback failure is explicit and blocks execution.

### N6 — Deterministic validator + state-chain engine

**Status: NOT STARTED.**

Implement:

- semantic pre/post diff;
- state-chain parent continuity and manual-drift detection;
- allowed-effects enforcement;
- geometry/style/topology fingerprinting;
- PID uniqueness checks;
- duplicate geometry detection;
- deterministic rule engine;
- append-only state-chain log;
- state-drift detection after manual edits.

Gate:

- manual mutation between steps causes `STATE_DRIFT`;
- duplicated object is detected even when native handle differs;
- unexpected deletion/modification fails validation;
- exact failed step can be reconstructed from logs.

### N7 — Two-phase native commit integrity

**Status: NOT STARTED.**

Implement:

1. provisional extraction + deterministic validation inside transaction;
2. commit;
3. independent read transaction;
4. persisted-state fingerprint comparison.

Gate:

- post-commit mismatch becomes `COMMIT_INTEGRITY_FAIL`;
- automatic continuation is blocked;
- rollback/restore path returns to verified predecessor or explicitly stops at `ROLLBACK_FAILED`.

### N8 — Migrate current live operations

**Status: NOT STARTED.**

Move current COM-backed public functionality incrementally to the native bridge while keeping public MCP semantics stable.

Priority:

1. read/query/measurement;
2. basic 2D creation/edit;
3. layers/blocks/layouts;
4. dimensions/hatch;
5. save/export orchestration;
6. 3D solids/advanced operations.

Each migrated family needs:

- COM-vs-.NET parity tests;
- Semantic State Loop tests;
- rollback tests;
- PID/fingerprint tests;
- real AutoCAD acceptance.

### N9 — Drawing workflow migration

**Status: NOT STARTED.**

Reference-driven drawing automation moves from screenshot-heavy step runners to semantic steps:

```text
ActionSpec
 -> native execute
 -> semantic extract
 -> diff/fingerprint
 -> deterministic validation
 -> commit/restore
 -> next ActionSpec
```

Major gates compare SemanticSnapshot to SourceSemanticModel.

Vision remains only for raster source ingestion and final visual/human review.

Gate:

- rerun the house-plan stress test from a clean file;
- every step has a semantic state-chain entry;
- intentionally injected wrong geometry is stopped at the exact failing step;
- rollback returns to predecessor fingerprint;
- final drawing still satisfies `DRAWING_QUALITY_ACCEPTANCE.md`.

### N10 — Public contract/promotion decision

**Status: NOT STARTED.**

Only after native parity and semantic integrity are proven:

- decide whether current MCP tools transparently route to .NET bridge;
- decide whether semantic inspection/validation tools become public;
- version any public contract changes explicitly;
- propose cross-provider semantic contract to `CDT_Engineer` only with sufficient multi-provider evidence.

## 5. Non-functional requirements

### Integrity

- no unknown mutation state may advance;
- every committed step has post-commit semantic proof;
- rollback is itself verified by fingerprint;
- previous accepted checkpoint remains recoverable.

### Identity

- persistent document/object IDs are independent of transient `ObjectId`;
- content fingerprints are deterministic and versioned;
- clone/copy semantics cannot silently duplicate PID identity;
- state drift is detectable before every mutation.

### Security

- local bridge only by default;
- no arbitrary C#/LISP/command execution;
- typed operation allowlist;
- bounded IPC payloads and timeouts;
- filesystem operations remain policy-contained;
- bridge identity/session binding is explicit.

### Reliability

- no network/AI wait while native write transaction is open;
- request idempotency strategy for retryable read operations;
- mutations are non-blind-retry by default;
- recovery path is explicit after bridge/process failure.

### Observability

Each semantic step records:

- request/action ID;
- parent state fingerprint;
- PIDs involved;
- mutation receipt;
- semantic delta;
- validation result;
- rollback receipt when applicable;
- resulting state fingerprint;
- native artifact hash when applicable;
- timing/error metadata.

## 6. What remains intentionally unchanged during preparation

- current Python package/runtime;
- current public 50-tool MCP contract;
- current `ezdxf` default backend;
- current live COM backend and its native acceptance evidence;
- pinned `specs/` snapshots;
- A3 promotion status.

The architecture documentation may lead implementation; runtime claims must continue to describe what is actually shipped today.

## 7. Current implementation frontier

**N0, N1, N2, N3 and N4 are complete. Next: N5 — typed native transactional mutation + verified rollback.**

N4 now provides authoritative read-only parent SemanticSnapshot data and native↔N1 document fingerprint parity without changing the public backend. The bridge remains staged/internal and current COM/ezdxf remains the supported public migration baseline. N5 may introduce only bounded typed mutation operations, must enforce `expected_parent_fp` before native writes, and must prove transaction abort/rollback by independent N4 read-back. N6 starts only after those N5 gates live-pass.
