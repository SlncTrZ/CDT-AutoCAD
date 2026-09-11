# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-11 11:10 +07:00
> Status: **MP0-T00 CLOSED / PASS · A-01 CLOSED / PASS · A-02 CLOSED / LIVE PASS · A-03+A-06 CLOSED / PASS · N0–N7 CLOSED · O1 CLOSED / LIVE PASS · MP-2 HOT RELOAD CLOSED / LIVE PASS**
> Accepted implementation baseline: `9224fb0` (`Feat: close MP-2 hot reload runtime`)
> Working architecture checkpoint: **ADR-002 ACCEPTED — CDT-AutoCAD remains a Generic CAD Execution Engine; domain standards/business logic/audit reporting stay in external Domain Agents.** Next program: G1 Generic Batch Geometry → G2 Schema-Agnostic Metadata → G3 Chunked Logical Atomicity; no G-series implementation is claimed yet.
> Primary native target: AutoCAD 2027 full · Windows x64 · COM `26.0` · Managed .NET `net10.0-windows`

## 1. Public runtime

The published/runtime baseline remains unchanged during the architecture migration:

- provider version: `0.3.0rc1`;
- contract: `autocad-a2-v1-rc1`;
- public MCP surface: **50 tools**;
- default backend: `ezdxf`;
- live Windows backend: ActiveX/COM;
- A2: live-verified PASS on AutoCAD 2027, still release-candidate pending explicit promotion;
- A3.1 / A3.2 / A3.3: native live-verified PASS but staged, capability-false/non-public until explicit contract promotion.

The N-series Managed .NET bridge is **not yet the public provider backend**.

## 2. Architecture checkpoint

| Phase | State | What is proven |
| --- | --- | --- |
| MP0-T00 | **CLOSED / PASS** | platform-specific PEP 751 dependency locks from one canonical resolver workflow; source/build/DLL/runtime provenance; baseline threat model; structured request diagnostics/correlation with telemetry separated from recovery journal truth |
| A-01 | **CLOSED / PASS** | COM timeout/cancel after dispatch is non-retryable unknown completion; one STA executor + mutation gate fence queued writers; late completion remains quarantined; read-only reconciliation stays available; document_new/open cannot clear quarantine |
| A-02 | **CLOSED / LIVE PASS** | `document_save` binds one concrete COM Document through path validation → Save → post-save path verification; active-tab switches cannot retarget Save; outside-root target is refused before side effect; disposable AutoCAD 2027 Session 1 fixture passed |
| A-03 / A-06 | **CLOSED / PASS** | all 50 public MCP tools have unique semantic descriptions + annotations; 12 true read-only tools expose `readOnlyHint`; status separates implementation, current runtime readiness, historical certification and build identity without self-certifying the current process |
| N0 | **CLOSED** | architecture/documentation freeze and migration boundary |
| N1 | **CLOSED / PASS** | Python semantic models, canonicalization/fingerprint primitives, rollback receipts and state-chain primitives |
| N2 | **CLOSED / LIVE PASS** | persistent document/entity PID carrier and clone/reconciliation policy on real AutoCAD 2027, P0–P10 |
| N3 | **CLOSED / LIVE PASS** | staged in-process C# Managed .NET bridge, bounded local typed Named Pipe IPC, runtime-document binding and read-only document identity |
| N4 | **CLOSED / LIVE PASS** | authoritative native read-only `SemanticSnapshot` extraction, persistent entity PID binding and native↔N1 document fingerprint parity |
| N5 | **CLOSED / LIVE PASS** | fixed-schema native LINE create/update/delete, composite parent binding, semantic document-FP v2, R0 abort verification and pre-write capacity guard |
| N6 | **CLOSED / LIVE PASS** | deterministic semantic delta, ActionSpec geometry/effect validation, duplicate detection, state-chain continuity, manual-drift blocking and post-dispatch uncertainty latch |
| O1 | **CLOSED / LIVE PASS** | internal typed CIRCLE/ARC/simple-LWPOLYLINE create/update/delete added to the N5/N6 parent-PID-transaction-semantic-chain foundation |
| N7 | **CLOSED / LIVE PASS** | two-phase native commit integrity, immutable checkpoint manifests/artifact hashes, R0/R1/R2 recovery, executor R1→R2 cascade, restart-persisted recovery and activation-safe R2 document replacement/rebinding all passed on real AutoCAD 2027 Session 1 |
| MP-2 | **CLOSED / LIVE PASS** | stable authenticated ASGI supervisor, replaceable stateless FastMCP workers, generation fencing/drain/fallback, protocol/contract-hash + runtime generation/build/policy identity, bounded native-bridge readiness probe and source watcher; focused `41 passed`, Linux `254/5`, Windows `.171` `253/6`, process `20` success + `10` injected failure PASS, AutoCAD 2027 Session 1 COM + required bridge `2` success + `1` failure PASS, TaskScheduler result `0`, zero worker orphans |
| G1 | **PLANNED / NOT STARTED** | generic typed batch geometry (`batch_create_entities`, transforms, block insertion) with bounded chunking and UI-blocking measurements; no domain concepts |
| G2 | **PLANNED / NOT STARTED** | schema-agnostic namespaced JSON metadata get/set/query over Extension Dictionary/XRecord with reserved provider-state isolation and bounded query/storage work |
| G3 | **PLANNED / NOT STARTED** | logical all-or-nothing batch outcome across bounded native chunks using checkpoint/journal/compensation plus exact predecessor fingerprint proof |
| N8 | **NOT STARTED** | formal incremental COM-to-.NET/public operation migration |
| N9 | **NOT STARTED** | semantic drawing-workflow migration |
| N10 | **NOT STARTED** | explicit public contract/promotion decision |

## 3. Accepted native runtime truth — N7 closed; public backend unchanged

N6 provides the Python semantic orchestration layer; O1 extends the staged C# typed mutation allowlist without changing the public MCP contract. N7 (`0.5.0-n7`) is now accepted for its documented internal recovery scope after the final AutoCAD 2027 Session 1 lifecycle gate. It is **still not the public provider backend** and does not publish new MCP tools or capabilities.

Enabled internal operations:

```text
bridge.health
bridge.documents.list
bridge.document.identity
bridge.document.snapshot
entity.create.line
entity.update.line
entity.delete.line
entity.create.circle
entity.update.circle
entity.delete.circle
entity.create.arc
entity.update.arc
entity.delete.arc
entity.create.lwpolyline
entity.update.lwpolyline
entity.delete.lwpolyline
```

Measured invariants:

- protocol: `cdt-autocad-native-v1`;
- maximum frame payload: 65,536 bytes;
- transport: Windows Named Pipe;
- pipe boundary: current user + local computer + same Windows session;
- same-user SSH Session 0 is rejected with `CLIENT_SESSION_MISMATCH`;
- pipe I/O thread does not call AutoCAD API; native reads are dispatched on `Application.Idle`;
- `document_pid` is semantic-lineage identity, not unique physical-file identity;
- two open raw-copy DWGs sharing one `document_pid` receive distinct `runtime_document_id` values;
- PID-only target selection is rejected;
- read-only N3 acceptance leaves tested drawings at `DBMOD=0`;
- `bridge.document.snapshot` reads the active bound AutoCAD Database through native `ForRead` transactions;
- N4 scope is intentionally bounded to the active document's current space plus referenced block definitions/content, with `MaxSnapshotEntities=32`; it is not yet a whole-DWG/all-layout semantic extractor;
- extracted entities require persistent N2 semantic PIDs and duplicate/missing PIDs fail closed;
- supported N4 semantic families live-proven: LINE, CIRCLE, ARC, LWPOLYLINE, TEXT, MTEXT, INSERT, BLOCK_DEFINITION, DIMENSION and HATCH;
- referenced block definitions and their PID-bearing contents are included recursively in the semantic fingerprint; a saved block-definition geometry mutation changes `document_fp` even with `DBMOD=0` on both compared states;
- layer, linetype, text-style and dimension-style resources are extracted deterministically;
- native document fingerprint matches the N1 canonical fingerprint byte-for-byte for the accepted fixture;
- repeated reads keep `DBMOD=0`, and save/reopen preserves the semantic document fingerprint;
- a snapshot whose serialized result exceeds the 65,536-byte frame fails as correlated `RESPONSE_TOO_LARGE`; the bridge remains alive and the read attempt does not change `DBMOD`;
- accepted staged bridge version is `0.5.0-n7`; `mutation_enabled=true` only for the exact 12 typed LINE/CIRCLE/ARC/LWPOLYLINE operations listed above;
- every mutation requires `runtime_document_id + document_pid + expected_parent_fp`; update/delete additionally target a persistent `semantic_pid` rather than Handle/ObjectId;
- `expected_parent_fp` is re-read through the native semantic extractor before the write transaction; mismatch fails with `STATE_DRIFT` before mutation;
- create assigns a fresh persistent PID for LINE/CIRCLE/ARC/LWPOLYLINE; update preserves PID/Handle; delete removes the PID-bearing entity from the semantic snapshot;
- CIRCLE and ARC create/update are independently checked against native center/radius/angle semantics and COM measurements; create uses +Z planar normals while updates preserve the target normal represented in the authoritative snapshot;
- O1 LWPOLYLINE create/update is intentionally simple 2D: finite `[x,y]` vertices, max 128 vertices, zero elevation, +Z normal, zero bulge and zero per-vertex widths; complex existing polylines are refused for update as `UNSUPPORTED_TARGET_GEOMETRY` before write;
- LWPOLYLINE update rebuilds geometry in place to preserve persistent identity and AutoCAD validity; live acceptance proves closed 4-vertex -> open 2-vertex shrink -> closed 5-vertex grow while PID/Handle and COM parity remain stable;
- deterministic fault injection `after_apply_before_commit` is a closed enum used only to prove R0 `Transaction.Abort()`; create/update aborts return `ROLLED_BACK_VERIFIED` only when independent read-back equals the predecessor semantic fingerprint;
- AutoCAD `DBMOD` behavior after abort is lifecycle metadata rather than semantic identity: measured create-abort left `DBMOD=1`, while update-abort returned `DBMOD=0`; document fingerprint schema v2 therefore retains `saved` in the snapshot but excludes it from `document_fp`;
- document fingerprint schema v2 is advertised by bridge health/snapshot and domain-separated as `document/v2`; N1 geometry/action fingerprint domains remain v1;
- at the N4 semantic capacity boundary, create is rejected before opening the write transaction with `SNAPSHOT_CAPACITY_EXCEEDED`, preventing a committed object from becoming unreadable by the bounded extractor;
- N6 computes deterministic created/modified/deleted PID sets from authoritative before/after snapshots and ignores transient Handle/fingerprint-cache changes;
- create/update geometry for LINE/CIRCLE/ARC/simple-LWPOLYLINE is independently matched against the `ActionSpec`; update additionally requires target type/layer/style/hierarchy to remain unchanged, and the typed mutation surface may not alter document units/current space/style resources;
- newly introduced exact duplicate geometry is detected even when PID/Handle differ; pre-existing duplicate groups are not retroactively rejected by unrelated mutations;
- every accepted `COMMITTED_VERIFIED` semantic step appends exactly one tamper-evident `StateChainEntry`; verified R0 rollback does not advance the chain;
- before every later step, current native `document_fp` must equal both the action parent and chain tip; measured manual COM geometry drift is refused as `STATE_DRIFT` before a new native mutation is dispatched;
- on the accepted O1/N6 baseline, any unverified condition after mutation dispatch begins—including malformed receipt, unknown transport completion, post-commit bridge error, independent read-back failure, validator failure or chain-construction failure—latches `STATE_UNCERTAIN` and blocks later mutation;
- N7 now replaces deterministic post-commit semantic-integrity failures with checkpoint-backed R1/R2 recovery: the state chain advances only for accepted commits, verified recovery restores the exact predecessor, and unresolved/ambiguous recovery remains fail-closed;
- N6 records append-only JSONL evidence with `fsync`; journal write failure blocks execution, and non-empty journals are not silently resumed across a new executor process;
- native relation extraction remains empty in the current bridge, so N6 does not claim topology validation beyond the semantic fields actually present.

Acceptance deployment on Windows `.171` uses the per-user bundle:

```text
%APPDATA%\Autodesk\ApplicationPlugins\CDT.AutoCAD.Bridge.bundle
```

with only its `Contents\Windows` directory explicitly added to `TRUSTEDPATHS`; `SECURELOAD=1` remains enabled.

Canonical N3 evidence: `docs/evidence/n3-native-bridge-readonly-2026-09-10.json`.

Canonical N4 evidence: `docs/evidence/n4-native-semantic-2026-09-10.json`.

Canonical N5 evidence: `docs/evidence/n5-native-mutation-2026-09-10.json`.

Canonical N6 evidence: `docs/evidence/n6-semantic-state-chain-2026-09-10.json`.

Canonical O1 evidence: `docs/evidence/o1-native-shape-mutation-2026-09-10.json`.

## 4. Identity / rollback invariants

Two pillars remain non-negotiable:

1. **Data Integrity / Rollback** — future native mutation may advance only from a known parent state and must end as `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`; uncertain state blocks later mutation.
2. **Precise Identity / PID + Fingerprinting** — native `ObjectId`/Handle is insufficient semantic identity; persistent PID, runtime-document binding and deterministic fingerprints are required.

N2 has proven PID carrier/storage and clone semantics. N3 has proven runtime-document disambiguation over IPC. N4 provides authoritative native parent-state snapshots. N5 enforces composite runtime-document + lineage PID + semantic `expected_parent_fp` binding and proves R0 rollback. N6 independently validates semantic delta/effects and advances a tamper-evident state chain only for accepted steps. O1 live-proves that the same integrity model extends to CIRCLE, ARC and simple LWPOLYLINE. **N7 now closes bounded post-commit integrity/recovery: R1 compensation and activation-safe R2 checkpoint restore both require exact predecessor read-back before `ROLLED_BACK_VERIFIED`.**

## 5. Semantic State Loop implementation status

The target loop remains mandatory:

```text
ActionSpec
 -> native execution
 -> SemanticSnapshot extraction
 -> canonicalize / fingerprint / semantic diff
 -> deterministic validation
 -> commit or verified rollback
 -> independent read-back
 -> state-chain log
```

Current implementation coverage is partial:

- N1: semantic/fingerprint/state-chain primitives exist in Python;
- N2: persistent PID policy exists and is live-verified;
- N3: native read-only transport/document binding exists and is live-verified;
- N4: native read-only semantic extraction + parent document fingerprinting is implemented and live-verified;
- N5: bounded typed LINE mutation + `expected_parent_fp` + native transaction/R0 rollback is implemented and live-verified;
- N6: deterministic semantic delta, requested-geometry/allowed-effects validation, duplicate detection, state-chain continuity, manual-drift blocking and uncertainty latching are implemented and live-verified;
- O1: the same internal mutation/semantic loop is live-verified for CIRCLE, ARC and simple LWPOLYLINE create/update/delete;
- N7 accepted scope: provisional extraction inside the write transaction, deterministic native validation, post-commit provisional-vs-persisted comparison, provider-owned immutable checkpoint manifests/artifact hashes, typed recovery list/resolve/finalize, R1 compensation, R2 checkpoint restore and Python executor R1→R2 orchestration;
- N7 R2 lifecycle is activation-safe: the bridge defers one recovery request across AutoCAD Idle ticks, activates a temporary checkpoint document first, closes only inactive documents, reopens/reactivates the restored original, then proves runtime rebound + document PID + exact predecessor fingerprint before success;
- N8/N9/N10: formal broader migration, drawing-workflow migration and public promotion remain **NOT STARTED**.

Therefore the accepted staged native executor covers four basic 2D families under the N7 integrity/recovery loop. This is internal staged capability only; it does not imply whole-DWG coverage or public routing.

## 6. Verification checkpoint

MP0-T00 closure evidence on isolated clean gates derived from `1957ad8` and containing no N7 dirty files:

- canonical resolver: pip `26.1.2` `pip lock`, PEP 751 platform locks;
- `pylock.linux.toml`: 88 locked packages, SHA-256 `e7fe668b159c56233be4d008600fe80dd0778689a58b48ab55cecc670c45bf06`;
- `pylock.windows.toml`: 89 locked packages, SHA-256 `acbbc28bc07d26c2e07c76ab5d24f2e474945ba10cc14a0c9e94cca29867869e`;
- canonical lock generator normalizes platform output to LF and has an explicit newline-regression test;
- clean Linux locked environment + full suite: **204 passed / 4 skipped**;
- clean Windows `.171` locked environment + full suite: **203 passed / 5 skipped** with AutoCAD 2027 running in Session 1;
- changed-file Ruff on Linux and Windows: PASS; compileall on Linux and Windows: PASS; `git diff --check` on both clean gates: PASS;
- clean-baseline full-tree Ruff with locked `ruff 0.16.7` reports **12 pre-existing findings outside MP0-T00 scope**; they remain separate lint debt and were not opportunistically refactored;
- structured MCP diagnostics: correlated start/completion events PASS; injected telemetry-sink failure does not fail the tool call; existing N6 journal-failure test remains fail-closed as `JOURNAL_FAILED`;
- canonical MP0 evidence: `docs/evidence/mp0-t00-baseline-2026-09-11.json`, with Linux/Windows runtime manifests beside it;
- observed installed `.171` bridge DLL SHA-256 remains `8f3c28b7f765c1420afaeafc9f54f726e5c24306de6a852ea3f38758158b3767`; MP0 records it as **OBSERVED_ONLY**, not as a newly certified native artifact.

A-01 closure evidence on isolated clean gates derived from `4e0a714` and containing no N7 dirty files:

- focused timeout-safety suite: **8/8 PASS** on Linux and Windows `.171`;
- clean Linux locked environment + full suite: **212 passed / 4 skipped**;
- clean Windows `.171` locked environment + full suite: **211 passed / 5 skipped** with AutoCAD 2027 running in Session 1;
- unknown mutation completion uses one COM STA executor plus an async mutation gate before dispatch; a request queued before the first timeout is fenced before its callable reaches AutoCAD;
- mutation timeout/cancel reports `retryable=false` + `completion_unknown=true`; read-only timeout remains retryable and does not quarantine;
- late completion does not clear quarantine; read-only calls remain available for reconciliation; `document_new`/`document_open` do not clear quarantine; mutation resumes only after verified operator recovery and provider-process restart;
- changed-file Ruff, compileall and `git diff --check`: PASS on Linux and Windows; `ezdxf_backend.py:157 B905` remains a documented pre-existing lint finding outside the A-01 hunk;
- real live timeout injection was intentionally not performed because forcing unknown mutation completion would itself create uncontrolled CAD state; deterministic Windows fault-injection covers the state machine while the full suite runs on the target platform;
- canonical evidence: `docs/evidence/a01-com-timeout-safety-2026-09-11.json`.

A-02 closure evidence on isolated clean gates derived from `565712b` and containing no N7 dirty files:

- focused document-binding suite: **3 PASS / 1 live SKIP** on Linux and Windows `.171`;
- vulnerable implementation reproduced **2 FAIL / 1 PASS** before the fix: active-document switch saved B after validating A, and post-Save path drift returned false success;
- clean Linux full suite: **215 passed / 5 skipped**;
- clean Windows `.171` full suite: **214 passed / 6 skipped**;
- sibling `document_open` / `document_save_as` / `document_export_pdf` audit: **7/7 PASS** with no A-02 split-binding reproduction, therefore no unrelated sibling rewrite;
- real AutoCAD 2027 Session 1 disposable fixture: **1/1 PASS in 2.57s** using interactive `pythonw.exe`; flow was new document → SaveAs temp DWG → `document_save` → verified same path → close;
- live evidence hashes: log `0503f4d47e5512758e16e57111568accad1349a27e79967770c014b8dea6439f`, JUnit `fa3c66770fdd8090adffe6962729f87bfed30248d6ab7462cac6de0cba936e2c`;
- Windows locked Ruff: PASS; Linux/Windows compileall and `git diff --check`: PASS; current gateway Linux `.deps` Ruff wrapper lacked its binary, so no false Linux lint-PASS claim is made;
- canonical evidence: `docs/evidence/a02-document-binding-2026-09-11.json`.

A-03/A-06 closure evidence on isolated clean gates derived from `39a093c` and containing no N7 dirty files:

- runtime MCP catalog on Linux and Windows: **50 tools / 0 missing descriptions / 0 missing annotations / 50 unique descriptions / 12 read-only hints**;
- catalog TDD gate failed before metadata was added; public status TDD gate failed before implementation/runtime/certification separation was added;
- focused server-contract + COM metadata suite: **45 passed / 1 skipped** on the current tree before clean isolation;
- clean Linux full suite: **217 passed / 5 skipped**;
- clean Windows `.171` full suite: **216 passed / 6 skipped**;
- current-process self-certification is explicitly forbidden: detected release match may be true while `current_process_certified` remains false without bound provenance;
- Windows locked Ruff, Linux/Windows compileall and `git diff --check`: PASS; Linux gateway has no runnable Ruff binary so no Linux Ruff PASS is claimed;
- public tool count remains exactly 50 and no capability is promoted by metadata changes;
- canonical evidence: `docs/evidence/a03-tool-catalog-status-2026-09-11.json`.

Accepted regression/evidence on the O1 closure tree:

- focused O1/N5/N6 native-protocol + semantic regression: **76 passed**;
- Linux full Python suite: **195 passed / 4 skipped**;
- Windows `.171` full Python suite: **194 passed / 5 skipped**;
- C# bridge build: **0 errors**;
- documented unsuppressed build-warning families: `Microsoft.VisualBasic`, `System.Drawing`, `WindowsBase` (`MSB3277`);
- native N2 P0–P10: PASS;
- native N3 read-only acceptance: PASS;
- native N4 semantic acceptance: PASS;
- native N5 typed mutation/R0 rollback acceptance: PASS;
- native N6 semantic delta/state-chain acceptance: PASS;
- native O1 CIRCLE/ARC/simple-LWPOLYLINE mutation + nine-step state-chain acceptance: **PASS on real AutoCAD 2027 Session 1**;
- O1 changed-file `ruff check`, `compileall` and `git diff --check`: PASS;
- full-repository Ruff currently reports **14 pre-existing findings outside the N6 change set**; they remain separate lint debt and are not treated as N6 pass evidence.

N7 closure evidence:

- final focused recovery/semantic/bridge regression: **40/40 PASS**;
- final Linux full Python suite: **227 passed / 5 skipped**;
- final Windows `.171` full Python suite: **226 passed / 6 skipped**;
- C# Release x64 build with user-local .NET SDK `10.0.401`: **0 errors**, retaining only the documented `MSB3277` warning families (`Microsoft.VisualBasic`, `System.Drawing`, `WindowsBase`);
- real AutoCAD 2027 Session 1 full recovery runner: **PASS / exit 0 / TaskScheduler result 0**; AutoCAD restarted during the test and remained alive in Session 1 afterward;
- live checks all PASS: exact 12 typed mutations, typed recovery surface, R0 exact predecessor, provisional abort, checkpoint finalize, post-commit mismatch detection, R1 exact restore, corrupt-manifest fail-closed, R1 failure retains checkpoint, R2 exact restore/runtime rebound, executor auto R1→R2 + continued execution, restart-persisted manifest, restart R1 unavailable fail-closed, restart R2 exact predecessor, no pending recovery and no arbitrary execution surface;
- final restored fingerprint `sha256:2ab0d6ce7dab33530d600fce060ffbd125d561b442d0175c40b3c70644cc6984` exactly matches the accepted pre-corruption chain tip after real process restart;
- deployed/live bridge DLL SHA-256 `4b76e362f20122b9a8b8b18471a3a1a3d9a77718dc749bf8b8a25cfb65bb578d`; live summary SHA-256 `a38e17556b04c1c5201e8d5049e3880e16a05a75fac539da1fec3dafadfbd44f`;
- canonical evidence: `docs/evidence/n7-native-recovery-2026-09-11.json`.

Native bridge DLL accepted for O1:

```text
SHA-256 4ad2d4138d6e71a8fa91281ea5482c592e2a0c04cb5d41ec5f2452b7cff31484
```

## 7. Current boundary / next activity

Master Plan public-safety prework and N7 are now CLOSED: **MP0-T00, A-01, A-02, A-03/A-06 and N7 are CLOSED/PASS.** N7 eliminated the active-document crash path by splitting R2 across verified AutoCAD Idle activation phases and closing only inactive documents.

**MP-2 Python MCP/provider hot reload is CLOSED / LIVE PASS.** The runtime now uses a stable authenticated ASGI supervisor over replaceable stateless FastMCP worker generations, with request fencing/drain, last-healthy fallback, frozen policy identity, protocol/contract-hash promotion validation, runtime generation/build provenance and bounded native-bridge readiness probing. Public COM/ezdxf behavior and the 50-tool contract remain unchanged.

Final MP-2 evidence: focused `41 passed`; Linux full `254 passed / 5 skipped`; Windows `.171` full `253 passed / 6 skipped`; process-level `20` successful source reloads + `10` injected startup failures PASS; AutoCAD 2027 Interactive Session 1 COM + required native bridge `2` success + `1` injected failure PASS with TaskScheduler result `0`, bridge `0.5.0-n7`, pending recovery `0`, and zero post-run worker orphans. Canonical evidence: `docs/evidence/mp2-hot-reload-2026-09-11.json`. Control-plane edits to `hot_reload.py` or `supervisor.py` require supervisor restart by design.

`.171` currently has `cloudflared` running but no CDT-AutoCAD ingress route; its config remains unmodified. Tunnel/process presence is not provider readiness and must never replace authenticated MCP + required bridge health checks.

Historical MP-2 implementation handoff: `docs/SESSION_HANDOFF_2026-09-11_MP2_HOT_RELOAD.md`; canonical MP-2 closure evidence: `docs/evidence/mp2-hot-reload-2026-09-11.json`. Historical N7 handoff/incident record: `docs/N7_WORKING_CHECKPOINT_2026-09-10.md`; canonical N7 closure evidence: `docs/evidence/n7-native-recovery-2026-09-11.json`.

The next approved engineering program is defined by `docs/ADR-002-GENERIC-CAD-EXECUTION-ENGINE.md`: **G1 Generic Batch Geometry → G2 Schema-Agnostic Metadata → G3 Chunked Logical Atomicity**, followed by measured graduation at `100 → 1,000 → 5,000 → 10,000` entities. CDT-AutoCAD must stay domain-agnostic: TCVN/ISO/ASME/customer rules, engineering calculations and audit/report logic belong to external Domain Agents. G-series work must preserve bounded typed contracts, TDD, native build, AutoCAD Session 1 acceptance, semantic-chain/recovery evidence, review and separate commits. Public contract changes remain separately gated.

## 8. Status authority

Use this file for the current implementation checkpoint. Historical documents and evidence may contain older test counts or statements that were true at their original checkpoint; those records should be read as historical evidence, not as the current frontier.
