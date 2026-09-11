# Observability Contract — CDT-AutoCAD

> Baseline: MP0-T00 · Version: 1 · Updated: 2026-09-10 +07:00
> Scope: structured diagnostic events and correlation semantics; not a monitoring-platform requirement.

## 1. Decision

CDT-AutoCAD starts observability with **structured events + end-to-end correlation**, not with a large monitoring stack. The first implementation uses a fixed event schema and a best-effort sink through Python logging. Collector, dashboard, metrics storage and alerting are optional follow-up infrastructure justified by measured operational need.

Recovery journal/checkpoint data is intentionally separate. Journal loss can block mutation/recovery; telemetry loss follows its own policy and must never be interpreted as recovery truth.

## 2. Implemented MP0-T00 event boundary

`src/cdt_autocad/diagnostics.py` provides:

- `DiagnosticContext` propagated through a Python `ContextVar`;
- `DiagnosticEvent`, a fixed JSON-safe schema without arbitrary request payload or free-text error body;
- `DiagnosticSink` contract;
- `JsonLoggingSink`, the default structured logging sink;
- `DiagnosticEmitter`, which records dropped-event count while isolating sink failure from tool correctness;
- `ProviderDiagnosticMiddleware`, which emits `tool.started`, `tool.completed` and `tool.failed` around public MCP tool calls.

`create_mcp(..., diagnostic_sink=...)` accepts an injected sink for tests or later runtime wiring. The middleware is outside the provider error middleware, so expected provider errors are observed as completed tool outcomes rather than leaking internal exception text into diagnostic events.

## 3. Event schema

| Field | MP0 status | Semantics |
| --- | --- | --- |
| `event_type` | Required | Stable event type, initially `tool.started`, `tool.completed`, `tool.failed`. |
| `timestamp_utc` | Required | UTC ISO-8601 event timestamp. |
| `request_id` | Required | Provider-generated opaque correlation ID; contains no user data. |
| `operation` | Required | MCP/native/workflow operation name represented by the event. |
| `backend` | Required | Active provider backend identity. |
| `provider_version` | Required | Provider version handling the request. |
| `generation` | Required | Runtime generation identity; MP0 public baseline is `static`, MP-2 replaces this with supervisor generation identity. |
| `outcome` | Completion/failure | `success`, `error` or `exception`. |
| `latency_ms` | Completion/failure | Measured wall latency for this event boundary. |
| `error_class` | Exceptional failure only | Exception class, not free-text message or payload. |
| `job_id` | Reserved/nullable | Workflow job correlation when MP-6 introduces durable jobs. |
| `document_pid` | Reserved/nullable | Semantic lineage binding when the layer has an authoritative PID. |
| `runtime_document_id` | Reserved/nullable | Runtime document binding for native operations. |
| `recovery_id` | Reserved/nullable | Recovery/checkpoint correlation. |
| `bridge_version` | Reserved/nullable | Native bridge build/version identity. |
| `autocad_version` | Reserved/nullable | Runtime AutoCAD version when authoritatively known. |
| `scope` | Reserved/nullable | Declared capability/snapshot/workflow scope. |

Reserved fields are intentionally nullable now. MP0 does **not** invent values at layers that do not yet own authoritative job/document/native state.

## 4. Correlation rule

The target path is:

```text
request_id
  -> job_id (when jobs exist)
  -> document_pid + runtime_document_id
  -> runtime generation
  -> operation/capability tuple
  -> semantic step
  -> native IPC/bridge
  -> artifact/recovery_id
```

Each layer must propagate the existing correlation identity and enrich only fields it owns. No layer should create a second unrelated request identifier merely because execution crossed process or IPC boundaries.

MP0-T00 acceptance proves correlation across a real FastMCP tool request and nested provider execution. Native IPC/document/recovery enrichment is a later slice because those current N7 files are intentionally not modified by MP0-T00 while N7 is dirty.

## 5. Journal and telemetry are different failure domains

| Property | Recovery journal/checkpoint | Diagnostic telemetry |
| --- | --- | --- |
| Purpose | Correctness, durable recovery authority | Diagnosis, performance and operational visibility |
| Required for mutation/recovery continuation | Yes where the state machine requires it | No at MP0 |
| Write failure | Fail closed / block according to semantic state machine | Count/drop according to telemetry policy; do not mutate semantic state |
| Corruption | Recovery integrity failure requiring reconciliation | Observability degradation only unless a future explicit policy says otherwise |
| Can prove commit/rollback | Only with required semantic/artifact read-back | No |
| Can be reconstructed from journal | Some diagnostics may be reconstructed | Not a reason to weaken journal durability |

Existing N6 coverage `test_rollback_journal_failure_blocks_future_execution` proves required journal failure becomes `JOURNAL_FAILED` and blocks execution. MP0 diagnostics coverage separately injects a failing sink and proves `system_status` still succeeds.

## 6. Loss/backpressure policy

MP0 policy is deliberately small:

- diagnostic sink exceptions are contained by `DiagnosticEmitter`;
- every failed emission increments an in-process dropped-event counter;
- no unbounded retry queue is introduced;
- tool/recovery correctness does not depend on telemetry availability;
- event bodies exclude credentials and arbitrary drawing/request payloads;
- monitoring storage/alerting is deferred until MP-2/MP-7 measurements justify it.

When a durable operational event sink is later introduced, its queue size, backpressure, drop priority and shutdown/drain behavior must be explicit and fault-tested. That change must not silently turn telemetry into a prerequisite for semantic recovery unless an ADR intentionally changes the architecture.

## 7. MP-2 enrichment requirements

MP-2 replaces `generation="static"` for supervised workers with a real generation/build identity sourced from the supervisor environment. The stable supervisor owns reload phase, active generation/build, active request count, success/failure counters and drop-count telemetry; worker diagnostics inherit the promoted runtime generation. **This behavior is CLOSED / LIVE PASS.**

Reload operational events cover bootstrap/start/fence/completion/failure with reload ID, phase, active/candidate generation, outcome/error and latency. Telemetry sink failure increments an explicit drop counter and cannot alter generation promotion/fallback semantics. Full process `20` success + `10` injected failure acceptance and Windows AutoCAD 2027 Session 1 acceptance passed with stable generation correlation and zero post-run worker orphans.

`system_status` fields include protocol version, contract version/hash, generation, provider build, policy fingerprint, supervised profile, bridge build/readiness and pending recovery. Candidate promotion validates protocol/contract hash in addition to generation/build/tool-count/policy identity, preventing an incompatible worker from becoming authoritative.

Native bridge correlation should be propagated in a typed IPC field only after the protocol change has its own compatibility/version review; it must not be smuggled through free-text commands or logs. Control-plane edits to `hot_reload.py` or `supervisor.py` require supervisor restart; worker-source hot reload intentionally does not claim to replace its own supervisor.

Canonical closure evidence: `docs/evidence/mp2-hot-reload-2026-09-11.json`.
