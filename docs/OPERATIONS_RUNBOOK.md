# Operations Runbook — CDT-AutoCAD

> Operational start: 2026-09-12
> Updated: 2026-10-10 +07:00
> Current released source: `0.4.2 / autocad-generic-v1 / 87 tools`. Running provider identity must be queried separately.
> Current native bridge candidate: `0.8.6-d18` · deployed DLL SHA-256 `13293B5E21F9E2B31A7B75D3EFFC107AB912F3C202AC70426CC65CBCB8C79070` · historical release `v0.4.0` (2026-10-07); current source release `v.0.4.2` (not evidence of native redeployment)
> Primary live lane: AutoCAD 2027 full / Windows x64 / Managed .NET `net10.0-windows`
> Live gates: certified-at `9c69430` + 2026-09-30 + AutoCAD 2027/Windows x64, extended by affected-scoped requal at `a3faa7b` + 2026-10-06 and AC-P02 requal at `a69289f` + 2026-10-07 (smoke, 320-scale, 10k-scale, U1, D15–D18, hot-reload — see LIVE_ACCEPTANCE) — pending is an operational stamp, not a use prohibition; deployment health checks still govern each host/session
> Operational incident OP-01 CLOSED 2026-10-07: AutoCAD PID 37688 Session 1, pipe present, backend port 8000 listen, health gates clean (tx=0, uncertainty=false, pending=0), no recovery required

## 1. Operating boundary

CDT-AutoCAD is released as a **stable product**. Its native execution scope remains restricted to measured builds; a stable tag does not prove an upgraded workstation or expand capability claims. Scoped technical recertification passed on 2026-09-30 for execution source `9c69430` (certified-at: `9c69430` + 2026-09-30 + AutoCAD 2027/Windows x64); a deployment must still pass health checks for its actual host/session. Writes are allowed on disposable fixtures; production-document writes default-refuse.

The provider owns generic CAD execution, persistent identity, semantic state, bounded mutation, recovery and artifact evidence. Domain standards, engineering calculations and design judgment remain outside the provider.

## 2. Preflight before live mutation

Before starting a production/live session:

1. AutoCAD 2027 is already running in the intended interactive Windows user/session.
2. The required Managed .NET bridge is loaded, reports ready and matches the bridge identity expected by the workflow; current D18 candidate is `0.8.6-d18`.
3. After creating a new empty drawing that has no provider lineage, call `native_document_identity_initialize` once. It is intentionally limited to an empty current space; D18 requires the bound document to remain active and keeps provisional PID write, PID readback and semantic extraction inside one native transaction that commits only after verification. Never use it to imply adoption of legacy/non-empty geometry.
4. Before any native strong-integrity write, call `native_integrity_status` and retain the caller-planned `document_pid` + `document_fp`; pass them to the write as `document_pid` + `expected_parent_fp`. Do not refresh them implicitly at dispatch if the workflow intended to mutate the earlier planned state.
5. `CDT_AUTOCAD_BACKEND=com`.
6. `CDT_AUTOCAD_COM_PROGID=AutoCAD.Application.26`.
7. `CDT_AUTOCAD_COM_ATTACH_POLICY=attach_only` unless an explicitly reviewed workflow requires otherwise.
8. `CDT_AUTOCAD_ALLOWED_PATHS` contains only approved working roots.
9. HTTP/supervisor transport has a non-empty `CDT_AUTOCAD_AUTH_TOKEN`.
10. Non-loopback HTTP remains disabled unless `CDT_AUTOCAD_ALLOW_REMOTE_HTTP=true` was explicitly approved.
11. No unresolved timeout uncertainty or pending recovery is present.
12. The intended DWG/document is identified before the first mutation.

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
cdt-autocad-supervisor --host 127.0.0.1 --port 8000 --repo-root <repo-root> --runtime-dir <repo-root>/.runtime/hot-reload --require-bridge
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

- provider/contract identity matches the expected operational baseline; after a bridge/provider upgrade, re-read the current schema-v3 predecessor before sending any stale fingerprint captured under the prior bridge;
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
7. save at meaningful checkpoints; both `document_save` and `document_save_as` succeed only after same-document/path verification and immediate persisted-clean readback (`Saved=true`, `DBMOD=0`), not merely command acknowledgement;
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

The Windows native pipe client uses overlapped I/O with a default 5-second connection budget and a separate 60-second budget shared by request write, response header and response body. Partial progress never renews the I/O deadline. Local cancellation drains the outstanding I/O before releasing buffers/handles; it does not prove that CAD work stopped. A post-connection deadline is completion-unknown and the shared mutation coordinator fences later COM/native writes until authoritative reconciliation. These transport budgets are implementation defaults, distinct from `CDT_AUTOCAD_COM_TIMEOUT`; no native-pipe environment override is advertised.

A historical checkpoint must not be removed merely to clear a health gate. For a confirmed disposable test fixture, obtain the owner's disposition, verify checkpoint/document bindings, retain byte-verified backups of manifest/checkpoint/original and a reversible path mapping, then retire only that fixture from live inventory. Re-read global pending recovery and health afterwards. Fixture retirement is not native rollback/finalization evidence; real unfinished work follows owner-bound semantic recovery.

Preserve:

- the current DWG and any prior accepted sealed artifact;
- recovery journal/checkpoint files;
- provider/supervisor worker logs;
- request/correlation IDs;
- `system_status` / `native_integrity_status` snapshots;
- exact provider, contract, bridge and AutoCAD versions.

Reconcile through authoritative semantic read-back and the recovery procedures defined by `SEMANTIC_STATE_PROTOCOL.md`; use `LIVE_ACCEPTANCE.md` plus retained machine evidence for accepted scope/evidence context. Telemetry alone never proves commit or rollback.

## 7. Save and artifact policy

For important drawing steps:

- save after accepted semantic features/checkpoints;
- use `artifact_seal` for accepted deliverable checkpoints when provenance matters;
- retain the SHA-256 and exact source/version identity with evidence;
- never overwrite the only known-good artifact during recovery experiments;
- run destructive/fault-injection work only on disposable fixtures.

## 8. Logs and evidence

Runtime directories, local artifacts, test workspaces and maintainer-only evidence are intentionally local/ignored operational areas. They are not publication targets by default.

Raw machine evidence must never be swept into a public/product commit. Published acceptance claims belong in `COMPATIBILITY.md` and `LIVE_ACCEPTANCE.md`; structured diagnostics are operational evidence, not recovery authority. See `OBSERVABILITY.md`.

## 9. Verification environment

On Windows, run the regression with the repository `src` directory on `PYTHONPATH` and let a platform-correct virtual environment supply compiled dependencies:

```text
$env:PYTHONPATH='<repo-root>\src'
<venv>\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

Do **not** add the shared Linux `.deps` directory to Windows `PYTHONPATH`; it contains Linux-native wheels and will produce false NumPy/Pydantic import failures on Windows.

Linux/headless verification may use the Linux venv/lock or a clean CI environment. The repository CI intentionally installs its own platform-correct dependencies instead of reusing `.deps`.

## 10. Maintenance

Before publishing a product change:

- follow the maintainer-internal release checklist;
- update the owning authority when truth changes: `ARCHITECTURE.md` for architecture/boundary/invariants, `COMPATIBILITY.md` for runtime support/assurance limits, and `LIVE_ACCEPTANCE.md` for accepted evidence;
- rerun the exact gates required by the changed scope;
- perform live Windows/AutoCAD acceptance for any capability that requires AutoCAD;
- update threat-model deltas for materially new risky authority;
- keep dependency locks synchronized with intentional dependency changes.

Operational issues and security reports follow `SUPPORT.md` and `SECURITY.md`.

## 11. Shutdown

For supervised operation, stop accepting new work, allow/fence in-flight work according to MP-2 semantics, then terminate the supervisor normally. Do not kill AutoCAD or provider processes during a mutation unless the recovery test explicitly requires process failure and the drawing is disposable.

Before closing a production drawing, verify the last accepted save/seal and ensure pending recovery is zero.

## 12. Split-host pilot

The control process can select the existing workstation adapter without local
COM imports. Keep both HTTP endpoints on loopback and carry the cross-host
channel through authenticated, encrypted transport.

On the native host, configure the normal COM/attach-only policy and approved
working roots, then run `cdt-autocad-agent --port <port> --token-file <private-file>
--session-id <interactive-session>`. Application startup remains a separate
owner action; restarting the listener does not restart AutoCAD. An optional
private `--stop-file` lets an interactive scheduler stop the agent gracefully
before replacing its task.

On the control host, set `CDT_AUTOCAD_BACKEND=com`,
`CDT_AUTOCAD_RUNTIME_ENDPOINT`, `CDT_AUTOCAD_RUNTIME_TOKEN_FILE` and
`CDT_AUTOCAD_RUNTIME_STATE_FILE`, then launch the normal MCP entrypoint.
The first authenticated heartbeat pins the agent generation. A durable
in-flight marker fences mutation across provider restart; agent replacement
does not silently adopt a new generation. Identity tools remain reachable
during dependency outages.

Before rebinding or clearing an uncertain marker, retain its bytes and verify
the intended document, native predecessor/post-state, recovery inventory and
actual agent generation. Record the operator's reconciliation; never replay
the original mutation as a recovery procedure. Historical disposable-fixture
checkpoints follow the owner-disposition rule in section 6.

This launch mode does not close native R3/R4, change preferred placement, or
retire the local/legacy path without their separate live acceptance.
