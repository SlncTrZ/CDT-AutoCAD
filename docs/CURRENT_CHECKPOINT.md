# Current Checkpoint — CDT-AutoCAD

> Updated: 2026-09-10 11:56 +07:00
> Status: **N0–N3 CLOSED · N4 NEXT · native mutation disabled**
> Implementation checkpoint commit: `2245ebd` (N3 code/evidence); this document may be updated by later docs-only commits without changing that implementation checkpoint
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
| N4 | **NEXT / NOT STARTED** | authoritative native read-only `SemanticSnapshot` extraction |
| N5+ | **NOT STARTED** | native typed mutation, transactional rollback integration, deterministic delta validation, post-commit integrity and migration/promotion |

## 3. N3 runtime truth

N3 is implemented under `native/CDT.AutoCAD.Bridge/` and is intentionally read-only.

Enabled internal operations:

```text
bridge.health
bridge.documents.list
bridge.document.identity
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
- `mutation_enabled=false`.

Acceptance deployment on Windows `.171` uses the per-user bundle:

```text
%APPDATA%\Autodesk\ApplicationPlugins\CDT.AutoCAD.Bridge.bundle
```

with only its `Contents\Windows` directory explicitly added to `TRUSTEDPATHS`; `SECURELOAD=1` remains enabled.

Canonical N3 evidence: `docs/evidence/n3-native-bridge-readonly-2026-09-10.json`.

## 4. Identity / rollback invariants

Two pillars remain non-negotiable:

1. **Data Integrity / Rollback** — future native mutation may advance only from a known parent state and must end as `COMMITTED_VERIFIED` or `ROLLED_BACK_VERIFIED`; uncertain state blocks later mutation.
2. **Precise Identity / PID + Fingerprinting** — native `ObjectId`/Handle is insufficient semantic identity; persistent PID, runtime-document binding and deterministic fingerprints are required.

N2 has proven PID carrier/storage and clone semantics. N3 has proven runtime-document disambiguation over IPC. **`expected_parent_fp` is not yet enforceable through the native bridge** because authoritative native `SemanticSnapshot` extraction is the N4 deliverable. Documentation must not claim otherwise.

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
- N4: full native semantic extraction is **not implemented yet**;
- N5+: native mutation/rollback/state-loop integration is **not implemented yet**.

Therefore a current drawing workflow may use structured COM/ezdxf/native identity evidence, but it must **not claim full native Semantic State Loop completion** until N4+ gates are implemented and accepted.

## 6. Verification checkpoint

Current regression/evidence on the N3 closure tree:

- focused N3 Python protocol/client/transport: **27 passed**;
- Linux full Python suite: **141 passed / 4 skipped**;
- Windows `.171` full Python suite: **140 passed / 5 skipped**;
- C# bridge build: **0 errors**;
- documented unsuppressed build-warning families: `Microsoft.VisualBasic`, `System.Drawing`, `WindowsBase` (`MSB3277`);
- native N2 P0–P10: PASS;
- native N3 read-only acceptance: PASS;
- `compileall`: PASS;
- `git diff --check`: PASS;
- Ruff: **UNAVAILABLE in the prepared project environments**, therefore not counted as PASS.

Final N3 bridge DLL recorded by canonical evidence:

```text
SHA-256 55954aabbdaae897935ea97e1a44f3de0e207b9555d8e94a8bd663d3cfb7e70a
```

## 7. Next development gate

**N4 is the only current N-series implementation frontier.**

N4 must remain read-only and implement authoritative native extraction for at least:

- document identity / units / current space / extents;
- layers, linetypes and styles;
- core 2D entities;
- blocks/references;
- dimensions and hatches;
- native metrics / WCS bounds;
- persistent PIDs;
- deterministic snapshot serialization suitable for N1 canonical fingerprinting.

Acceptance must prove repeated-read stability, no drawing dirtying, save/reopen semantic stability where semantics are unchanged, and COM-vs-.NET parity for supported measurements.

**Do not open a native mutation endpoint in N4.** N5 may start only after N4 provides authoritative parent-state data sufficient to support `expected_parent_fp`, rollback and post-commit integrity gates.

## 8. Status authority

Use this file for the current implementation checkpoint. Historical documents and evidence may contain older test counts or statements that were true at their original checkpoint; those records should be read as historical evidence, not as the current frontier.
