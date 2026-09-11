# Threat Model — CDT-AutoCAD

> Baseline: MP0-T00 · Version: 2 · Updated: 2026-09-11 +07:00
> Scope: public MCP/COM/ezdxf, staged native IPC/bridge, filesystem/artifacts, recovery state and runtime lifecycle.
> Status: **ACTIVE BASELINE** — update by threat-model delta before enabling a materially new risky capability.

## 1. Purpose and security invariants

This document is the baseline threat model for CDT-AutoCAD. It is deliberately established before N8/public capability expansion; MP-8 performs a final re-review rather than introducing threat modeling for the first time.

Non-negotiable invariants:

1. A mutation targets an explicitly bound document/runtime identity; active-tab selection is not authority.
2. Unknown completion after dispatch is not permission to retry or release a second writer.
3. Recovery truth comes from durable journal/checkpoint state plus authoritative read-back, not diagnostic telemetry.
4. Filesystem authority is bounded by configured roots and the actual object/path used for the side effect.
5. Native IPC exposes typed operations only; no arbitrary shell, AutoLISP, C# or free-text AutoCAD command execution.
6. Capability certification and promotion apply only to the exact `family × operation × backend × AutoCAD version × scope` tuple with evidence.
7. Unsupported/unknown state fails closed; no silent downgrade to a weaker backend or validation mode.
8. Credentials and unnecessary drawing/brief payloads are excluded from diagnostic events and evidence.

## 2. Assets and trust boundaries

| ID | Asset / boundary | Why it matters |
| --- | --- | --- |
| A1 | User DWG/DXF and exported artifacts | Primary engineering data; wrong-target or corrupt writes are high impact. |
| A2 | Document lineage PID, entity PID and fingerprints | Authority for precise identity, drift detection and recovery verification. |
| A3 | Recovery journal, checkpoint manifest and checkpoint artifact | Correctness state required to determine whether writing may continue. |
| A4 | MCP auth/path/capability policy | Controls who may request work and where file side effects may occur. |
| A5 | Python provider generation/process | Owns request/job orchestration, retry semantics and public contract. |
| A6 | Local native IPC boundary | Separates provider process from code executing inside `acad.exe`. |
| A7 | Managed bridge loaded in AutoCAD | Executes native database transactions under AutoCAD lifecycle constraints. |
| A8 | Dependency lock/build/DLL provenance | Prevents evidence being attached to an unidentifiable or different runtime. |
| A9 | Diagnostic telemetry | Operational evidence only; useful for diagnosis but not recovery authority. |
| B1 | MCP client → provider | Potentially remote/untrusted request boundary. |
| B2 | Provider → filesystem | TOCTOU, path escape and artifact durability boundary. |
| B3 | Provider → native IPC → AutoCAD session | Session binding, replay, overload and lifecycle boundary. |
| B4 | AutoCAD document/database → external refs/input | Untrusted CAD content, xref/underlay/plugin/resource boundary. |
| B5 | Old runtime generation → new generation | Hot-reload fencing and mixed-generation ownership boundary. |

## 3. Baseline threats and controls

| ID | Attacker/action or failure | Failure mode | Required control | Verification | Owner |
| --- | --- | --- | --- | --- | --- |
| T01 | Unauthorized MCP caller or exposed endpoint | Unapproved read/write operations | Fail-closed HTTP auth; loopback-safe defaults; capability/path policy | HTTP/client auth tests; config review | Provider maintainer |
| T02 | Path traversal, symlink/junction/UNC or path swap | Read/write outside authorized roots | Resolve/validate actual target at side-effect boundary; platform-specific TOCTOU tests | Negative path suite on Linux + Windows | Provider maintainer |
| T03 | User/client switches active document between validation and mutation | Mutation lands on wrong drawing | Bind one concrete document/runtime identity through the operation; validate the bound object's actual path immediately before side effect; perform the side effect on that same object; verify the same object/path after side effect; response uses verified target only | A-02 active-document-switch regression + outside-root negative + post-save path-drift refusal + real AutoCAD 2027 Session 1 disposable Save fixture | Provider + native bridge |
| T04 | Timeout/cancel/disconnect/retry after dispatch | Duplicate write or concurrent writers | Unknown-completion latch; one COM STA executor; async mutation gate before dispatch; mutation timeout/cancel becomes non-retryable `completion_unknown`; later mutation stays quarantined after late completion; read-only reconciliation remains allowed; provider-process restart only after state verification | A-01 focused 8/8 PASS on Linux + Windows; clean full regression 212/4 Linux and 211/5 Windows; queued/running/cancel/late/read-timeout/document-new-negative cases | Provider maintainer |
| T05 | IPC spoof/replay/cross-session client | Native action executed by wrong process/session | Local current-user/current-session pipe boundary, typed protocol/version, bounded frames, replay/ownership checks | Session mismatch + protocol/adversarial tests | Native bridge |
| T06 | Malicious DWG/DXF/xref/underlay/plugin content | Code/resource load, path escape or parser abuse | No arbitrary command surface; explicit external-resource policy; bounded parser/input behavior | Malicious/unsupported input fixtures before related promotion | Provider + reviewer |
| T07 | Oversized request/snapshot/selection/queue/journal/checkpoint growth | DoS, memory/disk exhaustion, partial state | Explicit byte/entity/point/depth/time/disk budgets; refusal without silent truncation | Boundary + 1k/10k/100k or declared scale tests | Provider maintainer |
| T08 | Journal/checkpoint missing, corrupt, tampered or non-durable | False commit/rollback/recovery success | fsync/durability ordering, artifact hashes, fail-closed recovery state, independent semantic read-back | Corruption/disk/permission/process-kill tests | Semantic/recovery owner |
| T09 | Telemetry sink outage or diagnostic backpressure | Tool/recovery truth coupled to monitoring availability | Best-effort diagnostic emitter with explicit loss semantics; telemetry never substitutes for journal | Inject sink failure while tool succeeds; journal failure separately blocks | Runtime owner |
| T10 | Dependency/build/DLL drift | Test evidence attached to different executable/runtime | Platform PEP 751 locks, source-tree hash, Git/diff identity, DLL hash and target runtime in manifests | Clean locked install + manifest/evidence comparison | Release owner |
| T11 | Hot reload activates new worker before old writer is fenced | Mixed-generation mutation/race | Single authoritative generation; drain/fence/health gate; pending recovery blocks activation | Repeated reload + injected failure tests before MP-2 close | Runtime owner |
| T12 | PASS is generalized to sibling operation/backend/version/scope | Unsupported behavior advertised as certified | Capability registry/evidence keyed by exact promotion tuple | Metadata/evidence tests; review gate | Release owner |
| T13 | Error/log payload contains secrets or unnecessary project data | Credential or drawing-data leakage | Fixed diagnostic schema, redaction, no arbitrary payload/error text in structured telemetry | Schema tests + review | Runtime owner |
| T14 | Manual edit/save/reopen/clone/undo/redo races semantic state | Stale plan or false parent state | Persistent PID + versioned fingerprint + expected-parent guard + rebind/reconciliation | Native drift/clone/save/reopen acceptance | Semantic/native owner |

## 4. Failure-domain rule: journal vs telemetry

Recovery journal/checkpoint state is **correctness control-plane state**. If required state cannot be durably written/read/verified, the relevant mutation/recovery lane must fail closed and may remain blocked until explicit reconciliation.

Diagnostic events/logs/metrics are **observability state**. Sink failure follows a separate drop/buffer/backpressure policy and must not alter semantic commit/rollback/recovery truth. Telemetry presence is not evidence that a mutation committed; telemetry loss is not evidence that the journal was lost.

## 5. Capability threat-model delta gate

Before enabling a new risky capability tuple, add a delta containing:

| Field | Required content |
| --- | --- |
| Candidate tuple | `family × operation × backend × AutoCAD version × scope` |
| New assets/data | What becomes readable/writable/executable |
| New trust boundaries | New parser, file type, IPC operation, external ref, runtime or backend |
| Attacker/failure actions | Concrete misuse, race, corruption and resource-exhaustion cases |
| Existing controls reused | Named primitive/invariant already proven |
| New controls required | Minimum new enforcement; avoid duplicate infrastructure |
| Negative tests | Must fail before implementation and pass after it |
| Scalability/resource bounds | Predeclared sizes/budgets and refusal behavior |
| Evidence identity | Fixture hash, source/build/DLL, backend/version/scope, tolerance |
| Owner/reviewer | Person/role responsible for closure |

A delta is not required for a wording-only documentation change that adds no authority, input, side effect or runtime boundary. It is required before public promotion or enabling materially new mutation/input/recovery behavior.

## 6. Re-review triggers

Re-review this baseline when any of the following changes: public authentication/transport, allowed-path semantics, request/job ownership, native IPC trust boundary, recovery persistence model, hot-reload generation model, untrusted input support, dependency/build pipeline, or public capability tuple.

MP-8 must aggregate this baseline and all deltas against the final supported release surface; unresolved release-blocking threats remain explicit blockers rather than deferred notes.
