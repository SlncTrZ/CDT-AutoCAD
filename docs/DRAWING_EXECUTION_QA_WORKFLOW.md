# Drawing Execution & QA Workflow

> Updated: 2026-09-10 08:17 +07:00  
> Scope: all CDT-AutoCAD user-facing drawing/reconstruction workflows  
> Status: Project execution invariant

## 1. Purpose

This workflow defines HOW a drawing must be built and verified. `docs/DRAWING_QUALITY_ACCEPTANCE.md` defines WHAT quality is required; this document defines the mandatory execution loop that prevents late discovery of errors.

> **Build small -> verify -> log -> compare at major gates -> continue only from a known-good checkpoint.**

A long uninterrupted drawing script that creates most of a drawing before any visual verification is non-compliant for user-observed/reference-driven work.

## 2. Core execution invariant

Every meaningful drawing operation shall be decomposed into small semantic steps. Each step must complete this loop before the next dependent step starts:

```text
PLAN STEP
  -> APPLY SMALL CHANGE
  -> SAVE NEW STEP CHECKPOINT
  -> MACHINE VERIFY
  -> CAPTURE SCREENSHOT
  -> VISUAL VERIFY
  -> APPEND STEP LOG
  -> PASS ? CONTINUE : STOP/ROLL BACK
```

At defined major gates, add a source/brief comparison before continuing:

```text
LAST SMALL-STEP PASS
  -> MAJOR-GATE COMPARISON AGAINST SOURCE / PREVIOUS ACCEPTED STATE
  -> COMPARISON LOG
  -> PASS ? CONTINUE : STOP/ROLL BACK
```

No later drawing work may be used to hide, compensate for, or obscure an unresolved earlier defect.

## 3. Atomic step definition

A small step should change one coherent semantic unit only. Examples:

- create drawing units/styles/layers;
- create exterior envelope;
- add one façade/opening group;
- add one partition group;
- add one room/zone;
- add one door group;
- add one window group;
- add one linetype/hidden/centerline system;
- add one fixture/furniture family;
- add one annotation/dimension group;
- add one hatch/material/landscape zone;
- add one road/path segment family;
- add one utility/system layer.

Avoid combining unrelated wall geometry, furniture, labels, dimensions, and plotting changes into one step.

## 4. Checkpoint immutability

For traceability, each successful small step produces a new native checkpoint rather than repeatedly overwriting the only copy.

Recommended naming:

```text
<job>/steps/
  s00-preflight.dwg
  s00-preflight.png
  s00-preflight.json
  s01-envelope.dwg
  s01-envelope.png
  s01-envelope.json
  s02-openings.dwg
  s02-openings.png
  s02-openings.json
  ...
```

The previous accepted DWG remains untouched. If step `s07` fails, resume from accepted `s06`, not from a partially mutated `s07` document.

A final user-facing DWG may be copied/saved from the last accepted step only after all internal gates pass.

## 5. Append-only execution log

Every job keeps an append-only event log, for example:

```text
<job>/execution-log.jsonl
```

Each step event records at minimum:

- `step_id` and semantic name;
- start/end timestamp;
- input checkpoint;
- output DWG/screenshot/report paths;
- requested operation summary;
- expected invariants;
- actual object/layer/style counts where useful;
- units and relevant display variables;
- machine-check results;
- visual-check result;
- source-comparison result when applicable;
- assumptions added/changed (`GROUND_TRUTH`, `DERIVED`, `INFERRED`);
- status: `PASS`, `FAIL`, or `BLOCKED`;
- failure/exception detail and rollback checkpoint when failed.

Never rewrite history to make a failed step disappear. A corrected retry gets a new attempt record.

## 6. Machine verification after every small step

The exact checks depend on the step, but each small step must verify the invariants it could break.

Typical checks:

- AutoCAD document still attached and responsive;
- expected object count delta;
- expected entity types/layers exist;
- geometry has non-zero length/area where applicable;
- expected boundaries are closed;
- expected endpoints/intersections/offsets match tolerances;
- no duplicate or zero-length entity introduced;
- correct units;
- correct linetype/lineweight assignment;
- relevant `LTSCALE`, `CELTSCALE`, `MSLTSCALE`, `PSLTSCALE`, `LWDISPLAY` state;
- saved clean after final state change for the step.

A check must be tied to the semantic intent of the step. Object count alone is never sufficient.

## 7. Visual verification after every small step

Every small step must produce a screenshot of the actual AutoCAD state after save/regen/zoom as appropriate.

Visual verification asks only the questions relevant to the current step, for example:

- did the new wall/road/path appear in the intended location;
- are openings actually cut rather than symbols drawn on top of continuous walls;
- are curves/junctions clean;
- are door swings/orientations plausible;
- is a hidden/center/overhead line visibly non-continuous at review scale;
- did a new fixture/annotation overlap existing geometry;
- did the step damage an already accepted region.

If visual verification fails, stop. Do not proceed hoping later steps will improve it.

## 8. Major-gate comparison

Small-step checks catch local defects. Major gates catch cumulative layout, proportion, topology, and composition drift.

At each major gate, compare:

1. current screenshot;
2. source/reference or governing brief;
3. previous major-gate screenshot;
4. declared ground-truth/derived/inferred data.

Record a comparison result covering:

- overall silhouette/envelope;
- relative proportions;
- adjacency/topology;
- major axes/alignment;
- missing/extra major features;
- opening orientation/location;
- semantic linetypes/lineweights;
- visual density/readability;
- source conflicts or uncertainties discovered.

Major-gate status is fail-closed. A local machine pass cannot override a failed source comparison.

## 9. Recommended major gates by drawing class

### 9.1 Architecture floor plan / reference reproduction

- `G0 SOURCE_AUDIT` — classify source, dimensions, contradictions, uncertainties.
- `G1 PRIMARY_GEOMETRY` — outer envelope + major room/zone divisions.
- `G2 OPENINGS_CIRCULATION` — doors, windows, passages, stairs, circulation.
- `G3 TECHNICAL_LINEWORK` — hidden/overhead/center/cutting/reference conventions and lineweight hierarchy.
- `G4 CONTENT` — fixtures, cabinetry, furniture, sanitary/laundry/garage content.
- `G5 ANNOTATION` — labels, dimensions, symbols, notes.
- `G6 FINAL_VISUAL` — complete source comparison, clean saved state, evidence delivery.

### 9.2 Site / park / plaza / landscape / master plan

- `G0 SOURCE_AUDIT`
- `G1 BOUNDARY_ORIENTATION`
- `G2 PRIMARY_CIRCULATION_AXES`
- `G3 FUNCTIONAL_ZONES_GEOMETRY`
- `G4 TECHNICAL_LINEWORK_INFRASTRUCTURE`
- `G5 LANDSCAPE_FURNITURE_FEATURES`
- `G6 ANNOTATION_DIMENSIONS_LEGEND`
- `G7 FINAL_VISUAL`

### 9.3 Elevation / section / detail

- `G0 SOURCE_AUDIT`
- `G1 PRIMARY_DATUMS_OUTLINE`
- `G2 OPENINGS_COMPONENTS`
- `G3 CUT_PROJECTION_HIDDEN_LINEWORK`
- `G4 MATERIAL_DETAIL`
- `G5 ANNOTATION_DIMENSIONS`
- `G6 FINAL_VISUAL`

## 10. Source audit before drawing

Before any geometry is created, explicitly inspect the source for internal conflicts.

Examples:

- written room dimensions disagree with raster proportions;
- two dimensions cannot both be satisfied with a shared wall;
- plan/elevation disagree;
- a door swing or fixture conflicts with circulation;
- an overall dimension chain is absent;
- labels are unreadable or ambiguous.

Conflict policy:

1. explicit readable dimensions/specification = `GROUND_TRUTH` unless the brief says otherwise;
2. exact values mathematically implied by ground truth = `DERIVED`;
3. raster/visual proportion used to place undimensioned geometry = `INFERRED`;
4. when source facts conflict materially, log the conflict and select a governing rule before drawing;
5. never claim that an inferred compromise is exact source truth.

## 11. Step-runner contract

For automated/live execution, prefer a step runner that accepts exactly one step per invocation, e.g.:

```text
python house_stepper.py --step s03
```

The runner shall:

- load only the last accepted checkpoint required by that step;
- verify its expected predecessor identity/state;
- apply only the requested step;
- save to a new step DWG;
- capture screenshot;
- emit a step report;
- append log event;
- exit non-zero on any failed invariant;
- never automatically execute the next step.

The controlling agent reviews the result and explicitly launches the next step only after acceptance of the current one.

## 12. Rollback and retry

When a step fails:

1. record failure and traceback/error evidence;
2. preserve the failed screenshot/report when useful;
3. do not mutate the previous accepted DWG;
4. diagnose from the failed step boundary;
5. fix the step implementation;
6. rerun from the previous accepted checkpoint;
7. record retry as a new attempt.

Do not blindly retry COM mutations after timeout/uncertain completion. Inspect live AutoCAD state first because the mutation may have completed after the caller timed out.

## 13. User-observed pacing

When the reviewer is watching AutoCAD, visible operations should normally remain approximately `0.5–0.8 s` apart unless the user requests another pace. Expensive native operations may take longer.

Pacing does not replace checkpointing. A slowly executed monolithic script is still non-compliant.

## 14. Final acceptance handoff

The final drawing remains:

`PENDING_USER_VISUAL_ACCEPTANCE`

until all of the following are true:

- all small steps have PASS logs;
- all major-gate comparisons pass internally;
- final native DWG is saved clean;
- final screenshot/file evidence is actually accessible to the reviewer;
- reviewer approves the result.

Only then may the checkpoint be marked `USER_ACCEPTED`.
