# CDT AutoCAD Provider

> Status: A2 release candidate · N0–N6 + O1 closed/live-pass · N7 in progress/not closed · Version: 0.3.0rc1 · Updated: 2026-09-10 +07:00

This provider is the first CDT_Engineer reference implementation. The default backend remains
`ezdxf`; the public release-candidate contract is a bounded 50-tool `autocad-a2-v1-rc1` surface.
The current `com` backend adds live Windows AutoCAD, native DWG, viewport management, live zoom and screenshot capture while preserving typed refusal on the headless backend. A staged in-process C# Managed .NET native bridge now provides persistent PID-aware semantic snapshots plus internal typed LINE/CIRCLE/ARC/simple-LWPOLYLINE create/update/delete over local Named Pipe IPC. N7 two-phase post-commit recovery is actively under development but is not yet accepted; current COM/ezdxf behavior therefore remains the public migration baseline.

## Current public scope

- FastMCP provider with Streamable HTTP `/mcp` and stdio.
- HTTP Bearer authentication with fail-closed launch guard.
- Read-only `help`, `system_status`, `system_capabilities`.
- DXF document new/open/info/save/save-as on `ezdxf`.
- Native DWG/DXF document lifecycle on staged `com` backend.
- Entity list/get/count and A0/A1 creation/modification parity.
- LINE, CIRCLE, ARC, LWPOLYLINE, TEXT, HATCH and linear/aligned dimensions.
- Layer list/create/set-current.
- Block list/create/insert.
- Layout list/create/set-current with current-space routing.
- Audit/purge and PDF export according to backend capability.
- Snapshot transactions on `ezdxf`; native AutoCAD undo marks on `com`.
- Allowed-root path containment and bounded backend calls.
- Typed MCP errors for unsupported capabilities, state conflicts and timeouts.

A2 viewport create/list/scale/lock/delete, live zoom and native-window PNG capture are public MCP
release-candidate tools and are **live-verified** on the primary AutoCAD 2027 Windows lane. Mock/Linux verification remains regression evidence only and does not substitute for native acceptance.

## Target architecture — N0–N6 + O1 accepted; N7 recovery in progress

The accepted target architecture is:

```text
MCP / SlncTrZ Gateway
        |
        v
Python Provider / Semantic Core
  - policy/orchestration
  - SourceSemanticModel / ActionSpec
  - canonicalization + PID/fingerprinting
  - semantic diff + deterministic validation
  - rollback/state-chain audit
        |
        | local typed IPC
        v
C# AutoCAD Managed .NET Native Bridge
  - in-process in acad.exe
  - Document/Database/ObjectId access
  - native transactions
  - persistent PID metadata
  - semantic extraction + event-assisted deltas
        |
        v
AutoCAD DWG
```

Two invariants are non-negotiable: **Data Integrity / Rollback** and **Precise Identity / PID + Fingerprinting**. Every engineering mutation must end in either a verified committed state or a verified restoration of the predecessor state. The next step is blocked on drift, uncertainty, commit-integrity failure or rollback failure.

Screenshots/Vision are not the geometry oracle. Native semantic data drives step validation; Vision remains useful for raster-source ingestion and final visual/user review.

Architecture status: `N0–N7 CLOSED/LIVE PASS`, `O1 CLOSED/LIVE PASS`. N1 supplies typed semantic contracts/fingerprints/state-chain primitives; N2 persistent document/entity PID policy; N3 the in-process `net10.0-windows` bridge and bounded same-user/local/same-session Named Pipe transport; N4 authoritative native semantic snapshot/fingerprint extraction; N5 typed transactional mutation with parent-fingerprint binding and R0 rollback; N6 deterministic semantic validation/state-chain orchestration; O1 expands the accepted internal mutation surface to LINE/CIRCLE/ARC/simple-LWPOLYLINE create/update/delete; N7 adds two-phase in-transaction validation plus checkpoint-backed R1/R2 recovery, including activation-safe R2 after real AutoCAD restart. Public routing remains the 50-tool COM/ezdxf release-candidate baseline. See `docs/evidence/n7-native-recovery-2026-09-11.json`.

Current implementation status is canonical in [`docs/CURRENT_CHECKPOINT.md`](docs/CURRENT_CHECKPOINT.md). Architecture details are in [`docs/ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md`](docs/ADR-001-NATIVE-BRIDGE-SEMANTIC-STATE-LOOP.md), [`docs/SEMANTIC_STATE_PROTOCOL.md`](docs/SEMANTIC_STATE_PROTOCOL.md), [`docs/N2_PID_ACCEPTANCE.md`](docs/N2_PID_ACCEPTANCE.md), [`docs/NATIVE_BRIDGE_ACCEPTANCE.md`](docs/NATIVE_BRIDGE_ACCEPTANCE.md) and [`docs/ARCHITECTURE_UPGRADE_PLAN.md`](docs/ARCHITECTURE_UPGRADE_PLAN.md).

## Runtime configuration

Environment variables:

```text
CDT_AUTOCAD_ALLOWED_PATHS       path-list of allowed CAD/PDF roots; defaults to launch directory
CDT_AUTOCAD_MAX_DXF_BYTES       maximum input CAD file size at the provider boundary; default 50 MiB
CDT_AUTOCAD_CALL_TIMEOUT        ordinary headless deadline in seconds; default 120
CDT_AUTOCAD_RENDER_TIMEOUT      headless render/PDF deadline in seconds; default 300
CDT_AUTOCAD_UNDO_DEPTH          ezdxf snapshot undo depth; default 10; 0 disables undo/redo
CDT_AUTOCAD_TRANSACTION_DEPTH   maximum tracked transaction depth; default 8
CDT_AUTOCAD_BACKEND             ezdxf (default) | com
CDT_AUTOCAD_COM_PROGID          COM ProgID; default AutoCAD.Application
CDT_AUTOCAD_COM_ATTACH_POLICY   attach_only (default) | attach_or_start
CDT_AUTOCAD_COM_TIMEOUT         live COM deadline in seconds; default 60
CDT_AUTOCAD_AUTH_TOKEN          required for every HTTP launch
CDT_AUTOCAD_ALLOW_REMOTE_HTTP   must be true in addition to auth for non-loopback bind
```

`attach_only` is intentionally the default: selecting COM must not silently launch AutoCAD. A timed-
out COM mutation is treated as uncertain because the abandoned STA call may still land in AutoCAD;
verify the drawing before retrying to avoid double-applying an operation.

Credentials must be supplied by deployment/runtime configuration. Do not commit them.

### Python MCP hot reload requirement

A supported Python MCP/provider hot-reload lifecycle is now mandatory before deep N8/N9/N10 migration. Reload must drain or deterministically reject in-flight work, expose one authoritative generation, verify the new generation through health/status, retain the previous healthy generation on reload failure, and preserve authentication/path-policy/public-contract invariants. This is not yet implemented. The `.171` Windows host has `cloudflared` available for a stable externally reachable endpoint; tunnel/service configuration remains separate infrastructure work and has not yet been changed for CDT-AutoCAD.

## AutoCAD version policy

The runtime default remains `AutoCAD.Application` so normal use can attach to the registered full
AutoCAD installation. The **primary certification target is AutoCAD 2027 full on Windows x64**, whose
versioned ActiveX ProgID is `AutoCAD.Application.26`. Other releases are compatibility candidates
and require their own native evidence before they are called certified. `system_status` reports the
primary certification target and, after COM attachment, the detected application version, COM
version and mapped AutoCAD release.

See [`docs/LIVE_ACCEPTANCE.md`](docs/LIVE_ACCEPTANCE.md) for the version matrix and gate procedure.

## Optional dependencies

Headless PDF rendering:

```text
pip install 'cdt-autocad-provider[render]'
```

Live Windows AutoCAD automation and staged screenshot capture:

```text
pip install 'cdt-autocad-provider[com]'
```

The COM extra supplies `pywin32` plus Pillow for PNG window capture. Missing optional dependencies
remain capability/refusal conditions rather than silent fallbacks.

## A2 release-candidate verification lane

Generic CI uses mocks and remains cross-platform. The primary certification lane is opt-in and
version-pinned:

```powershell
./scripts/run_live_acceptance.ps1
```

The runner targets AutoCAD 2027 / `AutoCAD.Application.26`, preserves separate pytest/JUnit
evidence under ignored `artifacts/live-acceptance/`, and executes A2/A3.1 plus independent A3.2
advanced-dimension and A3.3 measurement/intersection lanes. AutoCAD must already be running because
acceptance uses the fail-closed `attach_only` policy.

A2 native acceptance has passed on Windows `.171` against full AutoCAD 2027; the RC identity is
retained until an explicit promotion/release decision. A3.1 solid, A3.2 advanced-dimension and A3.3
analysis methods are live-verified but remain backend-staged/capability-false until explicit
promotion; the public MCP surface remains 50 tools.

## Development checks

The repository may live on a filesystem that does not support Python venv symlinks. A local `.deps/`
directory can be used with `pip --target` and is gitignored.

Example test invocation when dependencies are available in `.deps/`:

```text
PYTHONPATH=.deps:src python3 -m pytest -q
```

Ruff should be run read-only (`ruff check src tests`) on this mounted workspace; avoid automatic
fixing if the filesystem does not provide reliable atomic replacement semantics.

## Tool guide

Runtime `help` is sourced from [`docs/TOOL_GUIDE.md`](docs/TOOL_GUIDE.md) and fingerprints that
content with SHA-256.

## Reference provenance

A0/A1 and staged A2 behavior were informed primarily by the MIT-licensed `U-C4N/Autocad-MCP`
reference used during the original CDT_Engineer monorepo research, especially its dual-engine,
capability-refusal, COM STA, viewport, screenshot, layout, transaction and timeout-integrity patterns.
CDT-AutoCAD does not vendor the upstream monolithic server surface; the public contract is normalized
to CDT/SlncTrZ.
