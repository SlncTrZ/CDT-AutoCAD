# CDT-AutoCAD Documentation Index

> Updated: 2026-09-19

This page defines the **published documentation structure** and authority boundaries for CDT-AutoCAD.

The governing rule is simple: **one concern, one source of truth**. Published architecture, current status, contract, operations and evidence documents must not compete with each other. Maintainer-local planning, session notes and raw machine evidence are intentionally outside the published documentation contract and cannot override it.

## 1. Public documentation authority

| Concern | Canonical authority | What it may contain |
| --- | --- | --- |
| Provider architecture | [`ARCHITECTURE.md`](ARCHITECTURE.md) | Stable component boundaries, execution lanes, invariants, responsibility model |
| Current public status | [`CURRENT_CHECKPOINT.md`](CURRENT_CHECKPOINT.md) | Current identity, launch state, verified gates, current capability boundaries |
| Public tool contract | [`TOOL_GUIDE.md`](TOOL_GUIDE.md) | Tool names/schemas/help material; contract-hash material |
| Semantic state/recovery | [`SEMANTIC_STATE_PROTOCOL.md`](SEMANTIC_STATE_PROTOCOL.md) | Normative state model, fingerprints, validation, rollback/recovery rules |
| Live acceptance evidence | [`LIVE_ACCEPTANCE.md`](LIVE_ACCEPTANCE.md) | Accepted live gates, evidence scope and runtime measurements |
| Operations | [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) | Start/stop/use/recovery/maintenance procedure |
| Threat model | [`THREAT_MODEL.md`](THREAT_MODEL.md) | Threats, trust boundaries and mitigations |
| Observability | [`OBSERVABILITY.md`](OBSERVABILITY.md) | Telemetry and diagnostic contracts |
| Drawing quality | [`DRAWING_QUALITY_ACCEPTANCE.md`](DRAWING_QUALITY_ACCEPTANCE.md) | User-facing drawing acceptance criteria |
| Drawing execution QA | [`DRAWING_EXECUTION_QA_WORKFLOW.md`](DRAWING_EXECUTION_QA_WORKFLOW.md) | Drawing build/review procedure |
| Reproducible baseline | [`REPRODUCIBLE_BASELINE.md`](REPRODUCIBLE_BASELINE.md) | Reproduction/build baseline |
| Upstream spec pin | [`SPEC_BASELINE.md`](SPEC_BASELINE.md) | Pinned CDT-Engineer control-plane snapshot consumed by this provider |
| Marketing demo | [`MARKETING_DEMO_RUNBOOK.md`](MARKETING_DEMO_RUNBOOK.md) | Public-safe demo procedure and approved claims |

### Important distinction: `docs/ARCHITECTURE.md` vs `specs/ARCHITECTURE.md`

`docs/ARCHITECTURE.md` is the **current provider-local architecture source of truth**.

`specs/ARCHITECTURE.md` is a **frozen upstream snapshot** copied from the pinned CDT-Engineer control-plane baseline. Files under `specs/` are read-only inputs for provider work and must not be treated as current CDT-AutoCAD implementation status.

## 2. Source-of-truth rules

To prevent documentation drift:

- `ARCHITECTURE.md` must not become a release diary or backlog.
- `CURRENT_CHECKPOINT.md` reports current truth; it must not design future architecture or schedule work.
- `LIVE_ACCEPTANCE.md` records accepted evidence; it does not set product direction.
- `SEMANTIC_STATE_PROTOCOL.md` owns the normative state/recovery contract.
- `OPERATIONS_RUNBOOK.md` owns consumer-visible operating procedure, not workstation-specific maintainer paths.
- `CHANGELOG.md` records history; historical evidence never overrides current architecture/status docs.
- `specs/**` remains pinned upstream input and is never silently edited to match local implementation.
- Maintainer-local roadmap, debt, handoff and raw evidence remain unpublished and cannot be required to understand the public contract.

## 3. Current product position

As of 2026-09-19, CDT-AutoCAD is **launch-ready / operational RC** for its intended role as a Generic CAD Execution Engine, with D15–D18 assurance closures live-accepted on AutoCAD 2027.

Current source/public contract identity is:

```text
provider_version: 0.4.0rc3
contract_version: autocad-generic-v1-rc3
public_tools: 87
execution_model: feature-based-chunks-streaming-v1
native_bridge: 0.8.6-d18
```

The last published/tagged release remains `v0.4.0rc2`; RC3 is the current source candidate and still requires final exact-source recertification. See [`CURRENT_CHECKPOINT.md`](CURRENT_CHECKPOINT.md) for measured current state and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the stable product boundary.

## 4. Change routing

When published truth changes, update the authority that owns it:

- architecture/boundary/invariant changed → `ARCHITECTURE.md`;
- current version/capability/gate changed → `CURRENT_CHECKPOINT.md`;
- semantic-state contract changed → `SEMANTIC_STATE_PROTOCOL.md`;
- new accepted live evidence → `LIVE_ACCEPTANCE.md`;
- operational procedure changed → `OPERATIONS_RUNBOOK.md`;
- security boundary changed → `THREAT_MODEL.md` and, where applicable, root `SECURITY.md`;
- public tool behavior changed → `TOOL_GUIDE.md`;
- reproducibility/provenance procedure changed → `REPRODUCIBLE_BASELINE.md`.

If a change affects more than one concern, update each owning document, but do not copy whole sections between them.

## 5. Publication boundary

Repository-level public/legal/provenance files remain at the project root:

- `README.md`
- `CHANGELOG.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `SUPPORT.md`
- `SOURCE_PROVENANCE.md`
- `COPYRIGHT.md`
- `THIRD_PARTY_NOTICES.md`
- `LICENSE`
- `NOTICE`

`README.md` is a product landing page and summary only; it must link to the canonical authorities instead of becoming another architecture/status source of truth.
