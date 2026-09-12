# CDT-AutoCAD Documentation Index

> Updated: 2026-09-12

This page defines the documentation structure and authority boundaries for CDT-AutoCAD.

The governing rule is simple: **one concern, one source of truth**. Architecture, current status, roadmap and session notes must not compete with each other.

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
| Upstream spec pin | [`SPEC_BASELINE.md`](SPEC_BASELINE.md) | Which CDT-Engineer control-plane snapshot this provider consumes |
| Marketing demo | [`MARKETING_DEMO_RUNBOOK.md`](MARKETING_DEMO_RUNBOOK.md) | Public-safe demo procedure and approved claims |

### Important distinction: `docs/ARCHITECTURE.md` vs `specs/ARCHITECTURE.md`

`docs/ARCHITECTURE.md` is the **current provider-local architecture source of truth**.

`specs/ARCHITECTURE.md` is a **frozen upstream snapshot** copied from the pinned CDT-Engineer control-plane baseline. Files under `specs/` are read-only inputs for provider work and must not be treated as current CDT-AutoCAD implementation status.

## 2. Maintainer/private authority

Maintainer context is intentionally limited to exactly five ignored control files:

| File | Sole responsibility |
| --- | --- |
| `_private/AUDIT.md` | Current internal audit/state verdict |
| `_private/TECH_DEBT.md` | Known technical debt and explicit non-debt boundaries |
| `_private/DEVELOP_PLAN.md` | **Only roadmap authority**: what may be worked on next and under which trigger |
| `_private/HANDOFF.md` | What the latest work session completed |
| `_private/NEXT_SESSION.md` | Where the next session should start |

No additional TODO/roadmap/checkpoint/history tree belongs under `_private/`. Raw machine evidence worth retaining belongs under ignored `artifacts/internal-evidence/`.

Private files do not define public architecture or public contract behavior. Public docs must remain usable without them.

## 3. What each document must not do

To prevent source-of-truth drift:

- `ARCHITECTURE.md` must not become a release diary or backlog.
- `CURRENT_CHECKPOINT.md` must not design future architecture or schedule work.
- `DEVELOP_PLAN.md` must not redefine current architecture or claim runtime evidence.
- `AUDIT.md` must not become a roadmap.
- `HANDOFF.md` and `NEXT_SESSION.md` are disposable continuity notes, not durable design authorities.
- `LIVE_ACCEPTANCE.md` records evidence; it does not set product direction.
- `CHANGELOG.md` records history; history never overrides current architecture/status docs.
- `specs/**` remains pinned upstream input and is never silently edited to match local implementation.

## 4. Current product position

As of 2026-09-12, CDT-AutoCAD is **launch-ready / operational RC** for its intended role as a Generic CAD Execution Engine.

Current public identity remains:

```text
provider_version: 0.4.0rc1
contract_version: autocad-generic-v1-rc1
public_tools: 86
execution_model: feature-based-chunks-streaming-v1
native_bridge: 0.8.2-mp7
```

There is no known top-level blocker that requires further AutoCAD breadth before CDT-Engineer work begins. New AutoCAD capability is opened only from a concrete downstream engineering need plus a verification invariant. See [`CURRENT_CHECKPOINT.md`](CURRENT_CHECKPOINT.md) for the current measured state and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the stable boundary.

## 5. Change routing

When a change occurs, update only the authority that owns it:

- architecture/boundary/invariant changed → `ARCHITECTURE.md`;
- current version/capability/gate changed → `CURRENT_CHECKPOINT.md`;
- semantic-state contract changed → `SEMANTIC_STATE_PROTOCOL.md`;
- new accepted live evidence → `LIVE_ACCEPTANCE.md`;
- operational procedure changed → `OPERATIONS_RUNBOOK.md`;
- future work priority/trigger changed → `_private/DEVELOP_PLAN.md`;
- debt opened/closed → `_private/TECH_DEBT.md`;
- session ends → overwrite `_private/HANDOFF.md` and `_private/NEXT_SESSION.md`.

If a change affects more than one concern, update each owning document, but do not copy whole sections between them.

## 6. Publication boundary

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
