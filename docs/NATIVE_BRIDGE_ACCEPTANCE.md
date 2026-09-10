# Native Bridge + Semantic Integrity Acceptance

> Updated: 2026-09-10  
> Status: PRE-IMPLEMENTATION ACCEPTANCE PLAN  
> Target: AutoCAD 2027 full / Windows x64 / Managed .NET

## 1. Purpose

This runbook defines the acceptance gates for the upcoming C# Managed .NET native bridge and Semantic State Loop. It does not replace the existing COM live-acceptance lane; the two run side-by-side during migration.

The upgrade is not accepted unless both core pillars are proven under fault injection:

1. **Data Integrity / Rollback**
2. **Precise Identity / PID + Fingerprinting**

## 2. Gate families

### NB0 — Bridge identity / secure loading

Prove:

- bridge loads into the intended AutoCAD 2027 process;
- protocol/version identity is explicit;
- local IPC binds only to the intended Windows user/session/policy;
- malformed/oversized/unauthorized requests fail closed;
- no endpoint accepts arbitrary C#, AutoLISP, shell, macro or free-text AutoCAD command execution.

### NB1 — Read-only semantic extraction

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

### NB2 — Persistent identity / PID

Prove on real DWG:

- document PID survives save/close/reopen;
- entity PID survives save/close/reopen;
- ordinary geometry/style edits preserve conceptual PID;
- copy intended as a new object gets a new PID;
- duplicate PID corruption is detected;
- erase/undo/redo behavior is defined and verified;
- block clone/deep-clone/WBLOCK/INSERT behavior is documented and tested;
- cross-document clone remapping cannot silently alias two conceptual instances.

AutoCAD `ObjectId` is not accepted as persistent identity. Native Handle may assist lookup but is not sufficient by itself.

### NB3 — Canonical fingerprints

Golden tests must prove deterministic:

- geometry fingerprint;
- style fingerprint;
- topology fingerprint;
- instance fingerprint;
- scope/document fingerprint;
- step/state-chain fingerprint.

Required adversarial cases:

- floating-point noise within tolerance produces the same fingerprint;
- meaningful geometry change outside tolerance changes the fingerprint;
- entity enumeration order does not change set-level fingerprints;
- different semantic PIDs can share geometry fingerprint without sharing instance fingerprint;
- forbidden duplicate geometry is detected independently of native handle.

### NB4 — Native transaction rollback

Inject failures after each mutation stage and prove:

- deterministic validation failure before commit aborts transaction;
- predecessor semantic fingerprint is unchanged after abort;
- no leaked entity/object/style remains;
- next mutation is allowed only after predecessor state is re-read and verified.

Target status: `ROLLED_BACK_VERIFIED`.

### NB5 — Post-commit integrity / recovery

Inject mismatches after commit and prove:

- independent read-back detects mismatch;
- status becomes `COMMIT_INTEGRITY_FAIL`;
- no later mutation executes;
- R1 compensation is used only where deterministic;
- R2 immutable-checkpoint restore can return to exact predecessor state;
- rollback success requires `actual_restore_fp == expected_parent_fp`.

If restoration cannot be proven, result must remain `ROLLBACK_FAILED` / `STATE_UNCERTAIN`.

### NB6 — State drift

After a verified step, manually modify the DWG before the next step.

Prove:

- current semantic state is re-read;
- current fingerprint differs from `expected_parent_fp`;
- next mutation is refused with `STATE_DRIFT`;
- no attempted mutation leaks into the document.

### NB7 — Allowed-effects enforcement

For each ActionSpec, inject an unexpected sibling create/modify/delete and prove semantic delta catches it.

Examples:

- create one wall but mutate another wall;
- create one door but delete a room boundary;
- change geometry and unexpectedly alter linetype/lineweight;
- duplicate the target object.

Unexpected effects must fail validation and invoke rollback/recovery.

### NB8 — File-level integrity

For SaveAs/export/native checkpoints prove:

- last accepted checkpoint is never overwritten before replacement is verified;
- staging output hash/artifact is recorded;
- failed export/save does not advance state-chain identity;
- reopen of promoted native checkpoint reproduces the expected semantic fingerprint.

### NB9 — COM parity migration

For each migrated operation family, execute equivalent disposable-drawing scenarios through current COM and new .NET bridge.

Compare normalized semantic outcomes, not raw implementation details.

No operation family replaces COM in the default live path until:

- semantic parity is acceptable;
- rollback gates pass;
- PID/fingerprint gates pass;
- real AutoCAD 2027 native acceptance passes.

### NB10 — Drawing stress test

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
