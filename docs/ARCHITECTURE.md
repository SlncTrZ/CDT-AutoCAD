# Architecture — CDT-AutoCAD

> Canonical public architecture authority for the CDT-AutoCAD provider.
> Updated: 2026-09-12

## 1. Mission

CDT-AutoCAD is a **Generic CAD Execution Engine** for AutoCAD automation.

Its job is to execute generic CAD actions against real drawing state and return enough structured evidence to prove what happened. It is infrastructure for higher-level systems such as CDT-Engineer; it is not itself an engineering-design domain.

The provider is considered launch-ready for this mission. Future capability work is demand-driven: a new AutoCAD capability is added only when CDT-Engineer or another production domain can name the blocked workflow, required postcondition and verification invariant.

## 2. Responsibility boundary

```text
CDT-Engineer / Domain Agent
  engineering intent
  standards and rules
  calculations and checks
  design/review decisions
            |
            | generic CAD ActionSpecs / feature plans
            v
CDT-AutoCAD
  typed CAD execution
  document/entity identity
  semantic read-back
  deterministic verification
  rollback/recovery
  artifact/runtime provenance
            |
            v
AutoCAD execution backends
  Managed .NET Bridge
  COM / ActiveX
  ezdxf headless
            |
            v
DWG / DXF / AutoCAD Database / ACIS
```

CDT-AutoCAD must not absorb civil, structural, mechanical, architectural or other engineering-domain semantics merely because those domains use AutoCAD.

## 3. Runtime architecture

The production architecture contains three intentional execution lanes.

### 3.1 Managed .NET strong-integrity lane

```text
Python MCP Provider / Semantic Core
        |
        | typed local IPC
        v
C# AutoCAD Managed .NET Bridge
        |
        | short bounded native transactions
        v
AutoCAD Database
```

This lane owns the strongest execution guarantees where implemented: provider-owned persistent PID, versioned fingerprints, expected-parent drift protection, bounded transactions, independent semantic read-back and verified recovery.

### 3.2 COM / ActiveX compatibility lane

COM provides broad access to live AutoCAD functionality that has not migrated to the native strong-integrity lane. Operations on this lane must use bounded/fail-closed behavior and truthful postcondition checks where available. Catalog presence does not imply native-level guarantees.

### 3.3 ezdxf headless lane

The headless lane supports cross-platform DXF workflows that do not require a live AutoCAD process. Its runtime and guarantee model is distinct from live AutoCAD and must not be presented as native AutoCAD verification.

There is deliberately no architectural objective to move every operation to Managed .NET. A route may remain on COM when its assurance is sufficient for the production workflow.

## 4. Non-negotiable invariants

### 4.1 Data Integrity / Rollback

A strong-integrity mutation must end in one of two proven states:

- `COMMITTED_VERIFIED`; or
- `ROLLED_BACK_VERIFIED`.

Uncertain completion, failed rollback or unverifiable state blocks dependent mutation. No timeout, exception or optimistic receipt may be converted into a success claim without authoritative read-back.

### 4.2 Precise Identity / PID + Fingerprinting

AutoCAD ObjectId and Handle are not sufficient semantic identity on their own. Strong-integrity state uses provider-owned persistent document/entity identity plus versioned semantic fingerprints and `expected_parent_fp` drift protection.

Read paths do not silently adopt unmanaged entities into provider identity scope.

## 5. Semantic State Loop

New strong-integrity automation follows this model:

```text
ActionSpec
  -> bind runtime/document/predecessor state
  -> native execution
  -> semantic extraction
  -> canonicalization + fingerprint/diff
  -> deterministic validation
  -> commit or verified rollback
  -> independent persisted read-back
  -> state/evidence receipt
```

Screenshots are supplemental visual evidence. Structured native state is the geometry/state oracle wherever a supported semantic extractor exists.

The normative state/recovery rules live in [`SEMANTIC_STATE_PROTOCOL.md`](SEMANTIC_STATE_PROTOCOL.md).

## 6. Production execution model

The approved orchestration model is **Feature-based Chunks Streaming**.

A higher-level agent submits one meaningful feature at a time. CDT-AutoCAD may split that feature into bounded native micro-chunks, but feature-level failure restores only that feature's predecessor while earlier accepted features remain committed.

The provider treats `feature_id` and correlation metadata as orchestration identity, not as engineering meaning. It must not infer that a feature named `beam`, `pipe`, `road` or similar carries domain semantics.

## 7. Capability and guarantee model

Every public capability belongs conceptually to one of these assurance classes:

- **native strong-integrity** — PID/fingerprint/read-back/recovery guarantees are implemented for the documented scope;
- **COM bounded-integrity** — live AutoCAD compatibility route with explicit bounded/postcondition behavior appropriate to that operation;
- **headless** — ezdxf/DXF route with its own contract;
- **explicit refusal** — unsupported or unverifiable operation fails rather than broadening claims.

The current runtime status of a capability is reported by provider status/capability tools and the public checkpoint. This architecture document defines the model, not the live availability of every route.

## 8. Security boundary

The provider does not expose arbitrary shell, AutoLISP, macro, caller-supplied C# or free-text AutoCAD command execution surfaces. Filesystem access and runtime side effects remain bounded by configured policy and typed tool contracts.

Threats and mitigations are maintained in [`THREAT_MODEL.md`](THREAT_MODEL.md).

## 9. Extension rule after launch

CDT-AutoCAD is now infrastructure, not an independent feature-parity roadmap.

A new capability is justified only when at least one of these is true:

1. a regression or integrity defect threatens an existing guarantee;
2. CDT-Engineer identifies a concrete production workflow blocked by the missing capability;
3. that workflow cannot be independently verified with the current read-back surface;
4. a measured production workload exposes a real performance or operability bottleneck.

“AutoCAD supports it” is not sufficient justification by itself.

When a new capability is accepted, its verification invariant must be designed with it. Native migration, topology extraction, ACIS parity and similar breadth are therefore optional until demanded by a real workflow.

## 10. Documentation authority

This file is the only provider-local **public architecture source of truth**.

Related documents have narrower roles:

- [`CURRENT_CHECKPOINT.md`](CURRENT_CHECKPOINT.md) — current public release/runtime status, not architecture design;
- [`SEMANTIC_STATE_PROTOCOL.md`](SEMANTIC_STATE_PROTOCOL.md) — normative semantic-state/recovery protocol;
- [`LIVE_ACCEPTANCE.md`](LIVE_ACCEPTANCE.md) — accepted live evidence and scope;
- [`OPERATIONS_RUNBOOK.md`](OPERATIONS_RUNBOOK.md) — operational procedure;
- [`SPEC_BASELINE.md`](SPEC_BASELINE.md) and `../specs/**` — pinned upstream control-plane inputs, not the current provider architecture;
- `_private/DEVELOP_PLAN.md` — maintainer roadmap, intentionally unpublished and not architecture authority.

Historical evidence or roadmap text must never override this architecture definition. If architecture changes, update this file explicitly and then reconcile dependent docs.
