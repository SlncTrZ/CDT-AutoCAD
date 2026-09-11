# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-11 +07:00
> Status: **MP0-T00 CLOSED / PASS · A-01 NEXT · N0–N6 CLOSED · O1 CLOSED / LIVE PASS · N7 PRESERVED / NOT CLOSED**
> Accepted implementation baseline: `04ff820` (`Feat: expand native typed shape mutations`)
> Working implementation checkpoint: N7 two-phase native commit integrity + R0/R1/R2 recovery remains uncommitted and preserved; Master Plan ordering is MP0-T00 → A-01 → A-02 → A-03 → resume N7
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
| N0 | **CLOSED** | architecture/documentation freeze and migration boundary |
| N1 | **CLOSED / PASS** | Python semantic models, canonicalization/fingerprint primitives, rollback receipts and state-chain primitives |
| N2 | **CLOSED / LIVE PASS** | persistent document/entity PID carrier and clone/reconciliation policy on real AutoCAD 2027, P0–P10 |
| N3 | **CLOSED / LIVE PASS** | staged in-process C# Managed .NET bridge, bounded local typed Named Pipe IPC, runtime-document binding and read-only document identity |
| N4 | **CLOSED / LIVE PASS** | authoritative native read-only `SemanticSnapshot` extraction, persistent entity PID binding and native↔N1 document fingerprint parity |
| N5 | **CLOSED / LIVE PASS** | fixed-schema native LINE create/update/delete, composite parent binding, semantic document-FP v2, R0 abort verification and pre-write capacity guard |
| N6 | **CLOSED / LIVE PASS** | deterministic semantic delta, ActionSpec geometry/effect validation, duplicate detection, state-chain continuity, manual-drift blocking and post-dispatch uncertainty latch |
| O1 | **CLOSED / LIVE PASS** | internal typed CIRCLE/ARC/simple-LWPOLYLINE create/update/delete added to the N5/N6 parent-PID-transaction-semantic-chain foundation |
| N7 | **IN PROGRESS / NOT CLOSED** | provisional in-transaction validation, persisted checkpoint/recovery protocol and R0/R1/R2 implementation exist on the working tree; final R2 lifecycle acceptance is blocked by an AutoCAD active-document crash |
| N8 | **NOT STARTED** | formal incremental COM-to-.NET/public operation migration |
| N9 | **NOT STARTED** | semantic drawing-workflow migration |
| N10 | **NOT STARTED** | explicit public contract/promotion decision |

## 3. Accepted native runtime truth — N6 + O1; N7 candidate is not accepted

N6 provides the accepted Python semantic orchestration layer; O1 extends the staged C# typed mutation allowlist without changing the public MCP contract. The accepted bridge baseline remains O1 (`0.4.0-o1`). N7 development uses a `0.5.0-n7` candidate, but that candidate is **not accepted, not committed and not the public provider backend**.

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
- current bridge version is `0.4.0-o1`; `mutation_enabled=true` only for the exact 12 typed LINE/CIRCLE/ARC/LWPOLYLINE operations listed above;
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
- on the accepted O1/N6 baseline, allowed-effects or duplicate-geometry failure discovered only after native commit also latches `STATE_UNCERTAIN`; the working N7 candidate is specifically replacing those post-commit uncertainty paths with checkpoint-backed R1/R2 recovery, but that behavior is not yet accepted;
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

N2 has proven PID carrier/storage and clone semantics. N3 has proven runtime-document disambiguation over IPC. N4 provides authoritative native parent-state snapshots. N5 enforces composite runtime-document + lineage PID + semantic `expected_parent_fp` binding and proves R0 rollback. N6 independently validates semantic delta/effects and advances a tamper-evident state chain only for accepted steps. O1 live-proves that the same integrity model extends to CIRCLE, ARC and simple LWPOLYLINE. **N7 post-commit recovery is now IN PROGRESS but remains unaccepted until its R2 document-lifecycle crash is eliminated and the full gate passes.**

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
- N7 working candidate: provisional extraction inside the write transaction, deterministic native validation, post-commit provisional-vs-persisted comparison, provider-owned checkpoint manifests/artifact hashes, typed recovery list/resolve/finalize operations, R1 compensation and R2 checkpoint restore are implemented on the working tree; Python executor recovery TDD and several real AutoCAD R0/R1/R2 paths have passed during development;
- N7 close gate: **OPEN** because the final R2-after-restart acceptance exposed an AutoCAD `FATAL ERROR: Unhandled Access Violation` while the bridge closed/restored an active document; the implementation must be activation-safe before acceptance;
- N8/N9/N10: formal broader migration, drawing-workflow migration and public promotion remain **NOT STARTED**.

Therefore the accepted staged native executor still covers four basic 2D families under the O1/N6 integrity loop. N7 code exists but does **not** yet constitute accepted post-commit recovery capability.

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

N7 development evidence beyond the accepted O1 baseline:

- N7 recovery protocol/client combined contract: **10/10 PASS** at the latest focused checkpoint;
- N7 + N6 recovery/state-chain focused checkpoint: **45/45 PASS**;
- broader native/semantic focused checkpoint: **86/86 PASS**;
- N7 C# candidate build on AutoCAD 2027 SDK: **0 errors** with the same documented `MSB3277` warning families;
- real AutoCAD 2027 development runs have exercised R0 exact restore, provisional-abort, post-commit mismatch detection, R1 exact restore, R2 exact restore/runtime rebound, executor R1→R2 recovery, restart-persisted recovery metadata and corrupt-manifest fail-closed behavior;
- these results are **not N7 closure evidence** because the final R2 document lifecycle still has a reproducible/observed AutoCAD crash path.

Native bridge DLL accepted for O1:

```text
SHA-256 4ad2d4138d6e71a8fa91281ea5482c592e2a0c04cb5d41ec5f2452b7cff31484
```

## 7. Current boundary / next activity

Master Plan execution order is now authoritative: **MP0-T00 is CLOSED/PASS; next is A-01, then A-02, then A-03, and only then resume N7.** The existing N7 working tree is intentionally preserved unchanged while the public-safety fixes execute in front of it.

**N7 remains open / NOT CLOSED.** Its immediate recovery blocker is R2 document replacement safety: the bridge must not close/replace an active AutoCAD document from an unsafe application-context callback. When N7 resumes after A-03, the next recovery slice must switch activation safely, close only inactive documents, reopen the restored original, rebind runtime identity, and independently prove `document_pid + document_fp` before returning `ROLLED_BACK_VERIFIED`.

Detailed unfinished-N7 handoff: `docs/N7_WORKING_CHECKPOINT_2026-09-10.md`.

A new mandatory infrastructure/runtime requirement is also recorded: **the Python MCP/provider must support a fail-safe hot-reload lifecycle before the formal N8/N9/N10 migration is considered complete.** Python hot reload is not yet implemented. The `.171` Windows host has `cloudflared` available per operator infrastructure; its tunnel/service configuration has not yet been audited or modified for CDT-AutoCAD.

After N7 is closed, Python MCP hot reload should be implemented and accepted before deep N8/N9/N10 migration. Future operation-family work must still keep bounded typed contracts, TDD, native build, AutoCAD Session 1 acceptance, semantic-chain evidence, review and separate commits. TEXT/MTEXT, ELLIPSE/SPLINE, BLOCK/DIM/HATCH and later families remain future work until explicitly implemented and accepted.

## 8. Status authority

Use this file for the current implementation checkpoint. Historical documents and evidence may contain older test counts or statements that were true at their original checkpoint; those records should be read as historical evidence, not as the current frontier.
