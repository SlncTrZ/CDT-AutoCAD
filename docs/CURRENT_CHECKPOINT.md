# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-10-07
> Status: **RC3 CANDIDATE — EXACT-SOURCE TECHNICAL RECERTIFICATION PASS FOR DECLARED PROVIDER SCOPE (certified-at: `9c69430` + 2026-09-30 + AutoCAD 2027/Windows x64; batch-create affected-scoped verify live-accepted at HEAD `a3faa7b` + 2026-10-06 + AutoCAD 2027/Windows x64; insert/transform affected-scoped verify (AC-P02) live-accepted at HEAD `a69289f` + 2026-10-07 + AutoCAD 2027/Windows x64)**
> Tag `v0.4.0rc3` exists at `689dd03` (2026-10-05); `v0.4.0rc2` at `295a064` remains the last published release record — tag existence and publication remain separate; current D18 source candidate is RC3; exact-source technical recertification passed on 2026-09-30, publication remains separate
> Primary certification lane: AutoCAD 2027 full · Windows x64 · COM `26.0` / `AutoCAD.Application.26` · Managed .NET `net10.0-windows`
> Native bridge deployed: DLL SHA-256 `13293B5E21F9E2B31A7B75D3EFFC107AB912F3C202AC70426CC65CBCB8C79070` (Release/x64, 0 errors / 3 inherited MSB3277 warnings; replaces `9A877C66…`), bridge `0.8.6-d18` unchanged, loaded from ApplicationPlugins bundle, pipe `SlncTrZ.CDT.AutoCAD.Bridge.v1.s1`

This file is the canonical **public current-state authority**. It reports what is true now. It does not define architecture or future roadmap.

> **Current HEAD `a69289f` (2026-10-07, clean, sync origin/main; parent `66d5587`):** AC-P02 affected-scoped post-commit verify for `entity.batch.insert_blocks` + `entity.batch.transform` implemented (`ExecuteBatchInsertBlocks` + `ExecuteBatchTransform` → `VerifyAffectedPostCommit`, receipt `affected_scoped`) and live-accepted on AutoCAD 2027 Session 1 (baseline + P02 DLL both PASS; same-workload chunk total −17.7%, p50 −16%, p95 −11%; stray-outside-affected per contract: chunk `COMMITTED_VERIFIED`, finalize mismatch `RECOVERY_FINALIZE_STATE_MISMATCH`, R2 restore `ROLLED_BACK_VERIFIED`); full pytest Windows **525 passed / 12 skipped** (from 517/12 at `a3faa7b`), 10/10 new tests PASS, Ruff + `git diff --check` clean, C# Release/x64 0 errors / 3 inherited MSB3277. AC-A01 batch-create affected-scoped verify at `a3faa7b` (2026-10-06) retained as history: smoke + 320-entity scale (p50 115,6 ms / p95 125,0 ms) + 10.000-entity scale (313 chunks, p50 694,6 ms / p95 1.127,8 ms, COM count exact) + U1 + D15/D16/D17/D18 + hot-reload 3+2, all PASS; full pytest Windows 517/12 at that HEAD. Operational incident OP-01 **CLOSED 2026-10-07** (AutoCAD PID 37688 Session 1, pipe present, backend port 8000 listen, health gates clean: tx=0, uncertainty=false, pending=0, no recovery required). Historical delta after `9c69430` now covered: `c35de8e` (core P1 patch) + `689dd03` (tag `v0.4.0rc3` points here) + relay commits + `a3faa7b` (AC-A01) + `a69289f` (AC-P02). Remaining historical note: `v0.4.0rc2` at `295a064` is still the last published release record. Writes allowed on disposable fixtures; production-document writes default-refuse.


## 1. Launch decision

CDT-AutoCAD is sufficiently complete to launch in its intended role as a **Generic CAD Execution Engine** for CDT-Engineer and other higher-level production domains.

There is no known top-level caller-state, document-provenance, filesystem-containment, semantic-recovery or CAD-execution integrity blocker that must be closed before downstream engineering-domain work begins. B1 is closed with split assurance: provider-owned file I/O is descriptor/handle-bound at the actual I/O boundary against concurrent descendant namespace mutation beneath a trusted configured root, while AutoCAD COM methods that accept pathname strings only remain a bounded lane with pre/post verification and are not advertised as race-free.

From this checkpoint forward, AutoCAD capability expansion is **demand-driven**:

> CDT-Engineer names a concrete blocked workflow, required postcondition and verification invariant; CDT-AutoCAD then adds only the capability needed to satisfy that requirement.

The project is deliberately **not** pursuing full AutoCAD API parity or speculative native migration as an independent roadmap.

Launch-ready does not mean “every AutoCAD feature exists.” It means the provider now satisfies the execution-engine mission and has explicit refusal/assurance boundaries for capability that is not yet proven.

## 2. Current public identity

```text
provider_version: 0.4.0rc3
contract_version: autocad-generic-v1-rc3
public MCP tools: 87
execution_model: feature-based-chunks-streaming-v1
native bridge candidate: 0.8.6-d18
```

The public tool count is now 87. RC3 adds one explicit public bootstrap entrypoint, `native_document_identity_initialize`, for a new empty current space and expands the existing bounded native create payload with generic TEXT/MTEXT/aligned/linear dimension families plus optional layer/color assignment. It does not add engineering-domain semantics or implicit legacy adoption.

Canonical architecture: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## 3. What is accepted now

The following major programs/scopes are closed within their documented boundaries:

| Scope | State | Accepted meaning |
| --- | --- | --- |
| N0–N7 / O1 | **CLOSED / LIVE PASS** | Managed .NET semantic-state, typed native mutation and verified recovery for their bounded families |
| MP-2 | **CLOSED / LIVE PASS** | Authenticated supervisor/hot-reload generation fencing and health-proven replacement |
| G1 | **CLOSED / LIVE PASS** | Generic bounded batch create/insert/transform with native micro-chunks |
| G2 | **CLOSED / LIVE PASS** | Namespaced bounded entity metadata with fingerprint participation |
| G3 | **CLOSED / LIVE PASS** | Logical feature/batch predecessor checkpoint and exact recovery across yielded micro-chunks |
| Scale graduation | **CLOSED / LIVE PASS** | 100 / 1,000 / 5,000 / 10,000 entity tiers with injected-failure recovery |
| Feature-based Chunks Streaming | **CLOSED / LIVE PASS** | Feature-local commit/rollback while preserving earlier accepted features |
| Integrity Maintenance P0 | **CLOSED** | Late-writer fencing, destructive postconditions, create atomicity, XREF completeness and rollback-receipt honesty |
| B0 document provenance | **CLOSED / LIVE PASS** | SaveAs and artifact sealing bind/verify the same canonical document path after mutation before success/provenance acceptance |
| B1 filesystem containment | **CLOSED / SPLIT ASSURANCE** | Provider-owned file I/O is descriptor/handle-bound at actual I/O against concurrent descendant namespace mutation beneath a trusted configured root; AutoCAD pathname-only APIs remain bounded by pre/post verification |
| B2 reproducible release proof | **CLOSED / EXACT-LOCK PASS** | Fresh Linux/Windows A/B reconstructions from canonical platform `pylock.*` reproduce the same clean Git/source/package identity and pass full regression + Ruff |
| B3 repo/tooling governance | **CLOSED** | `uv.lock` is explicitly non-canonical ignored residue, `pylock.*` is the exact dependency authority, Ruff is repeatable on both release platforms, and the release tree is clean |
| B4 caller-state binding | **CLOSED / LIVE PASS** | Five native strong-integrity write tools require caller document PID + predecessor fingerprint; wrong-document/stale-parent requests refuse before journal/checkpoint/CAD mutation |
| U1 core hardening | **CLOSED / LIVE PASS 2026-09-17** | New-empty-document PID bootstrap, truthful `document_save`, cached-COM readiness repair and native TEXT/MTEXT/aligned/linear dimension create breadth verified on AutoCAD 2027; D9/D11 and legacy/non-empty adoption remain separate deferred scope |
| D8 stale COM proxy recovery | **CLOSED / LIVE PASS 2026-09-18** | Cached application reuse probes liveness before use, does not misclassify COM busy as death, evicts stale generation-local state and reattaches after process replacement when no tracked transaction is open; process loss during an open tracked transaction quarantines fail-closed instead of blind retry |
| D15 request/checkpoint ownership | **CLOSED / LIVE PASS 2026-09-19** | Native recovery checkpoints are bound to the originating request owner; public recovery discovery exposes only a one-way owner fingerprint, new durable manifests persist no raw owner capability, foreign begin/resolve/finalize/logical-chunk attempts refuse before CAD mutation, and lost-response reconnect recovers only the originating checkpoint |
| D16 shared COM/native mutation ownership | **CLOSED / LIVE PASS 2026-09-19** | One provider-local writer authority serializes COM and native mutations; timeout/cancellation/process-loss uncertainty quarantines both lanes until provider restart, late completion does not clear quarantine, and deterministic pre-mutation native refusals remain typed refusals without false quarantine |
| D17 Save/SaveAs persisted-clean proof | **CLOSED / LIVE PASS 2026-09-19** | Save and SaveAs share one persisted-clean verifier; SaveAs proves the requested path remains bound to the same live document and requires `Saved=true`, `DBMOD=0`; dirty or unverifiable post-state quarantines later mutation |
| D18 atomic document-PID bootstrap | **CLOSED / LIVE PASS 2026-09-19** | Bootstrap verifies the bound document is active before PID side effect, keeps provisional PID write/readback/semantic extraction in one native transaction, commits only after verification, and aborts back to no persistent PID on pre-commit failure |
| Mixed PID P1 | **CLOSED / LIVE PASS** | Unmanaged entities refuse deterministically as `UNMANAGED_ENTITY_PRESENT`; read paths do not auto-adopt PID |
| MP-G05 bounded native solid loop | **CLOSED / LIVE PASS** | Provider PID + `solid-semantic-v2` + drift guard + persisted read-back + R0/R2 for planar `3DSOLID` translation |

The broader product surface remains hybrid: some tools use native strong-integrity execution, others use bounded COM/ActiveX compatibility, and unsupported/unverifiable behavior refuses explicitly.

## 4. Current execution guarantees

The provider currently supports the two launch-critical pillars:

- **Data Integrity / Rollback** — strong-integrity mutation paths prove `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`; uncertain state blocks dependent work.
- **Precise Identity / PID + Fingerprinting** — strong-integrity paths use provider-owned persistent identity, versioned semantic fingerprints and expected-parent drift protection rather than relying only on AutoCAD ObjectId/Handle.

Feature-based Chunks Streaming is the approved production orchestration model. Native micro-chunks remain bounded while the logical feature carries the higher-level checkpoint/recovery contract.

Normative behavior is defined in [`SEMANTIC_STATE_PROTOCOL.md`](SEMANTIC_STATE_PROTOCOL.md).

## 5. Latest measured gates

### 2026-10-07 AC-P02 insert/transform affected-scoped verify — PASS (HEAD `a69289f`)

`ExecuteBatchInsertBlocks` + `ExecuteBatchTransform` post-commit moved to `VerifyAffectedPostCommit` with `affected_scoped` receipts. New tests 10/10 PASS; full Windows **525 passed / 12 skipped** (from 517/12 at `a3faa7b`); Ruff + `git diff --check` clean; C# Release/x64 0 errors / 3 inherited MSB3277. Live Session-1 PASS on both baseline and P02 DLL; same-workload improvement: chunk total −17.7%, p50 −16%, p95 −11%. Stray-outside-affected behavior per contract (chunk `COMMITTED_VERIFIED`, finalize mismatch `RECOVERY_FINALIZE_STATE_MISMATCH`, R2 restore `ROLLED_BACK_VERIFIED`). Deployed DLL SHA-256 `13293B5E…` (replaces `9A877C66…`); bridge `0.8.6-d18` unchanged. Historical AC-A01/U1/D15–D18 figures below retain their original HEAD/scope identities.

### 2026-09-30 final recertification — PASS for declared RC provider scope

Execution source remains clean `9c69430bafc77ac854684e03ebaf677afd191050`; the Python/native tree and unchanged canonical locks match the previously recorded full-suite and wheel identities. No implementation changed between the partial run and this closure.

The owner identified the 2026-09-19 drawing as an old test fixture. Its checkpoint manifest/DWG and original drawing were archived with verified SHA-256 before retiring only that checkpoint from the live queue. The original drawing remains unchanged. This is owner-approved fixture retirement, not a claim of native recovery/finalization.

| Final rerun | Result |
| --- | --- |
| Linux exact-lock | 461 passed / 16 skipped; Ruff/dependency check PASS |
| Windows exact-lock | 465 passed / 12 skipped; Ruff/dependency check PASS |
| Installed-wheel smoke | Both platforms PASS outside repository: packaged help, 87 tools, DXF create/save |
| C# Release/x64 | 0 errors / 3 inherited reference warnings |
| D16/D17/D18 | LIVE PASS: shared mutation/process loss, persisted-clean save, PID bootstrap |
| D15 ownership/lost response | LIVE PASS; owner rediscovery, exact R2 restore, foreign refusals before mutation, pending recoveries 0 |
| U1 typed annotation floorplan | LIVE PASS; LINE/TEXT/MTEXT/two DIMENSION types, semantic readback, Saved=true and DBMOD=0 |
| MP-2 COM + required native bridge | LIVE PASS; 20 success cycles, 10 injected failure cycles and 10 successful recoveries; zero incidental failures |
| MP-2 terminal health | 87 tools, contract RC3, bridge ready in Session 1, pending recovery count 0 |
| Runtime identity and cleanup | Loaded DLL unchanged before/after each profile; all three Session-1 tasks deleted and cleanup verified |

This closes the remaining technical recertification gates for this committed execution candidate together with the Linux/Windows suites, installed-wheel smoke and D16–D18 live evidence recorded below. The earlier REVIEW_PENDING event and failed artifacts are retained as history.

The acceptance remains bounded to the documented Generic CAD Execution Engine RC scope. D18 fault rollback does not claim DBMOD preservation; existing B1/geometry/recovery assurance limits remain. No GA, portfolio integration, version bump, tag, push or public-release claim is made. Default provider environment and gateway routing were not replaced.

### Earlier 2026-09-30 observation — partial recertification

Clean execution source `9c69430bafc77ac854684e03ebaf677afd191050` was reconstructed with the unchanged canonical platform locks.

| Gate | Result |
| --- | --- |
| Linux CPython 3.12 | 461 passed / 16 skipped; Ruff and dependency check PASS |
| Windows x64 CPython 3.12 | 465 passed / 12 skipped; Ruff and dependency check PASS |
| Installed wheel, outside repository | Both platforms PASS: packaged help, 87 tools and DXF create/save |
| C# Release/x64 | Build PASS, 0 errors / 3 inherited reference warnings |
| AutoCAD 2027 Session 1 | D16 shared writer/process loss, D17 persisted-clean save, D18 PID bootstrap LIVE PASS |
| Final host-wide recertification | REVIEW_PENDING: one pre-existing unresolved logical recovery |

The named pipe client now uses overlapped I/O with one default 60-second budget for request write, response header and response body. Progress does not renew that budget. Local I/O cancellation retains buffers until completion; it does not cancel CAD work. A deadline is completion-unknown and fences later COM/native writes. Real Windows tests cover silent peers, partial bodies, drip headers, blocked writes and fragmented successful replies. Actual supervisor and worker HTTP boundaries reject absent/invalid Bearer tokens while authenticated 87-tool traffic survives reload.

Source-tree SHA-256: `a6ccedad7ec9746ff65784b3dfa4ed4358a5cd82b22bff6ee071a6489bbcc4f2`.
Candidate and AutoCAD-loaded bridge SHA-256: `f2de981dc6fb9dd903c0c1ec127510a4b45f20350751bd0da4d906887fc36b36`; bridge identity remains `0.8.6-d18`.
D16 observed process replacement and retained uncertainty fences. D18 again restored PID/entity identity after the injected fault while DBMOD changed 0→1; persisted-clean rollback is not claimed.

At this earlier observation, one unrelated checkpoint from 2026-09-19 remained on the host. After closing the restart's informational Drawing Recovery dialog, COM and bridge readiness were true, coordinator quarantine was false, and pending recovery count was 1. D15 stopped at its inventory assertion; MP-2 rejected activation as `ACTIVATION_HEALTH_FAILED`; U1 had not yet been rerun. The old checkpoint/document were preserved and that event remained REVIEW_PENDING. The subsequent owner-approved fixture retirement and passing final reruns are recorded above; this historical refusal is not the current host state.

The public contract remains RC3 / 87 tools. Tag `v0.4.0rc3` exists at `689dd03`; `v0.4.0rc2` remains the last published release record — tag existence and publication/release remain separate. Older measurements below retain their original source/runtime identities.

### Historical measured gates

U1 RC3 source candidate on 2026-09-17:

- Focused native/public/schema regression: **81/81 passed**.
- Full Linux regression: **412 passed / 11 skipped**.
- Full Windows `.171` regression: **411 passed / 12 skipped**.
- C# Managed .NET Release/x64 against AutoCAD 2027 SDK: **build PASS / 0 errors** with the same inherited MSB3277 warning families already documented for Autodesk/.NET reference-version conflicts.
- Exact-source AutoCAD 2027 Session-1 U1 acceptance: document PID bootstrap read-back verified with scope `empty-current-space-only`; LINE, TEXT, MTEXT, aligned dimension and linear dimension each returned `COMMITTED_VERIFIED`; `pending_recoveries=0`; final save reported `Saved=true`, `DBMOD=0`, `persisted_clean=true`.
- Dimension creation now canonicalizes native dimension layout before provisional fingerprinting, validates equivalent dimension-line geometry rather than a non-canonical definition-point coordinate, and re-extracts style resources in-transaction so AutoCAD-created resources such as `Defpoints` participate in the same provisional/persisted fingerprint.
- Document fingerprint schema remains **v3** because these changes complete the already documented v3 semantic fields rather than define a new hash schema; callers must re-read/rebaseline the predecessor after a bridge upgrade instead of comparing fingerprints captured under an older bridge implementation.
- D8 stale-COM recovery gate on 2026-09-18: focused liveness/rebind tests **3/3 passed**; full Linux **415 passed / 11 skipped**; full Windows `.171` **414 passed / 12 skipped**; live AutoCAD 2027 Session-1 fixture cached PID `25048`, terminated that exact process, observed replacement PID `26788`, and the same backend object reattached with `connected=true`, `transaction_depth=0`. Busy COM remains distinct from stale-process detection; tracked-transaction process loss remains fail-closed/quarantined.
- D15 request/checkpoint ownership gate on 2026-09-19: affected focused regression **68/68 passed**; exact-lock Linux **418 passed / 11 skipped + Ruff PASS**; Windows `.171` **417 passed / 12 skipped**; C# Release/x64 **PASS / 0 errors** with the known MSB3277 warning families. Exact-source AutoCAD 2027 Session-1 live acceptance on bridge `0.8.5-d15` simulated a lost `logical.begin` response, rediscovered the checkpoint only by the originating owner's SHA-256 correlation, proved `bridge.recovery.list` exposes no raw owner ID, proved the schema-v2 manifest persists only the owner fingerprint, refused foreign begin/resolve/finalize/logical-chunk paths before CAD mutation, restored the exact predecessor by owner A, and ended with `pending_recoveries=0`. The deployed candidate DLL SHA-256 was `b9ffc7d25ba40fd33f404489a5e547367701148d4ffd1921cd5552447973a627`.
- D16 shared mutation ownership gate on 2026-09-19: final focused D16/native-surface regression **33/33 passed**; exact-lock Linux **436 passed / 11 skipped + Ruff PASS**; exact-lock Windows `.171` **435 passed / 12 skipped + Ruff PASS**. Exact-source AutoCAD 2027 Session-1 acceptance proved COM→native and native→COM serialization under one provider-local writer authority; COM timeout, native cancellation and tracked-transaction process loss quarantined the opposite lane before dispatch; late completion did not clear quarantine; and a real metadata `ENTITY_NOT_FOUND` typed refusal left `quarantined=false`. No C# source changed for D16, so the accepted bridge remained `0.8.5-d15`; deployed DLL SHA-256 remained `b9ffc7d25ba40fd33f404489a5e547367701148d4ffd1921cd5552447973a627`.
- D17 save-family persistence gate on 2026-09-19: focused Save/SaveAs/binding compatibility regression **101 passed / 3 skipped**; exact-lock Linux **442 passed / 11 skipped + Ruff PASS**; exact-lock Windows `.171` **441 passed / 12 skipped + Ruff PASS**. AutoCAD 2027 Session-1 acceptance proved real SaveAs returned the requested bound path with `Saved=true`, `DBMOD=0`, `persisted_clean=true` on the same live document. A real LINE was then fault-injected after SaveAs path verification but before clean-state readback; the provider refused success, latched COM/shared quarantine, and blocked a later Save before dispatch. No C# source changed; native bridge identity remains `0.8.5-d15`.
- D18 atomic document-PID bootstrap gate on 2026-09-19: focused D18 regression **4/4 passed** and affected native/protocol/client regression **37/37 passed**; exact-lock Linux **446 passed / 11 skipped + Ruff PASS**; exact-lock Windows `.171` **445 passed / 12 skipped + Ruff PASS**; C# Release/x64 **PASS / 0 errors** with the known MSB3277 warning families. AutoCAD 2027 Session-1 acceptance on bridge `0.8.6-d18` proved a runtime document that lost active status refused as `DOCUMENT_NOT_ACTIVE` with zero PID side effect, and an injected `BOOTSTRAP_FAULT_INJECTED` after provisional PID write aborted back to `document_pid=null` with entity state unchanged. Successful bootstrap then committed one PID and matching schema-v3 fingerprint readback. AutoCAD reports `DBMOD` changed `0→1` after the aborted transaction even though PID/entity identity rolled back; D18 therefore claims exact predecessor identity rollback, not persisted-clean rollback. Deployed candidate DLL SHA-256: `c058c0266664490e4fc05431320aab9fca11dcf6f0d8796241cd1823447f44ae`.

Last published RC2 reproducible-release checkpoint remains `bcb5c66` / tag `v0.4.0rc2`; its historical measurements are preserved below:

- Linux clean reconstruction A: **405 passed / 11 skipped + Ruff PASS**; Linux clean reconstruction B: **405 / 11 + Ruff PASS**.
- Windows `.171` clean reconstruction A: **404 passed / 12 skipped + Ruff PASS**; Windows clean reconstruction B: **404 / 12 + Ruff PASS**.
- Both Linux and Windows reconstruction pairs reproduce clean Git HEAD `bcb5c661bc6e01961525cd08c2ac5e0c0714b790`, source-tree SHA-256 `3f4b45b13aa8ab4a60f92c02b99c97eff1aca8baf8a9ac84246b41e5d36db3d8`, provider `0.4.0rc2`, the exact platform package map, and identical per-platform `pip freeze --all` output.
- Linux lock: `pylock.linux.toml`, SHA-256 `e7fe668b159c56233be4d008600fe80dd0778689a58b48ab55cecc670c45bf06`, 88 locked packages. Windows lock: `pylock.windows.toml`, SHA-256 `acbbc28bc07d26c2e07c76ab5d24f2e474945ba10cc14a0c9e94cca29867869e`, 89 locked packages.
- Windows provenance binds installed bridge DLL SHA-256 `18740fc6cc35a4e9117efed29fc98bd9c2d4836b77140d751e8d1628abd75a43` and observed AutoCAD PID 7888 / Session 1.
- `uv.lock` is non-canonical local resolver residue and is ignored; canonical exact dependency authority remains the two platform `pylock.*` files.
- B1 actual-I/O containment fixtures remain PASS on both platforms; B0/B4 accepted AutoCAD live evidence remains unchanged in scope.
- `git diff --check` and clean release-tree checks pass. C# bridge source was unchanged by B1/B2/B3; accepted bridge remains `0.8.2-mp7`.

Detailed accepted evidence scope remains in [`LIVE_ACCEPTANCE.md`](LIVE_ACCEPTANCE.md). Raw machine manifests are maintainer-local observations and are intentionally not part of the published documentation contract.

## 6. Important current truth boundaries

The following statements are intentional boundaries, not launch blockers:

- 87 public tools do **not** mean every route has native strong-integrity guarantees.
- COM/ActiveX remains an intentional compatibility/breadth lane.
- Native `3DSOLID` closure currently proves **planar translate only** under the strong-integrity loop.
- Native Boolean exact recovery, arbitrary XYZ/Rotate3D/scale parity and B-rep face/edge topology equivalence are not implied.
- `topology_verified=false` remains truthful for current solid semantics.
- Native whole-DWG semantic extraction is still bounded rather than a stable paged exhaustive reader.
- Deep/nested source dependency completeness is not universally guaranteed.
- Domain standards, engineering calculations, design checks, approvals and reports remain outside CDT-AutoCAD and belong to CDT-Engineer/domain systems.

These boundaries become implementation work only when a real production workflow requires them.

## 7. Solid semantic correction retained as current truth

Live AutoCAD 2027 work proved that `Solid3d.MassProperties.Extents` is not a translation-faithful WCS geometry box. The provider therefore uses `Solid3d.GeometricExtents` for canonical solid geometric extents in `solid-semantic-v2`, while retaining supported mass properties separately.

This correction is part of the accepted MP-G05 state and must not be reverted without new evidence.

## 8. Documentation authority from this checkpoint

Use the documents according to their concern:

- architecture/boundary/invariants → [`ARCHITECTURE.md`](ARCHITECTURE.md);
- current public status → this file;
- semantic-state/recovery contract → [`SEMANTIC_STATE_PROTOCOL.md`](SEMANTIC_STATE_PROTOCOL.md);
- live evidence → [`LIVE_ACCEPTANCE.md`](LIVE_ACCEPTANCE.md);
- operations → [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md);
- tool schemas/help → [`TOOL_GUIDE.md`](TOOL_GUIDE.md);
- pinned upstream control-plane input → [`SPEC_BASELINE.md`](SPEC_BASELINE.md) and `../specs/**`.

Maintainer roadmap and session notes are private and must not redefine this public current state.

## 9. Reopen rule

A closed capability is reopened only by:

1. regression evidence showing an existing guarantee is false;
2. a concrete CDT-Engineer/production-domain requirement that cannot be satisfied safely with the current surface; or
3. a measured operability/performance failure in a real workload.

Absent one of those triggers, **CDT-AutoCAD remains launch-ready and development attention moves to CDT-Engineer rather than speculative AutoCAD breadth.**
