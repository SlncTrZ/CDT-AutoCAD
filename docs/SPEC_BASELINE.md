# Spec Baseline — CDT-AutoCAD

> Pinned upstream commit: `SlncTrZ/CDT_Engineer@643019c`
> Pinned: 2026-09-09
> Provider-local architecture authority: `docs/ARCHITECTURE.md`

This document records which CDT-Engineer control-plane specifications CDT-AutoCAD consumes as frozen upstream inputs.

It is **not** the current provider architecture and it is **not** the provider roadmap.

## Pinned inputs

| Local frozen snapshot | Upstream source at `643019c` |
| --- | --- |
| `specs/MCP_PROVIDER_STANDARD.md` | `MCP_PROVIDER_STANDARD.md` |
| `specs/ARCHITECTURE.md` | `docs/ARCHITECTURE.md` |
| `specs/CONTRACTS.md` | `docs/CONTRACTS.md` |

The identical filename `ARCHITECTURE.md` has two different roles:

- `specs/ARCHITECTURE.md` = frozen common/control-plane input from CDT-Engineer;
- `docs/ARCHITECTURE.md` = current provider-local CDT-AutoCAD architecture source of truth.

Do not substitute one for the other.

## Rules

- Files in `specs/` are read-only snapshots for ordinary provider work.
- `CDT_Engineer` owns common-contract/common-architecture changes.
- A common-spec update requires an explicit pin update and conformance review; never silently track hub `main`.
- Provider-local implementation may evolve without rewriting the frozen snapshot when common semantics do not change.
- No runtime import/dependency on another CDT provider repository is permitted.
- Provider roadmap lives only in `_private/DEVELOP_PLAN.md`; no published roadmap mirror is maintained here.

## Extraction baseline

- Source subtree: `CDT_Engineer/servers/autocad/**`.
- Source checkpoint: `CDT_Engineer@643019c`.
- History-preserving subtree split head: `11adadb`.
- Historical extraction identity at the pinned split: `0.3.0rc1 / autocad-a2-v1-rc1`.
- Expected pre-extraction generic regression: `54 passed, 2 skipped`.

Those values describe extraction history only; they do not override the current provider identity in `docs/CURRENT_CHECKPOINT.md`.

## Provider-local overlay

Provider-local behavior is defined by current CDT-AutoCAD public authorities rather than by mutating the pinned snapshots:

- `docs/ARCHITECTURE.md` — provider-local architecture;
- `docs/CURRENT_CHECKPOINT.md` — current runtime/release state;
- `docs/SEMANTIC_STATE_PROTOCOL.md` — semantic-state/recovery contract;
- `docs/LIVE_ACCEPTANCE.md` — accepted live evidence;
- `docs/DRAWING_EXECUTION_QA_WORKFLOW.md` and `docs/DRAWING_QUALITY_ACCEPTANCE.md` — drawing workflow/quality concerns.

Cross-provider abstractions that should become common semantics must be proposed back to CDT-Engineer through an explicit spec/pin update after sufficient evidence; they must not be smuggled into `specs/**` from this provider.
