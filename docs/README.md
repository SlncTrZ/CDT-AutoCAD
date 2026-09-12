# CDT-AutoCAD Documentation Index

This page is the map for the **published documentation set**: product-facing contracts and guides
needed to use CDT-AutoCAD, including its provider contract, operation and quality rules.

## Publication boundary

Only product-facing contracts/guides are published here. Research, development, roadmap, ADR,
session-handoff, internal acceptance/evidence and other internal-direction material is
maintainer-internal under the ignored `_private/` workspace and is **not** part of the published
tree. Published pages must remain usable without `_private/`. `_private/README.md` records the
internal layout, including the old-to-new path mapping for material that moved out of `docs/`.

## Start here

| Purpose | Current authority |
| --- | --- |
| Current product/contract state | `CURRENT_CHECKPOINT.md` |
| Public tool contract | `TOOL_GUIDE.md` |
| Operations | `OPERATIONS_RUNBOOK.md` |
| Semantic integrity protocol | `SEMANTIC_STATE_PROTOCOL.md` |
| Live AutoCAD acceptance | `LIVE_ACCEPTANCE.md` |
| Drawing quality | `DRAWING_QUALITY_ACCEPTANCE.md` |
| Drawing execution QA | `DRAWING_EXECUTION_QA_WORKFLOW.md` |
| Threat model | `THREAT_MODEL.md` |
| Observability | `OBSERVABILITY.md` |
| Reproducible baseline | `REPRODUCIBLE_BASELINE.md` |
| Pinned spec baseline | `SPEC_BASELINE.md` |
| Marketing demo | `MARKETING_DEMO_RUNBOOK.md` |

Pinned control-plane snapshots live in `../specs/` and are read-only for provider work.

## Operational status

Operational use begins **2026-09-12** under provider `0.4.0rc1`, contract `autocad-generic-v1-rc1`, and 86 public MCP tools. This is an RC/preview operational baseline, not a GA/stable-version declaration.

## Evidence

`LIVE_ACCEPTANCE.md` is the public evidence reference reported by `system_status` and
`native_integrity_status`; it states the accepted live gates and their scope. The machine-readable
acceptance artifacts behind those statements are retained with the internal acceptance record under
their original file names and are not part of the published tree. Those artifacts describe the exact
versions, fixtures and scopes under which they were produced; do not rewrite old evidence to match
newer identities.

## Ownership and legal

Repository-level legal and provenance files are at the project root:

- `LICENSE`
- `COPYRIGHT.md`
- `NOTICE`
- `SOURCE_PROVENANCE.md`
- `THIRD_PARTY_NOTICES.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `SUPPORT.md`
- `CHANGELOG.md`
