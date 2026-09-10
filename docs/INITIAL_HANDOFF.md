# Initial Handoff — Agent A / AutoCAD

## Repository

- Path: `/mnt/pc-dev/CDT-AutoCAD`
- GitHub: `SlncTrZ/CDT-AutoCAD`
- Branch: `main`
- Governing hub pin: `CDT_Engineer@643019c`

## Read first

1. `AGENTS.md`
2. `docs/SPEC_BASELINE.md`
3. `docs/ROADMAP.md`
4. `README.md`
5. `docs/TOOL_GUIDE.md`
6. `docs/LIVE_ACCEPTANCE.md`
7. `docs/ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md`
8. `docs/SEMANTIC_STATE_PROTOCOL.md`
9. `docs/ARCHITECTURE_UPGRADE_PLAN.md`
10. `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`
11. `docs/DRAWING_QUALITY_ACCEPTANCE.md`
12. `specs/MCP_PROVIDER_STANDARD.md`
13. `specs/ARCHITECTURE.md`
14. `specs/CONTRACTS.md`

## Starting state

The repository was extracted with Git history from `CDT_Engineer/servers/autocad` rather than copied as a new snapshot.

Known implementation state:

- A0+A1 closed in prior monorepo history;
- A2 public release candidate `0.3.0rc1 / autocad-a2-v1-rc1`, 50 tools;
- primary native certification target is AutoCAD 2027 full / Windows x64 / COM `26.0`;
- A2 live acceptance passed on full AutoCAD 2027 / Windows `.171`; RC identity remains pending explicit release/promotion;
- A3.1 native ACIS implementation staged below public capability surface;
- A3.2 angular/radial/diametric/ordinate dimensions implemented backend-side, capability-false and non-public pending native verification;
- A3.3 object measurement/current-space WCS extents + native COM intersections implemented backend-side, capability-false and non-public pending native verification;
- source profile preservation for region-based 3D operations uses Copy -> temporary Region -> solid -> cleanup.
- target architecture upgrade is accepted but not yet implemented: Python Semantic Core + local typed IPC + in-process C# Managed .NET bridge;
- the two architecture pillars are Data Integrity/Rollback and Precise Identity/PID+Fingerprinting;
- Semantic State Loop is mandatory for future engineering automation; screenshots are supplemental, not per-step geometry proof.

## First task

Architecture phase **N1 — Semantic contract models + canonical fingerprint engine** is complete and regression-verified. The next task is **N2 — persistent PID design prototype on real AutoCAD 2027**: validate the native PID carrier plus save/reopen, ordinary edits, clone/deep-clone/WBLOCK/INSERT, erase/undo/redo and duplicate-PID behavior before N3 depends on that storage choice.

The current COM/ezdxf runtime remains the comparison baseline. New implementation must not weaken or silently replace current public behavior until real AutoCAD parity/integrity gates pass.

## Restrictions

Do not modify another provider repo. Treat `CDT_Engineer` as read-only. Do not publish A3 tools/capabilities or mark A2 CLOSED without real AutoCAD evidence.
