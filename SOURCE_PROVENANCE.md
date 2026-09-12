# CDT-AutoCAD Source Provenance

Audit date: 2026-09-12
Audited public commit: `0516fe37a91174d6af5af87c0778f4e126669c9b`
Audited tree: `95e92a75182f4e205706f3638bac56c421ce673f`

## Purpose

This file records engineering evidence about where CDT-AutoCAD source material
came from. It is intended to make ownership boundaries explicit and to prevent
future accidental copying of third-party code.

It is **not** a legal opinion, a plagiarism certificate, or proof that no
independently created line can resemble code in another project.

## Project-authored source

The audited repository contains the CDT-AutoCAD implementation developed for
the SlncTrZ project, including:

- Python MCP provider, semantic core, COM/headless backends and orchestration;
- C# AutoCAD managed bridge and PID/native prototypes;
- tests and acceptance tooling;
- project-specific configuration and original documentation.

The `specs/` tree is a pinned/synchronized specification snapshot from the
owner-controlled `SlncTrZ/CDT_Engineer` architecture/specification repository;
it is not a third-party vendor code drop.

At the audited commit:

- tracked files: 168;
- Python source files under `src/cdt_autocad`: 36;
- C# source files under `native/` and `prototypes/`: 26;
- audited Git history: 30 commits;
- Git author identity across those 30 commits: `SlncTrZ`;
- no `vendor`, `third_party`, `external`, `node_modules`, or `site-packages`
  source tree was present;
- no tracked DLL, EXE, wheel, archive, font, DWG, DXF, SAT, or other bundled
  third-party binary payload was found.

Commit metadata is useful provenance evidence but does not by itself establish
legal authorship of every line.

## External dependencies are linked/installed, not copied

Python libraries are declared in `pyproject.toml` and locked in
`pylock.linux.toml` / `pylock.windows.toml`. The C# bridge references Autodesk
managed assemblies from the local AutoCAD installation with `Private=false`.
Those dependencies are separate works and are not copied into this repository.
See `THIRD_PARTY_NOTICES.md`.

## Research/reference audit: U-C4N/Autocad-MCP

`src/cdt_autocad/backends/ezdxf_backend.py` has, since the project's initial
history, disclosed that the dual-engine shape and several edge-case choices
were informed by the MIT-licensed `U-C4N/Autocad-MCP` reference.

To distinguish research influence from source copying, a static comparison was
performed on 2026-09-12 between:

- CDT-AutoCAD commit `0516fe3`; and
- `U-C4N/Autocad-MCP` commit `abc2a82`.

### Comparison evidence

Same-purpose file comparisons produced low normalized similarity:

| CDT-AutoCAD file | Upstream comparison | Normalized line ratio | Longest exact normalized line block |
| --- | --- | ---: | ---: |
| `backends/ezdxf_backend.py` | upstream `backends/ezdxf_backend.py` | 3.06% | 8 lines |
| `backends/com_backend.py` | upstream `backends/com_backend.py` | 4.66% | 10 lines |
| `backends/base.py` | upstream `backends/base.py` | 4.54% | 3 lines |
| `server.py` | upstream `server.py` | 2.15% | 4 lines |
| `security.py` | upstream `security.py` | 4.55% | 2 lines |
| `config.py` | upstream `config.py` | 6.93% | 2 lines |

The exact overlaps observed in the audit were dominated by conventional API
signatures, import/error-handling boilerplate, AutoCAD/ezdxf API call shapes,
and small generic result dictionaries. The audit did **not** identify a large
verbatim implementation block or a copied upstream source file.

This comparison is engineering evidence only. Automated similarity checks can
miss semantically equivalent rewrites and can also flag independently written
boilerplate. For that reason the research-reference disclosure is retained
rather than erased.

## Documentation research

Architecture/research documents cite official Autodesk and Microsoft technical
documentation. Those sources were used to understand public APIs,
interoperability constraints, .NET behavior, AutoCAD transactions, identity,
and persistence. Research citations are not claimed as CDT-AutoCAD-owned
content.

## AI-assisted development

Automated coding assistants may be used as development tools under project-owner
direction. Generated suggestions are treated as untrusted draft material: the
project owner/maintainer selects, reviews, integrates, tests, and accepts the
result. The contribution policy prohibits knowingly importing third-party code
without provenance and license review.

Use of an AI development tool is not represented here as proof of legal
authorship or non-infringement; copyrightability and ownership questions remain
subject to applicable law.

## Current conclusion

For the audited tree, there is no evidence of a vendored third-party source
codebase or substantial verbatim copy of the explicitly disclosed MIT research
reference. CDT-AutoCAD should therefore be described accurately as an
independently developed implementation that uses external libraries/APIs and
was informed by documented technical references.

Do not strengthen this statement into an absolute claim such as "no line can
possibly resemble third-party code." Future contributions must preserve this
provenance boundary.
