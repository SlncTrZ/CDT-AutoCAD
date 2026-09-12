# Contributing to CDT-AutoCAD

CDT-AutoCAD uses a strict source-provenance policy. A contribution is accepted
only when its origin and licensing are clear.

## Contribution certification

By submitting a contribution, you represent that at least one of the following
is true:

1. you authored the contribution and have the right to submit it;
2. you received the contribution under terms that permit its inclusion in
   CDT-AutoCAD and have documented those terms; or
3. the contribution is based on material that is legally reusable and all
   required copyright/license notices are preserved and disclosed.

Do not submit code merely because it is publicly visible on GitHub, Stack
Overflow, a blog, a gist, a tutorial, another MCP server, or an AI-generated
response. Public visibility is not a license grant.

## No silent third-party copying

Before incorporating any external source code or substantial adaptation:

- identify the exact repository/file/version/commit;
- verify the applicable license;
- record required attribution and redistribution obligations;
- update `THIRD_PARTY_NOTICES.md`;
- mark the affected CDT-AutoCAD files clearly; and
- obtain maintainer approval before merge.

If a source is useful only for ideas, protocol understanding, interoperability,
or edge-case research, study it and implement the CDT-AutoCAD behavior
independently. Record material research references in documentation when they
meaningfully influenced the design.

## Dependencies

Prefer package-manager dependencies over vendoring source or binaries. New
direct dependencies must be declared in `pyproject.toml`, pinned through the
project's reproducible lock workflow, and reviewed for license compatibility.

Do not commit Autodesk DLLs, Python wheels, fonts, third-party archives, or
other vendor payloads unless the maintainer has explicitly approved both the
technical need and the redistribution rights.

## AI-assisted contributions

AI coding tools may be used as drafting/review aids, but the contributor remains
responsible for provenance, correctness, security, and license compliance.
Treat generated code as untrusted until reviewed. If generated output appears
to reproduce a distinctive external implementation, stop and perform a source
and license audit before submission.

## Commit and review expectations

- keep changes attributable and reviewable;
- do not erase existing copyright/provenance notices;
- do not rewrite history to hide source origin;
- preserve repository tests and acceptance evidence;
- use the project's normal review and verification gates before merge.

The Git history is the canonical contributor record. The CDT-AutoCAD copyright
holder is Trương Công Định (SlncTrZ). Additional ownership or assignment terms,
if required for a contribution, must be agreed in writing with the copyright
holder before merge.
