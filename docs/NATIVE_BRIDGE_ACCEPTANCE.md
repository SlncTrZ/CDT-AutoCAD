# Native Bridge + Semantic Integrity Acceptance

> Updated: 2026-09-10 14:20 +07:00
> Status: N3/NB0 LIVE PASS · N4/NB1 LIVE PASS · N5/NB4 bounded R0 LIVE PASS · NB3 document-FP v2 parent binding PASS · NB5–NB10 otherwise OPEN
> Target: AutoCAD 2027 full / Windows x64 / Managed .NET

## 1. Purpose

This runbook defines the acceptance gates for the staged C# Managed .NET native bridge and the remaining Semantic State Loop migration. N3/NB0, N4/NB1 and the bounded N5 R0 native-mutation subset have live-passed; N6 semantic delta/state-chain and later integrity gates remain open. It does not replace the existing COM live-acceptance lane; the two run side-by-side during migration.

The upgrade is not accepted unless both core pillars are proven under fault injection:

1. **Data Integrity / Rollback**
2. **Precise Identity / PID + Fingerprinting**

### N3.0 measured checkpoint — LIVE PASS (2026-09-10)

The Managed .NET bridge skeleton is now implemented and live-verified in full AutoCAD 2027 on Windows `.171`. Canonical evidence: `docs/evidence/n3-native-bridge-readonly-2026-09-10.json`.

Measured properties:

- `net10.0-windows` in-process bundle autoloads inside interactive `acad.exe` Session 1;
- protocol `cdt-autocad-native-v1`, 65,536-byte frame limit and request correlation are enforced;
- only `bridge.health`, `bridge.documents.list`, and `bridge.document.identity` are enabled; `mutation_enabled=false`;
- Windows Named Pipe is current-user-only and additionally rejects remote-computer and different-Windows-session clients; same-user SSH Session 0 is refused with `CLIENT_SESSION_MISMATCH`;
- pipe I/O never calls AutoCAD API; queued reads are drained from `Application.Idle`;
- two simultaneously open raw-copy P10 DWGs with the same lineage `document_pid` receive distinct `runtime_document_id` values; PID-only targeting is rejected;
- malformed JSON, invalid UTF-8, bad request IDs, oversized frames, unsupported operations, lineage mismatch and stale runtime IDs fail typed/closed and the server survives;
- eight simultaneous health clients complete against one bridge instance through serialized AutoCAD `Application.Idle` dispatch;
- read-only bridge calls leave tested drawings at `DBMOD=0`;
- public MCP contract remains 50 tools and current COM/ezdxf remains the supported migration baseline.

This closes **N3 and NB0 only** plus the document-identity/non-dirtying subset needed to start N4. It does **not** retroactively claim N4/NB1, NB4+ rollback/state-chain integration, or any native mutation capability. The three existing Autodesk product-reference `MSB3277` warning families are documented rather than suppressed; native build is 0 errors and live runtime acceptance passes.

### N4.0 measured checkpoint — LIVE PASS (2026-09-10)

N4 adds the read-only `bridge.document.snapshot` operation and live-verifies authoritative native semantic extraction on full AutoCAD 2027 in interactive Session 1. Canonical evidence: `docs/evidence/n4-native-semantic-2026-09-10.json`. The accepted scope is intentionally bounded to the active document's current space plus referenced block definitions/content, with a 32-object semantic cap; it is not a whole-DWG/all-layout extractor yet.

Measured properties:

- bound-document snapshot extraction runs through native AutoCAD Database `ForRead` access on `Application.Idle`;
- every extracted managed entity requires the persistent N2 semantic PID; missing or duplicate PIDs fail closed;
- live fixture covers LINE, CIRCLE, ARC, LWPOLYLINE, TEXT, MTEXT, INSERT, BLOCK_DEFINITION, DIMENSION and HATCH plus layer/linetype/text-style/dimension-style resources;
- referenced block definitions and their PID-bearing contents are fingerprinted recursively with a visited-definition guard; a saved block-definition geometry mutation changes the document fingerprint even when both compared snapshots have `DBMOD=0`;
- native WCS extents and representative native metrics match COM observations;
- repeated native snapshots produce the same document fingerprint and do not change `DBMOD`;
- save/close/reopen changes runtime document binding but preserves persistent document PID and semantic document fingerprint;
- native document fingerprint matches the N1 canonical Python fingerprint for the accepted snapshot;
- bridge protocol/frame/security boundary remains unchanged and `mutation_enabled=false`;
- a real 80,000-character MText proves oversized snapshot responses fail as correlated `RESPONSE_TOO_LARGE` instead of ending the pipe; the bridge remains alive and the read attempt preserves `DBMOD`;
- Linux regression is 145 PASS / 4 SKIP; Windows `.171` regression is 144 PASS / 5 SKIP; native build is 0 errors with the same three unsuppressed `MSB3277` warning families.

This closes **N4/NB1** and the native document-fingerprint integration required to make authoritative parent-state data available to N5. It does **not** enforce `expected_parent_fp` on mutation, because N4 exposes no mutation endpoint.

### N5.0 measured checkpoint — LIVE PASS (2026-09-10)

N5 adds only three fixed-schema native mutations: `entity.create.line`, `entity.update.line`, and `entity.delete.line`. Canonical evidence: `docs/evidence/n5-native-mutation-2026-09-10.json`.

Measured properties:

- every mutation requires `runtime_document_id + document_pid + expected_parent_fp`; update/delete additionally require persistent `semantic_pid` targeting;
- stale parent fingerprint is refused as `STATE_DRIFT` before the write transaction and the semantic state remains unchanged;
- create assigns a fresh PID; update preserves PID and native Handle; delete removes the target from the authoritative snapshot;
- real AutoCAD/COM measurements match native semantic LINE geometry after create/update;
- deterministic `after_apply_before_commit` fault injection proves R0 `Transaction.Abort()` for create and update; independent read-back returns the exact predecessor semantic fingerprint and allows the next mutation only after that verification;
- AutoCAD `DBMOD` is not deterministic across otherwise exact abort restoration (measured create-abort `DBMOD=1`, update-abort `DBMOD=0`), so N5 introduces document fingerprint schema v2: `saved` remains observable but is excluded from semantic `document_fp`;
- save/close/reopen preserves document lineage PID, target entity PID and semantic fingerprint for the saved updated drawing while runtime document ID changes;
- at exactly 32 semantic objects, a create that would exceed the N4 extractor bound is refused before transaction with `SNAPSHOT_CAPACITY_EXCEEDED`; entity count and parent fingerprint remain unchanged;
- arbitrary C#/AutoLISP/shell/macro/free-text command execution remains absent; `mutation_enabled=true` advertises only the exact three LINE operations;
- Linux regression is 155 PASS / 4 SKIP; Windows `.171` regression is 154 PASS / 5 SKIP; focused N5/N4/native-semantic regression is 68 PASS; native build is 0 errors with the same three unsuppressed `MSB3277` warning families.

This closes the **bounded N5/NB4 R0 transaction-abort gate for the accepted LINE surface** and proves mutation-time parent fingerprint enforcement. It does not close N6 allowed-effects/state-chain drift orchestration or N7/NB5 post-commit recovery.

## 2. Gate families

### NB0 — Bridge identity / secure loading — **PASS (N3)**

Prove:

- bridge loads into the intended AutoCAD 2027 process;
- protocol/version identity is explicit;
- local IPC binds only to the intended Windows user/session/policy;
- malformed/oversized/unauthorized requests fail closed;
- no endpoint accepts arbitrary C#, AutoLISP, shell, macro or free-text AutoCAD command execution.

### NB1 — Read-only semantic extraction — **PASS (N4)**

Prove repeated reads are stable for:

- document PID/identity;
- units/current space/extents;
- layers/linetypes/styles;
- core 2D entities;
- blocks/references;
- dimensions/hatches;
- metrics and bounding boxes;
- relations/intersections where implemented.

Read-only extraction must not dirty or mutate the drawing.

**N4 result:** PASS for the supported N4 scope. Repeated read stability, persistent PID binding, resource/entity extraction, referenced-block-definition sensitivity, COM metric parity, `DBMOD` stability, typed oversized-response failure and save/reopen fingerprint stability are recorded in the N4 canonical evidence.

### NB2 — Persistent identity / PID — **N2 STORAGE/POLICY PASS · FULL NATIVE INTEGRATION OPEN**

**Storage/policy prototype status: LIVE PASS in N2 (2026-09-10).** See `docs/N2_PID_ACCEPTANCE.md` and `docs/evidence/n2-pid-native-2026-09-10.json`. N3 reused the document-lineage carrier through bridge IPC and re-proved runtime-document disambiguation/non-dirtying for its read-only identity subset. Full entity-level native semantic extraction/fingerprint integration remains an N4/NB1/NB3 concern before overall bridge promotion.

Prove on real DWG:

- document lineage PID survives save/close/reopen;
- entity PID survives save/close/reopen;
- ordinary geometry/style edits preserve conceptual PID;
- copy intended as a new object gets a new PID;
- duplicate PID corruption is detected;
- erase/undo/redo behavior is defined and verified;
- block clone/deep-clone/WBLOCK/INSERT behavior is documented and tested;
- cross-document clone remapping cannot silently alias two conceptual instances;
- raw filesystem copies may preserve document lineage PID; the bridge must fail closed if multiple open documents share the PID and runtime target binding is ambiguous;
- mutation requests bind runtime document context + document PID + expected parent fingerprint; artifact fingerprint is used when physical-file/checkpoint identity matters.

AutoCAD `ObjectId` is not accepted as persistent identity. Native Handle may assist lookup but is not sufficient by itself.

### NB3 — Canonical fingerprints — **N1 PRIMITIVES PASS · N4 NATIVE INTEGRATION PASS · N5 DOCUMENT-FP V2 PARENT BINDING PASS · STATE-CHAIN USE OPEN**

Golden tests must prove deterministic:

- geometry fingerprint;
- style fingerprint;
- topology fingerprint;
- instance fingerprint;
- scope/document fingerprint;
- step/state-chain fingerprint.

N4 proves native document-snapshot fingerprint parity with the canonical Python engine for the accepted live fixture. N5 advances the document domain to explicitly versioned schema v2 so volatile `saved/DBMOD` cannot create false semantic drift, and live-proves v2 as the mutation parent/rollback identity. Geometry/style/instance fingerprint primitives remain owned by N1; N6 must prove delta/state-chain transitions.

Required adversarial cases:

- floating-point noise within tolerance produces the same fingerprint;
- meaningful geometry change outside tolerance changes the fingerprint;
- entity enumeration order does not change set-level fingerprints;
- different semantic PIDs can share geometry fingerprint without sharing instance fingerprint;
- forbidden duplicate geometry is detected independently of native handle.

### NB4 — Native transaction rollback — **BOUNDED PASS (N5 LINE SURFACE)**

Inject failures after each mutation stage and prove:

- deterministic validation failure before commit aborts transaction;
- predecessor semantic fingerprint is unchanged after abort;
- no leaked entity/object/style remains;
- next mutation is allowed only after predecessor state is re-read and verified.

Target status: `ROLLED_BACK_VERIFIED`.

**N5 result:** PASS for deterministic pre-commit R0 abort on the staged LINE create/update surface. The accepted proof requires independent post-abort semantic read-back and exact v2 predecessor fingerprint restoration. This does not claim R1/R2 or post-commit recovery; those remain later gates.

### NB5 — Post-commit integrity / recovery — **OPEN**

Inject mismatches after commit and prove:

- independent read-back detects mismatch;
- status becomes `COMMIT_INTEGRITY_FAIL`;
- no later mutation executes;
- R1 compensation is used only where deterministic;
- R2 immutable-checkpoint restore can return to exact predecessor state;
- rollback success requires `actual_restore_fp == expected_parent_fp`.

If restoration cannot be proven, result must remain `ROLLBACK_FAILED` / `STATE_UNCERTAIN`.

### NB6 — State drift — **OPEN**

After a verified step, manually modify the DWG before the next step.

Prove:

- current semantic state is re-read;
- current fingerprint differs from `expected_parent_fp`;
- next mutation is refused with `STATE_DRIFT`;
- no attempted mutation leaks into the document.

### NB7 — Allowed-effects enforcement — **OPEN**

For each ActionSpec, inject an unexpected sibling create/modify/delete and prove semantic delta catches it.

Examples:

- create one wall but mutate another wall;
- create one door but delete a room boundary;
- change geometry and unexpectedly alter linetype/lineweight;
- duplicate the target object.

Unexpected effects must fail validation and invoke rollback/recovery.

### NB8 — File-level integrity — **OPEN**

For SaveAs/export/native checkpoints prove:

- last accepted checkpoint is never overwritten before replacement is verified;
- staging output hash/artifact is recorded;
- failed export/save does not advance state-chain identity;
- reopen of promoted native checkpoint reproduces the expected semantic fingerprint.

### NB9 — COM parity migration — **OPEN**

For each migrated operation family, execute equivalent disposable-drawing scenarios through current COM and new .NET bridge.

Compare normalized semantic outcomes, not raw implementation details.

No operation family replaces COM in the default live path until:

- semantic parity is acceptable;
- rollback gates pass;
- PID/fingerprint gates pass;
- real AutoCAD 2027 native acceptance passes.

### NB10 — Drawing stress test — **OPEN**

Rerun a complex reference-driven drawing from a clean document using only the Semantic State Loop for step correctness.

Required evidence:

- one state-chain entry per semantic step;
- source model with Ground Truth/Derived/Inferred distinctions;
- major-gate Data-vs-Data comparisons;
- intentionally injected wrong step is caught at the exact step;
- rollback restores predecessor fingerprint;
- manual drift is detected;
- final drawing satisfies semantic, drafting-quality and user-visual acceptance.

## 3. Failure policy

Do not convert failure into skip. Do not accept command/API success as proof. Do not use screenshot similarity to close semantic gates.

Any of these blocks migration/promotion:

- `STATE_UNCERTAIN`
- `STATE_DRIFT`
- `COMMIT_INTEGRITY_FAIL`
- `ROLLBACK_FAILED`
- duplicate/unresolved PID
- non-deterministic fingerprint
- unbounded arbitrary execution surface

## 4. Promotion policy

The architecture upgrade may become the authoritative live AutoCAD path only after NB0–NB10 evidence is reviewed and an explicit promotion decision is made.

Until then:

- current COM live backend remains supported;
- public MCP contract remains unchanged;
- .NET bridge capabilities are staged/internal;
- documentation must distinguish planned, implemented, live-verified and promoted states.
