# Architecture Upgrade Plan — Native Bridge + Semantic State Loop

> Updated: 2026-09-11 21:50 +07:00
> Current status: **N0–N7/O1 CLOSED · MP-2 CLOSED · G1/G2/G3 CLOSED/LIVE PASS · 10k graduation PASS · Feature-based Chunks Streaming LIVE PASS · public promotion CLOSED/PUSHED at `0516fe3`**
> Current-state authority: `docs/CURRENT_CHECKPOINT.md`
> Acceptance companion: `docs/NATIVE_BRIDGE_ACCEPTANCE.md`

## 0. Current frontier

The architecture upgrade has passed the G-series stage that older sections of this plan described as future work. The accepted native candidate is `0.8.1-g3`, uses document fingerprint schema v3, keeps native mutation chunks bounded to 32 entities, supports a graduated semantic capacity of 12,288 entities, and has live acceptance through a 10,000-item logical workload.

G2 schema-agnostic metadata and G3 checkpoint-backed logical execution are implemented and live-verified. The approved Production Domain execution model is now **Feature-based Chunks Streaming**: one caller-defined logical feature at a time, one feature-local predecessor checkpoint, exact rollback of the current failed feature only, and optional 300 ms presentation pacing between completed features. Domain meaning remains outside the provider.

The public contract `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools` is **release-closed and pushed** at commit `0516fe3`. Final Linux/Windows regression, C# build, review, documentation agreement and selective publication gates passed on one reviewed tree. Statements later in this document about the historical 50-tool baseline, staged A3 surface, G2/G3 being future work or N8/N10 being unopened describe earlier checkpoints and are retained as implementation history; use `docs/CURRENT_CHECKPOINT.md` for current truth.

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

Implemented under `src/cdt_autocad/semantic/`. Historical N1 closure evidence was `31 passed` focused semantic tests and Windows `.171` full suite `113 passed, 5 skipped`. Those numbers belong to the N1 checkpoint; the current O1-closure regression is Linux `195 passed, 4 skipped`, Windows `.171` `194 passed, 5 skipped`, and focused O1/N5/N6 regression `76 passed`. See `docs/CURRENT_CHECKPOINT.md`.

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

**Status: CLOSED / LIVE PASS — 2026-09-10.** Canonical evidence: `docs/evidence/n5-native-mutation-2026-09-10.json`.

N5 intentionally opens only a narrow fixed-schema mutation surface:

- `entity.create.line`;
- `entity.update.line`;
- `entity.delete.line`;
- document-safe `DocumentLock` + native transaction scope.

Implemented precondition / rollback behavior:

- every mutation binds runtime document + persistent document PID + `expected_parent_fp`;
- stale/mismatched parent fingerprint is rejected as `STATE_DRIFT` before write;
- update/delete resolve by persistent semantic PID, never Handle/ObjectId as semantic target identity;
- create assigns a fresh persistent PID; update preserves PID/Handle;
- deterministic `after_apply_before_commit` fault injection proves R0 transaction abort and independent predecessor read-back;
- rollback advances only as `ROLLED_BACK_VERIFIED` when the actual restore fingerprint equals the expected predecessor fingerprint;
- document fingerprint schema v2 excludes volatile `saved/DBMOD` from semantic identity while retaining that lifecycle state in snapshots;
- create is preflight-rejected with `SNAPSHOT_CAPACITY_EXCEEDED` when it would exceed the bounded N4 semantic extractor capacity.

Gate — **PASS for the bounded LINE surface**:

- stale parent request mutates nothing;
- create/update R0 fault injection restores the exact semantic predecessor fingerprint;
- a subsequent mutation is allowed only after verified restoration;
- create/update native state matches COM measurements;
- PID survives update + save/reopen and runtime-document rebinding;
- delete removes the target PID-bearing entity;
- 32→33 capacity boundary refuses before mutation;
- no arbitrary command/eval/shell surface is introduced.

N5 does **not** claim N6 allowed-effects/state-chain enforcement or N7 R1/R2/post-commit recovery.

### N6 — Deterministic validator + state-chain engine

**Status: CLOSED / LIVE PASS — 2026-09-10.** Canonical evidence: `docs/evidence/n6-semantic-state-chain-2026-09-10.json`.

N6 is implemented in the Python semantic layer over the unchanged N5 bridge and remains bounded to the fixed-schema LINE surface. It provides:

- deterministic semantic pre/post PID delta while excluding transient native Handle/fingerprint-cache differences;
- explicit allowed-effects enforcement by entity type plus persistent PID target scope;
- independent create/update LINE geometry comparison against the `ActionSpec`;
- geometry-only update envelope protection for target type/layer/style/hierarchy and invariant document units/current space/style resources;
- detection of newly introduced exact duplicate geometry even when PID/Handle differ;
- exactly one tamper-evident state-chain entry per accepted `COMMITTED_VERIFIED` step;
- no chain advance for verified R0 rollback, validation failure, drift or uncertain completion;
- manual/external parent drift refusal before native dispatch;
- append-only per-run JSONL journal with `fsync` and explicit refusal to silently resume a non-empty journal;
- fail-closed `STATE_UNCERTAIN` latching after any unverified post-dispatch condition.

Gate — **PASS for the bounded N5 LINE surface**:

- live create/update/delete sequence produces exactly three verified chain entries;
- verified R0 rollback produces no chain entry;
- manual COM geometry mutation is detected as `STATE_DRIFT` before the next native mutation and the refused request leaks no change;
- allowed-effects failure after commit is recorded, produces no chain entry and blocks later mutation as `STATE_UNCERTAIN`;
- exact duplicate geometry with a fresh PID/Handle is detected and blocks chain advancement;
- malformed receipts, unknown transport completion, post-commit native errors, read-back failure, validator failure and state-chain failure all latch uncertainty in focused TDD;
- N6 does not change the N5 native bridge binary or operation allowlist.

N6 does **not** claim topology validation because current native relation extraction is empty, does not resume journals across a fresh process, and does not implement R1/R2 or N7 two-phase post-commit recovery.

### O1 — Internal basic-shape operation-family expansion

**Status: CLOSED / LIVE PASS — 2026-09-10.** Canonical evidence: `docs/evidence/o1-native-shape-mutation-2026-09-10.json`.

O1 is a separately authorized internal expansion over the closed N4–N6 foundation; it does not open N7 and does not mark the formal N8 public migration phase complete. The staged bridge version advances to `0.4.0-o1` and the exact typed mutation allowlist becomes 12 operations:

- LINE create/update/delete retained from N5;
- CIRCLE create/update/delete;
- ARC create/update/delete;
- simple LWPOLYLINE create/update/delete.

The new families preserve the existing invariants: runtime-document + lineage document PID + `expected_parent_fp` binding, persistent entity PID targeting, native `DocumentLock`/transaction boundaries, R0 fault-injection verification, independent semantic read-back, N6 requested-geometry/allowed-effects validation and one state-chain entry per accepted semantic step.

O1 LWPOLYLINE update is intentionally bounded to simple 2D zero-elevation +Z polylines with zero bulge/vertex widths and at most 128 finite XY vertices. Complex existing polylines are refused before write as `UNSUPPORTED_TARGET_GEOMETRY`. Live debugging proved AutoCAD throws `eDegenerateGeometry` if an update removes the final remaining vertex; the accepted implementation therefore rebuilds in place with `SetPointAt`, tail shrink/grow and final `Closed` application. Live acceptance proves 4→2 shrink and 2→5 grow with stable PID/Handle and COM parity.

Gate — **PASS**:

- exact 12-operation native allowlist and no arbitrary command/eval surface;
- stale parent fails before mutation;
- CIRCLE/ARC/LWPOLYLINE R0 rollback paths return verified predecessor fingerprints;
- create/update COM parity and PID/Handle stability;
- save/reopen preserves all new-family PID/geometry state;
- complex LWPOLYLINE update refuses without mutation;
- direct deletes remove each target PID;
- N6 create/update/delete for all three new families yields exactly nine verified chain entries;
- generalized 32→33 snapshot-capacity guard refuses a new CIRCLE before write;
- Linux `195 passed / 4 skipped`, Windows `.171` `194 passed / 5 skipped`, focused O1/N5/N6 `76 passed`;
- N7 is not used.

### N7 — Two-phase native commit integrity

**Status: CLOSED / LIVE PASS (2026-09-11).** Historical working handoff: `docs/N7_WORKING_CHECKPOINT_2026-09-10.md`; canonical closure evidence: `docs/evidence/n7-native-recovery-2026-09-11.json`.

Implemented on the current working tree:

1. provisional semantic extraction inside the same native write transaction;
2. deterministic native validation before commit;
3. commit;
4. independent persisted-state read-back and provisional-vs-persisted fingerprint comparison;
5. provider-owned recovery checkpoint + manifest + artifact SHA-256 binding;
6. typed recovery list/resolve/finalize protocol;
7. R0 transaction abort, deterministic R1 compensation and R2 checkpoint restore;
8. Python semantic executor recovery orchestration that attempts R1 then R2 and advances no state-chain entry for recovered failures;
9. corrupt recovery manifests fail closed and block later mutation.

Final verification is 40/40 focused recovery/semantic/bridge PASS, 227 PASS / 5 SKIP full Linux, 226 PASS / 6 SKIP full Windows, and a 0-error C# x64 Release build using .NET SDK 10.0.401. Real AutoCAD 2027 Session 1 passed R0, provisional-abort, post-commit mismatch, R1 exact restore, activation-safe R2 exact restore/runtime rebound, executor R1→R2 recovery, continued execution after recovery and restart-persisted recovery metadata.

The historical R2 active-document crash is resolved by deferring the same recovery request across AutoCAD Idle ticks, explicitly activating a checkpoint document before closing the original, then activating the restored original before closing the checkpoint. Only inactive documents are closed; activation ambiguity fails closed and retains persisted recovery evidence.

Required fix/gate:

- R2 must make the checkpoint/temp document active before closing the original, and must close only inactive documents;
- restored original must be reopened, activated and rebound to a new `runtime_document_id` before cleanup of the temporary checkpoint document;
- restored `document_pid` and exact predecessor `document_fp` must be independently re-read;
- any activation/close/reopen ambiguity remains `ROLLBACK_FAILED` and retains recovery evidence;
- no AutoCAD crash, Drawing Recovery dependency or hidden modal-dialog intervention may be required for the accepted path;
- final Linux/Windows regressions, C# build, live AutoCAD 2027 Session 1 recovery, review and canonical N7 evidence all pass on the closure tree.

### N8 — Migrate current live operations

**Status: NOT STARTED as the formal COM-to-.NET/public migration phase.** O1 has staged three additional internal native mutation families, but no public MCP routing/capability has changed.

**Mandatory prerequisite added 2026-09-10:** the Python MCP/provider must have a supported fail-safe hot-reload lifecycle before deep N8 migration. Hot reload must drain or deterministically refuse in-flight work, expose one authoritative runtime generation, verify health after reload, preserve the previous healthy generation on reload failure, and keep auth/path policy/public contract identity fail-closed. This requirement is not yet implemented.

The Windows `.171` host has `cloudflared` available per operator infrastructure and can be inspected over SSH for endpoint/tunnel deployment. Tunnel hostname/ingress/credential/service changes remain separate infrastructure work and must be audited/documented before modification.

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

**N0 through N7 and O1 are complete for their documented bounded scopes.**

The accepted staged native executor is now `0.5.0-n7` for the bounded LINE, CIRCLE, ARC and simple LWPOLYLINE recovery scope. N7 adds two-phase validation/checkpoint/recovery machinery and activation-safe R2 restore, but still does not make the native bridge public. **MP-2 Python MCP/provider hot reload is CLOSED / LIVE PASS; the runtime prerequisite before deeper migration is satisfied.** N8/N9/N10 remain unopened until explicitly planned.

MP-2 now provides a stable authenticated ASGI supervisor over replaceable stateless FastMCP workers, source/build generation identity, protocol/contract-hash promotion validation, request fence/drain, last-healthy fallback, frozen policy identity and bounded native-bridge readiness probing. Final measured gates: focused `41 passed`; Linux full `254 passed / 5 skipped`; Windows `.171` full `253 passed / 6 skipped`; process-level `20` successful source reloads + `10` injected startup failures PASS; Windows AutoCAD 2027 Interactive Session 1 COM + required native bridge `2` success + `1` injected failure PASS with TaskScheduler result `0` and zero post-run worker orphans. Canonical evidence: `docs/evidence/mp2-hot-reload-2026-09-11.json`.

Control-plane files `hot_reload.py` and `supervisor.py` are intentionally outside worker source identity and require supervisor restart when changed.

### 7.1 ADR-002 Generic CAD Execution Engine boundary

The next architecture program is governed by `docs/ADR-002-GENERIC-CAD-EXECUTION-ENGINE.md`. CDT-AutoCAD remains domain-agnostic: standards such as TCVN/ISO/ASME, engineering calculations, discipline-specific constraint logic and audit/report generation are owned by external Domain Agents. The provider and C# bridge own only generic CAD execution, identity, bounded storage/query, transaction/recovery and AutoCAD lifecycle invariants.

The approved implementation sequence is:

- **G1 Generic Batch Geometry — CLOSED / LIVE PASS** — typed batch creation for LINE/CIRCLE/ARC/simple-LWPOLYLINE, planar similarity transforms, and referenced block insertion by definition PID; native chunk limit 32, semantic verification limit 2,048, 100/1,000 scale live-pass, and exact batch R0/R1/R2 evidence; cross-chunk atomicity remains explicitly false;
- **G2 Schema-Agnostic Metadata** — namespaced canonical JSON get/set/query over Extension Dictionary/XRecord, with provider-owned PID/recovery namespaces reserved and generic storage/query work strictly bounded;
- **G3 Chunked Logical Atomicity** — a logical batch is accepted only as `COMMITTED_VERIFIED`; any failed chunk triggers reverse compensation and exact predecessor read-back/fingerprint proof for `ROLLED_BACK_VERIFIED`; unverifiable recovery blocks later mutation as uncertain state;
- **graduation ladder** — 100, 1,000, 5,000 and 10,000 entities, measuring correctness, injected failures, longest continuous UI-blocked interval, total latency, memory, recovery latency, modal/busy behavior and process leaks.

G3 explicitly does not keep one AutoCAD `DocumentLock + Transaction` open across thousands of entities. Intermediate chunk state may temporarily exist in the live DWG until the logical operation reaches final verification or verified compensation. A requirement for invisible intermediate state would require a separate staging/off-document Database architecture.

G1 closure does not publish tools or promote the native bridge; later G-series implementation likewise does not automatically publish tools or promote it. Public contract/version/routing changes remain N8/N10 decisions after live native acceptance. Future individual entity-family expansion remains separately bounded; no family or scale tier inherits certification from another automatically.
