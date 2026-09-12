# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-12 08:19 +07:00
> Status: **OPERATIONAL RC since 2026-09-12 · N0–N7 CLOSED · O1 CLOSED/LIVE PASS · MP-2 CLOSED/LIVE PASS · G1/G2/G3 CLOSED/LIVE PASS · 10,000-entity graduation PASS · Feature-based Chunks Streaming LIVE PASS · public promotion FINAL CLOSE GATES PASS**
> Primary certification target: **AutoCAD 2027 full · Windows x64 · COM `26.0` / `AutoCAD.Application.26` · Managed .NET `net10.0-windows`**
> Architecture boundary: **CDT-AutoCAD is a Generic CAD Execution Engine. Domain standards, engineering rules, calculations and reports remain outside the provider.**

## 1. What is authoritative now

The architecture and native execution program has moved beyond the historical 50-tool A2 baseline. The reviewed public promotion is now closed and published on `main` at commit `0516fe3`:

```text
provider_version: 0.4.0rc1
contract_version: autocad-generic-v1-rc1
public MCP tools: 86
execution_model: feature-based-chunks-streaming-v1
native bridge line: 0.8.1-g3
```

These identities have now passed the final public-promotion close gates on the reviewed promotion tree. Historical evidence files that mention `0.3.0rc1`, `autocad-a2-v1-rc1` or 50 tools remain valid for the older checkpoint they recorded and must not be rewritten as if those older runs used the new contract.

Public-contract promotion completed at `0516fe3`. That commit was surgically staged and excluded unrelated local/private workspace state; later legal, provenance and operational-maintenance commits do not change the promotion evidence or contract identity. Historical evidence/handoffs remain unchanged where they describe older contract identities.

## 2. Closed native architecture program

| Program | State | Accepted result |
| --- | --- | --- |
| N0–N7 | **CLOSED / LIVE PASS** | Managed .NET bridge, PID/fingerprint state model, bounded semantic extraction, typed native mutation, deterministic validation/state-chain, immutable checkpoints and exact R0/R1/R2 recovery including activation-safe R2 across real AutoCAD restart |
| O1 | **CLOSED / LIVE PASS** | LINE/CIRCLE/ARC/simple-LWPOLYLINE typed native mutation over the N5–N7 integrity model |
| MP-2 | **CLOSED / LIVE PASS** | stable authenticated supervisor, replaceable stateless FastMCP workers, generation fencing/drain/fallback, protocol/contract identity and bounded native-bridge readiness |
| G1 | **CLOSED / LIVE PASS** | generic batch create, PID-targeted planar translate/rotate-Z/uniform-scale, persistent block-definition-PID insertion; native chunks bounded to 32 |
| G2 | **CLOSED / LIVE PASS** | schema-agnostic namespaced JSON metadata get/set/query in ExtensionDictionary/XRecord; provider namespaces reserved; metadata participates in document fingerprint v3; storage/query/numeric work bounded |
| G3 | **CLOSED / LIVE PASS** | one immutable predecessor checkpoint across yielded native chunks, exact predecessor R2 recovery on logical failure/unknown completion, independent final compact-state read-back and finalize reconciliation |
| Scale graduation | **CLOSED / LIVE PASS** | 100 / 1,000 / 5,000 / 10,000 entity tiers passed on real AutoCAD 2027 with beginning/middle/end failure injection, exact predecessor recovery, zero pending recovery and stable AutoCAD/bridge process identity |
| Feature-based Chunks Streaming | **CLOSED / LIVE PASS** | feature-local checkpoint/rollback: a failed current feature is restored without undoing earlier committed features; following features can continue from the preserved predecessor |

The native bridge currently advertises:

- protocol `cdt-autocad-native-v1`;
- bridge `0.8.1-g3`;
- document fingerprint schema **v3**;
- maximum native batch chunk **32 entities**;
- graduated semantic capacity **12,288 entities**;
- public logical feature/batch item cap **10,000**;
- `batch_yield_per_idle=true`;
- `cross_chunk_atomic=false` at the individual micro-chunk primitive;
- `logical_batch_atomic=true` through the G3 predecessor checkpoint/recovery model.

`cross_chunk_atomic=false` and `logical_batch_atomic=true` are not contradictory: each native transaction is a bounded micro-chunk; G3 provides higher-level semantic atomicity by restoring the logical predecessor when the grouped operation fails.

## 3. Production execution model

**Feature-based Chunks Streaming is the approved Production Domain execution model.**

A Domain Agent defines one meaningful feature at a time and submits generic CAD actions. The provider does not interpret the domain meaning of names such as road, manhole, kiosk, beam or pipe.

```text
Domain Agent
  -> Feature 01
       -> native chunk <= 32
       -> native chunk <= 32
       -> verify + commit feature
  -> presentation pacing ~300 ms
  -> Feature 02
       -> ...
       -> failure
       -> restore Feature 02 predecessor only
  -> recompute/retry Feature 02
  -> Feature 03
```

Public orchestration entry point: `feature_execute(feature_id, feature_sequence, correlation_id, actions)`.

A feature can mix the generic native action families already proven by G1/G3:

- typed entity creation;
- persistent-PID block insertion;
- persistent-PID transform.

Successful receipts carry feature/correlation identity, pre/post document fingerprints, affected semantic PIDs, native chunk count, journal path and `recommended_next_delay_ms=300`. Failure receipts identify the failed action/native-chunk position and are accepted only after exact predecessor recovery.

The 300 ms delay is **between completed features for presentation**, not between native micro-chunks. Native chunks run as quickly as the bounded AutoCAD Idle-yield model allows.

For 3D showcase work the current presentation convention remains **SE Isometric + Shades of Gray**.

## 4. Live evidence

Canonical new evidence:

- `docs/evidence/g23-live-2026-09-11.json` — G2 metadata + G3 recovery acceptance;
- `docs/evidence/g3-scale-100-2026-09-11.json`;
- `docs/evidence/g3-scale-1000-2026-09-11.json`;
- `docs/evidence/g3-scale-5000-2026-09-11.json`;
- `docs/evidence/g3-scale-10000-2026-09-11.json`;
- `docs/evidence/feature-stream-production-2026-09-11.json`.

Feature-stream live acceptance proves:

- baseline semantic entity count: 12;
- Feature 1 commits 40 entities and advances the state to 52;
- configured 300 ms presentation pause measured **300.295 ms**;
- Feature 2 commits its first native micro-chunk, fails at native chunk index 1, then returns `ROLLED_BACK_VERIFIED` to the exact Feature-1 fingerprint/count 52;
- Feature 1 remains committed and unchanged;
- Feature 3 then commits 8 entities and advances the state to 60;
- final pending native recovery count: **0**.

10,000-entity graduation proves 313 bounded native chunks, 10,000 unique persistent entity PIDs, beginning/middle/end fault injection with exact predecessor recovery, zero pending recovery and stable AutoCAD/bridge process identity.

The latest native C# candidate has built Release/x64 with **0 errors**. The three inherited Autodesk-reference `MSB3277` warning families remain documented rather than hidden.

## 5. Public contract promotion state

The promoted contract adds the generic strong-integrity native surface to the broader product surface. The current FastMCP catalog is **86 tools**, including `feature_execute`.

Strong-integrity tools include:

```text
native_integrity_status
feature_execute
batch_create_entities
batch_insert_blocks
batch_transform_entities
metadata_get
metadata_set
metadata_query
```

The same promotion exposes the previously implemented A3 drafting/analysis/3D surface represented in the current `server.py` and backend capability maps. Provider identity, contract identity and public tool count advanced together intentionally.

**Final public-promotion close gates PASS on the reviewed tree.** Contract identity measured exactly **86 tools** at `0.4.0rc1 / autocad-generic-v1-rc1`; full Linux regression is **316 passed / 5 skipped**; Windows `.171` regression is **315 passed / 6 skipped**; C# `Release/x64` built with SDK `10.0.401` at **0 errors / 3 known MSB3277 warning families**. Linux and Windows `compileall` pass and `git diff --check` passes. Focused code/security review found no blocking regression or new arbitrary-command surface. Ruff could not be rerun because the Linux `.deps` wrapper has no Ruff executable payload and the Windows test venv has no Ruff module; this is recorded as a tooling availability gap, not converted into a false PASS.

## 6. Final public-promotion close gates

| Gate | Result |
| --- | --- |
| Contract identity | **PASS** — 86 public tools; provider `0.4.0rc1`; contract `autocad-generic-v1-rc1`; execution model `feature-based-chunks-streaming-v1` |
| Linux full regression | **PASS** — 316 passed / 5 skipped |
| Windows `.171` full regression | **PASS** — 315 passed / 6 skipped |
| C# Release/x64 | **PASS** — SDK 10.0.401; 0 errors; 3 inherited MSB3277 warning families |
| Compile/hygiene | **PASS with tooling note** — Linux/Windows compileall PASS; `git diff --check` PASS; Ruff unavailable in both prepared environments |
| Focused security/code review | **PASS** — no blocking finding; fixed-command presets only, bounded protocol/storage/query surfaces, fail-closed fingerprint/recovery binding preserved |
| Selective staging/publication | **PASS** — promotion commit `0516fe3` pushed to `main`; `.gitignore`, `_private/`, `_test_workspace/`, `specs/**` excluded |

The two MP-2 assertions still hard-coded to the historical 50-tool count were the only full-Linux regression failures encountered during this close session. They were migrated to the canonical `PUBLIC_TOOL_COUNT`; the complete Linux suite then passed. No G1/G2/G3, scale or Feature Streaming implementation was reopened.

## 7. Operational-readiness gate — 2026-09-12

The repository was cleaned and re-gated before beginning operational RC use. This maintenance gate does not replace the live AutoCAD promotion evidence above and does not change the public contract.

| Gate | Result |
| --- | --- |
| Linux full headless regression | **PASS** — 320 passed / 5 skipped on Python 3.12 |
| Windows `.171` full regression | **PASS** — 319 passed / 6 skipped on Python 3.12 with `PYTHONPATH=H:\\Develop\\CDT-AutoCAD\\src` |
| Python compile | **PASS** — Linux and Windows |
| Ruff | **PASS** — Ruff 0.16.7, `src scripts tests` clean |
| Package build | **PASS** — `cdt_autocad_provider-0.4.0rc1-py3-none-any.whl`; owner/license metadata verified |
| MP-2 runner smoke | **PASS** — 1 successful reload + 1 injected startup failure + healthy recovery; stable endpoint reports 86 tools and zero pending recovery |
| Repository hygiene | **PASS** — `git diff --check`, documentation-link check, private-artifact gate, obvious-secret scan and current provenance scan pass |
| C# bridge | **Not rebuilt in this maintenance gate** — no C# source changed; prior `Release/x64` 0-error promotion evidence remains authoritative |

The cleanup also fixed the standalone MP-2 acceptance fixture so it copies the bounded command-preset registry, replaced stale hard-coded 50-tool assertions in that runner with canonical `PUBLIC_TOOL_COUNT`, and made startup failure diagnostics preserve the supervisor log tail.

## 8. Operational repository state

Operational use begins **2026-09-12** under the existing `0.4.0rc1` RC/preview contract. This does not declare GA/stable status and does not expand capability claims beyond accepted evidence.

Repository hygiene now intentionally keeps `_private/`, `_test_workspace/`, local `artifacts/`, runtime/cache/build output and local secret files untracked. `_private/` contains benchmark/planning material and must not be swept into public/product commits. `specs/**` remains a pinned snapshot and is not to be edited.

Use `docs/README.md` as the documentation map, `docs/OPERATIONS_RUNBOOK.md` for live operation and `docs/RELEASE_CHECKLIST.md` for future publication gates. `docs/SESSION_HANDOFF_2026-09-11_FEATURE_STREAMING_PRODUCTION.md` is retained only as historical pre-close evidence.
