# Session Handoff — Feature-based Chunks Streaming / Public Promotion Candidate

> Date: 2026-09-11 20:30 +07:00
> Branch: `main`
> Repository: `/mnt/pc-dev/CDT-AutoCAD`
> State: **dirty working tree · no commit/push for the current G2/G3/Feature Streaming/public-promotion work**
> Current-state authority: `docs/CURRENT_CHECKPOINT.md`
> Architecture decision: `docs/ADR-002-GENERIC-CAD-EXECUTION-ENGINE.md`

## 1. Why this handoff exists

This session became long while closing production findings, G2/G3, scale graduation and the Production Domain execution model. The native architecture work itself is now live-proven; the remaining work is primarily final public-contract migration/regression/review/release hygiene.

**Do not restart G2/G3/Feature Streaming from scratch. Do not call the public promotion closed until the final regression/review/commit gates pass.**

## 2. Accepted architecture

CDT-AutoCAD is a **Generic CAD Execution Engine**.

Domain Agents own:

- engineering/business rules;
- TCVN/ISO/ASME/customer standards;
- engineering calculations and design intent;
- domain constraints;
- engineering audit/report generation.

CDT-AutoCAD owns:

- typed generic CAD operations;
- document/runtime binding;
- persistent document/entity PID;
- canonical semantic fingerprints;
- bounded native execution;
- schema-agnostic metadata;
- validation/recovery primitives;
- AutoCAD lifecycle/runtime state.

Do not introduce domain concepts into the provider contract. `feature_id` is correlation metadata only.

## 3. Production execution model — APPROVED

**Feature-based Chunks Streaming** is the production orchestration model.

```text
Feature 1 -> native micro-chunks -> verify -> commit
300 ms presentation pacing
Feature 2 -> native micro-chunks -> verify -> commit
300 ms presentation pacing
Feature 3 -> chunk failure -> restore Feature 3 predecessor only
Feature 1 + Feature 2 remain committed
Domain Agent recomputes/retries Feature 3
Feature 4 -> continue
```

Important distinction:

- **native micro-chunk**: technical bounded transaction, max 32 entities, one batch mutation per AutoCAD Idle tick;
- **feature**: caller-defined logical unit, up to the current 10,000-item contract, owns one predecessor checkpoint;
- **drawing workflow**: sequence of committed features; a later feature failure does not roll back earlier features.

Default showcase pacing is **300 ms between completed features**, never 300 ms between native micro-chunks.

3D showcase convention remains **SE Isometric + Shades of Gray**.

## 4. Native implementation checkpoint

Current native candidate:

```text
bridge_version                 0.8.1-g3
protocol                       cdt-autocad-native-v1
document_fp_schema_version     3
max native chunk               32
semantic capacity              12,288
logical feature/batch max      10,000
batch_yield_per_idle           true
cross_chunk_atomic             false
logical_batch_atomic           true
```

`cross_chunk_atomic=false` describes the native micro-transaction primitive. G3 provides higher-level logical/feature atomicity through one immutable predecessor checkpoint and exact recovery.

### G2 — CLOSED / LIVE PASS

Implemented:

- `metadata.get` / `metadata.set` / `metadata.query`;
- ExtensionDictionary/XRecord storage;
- schema-agnostic namespaced JSON;
- provider-owned namespace reservation;
- bounded size/depth/key/string/native-decimal/query scan work;
- metadata included in document fingerprint schema v3;
- independent metadata/document-state read-back before checkpoint finalization;
- R0/R1/R2 recovery paths and unknown-completion checkpoint discovery.

### G3 — CLOSED / LIVE PASS

Implemented:

- one immutable predecessor checkpoint for a logical operation;
- many short native transactions with AutoCAD Idle yield;
- independent final `bridge.document.state` read-back;
- exact R2 predecessor recovery after chunk failure or uncertain completion;
- begin-completion and finalize-response reconciliation;
- no giant long-lived AutoCAD transaction across the feature.

### Scale graduation — CLOSED / LIVE PASS

Real AutoCAD 2027 Session 1 evidence:

- 100 — PASS;
- 1,000 — PASS;
- 5,000 — PASS;
- 10,000 — PASS.

Each tier includes beginning/middle/end failure injection with exact predecessor restoration and zero pending recovery. The 10,000-entity accepted run used 313 native chunks, produced 10,000 unique persistent PIDs and preserved the same AutoCAD/bridge process identity.

Canonical evidence:

```text
docs/evidence/g23-live-2026-09-11.json
docs/evidence/g3-scale-100-2026-09-11.json
docs/evidence/g3-scale-1000-2026-09-11.json
docs/evidence/g3-scale-5000-2026-09-11.json
docs/evidence/g3-scale-10000-2026-09-11.json
```

## 5. Feature-based Chunks Streaming implementation

Implemented files:

```text
src/cdt_autocad/native_bridge/feature_stream.py
src/cdt_autocad/native_bridge/public_runtime.py
src/cdt_autocad/server.py
scripts/run_feature_stream_acceptance.py
tests/test_feature_streaming.py
tests/test_native_public_surface.py
```

Public candidate tool:

```text
feature_execute(feature_id, feature_sequence, correlation_id, actions)
```

Current generic action families inside one feature:

- `create_entities`;
- `insert_blocks`;
- `transform_entities`.

Receipt carries:

- feature ID/sequence/correlation;
- pre/post document fingerprints;
- action count;
- native chunk count;
- affected semantic PIDs;
- failure action/native-chunk index;
- journal path;
- recommended next delay.

Live feature acceptance PASS:

- baseline semantic count 4;
- Feature 1 committed 40 entities -> count 44;
- configured 300 ms pause measured **300.295 ms**;
- Feature 2 committed its first micro-chunk, failed at native chunk index 1, then restored exactly to Feature-1 fingerprint/count 44;
- Feature 1 remained intact;
- Feature 3 committed 8 entities -> count 52;
- final pending recoveries = 0.

Evidence: `docs/evidence/feature-stream-production-2026-09-11.json`.

## 6. Public promotion candidate — NOT YET CLOSED

Current dirty-tree identity:

```text
provider_version     0.4.0rc1
contract_version     autocad-generic-v1-rc1
public MCP tools     86
execution_model      feature-based-chunks-streaming-v1
```

The FastMCP candidate catalog was measured at 86 tools after `feature_execute` was added.

Strong-integrity public candidate tools:

```text
native_integrity_status
feature_execute
batch_create_entities
batch_insert_blocks
batch_transform_entities
metadata_get
metadata_set
metadata_query
```

A3 dimensions/analysis/ACIS/view/XREF/artifact-related public product work is also represented in the current broad promotion diff.

Last full Linux run before all stale contract assertions were migrated:

```text
308 passed / 5 skipped / 8 failed
```

The eight failures were identified as stale historical expectations for the 50-tool contract, staged A3 flags and document fingerprint v2. Several migration tests were edited afterward. **The full suite has not yet been rerun after the latest edits.**

Focused gates already green during this work included:

- G3 reconciliation tests: 11/11 PASS;
- native/public focused set before latest promotion edits: 94/94 PASS;
- Feature Streaming focused tests: 4/4 PASS;
- public facade feature test: PASS;
- C# Release/x64 builds: 0 errors, with the three inherited Autodesk `MSB3277` warning families still documented.

Do not convert these focused results into a final full-regression claim.

## 7. Documentation updated before handoff

Updated to current truth:

```text
README.md
docs/TOOL_GUIDE.md
docs/CURRENT_CHECKPOINT.md
docs/ROADMAP.md
docs/ADR-002-GENERIC-CAD-EXECUTION-ENGINE.md
docs/ARCHITECTURE_UPGRADE_PLAN.md
docs/NATIVE_BRIDGE_ACCEPTANCE.md
docs/LIVE_ACCEPTANCE.md
docs/SEMANTIC_STATE_PROTOCOL.md
AGENTS.md
```

Historical evidence/handoff files intentionally retain old 50-tool/v2/staged statements when those were true at the original checkpoint. Do not rewrite historical evidence to pretend older runs used the new contract.

`docs/SPEC_BASELINE.md` and `specs/**` remain pinned historical/control-plane material and were not changed for this local promotion candidate.

## 8. Working-tree rules

The tree is intentionally dirty. Do not pull/reset/clean over it.

`git diff --check` passed immediately before this handoff.

Important exclusions for the eventual promotion commit:

```text
.gitignore       modified separately for _test_workspace/
_private/        benchmark findings/private planning
_test_workspace/ local destructive/live CAD benchmark workspace
specs/**         pinned snapshots; never edit/stage here
```

Do **not** use blind `git add .`. Stage the reviewed promotion files explicitly.

The `.gitignore` change is user-approved and should remain preserved but separate from the promotion commit unless the owner explicitly asks to include it.

## 9. Immediate next-session plan

1. Bootstrap `/mnt/pc-dev/CDT-AutoCAD` and read `AGENTS.md`, `docs/CURRENT_CHECKPOINT.md`, then this handoff.
2. Run `git status --short --branch`; do not pull/reset/clean the dirty tree.
3. Recompute FastMCP catalog/identity and prove exactly 86 tools + `0.4.0rc1 / autocad-generic-v1-rc1`.
4. Run the full Linux regression on the latest promotion-test migration.
5. Fix only genuine failures; distinguish stale historical assertions from runtime regressions.
6. Run the full Windows `.171` regression using the correct local venv/PYTHONPATH.
7. Build C# Release/x64; require 0 errors.
8. Run compile/hygiene + `git diff --check` + focused code/security review.
9. Reconcile any remaining current-status docs/help/contract-hash discrepancies.
10. Stage surgically, excluding `.gitignore`, `_private/`, `_test_workspace/`, `specs/**`.
11. Commit + push `main` only after all final gates are green.
12. Log final closure to CyberBrain after commit/push.

## 10. Do not reopen without evidence

Already live-proven and not the next task:

- N0–N7 / O1;
- MP-2 hot reload;
- G1 batch geometry;
- G2 metadata;
- G3 logical atomicity;
- 100/1k/5k/10k scale graduation;
- Feature-based Chunks Streaming semantics and 300 ms pacing acceptance.

If a regression proves a defect in one of these, fix the defect. Otherwise continue the public-promotion close gates.

## 11. Short message for the next session

CyberBrain closeout: Knowledge `11360943-af2e-4706-a09e-55040a77b47f`; Episode `2034a2ed-6ad5-4f85-a18d-e3cf7d7c6d2a`; Dream queue `80`.

> Dùng @SlncTrZ-MCP, bootstrap `/mnt/pc-dev/CDT-AutoCAD`. Đọc `AGENTS.md`, `docs/CURRENT_CHECKPOINT.md` và handoff này. Tiếp tục **final public-promotion close gates**: xác nhận 86 tools -> full Linux -> Windows `.171` -> C# Release -> review/hygiene -> stage chọn lọc -> commit/push. Không pull/reset/clean; không stage `.gitignore`, `_private`, `_test_workspace`, `specs/**`; không làm lại G2/G3/Feature Streaming nếu regression không chứng minh lỗi.
