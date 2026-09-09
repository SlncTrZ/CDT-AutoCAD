# AGENTS.md — CDT-AutoCAD

## Role

This repository owns the AutoCAD MCP provider runtime only. `CDT_Engineer` is the architecture/spec/control repository and is read-only to provider agents unless the Architect explicitly assigns a contract change.

## Governing spec baseline

- Source repo: `SlncTrZ/CDT_Engineer`
- Pinned commit: `643019c`
- Read first: `docs/SPEC_BASELINE.md`, `specs/MCP_PROVIDER_STANDARD.md`, `specs/ARCHITECTURE.md`, `specs/CONTRACTS.md`, `docs/ROADMAP.md`.
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
- A2 implementation: release candidate; real AutoCAD acceptance still OPEN.
- A3.1 ACIS backend: staged/private pending real AutoCAD verification.
- Extraction baseline: 54 tests PASS, 2 live-Windows/AutoCAD tests SKIP before repository split.

## First delivery gates

1. Prove extraction caused no package/version/help/test drift.
2. A2: run the real Windows AutoCAD ActiveX lane; mocks are not substitutes.
3. A3: live-verify native solids before publishing capability/tool surface.
4. Continue advanced drafting/engineering only after A2/A3 acceptance evidence is explicit.

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

## Native-verification rule

A capability requiring AutoCAD is not CLOSED until it passes against a real supported AutoCAD installation on Windows. Capability metadata and docs must distinguish implemented, runtime-available, staged and live-verified states.
