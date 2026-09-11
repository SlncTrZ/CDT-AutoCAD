# Session Handoff — MP-2 Python MCP Hot Reload

> Date: 2026-09-11 +07:00
> Historical handoff status: **SUPERSEDED — MP-2 CLOSED / LIVE PASS on 2026-09-11**
> This file preserves the implementation checkpoint that existed before closure; current authority is `docs/CURRENT_CHECKPOINT.md` and canonical closure evidence is `docs/evidence/mp2-hot-reload-2026-09-11.json`.
> Accepted Git baseline before MP-2: `7304687` (`Feat: close N7 native recovery lifecycle`)
> Public runtime remains: provider `0.3.0rc1` · contract `autocad-a2-v1-rc1` · exactly 50 MCP tools · COM/ezdxf routing unchanged
> Canonical KB checkpoint: `b8f2b5f9-ec08-456c-9687-6c3959418e84`

## 1. Why this handoff exists

The session became long while implementing the mandatory MP-2 runtime gate. N7 is already closed/live-passed; **do not reopen N7 unless a regression proves a new defect**. The next session should continue MP-2 from the current dirty working tree and must not describe hot reload as accepted until the remaining process/Windows gates pass on one reviewed tree.

## 2. Current repository state

Branch authority at handoff:

```text
main == origin/main at 7304687
```

The MP-2 implementation is intentionally uncommitted. Current tracked/untracked working files are expected to include:

```text
M  .gitignore
M  pyproject.toml
M  src/cdt_autocad/server.py
?? scripts/run_hot_reload_acceptance.py
?? src/cdt_autocad/bridge_probe.py
?? src/cdt_autocad/hot_reload.py
?? src/cdt_autocad/runtime_identity.py
?? src/cdt_autocad/supervisor.py
?? src/cdt_autocad/worker.py
?? tests/test_hot_reload_endpoint_mp2.py
?? tests/test_hot_reload_process_mp2.py
?? tests/test_hot_reload_proxy_mp2.py
?? tests/test_hot_reload_supervisor_mp2.py
?? tests/test_hot_reload_watcher_mp2.py
?? tests/test_runtime_identity_mp2.py
```

`pylock.linux.toml` and `pylock.windows.toml` were regenerated with the canonical `pip 26.1.2` resolver after adding direct `httpx`/`uvicorn` dependencies, but regeneration produced no tracked lock diff at this checkpoint.

Do **not** use `git add .`. Stage MP-2 files explicitly only after final acceptance/review.

## 3. Runtime launch topology discovered on `.171`

Observed Windows host truth:

- there is currently **no persistent CDT-AutoCAD Python MCP service/process**;
- no CDT-AutoCAD listener was found on the checked runtime ports;
- existing CDT/AutoCAD Scheduled Tasks are acceptance/native/reload-bridge utilities rather than the Python provider service;
- `cloudflared.exe` is running;
- its current config has only a catch-all `http_status:404` ingress and no CDT-AutoCAD hostname/service route;
- therefore **tunnel alive != provider healthy** is now an explicit MP-2 invariant;
- Cloudflare configuration has **not** been modified during this MP-2 work.

The stable supervisor endpoint is therefore a new runtime layer, not a reload wrapper around an already deployed Python MCP service.

## 4. Chosen architecture

Rejected approach: scattered `importlib.reload()` inside the serving FastMCP process.

Implemented candidate architecture:

```text
client
  -> stable authenticated ASGI supervisor endpoint
      -> exactly one authoritative stateless FastMCP worker generation
          -> COM or ezdxf backend
          -> staged native bridge readiness/status when required
```

Reload protocol:

```text
source/build identity changes
  -> start candidate worker on private localhost port
  -> MCP startup/contract health probe
  -> fence new public requests
  -> drain old in-flight requests
  -> verify old generation safe state
  -> verify candidate activation/readiness
  -> atomically switch authoritative generation
  -> reopen public request gate
  -> stop old worker
```

Any candidate/startup/drain/readiness failure:

```text
stop candidate
  -> preserve old healthy generation
  -> reopen public request gate
  -> report failure with generation/build identity
```

No mixed-generation writer is allowed.

## 5. Implemented files and responsibilities

### `src/cdt_autocad/hot_reload.py`

Pure supervisor/generation state machine:

- `WorkerSpec`, `WorkerGeneration`, `WorkerHealth`, `ReloadResult`;
- one authoritative generation;
- request lease counting;
- request fencing and drain;
- promotion/fallback;
- pending transaction/recovery/timeout-uncertain activation refusal;
- telemetry failure isolation;
- frozen worker environment/policy identity;
- structured reload events.

### `src/cdt_autocad/supervisor.py`

Runtime layer:

- `SubprocessWorkerLauncher` starts/stops private worker processes;
- `McpWorkerProbe` checks real MCP tool catalog/status;
- `SupervisorProxyApp` is the stable authenticated ASGI proxy;
- Bearer auth is checked before public request routing;
- drain refusal returns deterministic HTTP 503 and does not touch a worker;
- source watcher fingerprints worker-relevant source/config and requests reload;
- `source_build_id()` supplies generation build identity;
- supervisor CLI is exposed by the new `cdt-autocad-supervisor` entry point.

Important source-watcher rule: supervisor control-plane files `hot_reload.py` and `supervisor.py` are excluded from worker build identity. Changes to supervisor control-plane code require supervisor/service restart; they are not self-hot-reloaded.

### `src/cdt_autocad/worker.py`

One replaceable FastMCP worker generation:

- loopback-only bind;
- `streamable-http`;
- `stateless_http=True`;
- `json_response=True`;
- existing provider auth/config remains enforced inside the worker as defense in depth.

### `src/cdt_autocad/runtime_identity.py`

Adds runtime provenance/status:

- supervisor generation;
- provider build ID;
- policy fingerprint;
- supervised/static identity;
- supported runtime profile;
- bridge DLL SHA-256 when present;
- bounded native bridge readiness/pending-recovery reporting.

### `src/cdt_autocad/bridge_probe.py`

Native bridge status probe runs in a child process so AutoCAD modal/busy behavior cannot block `system_status` indefinitely. Parent applies a hard timeout and reports typed reasons such as:

- `autocad_not_running`;
- `autocad_different_session`;
- `bridge_missing`;
- `bridge_unavailable`;
- `protocol_mismatch`;
- `autocad_busy_or_modal`.

### `scripts/run_hot_reload_acceptance.py`

Process-level acceptance harness using a temporary source fixture and a stable supervisor URL. It supports success cycles and injected syntax/startup failures. Current smoke result is described below; the required full 20/10 process run is still pending.

## 6. TDD / acceptance evidence already obtained

### Pure supervisor state machine

**11/11 PASS** at the latest focused run.

Covered:

- request fence + old-generation drain before promotion;
- startup failure keeps last healthy generation;
- drain timeout keeps old generation and reopens gate;
- pending transaction blocks switch;
- pending N7 recovery blocks switch;
- COM timeout uncertainty blocks switch;
- required native bridge not-ready blocks candidate promotion;
- auth token / allowed roots / policy identity remain frozen across generations;
- telemetry sink failure cannot alter reload result;
- fixture loop reaches **20 successful reloads + 10 injected failures** with one authoritative generation throughout.

### Stable proxy

**3/3 PASS**:

- Bearer enforced before forwarding;
- MCP session/query/body/header forwarding verified;
- reload drain returns 503 without touching worker;
- authenticated supervisor status endpoint works independently of upstream worker probe.

### Runtime identity / source watcher

Focused gates PASS. Verified:

- environment generation/build/policy identity;
- `system_status` exposes runtime identity fields;
- diagnostic events use the worker generation rather than `static`;
- source edit produces one reload request per new build fingerprint.

### Real worker subprocess replacement

**PASS**:

- four real Python worker generations;
- four distinct PIDs;
- every generation probed through real MCP;
- 50 tools preserved;
- runtime/build/policy identity matches the promoted generation.

### Stable public endpoint integration

**PASS**:

- same public `/mcp` URL initially served `g000001`;
- a real worker process replacement occurred;
- same `/mcp` URL then served `g000002`;
- tool count remained 50.

### Process-level edit/watch smoke

**PASS** for a small smoke run:

- 2 successful source-change reloads;
- 1 injected syntax/startup failure;
- failed candidate did not replace the old healthy generation;
- restoring valid source promoted a later healthy generation;
- public URL remained stable.

This smoke is **not** the required final 20-success/10-real-failure process acceptance.

## 7. Last code change before handoff — MUST REVERIFY

The latest edit added `protocol_version="MCP"` and a `contract_hash` derived from the public guide into `system_status` in `src/cdt_autocad/server.py`.

That last edit was made **after** the previous focused/integration runs. The next session must not assume those earlier green counts cover this final change.

Immediate first task:

1. extend `WorkerHealth` / `McpWorkerProbe` / `ReloadSupervisor` promotion validation to carry and verify the candidate protocol + contract hash;
2. define the expected contract-hash source once, so supervisor and worker cannot accidentally compare different material;
3. rerun focused MP-2 tests before any larger acceptance.

Do not weaken the gate to merely `contract_version + 50 tools`; MP-2 requires explicit build/protocol/contract identity.

## 8. Historical remaining close gates

> Resolved on 2026-09-11: the gates below were open at handoff time and subsequently passed on the reviewed closure candidate. They are retained only as historical execution context.

At handoff time, MP-2 was not closed until all of these passed on one reviewed candidate:

- finish protocol/contract-hash promotion validation;
- rerun all focused MP-2 tests after that change;
- run process-level **20 successful source reloads + 10 real injected syntax/startup failures** through one stable public endpoint;
- verify every failed reload preserves the last healthy generation;
- verify request generation/build correlation remains unambiguous across switch;
- run Windows `.171` acceptance with **COM backend + native bridge required** in the same Windows interactive session as AutoCAD 2027;
- prove pending recovery / bridge-not-ready prevents activation on the target host, without fabricating a dangerous real recovery state;
- verify modal/busy bridge probe is bounded and cannot hang provider status/reload indefinitely;
- confirm Cloudflare remains outside correctness: tunnel/process presence must not substitute for MCP + bridge readiness;
- full Linux regression;
- full Windows regression;
- changed-file Ruff;
- compileall Linux/Windows;
- `git diff --check`;
- code review with no blocking findings;
- write canonical `docs/evidence/mp2-hot-reload-2026-09-11.json`;
- update checkpoint/roadmap/architecture/threat/observability docs to measured final results;
- explicit stage only MP-2 files;
- commit + push MP-2 separately;
- update CyberBrain Knowledge from IN PROGRESS to the final measured closure result.

## 9. Windows `.171` acceptance topology requirement

The native Named Pipe bridge is same-user + same-Windows-session. SSH commands run in Session 0 and cannot prove the final bridge-required provider lane.

Final COM/native MP-2 acceptance should therefore run the supervisor/worker acceptance harness via an **Interactive Session 1 Scheduled Task** or equivalent already-proven interactive mechanism, using the existing user-owned Python environment and AutoCAD 2027 process in Session 1.

Do not weaken same-session bridge security to make an SSH Session 0 test pass.

## 10. Cloudflare boundary

`cloudflared.exe` is present/running on `.171`, but no CDT-AutoCAD ingress was configured in this session.

Keep deployment sequencing separate:

1. local supervisor correctness + target Windows acceptance;
2. local stable endpoint/service lifecycle;
3. only then configure a Cloudflare route if explicitly required;
4. external/tunnel health must still probe authenticated MCP/provider readiness, not only cloudflared process/tunnel state.

Secrets remain owner-managed. Never write Bearer tokens into committed docs/evidence.

## 11. CyberBrain record

Current MP-2 checkpoint is stored as:

```text
Knowledge ID: b8f2b5f9-ec08-456c-9687-6c3959418e84
Topic: autocad-mp2-hot-reload
State: IN PROGRESS / uncommitted
```

When MP-2 closes, evolve/store the final closure fact rather than treating this partial checkpoint as final certification.

## 12. Suggested first commands next session

```text
bootstrap /mnt/pc-dev/CDT-AutoCAD
read AGENTS.md
read docs/CURRENT_CHECKPOINT.md
read docs/SESSION_HANDOFF_2026-09-11_MP2_HOT_RELOAD.md
git status --short --branch
git diff --check
```

Then finish contract-hash/protocol health validation and run the focused MP-2 test set before changing deployment/runtime configuration.

## 13. Paste-ready handoff message

```text
Dùng @SlncTrZ-MCP bootstrap /mnt/pc-dev/CDT-AutoCAD.
Đọc AGENTS.md, docs/CURRENT_CHECKPOINT.md và docs/SESSION_HANDOFF_2026-09-11_MP2_HOT_RELOAD.md.
Tiếp tục MP-2 Python MCP hot reload từ working tree hiện tại. Không reset/clean các file MP-2 chưa commit. Trước tiên hoàn tất protocol/contract-hash promotion validation và rerun focused tests; sau đó process-level 20 success + 10 real injected failure, rồi Windows .171 COM + native-bridge-required Session 1 acceptance. Chỉ khi full regressions/review/evidence PASS mới cập nhật MP-2 CLOSED, commit và push riêng. Không sửa Cloudflare config trước khi local/Windows correctness gate đóng.
```
