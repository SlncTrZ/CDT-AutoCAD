# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-10 13:25 +07:00
> Status: **N0–N4 CLOSED · N5 NEXT · native mutation disabled**
> Implementation checkpoint: N4 code/evidence in the N4 closure commit; previous N3 checkpoint `2245ebd`
> Primary native target: AutoCAD 2027 full · Windows x64 · COM `26.0` · Managed .NET `net10.0-windows`

## 1. Public runtime

The published/runtime baseline remains unchanged during the architecture migration:

- provider version: `0.3.0rc1`;
- contract: `autocad-a2-v1-rc1`;
- public MCP surface: **50 tools**;
- default backend: `ezdxf`;
- live Windows backend: ActiveX/COM;
- A2: live-verified PASS on AutoCAD 2027, still release-candidate pending explicit promotion;
- A3.1 / A3.2 / A3.3: native live-verified PASS but staged, capability-false/non-public until explicit contract promotion.

The N-series Managed .NET bridge is **not yet the public provider backend**.

## 2. Architecture checkpoint

| Phase | State | What is proven |
| --- | --- | --- |
| N0 | **CLOSED** | architecture/documentation freeze and migration boundary |
| N1 | **CLOSED / PASS** | Python semantic models, canonicalization/fingerprint primitives, rollback receipts and state-chain primitives |
| N2 | **CLOSED / LIVE PASS** | persistent document/entity PID carrier and clone/reconciliation policy on real AutoCAD 2027, P0–P10 |
| N3 | **CLOSED / LIVE PASS** | staged in-process C# Managed .NET bridge, bounded local typed Named Pipe IPC, runtime-document binding and read-only document identity |
| N4 | **CLOSED / LIVE PASS** | authoritative native read-only `SemanticSnapshot` extraction, persistent entity PID binding and native↔N1 document fingerprint parity |
| N5 | **NEXT / NOT STARTED** | typed native mutation with `expected_parent_fp`, transaction boundary and verified rollback |
| N6+ | **NOT STARTED** | deterministic semantic delta validation, state-chain integration, post-commit integrity and migration/promotion |

## 3. N4 runtime truth

The staged bridge remains read-only, now with authoritative N4 semantic extraction under `native/CDT.AutoCAD.Bridge/`.

Enabled internal operations:

```text
bridge.health
bridge.documents.list
bridge.document.identity
bridge.document.snapshot
```

Measured invariants:

- protocol: `cdt-autocad-native-v1`;
- maximum frame payload: 65,536 bytes;
- transport: Windows Named Pipe;
- pipe boundary: current user + local computer + same Windows session;
- same-user SSH Session 0 is rejected with `CLIENT_SESSION_MISMATCH`;
- pipe I/O thread does not call AutoCAD API; native reads are dispatched on `Application.Idle`;
- `document_pid` is semantic-lineage identity, not unique physical-file identity;
- two open raw-copy DWGs sharing one `document_pid` receive distinct `runtime_document_id` values;
- PID-only target selection is rejected;
- read-only N3 acceptance leaves tested drawings at `DBMOD=0`;
- `bridge.document.snapshot` reads the active bound AutoCAD Database through native `ForRead` transactions;
- N4 scope is intentionally bounded to the active document's current space plus referenced block definitions/content, with `MaxSnapshotEntities=32`; it is not yet a whole-DWG/all-layout semantic extractor;
- extracted entities require persistent N2 semantic PIDs and duplicate/missing PIDs fail closed;
- supported N4 semantic families live-proven: LINE, CIRCLE, ARC, LWPOLYLINE, TEXT, MTEXT, INSERT, BLOCK_DEFINITION, DIMENSION and HATCH;
- referenced block definitions and their PID-bearing contents are included recursively in the semantic fingerprint; a saved block-definition geometry mutation changes `document_fp` even with `DBMOD=0` on both compared states;
- layer, linetype, text-style and dimension-style resources are extracted deterministically;
- native document fingerprint matches the N1 canonical fingerprint byte-for-byte for the accepted fixture;
- repeated reads keep `DBMOD=0`, and save/reopen preserves the semantic document fingerprint;
- a snapshot whose serialized result exceeds the 65,536-byte frame fails as correlated `RESPONSE_TOO_LARGE`; the bridge remains alive and the read attempt does not change `DBMOD`;
- `mutation_enabled=false`.

Acceptance deployment on Windows `.171` uses the per-user bundle:

```text
%APPDATA%\Autodesk\ApplicationPlugins\CDT.AutoCAD.Bridge.bundle
```

with only its `Contents\Windows` directory explicitly added to `TRUSTEDPATHS`; `SECURELOAD=1` remains enabled.

Canonical N3 evidence: `docs/evidence/n3-native-bridge-readonly-2026-09-10.json`.

Canonical N4 evidence: `docs/evidence/n4-native-semantic-2026-09-10.json`.

## 4. Identity / rollback invariants

Two pillars remain non-negotiable:

1. **Data Integrity / Rollback** — future native mutation may advance only from a known parent state and must end as `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`; uncertain state blocks later mutation.
2. **Precise Identity / PID + Fingerprinting** — native `ObjectId`/Handle is insufficient semantic identity; persistent PID, runtime-document binding and deterministic fingerprints are required.

N2 has proven PID carrier/storage and clone semantics. N3 has proven runtime-document disambiguation over IPC. N4 now provides authoritative native parent-state snapshots and deterministic document fingerprints. **`expected_parent_fp` is still not enforced on mutation because no native mutation endpoint exists until N5.**

## 5. Semantic State Loop implementation status

The target loop remains mandatory:

```text
ActionSpec
 -> native execution
 -> SemanticSnapshot extraction
 -> canonicalize / fingerprint / semantic diff
 -> deterministic validation
 -> commit or verified rollback
 -> independent read-back
 -> state-chain log
```

Current implementation coverage is partial:

- N1: semantic/fingerprint/state-chain primitives exist in Python;
- N2: persistent PID policy exists and is live-verified;
- N3: native read-only transport/document binding exists and is live-verified;
- N4: native read-only semantic extraction + parent document fingerprinting is implemented and live-verified;
- N5+: native mutation/rollback/delta/state-loop integration is **not implemented yet**.

Therefore a current drawing workflow may now use authoritative native N4 semantic snapshots for the supported scope, but it must **not claim full native Semantic State Loop completion** until N5/N6 mutation, rollback and deterministic delta/state-chain gates are accepted.

## 6. Verification checkpoint

Current regression/evidence on the N4 closure tree:

- focused N4 snapshot/protocol/client + N1 semantic regression: **58 passed**;
- Linux full Python suite: **145 passed / 4 skipped**;
- Windows `.171` full Python suite: **144 passed / 5 skipped**;
- C# bridge build: **0 errors**;
- documented unsuppressed build-warning families: `Microsoft.VisualBasic`, `System.Drawing`, `WindowsBase` (`MSB3277`);
- native N2 P0–P10: PASS;
- native N3 read-only acceptance: PASS;
- native N4 semantic acceptance: **PASS on real AutoCAD 2027 Session 1**;
- `git diff --check`: PASS.

Final N4 bridge DLL recorded by canonical evidence:

```text
SHA-256 e3402c18370c59e78a5a6586de90797482b64b26e1ba449c939eff869e2da01b
```

## 7. Next development gate

**N5 is the only current N-series implementation frontier.**

N5 may now introduce a bounded typed native mutation surface, but every mutation must:

- bind `runtime_document_id` + persistent `document_pid` + `expected_parent_fp`;
- re-read authoritative N4 parent state before mutation and fail with state drift on mismatch;
- execute inside one native transaction boundary;
- use persistent entity PID targeting rather than Handle/ObjectId as semantic identity;
- reconcile clone-created PIDs before acceptance;
- abort on deterministic validation failure;
- independently re-read after commit/abort and prove either `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`.

N5 must not add arbitrary AutoCAD command, AutoLISP, C#, macro or shell execution. N6 semantic delta/state-chain work starts only after N5 native transaction and rollback gates live-pass.

## 8. Status authority

Use this file for the current implementation checkpoint. Historical documents and evidence may contain older test counts or statements that were true at their original checkpoint; those records should be read as historical evidence, not as the current frontier.
