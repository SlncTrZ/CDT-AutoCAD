# Semantic State Protocol — CDT-AutoCAD

> Updated: 2026-09-12 17:30 +07:00
> Status: CONTRACT BASELINE · N1–N7/O1 + G1/G2/G3 implemented/live-accepted for documented scopes · Feature-based Chunks Streaming live-accepted
> Scope: source semantics, native extraction, PID, canonicalization, fingerprinting, diff, metadata, feature-local rollback and deterministic validation

## 1. System invariant

The authoritative state of a CAD step is structured data extracted from the software-native object/database API.

> **Action is not proof. A successful API return is not proof. A screenshot is not proof. The post-action semantic state is proof.**

Two pillars are non-negotiable:

1. **Data Integrity / Rollback** — every mutation must either produce a verified committed state or restore a verified predecessor state. No ambiguous half-success state is allowed to advance the workflow.
2. **Precise Identity / PID + Fingerprinting** — every document, semantic object, step and state must be identifiable independently of transient runtime references, and content identity must be verifiable deterministically.

The Semantic State Loop is the control architecture. AutoCAD .NET, COM, ezdxf and future adapters are implementation mechanisms beneath it.

### 1.1 Current implementation boundary

As of the current checkpoint:

- N1–N7 and O1 are closed/live-accepted for their documented semantic/PID/typed-mutation/recovery scopes;
- G1 provides bounded generic batch create/transform/block insertion with native chunks capped at 32 and Idle-yield scheduling;
- G2 provides schema-agnostic namespaced JSON metadata over ExtensionDictionary/XRecord with bounded storage/query work and metadata participation in document fingerprint schema v3;
- G3 provides one immutable predecessor checkpoint across yielded native chunks, independent final compact-state verification and exact R2 predecessor restoration for logical failure or unknown completion;
- scale graduation is live-accepted at 100, 1,000, 5,000 and 10,000 entities, with beginning/middle/end failure injection and zero pending recovery;
- Feature-based Chunks Streaming is the Production Domain orchestration model: one caller-defined feature owns one logical predecessor; failure restores only the current feature while prior committed features remain accepted;
- the public identity remains `0.4.0rc1 / autocad-generic-v1-rc1 / 86 tools`; current runtime/code checkpoint is `911ba09` with bridge candidate `0.8.2-mp7`; selected strong-integrity tools and `feature_execute` are public while the broader COM lane remains an intentional bounded-integrity compatibility route rather than an unfinished requirement to migrate everything native;
- MP-G05 is closed for one deliberately bounded native `3DSOLID` mutation family: planar translation. Provider PID carriage is reused; `solid-semantic-v2` fingerprints combine centroid, volume, translation-faithful `Solid3d.GeometricExtents` and inertia terms; `expected_parent_fp` rejects stale state; successful commits require independent persisted read-back; and the existing immutable checkpoint chain provides verified R0 abort plus exact R2 predecessor restore after post-commit integrity failure. `topology_verified=false` remains explicit because this semantic signature is not a B-rep topology oracle, and no Boolean/rotate/scale parity is implied.

The protocol below remains both implemented contract and normative guardrail. Current public status authority is `docs/CURRENT_CHECKPOINT.md`; the internal distilled state is `_private/AUDIT.md`, while raw historical machine evidence is retained only under ignored `artifacts/internal-evidence/`.

## 2. Core protocol objects

The target protocol defines:

- `SourceSemanticModel`
- `ActionSpec`
- `ValidationRuleSet`
- `MutationReceipt`
- `SemanticSnapshot`
- `SemanticDelta`
- `FingerprintSet`
- `ValidationResult`
- `RollbackReceipt`
- `StateChainEntry`

These are provider-internal contracts first. Public MCP exposure requires a separate explicit contract/version decision.

## 3. SourceSemanticModel

The source model captures what the drawing is supposed to represent independently of AutoCAD storage details.

Minimum shape:

```json
{
  "source_id": "src:...",
  "source_type": "raster|pdf-vector|dwg|dxf|brief|mixed",
  "units": "feet",
  "features": [],
  "constraints": [],
  "relationships": [],
  "certainty": [],
  "source_fp": "sha256:..."
}
```

Certainty vocabulary:

- `GROUND_TRUTH` — explicit readable/source-authoritative value;
- `DERIVED` — deterministic result derived from ground truth;
- `INFERRED` — estimated from visual proportion, domain judgment or incomplete source data.

Raster Vision is allowed during source ingestion when no structured source exists. Uncertainty must survive ingestion and remain traceable.

## 4. ActionSpec

One `ActionSpec` represents one small semantic mutation.

```json
{
  "action_id": "s04-a03",
  "operation": "entity.create.wall",
  "document_pid": "doc:...",
  "expected_parent_fp": "sha256:...",
  "scope": {
    "semantic_pids": ["pid:wall.primary.west"]
  },
  "args": {
    "start": [11.0, 10.0, 0.0],
    "end": [11.0, 21.0, 0.0],
    "thickness": 0.333333,
    "layer": "A-WALL-INT"
  },
  "allowed_effects": {
    "create": ["WALL"],
    "modify": [],
    "delete": []
  },
  "validation_ruleset_id": "wall-create-v1"
}
```

No `ActionSpec` may contain arbitrary C#, AutoLISP, shell, macro or free-form AutoCAD command text.

## 5. Pillar 1 — Data Integrity / Rollback

### 5.1 Integrity rule

A mutation may advance the state chain only when one of these outcomes is proven:

- `COMMITTED_VERIFIED` — mutation committed and independent read-back matches expected semantics;
- `ROLLED_BACK_VERIFIED` — mutation failed or integrity became uncertain, and the exact predecessor semantic fingerprint was restored.

Any other result is blocking:

- `EXECUTION_FAILED`
- `VALIDATION_FAILED`
- `COMMIT_INTEGRITY_FAIL`
- `ROLLBACK_FAILED`
- `STATE_UNCERTAIN`
- `STATE_DRIFT`

The next action MUST fail closed unless the current state fingerprint equals the expected parent fingerprint.

### 5.2 Transaction boundary

Preferred native sequence:

```text
verify parent state
  -> acquire document execution context / lock
  -> begin native transaction
  -> apply typed mutation
  -> extract provisional affected state
  -> deterministic in-transaction validation
      FAIL -> abort transaction
      PASS -> commit transaction
  -> independent read-only post-commit extraction
  -> canonicalize/fingerprint/diff
  -> verify persisted state
```

Do not hold a write transaction open while waiting for an AI/network round-trip. Validation rules required for commit are supplied with the action and executed deterministically.

### 5.3 Rollback tiers

Rollback is layered because not every side effect is a pure database mutation.

**R0 — Native transaction abort (preferred)**

- Used before commit when any deterministic invariant fails.
- Must restore the predecessor state without requiring UI commands.

**R1 — Verified inverse/undo compensation**

- Used only for a post-commit integrity failure when the bridge can deterministically reverse the exact committed mutation.
- Compensation itself is a semantic step and must be independently read back.

**R2 — Checkpoint restore**

- Required fallback for uncertain or non-invertible post-commit state.
- Restore from the last immutable accepted native checkpoint, reopen/read back, and prove `restored_document_fp == expected_parent_fp`.

No rollback is considered successful merely because an undo/reopen call returned success.

### 5.4 External side effects

Operations such as SaveAs/export/file replacement are not assumed to be covered by an AutoCAD database transaction.

For file side effects:

- write to a new/staging path where possible;
- verify produced artifact semantics/hash;
- promote/rename only after validation;
- never overwrite the last accepted native checkpoint before a replacement is verified;
- record filesystem artifact hashes in the state-chain entry.

### 5.5 Timeout / uncertain completion

A timeout is not automatically a failure and not automatically success.

If completion is uncertain:

1. stop dispatching new mutations;
2. perform native semantic read-back;
3. compare against predecessor and expected post-state fingerprints;
4. classify as committed, rolled back, or `STATE_UNCERTAIN`;
5. resolve to a verified known state before continuing.

Blind retry is forbidden because it can double-apply a mutation.

## 6. Pillar 2 — Precise Identity / PID + Fingerprinting

### 6.1 Identity layers

Do not conflate runtime identity with persistent semantic identity.

- `ObjectId` — AutoCAD runtime/database-session identity; useful in-process, not a cross-session PID.
- `Handle` — native DWG object handle; persisted within a drawing and useful for native resolution, but not sufficient semantic identity across clone/import/copy workflows.
- `semantic_pid` — provider-owned persistent semantic identity intended to survive save/reopen and distinguish conceptual instances.
- `geometry_fp` / `style_fp` / `topology_fp` — deterministic content identity.
- `instance_fp` — semantic PID + document PID + content fingerprints.

### 6.2 Document PID

Each managed document has a provider-owned `document_pid` stored in the DWG Named Objects Dictionary under application-owned XRecord metadata. N2 native AutoCAD 2027 probes selected `SLNCTRZ_CDT/DOCUMENT_PID` as the carrier.

`document_pid` is **persistent semantic lineage identity**, not globally unique physical-file identity. A byte-for-byte DWG copy preserves it. Therefore mutation targeting must combine runtime document binding + `document_pid` + `expected_parent_fp`; checkpoint/file identity additionally uses `artifact_fp` when needed. If multiple open documents share one `document_pid` and the runtime target cannot be unambiguously resolved, the mutation fails closed before execution.

The PID carrier is an implementation detail; lineage and targeting semantics are part of the contract.

### 6.3 Entity semantic PID

Each semantically managed DBObject receives a provider-owned PID. N2 native AutoCAD 2027 probes selected object Extension Dictionary + `SLNCTRZ_CDT_PID` XRecord as the carrier for N3+.

Required behavior:

- save/reopen preserves PID;
- ordinary editing preserves PID when conceptual identity remains the same;
- copy/clone intended as a new conceptual object receives a new PID;
- duplicate PIDs are detected and rejected/rehydrated according to an explicit clone policy;
- deletion retires the PID in the state delta;
- PID metadata must not be the only evidence that geometry is correct.

N2 finalized the carrier/clone policy for N3+: shallow `Clone()` receives a fresh PID; deep/cross/WBLOCK/INSERT result scopes require reconciliation/remap because those measured paths copy entity PID metadata; unresolved duplicates block acceptance.

The native semantic lane is intentionally a **provider-owned PID scope**, not an implicit adoption mechanism. AutoCAD 2027 Session-1 mixed-drawing acceptance confirmed that one legacy/COM-created entity without provider PID metadata would otherwise make document-wide semantic extraction and even unrelated PID-targeted operations fail ambiguously. The enforced short-term policy is therefore explicit refusal with `UNMANAGED_ENTITY_PRESENT`. Native semantic reads and mutations do not assign PIDs as a side effect; bringing legacy entities under native ownership requires a future explicit, bounded adoption/bootstrap operation with its own fingerprint/re-baseline semantics.

### 6.4 Fingerprint families

The protocol must distinguish at least:

- `geometry_fp` — normalized geometry only;
- `style_fp` — layer, linetype, lineweight, visibility and relevant presentation semantics;
- `topology_fp` — normalized relationships such as intersection, adjacency, containment and connectivity;
- `instance_fp` — `document_pid + semantic_pid + geometry_fp + style_fp + topology_fp` as applicable;
- `scope_fp` — deterministic digest of a selected semantic scope;
- `document_fp` — deterministic semantic document digest for the extractor's declared scope; transport/lifecycle metadata such as transient dirty/saved state is not semantic identity;
- `step_fp` — predecessor state + ActionSpec + delta + resulting state;
- `artifact_fp` — hash of native DWG/export evidence when relevant.

### 6.5 Canonicalization before hashing

Raw floating-point values MUST NOT be hashed directly.

Canonicalization requirements:

- declare coordinate frame and units;
- normalize numbers using versioned tolerances/quantization;
- normalize angle representation;
- normalize entity-type-specific ordering where semantic order is irrelevant;
- preserve order where geometry order is semantically meaningful;
- serialize using a deterministic schema/key order;
- sort sets/relations deterministically;
- include schema version and canonicalization profile in the fingerprint domain separator.

Example: `11.000000000002` and `10.999999999998` may canonicalize to the same coordinate under a declared `1e-6` tolerance.

#### 6.5.1 Document fingerprint schema v2

N5 introduces **document fingerprint schema v2** without changing the v1 geometry/action fingerprint domains. The version remains explicit in the document fingerprint domain separator and is advertised by the native bridge.

`SemanticSnapshot.saved` remains observable state, but it is excluded from `document_fp` because AutoCAD `DBMOD` is volatile lifecycle metadata rather than drawing semantics. Native acceptance measured both behaviors after `Transaction.Abort()`: one create-abort left `DBMOD=1`, while an update-abort returned `DBMOD=0`, even though independent semantic read-back restored the exact same geometry/PID/style/extents state in both cases. Treating `saved` as semantic identity would therefore turn a verified R0 rollback into a false state-drift failure.

Physical-file/checkpoint identity is separate and must use `artifact_fp` plus the file/checkpoint policy when required.

#### 6.5.2 Document fingerprint schema v3

G2 advances the semantic document fingerprint to **schema v3**. V3 preserves the v2 decision that volatile `saved/DBMOD` is not semantic identity and additionally includes schema-agnostic entity metadata in the canonical entity state. A metadata change therefore advances `document_fp` even when geometry, PID, layer and style are unchanged.

Metadata is canonicalized and bounded before persistence/hashing. Provider-owned identity/recovery namespaces remain outside arbitrary caller mutation. The native bridge advertises the document fingerprint schema explicitly; callers must never compare v2 and v3 fingerprints as if they were the same domain.

### 6.6 Duplicate detection

Two different semantic PIDs may legitimately have identical geometry fingerprints, but exact duplicate geometry can also indicate an accidental repeated mutation.

The validator must be able to detect:

- duplicate `semantic_pid`;
- duplicate geometry in a forbidden scope;
- unexpected same-position clones;
- handle/PID remapping after import/clone;
- object disappearance or substitution.

## 7. SemanticSnapshot

A snapshot describes native CAD state in a normalized provider-owned representation.

```json
{
  "schema_version": 1,
  "snapshot_id": "snap:...",
  "document": {
    "document_pid": "doc:...",
    "path": "...",
    "units": "feet",
    "space": "Model",
    "saved": true,
    "extents": {
      "min": [0.0, 0.0, 0.0],
      "max": [58.0, 44.0, 0.0]
    }
  },
  "entities": [],
  "relations": [],
  "styles": [],
  "fingerprints": {}
}
```

### 7.1 Common entity envelope

```json
{
  "native_handle": "2AF",
  "semantic_pid": "pid:...",
  "type": "LWPOLYLINE",
  "layer": "A-WALL-INT",
  "geometry": {},
  "bbox": {},
  "metrics": {},
  "style": {},
  "hierarchy": {},
  "fingerprints": {}
}
```

### 7.2 Minimum geometry extraction

2D baseline:

- LINE: start/end/length;
- CIRCLE: center/radius/circumference/area;
- ARC: center/radius/start/end/sweep/length;
- LWPOLYLINE/POLYLINE: ordered vertices, bulges, closed flag, length/area;
- TEXT/MTEXT: content, insertion/alignment, extents, height/rotation/style;
- INSERT/BLOCKREFERENCE: definition identity, transform, attributes/dynamic properties where applicable;
- HATCH: boundary references/loops, pattern and area where available;
- DIMENSION: measured value, definition points, style/type/text override;
- layers/linetypes/styles: stable semantic properties required for validation.

3D baseline:

- transform/matrix;
- bounding box;
- centroid/volume/surface metrics where supported;
- primitive/solid type when reliable;
- topology or BREP-derived signatures where practical;
- hierarchy/block ownership;
- relevant material/layer/style metadata.

## 8. SemanticDelta

A delta is computed from pre/post semantic states, assisted by native database events where available.

```json
{
  "created": ["pid:..."],
  "modified": ["pid:..."],
  "deleted": ["pid:..."],
  "unchanged_scope": ["pid:..."],
  "unexpected_changes": []
}
```

Native events are evidence/optimization, not the sole truth source. Post-action extraction remains authoritative.

`allowed_effects` in the ActionSpec is compared against this delta. In the accepted N7+O1 scope, validation independently checks requested geometry for LINE/CIRCLE/ARC/simple-LWPOLYLINE, target type/layer/style/hierarchy invariants, invariant document/style-resource state, affected-PID consistency and newly introduced duplicate geometry. O1 LWPOLYLINE is intentionally simple 2D: finite XY vertices, zero elevation, +Z normal and zero bulge/vertex widths; complex existing polylines are refused before write. Deterministic violations are rejected inside the native write transaction where possible; post-commit integrity failures use checkpoint-backed R1/R2 recovery and advance no state-chain entry unless the commit is accepted.

## 9. Deterministic ValidationRuleSet

Validation is machine-readable and tolerance-aware.

Rule families include:

- existence / non-existence;
- type/class;
- PID uniqueness;
- geometry fingerprint equality;
- coordinate/length/area/volume/radius tolerances;
- bbox constraints;
- closed/open state;
- layer/linetype/lineweight/style;
- parallel/perpendicular/tangent/concentric;
- intersection count/points;
- containment/adjacency/connectivity;
- duplicate geometry;
- allowed mutation scope;
- expected created/modified/deleted set;
- document units/state;
- predecessor fingerprint match.

Example:

```json
{
  "rules": [
    {"type": "exists", "target": "pid:wall.primary.west"},
    {"type": "layer", "target": "pid:wall.primary.west", "expected": "A-WALL-INT"},
    {"type": "length", "target": "pid:wall.primary.west", "expected": 11.0, "tolerance": 0.001},
    {"type": "unexpected_mutations", "expected": 0}
  ]
}
```

AI may decide which rules are required from design intent, but numeric/object validation itself should be deterministic whenever the native data permits it.

## 10. MutationReceipt

The native bridge returns an execution receipt that does not self-certify correctness.

```json
{
  "action_id": "s04-a03",
  "execution_status": "APPLIED_PROVISIONAL",
  "created_native_handles": ["2AF"],
  "event_delta": {},
  "transaction_id": "...",
  "provisional_snapshot": {},
  "native_errors": []
}
```

A receipt is input to validation, not final proof.

## 11. ValidationResult

```json
{
  "status": "PASS|FAIL|STATE_DRIFT|STATE_UNCERTAIN",
  "ruleset_id": "wall-create-v1",
  "checks": [],
  "expected_parent_fp": "sha256:...",
  "actual_parent_fp": "sha256:...",
  "post_state_fp": "sha256:...",
  "unexpected_changes": []
}
```

## 12. RollbackReceipt

Every rollback attempt must itself be proven.

```json
{
  "rollback_id": "rb:...",
  "reason": "VALIDATION_FAILED",
  "strategy": "R0_ABORT|R1_COMPENSATE|R2_CHECKPOINT_RESTORE",
  "expected_restore_fp": "sha256:parent...",
  "actual_restore_fp": "sha256:parent...",
  "status": "ROLLED_BACK_VERIFIED"
}
```

If the fingerprints differ, status is `ROLLBACK_FAILED` and the workflow halts.

## 13. StateChainEntry

Verified steps form a tamper-evident semantic chain:

```json
{
  "step_id": "s04",
  "parent_step_fp": "sha256:...",
  "parent_state_fp": "sha256:...",
  "action_fp": "sha256:...",
  "delta_fp": "sha256:...",
  "post_state_fp": "sha256:...",
  "artifact_fp": "sha256:...",
  "step_fp": "sha256:...",
  "status": "COMMITTED_VERIFIED"
}
```

Before the next step, current native state is read and compared with `post_state_fp`. A mismatch is `STATE_DRIFT`.

N6 additionally records per-run append-only JSONL evidence for committed steps, verified rollback, drift, validation failure and uncertainty. Each append is flushed and `fsync`-ed. The N6 journal deliberately refuses to infer/resume execution from a pre-existing non-empty journal; journal replay/resume is not part of the accepted N6 contract. Process-restart recovery is claimed only by later mechanisms/scopes that have their own documented acceptance evidence.

## 14. Major-gate semantic comparison

Major gates compare current semantic state to the SourceSemanticModel, not screenshots.

Examples:

- room/zone dimensions;
- footprint/extents;
- adjacency/topology;
- opening counts/locations/orientations;
- path/road connectivity;
- line/layer/style semantics;
- duplicate/gap/self-intersection checks;
- expected object hierarchy;
- declared `GROUND_TRUTH`/`DERIVED`/`INFERRED` constraints.

Screenshots may supplement human presentation quality, but cannot override failed semantic data.

## 15. Vision boundary

Correct execution loop:

```text
Action -> Native Execution -> Semantic Extraction -> Canonicalize -> Fingerprint/Diff -> Deterministic Validation -> Commit/Restore -> Next Action
```

Not:

```text
Action -> Screenshot -> AI guesses correctness -> Next Action
```

Vision remains valid for:

- raster source ingestion;
- visual composition/aesthetic review;
- final user-facing evidence.

## 16. Versioning and extension

The semantic protocol is versioned independently of transport and native adapter implementations.

Current rules:

- COM/ezdxf may populate only the semantic fields they can prove for their documented scopes;
- the Managed .NET lane owns the strongest PID/fingerprint/read-back/recovery guarantees for the native families that have actually passed their gates;
- document fingerprint schema v1/v2 evidence remains historical; current G2/G3 parent-state binding uses schema v3, which retains the v2 exclusion of volatile `saved/DBMOD` and adds canonical schema-agnostic metadata to entity semantic state;
- current generic native snapshots do not imply authoritative relation/topology data where no family-specific extractor exists;
- N7 post-commit recovery/R1/R2 is CLOSED / LIVE PASS for its bounded accepted scope, including activation-safe R2 after real AutoCAD restart;
- MP-2 hot reload is CLOSED/LIVE PASS and its one-authoritative-generation, drain/fence and health-proven switch invariants remain mandatory for runtime replacement;
- missing semantic fields must be reported as unsupported/unknown, never fabricated;
- any new native family must be introduced only from a concrete production requirement and must prove its own semantic/postcondition/recovery invariants before its assurance level is advertised.

There is no standing architecture requirement to replace every COM route with native code. Extension follows the demand-driven rule in `docs/ARCHITECTURE.md`.
