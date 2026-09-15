# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-15
> Status: **LAUNCH-READY / OPERATIONAL RC — B0 + B1 + B4 CLOSED**
> Current runtime/code checkpoint: `abeb9d9` (`fix: harden provider filesystem containment`)
> Primary certification lane: AutoCAD 2027 full · Windows x64 · COM `26.0` / `AutoCAD.Application.26` · Managed .NET `net10.0-windows`

This file is the canonical **public current-state authority**. It reports what is true now. It does not define architecture or future roadmap.

## 1. Launch decision

CDT-AutoCAD is sufficiently complete to launch in its intended role as a **Generic CAD Execution Engine** for CDT-Engineer and other higher-level production domains.

There is no known top-level caller-state, document-provenance, filesystem-containment, semantic-recovery or CAD-execution integrity blocker that must be closed before downstream engineering-domain work begins. B1 is closed with split assurance: provider-owned file I/O is descriptor/handle-bound at the actual I/O boundary against concurrent descendant namespace mutation beneath a trusted configured root, while AutoCAD COM methods that accept pathname strings only remain a bounded lane with pre/post verification and are not advertised as race-free.

From this checkpoint forward, AutoCAD capability expansion is **demand-driven**:

> CDT-Engineer names a concrete blocked workflow, required postcondition and verification invariant; CDT-AutoCAD then adds only the capability needed to satisfy that requirement.

The project is deliberately **not** pursuing full AutoCAD API parity or speculative native migration as an independent roadmap.

Launch-ready does not mean “every AutoCAD feature exists.” It means the provider now satisfies the execution-engine mission and has explicit refusal/assurance boundaries for capability that is not yet proven.

## 2. Current public identity

```text
provider_version: 0.4.0rc2
contract_version: autocad-generic-v1-rc2
public MCP tools: 86
execution_model: feature-based-chunks-streaming-v1
native bridge candidate: 0.8.2-mp7
```

The public tool count remains 86. The contract advanced to RC2 because the five native strong-integrity write entrypoints now require caller-supplied `document_pid` + `expected_parent_fp`; this is an intentional schema-strengthening change, not a new CAD capability family.

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
| B4 caller-state binding | **CLOSED / LIVE PASS** | Five native strong-integrity write tools require caller document PID + predecessor fingerprint; wrong-document/stale-parent requests refuse before journal/checkpoint/CAD mutation |
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

At current runtime/code checkpoint `abeb9d9`:

- Linux full regression from the canonical Linux locked environment: **405 passed / 11 skipped**.
- Windows `.171` full regression: **404 passed / 12 skipped**.
- B1 actual-I/O containment fixtures exercise descriptor/handle-bound read, atomic write/replace, contained directory creation and adversarial parent namespace swap on both platforms.
- Canonical Linux locked-environment Ruff gate: **PASS**.
- `git diff --check`: **PASS**.
- B0 SaveAs + artifact-seal AutoCAD 2027 Session-1 closure remains **2/2 passed** historical/current capability evidence; B1 does not retroactively turn AutoCAD pathname-only APIs into race-free primitives.
- B4 caller-state AutoCAD 2027 Session-1 acceptance remains **PASS** — wrong document PID, stale parent and stale replay all refuse with zero state change; valid caller predecessor commits and independently reads back the new fingerprint/entity count.
- A current B1 artifact-seal live retry reached Session 1/COM but AutoCAD rejected `Documents.Add` with `RPC_E_CALL_REJECTED` before B1 filesystem code executed; it is therefore recorded as non-evidence, not a B1 pass/fail.
- C# bridge source was unchanged by B1; accepted bridge remains `0.8.2-mp7` and no new native build claim is made.
- Windows exact-lock Ruff/release reproducibility is tracked by B2/B3 rather than inferred from the existing Windows venv.

Detailed evidence scope remains in [`LIVE_ACCEPTANCE.md`](LIVE_ACCEPTANCE.md) and retained machine evidence under ignored `artifacts/internal-evidence/`.

## 6. Important current truth boundaries

The following statements are intentional boundaries, not launch blockers:

- 86 public tools do **not** mean every route has native strong-integrity guarantees.
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
