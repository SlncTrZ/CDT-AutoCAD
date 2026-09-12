# CDT-AutoCAD Source Provenance

Audit date: 2026-09-12
Initial audited public baseline: `0516fe37a91174d6af5af87c0778f4e126669c9b`
Initial audited tree: `95e92a75182f4e205706f3638bac56c421ce673f`

## Purpose

This file records engineering evidence about the origin and ownership boundary
of CDT-AutoCAD source material. Its purpose is to make the independently
developed project boundary explicit and to prevent future accidental copying of
third-party implementation source.

It is **not** a legal opinion, a plagiarism certificate, or proof that no
independently created line can resemble code in another project.

## Project ownership and development

CDT-AutoCAD is independently developed software directed, reviewed, accepted,
and owned by Trương Công Định (SlncTrZ). The project owner defines product
intent, architecture, acceptance criteria, implementation direction, review
standards, and release decisions.

The project-authored implementation includes:

- Python MCP provider, semantic core, COM/headless backends and orchestration;
- C# AutoCAD managed bridge and PID/native prototypes;
- tests and acceptance tooling;
- project-specific configuration and original documentation.

The `specs/` tree is a pinned/synchronized specification snapshot from the
owner-controlled `SlncTrZ/CDT_Engineer` architecture/specification repository;
it is not a third-party vendor code drop.

## Audited repository evidence

At the initial audited baseline:

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

## External dependencies are linked or installed, not copied

Python libraries are declared in `pyproject.toml` and locked in
`pylock.linux.toml` / `pylock.windows.toml`. The C# bridge references Autodesk
managed assemblies from the local AutoCAD installation with `Private=false`.
Those dependencies are separate works and are not copied into this repository.
See `THIRD_PARTY_NOTICES.md`.

## External technical information

Development may consult official API documentation, standards, product
manuals, protocol descriptions, and other public technical information needed
for interoperability and implementation correctness. Studying such information
does not make the external documentation or external implementations part of
CDT-AutoCAD source code.

The project policy is implementation-independent: external source code is not
to be copied or adapted into CDT-AutoCAD without explicit owner approval,
provenance review, license review, and a corresponding entry in
`THIRD_PARTY_NOTICES.md`.

## AI-assisted development

Automated coding assistants may be used as implementation, drafting, review, or
testing tools under project-owner direction. Their output is treated as draft
material until selected, reviewed, integrated, tested, and accepted by the
project owner/maintainer.

The project policy prohibits knowingly importing third-party implementation
source through an AI tool or any other channel without provenance and license
review. Use of an AI development tool is not represented here as a legal opinion
about copyrightability or non-infringement; those questions remain subject to
applicable law.

## Current conclusion

The audited evidence supports describing CDT-AutoCAD as an independently
developed implementation owned by Trương Công Định (SlncTrZ), using external
libraries and public APIs under their respective licenses while keeping
third-party implementation source outside the CDT-AutoCAD codebase unless it is
explicitly approved and documented.

No current CDT-AutoCAD file is intentionally identified as copied or adapted
from another software project's implementation source.

Do not strengthen this engineering record into an absolute legal claim that no
independently written code can resemble third-party code. Future contributions
must preserve the provenance boundary defined here and in `CONTRIBUTING.md`.
