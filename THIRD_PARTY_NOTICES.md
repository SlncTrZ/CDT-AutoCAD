# Third-Party Notices

This file distinguishes CDT-AutoCAD's original project source from external
software, APIs, and research references.

## Distribution model

CDT-AutoCAD does **not** vendor the source code or binary payloads of the Python
packages listed below. They are declared as package-manager dependencies in
`pyproject.toml` and resolved by `pylock.linux.toml` / `pylock.windows.toml`.
Each dependency remains licensed by its own copyright holders.

The table records direct dependencies and project tooling audited on
2026-09-12. It is not a substitute for the license metadata shipped by those
packages. If CDT-AutoCAD is later distributed as a self-contained installer,
container, frozen executable, or other bundle that includes third-party
payloads, a release-specific SBOM and complete required license texts must be
included with that bundle.

| Component | Audited resolved version(s) | Role | License / upstream metadata |
| --- | --- | --- | --- |
| FastMCP | 3.4.7 | runtime MCP framework | Apache-2.0 |
| ezdxf | 1.4.4 | headless DXF backend | MIT |
| Pydantic | 2.13.5 | runtime validation/models | MIT |
| HTTPX | 0.28.1 | runtime HTTP client | BSD-3-Clause |
| Uvicorn | 0.52.4 | runtime HTTP server | BSD-3-Clause |
| pywin32 | 312 (Windows) | Windows COM integration | Python Software Foundation license |
| Pillow | 11.3.0 (Windows lock), 12.3.0 (Linux lock) | optional image support | MIT-CMU |
| Matplotlib | 3.11.1 | optional headless rendering | Matplotlib license (PSF-compatible project license; package may contain separately licensed bundled assets) |
| pytest | 8.4.2 | development/test | MIT |
| pytest-asyncio | 0.26.0 | development/test | Apache-2.0 |
| Ruff | 0.16.7 | development/lint | MIT |
| Hatchling | 1.32.0 | build backend | MIT |

Package home pages and complete license texts are available through the package
metadata and distributions obtained from PyPI. The platform lock files are the
canonical reproducibility record for the exact dependency set used by an
individual audited environment.

## Autodesk AutoCAD managed assemblies

The native C# projects reference these assemblies from a locally installed
AutoCAD 2027 installation:

- `AcCoreMgd.dll`
- `AcDbMgd.dll`
- `AcMgd.dll`

The project files set these references as non-private (`Private=false`). The
assemblies are not tracked in this repository and are not redistributed by
CDT-AutoCAD. Autodesk software, APIs, SDK material, and documentation remain
subject to Autodesk's own licenses and terms.

## Research/reference implementation: U-C4N/Autocad-MCP

The header of `src/cdt_autocad/backends/ezdxf_backend.py` records that the
project's dual-engine shape and several edge-case choices were informed by the
MIT-licensed `U-C4N/Autocad-MCP` project.

Audited reference identity on 2026-09-12:

- repository: `U-C4N/Autocad-MCP`;
- audited upstream commit: `abc2a82`;
- upstream license: MIT;
- upstream copyright notice: `Copyright (c) 2026 Umutcan Edizsalan`.

This is recorded as a **research/reference source, not a vendored dependency**.
No upstream source file or binary is included in CDT-AutoCAD. The accompanying
`SOURCE_PROVENANCE.md` documents the static similarity audit performed against
this reference and the limits of that audit.

If third-party source from this or any other project is intentionally copied or
adapted into CDT-AutoCAD in the future, the contribution policy requires that
the exact origin, license, copyright notice, modified files, and redistribution
obligations be recorded here before merge.

## Documentation and interoperability references

Project research and architecture documents link to Autodesk and Microsoft
technical documentation. Linking to or studying public API documentation does
not transfer ownership of that documentation to CDT-AutoCAD. Any quotations or
reproduced material remain subject to the rights of their original owners.
