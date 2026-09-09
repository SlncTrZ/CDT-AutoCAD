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
6. `specs/MCP_PROVIDER_STANDARD.md`
7. `specs/ARCHITECTURE.md`
8. `specs/CONTRACTS.md`

## Starting state

The repository was extracted with Git history from `CDT_Engineer/servers/autocad` rather than copied as a new snapshot.

Known implementation state:

- A0+A1 closed in prior monorepo history;
- A2 public release candidate `0.3.0rc1 / autocad-a2-v1-rc1`, 50 tools;
- A2 live acceptance blocked until a real AutoCAD installation is available on Windows;
- A3.1 native ACIS implementation staged below public capability surface;
- source profile preservation for region-based 3D operations uses Copy -> temporary Region -> solid -> cleanup.

## First task

Before adding features, prove repository extraction equivalence:

```text
package/runtime version unchanged
help contract version unchanged
help fingerprint unchanged
focused contract/A3 tests pass
full generic suite matches 54 PASS / 2 live SKIP
compile/hygiene pass
```

Then proceed with A2 live acceptance followed by A3 live verification per `docs/ROADMAP.md`.

## Restrictions

Do not modify another provider repo. Treat `CDT_Engineer` as read-only. Do not publish A3 tools/capabilities or mark A2 CLOSED without real AutoCAD evidence.
