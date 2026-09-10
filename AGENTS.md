# AGENTS.md — CDT-AutoCAD

## Role

This repository owns the AutoCAD MCP provider runtime only. `CDT_Engineer` is the architecture/spec/control repository and is read-only to provider agents unless the Architect explicitly assigns a contract change.

## Governing spec baseline

- Source repo: `SlncTrZ/CDT_Engineer`
- Pinned commit: `643019c`
- Read first: `docs/SPEC_BASELINE.md`, `specs/MCP_PROVIDER_STANDARD.md`, `specs/ARCHITECTURE.md`, `specs/CONTRACTS.md`, `docs/ROADMAP.md`, `docs/DRAWING_QUALITY_ACCEPTANCE.md`.
- Do not edit files under `specs/`; they are pinned snapshots. Contract changes must be proposed in `CDT_Engineer` and synced here only after approval.

## Ownership boundary

Allowed: all files in `CDT-AutoCAD/**`.

Forbidden unless explicitly assigned:

- `CDT-SketchUp/**`, `CDT-Blender/**`, `CDT-SolidWorks/**`;
- provider business logic in `CDT_Engineer/**`;
- creating `CDT-Provider-Kit` before Rule-of-Two evidence;
- arbitrary AutoLISP/command/script execution surfaces;
- claiming live AutoCAD verification from mocks/Linux.

## Current checkpoint

- Provider version: `0.3.0rc1`
- Contract: `autocad-a2-v1-rc1`
- Public surface: 50 MCP tools
- A2 native AutoCAD 2027 acceptance: PASS on Windows `.171`; release-candidate state retained pending explicit promotion/release review.
- Primary certification target: AutoCAD 2027 full, Windows x64, ActiveX COM `26.0` / `AutoCAD.Application.26`.
- A3.1 ACIS backend: live-verified on AutoCAD 2027; remains staged/private until explicit promotion.
- A3.2 advanced dimensions: live-verified on AutoCAD 2027; capability-false/non-public pending explicit promotion.
- A3.3 analysis: live-verified on AutoCAD 2027; capability-false/non-public pending explicit promotion.
- Extraction baseline: 54 tests PASS, 2 live-Windows/AutoCAD tests SKIP before repository split.
- Current generic staging checkpoint: Linux 83 PASS / 4 live SKIP; Windows `.171` 82 PASS / 5 SKIP (4 native gates + 1 non-Windows honesty test).

## First delivery gates

1. Extraction equivalence is complete; preserve the pinned spec/runtime boundary.
2. A2: run `scripts/run_live_acceptance.ps1` against full AutoCAD 2027; mocks are not substitutes.
3. A3.1: live-verify native solids before publishing capability/tool surface.
4. A3.2: live-verify advanced dimensions before adding the four MCP tools or enabling `autocad.dimensions.advanced`.
5. A3.3: live-verify measurement/extents/intersections before adding analysis MCP tools or enabling their capabilities.
6. Continue advanced drafting/engineering only with capability-false staging until each native acceptance boundary is explicit.
7. Any reference-driven or user-reviewed drawing/stress test must satisfy `docs/DRAWING_QUALITY_ACCEPTANCE.md`; provider success, valid entities, save success, object counts, or internal screenshot readability are insufficient to call the drawing PASS.

## Required workflow

1. Follow the global SlncTrZ Agent Harness returned by `context.bootstrap`.
2. Read existing code before edits; reuse first.
3. TDD: failing test -> implementation -> pass -> regression.
4. Validate before side effects; fail closed on unknown capability/state.
5. Keep COM mutations on the existing STA/timeout integrity model.
6. Run focused tests, full regression, compile/hygiene and `git diff --check` before commit.
7. Every code/deploy change must be logged through CyberBrain `kb.knowledge_store`.
8. End each work session with episodic save (`memory_store`/`conversation_save`) followed by `dream_enqueue`.
9. Commit/push only this repository; branch convention is `main` unless the task explicitly defines a feature branch.

## Drawing-quality invariant

Before generating or reconstructing a user-facing drawing, classify it into one or more profiles from `docs/DRAWING_QUALITY_ACCEPTANCE.md` (for example `ARCHITECTURE_FLOOR_PLAN`, `SITE_PLAN`, `PARK_PLAN`, `PLAZA_PLAN`, `LANDSCAPE_PLAN`, `MASTER_PLAN`, `PARKING_PLAN`, `ROAD_ACCESS_PLAN`, `ELEVATION`, `SECTION`, `DETAIL`, `REFERENCE_REPRODUCTION`).

For every semantically required technical condition, use the correct linetype role and lineweight hierarchy. In particular, hidden/overhead/underground geometry, centerlines/axes, cutting planes, boundaries/easements, existing/proposed/removal states and major/minor contours must not be collapsed into `Continuous` when their drawing profile requires a distinct convention. Missing a required dashed/hidden/center/chain/break/other semantic line is a drawing defect even when coordinates are correct.

Drawing checkpoints progress through `TECHNICAL_PASS -> GEOMETRY_PASS -> DOMAIN_PASS -> VISUAL_PASS -> USER_ACCEPTED`. Only `USER_ACCEPTED` is a completed user-reviewed checkpoint. If the reviewer cannot access the actual screenshot/file, keep the state `PENDING_USER_VISUAL_ACCEPTANCE`.

## Native-verification rule

A capability requiring AutoCAD is not CLOSED until it passes against a real supported AutoCAD installation on Windows. Capability metadata and docs must distinguish implemented, runtime-available, staged and live-verified states.
