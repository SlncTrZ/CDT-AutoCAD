# Initial Handoff — Agent A / AutoCAD

> Historical bootstrap document. N7 and MP-2 closed/live-passed on 2026-09-11. Use `docs/CURRENT_CHECKPOINT.md` for current status; `docs/SESSION_HANDOFF_2026-09-11_MP2_HOT_RELOAD.md` and `docs/N7_WORKING_CHECKPOINT_2026-09-10.md` are retained as historical implementation/handoff records.

## Repository

- Path: `/mnt/pc-dev/CDT-AutoCAD`
- GitHub: `SlncTrZ/CDT-AutoCAD`
- Branch: `main`
- Governing hub pin: `CDT_Engineer@643019c`

## Read first

1. `AGENTS.md`
2. `docs/CURRENT_CHECKPOINT.md`
3. `docs/SPEC_BASELINE.md`
4. `docs/ROADMAP.md`
5. `README.md`
6. `docs/TOOL_GUIDE.md`
7. `docs/LIVE_ACCEPTANCE.md`
8. `docs/ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md`
9. `docs/SEMANTIC_STATE_PROTOCOL.md`
10. `docs/ARCHITECTURE_UPGRADE_PLAN.md`
11. `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`
12. `docs/DRAWING_QUALITY_ACCEPTANCE.md`
13. `specs/MCP_PROVIDER_STANDARD.md`
14. `docs/SESSION_HANDOFF_2026-09-11_MP2_HOT_RELOAD.md`
15. `docs/N7_WORKING_CHECKPOINT_2026-09-10.md`
16. `specs/ARCHITECTURE.md`
17. `specs/CONTRACTS.md`

## Historical starting state

The repository was extracted with Git history from `CDT_Engineer/servers/autocad` rather than copied as a new snapshot.

Known implementation state:

- A0+A1 closed in prior monorepo history;
- A2 public release candidate `0.3.0rc1 / autocad-a2-v1-rc1`, 50 tools;
- primary native certification target is AutoCAD 2027 full / Windows x64 / COM `26.0`;
- A2 live acceptance passed on full AutoCAD 2027 / Windows `.171`; RC identity remains pending explicit release/promotion;
- A3.1 native ACIS implementation is native live-verified on AutoCAD 2027 and remains staged below the public capability surface pending explicit promotion;
- A3.2 angular/radial/diametric/ordinate dimensions are backend-implemented and native live-verified on AutoCAD 2027; still capability-false/non-public pending explicit promotion;
- A3.3 object measurement/current-space WCS extents + native COM intersections are backend-implemented and native live-verified on AutoCAD 2027; still capability-false/non-public pending explicit promotion;
- source profile preservation for region-based 3D operations uses Copy -> temporary Region -> solid -> cleanup.
- target architecture migration is in progress: N1 Python Semantic Core, N2 persistent PID policy, and N3 local typed IPC + in-process C# Managed .NET bridge are implemented/live-verified; N4 native semantic extraction is next;
- the two architecture pillars are Data Integrity/Rollback and Precise Identity/PID+Fingerprinting;
- Semantic State Loop is mandatory for future engineering automation; screenshots are supplemental, not per-step geometry proof.

## Current handoff task

N0–N7 and O1 are complete for their bounded scopes. N7 `0.5.0-n7` closed/live-passed on AutoCAD 2027 Session 1 with activation-safe R2 recovery before and after a real process restart; canonical evidence is `docs/evidence/n7-native-recovery-2026-09-11.json`.

**MP-2 Python MCP/provider hot reload is CLOSED / LIVE PASS.** The stable supervisor + replaceable stateless worker runtime passed protocol/contract-hash promotion validation, process-level `20` success + `10` injected failure acceptance, Windows `.171` AutoCAD 2027 Interactive Session 1 COM + required native bridge acceptance, full Linux/Windows regressions and review. Canonical evidence: `docs/evidence/mp2-hot-reload-2026-09-11.json`. `.171` has `cloudflared` running but no CDT-AutoCAD ingress route; its config was not changed for MP-2.

The current COM/ezdxf runtime remains the comparison/public baseline. New implementation must not weaken or silently replace current public behavior until real AutoCAD parity/integrity and explicit promotion gates pass.

## Restrictions

Do not modify another provider repo. Treat `CDT_Engineer` as read-only. Do not publish A3 tools/capabilities without an explicit contract promotion. Do not mark A2 CLOSED/promoted without an explicit promotion decision; native AutoCAD evidence is necessary but is not itself a promotion decision.
