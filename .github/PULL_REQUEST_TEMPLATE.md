## Scope

- What changed?
- Which backend/runtime/capability tuple is affected?

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
- [ ] `CHANGELOG.md` / `CURRENT_CHECKPOINT.md` updated when current truth changes
- [ ] Historical evidence was not rewritten to match a newer identity
