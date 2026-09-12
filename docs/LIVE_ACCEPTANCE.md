# AutoCAD Live Acceptance Runbook

> Updated: 2026-09-12 17:30 +07:00
> Scope: COM/A3 live baseline + Managed .NET N0–N7/O1/G1/G2/G3 + 10k scale + Feature-based Chunks Streaming + bounded MP-G05 native 3DSOLID integrity loop
> Primary certification target: **AutoCAD 2027 full, Windows x64**
> Public promotion: `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools` — **CLOSED/PUSHED at `0516fe3`** · current maintenance publication **`31186f5`**

## 0. Current production live gate

The reviewed public-promotion tree preserves both the historical COM/A3 acceptance below and the newer strong-integrity native evidence.

Current live native requirements already measured PASS on `.171` AutoCAD 2027 Session 1:

- current bridge `0.8.2-mp7`, document fingerprint schema v3; historical G1/G2/G3/scale/Feature Streaming artifacts below remain `0.8.1-g3` evidence and are not rewritten;
- schema-agnostic metadata commit/readback/query plus exact R0/R1 recovery;
- one logical predecessor checkpoint across yielded native micro-chunks;
- micro-chunk maximum 32 entities and one batch mutation per AutoCAD Idle tick;
- 100 / 1,000 / 5,000 / 10,000 entity graduation with beginning/middle/end fault injection and exact predecessor R2 restoration;
- 10,000 entities = 313 native chunks, 10,000 unique persistent PIDs, zero pending recovery, stable AutoCAD/bridge process identity;
- Feature-based Chunks Streaming: Feature 1 commit, Feature 2 failure at native chunk 1 restores only Feature 2 predecessor, Feature 1 remains exact, Feature 3 continues successfully;
- feature presentation pacing recommendation 300 ms; live measured pause 300.295 ms;
- MP-G05 bounded native `3DSOLID` translation: persistent PID + `solid-semantic-v2`, stale-parent `STATE_DRIFT` refusal, `COMMITTED_VERIFIED`, exact R0 abort, post-commit integrity-failure detection and exact R2 predecessor restore; rotate/scale/Boolean/topology are not implied;
- no arbitrary C#/AutoLISP/shell/macro/free-text command surface.

Canonical current evidence (machine-readable artifacts retained with the internal acceptance record
under these file names; not part of the published tree):

```text
g23-live-2026-09-11.json
g3-scale-100-2026-09-11.json
g3-scale-1000-2026-09-11.json
g3-scale-5000-2026-09-11.json
g3-scale-10000-2026-09-11.json
feature-stream-production-2026-09-11.json
mp-g07-visual-style-live-2026-09-12.json
mp2-hot-reload-current-identity-2026-09-12-isolated.json
mp-g10-acis-soak-live-2026-09-12.json
bridge-0.8.2-mp7-runtime-binding-2026-09-12.json
```

Final close-gate result on the original promotion tree remains: contract identity **86 tools / 0.4.0rc1 / autocad-generic-v1-rc1**; Linux **316 passed / 5 skipped**; Windows `.171` **315 passed / 6 skipped**; C# Release/x64 with SDK `10.0.401` **0 errors / 3 known MSB3277 warning families**. The current maintenance tree has now independently passed Linux **386 / 6 skipped**, Windows `.171` **385 / 7 skipped**, Ruff + compile, C# Release/x64 **0 errors / 3 inherited warning families**, current-identity MP-2 **20 success + 10 injected failure**, visual-style round-trip/restore, ACIS 10/100-part adversarial soak and the bounded MP-G05 native solid translation/recovery gate. Older sections below retain historical wording such as “50 tools” where that wording records the state of an earlier acceptance run.

## 1. Certification policy

CDT-AutoCAD separates application identification from certification.

- Default runtime ProgID stays unversioned: `AutoCAD.Application`.
- Primary certification target is AutoCAD 2027 full on Windows x64.
- AutoCAD 2027 ActiveX COM version is `26.0`; the live-verified versioned ProgID on Windows `.171` is `AutoCAD.Application.26`.
- Other AutoCAD releases are compatibility candidates, not automatically certified.
- AutoCAD LT is not a certification target because the provider's A3 native-solid lane requires full ActiveX/3D functionality.
- A release becomes supported/certified only after its native matrix gate passes; version parsing alone never grants capability support.

Known ActiveX COM release identifiers used only for runtime identification:

| COM version | AutoCAD release |
| --- | --- |
| 23.0 | 2019 |
| 23.1 | 2020 |
| 24.0 | 2021 |
| 24.1 | 2022 |
| 24.2 | 2023 |
| 24.3 | 2024 |
| 25.0 | 2025 |
| 25.1 | 2026 |
| 26.0 | 2027 |

Official Autodesk references:

- AutoCAD 2027 out-of-process/COM guidance: `https://help.autodesk.com/cloudhelp/2027/ENU/OARX-DevGuide-Managed/files/GUID-C8C65D7A-EC3A-42D8-BF02-4B13C2EA1A4B.htm`
- AutoCAD 2027 COM interoperability/version mapping: `https://help.autodesk.com/cloudhelp/2027/ENU/OARX-DevGuide-Managed/files/GUID-BFFF308E-CC10-4C56-A81E-C15FB300EB70.htm`
- AutoCAD 2027 installation requirements: `https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-ReleaseNotes/files/installation/INSTALLATION_REQUIREMENTS_AUTOCAD_2027.html`

## 2. Preconditions

For the primary gate:

1. Windows x64 with full AutoCAD 2027 installed and licensed.
2. AutoCAD 2027 is already running; acceptance uses `attach_only` and must not silently launch the application.
3. The provider checkout is current and its Python environment has the `[com]` dependencies (`pywin32`, Pillow) plus test dependencies.
4. No production drawing is active as the only unsaved work. Tests create disposable drawings and close only drawings they created.
5. Use the version-pinned ProgID for the primary certification run.

## 3. Windows Python environment

Keep the COM/test environment on the Windows local disk rather than inside the SMB-mounted source tree:

```powershell
$venv = Join-Path $env:USERPROFILE ".venvs\CDT-AutoCAD"
python -m venv $venv
& "$venv\Scripts\python.exe" -m pip install `
  "fastmcp>=3.4.5,<4" "ezdxf>=1.4,<2" "pydantic>=2,<3" `
  "pywin32>=306" "pillow>=10,<12" "pytest>=8,<9" "pytest-asyncio>=0.23,<1"
```

The live runner auto-detects this venv when present, otherwise it falls back to `python` from PATH.
On the current Windows `.171` lane this isolated environment is already prepared and generic tests
pass before AutoCAD installation.

## 4. Canonical primary-certification command

PowerShell from the `CDT-AutoCAD` repository root:

```powershell
./scripts/run_live_acceptance.ps1
```

The runner sets `CDT_AUTOCAD_LIVE_TEST=1`, expects release `2027`, prefers
`AutoCAD.Application.26`, and keeps the backend in `attach_only` mode. It writes separate JUnit
reports for the A2/A3.1 core gate, A3.2 advanced-dimension gate and A3.3 analysis gate so one failure
does not obscure which acceptance boundary failed.

On 2026-09-09, the final hidden `pythonw.exe` interactive run against full AutoCAD 2027 on Windows
`.171` passed A2, A3.1, A3.2 and A3.3. At that historical checkpoint A3 remained staged; those
surfaces were later intentionally promoted in `0.4.0rc1 / autocad-generic-v1-rc1` after the separate contract/review gates closed.

## 5. A2 live evidence covered

The A2 acceptance lane exercises a disposable native drawing and checks:

- ActiveX attachment and application version/release metadata;
- document create, DWG save, close and native reopen;
- layer create/current;
- LINE, CIRCLE, ARC, LWPOLYLINE, TEXT and HATCH;
- linear/aligned dimensions;
- object list/get/count/property edit/move/copy/rotate/scale;
- block create/insert;
- native transaction begin/commit;
- purge;
- layout create/current;
- paper-space viewport create/list/scale/lock/delete;
- zoom extents/window;
- native window PNG capture;
- PDF plot through `DWG To PDF.pc3`;
- application/document metadata after native save/reopen.

Mock/Linux checks remain regression evidence only and cannot close A2.

## 6. A3.1 live evidence covered

The A3 live lane exercises a second disposable drawing and checks:

- BOX, CYLINDER, SPHERE, CONE, TORUS and WEDGE;
- Boolean subtraction;
- 3D move, Rotate3D, ScaleEntity and Mirror3D;
- closed-profile extrusion;
- closed-profile revolve;
- 3D polyline path creation + profile sweep;
- 3DSOLID volume/type inspection;
- arbitrary normalized 3D view direction;
- native window PNG capture.

This lane was the required live evidence before A3 promotion. The current `0.4.0rc1` contract has since published the reviewed A3 surface; this section records the original acceptance boundary.

## 7. A3.2 advanced-dimension live evidence covered

A3.2 is implemented behind a capability-false staging boundary and has its own disposable-drawing
native test. It checks typed ActiveX creation for:

- angular dimension (`AddDimAngular`);
- radial dimension (`AddDimRadial`);
- diametric dimension (`AddDimDiametric`);
- X/Y ordinate dimensions (`AddDimOrdinate`);
- common `DIMENSION` object normalization and object counting.

The headless ezdxf lane independently verifies the same semantic methods plus DXF save/reopen.
Visual placement may differ between ezdxf rendering and native AutoCAD; live evidence is required
before public promotion.

## 8. A3.3 measurement/intersection live evidence covered

A3.3 has a third disposable-drawing native lane that verifies:

- typed line length and circle radius/area;
- `GetBoundingBox`-based current-space WCS extents across multiple entities;
- exact line/line intersection from ActiveX `IntersectWith` with no extension;
- normalized XYZ intersection payload and count.

The ezdxf backend independently verifies exact core measurement and WCS extents, but deliberately
refuses generic intersections because a complete exact solver is not implemented there. This is
intentional capability honesty rather than backend parity by approximation.

## 9. Acceptance decision

A2 may move from RC to CLOSED only when all of these are true:

- generic full suite passes;
- the primary AutoCAD 2027 live A2 test passes without capability mismatch;
- DWG save/reopen, viewport/screenshot and PDF output are real artifacts;
- no timeout-uncertain or document-scope integrity issue occurs;
- runtime status reports release `2027` / COM version `26.0` for the primary run;
- docs/help are updated with the verified result.

These A3 promotion rules were satisfied by the reviewed `0.4.0rc1 / autocad-generic-v1-rc1` publication. They remain the historical acceptance rationale: live evidence had to pass first, then the provider contract/version had to advance intentionally; native verification alone never published a tool.

## 10. Failure handling

- A COM timeout is integrity-uncertain: inspect the drawing before retrying because the abandoned call may still complete.
- Do not convert a live failure into a skip or mock PASS.
- If a specific operation fails, record AutoCAD release/version, ProgID, failing operation, HRESULT/error text and whether the disposable document was modified.
- Keep the RC/staged capability state unchanged until the failing native behavior is understood and retested.

## 11. Architecture migration acceptance

The current COM lane remains the public/runtime migration baseline. The accepted target architecture is an in-process AutoCAD Managed .NET bridge plus the provider-level Semantic State Loop. N0–N7 and O1 have passed their bounded native gates; N7 two-phase post-commit recovery now includes activation-safe R2 restore after a real AutoCAD restart. Remaining parity and promotion gates are tracked in the maintainer-internal native bridge acceptance record; canonical N7 closure evidence is `n7-native-recovery-2026-09-11.json`.

The native architecture lane must ultimately prove, on real AutoCAD 2027, both non-negotiable pillars:

1. **Data Integrity / Rollback** — injected failures/timeout uncertainty cannot advance state; native abort or recovery must be followed by read-back proving the exact predecessor fingerprint.
2. **Precise Identity / PID + Fingerprinting** — document/entity PID persistence, clone/remap behavior, duplicate detection, deterministic content fingerprints and `expected_parent_fp` state-drift blocking.

The .NET bridge is not promoted merely because an internal native gate passes. Selected strong-integrity tools are now public in `0.4.0rc1`, while full replacement of the broader COM backend remains separately gated by parity/evidence. **MP-2 Python MCP hot reload is CLOSED / LIVE PASS**: stable supervisor/process/stable-URL, protocol/contract-hash promotion and `.171` AutoCAD 2027 Interactive Session 1 COM + required native bridge acceptance all passed. Historical evidence remains `mp2-hot-reload-2026-09-11.json`; the current 86-tool/`0.8.2-mp7` identity was re-proven with one isolated Session-1 owner at **20 successful reloads + 10 injected startup failures**, zero pending recovery and verified task/process cleanup in `mp2-hot-reload-current-identity-2026-09-12-isolated.json`.

AutoCAD 2027 installs Microsoft .NET 10 when needed; the bridge build/runtime target must follow the AutoCAD 2027 Managed .NET compatibility requirements and secure loading policy.

## G1 Generic Batch Geometry live evidence — 2026-09-11

G1 is an internal native-bridge capability and does **not** add public MCP tools. The accepted bridge is `0.6.0-g1`; transport/protocol remain `cdt-autocad-native-v1` over the same Session-1 Named Pipe boundary.

Live AutoCAD 2027 Interactive Session 1 evidence on `.171` proves:

- `entity.batch.create`: LINE/CIRCLE/ARC/simple-LWPOLYLINE only; one native chunk is atomic, maximum 32 entities; cross-chunk atomicity is explicitly false; semantic verification is bounded at 2,048 entities.
- 100-entity tier: 4 chunks (`32/32/32/4`), 100 unique persistent PIDs, exact injected R0 rollback, 1.529 s total, 154.179 ms maximum chunk latency.
- 1,000-entity tier: 32 chunks, 1,000 unique persistent PIDs, exact injected R0 rollback, 7.845 s total, 249.987 ms maximum chunk latency; working-set delta +159,830,016 B and private-bytes delta +194,674,688 B for the measured AutoCAD process.
- `entity.batch.transform`: persistent-PID targeting for LINE/CIRCLE/ARC/simple-LWPOLYLINE with only planar `translate`, `rotate_z` and `scale_uniform`; shear/general affine/non-uniform scale is not enabled.
- `entity.batch.insert_blocks`: insertion is authorized by persistent `BLOCK_DEFINITION` PID already visible in the predecessor semantic fingerprint; block-name/Handle authority and unreferenced-definition insertion are not accepted.
- G1-B/C live run: all three transform kinds `COMMITTED_VERIFIED`; injected transform and block-insert R0 faults `ROLLED_BACK_VERIFIED`; 4 committed inserts produced 4 unique PIDs; zero pending recoveries.
- Batch R1: committed transform of 4 targets and committed insertion of 4 block references both compensated to the exact predecessor fingerprint and exact ModelSpace membership; zero pending recoveries.
- Batch R2: committed insertion of 2 block references restored from immutable checkpoint through activation-safe document replacement; `runtime_document_id` changed as expected, predecessor fingerprint and ModelSpace membership were exact, zero pending recoveries.
- Final regressions on the G1 production tree: Linux `270 passed / 5 skipped`; Windows `.171` `269 passed / 6 skipped`; native Release build 0 errors with the existing Autodesk-reference MSB3277 warning families; Linux/Windows compileall and `git diff --check` pass.

Canonical evidence: `g1-generic-cad-execution-2026-09-11.json`. Scale graduation beyond 1,000 entities remains open; 5,000/10,000 tiers are not claimed. Cross-chunk logical all-or-nothing semantics remains G3.
