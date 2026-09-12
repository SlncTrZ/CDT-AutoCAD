# Spec Baseline — CDT-AutoCAD

> Pinned: 2026-09-09
> Architecture source: `SlncTrZ/CDT_Engineer@643019c`
> Provider-local operational overlay reviewed: 2026-09-12 · current public identity `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools`

This provider repository implements AutoCAD runtime behavior against a pinned snapshot of the CDT control-plane specifications.

## Pinned inputs

| Local snapshot | Source at `643019c` |
| --- | --- |
| `specs/MCP_PROVIDER_STANDARD.md` | `MCP_PROVIDER_STANDARD.md` |
| `specs/ARCHITECTURE.md` | `docs/ARCHITECTURE.md` |
| `specs/CONTRACTS.md` | `docs/CONTRACTS.md` |

The local roadmap/plan mirror of `docs/PLAN_AUTOCAD.md` is maintainer-internal and is not part of the published documentation set.

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
- Historical extraction identity at the pinned split: `0.3.0rc1 / autocad-a2-v1-rc1`. This is not the current provider identity.
- Expected pre-extraction generic regression: `54 passed, 2 skipped`.

## Local architecture overlay

The pinned common specifications remain the frozen common-control input; provider-local extensions have since advanced under explicit local contracts without rewriting the pinned snapshots. CDT-AutoCAD also has a provider-local architecture overlay; N0–N7 and O1 are closed/live-verified for their bounded native scopes. The overlay's published parts are:

- `docs/CURRENT_CHECKPOINT.md`;
- `docs/SEMANTIC_STATE_PROTOCOL.md`;
- `docs/LIVE_ACCEPTANCE.md`;
- `docs/DRAWING_EXECUTION_QA_WORKFLOW.md`;
- `docs/DRAWING_QUALITY_ACCEPTANCE.md`.

The overlay makes two local target-architecture invariants explicit: **Data Integrity / Rollback** and **Precise Identity / PID + Fingerprinting**.

This overlay does **not** mutate the pinned `specs/` snapshots or silently change common CDT semantics. Cross-provider semantic-state abstractions must be proposed to `CDT_Engineer` through an explicit spec/pin update after sufficient Rule-of-Two evidence.
