# CDT-AutoCAD — Release and deployment

**Release:** `0.4.2` · tag `v.0.4.2` · MCP contract `autocad-generic-v1` · 87 tools.

[GitHub releases](https://github.com/SlncTrZ/CDT-AutoCAD/releases) publish source references. Do **not** infer that an installable Windows package, native DLL, workstation update or Gateway activation has been produced merely because a release exists.

## Install and connect

1. Use the [locked dependency installation](REPRODUCIBLE_BASELINE.md) and [README setup](../README.md) for the target Windows/Python environment. The validated native lane is AutoCAD 2027 full, Windows x64, with its separately versioned Managed .NET bridge. Other AutoCAD builds are **unverified**, not automatically supported.
2. Run the provider with the documented COM attach policy and explicit allowed roots. Supply auth using the managed environment for network/HTTP transport; never add the token to a command line, URL, repository or logs.
3. With AutoCAD already running in the intended interactive Windows session, verify `system_status`, `system_capabilities` and `native_integrity_status` before native writes. Compare runtime provider version, bridge DLL hash, document PID and fingerprint with the accepted [compatibility](COMPATIBILITY.md) and [live acceptance](LIVE_ACCEPTANCE.md) scope.
4. For split-host SlncTrZ-MCP, retain localhost listeners and authenticated/private forwarding; inspect the provider registration **after** the owner activates the new immutable build. A GitHub tag does not switch the active Gateway process.

## Auth-safe promotion / rollback

Stage a fresh immutable release directory; retain the previous provider wheel, runtime binding, validated bridge DLL and encrypted auth source. Record source SHA, artifact hashes and generation *separately* from package version. Do not rotate or copy credentials during upgrade. Test read-only status/discovery, a safe scratch-document mutation and readback. If auth or native verification fails, restore the previous managed provider entry and executable, then verify session/document health. Never edit an active production DWG merely to test an upgrade.

[Operating runbook](OPERATIONS_RUNBOOK.md) · [Tool guide](TOOL_GUIDE.md) · [Live evidence](LIVE_ACCEPTANCE.md) · [Security](../SECURITY.md).

Historical RC and native-certification identities in acceptance records are intentional provenance; they are **not** the current CDT package version.
