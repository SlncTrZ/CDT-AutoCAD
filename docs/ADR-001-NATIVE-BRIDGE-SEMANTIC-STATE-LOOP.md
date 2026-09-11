# ADR-001 — Native .NET Bridge + Semantic State Loop

> Date: 2026-09-10
> Status: ACCEPTED ARCHITECTURE · N0–N7 + O1 CLOSED/LIVE-VERIFIED
> Scope: CDT-AutoCAD target architecture; current COM/ezdxf runtime remains supported during migration

## 1. Decision

CDT-AutoCAD will evolve from a primarily Python + ActiveX/COM live-automation architecture into a hybrid architecture with:

- **Python MCP provider** for transport, policy, orchestration, expected-state planning, canonicalization, deterministic validation, fingerprinting, audit logs and cross-provider semantics;
- **C# AutoCAD Managed .NET bridge loaded in-process in AutoCAD** for native document/database access, transactional mutations, semantic extraction, event-assisted delta capture and native read-back;
- **local authenticated/ACL-constrained IPC**, with Windows Named Pipes as the default design candidate, between Python and the in-process bridge;
- **Semantic State Loop** as the mandatory correctness mechanism for stepwise engineering work;
- **ActiveX/COM retained as migration/bootstrap/compatibility/fallback infrastructure**, not the long-term authoritative semantic execution path;
- **AutoLISP retained as an optional compatibility/user-extension mechanism**, but not as the core execution architecture and never exposed as unrestricted arbitrary-code execution through MCP;
- **Vision/screenshot evidence moved out of the per-step correctness loop**. Raster vision is allowed for source ingestion and presentation review; geometric correctness must be validated from software-native semantic data.

## 2. Why this decision

The provider now needs more than remote command execution. It must be able to prove, step by step, what changed in a CAD document and why the resulting state is acceptable.

The required capabilities are:

- native transactions with commit/abort semantics;
- stable object identity plus content identity;
- precise geometry/style/topology extraction;
- created/modified/deleted delta capture;
- deterministic validation with tolerances;
- state-chain fingerprinting to detect drift/manual edits;
- independent post-commit read-back;
- forensic logs that identify the exact step where state diverged;
- reusable semantics that can later map to Blender, SketchUp, SolidWorks and other providers.

The AutoCAD Managed .NET API is the preferred native adapter because it runs in-process and directly exposes AutoCAD `Document`, `Database`, `ObjectId`, `TransactionManager`, entity classes and database events. AutoCAD 2027 supports Managed .NET with .NET 10.0.

Official Autodesk references:

- Managed .NET Developer Guide: `https://help.autodesk.com/view/OARX/2027/ENU/?guid=GUID-C3F3C736-40CF-44A0-9210-55F6A939B6F2`
- Supported programming interfaces: `https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-Customization/files/GUID-E6429154-36DF-4D84-8ABC-9FCA15B66158.htm`
- Managed .NET compatibility / AutoCAD 2027 + .NET 10.0: `https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-Customization/files/GUID-A6C680F2-DE2E-418A-A182-E4884073338A.htm`
- Transactions: `https://help.autodesk.com/cloudhelp/2027/FRA/OARX-DevGuide-Managed/files/GUID-50FD6118-B2D1-4313-A7D6-830794DFDEFA.htm`
- Object/database events: `https://help.autodesk.com/cloudhelp/2027/PLK/OARX-DevGuide-Managed/files/GUID-E30279D1-E4B5-48A4-A3D8-9CEC83BD0967.htm`

## 3. Architectural boundary

The .NET bridge is **not** the MCP server.

```text
MCP Client / SlncTrZ Gateway
          |
          v
Python CDT-AutoCAD Provider
  - auth/policy
  - typed contracts
  - expected state
  - fingerprint/diff
  - validation/audit
          |
          | local typed IPC
          v
C# AutoCAD Native Bridge
  - loaded in acad.exe
  - document dispatch/lock
  - transactions
  - native mutation
  - semantic extraction
  - post-commit read-back
          |
          v
AutoCAD Database / DWG
```

No public endpoint may accept free-form C#, AutoLISP, shell, macro, `SendCommand` text or arbitrary code for execution.

## 4. Semantic State Loop invariant

Every engineering mutation must follow this logical loop:

```text
Source / Design Intent
        |
        v
Expected Semantic State + Validation Rules
        |
        v
Verify expected_parent_state fingerprint
        |
        v
Native Execution Transaction
        |
        v
Provisional Semantic Extraction
        |
        v
Deterministic Validation
    PASS | FAIL
         |   +--> Abort / Rollback + failure record
         v
       Commit
         |
         v
Independent Post-Commit Read-Back
         |
         v
Canonicalize + Fingerprint + State Diff
         |
         v
Persist Verified State Chain
         |
         v
Next Step
```

The next step must not execute unless the current document state matches the expected parent fingerprint.

## 5. Two-phase validation

### Phase A — in-transaction deterministic validation

The Python provider sends an `ActionSpec` together with deterministic validation rules. The .NET bridge:

1. locks/selects the target document as required;
2. starts a native transaction;
3. applies only the typed mutation;
4. extracts the provisional semantic state of the affected scope;
5. evaluates deterministic rules that can be executed without AI judgment;
6. commits only if the rules pass; otherwise aborts.

Do not hold an AutoCAD write transaction open while waiting for an AI/network round-trip.

### Phase B — post-commit independent read-back

After commit, a new read transaction independently extracts the persisted state. The provider then canonicalizes and fingerprints the result. A mismatch between provisional/expected state and persisted state is `COMMIT_INTEGRITY_FAIL` and blocks the next step.

## 6. Fingerprint model

The target protocol must distinguish at least:

- `geometry_fp` — normalized entity geometry;
- `style_fp` — layer, linetype, lineweight and other relevant presentation semantics;
- `topology_fp` — relationships such as adjacency/intersection/containment/connectivity;
- `instance_fp` — concrete object identity + content fingerprint;
- `scope_fp` — deterministic digest for the affected semantic scope;
- `document_fp` — whole-document semantic digest when practical/required;
- `step_fp` — pre-state + action + delta + post-state chain digest.

Raw floating-point values must not be hashed directly. Geometry is canonicalized and quantized using declared tolerances before hashing.

## 7. Identity and drift detection

Native AutoCAD handles/ObjectIds are runtime identity, not sufficient content identity. Two duplicate entities can have different handles but identical geometry fingerprints.

Each step declares `expected_parent_fp`. If the current semantic fingerprint differs — for example because of a manual edit, another automation client or unexpected background mutation — the step fails closed with `STATE_DRIFT`.

## 8. COM policy after migration begins

ActiveX/COM remains valuable for:

- bootstrapping/discovery of an AutoCAD session during migration;
- compatibility with current public A2/A3 behavior;
- fallback operations not yet implemented by the native bridge;
- native acceptance comparison between old and new adapters.

However, new semantic correctness features should target the .NET bridge first. COM timeout uncertainty is one reason it should not remain the sole long-term mutation authority.

## 9. AutoLISP policy

AutoLISP remains a supported AutoCAD programming technology, but it is not the core CDT execution backend.

Allowed future roles:

- importing/maintaining user-owned legacy routines;
- explicit compatibility workflows;
- controlled export of `.lsp` artifacts when requested;
- debugging/manual operator tools.

Not allowed:

- unrestricted MCP `eval_lisp`;
- free-form code supplied by an untrusted caller;
- treating LISP return values as semantic proof of document correctness.

If a future constrained LISP capability is added, it requires an explicit contract, policy boundary, trusted location/security handling and independent semantic read-back.

## 10. Vision policy

Vision is not the geometry oracle.

Use it for:

- converting raster/reference imagery into a `SourceSemanticModel` when no vector/structured source exists;
- presentation/aesthetic review;
- final human evidence.

Do not use it as the primary per-step validation mechanism when native structured state is available.

## 11. Cross-provider direction

The reusable CDT architecture is the semantic protocol, not one implementation language:

```text
CDT-AutoCAD    -> C#/.NET native adapter
CDT-Blender    -> Python bpy native adapter
CDT-SketchUp   -> native Ruby/C adapter
CDT-SolidWorks -> C#/.NET/native automation adapter
                       |
                       v
          Common Semantic State Protocol
          Common Fingerprint / Diff Model
          Common Deterministic Validation Model
          Common Audit Chain
```

Provider-specific geometry remains provider-owned; common semantic concepts should be proposed back to `CDT_Engineer` after Rule-of-Two evidence.

## 12. Compatibility rule

This ADR defines the **target architecture** and records a decision that predates implementation. Current status authority is `docs/CURRENT_CHECKPOINT.md`. As of the 2026-09-11 checkpoint, N0–N7 and O1 are closed/live-verified for their bounded scopes; the staged bridge has typed LINE/CIRCLE/ARC/simple-LWPOLYLINE mutation plus checkpoint-backed R0/R1/R2 recovery, including activation-safe R2 after real AutoCAD restart. The public MCP provider remains the unchanged 50-tool COM/ezdxf release-candidate baseline. Python MCP hot reload is now the mandatory prerequisite before deep N8/N9/N10 migration.

The existing `ezdxf` + COM provider remains the public/runtime migration baseline until later native semantic, rollback, parity and promotion gates are implemented, tested on real AutoCAD and explicitly promoted. See `docs/CURRENT_CHECKPOINT.md` for the authoritative current frontier.
