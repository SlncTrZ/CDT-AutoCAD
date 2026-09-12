# CDT-AutoCAD Documentation Index

Use this page to distinguish current operating authority from historical evidence.

## Start here

| Purpose | Current authority |
| --- | --- |
| Current product/contract state | `CURRENT_CHECKPOINT.md` |
| Operations | `OPERATIONS_RUNBOOK.md` |
| Release gate | `RELEASE_CHECKLIST.md` |
| Public tool contract | `TOOL_GUIDE.md` |
| Architecture direction | `ARCHITECTURE_UPGRADE_PLAN.md` |
| Generic execution decision | `ADR-002-GENERIC-CAD-EXECUTION-ENGINE.md` |
| Semantic integrity protocol | `SEMANTIC_STATE_PROTOCOL.md` |
| Live AutoCAD acceptance | `LIVE_ACCEPTANCE.md` |
| Native bridge acceptance | `NATIVE_BRIDGE_ACCEPTANCE.md` |
| Drawing quality | `DRAWING_QUALITY_ACCEPTANCE.md` |
| Drawing execution QA | `DRAWING_EXECUTION_QA_WORKFLOW.md` |
| Threat model | `THREAT_MODEL.md` |
| Observability | `OBSERVABILITY.md` |
| Marketing demo | `MARKETING_DEMO_RUNBOOK.md` |

## Operational status

Operational use begins **2026-09-12** under provider `0.4.0rc1`, contract `autocad-generic-v1-rc1`, and 86 public MCP tools. This is an RC/preview operational baseline, not a GA/stable-version declaration.

## Evidence

Machine-readable acceptance evidence lives in `docs/evidence/`. Evidence files describe the exact versions, fixtures and scopes under which they were produced; do not rewrite old evidence to match newer identities.

## Historical material

Files named `SESSION_HANDOFF_*`, `INITIAL_HANDOFF.md`, `N4_N6_AUDIT_*`, `N7_WORKING_CHECKPOINT_*` and similar checkpoint-specific documents are retained as historical engineering evidence. They may contain older tool counts, staged capability states or fingerprint versions. Use `CURRENT_CHECKPOINT.md` for present truth.

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
