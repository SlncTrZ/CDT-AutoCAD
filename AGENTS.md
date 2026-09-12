# AGENTS.md — CDT-AutoCAD

## Role

This repository owns the AutoCAD MCP provider runtime only. `CDT_Engineer` owns engineering-domain architecture/spec/control and is read-only to provider agents unless the Architect explicitly assigns a cross-repo contract change.

## Documentation authority — read before work

Do not infer architecture or roadmap from whichever file is easiest to find. Each concern has one authority:

| Concern | Authority |
| --- | --- |
| Public provider architecture | `docs/ARCHITECTURE.md` |
| Public current runtime/release state | `docs/CURRENT_CHECKPOINT.md` |
| Semantic state/recovery protocol | `docs/SEMANTIC_STATE_PROTOCOL.md` |
| Live acceptance evidence | `docs/LIVE_ACCEPTANCE.md` |
| Operations | `docs/OPERATIONS_RUNBOOK.md` |
| Public tool contract | `docs/TOOL_GUIDE.md` |
| Pinned upstream baseline | `docs/SPEC_BASELINE.md` + `specs/**` |
| Internal current-state audit | `_private/AUDIT.md` |
| Technical debt | `_private/TECH_DEBT.md` |
| Roadmap | `_private/DEVELOP_PLAN.md` |
| Latest session handoff | `_private/HANDOFF.md` |
| Next-session start point | `_private/NEXT_SESSION.md` |

`_private/` must contain exactly those five control files. Do not create additional TODO/roadmap/checkpoint/history/evidence trees there.

`specs/**` are pinned read-only snapshots from CDT-Engineer, not current provider architecture. Do not edit them unless an explicit common-spec/pin update is assigned.

## Current checkpoint

- Status: **LAUNCH-READY / OPERATIONAL RC** for the Generic CAD Execution Engine mission.
- Current main checkpoint: `911ba09` (`feat: close native solid integrity loop`).
- Primary certification target: AutoCAD 2027 full, Windows x64, ActiveX COM `26.0` / `AutoCAD.Application.26`, Managed .NET `net10.0-windows`.
- Public contract: provider `0.4.0rc1`, contract `autocad-generic-v1-rc1`, **86 MCP tools**, execution model `feature-based-chunks-streaming-v1`.
- Native bridge candidate: `0.8.2-mp7`.
- N0–N7/O1, MP-2, G1/G2/G3, scale 100/1k/5k/10k, Feature Streaming, Integrity P0, mixed-PID P1 and bounded MP-G05 planar-solid-translate scope are closed according to `docs/CURRENT_CHECKPOINT.md` / `docs/LIVE_ACCEPTANCE.md`.
- No default AutoCAD expansion milestone is open. New capability work must be driven by a concrete CDT-Engineer/production workflow or regression evidence.

## Product boundary

CDT-AutoCAD is a **Generic CAD Execution Engine**, not an engineering domain.

It executes typed generic CAD actions and proves resulting state. Standards, engineering rules, calculations, design intent, discipline-specific validation, review/approval and reports remain outside this provider.

Do not add a capability merely because AutoCAD exposes an API for it. Follow the extension rule in `docs/ARCHITECTURE.md` and the trigger/intake rules in `_private/DEVELOP_PLAN.md`.

## Ownership boundary

Allowed: all files in `CDT-AutoCAD/**`.

Forbidden unless explicitly assigned:

- `CDT-SketchUp/**`, `CDT-Blender/**`, `CDT-SolidWorks/**`;
- provider business logic in `CDT_Engineer/**`;
- creating `CDT-Provider-Kit` before Rule-of-Two evidence;
- arbitrary AutoLISP/command/script/caller-supplied C# execution surfaces;
- claiming live AutoCAD verification from mocks/Linux.

## Delivery gates

1. Do not reopen closed lanes without regression evidence or a concrete Engineer requirement.
2. Preserve the two launch-critical pillars defined in `docs/ARCHITECTURE.md`: Data Integrity/Rollback and Precise Identity/PID+Fingerprinting.
3. Preserve native chunk bounds and Idle-yield responsiveness; never implement logical atomicity as one giant long-lived AutoCAD transaction.
4. Unknown or unverifiable completion remains fail-closed.
5. A live-dependent capability is not CLOSED until verified on real supported AutoCAD/Windows.
6. Any material public contract/version change requires full Linux regression, Windows `.171` regression, relevant AutoCAD 2027 live acceptance, C# Release/x64 when native code changes, compile/hygiene, `git diff --check` and code/security review.
7. Stage surgically. Do not stage `.gitignore`, `_private/`, `_test_workspace/`, `artifacts/` or `specs/**` unless explicitly required by the task.
8. Historical evidence describes its original identity/scope. Never rewrite old evidence to make it appear to have run under a newer contract/runtime.

## Required workflow

1. Follow the global SlncTrZ Agent Harness returned by `context.bootstrap`.
2. Read `docs/ARCHITECTURE.md` and `docs/CURRENT_CHECKPOINT.md` before architecture/capability work.
3. For a new Engineer-driven capability, capture the intake defined in `_private/DEVELOP_PLAN.md` before implementation.
4. Read existing code before edits; reuse first.
5. TDD/fault injection where appropriate: failing test -> implementation -> pass -> regression.
6. Validate before side effects; fail closed on unknown capability/state.
7. Run focused tests, relevant/full regression, compile/hygiene and `git diff --check` before commit.
8. C# changes require Release/x64 build and live verification appropriate to the behavior.
9. Every code/deploy change must be logged through CyberBrain `kb.knowledge_store`.
10. End each work session with episodic save (`memory_store`/`conversation_save`) followed by `dream_enqueue`.
11. Commit/push only this repository; branch convention is `main` unless the task explicitly defines otherwise.

## Drawing-quality invariant

Before generating or reconstructing a user-facing drawing, classify it using `docs/DRAWING_QUALITY_ACCEPTANCE.md` and execute through `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`.

Required semantic linetype/lineweight roles must not be collapsed merely because geometry coordinates are correct. Hidden/overhead/underground geometry, centerlines/axes, cutting planes, boundaries/easements, existing/proposed/removal states and contour roles remain drawing semantics where the selected profile requires them.

Drawing checkpoints progress through `TECHNICAL_PASS -> GEOMETRY_PASS -> DOMAIN_PASS -> VISUAL_PASS -> USER_ACCEPTED`. Only `USER_ACCEPTED` is a completed user-reviewed checkpoint. If the reviewer cannot access the actual screenshot/file, keep `PENDING_USER_VISUAL_ACCEPTANCE`.

Screenshots are supplemental evidence, not the geometry oracle. Do not run the next dependent step after failed, drifted, uncertain or unverified state.

## Integrity guardrails

The canonical architecture/state-loop definitions live in `docs/ARCHITECTURE.md` and `docs/SEMANTIC_STATE_PROTOCOL.md`; this file does not redefine them.

Operationally enforce these consequences:

- a strong-integrity mutation must end `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`;
- `STATE_UNCERTAIN`, `ROLLBACK_FAILED`, `COMMIT_INTEGRITY_FAIL` or equivalent uncertainty blocks later mutation;
- AutoCAD ObjectId/Handle alone is not sufficient semantic identity for strong-integrity state;
- provider PID/fingerprint and `expected_parent_fp` rules must be preserved for supported native scopes;
- mixed unmanaged entities must remain explicit/fail-closed rather than silently adopted by read paths;
- capability metadata and docs must distinguish implemented/runtime-available/live-verified/assurance scope rather than treating catalog presence as proof.

## Demand-driven extension rule

When CDT-Engineer asks for a new capability, require at least:

```text
engineering_workflow
blocked_step
required_cad_capability
expected_postcondition
verification_invariant
required_integrity_level
live_acceptance_fixture
```

Implement the smallest reusable primitive that satisfies that requirement. Native Boolean recovery, arbitrary 3D transform parity, B-rep topology, shell/fillet/chamfer, whole-DWG exhaustive verification and similar breadth are optional until a real workflow needs them.
