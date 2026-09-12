# Marketing Demo Runbook — CDT-AutoCAD

> Updated: 2026-09-12 17:30 +07:00
> Product identity: `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools`
> Positioning: **RC/preview — Generic CAD Execution Engine for AI agents**
> Primary demo target: AutoCAD 2027 full / Windows x64 / Interactive Session 1
> Current runner bridge identity: `0.8.2-mp7`; the evidence table in §4 intentionally preserves the older bridge identity of the recorded historical demo run.

## 1. Purpose

This runbook defines the public-safe marketing demo for CDT-AutoCAD. The demo is intentionally synthetic: it uses only repo-owned generated geometry and does **not** use BeachSquare, customer drawings, received DWGs, concept PDFs, XREFs or other project-specific visual assets.

The core message is:

> **AI CAD that can prove what it changed — and recover exactly when a feature fails.**

The demo must show the real public MCP contract and the live Managed .NET integrity route. It must not imply domain knowledge or capabilities that the provider does not claim.

## 2. Camera-ready runner

Tracked runner:

```text
scripts/run_marketing_integrity_demo.py
```

Windows `.171` has one on-demand InteractiveToken Scheduled Task:

```text
CDT-AutoCAD-Marketing-Demo
```

It launches `pythonw.exe` directly in Windows Session 1. No PowerShell console is used during the recording path.

The runner:

1. restores/maximizes AutoCAD;
2. closes only stale `artifacts/marketing/CDT-AutoCAD-Marketing-*.dwg` documents created by this runner;
3. creates a disposable AutoCAD 2027 drawing from the explicit `acad.dwt` template;
4. bootstraps provider-owned document/entity PIDs using the existing trusted synthetic N4 fixture;
5. restores `FILEDIA` and `CMDECHO` immediately after bootstrap;
6. creates a dedicated `CDT-MARKETING` layer through the public MCP surface;
7. executes three public `feature_execute` calls;
8. saves and content-address seals the accepted DWG;
9. captures a final AutoCAD PNG;
10. leaves the successful demo drawing open for recording.

Ephemeral outputs are under the already-ignored directory:

```text
artifacts/marketing/
  latest-result.json
  latest-events.jsonl
  latest-frame.png
  CDT-AutoCAD-Marketing-<run-id>.dwg
  sealed/
```

## 3. Synthetic live sequence

### Feature 1 — verified build

`marketing.integrity.lattice`

- 40 typed LINE entities;
- spans exactly two native chunks at the 32-item chunk bound;
- expected result: `COMMITTED`;
- live receipt must report `recommended_next_delay_ms=300`.

### Feature 2 — intentional mid-feature failure

`marketing.integrity.injected-failure`

- first native chunk: 32 unique typed CIRCLE entities;
- second native chunk begins with one exact duplicate of the first circle;
- validation rejects the second chunk;
- expected result: `ROLLED_BACK_VERIFIED`;
- expected `failed_native_chunk_index=1`;
- final fingerprint must equal Feature 1's accepted fingerprint exactly.

This is the primary marketing shot: circles appear, the feature fails, those circles disappear, and the previously committed lattice remains unchanged.

### Feature 3 — continue after recovery

`marketing.integrity.recovery-nodes`

- 16 typed CIRCLE entities;
- expected result: `COMMITTED`;
- proves that later work can continue from the preserved predecessor after verified rollback.

The product recommendation remains **300 ms between completed features**. The marketing runner uses a separate **900 ms recording pause** so a human viewer can see state transitions. Never describe 900 ms as an engine requirement.

## 4. Latest live smoke evidence

The production runner now requires current bridge identity `0.8.2-mp7`. The table below intentionally remains the historical marketing smoke `20260911-220855-712350`; its `0.8.1-g3` row is evidence for that run and must not be rewritten retroactively.

Live Session-1 run `20260911-220855-712350` passed on AutoCAD 2027:

| Check | Result |
| --- | --- |
| Provider | `0.4.0rc1` |
| Contract | `autocad-generic-v1-rc1` |
| Public tools | 86 |
| Execution model | `feature-based-chunks-streaming-v1` |
| Native bridge | `0.8.1-g3` |
| Baseline semantic entities | 12 |
| Feature 1 | **COMMITTED** — 40 PIDs / 2 native chunks |
| Feature 2 | **ROLLED_BACK_VERIFIED** — failure in native chunk index 1 |
| Prior feature preserved | **true** — exact same Feature-1 fingerprint |
| Feature 3 | **COMMITTED** — 16 PIDs / 1 native chunk |
| Final semantic entities | 68 |
| Recording pause | 900 ms |
| Product recommendation | 300 ms |
| Scheduled Task result | 0 |
| Final screenshot | 1936×1048 PNG |
| Sealed DWG SHA-256 | `dd0149fda83b8baa78fa6937a6272f30e256ce243149c831c702e3a95e0293f3` |

The run began with one stale synthetic marketing drawing, closed it automatically, and finished with no pre-existing user drawing in the AutoCAD document set.

## 5. Approved marketing claims

Use claims narrowly and literally supported by evidence:

- **86 public MCP tools** in the current RC contract.
- **AutoCAD 2027 / Windows x64** is the primary certified live lane.
- Feature-based Chunks Streaming uses bounded native micro-chunks of at most **32 items**.
- A failed current feature can be restored to its exact predecessor while earlier committed features remain intact.
- Native execution uses persistent document/entity PIDs and versioned semantic fingerprints.
- Scale graduation has live evidence at **100 / 1,000 / 5,000 / 10,000 entities**.
- The accepted 10,000-entity run used **313 native chunks**, beginning/middle/end fault injection, exact predecessor recovery and zero pending recovery.
- Content-addressed accepted-artifact sealing records SHA-256 provenance.
- The public surface includes typed 2D drafting, dimensions, measurement/analysis, XREF/layer/document operations and verified ACIS 3D operations within their documented scope.

Recommended headline:

> **CDT-AutoCAD — a recoverable CAD execution engine for AI agents.**

Recommended proof line:

> **Typed actions. Persistent identity. Verified state. Exact feature-local recovery.**

## 6. Claims to avoid

Do **not** say:

- “production GA” — current product version is `0.4.0rc1`; say **RC/preview**;
- “supports every AutoCAD version” — only the documented target is certified;
- “understands civil/mechanical/architectural standards” — domain rules belong to Domain Agents;
- “10,000 entities instantly” or “no freeze at 10k” — the scale evidence proves bounded execution/recovery, not instantaneous completion;
- “all 86 tools use G3 native rollback” — selected strong-integrity tools use the native route; the broader surface still includes COM;
- “3D topology verified” — deterministic face/edge topology is not claimed;
- “STEP/STL export” — verified 3D export is SAT;
- “edge fillet/chamfer/shell supported” — those operations are explicitly unsupported on the current typed ActiveX lane;
- “zero possibility of data loss” — use the specific tested recovery and fail-closed claims instead.

## 7. Suggested 75–90 second video

| Time | Visual | Narration / overlay |
| --- | --- | --- |
| 0–8s | Empty synthetic scene / AutoCAD | “AI drawing is easy. Trusting what it changed is harder.” |
| 8–22s | Feature 1 lattice forms | `40 entities · 2 bounded native chunks · COMMITTED` |
| 22–40s | Feature 2 circles appear, then rollback | `Injected feature failure → ROLLED_BACK_VERIFIED` |
| 40–50s | Lattice remains exactly | `Previous feature fingerprint preserved` |
| 50–60s | Feature 3 nodes commit | `Continue from verified predecessor` |
| 60–72s | Receipts/evidence montage | `PID · fingerprint v3 · artifact SHA-256` |
| 72–82s | Scale/evidence card | `100 → 1k → 5k → 10k · exact fault recovery` |
| 82–90s | Clean final frame + logo/title | `CDT-AutoCAD — Generic CAD Execution Engine for AI agents` |

For a separate 3D montage, use a synthetic model and label it as the broad AutoCAD/ACIS capability lane. Do not visually imply that current ACIS operations are executed inside the same G3 feature rollback shown in the integrity demo.

## 8. Recording checklist

Before recording:

1. AutoCAD 2027 is running in Interactive Session 1.
2. `CDT.AutoCAD.Bridge.dll` is loaded.
3. `CDT-AutoCAD-Marketing-Demo` is the only persistent `CDT-AutoCAD-*` Scheduled Task needed for the recording path.
4. Browser/customer/project windows are not included in the capture crop.
5. Record the AutoCAD monitor/canvas, not the entire multi-monitor desktop.
6. Start the on-demand task once and let it complete; do not click inside AutoCAD while a feature is executing.
7. Confirm `latest-result.json` says `PASS` before using the take.
8. Keep `latest-events.jsonl` as timing evidence for editing overlays.
9. Use `latest-frame.png` only as a review/reference still; capture the actual video at native display resolution.

If AutoCAD is not quiescent, do not retry mutations blindly. Resolve the active command/modal first. The marketing runner uses an explicit AutoCAD 2027 template and restores temporary `FILEDIA`/`CMDECHO` changes so repeated takes do not leave the NEW command waiting for input.
