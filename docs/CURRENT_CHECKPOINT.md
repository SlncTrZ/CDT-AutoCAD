# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-10 14:20 +07:00
> Status: **N0–N5 CLOSED · N6 NEXT · typed native LINE mutation staged/internal**
> Implementation checkpoint: N5 typed native transaction/rollback closure tree; N4 closure commit `ef5d947`
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
| N6 | **NEXT / NOT STARTED** | semantic pre/post delta, allowed-effects validation, state-chain continuity and manual-drift blocking |
| N7+ | **NOT STARTED** | two-phase post-commit integrity/recovery and later migration/promotion |

## 3. N5 runtime truth

The staged bridge now adds the first bounded native mutation surface under `native/CDT.AutoCAD.Bridge/`; it remains internal and is not the public MCP backend.

Enabled internal operations:

```text
bridge.health
bridge.documents.list
bridge.document.identity
bridge.document.snapshot
entity.create.line
entity.update.line
entity.delete.line
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
- current bridge version is `0.3.0-n5`; `mutation_enabled=true` only for the exact three LINE operations listed above;
- every mutation requires `runtime_document_id + document_pid + expected_parent_fp`; update/delete additionally target a persistent `semantic_pid` rather than Handle/ObjectId;
- `expected_parent_fp` is re-read through the native semantic extractor before the write transaction; mismatch fails with `STATE_DRIFT` before mutation;
- LINE create assigns a fresh persistent PID; update preserves PID/Handle; delete removes the PID-bearing entity from the semantic snapshot;
- deterministic fault injection `after_apply_before_commit` is a closed enum used only to prove R0 `Transaction.Abort()`; create/update aborts return `ROLLED_BACK_VERIFIED` only when independent read-back equals the predecessor semantic fingerprint;
- AutoCAD `DBMOD` behavior after abort is lifecycle metadata rather than semantic identity: measured create-abort left `DBMOD=1`, while update-abort returned `DBMOD=0`; document fingerprint schema v2 therefore retains `saved` in the snapshot but excludes it from `document_fp`;
- document fingerprint schema v2 is advertised by bridge health/snapshot and domain-separated as `document/v2`; N1 geometry/action fingerprint domains remain v1;
- at the N4 semantic capacity boundary, create is rejected before opening the write transaction with `SNAPSHOT_CAPACITY_EXCEEDED`, preventing a committed object from becoming unreadable by the bounded extractor.

Acceptance deployment on Windows `.171` uses the per-user bundle:

```text
%APPDATA%\Autodesk\ApplicationPlugins\CDT.AutoCAD.Bridge.bundle
```

with only its `Contents\Windows` directory explicitly added to `TRUSTEDPATHS`; `SECURELOAD=1` remains enabled.

Canonical N3 evidence: `docs/evidence/n3-native-bridge-readonly-2026-09-10.json`.

Canonical N4 evidence: `docs/evidence/n4-native-semantic-2026-09-10.json`.

Canonical N5 evidence: `docs/evidence/n5-native-mutation-2026-09-10.json`.

## 4. Identity / rollback invariants

Two pillars remain non-negotiable:

1. **Data Integrity / Rollback** — future native mutation may advance only from a known parent state and must end as `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`; uncertain state blocks later mutation.
2. **Precise Identity / PID + Fingerprinting** — native `ObjectId`/Handle is insufficient semantic identity; persistent PID, runtime-document binding and deterministic fingerprints are required.

N2 has proven PID carrier/storage and clone semantics. N3 has proven runtime-document disambiguation over IPC. N4 provides authoritative native parent-state snapshots. N5 now enforces composite runtime-document + lineage PID + semantic `expected_parent_fp` binding on the exact staged LINE mutation allowlist and proves R0 rollback by independent read-back. **N6 state-chain/delta enforcement and N7 post-commit recovery remain open.**

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
- N6+: semantic delta/state-chain enforcement and later post-commit recovery are **not implemented yet**.

Therefore a current drawing workflow may exercise the internal N5 LINE executor on disposable/controlled drawings, but it must **not claim full native Semantic State Loop completion** until N6 delta/state-chain gates and later integrity gates are accepted.

## 6. Verification checkpoint

Current regression/evidence on the N5 closure tree:

- focused N5/N4/native protocol + semantic regression: **68 passed**;
- Linux full Python suite: **155 passed / 4 skipped**;
- Windows `.171` full Python suite: **154 passed / 5 skipped**;
- C# bridge build: **0 errors**;
- documented unsuppressed build-warning families: `Microsoft.VisualBasic`, `System.Drawing`, `WindowsBase` (`MSB3277`);
- native N2 P0–P10: PASS;
- native N3 read-only acceptance: PASS;
- native N4 semantic acceptance: PASS;
- native N5 typed mutation/R0 rollback acceptance: **PASS on real AutoCAD 2027 Session 1**;
- `ruff check`, `compileall` and `git diff --check`: PASS.

Final N5 bridge DLL recorded by canonical evidence:

```text
SHA-256 1e3cfc1dfeb2080cf5d72b89ba435ea4419ab0a12a7cf353e90e8493d2d78ce1
```

## 7. Next development gate

**N6 is the only current N-series implementation frontier.**

N6 must wrap the accepted N5 mutation surface with deterministic semantic-state orchestration:

- capture before/after `SemanticSnapshot` and compute created/modified/deleted PID sets;
- validate those deltas against explicit allowed effects for each ActionSpec;
- append exactly one tamper-evident state-chain entry per accepted semantic step;
- re-read the current document before each later step and reject manual/external changes as `STATE_DRIFT`;
- do not advance the chain for `ROLLED_BACK_VERIFIED`, failed validation, drift or uncertain state;
- persist enough structured evidence to reconstruct the exact failed/accepted step.

N6 must not broaden the native mutation allowlist or implement N7 two-phase post-commit recovery. N7 remains explicitly out of scope until N6 is separately accepted and committed.

## 8. Status authority

Use this file for the current implementation checkpoint. Historical documents and evidence may contain older test counts or statements that were true at their original checkpoint; those records should be read as historical evidence, not as the current frontier.
