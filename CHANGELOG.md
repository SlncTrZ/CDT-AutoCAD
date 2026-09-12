# Changelog

All notable product and operational changes to CDT-AutoCAD are recorded here. Historical evidence files remain authoritative for the exact test conditions of their original checkpoints.

## [Unreleased]

### Planned

- Production-driven expansion of typed native CAD actions only where real Domain workflows require them.
- Continued COM-to-native parity work under the existing Semantic State and recovery invariants.

### Maintenance checkpoint — 2026-09-12 (`31186f5`)

- Native bridge maintenance candidate advanced to `0.8.2-mp7` without changing provider `0.4.0rc1`, contract `autocad-generic-v1-rc1` or the 86-tool public catalog.
- Added typed Managed .NET visual-style state read/restore with exact handle read-back and drift guard.
- Added canonical Session-1 acceptance lifecycle wrapper and current-identity MP-2 `20 success + 10 injected failure` rerun.
- Added 10/100-part ACIS soak/adversarial acceptance and destructive Boolean uncertainty quarantine coverage.
- Hardened XREF/source/artifact/solid provenance and cleanup paths covered by the maintenance test set.
- Fixed a live `RPC_E_CALL_REJECTED` viewport regression by routing viewport create/read/scale/lock/delete through the shared bounded COM-busy primitives.
- Final maintenance gates: Linux **365 passed / 6 skipped**, Windows `.171` **364 passed / 7 skipped**, targeted live suite **5/5 twice consecutively**, C# Release/x64 **0 errors / 3 inherited warning families**. Ruff was unavailable in the prepared Linux environment and is not claimed as PASS.


## [0.4.0rc1] - 2026-09-12

### Operational baseline

- CDT-AutoCAD begins operational use on 2026-09-12 as an **RC/preview** product; this is not a GA/stable-version declaration.
- Public contract: `autocad-generic-v1-rc1`.
- Public MCP surface: 86 tools.
- Production orchestration model: `feature-based-chunks-streaming-v1`.
- Primary certified live lane: AutoCAD 2027 full / Windows x64.

### Added

- Operational repository baseline: security/support policy, operations runbook, release checklist, documentation index, CODEOWNERS, pull-request template and headless GitHub CI.
- Package metadata now carries the project owner, proprietary license reference, README and repository/documentation URLs.
- Feature-based Chunks Streaming with feature-local predecessor checkpoints and verified rollback.
- Generic native batch create, persistent-PID block insertion and PID-targeted transforms.
- Schema-agnostic bounded entity metadata participating in document fingerprint schema v3.
- Managed .NET native bridge line `0.8.1-g3` with bounded native chunks of at most 32 entities.
- Content-addressed accepted-artifact sealing and SAT export provenance.
- Public-safe synthetic marketing integrity demo and runbook.
- Proprietary license, copyright record, source provenance, third-party notices and contribution provenance policy.

### Verified

- Operational-readiness maintenance gate: Linux 320 passed / 5 skipped, Windows `.171` 319 passed / 6 skipped, Ruff clean, wheel build/metadata PASS and MP-2 `1 success + 1 injected failure + recovery` smoke PASS.
- Live scale graduation at 100 / 1,000 / 5,000 / 10,000 entities.
- Exact predecessor recovery under beginning/middle/end injected failures with zero pending recovery at accepted scale gates.
- Feature-stream live acceptance: prior committed features remain intact when the current feature rolls back, and later work can continue from the verified predecessor.
- Final promotion gates: Linux 316 passed / 5 skipped; Windows `.171` 315 passed / 6 skipped; C# Release/x64 0 errors with 3 documented inherited Autodesk-reference warning families.

### Fixed

- MP-2 standalone acceptance fixture now includes the bounded command-preset registry required by worker startup.
- MP-2 acceptance runner uses canonical `PUBLIC_TOOL_COUNT` instead of stale 50-tool assertions and binds loop-state predicates safely.
- Existing safe Ruff findings were cleaned without changing the public contract; intentional contract-mixin/string-enum compatibility exceptions are documented in scoped Ruff configuration.

### Notes

- Domain semantics and design judgment remain outside CDT-AutoCAD.
- The broader live product surface still includes COM; public native promotion is capability-specific rather than a claim that every tool uses the G3 native route.
- Deterministic face/edge topology, STEP/STL export, and typed edge fillet/chamfer/shell remain outside the current verified public claims.
