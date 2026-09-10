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

## Local architecture overlay

The pinned common specifications remain authoritative for the currently published provider contract, but CDT-AutoCAD now has a provider-local target-architecture overlay for the next development phase:

- `docs/ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md`;
- `docs/SEMANTIC_STATE_PROTOCOL.md`;
- `docs/ARCHITECTURE_UPGRADE_PLAN.md`;
- `docs/NATIVE_BRIDGE_ACCEPTANCE.md`;
- `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`;
- `docs/DRAWING_QUALITY_ACCEPTANCE.md`.

The overlay makes two local target-architecture invariants explicit: **Data Integrity / Rollback** and **Precise Identity / PID + Fingerprinting**.

This overlay does **not** mutate the pinned `specs/` snapshots or silently change common CDT semantics. Cross-provider semantic-state abstractions must be proposed to `CDT_Engineer` through an explicit spec/pin update after sufficient Rule-of-Two evidence.
