# PLAN — 3-Agent Parallel Native/Semantic Development

> Prepared: 2026-09-10
> Baseline: `main@f0b4dce` · N4–N6 CLOSED
> Historical status: **PLAN ONLY at preparation time**
> Supersession note: later operator authorization opened O1 and then N7; current authority is `docs/CURRENT_CHECKPOINT.md` and `docs/N7_WORKING_CHECKPOINT_2026-09-10.md`
> Audit input: `docs/N4_N6_AUDIT_2026-09-10.md`

## 1. Goal

Reduce implementation elapsed time without weakening CDT-AutoCAD's two non-negotiable pillars:

1. **Data Integrity / Rollback** — no uncertain state advances.
2. **Precise Identity / PID + Fingerprinting** — every accepted mutation is bound to a known semantic predecessor and independently verified result.

The model uses **two parallel production agents plus one independent integration/acceptance agent**. Parallelism starts only after a shared contract freeze for the wave. No agent may treat its own tests as final acceptance.

## 2. Repository / Git model

All agents start from the same approved `origin/main` SHA and use isolated worktrees/branches. No agent commits directly to `main`.

Recommended branch/worktree naming:

```text
agent-a/native-<wave>       -> /mnt/pc-dev/_worktrees/cdt-autocad-agent-a
agent-b/semantic-<wave>     -> /mnt/pc-dev/_worktrees/cdt-autocad-agent-b
agent-c/integration-<wave>  -> /mnt/pc-dev/_worktrees/cdt-autocad-agent-c
```

Rules:

- each wave records `base_sha` before parallel work begins;
- A and B rebase/refresh only at explicit wave boundaries, not continuously while implementing;
- A/B publish immutable candidate commit SHA(s), never “latest branch” as acceptance input;
- Agent C consumes candidate SHAs, verifies merge compatibility in its own worktree and never rewrites A/B production history;
- only reviewed candidate commits are merged to `main`;
- every logical checkpoint keeps its own tests, native acceptance, evidence, review and commit/push boundary;
- N-series status changes only in the integration/evidence commit after measured acceptance.

## 3. Ownership boundaries

### Agent A — Native Bridge / AutoCAD runtime

Primary ownership:

```text
native/CDT.AutoCAD.Bridge/**
prototypes/pid-native/**          # native probes/fixtures only
native-specific acceptance harnesses
```

Responsibilities:

- AutoCAD Managed .NET document/database/transaction behavior;
- typed native protocol implementation after the wave contract is frozen;
- PID storage/resolution and native clone/reconciliation behavior;
- native extraction and write-path mechanics;
- AutoCAD Session 1 fault probes and native build evidence;
- no generic command/eval/LISP/shell execution surface.

Agent A must not redefine Python semantic acceptance rules unilaterally.

### Agent B — Semantic Core / deterministic orchestration

Primary ownership:

```text
src/cdt_autocad/semantic/**
src/cdt_autocad/native_bridge/semantic*.py
semantic/unit tests in new dedicated test files
```

Responsibilities:

- ActionSpec-derived deterministic validation contracts;
- delta/fingerprint/state-chain/recovery models;
- journal/state reconstruction semantics;
- allowed-effects and failure classification;
- compiling future semantic intent into a frozen typed native validation/request contract;
- no direct AutoCAD UI/COM mutation as proof of correctness.

Agent B must not expand the C# operation allowlist independently.

### Agent C — Integration / Review / Native Acceptance

Primary ownership:

```text
docs/evidence/**
docs/*ACCEPTANCE*.md
checkpoint/audit/handoff docs
integration/acceptance runners that do not become product runtime
```

Responsibilities:

- independently review A/B diffs and trace cross-boundary error paths;
- merge candidate SHAs in an isolated integration worktree;
- run Linux + Windows regression;
- build/deploy candidate native binary only after reviewing the exact SHA;
- execute real AutoCAD 2027 Session 1 acceptance;
- compare native state against independent semantic/COM observations where applicable;
- write canonical evidence from measured results;
- reject overclaims and keep future gates OPEN when proof is incomplete.

Agent C does **not** fix A/B production code during acceptance. Findings go back to the owning agent as a new candidate commit. This preserves independent verification.

## 4. Shared-file freeze

The following are **single-writer integration files** during a parallel wave and should normally be edited only by Agent C after A/B candidates exist:

```text
docs/CURRENT_CHECKPOINT.md
docs/ARCHITECTURE_UPGRADE_PLAN.md
docs/NATIVE_BRIDGE_ACCEPTANCE.md
docs/SEMANTIC_STATE_PROTOCOL.md
docs/ROADMAP.md
```

Protocol/model files that both A and B conceptually depend on must be frozen before the wave. If the frozen contract proves wrong, stop both production lanes, amend the contract once, publish a new `contract_sha`, then restart/rebase the wave. Do not let A and B independently “fix” the same contract.

## 5. Execution waves

The sequence below describes future work organization only. Starting any implementation wave requires a separate explicit authorization.

### Wave 0 — Contract freeze / task decomposition

Owner: Agent C with review input from A/B. No runtime implementation.

Outputs:

- exact problem statement and gate mapping;
- typed request/response/failure vocabulary;
- ownership map for every planned file;
- native acceptance matrix and fault stages;
- frozen `base_sha` + `contract_sha`;
- explicit list of out-of-scope features.

Gate: A/B can implement without editing the same contract or production file.

### Wave 1 — Recovery foundation in two parallel lanes

Historical planning target: the unresolved post-commit/restart integrity boundary identified by the N4–N6 audit. This plan itself did not start N7; N7 was subsequently opened by explicit operator authorization and is now tracked in the current checkpoint documents.

Agent A candidate lane:

- native transaction/provisional-read mechanics required by the frozen contract;
- narrowly typed compensation/checkpoint hooks only if the contract explicitly includes them;
- native fault injection at deterministic boundaries;
- no semantic self-certification.

Agent B candidate lane:

- recovery/uncertainty state model;
- persisted journal reconstruction and chain-resume rules;
- deterministic validation-plan compiler/receipt verification if required by the frozen native contract;
- invariant tests independent of AutoCAD.

Agent C integration lane:

- reject incompatible request/receipt semantics before merge;
- inject post-dispatch/commit/process/restart faults;
- prove exact predecessor or exact accepted post-state before allowing continuation;
- leave the gate OPEN if R1/R2/read-back cannot be independently proven.

### Wave 2 — Semantic extraction scalability

Agent A:

- bounded pagination/streaming or another frozen transport-safe snapshot strategy;
- additional spaces/layouts and supported native entity/resource extraction;
- topology/event capture only when deterministic and testable.

Agent B:

- multi-page/scope fingerprint composition;
- semantic-scope/delta validation across expanded snapshots;
- topology normalization/rules only for fields actually emitted by native extraction.

Agent C:

- large-DWG boundary tests, ordering stability, pagination completeness, save/reopen and response-size fault tests;
- reject any “whole drawing” claim until all layouts/spaces intended by the contract are proven.

### Wave 3 — Operation-family migration

Migrate one native operation family per sub-wave instead of adding many endpoints at once.

Examples of candidate families after explicit prioritization:

```text
polylines / transforms
layers / styles
text / blocks
dimensions / hatch
layouts / plotting
3D / advanced operations
```

Agent A implements the typed native family; Agent B implements ActionSpec/delta/validation semantics; Agent C performs COM-vs-.NET normalized parity plus rollback/drift/fault acceptance. Each family receives a separate evidence/commit boundary and can be rejected independently.

### Wave 4 — Real drawing stress test / promotion readiness

Agent A owns native runtime defects only; Agent B owns semantic/source-model defects only; Agent C drives the clean-document stress test and acceptance ledger.

Required before any promotion proposal:

- one state-chain entry per accepted semantic step;
- intentional bad step caught at the exact step;
- rollback/recovery proves a known state;
- manual drift caught;
- file/checkpoint integrity proven;
- source Data-vs-Data gates pass;
- final visual/drafting-quality gate remains separate from semantic correctness;
- public contract/tool count changes only by explicit version/promotion decision.

## 6. Agent handoff contract

Every A/B candidate handoff to Agent C must contain:

```text
base_sha
author_candidate_sha
contract_sha
files_changed
gate_claimed
out_of_scope
focused_tests + exact results
known_failures / warnings
native_build hash (Agent A when applicable)
required acceptance scenario
rollback/uncertainty expectations
```

Agent C records the exact candidate SHA in evidence. “Tested latest” is not acceptable because the branch may move after acceptance.

## 7. Conflict prevention

Do not parallelize work merely by assigning more agents. Parallel execution is allowed only when file ownership and dependency direction are clear.

Hard conflict rules:

- A and B never edit the same source file in one wave;
- neither A nor B updates `CURRENT_CHECKPOINT.md` while implementation is in flight;
- C never silently patches production code to make acceptance pass;
- protocol changes stop both lanes and create a new contract freeze;
- native binary deployment always references an exact reviewed build hash;
- runtime acceptance never uses screenshots as semantic proof;
- failed/uncertain native state stops that acceptance drawing; do not blindly retry.

## 8. Review / merge gates

For each candidate wave:

```text
A focused tests/build PASS ─┐
                            ├─> C integration review
B focused tests PASS ───────┘
                                   |
                                   v
                         merged candidate in C worktree
                                   |
                    Linux + Windows regression PASS
                                   |
                      real AutoCAD native acceptance
                                   |
                     canonical evidence + doc review
                                   |
                    checkpoint commit -> push main
```

If C finds a production defect, the candidate returns to A or B. C does not preserve a “green” acceptance by weakening checks or converting failures to skips.

## 9. Recommended concurrency level

Use at most **two simultaneous production writers** (A + B). Agent C may review/read/run acceptance concurrently when it does not consume unstable working-tree state, but final integration begins only from immutable candidate commits.

This gives useful parallelism without creating three competing authors on shared architecture files. For heavy native work, A can build/test on `.171` while B runs platform-independent semantic TDD on `.227`; C can prepare/read acceptance matrices, then consume their frozen commits.

## 10. Stop / escalation rules

Stop the current wave immediately when:

- actual native state becomes uncertain;
- A/B discover the frozen contract is insufficient;
- a change requires both agents to edit the same core file;
- a new capability would broaden security/execution surface beyond the approved scope;
- regression reveals an unrelated repository defect that cannot be isolated;
- live acceptance requires weakening an invariant to pass.

Escalate the contract or scope first, then resume from a new explicit wave boundary.

## 11. Starting point when future implementation is authorized

Start from a clean `origin/main` containing the N6 closure and this plan. Re-read `AGENTS.md`, `docs/CURRENT_CHECKPOINT.md`, this plan and the N4–N6 audit. Create the three isolated worktrees, freeze the first future contract, then send A/B their non-overlapping task briefs. Agent C should receive the acceptance matrix before A/B begin so the completion criteria cannot drift to match the implementation afterward.

Historical boundary at plan creation: **do not open N7, broaden the native mutation allowlist, or promote the staged bridge without explicit authorization.** That authorization later arrived for O1 and N7. This historical plan does not override the current implementation boundary; use `docs/CURRENT_CHECKPOINT.md` for live status. Public promotion remains separately gated and has not occurred.
