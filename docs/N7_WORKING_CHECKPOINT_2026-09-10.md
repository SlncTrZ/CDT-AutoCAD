# N7 Working Checkpoint — Two-Phase Native Commit Integrity

> Updated: 2026-09-11 +07:00
> Status: **HISTORICAL HANDOFF · N7 CLOSED / LIVE PASS ON 2026-09-11**
> Baseline commit: `04ff820` (`Feat: expand native typed shape mutations`)
> Public MCP contract: unchanged at 50 tools / `0.3.0rc1`

## 1. Scope now open

N7 is now actively implemented over the closed N4–N6 + O1 foundation. The staged bridge candidate is `0.5.0-n7`; it remains internal and is not the public provider backend.

The N7 candidate introduces:

- provisional native semantic extraction inside the same write transaction;
- deterministic native validation before commit;
- commit followed by independent read-back and provisional-vs-persisted fingerprint comparison;
- provider-owned immutable DWG recovery checkpoints;
- typed recovery operations `bridge.recovery.list`, `bridge.recovery.resolve`, `bridge.recovery.finalize`;
- R0 transaction abort, deterministic R1 compensation and R2 checkpoint restore;
- persisted recovery manifest + checkpoint artifact SHA-256 binding;
- Python semantic-executor orchestration that attempts R1 then R2 after post-commit integrity/validation failure;
- fail-closed pending-recovery behavior; corrupt recovery manifests return `RECOVERY_MANIFEST_INVALID` and block new mutations;
- no arbitrary C#/AutoLISP/shell/free-text AutoCAD command surface.

## 2. Verification already achieved during N7 development

Focused Python evidence has reached:

- N7 recovery protocol/client contract: 10/10 PASS across the combined N7 contract suites;
- N7 + N6 focused recovery/state-chain regression: 45/45 PASS at an intermediate checkpoint;
- broader native/semantic focused regression: 86/86 PASS at an intermediate checkpoint;
- C# `CDT.AutoCAD.Bridge` candidate build on the AutoCAD 2027 SDK: 0 errors; the existing Autodesk product-reference `MSB3277` warning families remain unsuppressed baseline debt.

Real AutoCAD 2027 Session 1 development acceptance has independently exercised and passed, at least once in the current candidate line:

- exact R0 predecessor restoration;
- provisional-validation failure abort before commit;
- post-commit mismatch detection as `COMMIT_INTEGRITY_FAIL`;
- deterministic R1 exact predecessor restoration;
- R2 exact predecessor restoration with runtime-document rebinding;
- Python executor automatic R1→R2 recovery and continuation after verified restoration;
- persisted recovery manifest surviving AutoCAD restart while R1 in-memory context is intentionally unavailable;
- corrupt-manifest fail-closed behavior for both recovery listing and attempted new mutation.

These development results were superseded by the final 2026-09-11 closure run. Canonical closure evidence is `docs/evidence/n7-native-recovery-2026-09-11.json`.

## 3. Historical blocker — R2 document lifecycle crash — RESOLVED

The earlier final blocker was a real AutoCAD process crash in the R2-after-restart path while recovery closed/restored the active document from bridge application context. That unsafe sequence remains rejected.

The accepted implementation now uses one deferred recovery request across AutoCAD `Application.Idle` ticks:

1. open the immutable checkpoint as a temporary read-only AutoCAD document and request activation;
2. on the next Idle tick, prove the checkpoint document is active and the original is inactive before closing the original;
3. replace original DWG bytes from the checkpoint artifact and reopen the restored original;
4. request restored-original activation and defer again;
5. on the next Idle tick, prove the restored original is active and the checkpoint is inactive before closing the checkpoint;
6. resolve the rebound runtime-document identity;
7. verify restored document PID and exact predecessor `document_fp` before returning `ROLLED_BACK_VERIFIED`.

No bridge sleep/retry loop is used. Activation ambiguity returns `ROLLBACK_FAILED` and retains persisted recovery evidence. The final runner also proved the same R2 sequence after a real AutoCAD restart without a crash.

## 4. Checkpoint capture findings

Checkpoint creation originally dirtied a clean drawing because full-database clone/save paths changed AutoCAD DBMOD. The candidate now uses AutoCAD `Document.PushDbmod()` / `PopDbmod()` around technical checkpoint creation and explicitly verifies that source DBMOD and source database path do not change.

`Database.Wblock()` was also rejected as the canonical R2 artifact because it did not reproduce the exact semantic predecessor and initially omitted the document-lineage PID from the checkpoint database. The current candidate uses a full `Database.SaveAs()` checkpoint under DBMOD preservation, with source-path and artifact-existence verification.

## 5. N7 close gate — PASS

The final reviewed tree passed the close gate on 2026-09-11:

- activation-safe R2 with no AutoCAD crash: PASS;
- R0, provisional-abort, post-commit mismatch, R1 and R2 live acceptance: PASS;
- R2 exact restore after real AutoCAD process restart: PASS;
- PID/fingerprint/runtime-binding verification after restore: PASS;
- corrupt-manifest fail-closed and pending-recovery mutation blocking: PASS;
- no pending recovery checkpoint after successful restore: PASS;
- focused recovery/semantic/bridge regression: 40 PASS;
- full Linux: 227 PASS / 5 SKIP; full Windows: 226 PASS / 6 SKIP;
- changed-file Ruff, compileall and `git diff --check`: PASS;
- C# Release x64 bridge build: 0 errors; only the three historical `MSB3277` warning families remain;
- review: no unresolved blocking findings;
- canonical evidence: `docs/evidence/n7-native-recovery-2026-09-11.json`.

This document remains as the historical handoff/incident record; `docs/CURRENT_CHECKPOINT.md` is the current authority.

## 6. Mandatory Python MCP hot reload requirement

The Python MCP/provider runtime must gain a supported hot-reload lifecycle before the formal N8/N9/N10 migration is considered complete. This is now a project requirement, not an optional optimization.

Required properties:

- Python provider changes reload without manually restarting the public MCP endpoint;
- in-flight requests drain or fail deterministically rather than executing against mixed generations;
- one generation is authoritative at a time;
- health/status exposes runtime generation/build identity so a client can prove the reload landed;
- failed reload preserves or restores the previous healthy generation;
- authentication, allowed-path policy and public contract identity remain fail-closed across reload;
- reload does not implicitly promote staged native operations or change the 50-tool public surface;
- test/acceptance includes repeated edit→reload→health→request cycles and reload-failure recovery.

The Windows `.171` host has `cloudflared` available according to operator infrastructure. SSH access is available for inspection. Cloudflare tunnel/service configuration is **not yet audited or changed** in this checkpoint; any hostname, ingress, credential or service-unit change should be treated as a separate infrastructure action and documented before modification.

## 7. What has not started

- N8 formal COM-to-.NET/public operation migration: NOT STARTED.
- N9 drawing-workflow migration: NOT STARTED.
- N10 public contract/promotion decision: NOT STARTED.
- Python MCP hot reload: requirement accepted, implementation NOT STARTED in this checkpoint.

Use `docs/CURRENT_CHECKPOINT.md` as the current status authority and this file as the detailed handoff for unfinished N7 implementation.
