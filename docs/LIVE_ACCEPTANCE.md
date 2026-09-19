# AutoCAD Live Acceptance Runbook

> Updated: 2026-09-19 +07:00
> Scope: COM/A3 live baseline + Managed .NET N0–N7/O1/G1/G2/G3 + 10k scale + Feature-based Chunks Streaming + bounded MP-G05 native 3DSOLID integrity loop + B0/B1/B4 + U1 core hardening + D8 stale-COM recovery + D15 request/checkpoint ownership
> Primary certification target: **AutoCAD 2027 full, Windows x64**
> Current source identity: `0.4.0rc3 / autocad-generic-v1-rc3 / 87 tools` · bridge `0.8.5-d15` · last published/tagged release remains `v0.4.0rc2`

## 0. Current production live gate

The reviewed public-promotion tree preserves both the historical COM/A3 acceptance below and the newer strong-integrity native evidence.

Current live native requirements already measured PASS on `.171` AutoCAD 2027 Session 1:

- current D15 bridge `0.8.5-d15`, document fingerprint schema v3; historical `0.8.3-u1`, `0.8.2-mp7` and earlier G1/G2/G3/scale/Feature Streaming artifacts below retain their original identities and are not rewritten;
- schema-agnostic metadata commit/readback/query plus exact R0/R1 recovery;
- one logical predecessor checkpoint across yielded native micro-chunks;
- micro-chunk maximum 32 entities and one batch mutation per AutoCAD Idle tick;
- 100 / 1,000 / 5,000 / 10,000 entity graduation with beginning/middle/end fault injection and exact predecessor R2 restoration;
- 10,000 entities = 313 native chunks, 10,000 unique persistent PIDs, zero pending recovery, stable AutoCAD/bridge process identity;
- Feature-based Chunks Streaming: Feature 1 commit, Feature 2 failure at native chunk 1 restores only Feature 2 predecessor, Feature 1 remains exact, Feature 3 continues successfully;
- feature presentation pacing recommendation 300 ms; live measured pause 300.295 ms;
- MP-G05 bounded native `3DSOLID` translation: persistent PID + `solid-semantic-v2`, stale-parent `STATE_DRIFT` refusal, `COMMITTED_VERIFIED`, exact R0 abort, post-commit integrity-failure detection and exact R2 predecessor restore; rotate/scale/Boolean/topology are not implied;
- no arbitrary C#/AutoLISP/shell/macro/free-text command surface;
- B0 document provenance re-certification: SaveAs + artifact sealing live fixtures PASS with verified renamed-document cleanup and no leaked basetemp/task residue;
- B1 provider-owned filesystem I/O closure is verified at the actual OS I/O boundary on Linux and Windows `.171` using descriptor/handle-bound adversarial namespace-swap fixtures; AutoCAD COM calls that accept pathname strings only retain bounded pre/post verification and inherit the prior B0 live provenance evidence rather than a new race-free claim;
- B4 caller-state binding live acceptance PASS on a disposable PID-bearing DWG: wrong document PID refused with exact fingerprint/entity-count preservation, stale parent refused with zero mutation, valid caller predecessor committed, and replaying the old predecessor after commit refused without changing accepted state.
- U1 exact-source Session-1 acceptance PASS on 2026-09-17 after the final bootstrap lock-scope review: explicit document lineage bootstrap holds the document lock through PID write/read-back and zero-entity semantic snapshot; LINE/TEXT/MTEXT/aligned-dimension/linear-dimension batches each returned `COMMITTED_VERIFIED`; native layer/color read-back matched; zero pending recovery remained; final `document.Save()` read-back was `Saved=true`, `DBMOD=0`. The deployed bridge hash in this final reload evidence is `0245e91fa4c067b86ec3fdb0797c709a661f78125cd12b0097789ec9f952661a`.
- U1 acceptance also reproduced and fixed AutoCAD dimension canonicalization: provisional layout is recomputed before fingerprinting, create validation accepts only the same dimension line rather than an unstable point parameterization, and in-transaction style re-extraction includes AutoCAD-created `Defpoints`.
- D8 live restart/rebind acceptance PASS on 2026-09-18: one source `ComBackend` cached AutoCAD 2027 PID `25048`, the fixture terminated exactly that process, launched a replacement in the same Interactive Session 1, observed ROT replacement PID `26788`, and the unchanged backend object detected the dead cached proxy, evicted generation-local app/document/view state and rebound to PID `26788`; final `connected=true`, `transaction_depth=0`. The fixture did not restart the backend object between cache and rebind. A tracked transaction is intentionally not auto-rebound after process death; that case quarantines and requires provider restart before later mutation.
- D15 request/checkpoint ownership acceptance PASS on 2026-09-19 under bridge `0.8.5-d15`: a real `logical.begin` reached the bridge and its response was intentionally dropped; reconnect discovered exactly one checkpoint using only the originating caller's locally retained raw UUID hashed to `owner_request_fp`. `bridge.recovery.list` exposed no raw owner; the live schema-v2 checkpoint manifest contained the owner fingerprint and no raw owner capability. A foreign caller could neither begin a second logical batch (`RECOVERY_PENDING`) nor resolve/finalize/use the checkpoint in a logical chunk (`RECOVERY_BINDING_MISMATCH`); document fingerprint/entity count remained unchanged before refusal. The originating owner completed exact R2 predecessor restoration and the run ended with zero pending recovery. Deployed DLL SHA-256: `b9ffc7d25ba40fd33f404489a5e547367701148d4ffd1921cd5552447973a627`.

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
u1-reload-lockfix-2026-09-17.json
u1-floorplan-lockfix-2026-09-17.json
d15-reload-owner-fp-2026-09-19.json
d15-checkpoint-ownership-owner-fp-2026-09-19.json
```

The B4 caller-binding acceptance on 2026-09-15 intentionally used a transient JSON report under the Windows temp directory and deleted it during verified cleanup; its measured result is summarized here rather than presented as a retained artifact.

Final close-gate result on the original promotion tree remains: contract identity **86 tools / 0.4.0rc1 / autocad-generic-v1-rc1**; Linux **316 passed / 5 skipped**; Windows `.171` **315 passed / 6 skipped**; C# Release/x64 with SDK `10.0.401` **0 errors / 3 known MSB3277 warning families**. The RC2 caller-binding tree at `872da68` independently passed Linux **398 / 9 skipped**, Windows `.171` **397 / 10 skipped**, focused public-native/schema **32/32**, B4 AutoCAD 2027 Session-1 live acceptance PASS, plus the preceding B0 live SaveAs/artifact-seal **2/2** closure. B1 later closed provider-owned actual-I/O containment at `abeb9d9`. B2/B3 then closed release reproducibility/tooling governance at clean checkpoint `bcb5c66`: two fresh exact-lock reconstructions per platform reproduced the same source/package identity, with Linux **405 / 11 + Ruff PASS** and Windows `.171` **404 / 12 + Ruff PASS** in both A/B environments. No C# source changed in B1/B2/B3, so those historical closure claims remain bound to `0.8.2-mp7`. U1 subsequently introduced bridge `0.8.3-u1` and the RC3/87-tool source identity with the live evidence summarized above. Older sections below preserve their historical identities and wording.

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

This lane was the required live evidence before A3 promotion. The then-current `0.4.0rc1` contract subsequently published the reviewed A3 surface; this section records that original acceptance boundary and is not a statement of current RC2 identity.

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

The current runtime is intentionally hybrid: COM/ActiveX remains the broad compatibility lane while selected Managed .NET families provide stronger PID/fingerprint/read-back/recovery guarantees. N0–N7 and O1 have passed their bounded native gates; N7 two-phase post-commit recovery includes activation-safe R2 restore after a real AutoCAD restart. Canonical N7 closure evidence is `n7-native-recovery-2026-09-11.json`.

For every newly accepted strong-integrity family, live AutoCAD 2027 evidence must prove both non-negotiable pillars:

1. **Data Integrity / Rollback** — injected failures/timeout uncertainty cannot advance state; native abort or recovery must be followed by read-back proving the exact predecessor fingerprint.
2. **Precise Identity / PID + Fingerprinting** — document/entity PID persistence, clone/remap behavior, duplicate detection, deterministic content fingerprints, caller-planned `document_pid` + `expected_parent_fp` binding before mutation, and stale/wrong-state blocking with independent read-back.

The .NET bridge is not promoted merely because an internal native gate passes. Selected strong-integrity tools were first published in `0.4.0rc1`; RC2 strengthens caller-state binding without expanding that native capability family. The broader COM lane may remain in place indefinitely where its bounded-integrity contract is sufficient; there is no standing requirement to replace it for parity. **MP-2 Python MCP hot reload is CLOSED / LIVE PASS**: stable supervisor/process/stable-URL, protocol/contract-hash promotion and `.171` AutoCAD 2027 Interactive Session 1 COM + required native bridge acceptance all passed. Historical evidence remains `mp2-hot-reload-2026-09-11.json`; the 86-tool/`0.8.2-mp7` identity was re-proven on 2026-09-12 with one isolated Session-1 owner at **20 successful reloads + 10 injected startup failures**, zero pending recovery and verified task/process cleanup in `mp2-hot-reload-current-identity-2026-09-12-isolated.json`.

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

Canonical evidence: `g1-generic-cad-execution-2026-09-11.json`. At that historical G1 checkpoint, scale graduation beyond 1,000 entities and cross-chunk logical all-or-nothing semantics had not yet closed; later G3/scale acceptance closed the 5,000/10,000 tiers and logical predecessor-recovery model. This sentence records evidence chronology rather than current project status.
