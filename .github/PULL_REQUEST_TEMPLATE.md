## Scope

- What changed?
- Which backend/runtime/capability tuple is affected?
- Which documentation authority owns the truth changed by this PR?

## Verification

- [ ] Focused tests pass
- [ ] Full headless regression passes when applicable
- [ ] `git diff --check` passes
- [ ] Package/build metadata remains valid
- [ ] Windows/AutoCAD live verification attached when required

## Integrity / security

- [ ] No secrets, private DWGs, `_private/`, `_test_workspace/`, or local artifacts are included
- [ ] No external implementation source was copied/adapted without provenance + license review
- [ ] No arbitrary shell/AutoLISP/macro/C#/free-text AutoCAD command surface was introduced
- [ ] PID/fingerprint/document-binding/recovery invariants remain intact where relevant
- [ ] Threat-model delta added for materially new risky authority

## Contract / docs

- [ ] Public tool/version/contract changes are intentional and fully gated
- [ ] `docs/ARCHITECTURE.md` updated only when architecture/boundary/invariants changed
- [ ] `docs/CURRENT_CHECKPOINT.md` updated only when current public runtime/release truth changed
- [ ] `docs/LIVE_ACCEPTANCE.md` updated only when accepted evidence changed
- [ ] Roadmap/session notes were not copied into public architecture/status docs
- [ ] Historical evidence was not rewritten to match a newer identity
