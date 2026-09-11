# AGENTS.md — CDT-AutoCAD

## Role

This repository owns the AutoCAD MCP provider runtime only. `CDT_Engineer` is the architecture/spec/control repository and is read-only to provider agents unless the Architect explicitly assigns a contract change.

## Governing spec baseline

- Source repo: `SlncTrZ/CDT_Engineer`
- Pinned commit: `643019c`
- Read first: `docs/CURRENT_CHECKPOINT.md`, `docs/SPEC_BASELINE.md`, `specs/MCP_PROVIDER_STANDARD.md`, `specs/ARCHITECTURE.md`, `specs/CONTRACTS.md`, `docs/ROADMAP.md`, `docs/ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md`, `docs/SEMANTIC_STATE_PROTOCOL.md`, `docs/ARCHITECTURE_UPGRADE_PLAN.md`, `docs/NATIVE_BRIDGE_ACCEPTANCE.md`, `docs/DRAWING_QUALITY_ACCEPTANCE.md`, `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`.
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
- Historical pre-split extraction baseline: 54 tests PASS, 2 live-Windows/AutoCAD tests SKIP; this is not the current regression count.
- Native semantic checkpoints N0–N7 are closed for their documented scopes; O1 additionally live-verifies internal CIRCLE/ARC/simple-LWPOLYLINE typed mutation over the N5/N6 integrity model; N7 live-verifies checkpoint-backed post-commit R1/R2 recovery including activation-safe R2 after real AutoCAD restart.
- Current O1 regression checkpoint: Linux full suite 195 PASS / 4 SKIP; Windows `.171` full suite 194 PASS / 5 SKIP; focused O1/N5/N6 76 PASS; O1 native acceptance PASS on real AutoCAD 2027 Session 1; C# bridge build 0 errors with three documented unsuppressed `MSB3277` warning families.

## Current delivery gates

1. Preserve the live-verified current `ezdxf + COM` baseline and public 50-tool contract during migration.
2. `N0` documentation freeze is complete; preserve its architecture boundary.
3. `N1` semantic contract models/canonical fingerprint engine are implemented and regression-verified; preserve golden fingerprints/state-chain invariants.
4. `N2` persistent PID storage/clone policy is live-verified on AutoCAD 2027 and closed; preserve NOD/XRecord document-lineage PID plus Extension-Dictionary/XRecord DBObject PID and mandatory clone reconciliation. Raw DWG copies may share lineage PID.
5. `N3` Managed .NET bridge skeleton + local typed IPC, `N4` native bounded SemanticSnapshot extraction, `N5` parent-guarded LINE transaction/R0 rollback, `N6` semantic delta/state-chain enforcement and `N7` checkpoint-backed post-commit recovery are CLOSED/LIVE PASS. O1 extends the internal typed allowlist to CIRCLE, ARC and simple LWPOLYLINE create/update/delete while preserving the same `expected_parent_fp` + persistent-PID invariants. N7 R2 is activation-safe across AutoCAD Idle ticks and proves runtime rebound + document PID + exact predecessor fingerprint after real process restart.
6. Every mutation architecture must preserve the two pillars: Data Integrity/Rollback and Precise Identity/PID+Fingerprinting; N7 recovery is accepted only for its documented bounded staged scope and must not be described as public promotion or whole-DWG coverage.
7. Continue A3/public capability promotion only through explicit contract/version changes; native implementation or verification alone does not publish tools.
8. Any reference-driven/user-reviewed drawing must satisfy both Semantic State integrity and `docs/DRAWING_QUALITY_ACCEPTANCE.md`.

## Required workflow

1. Follow the global SlncTrZ Agent Harness returned by `context.bootstrap`.
2. Read existing code before edits; reuse first.
3. TDD: failing test -> implementation -> pass -> regression.
4. Validate before side effects; fail closed on unknown capability/state.
5. Preserve the current COM STA/timeout integrity model during migration; all new architecture work must target the Native .NET Bridge + Semantic State Loop defined by ADR-001 and the Semantic State Protocol.
6. Run focused tests, full regression, compile/hygiene and `git diff --check` before commit.
7. Every code/deploy change must be logged through CyberBrain `kb.knowledge_store`.
8. End each work session with episodic save (`memory_store`/`conversation_save`) followed by `dream_enqueue`.
9. Commit/push only this repository; branch convention is `main` unless the task explicitly defines a feature branch.

## Drawing-quality invariant

Before generating or reconstructing a user-facing drawing, classify it into one or more profiles from `docs/DRAWING_QUALITY_ACCEPTANCE.md` (for example `ARCHITECTURE_FLOOR_PLAN`, `SITE_PLAN`, `PARK_PLAN`, `PLAZA_PLAN`, `LANDSCAPE_PLAN`, `MASTER_PLAN`, `PARKING_PLAN`, `ROAD_ACCESS_PLAN`, `ELEVATION`, `SECTION`, `DETAIL`, `REFERENCE_REPRODUCTION`).

For every semantically required technical condition, use the correct linetype role and lineweight hierarchy. In particular, hidden/overhead/underground geometry, centerlines/axes, cutting planes, boundaries/easements, existing/proposed/removal states and major/minor contours must not be collapsed into `Continuous` when their drawing profile requires a distinct convention. Missing a required dashed/hidden/center/chain/break/other semantic line is a drawing defect even when coordinates are correct.

Drawing checkpoints progress through `TECHNICAL_PASS -> GEOMETRY_PASS -> DOMAIN_PASS -> VISUAL_PASS -> USER_ACCEPTED`. Only `USER_ACCEPTED` is a completed user-reviewed checkpoint. If the reviewer cannot access the actual screenshot/file, keep the state `PENDING_USER_VISUAL_ACCEPTANCE`.

Execution must follow `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`: decompose work into small semantic steps and prefer structured state over screenshots. The invariant is native extraction -> PID/fingerprint -> semantic delta -> deterministic validation -> commit-or-verified-rollback. **N4–N7 plus O1 now provide this proof only for their documented bounded native scopes; do not project that evidence onto unsupported entity families, topology, whole-DWG extraction or public routing.** Screenshots are supplemental visual evidence, not the geometry oracle. Never run the next dependent step after failed, drifted, uncertain, or unverified state.

## Architecture-upgrade invariants

The target architecture is `Python MCP/Semantic Core -> local typed IPC -> C# AutoCAD Managed .NET Native Bridge -> AutoCAD Database`. The .NET bridge is not the MCP server and must never expose arbitrary code execution. Current COM/ezdxf remains the migration baseline until live native gates prove replacement parity.

Two pillars are non-negotiable:

1. **Data Integrity / Rollback:** every mutation must end as `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`; rollback itself must be proven by semantic read-back and predecessor fingerprint equality. `STATE_UNCERTAIN`, `ROLLBACK_FAILED`, `COMMIT_INTEGRITY_FAIL`, or timeout uncertainty block all later mutations.
2. **Precise Identity / PID + Fingerprinting:** do not treat AutoCAD `ObjectId` or Handle as sufficient semantic identity. The target design requires provider-owned persistent document-lineage/entity PIDs plus versioned geometry/style/topology/instance/state fingerprints, clone/duplicate handling, runtime-document disambiguation, artifact fingerprinting for physical checkpoints, and `expected_parent_fp` drift protection.

The Semantic State Loop is mandatory for new engineering automation: `ActionSpec -> native execution -> semantic extraction -> canonicalize -> fingerprint/diff -> deterministic validation -> commit/verified rollback -> independent read-back -> state-chain log`.

## Native-verification rule

A capability requiring AutoCAD is not CLOSED until it passes against a real supported AutoCAD installation on Windows. Capability metadata and docs must distinguish implemented, runtime-available, staged and live-verified states.
