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
7. `specs/MCP_PROVIDER_STANDARD.md`
8. `specs/ARCHITECTURE.md`
9. `specs/CONTRACTS.md`

## Starting state

The repository was extracted with Git history from `CDT_Engineer/servers/autocad` rather than copied as a new snapshot.

Known implementation state:

- A0+A1 closed in prior monorepo history;
- A2 public release candidate `0.3.0rc1 / autocad-a2-v1-rc1`, 50 tools;
- primary native certification target is AutoCAD 2027 full / Windows x64 / COM `26.0`;
- A2 live acceptance remains open while AutoCAD 2027 is being installed;
- A3.1 native ACIS implementation staged below public capability surface;
- A3.2 angular/radial/diametric/ordinate dimensions implemented backend-side, capability-false and non-public pending native verification;
- A3.3 object measurement/current-space WCS extents + native COM intersections implemented backend-side, capability-false and non-public pending native verification;
- source profile preservation for region-based 3D operations uses Copy -> temporary Region -> solid -> cleanup.

## First task

Repository extraction equivalence is complete. Current A3.3 staging checkpoint is Linux
`72 PASS / 4 live SKIP` and Windows `.171` `71 PASS / 5 SKIP`; four Windows skips are native
A2/A3.1/A3.2/A3.3 gates and the extra skip is the deliberate non-Windows capability-honesty test.

When AutoCAD 2027 is installed/licensed and running, execute:

```powershell
./scripts/run_live_acceptance.ps1
```

Review the generated JUnit/pytest evidence before changing A2/A3 capability state. Until then, useful
work may continue only on generic correctness, advanced drafting implementation and test preparation
that does not claim native acceptance.

## Restrictions

Do not modify another provider repo. Treat `CDT_Engineer` as read-only. Do not publish A3 tools/capabilities or mark A2 CLOSED without real AutoCAD evidence.
