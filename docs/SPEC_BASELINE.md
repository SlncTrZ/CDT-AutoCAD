# Spec Baseline — CDT-AutoCAD

> Pinned: 2026-09-09
> Architecture source: `SlncTrZ/CDT_Engineer@643019c`

This provider repository implements AutoCAD runtime behavior against a pinned snapshot of the CDT control-plane specifications.

## Pinned inputs

| Local snapshot | Source at `643019c` |
| --- | --- |
| `specs/MCP_PROVIDER_STANDARD.md` | `MCP_PROVIDER_STANDARD.md` |
| `specs/ARCHITECTURE.md` | `docs/ARCHITECTURE.md` |
| `specs/CONTRACTS.md` | `docs/CONTRACTS.md` |
| `docs/ROADMAP.md` | `docs/PLAN_AUTOCAD.md` |

## Rules

- Files in `specs/` are read-only snapshots for provider work.
- `CDT_Engineer` owns common-contract/architecture changes.
- AutoCAD provider extensions may evolve independently when they do not change common semantics.
- A common-spec update requires an explicit pin update and conformance review; never silently track hub `main`.
- No runtime import/dependency on another CDT provider repository is permitted.

## Extraction baseline

- Source subtree: `CDT_Engineer/servers/autocad/**`.
- Source checkpoint: `CDT_Engineer@643019c`.
- History-preserving subtree split head: `11adadb`.
- Expected provider identity: `0.3.0rc1 / autocad-a2-v1-rc1`.
- Expected pre-extraction generic regression: `54 passed, 2 skipped`.
