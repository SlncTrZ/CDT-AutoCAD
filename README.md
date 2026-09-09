# CDT AutoCAD Provider

> Status: A2 release candidate · primary AutoCAD 2027 live acceptance pending · Version: 0.3.0rc1 · Updated: 2026-09-09

This provider is the first CDT_Engineer reference implementation. The default backend remains
`ezdxf`; the public release-candidate contract is now a bounded 50-tool `autocad-a2-v1-rc1` surface.
The `com` backend adds live Windows AutoCAD, native DWG, viewport management, live zoom and screenshot
capture while preserving typed refusal on the headless backend.

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

A2 viewport create/list/scale/lock/delete, live zoom and native-window PNG capture are now public MCP
release-candidate tools. They remain **live-unverified** until the primary AutoCAD 2027 Windows gate
passes. Mock/Linux verification does not substitute for that acceptance gate.

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

## AutoCAD version policy

The runtime default remains `AutoCAD.Application` so normal use can attach to the registered full
AutoCAD installation. The **primary certification target is AutoCAD 2027 full on Windows x64**, whose
versioned ActiveX ProgID is `AutoCAD.Application.26.0`. Other releases are compatibility candidates
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

The runner targets AutoCAD 2027 / `AutoCAD.Application.26.0`, preserves pytest/JUnit evidence under
ignored `artifacts/live-acceptance/`, and executes both A2 and A3.1 live lanes. AutoCAD must already
be running because acceptance uses the fail-closed `attach_only` policy.

A2 implementation remains RC until this native gate passes and the evidence is reviewed.

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
