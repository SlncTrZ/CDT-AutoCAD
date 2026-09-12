# Release Checklist — CDT-AutoCAD

Use this checklist for any public contract/version change or any change that materially expands live CAD authority.

## 1. Scope and identity

- [ ] Change scope is explicit: docs-only, headless, COM, native bridge, recovery, transport, packaging, or public contract.
- [ ] Provider version, contract version, execution model and public tool count are intentionally chosen.
- [ ] `README.md`, `CURRENT_CHECKPOINT.md`, `TOOL_GUIDE.md` and runtime `help` agree.
- [ ] `CHANGELOG.md` records the release/operational change.
- [ ] Historical evidence/handoffs are not rewritten to pretend they ran under the new identity.

## 2. Security and provenance

- [ ] No credential, `.env`, private DWG or customer/project artifact is staged.
- [ ] New dependency licenses are reviewed and `THIRD_PARTY_NOTICES.md` is updated when required.
- [ ] No external implementation source is copied/adapted without owner approval and provenance/license documentation.
- [ ] Materially new authority has a `THREAT_MODEL.md` delta and negative tests.
- [ ] No new arbitrary shell, AutoLISP, macro, C#, or caller-supplied free-text AutoCAD command surface is introduced.

## 3. Repository hygiene

- [ ] `git status` is understood; unrelated local work is excluded from staging.
- [ ] `git diff --check` passes.
- [ ] Python source compiles.
- [ ] Package metadata/build succeeds.
- [ ] Generated caches/build output remain ignored.
- [ ] `_private/`, `_test_workspace/`, local `artifacts/` and secrets remain untracked unless an explicit reviewed evidence publication requires otherwise.

## 4. Automated regression

- [ ] Full Linux/headless regression passes on the reviewed tree.
- [ ] CI-equivalent minimum-Python lane passes.
- [ ] Focused tests cover changed behavior and failure paths.
- [ ] Lint/static checks required by the current repository gate pass or an unavailable-tool gap is stated explicitly rather than converted to PASS.

## 5. Windows / AutoCAD live gate

Required for any change that depends on live AutoCAD behavior:

- [ ] Windows `.171` full regression passes on the reviewed tree.
- [ ] Primary AutoCAD 2027 / Windows x64 live acceptance passes for affected capability tuples.
- [ ] Managed C# bridge builds `Release/x64` with zero errors.
- [ ] Runtime reports the expected bridge/provider/contract identity.
- [ ] Document binding, PID/fingerprint and expected-parent protections remain intact.
- [ ] Failure/rollback/recovery evidence proves the required predecessor exactly.
- [ ] Pending recovery is zero after the accepted run.

## 6. Operational compatibility

- [ ] Supervisor/hot-reload changes preserve generation fencing, drain/refusal and last-healthy fallback.
- [ ] A supervisor/control-plane code change is tested with an actual supervisor restart; worker hot reload is not used as false evidence for replacing supervisor code.
- [ ] Allowed-path/auth/remote-bind defaults remain fail-closed.
- [ ] `OPERATIONS_RUNBOOK.md`, `SECURITY.md` and `SUPPORT.md` remain accurate for the release.

## 7. Review and publication

- [ ] Code/security review reports no unresolved blocking finding.
- [ ] Stage only reviewed files.
- [ ] Commit message describes the actual release/operational change.
- [ ] Push `main` and verify `HEAD == origin/main`.
- [ ] Record the final commit, gate results and known limitations in CyberBrain Knowledge.
- [ ] Store the session episode, then enqueue Dreaming.

## 8. Current RC baseline reference

The `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools` promotion was closed at commit `0516fe3` after Linux 316/5, Windows `.171` 315/6, C# Release/x64 0-error and focused review gates. Later legal/operational commits do not retroactively change that promotion evidence.
