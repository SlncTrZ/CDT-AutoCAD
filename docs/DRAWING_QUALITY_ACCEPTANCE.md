# Drawing Quality Acceptance Standard

> Updated: 2026-09-10 11:56 +07:00
> Scope: CDT-AutoCAD reference-driven drafting, architectural/site/landscape/technical drawings
> Status: Project acceptance invariant · full native semantic enforcement pending N4+

## 1. Purpose

This document defines the minimum quality bar for any drawing generated, reconstructed, edited, or stress-tested through CDT-AutoCAD.

A drawing is not accepted merely because AutoCAD created valid entities, the provider returned success, tests passed, or the DWG saved successfully. The drawing must also be geometrically correct, domain-correct, graphically coherent, visually clean, and reviewable against its source or brief.

> **A technically valid drawing may still be an unacceptable drawing.**

### 1.1 Technical-standard baseline

Unless a project, client, national, or office drafting standard explicitly overrides them, use these international references as the baseline for technical representation:

- `ISO 128-2:2022` — Technical product documentation (TPD), general principles of representation, Part 2: basic conventions for lines; baseline reference for line types, line configurations, line drafting, leaders, and reference lines.
- `ISO 128-3:2022` — Technical product documentation (TPD), general principles of representation, Part 3: views, sections, and cuts; applies across technical drawings including architectural and civil-engineering drawings.
- `ISO 129-1:2018` + `ISO 129-1:2018/Amd 1:2020` — current published baseline for presentation of dimensions and associated tolerances. A new edition is under development and must not be treated as published until it replaces the current standard.

Project-specific conventions may be stricter or discipline-specific. When an explicit project/client/national standard conflicts with this baseline, record the override and follow the governing project standard rather than silently mixing conventions.

## 2. Acceptance states

Every drawing checkpoint moves through the following states in order:

1. `TECHNICAL_PASS` — provider/runtime/COM operations completed without integrity errors; expected entities/layers/styles exist; save succeeds.
2. `GEOMETRY_PASS` — geometry, joins, closures, offsets, dimensions, intersections, and object relationships are correct.
3. `DOMAIN_PASS` — the drawing obeys the conventions and functional logic of its drawing type.
4. `VISUAL_PASS` — hierarchy, linetypes, lineweights, annotation, spacing, composition, and plotted/displayed appearance are professionally readable.
5. `USER_ACCEPTED` — the reviewer can inspect the actual evidence and approves the result.

Only `USER_ACCEPTED` is a completed checkpoint. Any missing stage leaves the checkpoint `PENDING` or `FAIL`.

### 2.1 Semantic integrity prerequisite

Before any drawing-quality stage can advance, the underlying engineering step must satisfy the Semantic State Protocol. The two mandatory prerequisites are:

- **Data Integrity / Rollback:** current state is either `COMMITTED_VERIFIED` or a failed attempt has returned to `ROLLED_BACK_VERIFIED`; uncertain/failed rollback blocks acceptance.
- **Precise Identity / PID + Fingerprinting:** affected objects/state are identified by managed PID/content fingerprints, predecessor drift is checked, and unexpected/duplicate mutations are rejected.

A visually correct screenshot cannot override failed semantic integrity.

## 3. Core invariants for all drawing types

### 3.1 Source fidelity

When a reference image, PDF, survey, sketch, source DWG, specification, or explicit dimensions are provided:

- dimensions directly readable from the source are `GROUND_TRUTH`;
- values mathematically derived from ground-truth data are `DERIVED`;
- values estimated from visual proportion or professional judgment are `INFERRED`;
- inferred values must never be presented as measured truth;
- room/zone adjacency, orientation, axes, openings, boundaries, and visible geometry must follow the source unless the brief explicitly authorizes redesign;
- if the source is incomplete or ambiguous, preserve uncertainty in notes/evidence instead of inventing precision.

For source-reproduction tasks, fidelity outranks creative improvement unless redesign is explicitly requested.

### 3.2 Geometric correctness

The drawing must be topologically and geometrically sound:

- lines that should meet must meet;
- boundaries that should close must close;
- no accidental gaps, duplicate entities, overlaps, dangling stubs, or zero-length objects;
- parallel, perpendicular, concentric, tangent, symmetric, and aligned relationships must be intentional and verifiable where required;
- offsets and thicknesses must be consistent within the applicable drawing system;
- curves must be smooth and purposeful, especially in roads, paths, landscape edges, plazas, ramps, and site circulation;
- dimensions must measure the intended geometry, not a nearby approximation;
- geometry shall be created in a consistent coordinate/unit system.

### 3.3 Domain logic

A drawing must function correctly for its discipline, not merely resemble the source superficially.

Examples:

- architectural plans require coherent circulation, rooms, walls, openings, and fixture logic;
- site plans require readable boundaries, orientation, access, building/landscape relationships, and site circulation;
- park plans require connected entrances/paths, usable zones, landscape organization, and hierarchy of movement;
- plaza plans require intentional axes, paving/zone structure, access, furniture/features, and pedestrian logic;
- parking plans require plausible stall/aisle geometry, entry/exit, turning logic, accessible spaces where specified, and safe pedestrian paths;
- road/access plans require consistent centerline/edge geometry, radii, intersection logic, islands, sidewalks, and access points where applicable;
- sections/elevations/details require scale-appropriate conventions and consistency with referenced plans.

### 3.4 Graphic hierarchy

Every drawing shall communicate importance through graphics rather than treating all objects identically.

At minimum, the drawing must distinguish:

- primary/cut geometry;
- visible object geometry;
- hidden/overhead/underground geometry;
- secondary/furniture/fixture/landscape geometry;
- center/reference/axis geometry;
- dimensions, extension lines, leaders, and annotations;
- boundaries, zones, hatches, and material/paving/planting regions when applicable.

### 3.5 Composition and readability

- labels must be placed intentionally and avoid collisions;
- dimensions must not obscure primary geometry;
- repeated symbols must be aligned and spaced consistently;
- visual density must be controlled; empty space is preferable to clutter;
- the drawing should remain readable at its intended display/plot scale;
- titles, notes, symbols, dimensions, and geometry must not appear as debug artifacts or unformatted API output.

### 3.6 Traceable certainty

Evidence/reporting for a checkpoint must identify meaningful assumptions using:

- `GROUND_TRUTH`
- `DERIVED`
- `INFERRED`

If uncertainty materially affects acceptance, it must be surfaced before the checkpoint is called complete.

### 3.7 Evidence-based acceptance

Object counts, native handles, extents, successful API responses, and successful saves are technical evidence only. Geometry/domain correctness must be proven from the Semantic State Loop; visual inspection remains an additional presentation/user-acceptance gate.

Each substantial checkpoint must preserve enough evidence to verify:

- native DWG/recovery state;
- document/entity PID identity where managed by the semantic system;
- pre/post SemanticSnapshot and semantic delta;
- geometry/measurements/style/topology fingerprints where relevant;
- deterministic validation and rollback receipt when applicable;
- source/reference semantic comparison when a reference exists;
- visual appearance in AutoCAD for presentation quality;
- final saved state after the final view/style/annotation changes.

## 4. Drawing-type profiles

Before drawing, classify the task into one or more profiles. A drawing may use multiple profiles.

### 4.1 `ARCHITECTURE_FLOOR_PLAN`

Required checks include:

- exterior/interior wall hierarchy and thickness;
- clean wall junctions;
- door openings, leaves, and swing directions;
- window locations and proportions;
- room adjacency and circulation;
- stairs, sanitary fixtures, cabinetry, furniture, equipment, and built-ins as applicable;
- overhead elements above the cut plane shown using the proper non-continuous convention;
- room names, dimensions, levels, grids, section/elevation markers where required;
- consistent annotation and lineweight hierarchy.

### 4.2 `SITE_PLAN`

Required checks include:

- property/site boundary;
- building footprints and orientation;
- setbacks/easements when specified;
- roads, entries, drives, pedestrian routes, hardscape, softscape, and landscape zones;
- north/orientation information when relevant;
- existing/proposed distinction where applicable;
- utility or underground information where applicable;
- readable relationship between buildings, open space, access, and site edges.

### 4.3 `PARK_PLAN`

Required checks include:

- entrances connect into a continuous circulation network;
- primary/secondary paths have a visible hierarchy;
- path geometry is smooth and buildable in character;
- lawns, planting, play/recreation, water, plaza, seating, shelter, service, or other functional zones are legible;
- trees, planting masses, furniture, lighting, water features, and structures are placed intentionally;
- hardscape and softscape are graphically distinct;
- hidden/underground/site-control elements use appropriate linetypes;
- no isolated route or unusable residual space is created without design intent.

### 4.4 `PLAZA_PLAN`

Required checks include:

- main axes and geometric order are intentional;
- paving/material zones are coherent;
- entrances and pedestrian flow are clear;
- fountains, monuments, stages, planting, seating, lighting, bollards, and street furniture are aligned with the spatial concept;
- level changes, steps, ramps, edges, and drainage intent are represented when applicable;
- graphic hierarchy separates major geometry, paving, furniture, planting, and annotation.

### 4.5 `LANDSCAPE_PLAN`

Required checks include:

- tree/planting symbols use consistent conventions;
- planting masses, lawn, hardscape, edging, water, furniture, and material zones are distinct;
- major/minor landscape elements have visual hierarchy;
- existing/proposed/removal states are differentiated when applicable;
- canopy, root-zone, or other non-visible/extents information uses the correct non-continuous representation when required.

### 4.6 `MASTER_PLAN` / `URBAN_PLAN`

Required checks include:

- road/block/parcel hierarchy;
- building footprint or massing organization;
- public/open space structure;
- circulation/access relationships;
- major landscape/water/infrastructure features;
- orientation, zones, legends, and annotation sufficient to understand the plan at its intended scale.

### 4.7 `PARKING_PLAN`

Required checks include:

- stall dimensions and alignment;
- drive-aisle dimensions and circulation;
- turning/connectivity logic;
- entry/exit control;
- accessible spaces/routes when part of the brief;
- pedestrian routes and islands;
- wheel stops, curbs, arrows, striping, signage, or relevant conventions where required;
- line types/weights distinguish boundaries, striping, centerlines, hidden/underground features, and annotation.

### 4.8 `ROAD_ACCESS_PLAN`

Required checks include:

- centerline geometry;
- carriageway/edge/curb/sidewalk relationships;
- radii and intersection geometry;
- medians/islands/turning geometry;
- access points and tie-ins;
- centerlines, hidden/underground utilities, boundaries, and existing/proposed elements use proper linetypes.

### 4.9 `ELEVATION`

Required checks include:

- level and baseline consistency;
- opening alignment/proportion;
- roof, façade, material, and projection hierarchy;
- visible vs hidden/overhead conditions where applicable;
- lineweight hierarchy between silhouette, foreground, secondary/detail, and annotation.

### 4.10 `SECTION`

Required checks include:

- cut geometry is clearly heavier than projected geometry;
- cut plane is consistent with referenced plan where available;
- levels, floor/roof/ground relationships, structural/architectural layers, and key dimensions are coherent;
- beyond-cut and hidden elements use appropriate conventions;
- section hatching/material indication is readable at scale.

### 4.11 `DETAIL`

Required checks include:

- geometry is appropriate to the detail scale;
- materials/layers/joints/interfaces are explicit;
- dimensions, leaders, notes, hatches, and break lines are properly used;
- lineweights communicate cut, visible, hidden, hatch, and annotation hierarchy.

### 4.12 `REFERENCE_REPRODUCTION`

Required checks include:

- geometry and annotation fidelity to the source;
- no unrequested redesign;
- visible source dimensions retained as ground truth;
- uncertain or unreadable information remains explicitly uncertain;
- visual comparison to the source is mandatory before acceptance.

## 5. Linework and linetype standard

### 5.1 Principle

Linetype is semantic information. Using `Continuous` for every object is a drawing defect.

> **Missing a semantically required linetype is a drawing defect. An entity can be geometrically correct and still be drafted incorrectly because its linetype is wrong.**

The exact linetype naming may follow AutoCAD/office conventions, but the visual/semantic role must be preserved.

### 5.2 Required technical line roles

| Line role | Typical technical use |
| --- | --- |
| Continuous thick | Cut edges, principal section outlines, primary walls/major boundaries |
| Continuous medium | Visible object outlines, building edges, curbs, principal hardscape |
| Continuous thin | Secondary geometry, furniture, fixtures, dimensions, extension lines, leaders, projection lines |
| Dashed / Hidden | Edges/objects obscured behind or below visible geometry |
| Dashed overhead | Elements above the plan cut plane: roof/canopy/cabinet/beam/overhead feature where relevant |
| Center / chain thin | Axes, road/grid/column centerlines, circle centers, symmetry lines |
| Cutting-plane / chain thick | Section/cutting-plane indicators and viewing direction |
| Phantom / long-short-short | Alternate positions, range of motion, adjacent/repeated positions where the discipline uses this convention |
| Break line | Intentional shortening/omission of a long object or local break |
| Hatch / section thin | Material cut, section fill, paving/material/zone pattern as appropriate |
| Property/boundary line | Site/property/project limit with a distinct readable convention |
| Setback/easement line | Setbacks, easements, protection/corridor zones using an office/discipline-specific dash/chain convention |
| Underground utility line | Buried services distinguished from visible/above-ground systems |
| Existing/proposed/removal | Existing, proposed, demolition/removal must not be graphically indistinguishable when those states coexist |
| Contour major/minor | Major and minor topographic contours use distinct hierarchy |
| Demolition line | Demolition/removed work uses the relevant renovation convention |

Not every role must appear in every drawing. Every role that is semantically required by the represented conditions must appear and must be used correctly.

### 5.3 Linetype-scale integrity

A correct linetype assignment that displays or plots as effectively continuous because of poor scale configuration still fails visual acceptance.

Check, as applicable:

- `LTSCALE`
- `CELTSCALE`
- `MSLTSCALE`
- `PSLTSCALE`
- viewport/annotation/plot scale interaction

Dashed/center/phantom patterns must be visibly legible at the intended review and plot scale.

## 6. Lineweight standard

Use an intentional hierarchy based on recognized technical-drafting series. A common baseline subset is:

`0.13 / 0.18 / 0.25 / 0.35 / 0.50 / 0.70 mm`

The discipline/profile may use a different subset, but all entities must not collapse to one visual weight.

Typical hierarchy:

- strongest: principal cut edges / major section elements;
- strong: major outlines / site or building boundaries;
- medium: visible object geometry;
- light: secondary geometry / furniture / fixtures / planting detail;
- very light: dimensions / extension lines / leaders / hatches / construction/reference geometry.

Lineweights must be verified in the relevant AutoCAD display/plot context, not only by inspecting layer properties.

## 7. Annotation and dimension quality

- use consistent text/dimension styles;
- text height must suit the intended output scale;
- room/zone labels should be centered or intentionally aligned;
- dimensions shall not cross or obscure critical geometry without necessity;
- extension lines, leaders, symbols, and callouts must be clean and consistent;
- dimensions must correspond to source truth or clearly identified derived/inferred geometry;
- do not show fabricated precision;
- north arrows, scale bars, legends, key plans, grids, section marks, level markers, and symbols shall be added when the drawing type/brief requires them.

## 8. Layer and object organization

The exact office naming convention may vary, but layers must separate semantically different object classes sufficiently to support:

- linetype control;
- lineweight control;
- plotting/visibility control;
- discipline/state separation;
- validation and editing.

Do not place the complete drawing on a single default layer for convenience.

## 9. Human-visible execution and checkpoint discipline

All reference-driven/user-reviewed execution must also follow `docs/DRAWING_EXECUTION_QA_WORKFLOW.md` and `docs/SEMANTIC_STATE_PROTOCOL.md`. Quality acceptance does not authorize monolithic execution. The target architecture requires every small semantic step to be verified through native semantic extraction, PID/fingerprinting, deterministic diff/validation and commit-or-verified-rollback before the next dependent step. **At the current N3 checkpoint, full native semantic extraction/rollback is not yet implemented; therefore current workflows must use available structured COM/ezdxf/N3 identity evidence and must not claim a full native Semantic State PASS until N4+ provides the missing gates.** Major gates compare structured state against the SourceSemanticModel. Vision is supplemental, not the geometry oracle.

For live visual stress tests where the user is watching AutoCAD:

- pace visible drawing operations at approximately `0.5–0.8 s` unless a different pace is requested;
- slower waits are acceptable for expensive operations;
- save an independent DWG checkpoint after major phases;
- perform final zoom/view adjustment before the final save for that checkpoint;
- save again after any view/style/annotation change that dirties the drawing;
- capture the actual AutoCAD window/state at visual/debug/major gates as appropriate;
- use native semantic state, not screenshot interpretation, to decide geometric correctness and step continuation;
- visually review final/presentation gates for readability and aesthetics;
- keep the intended final checkpoint open when user inspection is expected.

## 10. Required checkpoint evidence

For a substantial reference-driven drawing checkpoint, preserve as applicable:

- native DWG path/name;
- SemanticSnapshot/state-chain evidence and rollback evidence where applicable;
- screenshot of the actual AutoCAD state for final/visual evidence;
- drawing type/profile;
- units and scale assumptions;
- ground-truth dimensions/data;
- derived values;
- inferred assumptions;
- object/layer/style counts when useful;
- extents and key measurements;
- linetype/lineweight style verification;
- save/reopen or saved-state verification;
- known deviations from the source.

## 11. Visual QA checklist

Before calling `VISUAL_PASS`, inspect the actual rendered AutoCAD result and reject the checkpoint for any material issue such as:

- missing dashed/hidden/center/overhead/cutting-plane line where required;
- dashed pattern visually collapsing into continuous because of scale;
- all lineweights appearing identical;
- misaligned walls/roads/paths/edges;
- rough or accidental curve transitions;
- overlapping text/dimensions;
- inconsistent symbol sizes;
- wrong door/window swing/orientation;
- disconnected site/park circulation;
- ambiguous existing/proposed/boundary information;
- cluttered or visually weak composition;
- missing major source feature;
- invented precision presented as fact;
- unsaved final state.

## 12. User-visible evidence rule

If user review is part of the checkpoint, evidence is not considered delivered merely because the agent/model can read the screenshot internally.

The checkpoint remains `PENDING_USER_VISUAL_ACCEPTANCE` until the reviewer can actually access the screenshot/file in the client and inspect it.

## 13. Fail-closed acceptance rule

Do not self-certify based on provider success, test success, object count, or subjective confidence.

If geometry, drafting convention, linetype/lineweight, domain logic, source fidelity, evidence delivery, or visual quality is unresolved, report the exact incomplete acceptance state and stop before the next dependent checkpoint.
