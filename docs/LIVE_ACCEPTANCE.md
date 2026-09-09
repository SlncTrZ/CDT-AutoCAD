# AutoCAD Live Acceptance Runbook

> Updated: 2026-09-09
> Scope: A2 COM acceptance + A3.1 native 3D verification
> Primary certification target: **AutoCAD 2027 full, Windows x64**

## 1. Certification policy

CDT-AutoCAD separates application identification from certification.

- Default runtime ProgID stays unversioned: `AutoCAD.Application`.
- Primary certification target is AutoCAD 2027 full on Windows x64.
- AutoCAD 2027 ActiveX COM version is `26.0`; version-pinned ProgID is `AutoCAD.Application.26.0`.
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
$env:CDT_AUTOCAD_LIVE_TEST = "1"
$env:CDT_AUTOCAD_EXPECT_RELEASE = "2027"
$env:CDT_AUTOCAD_COM_PROGID = "AutoCAD.Application.26.0"
python -m pytest -q tests/test_com_backend.py tests/test_com_3d.py
```

`CDT_AUTOCAD_EXPECT_RELEASE` makes the live A2 gate fail if the attached ActiveX application does not identify as the expected AutoCAD release. The test backend still uses `attach_only`.

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

A3 methods remain staged/non-public until this lane passes on the primary certification target and the result is reviewed.

## 7. Acceptance decision

A2 may move from RC to CLOSED only when all of these are true:

- generic full suite passes;
- the primary AutoCAD 2027 live A2 test passes without capability mismatch;
- DWG save/reopen, viewport/screenshot and PDF output are real artifacts;
- no timeout-uncertain or document-scope integrity issue occurs;
- runtime status reports release `2027` / COM version `26.0` for the primary run;
- docs/help are updated with the verified result.

A3.1 may be promoted to public tools/capabilities only after its live test passes and the capability contract/version is intentionally advanced.

## 8. Failure handling

- A COM timeout is integrity-uncertain: inspect the drawing before retrying because the abandoned call may still complete.
- Do not convert a live failure into a skip or mock PASS.
- If a specific operation fails, record AutoCAD release/version, ProgID, failing operation, HRESULT/error text and whether the disposable document was modified.
- Keep the RC/staged capability state unchanged until the failing native behavior is understood and retested.
