# CDT AutoCAD Provider

> Status: A1 public contract verified · A2 COM backend staged, live verification pending · Version: 0.2.0 · Updated: 2026-09-09

This provider is the first CDT_Engineer reference implementation. The default backend remains
`ezdxf`, with a bounded 42-tool `autocad-a1-v1` MCP surface. An A2 `com` backend is now staged behind
explicit configuration so the same A0/A1 operations can target a live Windows AutoCAD session and
native DWG without changing the public contract before the real-AutoCAD gate passes.

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

The staged A2 COM implementation additionally contains backend-level viewport create/list/scale/lock/
delete, live zoom and native-window PNG capture. These methods are deliberately **not yet promoted to
MCP tools**: ActiveX behavior and screenshot fidelity still require the real Windows + AutoCAD lane.
The existing 42-tool contract therefore remains unchanged until that gate passes.

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

## A2 live verification lane

Generic CI uses mocks and remains cross-platform. The destructive/live smoke is opt-in:

```text
CDT_AUTOCAD_LIVE_TEST=1 pytest -q tests/test_com_backend.py
```

Run that only on Windows with AutoCAD already running when using the default `attach_only` policy.
The smoke creates a disposable drawing, exercises basic geometry, layout + viewport operations, zoom,
PNG capture and native DWG save, then closes the created document without saving further changes.

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
reference in `_private/reference/autocad/Autocad-MCP`, especially its dual-engine, capability-refusal,
COM STA, viewport, screenshot, layout, transaction and timeout-integrity patterns. CDT_Engineer does
not vendor the upstream monolithic server surface; the public contract is normalized to CDT/SlncTrZ.
