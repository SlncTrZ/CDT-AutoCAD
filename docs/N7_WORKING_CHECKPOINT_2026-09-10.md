# N7 Working Checkpoint — Two-Phase Native Commit Integrity

> Updated: 2026-09-10 +07:00
> Status: **IN PROGRESS / NOT CLOSED / NOT COMMITTED AS IMPLEMENTATION**
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

These are development results only. N7 is not closed because the complete final acceptance run is not yet stable.

## 3. Current blocker — R2 document lifecycle crash

The current final blocker is a real AutoCAD process crash in the R2-after-restart path. During final live acceptance AutoCAD displayed `FATAL ERROR: Unhandled Access Violation` while the recovery implementation was closing/restoring the active document from bridge application context.

The unsafe R2 sequence is therefore rejected even though earlier R2 semantic-fingerprint checks could pass. N7 may not close while that lifecycle can crash `acad.exe`.

The next implementation change is to make R2 document replacement activation-safe:

1. open the immutable checkpoint as a temporary AutoCAD document;
2. wait until that temporary document is active;
3. close the original document only after it is inactive;
4. replace the original DWG bytes from the checkpoint artifact;
5. open the restored original path and wait until it is active;
6. close the temporary checkpoint document only after it is inactive;
7. resolve the new runtime-document binding;
8. verify restored document PID and exact predecessor `document_fp` before returning `ROLLED_BACK_VERIFIED`.

Any activation/close/reopen ambiguity must return failure and retain the recovery checkpoint; it must not be converted to success by retry loops.

## 4. Checkpoint capture findings

Checkpoint creation originally dirtied a clean drawing because full-database clone/save paths changed AutoCAD DBMOD. The candidate now uses AutoCAD `Document.PushDbmod()` / `PopDbmod()` around technical checkpoint creation and explicitly verifies that source DBMOD and source database path do not change.

`Database.Wblock()` was also rejected as the canonical R2 artifact because it did not reproduce the exact semantic predecessor and initially omitted the document-lineage PID from the checkpoint database. The current candidate uses a full `Database.SaveAs()` checkpoint under DBMOD preservation, with source-path and artifact-existence verification.

## 5. N7 close gate still outstanding

Before N7 can be called CLOSED, all of the following must pass on one final reviewed tree:

- activation-safe R2 with no AutoCAD crash;
- R0, provisional-abort, post-commit mismatch, R1 and R2 live acceptance;
- R2 exact restore after real AutoCAD process restart;
- PID/fingerprint/runtime-binding verification after restore;
- corrupt-manifest and artifact-mismatch fail-closed tests;
- no pending recovery checkpoint after successful finalize/restore;
- Python focused + full Linux + full Windows regression;
- changed-file Ruff, compileall and `git diff --check`;
- C# bridge build with 0 errors;
- code review with no unresolved blocking findings;
- canonical `docs/evidence/n7-...json` evidence;
- CURRENT_CHECKPOINT / architecture / acceptance docs updated to the final measured result;
- separate N7 implementation commit + push.

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
