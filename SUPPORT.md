# Support

CDT-AutoCAD is currently operated as an RC/preview product under the `0.4.0rc1 / autocad-generic-v1-rc1` contract.

## Supported operational lane

Primary live support target:

- AutoCAD 2027 full;
- Windows x64;
- ActiveX COM `26.0` / `AutoCAD.Application.26`;
- Managed .NET `net10.0-windows`;
- provider contract `autocad-generic-v1-rc1` with 86 public MCP tools.

The `ezdxf` backend is supported for its documented headless DXF scope. Capabilities are not generalized across backends or AutoCAD versions without evidence.

## Before reporting a problem

Capture:

- provider version and contract version;
- backend and AutoCAD version;
- output from `system_status` and `system_capabilities`;
- `native_integrity_status` when the native route is involved;
- the failing MCP tool and the smallest synthetic reproduction;
- whether the state is read-only, `COMMITTED_VERIFIED`, `ROLLED_BACK_VERIFIED`, pending recovery, or uncertain;
- relevant request/correlation IDs and timestamps.

Do not attach private/customer drawings unless the owner explicitly decides they are safe to share. Prefer synthetic fixtures.

## Operational incidents

If mutation completion is unknown, rollback is unverified, or pending recovery is non-zero, stop dependent mutations. Do not blindly retry. Preserve the drawing, journal/checkpoint state and diagnostic logs, then follow `docs/OPERATIONS_RUNBOOK.md` and the recovery rules in `docs/SEMANTIC_STATE_PROTOCOL.md`.

## Public issues vs security reports

Use normal GitHub issues for reproducible bugs, documentation problems and feature requests that contain no sensitive data. Use `SECURITY.md` for vulnerabilities, path escapes, auth bypasses, arbitrary execution, identity confusion, recovery-integrity defects or sensitive-data exposure.

## Scope boundary

Domain standards, engineering calculations, architectural/civil/mechanical rules and drawing-design judgment belong to Domain Agents or upstream applications. CDT-AutoCAD support covers the generic CAD execution, identity, integrity, recovery and documented presentation/tooling surface.
