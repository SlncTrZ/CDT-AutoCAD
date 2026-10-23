# CDT-AutoCAD

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](pyproject.toml)
[![MCP Protocol](https://img.shields.io/badge/MCP-Standard-orange.svg)](https://modelcontextprotocol.io)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%20x64%20%7C%20Linux%20(ezdxf)-lightgrey.svg)](docs/COMPATIBILITY.md)
[![Tools](https://img.shields.io/badge/tools-87%20callable-green.svg)](docs/TOOL_GUIDE.md)

Generic AutoCAD execution provider for Model Context Protocol (MCP) clients. It owns native CAD execution,
state verification and recovery; engineering design rules belong to CDT_Engineer.

Provider `0.4.3` · Contract `autocad-generic-v1` · 87 public tools.
Native acceptance is scoped to AutoCAD 2027 full on Windows x64. The headless DXF
lane has separate capabilities; other AutoCAD versions need their own acceptance.

---

## Quickstart / MCP Client Configuration

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "cdt-autocad": {
      "command": "uvx",
      "args": ["cdt-autocad-provider"],
      "env": {
        "CDT_AUTOCAD_BACKEND": "com",
        "CDT_AUTOCAD_COM_PROGID": "AutoCAD.Application.26",
        "CDT_AUTOCAD_COM_ATTACH_POLICY": "attach_only"
      }
    }
  }
}
```

### Cursor (`~/.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "cdt-autocad": {
      "command": "uvx",
      "args": ["cdt-autocad-provider"]
    }
  }
}
```

---

## Install and run

Use Python 3.12 and the platform lock from this checkout. On Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install pip==26.1.2
.\.venv\Scripts\python.exe -m pip install -r pylock.windows.toml
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation .
$env:CDT_AUTOCAD_BACKEND = 'com'
$env:CDT_AUTOCAD_COM_PROGID = 'AutoCAD.Application.26'
$env:CDT_AUTOCAD_COM_ATTACH_POLICY = 'attach_only'
.\.venv\Scripts\cdt-autocad.exe --transport stdio
```

Start AutoCAD in the same interactive user/session first. `attach_only` refuses
when the application is unavailable. For headless use, install the Linux lock
where applicable and select `CDT_AUTOCAD_BACKEND=ezdxf`.
See [reproducible installation](docs/REPRODUCIBLE_BASELINE.md).

## Use safely

1. Discover `help`, `system_status` and `system_capabilities`.
2. Bind native writes to `document_pid` and `expected_parent_fp`.
3. Execute bounded semantic features, then inspect/query/measure the result.
4. Continue only after verified commit or rollback. Reconcile uncertain completion
   before retrying; timeout does not prove native cancellation.

Tool presence does not imply native strong-integrity support for every route.
Configure allowed file roots; HTTP requires deployment-managed authentication.
No arbitrary shell, AutoLISP, macro or caller-supplied C# execution is exposed.

## Reference

- [Release, install and rollback](docs/RELEASE_AND_DEPLOYMENT.md).
- [Tool contract](docs/TOOL_GUIDE.md) — callable surface and guarantees.
- [Compatibility and assurance limits](docs/COMPATIBILITY.md).
- [Operations](docs/OPERATIONS_RUNBOOK.md).
- [Documentation index](docs/README.md) — architecture, security and QA references.

## License

Distributed under the open-source MIT License. See [LICENSE](LICENSE), [copyright](COPYRIGHT.md),
[third-party notices](THIRD_PARTY_NOTICES.md) and [source provenance](SOURCE_PROVENANCE.md).
