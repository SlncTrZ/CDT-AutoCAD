# Changelog

All notable product and operational changes to CDT-AutoCAD are recorded here. Historical evidence files remain authoritative for the exact test conditions of their original checkpoints.

## [Unreleased]

### Planned

- Production-driven AutoCAD capability expansion only where a concrete CDT-Engineer/Domain workflow requires it and defines a verification invariant.
- No standing COM-to-native parity program; existing COM/headless/native routes remain intentional according to their documented assurance scope.


## [0.4.0rc2] - 2026-09-16

### Changed

- Closed B1 filesystem containment at implementation checkpoint `abeb9d9` under an explicit split-assurance threat model: provider-owned file I/O now uses descriptor/handle-bound actual-I/O primitives with adversarial descendant namespace-swap coverage on Linux and Windows; AutoCAD pathname-only COM operations remain bounded by pre/post verification and are not advertised as race-free. Closure tree measured Linux **405 / 11 skipped** with Ruff PASS and Windows `.171` **404 / 12 skipped**.
- Closed B2 reproducible release proof at clean checkpoint `bcb5c66`: two fresh exact-lock reconstructions per platform reproduce the same Git/source/package identity and pass full regression + Ruff; Linux lock SHA-256 `e7fe668b…bf06`, Windows lock SHA-256 `acbbc28b…869e`, common source-tree SHA-256 `3f4b45b1…db3d8`.
- Closed B3 repo/tooling governance: `uv.lock` is explicitly non-canonical ignored residue, the two platform `pylock.*` files are the sole exact dependency authority, Ruff is repeatable on Linux + Windows locked environments, and the release tree is clean.
- Declared CDT-AutoCAD launch-ready for its Generic CAD Execution Engine mission; no known top-level AutoCAD blocker remains before CDT-Engineer work begins.
- Standardized documentation authority so provider architecture, current status, live evidence, roadmap, technical debt and session notes no longer compete as sources of truth.
- Added `docs/ARCHITECTURE.md` as the canonical provider-local architecture authority; `specs/**` remains a frozen upstream CDT-Engineer baseline.

### Maintenance checkpoint — 2026-09-12 (`31186f5`)

- Native bridge maintenance candidate advanced to `0.8.2-mp7` without changing provider `0.4.0rc1`, contract `autocad-generic-v1-rc1` or the 86-tool public catalog.
- Added typed Managed .NET visual-style state read/restore with exact handle read-back and drift guard.
- Added canonical Session-1 acceptance lifecycle wrapper and current-identity MP-2 `20 success + 10 injected failure` rerun.
- Added 10/100-part ACIS soak/adversarial acceptance and destructive Boolean uncertainty quarantine coverage.
- Hardened XREF/source/artifact/solid provenance and cleanup paths covered by the maintenance test set.
- Fixed a live `RPC_E_CALL_REJECTED` viewport regression by routing viewport create/read/scale/lock/delete through the shared bounded COM-busy primitives.
- Final maintenance gates: Linux **365 passed / 6 skipped**, Windows `.171` **364 passed / 7 skipped**, targeted live suite **5/5 twice consecutively**, C# Release/x64 **0 errors / 3 inherited warning families**. Ruff was unavailable in the prepared Linux environment and is not claimed as PASS.

### Integrity / contract

- Closed B0 document provenance gaps: `document_save_as` verifies the bound document's canonical post-SaveAs path before success, and artifact sealing re-binds the same saved source before hashing/copy/manifest acceptance.
- Closed B4 caller-state binding at implementation checkpoint `872da68`: `feature_execute`, `batch_create_entities`, `batch_insert_blocks`, `batch_transform_entities` and `metadata_set` now require caller-supplied `document_pid` + `expected_parent_fp`.
- Wrong-document and stale-parent requests fail before logical journal/checkpoint creation or CAD mutation; the caller predecessor remains the native executor drift guard after binding.
- Public identity advanced intentionally to provider `0.4.0rc2` / contract `autocad-generic-v1-rc2`; public tool count remains 86 and the execution model remains `feature-based-chunks-streaming-v1`.

### Verification

- Focused public native/schema regression: **32/32 passed**.
- Full Linux regression: **398 passed / 9 skipped**.
- Full Windows `.171` regression: **397 passed / 10 skipped**.
- B0 AutoCAD 2027 Session-1 SaveAs + artifact-seal closure: **2/2 passed**.
- B4 AutoCAD 2027 Session-1 disposable-DWG acceptance: wrong document PID, stale parent and stale replay refused with zero mutation; valid caller predecessor committed and independently read back.
- C# bridge source did not change; accepted native bridge remains `0.8.2-mp7`. Ruff was unavailable in the prepared Linux/Windows environments and is not claimed as PASS.


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
