# Architecture Upgrade Plan — Native Bridge + Semantic State Loop

> Updated: 2026-09-10
> Status: IMPLEMENTATION IN PROGRESS · N0–N3 CLOSED · N4 NEXT
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

Implemented under `src/cdt_autocad/semantic/` with focused Linux tests and full Windows project regression. Evidence at this checkpoint: `31 passed` focused semantic tests; Windows `.171` full suite `113 passed, 5 skipped`; Python compile pass; `git diff --check` pass. The server-side global Python environment lacks the repo's optional/runtime dependencies for a full Linux suite, so the existing Windows project venv remains the complete regression lane for this checkpoint.

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

Implement read-only extraction first.

Baseline semantic families:

- document/units/current space/extents;
- layers/linetypes/styles;
- LINE/CIRCLE/ARC/POLYLINE/TEXT;
- blocks/references;
- dimensions/hatches;
- geometry metrics/bounding boxes;
- relations/intersections where deterministic;
- document/entity PIDs;
- canonical snapshot export.

Gate:

- snapshot stability across repeated reads;
- save/reopen semantic fingerprint stability where semantics are unchanged;
- COM vs .NET measurement parity on current supported entities;
- read-only path cannot mutate drawing.

### N5 — Native transactional executor + rollback

Implement typed mutations behind native transactions.

Start with:

- create basic entities;
- property edits;
- move/copy/rotate/scale;
- layer operations;
- delete;
- document-safe transaction scope.

Required rollback behavior:

- R0 transaction abort before commit;
- R1 verified compensation only where deterministic;
- R2 immutable-checkpoint restore for uncertain/non-invertible committed state;
- rollback read-back must exactly restore expected predecessor fingerprint before workflow resumes.

Gate:

- intentionally injected validation failures leave predecessor semantic fingerprint unchanged;
- timeout/fault injection never permits next step from unknown state;
- rollback failure is explicit and blocks execution.

### N6 — Deterministic validator + state-chain engine

Implement:

- expected-parent fingerprint guard;
- semantic pre/post diff;
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

**N0, N1, N2 and N3 are complete. Next: N4 — native semantic extractor.**

N3 consumes the frozen N1 semantic contract and live-verified N2 document-lineage PID policy without changing clone semantics. The bridge remains staged/internal even after NB0 PASS; current COM/ezdxf remains the supported public migration baseline. N4 must stay read-only and add authoritative native SemanticSnapshot families before N5 may introduce typed mutation transactions. `expected_parent_fp` must not be claimed as enforced until the required native snapshot/fingerprint integration exists.
