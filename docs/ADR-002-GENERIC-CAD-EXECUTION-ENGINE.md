# ADR-002 — Generic CAD Execution Engine Boundary

> Status: **ACCEPTED**
> Date: 2026-09-11
> Decision owner: Architect
> Applies to: CDT-AutoCAD MCP provider, Python semantic/runtime layer, local typed IPC, C# AutoCAD Managed .NET bridge
> Supersedes: any plan that would place infrastructure/civil/mechanical/domain business logic inside the MCP provider

## 1. Context

CDT-AutoCAD must serve multiple engineering disciplines without becoming a domain monolith. Infrastructure standards such as TCVN, mechanical rules, customer-specific design constraints, engineering calculations and audit/report generation evolve independently from CAD execution mechanics and belong to Domain Agents outside this provider.

The provider already owns generic CAD runtime concerns: authenticated MCP transport, document/runtime binding, persistent provider identity, semantic fingerprints, local typed IPC, native AutoCAD execution, mutation integrity, rollback/recovery and observability. Extending that layer with road, manhole, beam, pipe, TCVN or other business concepts would violate Separation of Concerns and make the provider difficult to reuse, certify and evolve.

## 2. Decision

**CDT-AutoCAD is a Generic CAD Execution Engine.**

The provider may understand CAD primitives and CAD execution invariants. It must not understand domain-specific engineering meaning.

Canonical boundary:

```text
Domain Agent
  ├─ engineering/business rules
  ├─ TCVN / ISO / ASME / customer standards
  ├─ calculations and design intent
  ├─ domain constraint validation
  └─ audit/report generation
          │
          ▼
Generic CAD MCP
  ├─ typed CAD geometry operations
  ├─ bounded batch/chunk orchestration
  ├─ transforms and block insertion
  ├─ schema-agnostic metadata get/set/query
  ├─ PID + semantic fingerprints
  ├─ logical transaction/recovery state
  └─ document/runtime state
          │
          ▼
Typed local IPC
          │
          ▼
C# AutoCAD Managed .NET Bridge
          │
          ▼
AutoCAD Document / Database
```

The MCP/native bridge may validate CAD-level facts such as malformed geometry, missing objects, invalid transforms, document binding, expected-parent drift, payload bounds, transaction/recovery state and metadata encoding limits. It must not validate rules such as road class, permitted slope, pipe sizing, structural sections, TCVN compliance or customer engineering policy.

## 3. Core capability program

### G1 — Generic Batch Geometry

Provide reusable CAD execution operations rather than domain operations.

Initial target surface:

- `batch_create_entities`;
- generic `transform_entities`;
- `insert_blocks`;
- later bounded `batch_update_entities` and `batch_delete_entities` using the same execution model.

Requirements:

- inputs are typed CAD primitives only;
- no `Road`, `Manhole`, `Beam`, `Pipe`, `TCVN`, or equivalent domain concept enters the provider contract;
- each operation is bound to one runtime document and an expected parent semantic state;
- work is partitioned into bounded chunks so large drawings do not hold the AutoCAD UI execution context for one unbounded interval;
- chunk size and execution budget are bounded by provider policy, not arbitrary caller-controlled infinity;
- execution yields between chunks through the native AutoCAD scheduling/lifecycle mechanism;
- every created or targeted entity is correlated by provider-owned persistent PID, never only transient ObjectId/Handle;
- progress/correlation is deterministic and observable without making telemetry part of transaction truth.

The target is safe execution beyond 1,000 entities without one giant long-lived `DocumentLock + Transaction`.

### G2 — Schema-Agnostic Metadata

Provide generic metadata persistence without teaching C# any domain schema.

Target surface:

- `metadata.get`;
- `metadata.set`;
- `metadata.query`.

Storage and contract rules:

- primary carrier is `DBObject.ExtensionDictionary -> XRecord`;
- metadata payload is arbitrary JSON from the provider's perspective;
- payloads are stored under explicit namespaces, for example `customer.infrastructure.v1`;
- provider-owned identity/recovery records use reserved namespaces and cannot be overwritten through generic metadata APIs;
- JSON is canonicalized for deterministic hashing/comparison;
- limits are enforced for encoded size, nesting depth, key count, string length and query work;
- malformed/non-JSON/numerically unsafe values fail before mutation;
- C# does not embed or compile domain-specific schemas;
- query semantics operate on generic JSON path/key/value predicates with bounded work, not domain concepts;
- XData is not the primary metadata store and is used only where explicit CAD interoperability requires it.

### G3 — Chunked Logical Atomicity

Large operations must preserve all-or-nothing **semantic outcome** without keeping one native AutoCAD transaction open for the full batch.

Required lifecycle:

```text
capture expected_parent_fp + pre-state/checkpoint
        ↓
execute bounded chunk 1 → verify → persist journal
        ↓ yield
execute bounded chunk 2 → verify → persist journal
        ↓ yield
...
        ↓
all chunks accepted
        ↓
independent final read-back + fingerprint proof
        ↓
COMMITTED_VERIFIED
```

If any chunk fails:

```text
failure
  ↓
stop forward execution
  ↓
reverse compensation from durable transaction journal/checkpoint
  ↓
independent read-back
  ↓
predecessor fingerprint equality
  ↓
ROLLED_BACK_VERIFIED
```

If rollback cannot be proven exactly, the provider enters a blocking uncertainty state such as `STATE_UNCERTAIN` / `ROLLBACK_FAILED`; no later mutation may proceed until recovery establishes an authoritative state.

This is **logical/semantic atomicity across chunks**, not one database transaction spanning thousands of entities. Intermediate chunk results may exist temporarily in the DWG while the logical operation is in progress, but they are not an accepted provider state until final verification succeeds.

If a future requirement demands that no intermediate state ever become visible in the target DWG, that requires a separate staging/off-document Database design followed by a final bounded transfer/commit. It is not implied by G3.

## 4. UI responsiveness invariant

Chunking exists to bound UI blocking, not merely to improve throughput.

The provider must not certify large-batch support using total runtime alone. Acceptance measures at minimum:

- longest continuous AutoCAD UI blocked interval;
- total operation latency;
- peak memory/process growth;
- per-chunk latency distribution;
- success/failure location and recovery latency;
- pending transaction/recovery state;
- worker/native process leaks;
- AutoCAD busy/modal behavior;
- document switch/close/reopen behavior where applicable.

Chunk size must be benchmark-derived. A fixed number such as 100 or 500 is not an architectural guarantee and may differ by entity family and operation cost.

## 5. Scale graduation ladder

Generic batch execution graduates through measured tiers:

1. **100 entities** — correctness baseline;
2. **1,000 entities** — first anti-freeze production gate;
3. **5,000 entities** — sustained large drawing gate;
4. **10,000 entities** — scale/stability gate.

Each tier requires correctness, failure injection and scalability evidence, including failure near the beginning, middle and end of the logical batch and exact rollback/predecessor fingerprint verification.

Passing one entity family or one scale tier does not certify another family/tier automatically.

## 6. Security and ownership boundaries

Reserved provider state must be isolated from arbitrary metadata. Generic metadata APIs must never mutate:

- provider document lineage PID;
- provider entity PID;
- recovery/checkpoint manifests;
- state-chain/journal records;
- internal protocol/version records.

No arbitrary C#, AutoLISP, command script, shell or free-text execution surface is introduced by G1/G2/G3.

Domain Agents may submit data and typed CAD actions, but cannot bypass document binding, expected-parent fingerprinting, payload limits, transaction gates or recovery uncertainty.

## 7. Public contract and migration rule

G1/G2/G3 implementation does not automatically publish new MCP tools. Public tool additions or routing changes require an explicit contract/version/promotion review after native implementation and AutoCAD live acceptance.

N8/N9/N10 remain separately gated. The G-series establishes the generic execution capabilities that later migration/promotion may consume; it does not collapse those gates.

## 8. Consequences

Positive:

- one reusable CAD execution engine serves infrastructure, architecture, mechanical and future Domain Agents;
- CAD safety and AutoCAD lifecycle behavior are implemented once;
- TCVN/customer/domain logic can change without recompiling the native bridge;
- metadata remains extensible without schema churn in C#;
- large operations gain bounded UI blocking while retaining verified logical rollback.

Trade-offs:

- logical atomicity requires journal/checkpoint/compensation complexity across chunks;
- intermediate chunk state may temporarily exist in the live DWG;
- generic JSON query requires strict resource bounds to avoid becoming an unbounded query engine;
- capability certification must be per entity family and scale tier rather than one broad "batch supported" claim.

## 9. Implementation order

The approved sequence is:

1. **G1 — Generic Batch Geometry**;
2. **G2 — Schema-Agnostic Metadata**;
3. **G3 — Chunked Logical Atomicity**;
4. scale graduation `100 -> 1,000 -> 5,000 -> 10,000` with correctness/failure/scalability evidence;
5. only then use the proven generic capabilities in later migration/promotion work.

Each G phase must have its own TDD, native build, real AutoCAD 2027 Interactive Session 1 acceptance, evidence, review and separate commit.

## 10. Decision summary

**Accepted:** keep domain intelligence outside CDT-AutoCAD. The provider remains a generic, typed, bounded, recoverable CAD execution engine. The next engineering program is G1 -> G2 -> G3, with chunking and verified logical atomicity as first-class cross-domain capabilities.
