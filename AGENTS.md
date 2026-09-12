# AGENTS.md — CDT-AutoCAD

## Role

This repository owns the AutoCAD MCP provider runtime only. `CDT_Engineer` is the architecture/spec/control repository and is read-only to provider agents unless the Architect explicitly assigns a contract change.

## Governing spec baseline

- Source repo: `SlncTrZ/CDT_Engineer`
- Pinned commit: `643019c`
- Read first (published): `docs/CURRENT_CHECKPOINT.md`, `docs/SPEC_BASELINE.md`, `docs/SEMANTIC_STATE_PROTOCOL.md`, `specs/MCP_PROVIDER_STANDARD.md`, `specs/ARCHITECTURE.md`, `specs/CONTRACTS.md`, `docs/DRAWING_QUALITY_ACCEPTANCE.md`, `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`.
- Read first (internal, untracked working record): `_private/README.md`, `_private/development/roadmap/ROADMAP.md`, `_private/development/architecture/ARCHITECTURE_UPGRADE_PLAN.md`, `_private/development/adr/ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md`, `_private/development/acceptance/NATIVE_BRIDGE_ACCEPTANCE.md`.
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

- Primary certification target: AutoCAD 2027 full, Windows x64, ActiveX COM `26.0` / `AutoCAD.Application.26`, Managed .NET `net10.0-windows`.
- Current public contract: provider `0.4.0rc1`, contract `autocad-generic-v1-rc1`, **86 MCP tools**, execution model `feature-based-chunks-streaming-v1`.
- Operational use begins **2026-09-12** under this RC/preview identity; this is not a GA/stable-version declaration.
- Public promotion is **release-closed and pushed** at commit `0516fe3`; final Linux/Windows regressions, C# Release/x64 build, review and surgical staging all passed before publication.
- N0–N7 and O1 are CLOSED/LIVE PASS for their documented native scopes; MP-2 hot reload is CLOSED/LIVE PASS.
- G1 Generic Batch Geometry, G2 Schema-Agnostic Metadata and G3 Chunked Logical Atomicity are CLOSED/LIVE PASS on real AutoCAD 2027.
- Native bridge candidate: `0.8.2-mp7`; document fingerprint schema v3; native micro-chunk max 32; graduated semantic capacity 12,288; logical feature/batch cap 10,000; one batch mutation per AutoCAD Idle tick. Historical G1/G2/G3/scale/Feature Streaming evidence remains bound to the bridge identity recorded by each original run and is not rewritten.
- Scale graduation 100 / 1,000 / 5,000 / 10,000 is LIVE PASS with beginning/middle/end failure injection, exact predecessor recovery, zero pending recovery and process-stability evidence.
- Feature-based Chunks Streaming is the approved Production Domain execution model: the Domain Agent defines one meaningful feature; a failed current feature rolls back to its own predecessor without undoing earlier committed features. Default presentation pacing is 300 ms between completed features, not between native micro-chunks.
- Domain semantics remain outside CDT-AutoCAD. `feature_id` is correlation metadata; the provider must not interpret road/manhole/kiosk/beam/pipe/TCVN or similar business meaning.
- Canonical current-state authority: `docs/CURRENT_CHECKPOINT.md`; operational procedure: `docs/OPERATIONS_RUNBOOK.md`; release gate: the maintainer-internal release checklist under `_private/development/release/`. Historical session handoffs remain evidence of their original checkpoints and are not current-state instructions.

## Current delivery gates

1. Do not reimplement or reopen G1/G2/G3/Feature Streaming without regression evidence; their documented live gates are already accepted.
2. Preserve the two pillars: Data Integrity/Rollback and Precise Identity/PID+Fingerprinting. Unknown or unverifiable completion remains fail-closed.
3. Preserve native chunk bounds and Idle-yield responsiveness; logical/feature atomicity must not be implemented as one giant long-lived AutoCAD transaction.
4. The `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools` public promotion is CLOSED at `0516fe3`; do not reopen it unless regression evidence proves a defect.
5. Any future public contract/version change must again run full Linux regression, Windows `.171` regression, C# Release/x64 build, compile/hygiene, `git diff --check` and code/security review before commit/push.
6. Stage surgically. Do not stage `.gitignore`, `_private/`, `_test_workspace/` or `specs/**` in the promotion commit unless explicitly authorized.
7. Historical evidence/handoff files may legitimately mention 50 tools, fingerprint v2 or staged A3 because those values describe their original checkpoint; do not rewrite historical evidence to pretend it ran under the new contract.
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
