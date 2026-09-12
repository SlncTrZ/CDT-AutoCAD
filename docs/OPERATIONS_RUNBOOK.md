# Operations Runbook — CDT-AutoCAD

> Operational start: 2026-09-12
> Current product identity: `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools`
> Primary live lane: AutoCAD 2027 full / Windows x64 / Managed .NET `net10.0-windows`

## 1. Operating boundary

CDT-AutoCAD is operating as an **RC/preview Generic CAD Execution Engine**. Operational use does not convert the version into GA or expand capability claims beyond current evidence.

The provider owns generic CAD execution, persistent identity, semantic state, bounded mutation, recovery and artifact evidence. Domain standards, engineering calculations and design judgment remain outside the provider.

## 2. Preflight before live mutation

Before starting a production/live session:

1. AutoCAD 2027 is already running in the intended interactive Windows user/session.
2. The required Managed .NET bridge is loaded and reports ready.
3. `CDT_AUTOCAD_BACKEND=com`.
4. `CDT_AUTOCAD_COM_PROGID=AutoCAD.Application.26`.
5. `CDT_AUTOCAD_COM_ATTACH_POLICY=attach_only` unless an explicitly reviewed workflow requires otherwise.
6. `CDT_AUTOCAD_ALLOWED_PATHS` contains only approved working roots.
7. HTTP/supervisor transport has a non-empty `CDT_AUTOCAD_AUTH_TOKEN`.
8. Non-loopback HTTP remains disabled unless `CDT_AUTOCAD_ALLOW_REMOTE_HTTP=true` was explicitly approved.
9. No unresolved timeout uncertainty or pending recovery is present.
10. The intended DWG/document is identified before the first mutation.

Never store real auth tokens in the repository.

## 3. Start modes

### Stdio

```text
set CDT_AUTOCAD_BACKEND=com
set CDT_AUTOCAD_COM_PROGID=AutoCAD.Application.26
set CDT_AUTOCAD_COM_ATTACH_POLICY=attach_only
cdt-autocad --transport stdio
```

### Supervised HTTP / hot reload

Set the same COM environment plus `CDT_AUTOCAD_AUTH_TOKEN`, then run:

```text
cdt-autocad-supervisor --host 127.0.0.1 --port 8000 --repo-root H:\Develop\CDT-AutoCAD --runtime-dir H:\Develop\CDT-AutoCAD\.runtime\hot-reload --require-bridge
```

The supervisor refuses unauthenticated HTTP startup. Binding beyond loopback also requires the explicit remote-HTTP opt-in.

Worker-source changes may hot reload through MP-2. Changes to the supervisor/control plane itself require a deliberate supervisor restart; do not assume the supervisor can replace its own running code.

## 4. Health gate

Run these before mutation and after any reload/recovery event:

1. `system_status`
2. `system_capabilities`
3. `native_integrity_status`
4. `document_info` after binding/opening the intended document

Required live-mutation conditions include:

- provider/contract identity matches the expected operational baseline;
- runtime/backend is ready;
- native route is ready for tools that require it;
- transaction depth is zero before starting a new independent operation;
- timeout uncertainty is false;
- pending recovery count is zero;
- runtime document identity and document PID correspond to the intended DWG.

If any required state is unknown, stop and reconcile; do not downgrade silently to weaker validation.

## 5. Normal mutation workflow

Preferred production sequence:

1. bind/open the intended document;
2. configure units/layers/resources;
3. execute one meaningful logical feature through `feature_execute`;
4. require `COMMITTED_VERIFIED` or the documented successful receipt for that public surface;
5. inspect/query/measure the result;
6. use the recommended 300 ms delay between completed visual features only when presentation pacing is desired;
7. save at meaningful checkpoints;
8. seal accepted artifacts when a durable content-addressed checkpoint is required.

A feature failure must remain feature-local. Earlier committed features must not be discarded merely because the current feature fails.

## 6. Incident and recovery rule

Immediately stop dependent mutations when any of these occurs:

- completion is unknown after timeout/cancel/disconnect;
- state becomes `STATE_UNCERTAIN`, `ROLLBACK_FAILED`, `COMMIT_INTEGRITY_FAIL` or equivalent;
- pending recovery becomes non-zero;
- document identity/fingerprint drifts from the expected parent;
- bridge/runtime generation cannot be verified;
- artifact/journal/checkpoint integrity is uncertain.

Do **not** blindly retry a mutation after unknown completion.

Preserve:

- the current DWG and any prior accepted sealed artifact;
- recovery journal/checkpoint files;
- provider/supervisor worker logs;
- request/correlation IDs;
- `system_status` / `native_integrity_status` snapshots;
- exact provider, contract, bridge and AutoCAD versions.

Reconcile through authoritative semantic read-back and the recovery procedures defined by `SEMANTIC_STATE_PROTOCOL.md` and the maintainer-internal native bridge acceptance record. Telemetry alone never proves commit or rollback.

## 7. Save and artifact policy

For important drawing steps:

- save after accepted semantic features/checkpoints;
- use `artifact_seal` for accepted deliverable checkpoints when provenance matters;
- retain the SHA-256 and exact source/version identity with evidence;
- never overwrite the only known-good artifact during recovery experiments;
- run destructive/fault-injection work only on disposable fixtures.

## 8. Logs and evidence

`.runtime/`, local `artifacts/`, `_private/` and `_test_workspace/` are intentionally local/ignored operational areas. They are not publication targets by default.

Canonical reviewed acceptance evidence is retained in the ignored maintainer workspace (`_private/evidence/`) and must never be swept into a public/product commit. Structured diagnostics are operational evidence, not recovery authority; see `OBSERVABILITY.md`.

## 9. Verification environment

For the shared Windows workspace, run the Windows regression with the repository `src` directory on `PYTHONPATH` and let the Windows virtual environment supply compiled dependencies:

```text
$env:PYTHONPATH='H:\Develop\CDT-AutoCAD\src'
C:\Users\truon\.venvs\CDT-AutoCAD\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Do **not** add the shared Linux `.deps` directory to Windows `PYTHONPATH`; it contains Linux-native wheels and will produce false NumPy/Pydantic import failures on Windows.

Linux/headless verification may use the Linux venv/lock or a clean CI environment. The repository CI intentionally installs its own platform-correct dependencies instead of reusing `.deps`.

## 10. Maintenance

Before publishing a product change:

- follow the maintainer-internal release checklist;
- update `CHANGELOG.md` and `CURRENT_CHECKPOINT.md` when current truth changes;
- rerun the exact gates required by the changed scope;
- perform live Windows/AutoCAD acceptance for any capability that requires AutoCAD;
- update threat-model deltas for materially new risky authority;
- keep dependency locks synchronized with intentional dependency changes.

Operational issues and security reports follow `SUPPORT.md` and `SECURITY.md`.

## 11. Shutdown

For supervised operation, stop accepting new work, allow/fence in-flight work according to MP-2 semantics, then terminate the supervisor normally. Do not kill AutoCAD or provider processes during a mutation unless the recovery test explicitly requires process failure and the drawing is disposable.

Before closing a production drawing, verify the last accepted save/seal and ensure pending recovery is zero.
