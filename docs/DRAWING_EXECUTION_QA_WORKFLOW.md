# Drawing Execution & QA Workflow

> Updated: 2026-09-10 11:56 +07:00
> Scope: all CDT-AutoCAD user-facing drawing/reconstruction workflows
> Status: Project execution invariant; N3 transport/identity exists, full native Semantic State enforcement pending N4+

## 1. Purpose

This document defines HOW a drawing is built and verified. `DRAWING_QUALITY_ACCEPTANCE.md` defines the quality bar; `SEMANTIC_STATE_PROTOCOL.md` defines the authoritative state model.

> **Build small -> extract native semantic state -> fingerprint/diff -> validate -> commit or verified rollback -> log -> continue only from a known-good state.**

A long uninterrupted drawing script is non-compliant. A screenshot-driven correctness loop is also non-compliant when native structured state is available.

## 2. Two mandatory pillars

Every workflow must preserve:

1. **Data Integrity / Rollback** — a failed or uncertain mutation cannot advance. The system must prove either the expected post-state or restoration of the predecessor fingerprint.
2. **Precise Identity / PID + Fingerprinting** — every managed document/object/state is deterministically identifiable; runtime handles alone are not sufficient proof.

## 3. Mandatory Semantic State Loop

Each small semantic step follows:

```text
VERIFY CURRENT PARENT STATE
  -> DEFINE ActionSpec + expected effects + validation rules
  -> EXECUTE ONE SMALL NATIVE MUTATION
  -> EXTRACT PROVISIONAL SEMANTIC STATE
  -> DETERMINISTIC VALIDATION
       FAIL -> ABORT / ROLLBACK -> READ BACK -> VERIFY PARENT FP
       PASS -> COMMIT
  -> INDEPENDENT POST-COMMIT READ-BACK
  -> CANONICALIZE + PID/FINGERPRINT + SEMANTIC DIFF
  -> VERIFY PERSISTED STATE
  -> APPEND STATE-CHAIN / EXECUTION LOG
  -> PASS ? NEXT STEP : STOP
```

The next step must include and verify the previous step's `post_state_fp` as `expected_parent_fp`.

## 4. Atomic step definition

One step changes one coherent semantic unit only. Examples:

- units/styles/layers;
- one exterior-envelope segment group;
- one partition/room boundary group;
- one opening group;
- one semantic linetype system;
- one fixture/furniture family;
- one annotation/dimension group;
- one hatch/material/landscape zone;
- one road/path segment family;
- one utility/system family.

Do not combine unrelated walls, furniture, labels, dimensions and plotting changes into one mutation step.

## 5. Expected-state-first rule

Before mutation, define:

- predecessor state fingerprint;
- target semantic PID(s);
- requested geometry/style/topology change;
- allowed create/modify/delete effects;
- deterministic validation rules;
- tolerances;
- rollback/recovery expectation.

The AI may decide design intent and which invariants matter, but deterministic measurements and object checks must be performed by code/native data whenever possible.

## 6. Semantic extraction after every step

Do not ask Vision whether geometry is correct when the CAD API can report it.

Extract as applicable:

- document PID, units, space and saved state;
- entity semantic PIDs and native handles;
- class/type;
- coordinates/vertices/bulges;
- bounding boxes;
- length/area/volume/radius/centroid;
- layer/linetype/lineweight/style;
- block/hierarchy/ownership;
- intersections;
- adjacency/connectivity/containment;
- created/modified/deleted delta;
- duplicate PID/geometry conditions.

For target .NET execution, native database events may assist delta capture, but post-action extraction remains the authority.

## 7. PID / Fingerprint verification after every step

At minimum validate:

- `expected_parent_fp == current_parent_fp` before mutation;
- semantic PID uniqueness;
- geometry/content fingerprint of the affected scope;
- expected create/modify/delete set;
- no unexpected sibling mutations;
- post-state fingerprint after independent read-back.

If any current state differs from the expected parent state before mutation, return `STATE_DRIFT` and do not execute.

## 8. Rollback after every failed/uncertain mutation

Preferred recovery hierarchy:

- `R0_ABORT` — abort native transaction before commit;
- `R1_COMPENSATE` — verified inverse/undo only when deterministic and safe;
- `R2_CHECKPOINT_RESTORE` — restore previous immutable accepted native checkpoint.

A rollback is accepted only when read-back proves:

```text
actual_restored_state_fp == expected_parent_state_fp
```

If not, state is `ROLLBACK_FAILED`/`STATE_UNCERTAIN` and all later mutations stop.

Blind retry is forbidden after timeout/uncertain completion.

## 9. Checkpoint policy

Semantic state-chain entries are mandatory after each successful step.

Native DWG checkpoints are required at major gates and before/after risky or non-invertible file-level operations. They may also be produced per small step during stress-test/debug lanes.

Recommended job layout:

```text
<job>/
  source-model.json
  execution-log.jsonl
  state-chain.jsonl
  steps/
    s01-action.json
    s01-pre-state.json
    s01-post-state.json
    s01-validation.json
    s01-rollback.json       # only when needed
  gates/
    g01-state.json
    g01-comparison.json
    g01-checkpoint.dwg
  evidence/
    final.png
```

Do not overwrite the last accepted recovery checkpoint before the new state is verified.

## 10. Append-only logs

Every attempt, including failed attempts, remains in the log.

Each step records:

- action/step ID and timestamps;
- expected parent fingerprint;
- document/entity PIDs;
- ActionSpec hash;
- mutation receipt;
- provisional validation result;
- semantic delta;
- post-commit fingerprint;
- rollback receipt if any;
- status;
- failure detail;
- native artifact hashes where relevant.

A corrected retry is a new attempt; never rewrite failure history.

## 11. Major-gate comparison

Small-step validation catches local defects. Major gates detect cumulative drift in layout, topology and domain logic.

At a major gate compare **Data vs Data**:

1. current `SemanticSnapshot`;
2. `SourceSemanticModel` / governing brief;
3. previous accepted major-gate semantic state;
4. declared `GROUND_TRUTH`, `DERIVED`, `INFERRED` constraints.

Compare as applicable:

- dimensions/extents/proportions;
- adjacency/topology/connectivity;
- axes/alignment;
- opening count/location/orientation;
- required line/layer/style semantics;
- missing/extra semantic features;
- duplicate/gap/self-intersection conditions;
- hierarchy/ownership;
- allowed uncertainty.

A failed semantic major gate cannot be overridden by a good-looking screenshot.

## 12. Drawing-class major gates

### Architecture floor plan / source reproduction

- `G0 SOURCE_AUDIT`
- `G1 PRIMARY_GEOMETRY`
- `G2 OPENINGS_CIRCULATION`
- `G3 TECHNICAL_LINEWORK`
- `G4 CONTENT`
- `G5 ANNOTATION`
- `G6 FINAL_SEMANTIC`
- `G7 FINAL_VISUAL_USER`

### Site / park / plaza / landscape / master plan

- `G0 SOURCE_AUDIT`
- `G1 BOUNDARY_ORIENTATION`
- `G2 PRIMARY_CIRCULATION_AXES`
- `G3 FUNCTIONAL_ZONES_GEOMETRY`
- `G4 TECHNICAL_LINEWORK_INFRASTRUCTURE`
- `G5 LANDSCAPE_FURNITURE_FEATURES`
- `G6 ANNOTATION_DIMENSIONS_LEGEND`
- `G7 FINAL_SEMANTIC`
- `G8 FINAL_VISUAL_USER`

### Elevation / section / detail

- `G0 SOURCE_AUDIT`
- `G1 PRIMARY_DATUMS_OUTLINE`
- `G2 OPENINGS_COMPONENTS`
- `G3 CUT_PROJECTION_HIDDEN_LINEWORK`
- `G4 MATERIAL_DETAIL`
- `G5 ANNOTATION_DIMENSIONS`
- `G6 FINAL_SEMANTIC`
- `G7 FINAL_VISUAL_USER`

## 13. Source audit before drawing

Before geometry creation, identify source truth and conflicts.

Conflict policy:

1. explicit readable dimension/specification = `GROUND_TRUTH` unless a governing brief says otherwise;
2. exact mathematics from ground truth = `DERIVED`;
3. raster proportion/domain judgment for missing information = `INFERRED`;
4. material conflicts must be logged and resolved by an explicit governing rule before dependent geometry is created;
5. inferred compromise is never presented as exact source truth.

If source is raster-only, Vision may be used here to create the structured SourceSemanticModel.

## 14. Vision / screenshot boundary

Per-step correctness is **not** based on screenshot interpretation.

Vision is reserved for:

- raster source ingestion;
- aesthetic/presentation review when semantics cannot fully represent visual quality;
- final user-facing evidence.

Screenshots may be captured at major visual gates or debugging points, but they are supplemental evidence. They do not replace native semantic state.

## 15. Current migration rule

The N3 Managed .NET bridge is implemented and live-verified, but it is intentionally read-only and currently exposes only health/document identity. N4 full native semantic extraction and N5+ native mutation/rollback integration do not exist yet. During this migration phase:

- keep COM/ezdxf as the supported drawing/runtime baseline;
- use existing structured query/measurement APIs as much as possible;
- use the N3 bridge only for its verified internal read-only identity/transport scope;
- do not claim that current COM/native-identity checks equal the full target Semantic State Loop;
- a drawing step cannot be labeled full native `COMMITTED_VERIFIED`/`ROLLED_BACK_VERIFIED` until the required N4+ snapshot/fingerprint/rollback path exists;
- COM timeout remains integrity-uncertain and requires read-back before retry;
- old screenshot-heavy stress-test scripts are prototypes, not the target execution architecture.

## 16. Target native step runner

N3 provides the transport/document-binding foundation only. The N4+ runner must execute one ActionSpec at a time and return structured evidence, conceptually:

```text
semantic_step(action_spec)
  -> parent_guard
  -> native_transaction
  -> provisional_snapshot
  -> validation
  -> commit/abort
  -> post_commit_snapshot
  -> fingerprint/diff
  -> state_chain_entry
```

It must never automatically execute the next action after a failed or unresolved step.

## 17. Final acceptance handoff

A completed engineering drawing requires both:

- semantic correctness/integrity gates; and
- the visual/domain quality gates in `DRAWING_QUALITY_ACCEPTANCE.md`.

For user-reviewed work, final status remains `PENDING_USER_VISUAL_ACCEPTANCE` until actual evidence is accessible and the reviewer approves it.

Semantic PASS cannot be replaced by visual approval, and visual quality cannot be replaced by semantic correctness; both are required for final user acceptance.
