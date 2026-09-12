# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-12 17:30 +07:00
> Status: **OPERATIONAL RC since 2026-09-12 · N0–N7 CLOSED · O1 CLOSED/LIVE PASS · MP-2 CLOSED/LIVE PASS · G1/G2/G3 CLOSED/LIVE PASS · 10,000-entity graduation PASS · Feature-based Chunks Streaming LIVE PASS · public promotion FINAL CLOSE GATES PASS**
> Primary certification target: **AutoCAD 2027 full · Windows x64 · COM `26.0` / `AutoCAD.Application.26` · Managed .NET `net10.0-windows`**
> Architecture boundary: **CDT-AutoCAD is a Generic CAD Execution Engine. Domain standards, engineering rules, calculations and reports remain outside the provider.**
> Current maintenance publication: **`31186f5`** · bridge candidate **`0.8.2-mp7`** · public contract identity unchanged.

## 1. What is authoritative now

The architecture and native execution program has moved beyond the historical 50-tool A2 baseline. The reviewed public promotion is now closed and published on `main` at commit `0516fe3`:

```text
provider_version: 0.4.0rc1
contract_version: autocad-generic-v1-rc1
public MCP tools: 86
execution_model: feature-based-chunks-streaming-v1
native bridge line: 0.8.2-mp7
```

These identities have now passed the final public-promotion close gates on the reviewed promotion tree. Historical evidence files that mention `0.3.0rc1`, `autocad-a2-v1-rc1` or 50 tools remain valid for the older checkpoint they recorded and must not be rewritten as if those older runs used the new contract.

Public-contract promotion completed at `0516fe3`. The later maintenance close was published at `31186f5`; it hardens visual-style state, ACIS/live acceptance, current-identity MP-2 provenance, Session-1 lifecycle and COM-busy viewport handling without changing the 86-tool public contract. Historical evidence remains unchanged where it describes older contract identities.

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
- bridge `0.8.2-mp7` (the earlier G1/G2/G3/scale/Feature Streaming evidence remains historical `0.8.1-g3` evidence and is not rewritten);
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

Canonical new evidence (machine-readable artifacts retained with the internal acceptance record under these file names; not part of the published tree):

- `g23-live-2026-09-11.json` — G2 metadata + G3 recovery acceptance;
- `g3-scale-100-2026-09-11.json`;
- `g3-scale-1000-2026-09-11.json`;
- `g3-scale-5000-2026-09-11.json`;
- `g3-scale-10000-2026-09-11.json`;
- `feature-stream-production-2026-09-11.json`.

Feature-stream live acceptance proves:

- baseline semantic entity count: 12;
- Feature 1 commits 40 entities and advances the state to 52;
- configured 300 ms presentation pause measured **300.295 ms**;
- Feature 2 commits its first native micro-chunk, fails at native chunk index 1, then returns `ROLLED_BACK_VERIFIED` to the exact Feature-1 fingerprint/count 52;
- Feature 1 remains committed and unchanged;
- Feature 3 then commits 8 entities and advances the state to 60;
- final pending native recovery count: **0**.

10,000-entity graduation proves 313 bounded native chunks, 10,000 unique persistent entity PIDs, beginning/middle/end fault injection with exact predecessor recovery, zero pending recovery and stable AutoCAD/bridge process identity.

The current native C# candidate `0.8.2-mp7` has built Release/x64 with SDK `10.0.401` at **0 errors**. The three inherited Autodesk-reference `MSB3277` warning families remain documented rather than hidden. Fresh runtime provenance binds the Release/x64 candidate and the DLL actually loaded by AutoCAD to SHA-256 `4e31791d646d446a642855aec44686b7fcb5956ff28d4453b822787dde09cb0c` without retroactively rebinding older `0.8.1-g3` events.

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

Repository hygiene intentionally keeps `_private/`, `_test_workspace/`, local `artifacts/`, runtime/cache/build output and local secret files untracked. `_private/` is intentionally reduced to exactly five current control files (`AUDIT`, `DEVELOP_PLAN`, `TECH_DEBT`, `HANDOFF`, `NEXT_SESSION`) and must not be swept into public/product commits. `specs/**` remains a pinned snapshot and is not to be edited.

Internal working context is deliberately condensed into the five `_private/*.md` control files; there is no private history/ADR/evidence subtree. Canonical raw machine evidence that remains worth retaining is compressed under ignored `artifacts/internal-evidence/`. The published `docs/` set remains limited to product-facing contracts/guides and must stay usable without `_private/`.

Use `docs/README.md` as the documentation map and `docs/OPERATIONS_RUNBOOK.md` for live operation. Current internal status/debt/plan/session continuity lives only in the five `_private/*.md` control files and is not part of the published documentation set.

## 9. Current maintenance hardening and architecture frontier — 2026-09-12

The latest product-maintenance batch is pushed on `main` at commit **`f721998`** (`Fix: harden COM mutation integrity and provenance`). It does **not** change the public tool catalog, contract version, execution model or contract fingerprint.

Current identity remains:

```text
provider_version: 0.4.0rc1
contract_version: autocad-generic-v1-rc1
public MCP tools: 86
execution_model: feature-based-chunks-streaming-v1
contract_hash: 3e6e994100e1bde73dffc6d53f340ec335647aba90bbb29c8a07a5acd9981b9b
```

This maintenance batch hardens the broad live-COM compatibility lane without weakening the native G1/G2/G3 lane:

- `document_configure_units` now validates before mutation, snapshots all four relevant system variables, verifies exact read-back, restores the complete predecessor on failure/mismatch and quarantines later mutations when restore cannot be proven;
- `layer_update_state` now validates linetype before writes, refuses XREF-dependent layer mutation, preserves current-layer guards, snapshots/restores six layer fields and independently verifies the resulting state;
- COM entity/solid inspection now uses one case-insensitive typed/DumbDispatch-compatible property path for the declared verification families; unreadable object type fails closed instead of being reported as fabricated `UNKNOWN`;
- `document_new` retries only the explicit AutoCAD busy HRESULTs (`RPC_E_CALL_REJECTED` / `SERVERCALL_RETRYLATER`); mutating open/save paths retain their stricter no-blind-retry policy where completion could be ambiguous;
- acceptance provenance now distinguishes a candidate bridge binary from the bridge actually observed loaded in `acad.exe`; accepted-artifact registration requires the runtime-observed DLL hash and refuses retroactive binding.

Fresh AutoCAD 2027 Session 1 verification on the final code passed the targeted public/live suite **twice**: **5/5 in 13.85 s** and **5/5 in 13.22 s**. Coverage includes units/layer negative and read-back cases, real XREF-dependent layer refusal plus XREF `object_get`, LINE/LWPOLYLINE/INSERT/HATCH inspection, and 3DSOLID inspection/refusal truth. The final full regressions are **350 passed / 6 skipped on Linux** and **349 passed / 7 skipped on Windows `.171`**; Linux/Windows `compileall` and `git diff --check` pass. The loaded Session 1 bridge and current Release/x64 candidate both have SHA-256 **`e9dc90fba11749da08ea8ebf781669e4c7c317f2075dc1f97714e5a89d20f613`**. No C# source or public-contract material changed in this maintenance batch, so the bridge was not rebuilt solely for this Python-only change.

The earlier Ribbon-empty symptom is also understood operationally: `acad.CUIX` was present, while `WSCURRENT` was empty. Selecting **Drafting & Annotation** restored the Ribbon, and a later graceful AutoCAD restart verified `WSCURRENT="Drafting & Annotation"` with `CMDNAMES=""`. Live acceptance launch must start with an actual drawing/template rather than relying on the Start tab as a COM-ready document context.

### 9.1 What downstream orchestration may rely on now

A downstream Engineering/Domain Agent may rely on the following **within their declared scopes**:

- the 86-tool public contract and current names/schemas in `TOOL_GUIDE.md`;
- Feature-based Chunks Streaming through `feature_execute` for the proven native action families;
- G3 feature-local predecessor checkpoint/recovery and current native integrity status;
- bounded native create/insert/transform plus namespaced metadata;
- broad typed AutoCAD drafting/document/XREF/layout/viewport/3D compatibility tools with their advertised capability/refusal semantics;
- the newly hardened units/layer mutation and COM inspection behavior above;
- `artifact_seal` for the currently documented DWG content-addressed sealing behavior.

Do **not** infer that every one of the 86 tools has migrated to the native semantic state loop. The public product deliberately remains hybrid during migration.

### 9.2 Architecture work that is still open

The project is an **operational RC**, not a claim that the target architecture is 100% complete. The following remain explicit architecture frontier items and must not be silently assumed by downstream consumers:

- broad COM-to-native strong-integrity migration is incomplete; the native public strong-integrity surface remains the explicitly promoted subset rather than all non-read-only tools;
- full native semantic snapshots remain bounded; there is no stable paged whole-DWG semantic reader yet, and native `relations`/topology extraction is not currently an authoritative oracle;
- native mutation parity is still bounded to the currently advertised families rather than TEXT/MTEXT/HATCH/DIMENSION/SPLINE/layout/viewport and all other public mutation families;
- native semantic extraction is a provider-owned PID scope: a legacy/COM-created entity without provider PID metadata is refused explicitly as `UNMANAGED_ENTITY_PRESENT`; read paths never auto-adopt or assign PID, and a future typed adoption/bootstrap operation remains separate architecture work;
- MP-G05 native `3DSOLID` read semantics are now present: provider PID + versioned mass-property geometry signature participate in the native fingerprint chain, with explicit `topology_verified=false`; typed native solid mutation, predecessor checkpoint and exact recovery remain OPEN;
- XREF lifecycle now refuses reload/unload when ActiveX cannot prove load-state read-back and verifies detach absence when available; deep/nested source dependency completeness, non-DWG dependency families, post-seal drift/stale-evidence enforcement and broader artifact lifecycle policy still have follow-on work;
- a versioned generic validation-rules registry with unknown-ruleset refusal / explicit `NOT_EVALUATED` semantics is not yet implemented;
- historical G1/G2/G3/scale/feature-stream events that did not record a runtime DLL hash are intentionally **not** retroactively rebound to today's DLL; fresh events use the new accepted-artifact control;
- the canonical Session-1 acceptance wrapper now owns unique task names, per-run stdout/stderr/evidence and verified `finally` cleanup; older historical one-shot task scripts remain maintenance debt and the intentionally persistent marketing task remains a documented exception;
- the internal generic-engine Definition of Done is still under architecture review; this checkpoint therefore does not claim GA/stable or 100% target-architecture completion.

Downstream repositories should discover the live runtime with `system_status`, `system_capabilities` and, when native integrity is required, `native_integrity_status` rather than treating a cached source snapshot as runtime proof.

### 9.3 Current `0.8.2-mp7` maintenance evidence

The current maintenance candidate advances only the internal native bridge implementation identity; the provider remains `0.4.0rc1`, the public catalog remains **86 tools**, the contract remains `autocad-generic-v1-rc1`, and the execution model remains `feature-based-chunks-streaming-v1`.

Fresh AutoCAD 2027 Session-1 evidence now adds:

- **MP-G07 visual-style state:** typed Managed .NET `VisualStyleId` read-back and guarded handle-based restore; live `2dWireframe` handle `2F` → `Shades of Gray` handle `3A` → exact predecessor handle `2F`; `CMDNAMES=""` and `CMDACTIVE=0` after both transitions. The run also proved that visual-style change dirties the DWG (`DBMOD 0→17`), so failure recovery quarantines when artifact state cannot be restored exactly instead of claiming a clean rollback.
- **MP-2/A-09 current identity:** one isolated Session-1 owner completed **20 successful reloads + 10 injected startup failures** against the current 86-tool contract, bridge `0.8.2-mp7`, stable endpoint and zero pending recovery; the task was deleted and no acceptance process remained.
- **MP-G10 ACIS soak/adversarial:** 10-part and 100-part live tiers, ten repeated Boolean subtracts and a near-tangent subtract passed with bounded call latency, responsive UI pings and no retained working-set growth after disposable-document close. Destructive Boolean exception handling is additionally locked by fault-injection unit coverage and quarantines uncertain ACIS state.
- **Viewport COM-busy hardening:** a live targeted run exposed `RPC_E_CALL_REJECTED` on `PViewport.Target`; viewport create/read/scale/lock/delete now use the shared bounded COM-busy retry primitives. The same targeted five-test live suite then passed **twice consecutively: 5/5 in 18.92 s and 5/5 in 18.18 s**.
- **Integrity Maintenance Sprint P0:** deterministic RED reproductions closed late ezdxf writers, destructive COM false-success, create/configure partial-state leakage, XREF completeness false-positive and COM rollback receipt overclaim. Targeted AutoCAD 2027 Session-1 acceptance for the affected COM/ACIS/XREF paths passed **3/3** after Linux/Windows regression.
- **Mixed managed/unmanaged PID boundary P1:** Session-1 live evidence with exactly one COM-created unmanaged LINE proved that document state, unrelated PID-targeted metadata read and PID-targeted line mutation all previously failed as generic `ENTITY_PID_MISSING`. The native bridge now refuses all three deterministically as `UNMANAGED_ENTITY_PRESENT`, leaves the managed target unchanged and returns to the exact baseline fingerprint after the unmanaged object is removed. No read path auto-assigns PID.
- **Headless CI repair:** exact-head commit `e9a538d` restored Ruff compliance after five consecutive failing Headless CI runs; all four GitHub Actions jobs (Ubuntu/Windows × Python 3.11/3.12) now pass compile, lint and full headless regression.
- **MP-G05 read-only solid semantics:** AutoCAD 2027 Session-1 RED first returned `UNSUPPORTED_ENTITY_TYPE` for a PID-bearing `Solid3d`. GREEN after the native extractor change returned `entity_type=3DSOLID`, volume `480`, persistent solid PID, and deterministic geometry/document fingerprints. A reversible COM move advanced both fingerprints while preserving PID; moving back restored the exact baseline fingerprints, and save/reopen preserved PID and the exact document fingerprint. Verification is intentionally `mass-properties-v1` with `topology_verified=false`.
- **Current regressions after MP-G05 read slice:** Linux **385 passed / 6 skipped**; Windows `.171` **384 passed / 7 skipped**; C# Release/x64 **0 errors / 3 inherited warning families**. The public identity remains `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools`.

These results remove the previous evidence gaps for current-identity MP-2 process acceptance, the declared visual-style/ACIS-soak scopes, the P0 integrity findings, the mixed-PID refusal policy and MP-G05 read/PID/fingerprint semantics. MP-G05 remains OPEN until at least one typed native solid mutation family has expected-parent drift guards, independent post-commit read-back, immutable predecessor checkpointing and exact predecessor recovery.
