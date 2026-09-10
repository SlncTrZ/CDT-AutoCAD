# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-10 17:30 +07:00
> Status: **N0–N6 CLOSED · O1 CLOSED / LIVE PASS · N7 NOT STARTED**
> Implementation checkpoint: O1 typed CIRCLE/ARC/simple-LWPOLYLINE native mutation expansion over N6; base commit `71cc928`
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
| N0 | **CLOSED** | architecture/documentation freeze and migration boundary |
| N1 | **CLOSED / PASS** | Python semantic models, canonicalization/fingerprint primitives, rollback receipts and state-chain primitives |
| N2 | **CLOSED / LIVE PASS** | persistent document/entity PID carrier and clone/reconciliation policy on real AutoCAD 2027, P0–P10 |
| N3 | **CLOSED / LIVE PASS** | staged in-process C# Managed .NET bridge, bounded local typed Named Pipe IPC, runtime-document binding and read-only document identity |
| N4 | **CLOSED / LIVE PASS** | authoritative native read-only `SemanticSnapshot` extraction, persistent entity PID binding and native↔N1 document fingerprint parity |
| N5 | **CLOSED / LIVE PASS** | fixed-schema native LINE create/update/delete, composite parent binding, semantic document-FP v2, R0 abort verification and pre-write capacity guard |
| N6 | **CLOSED / LIVE PASS** | deterministic semantic delta, ActionSpec geometry/effect validation, duplicate detection, state-chain continuity, manual-drift blocking and post-dispatch uncertainty latch |
| O1 | **CLOSED / LIVE PASS** | internal typed CIRCLE/ARC/simple-LWPOLYLINE create/update/delete added to the N5/N6 parent-PID-transaction-semantic-chain foundation |
| N7+ | **NOT STARTED / UNOPENED** | two-phase post-commit integrity/recovery and later formal migration/promotion |

## 3. Current native runtime truth — N6 + O1

N6 provides the Python semantic orchestration layer; O1 extends the staged C# typed mutation allowlist without changing the public MCP contract. The bridge remains internal and is not the public provider backend.

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
- any unverified condition after mutation dispatch begins—including malformed receipt, unknown transport completion, post-commit bridge error, independent read-back failure, validator failure or chain-construction failure—latches `STATE_UNCERTAIN` and blocks later mutation;
- allowed-effects or duplicate-geometry failure discovered only after N5 has committed also latches `STATE_UNCERTAIN`; N6 deliberately performs no R1/R2 recovery because that belongs to N7/later work;
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

N2 has proven PID carrier/storage and clone semantics. N3 has proven runtime-document disambiguation over IPC. N4 provides authoritative native parent-state snapshots. N5 enforces composite runtime-document + lineage PID + semantic `expected_parent_fp` binding and proves R0 rollback. N6 independently validates semantic delta/effects and advances a tamper-evident state chain only for accepted steps. O1 live-proves that the same integrity model extends to CIRCLE, ARC and simple LWPOLYLINE without opening N7. **N7 post-commit recovery remains NOT STARTED.**

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
- N7+: two-phase post-commit recovery, R1/R2 restoration and formal broader migration/promotion are **not implemented yet**.

Therefore the staged native executor now covers four basic 2D families under the accepted N6 semantic loop for controlled/disposable workflows, but this still does **not** constitute full native migration/promotion or N7 recovery capability.

## 6. Verification checkpoint

Current regression/evidence on the O1 closure tree:

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

Native bridge DLL accepted for O1:

```text
SHA-256 4ad2d4138d6e71a8fa91281ea5482c592e2a0c04cb5d41ec5f2452b7cff31484
```

## 7. Current boundary / next activity

**N7 is explicitly NOT STARTED and remains unopened.** O1 was a separately authorized internal operation-family expansion over the closed N4–N6 foundation; it does not provide N7 recovery and does not promote the bridge publicly.

Future operation-family work must continue one bounded family at a time with the same TDD → native build → AutoCAD Session 1 acceptance → semantic-chain evidence → review → commit boundary. TEXT/MTEXT, ELLIPSE/SPLINE, BLOCK/DIM/HATCH and later families remain future work until explicitly implemented and accepted.

## 8. Status authority

Use this file for the current implementation checkpoint. Historical documents and evidence may contain older test counts or statements that were true at their original checkpoint; those records should be read as historical evidence, not as the current frontier.
