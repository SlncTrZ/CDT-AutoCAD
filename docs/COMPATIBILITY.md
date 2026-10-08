# Compatibility and assurance limits

The declared native target is AutoCAD 2027 full on Windows x64:
ActiveX COM `26.0` / `AutoCAD.Application.26` and the Managed .NET bridge.
Other releases require separate acceptance. Headless DXF execution is a distinct
lane and cannot certify native AutoCAD behavior.

| Concern | Consumer rule |
| --- | --- |
| Provider/contract | Discover `help`, `system_status` and `system_capabilities`; pin the running contract hash. |
| Native strong-integrity writes | Require the planned document PID and predecessor fingerprint. |
| Execution | Use bounded feature chunks and verified native read-back. |
| Recovery | Uncertain completion fences dependent writes until reconciliation. |
| Filesystem containment | Provider-owned I/O is handle-bound; pathname-only vendor APIs retain bounded pre/post verification. |
| 3D integrity | Bounded planar `3DSOLID` translation does not imply unrestricted B-rep/3D parity. |
| Application ownership | Use the intended interactive session; do not terminate user-owned processes. |

[Live acceptance](LIVE_ACCEPTANCE.md) describes measured scopes at their original
source/runtime identities. A later source commit, package release or successful
headless CI run does not expand those scopes or certify a new native deployment.
Check current runtime capabilities before execution.

See the [tool contract](TOOL_GUIDE.md), [semantic state protocol](SEMANTIC_STATE_PROTOCOL.md)
and [operations runbook](OPERATIONS_RUNBOOK.md) for the normative behavior.
