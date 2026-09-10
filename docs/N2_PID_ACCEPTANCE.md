# N2 PID Acceptance — AutoCAD 2027 Native Persistence Prototype

> Updated: 2026-09-10
> Status: **N2 CLOSED / NB2 STORAGE PROTOTYPE PASS**
> Target: AutoCAD 2027 full · Windows x64 · Managed .NET · real `acad.exe` Session 1

## 1. Scope

This checkpoint validates the persistent identity carrier and clone/remap policy required before the N3 Managed .NET bridge may depend on PID storage semantics.

It closes **N2 / the storage-and-identity portion of NB2**. At the time of N2 closure it did **not** close N3 IPC/native bridge, NB4 full Semantic State rollback/fingerprint integration, or NB5 post-commit recovery. N3 has since closed/live-passed; NB4/NB5 and N4+ semantic integration remain open.

## 2. Selected carrier

Prototype result accepted for the next architecture phase:

- document PID: Named Objects Dictionary -> `SLNCTRZ_CDT` `DBDictionary` -> `DOCUMENT_PID` `Xrecord`;
- managed DBObject PID: object Extension Dictionary -> `SLNCTRZ_CDT_PID` `Xrecord`;
- payload: schema version + PID string;
- native `ObjectId` and Handle remain runtime/native lookup aids only, not semantic identity.

The carrier is accepted together with an explicit **clone reconciliation policy**. The raw storage carrier alone is unsafe because native deep/cross/block cloning copies the extension-dictionary PID into the clone.

## 3. Live probe matrix

| Probe | Behavior | Result | Important evidence |
|---|---|---:|---|
| P0 | create + immediate read-back | PASS | document/entity PID persisted in native database objects |
| P1 | SaveAs + cold database reopen | PASS | document PID, entity PID and native Handle survived roundtrip |
| P2 | ordinary geometry edit | PASS | conceptual entity PID remained unchanged |
| P3 | same-database `DeepCloneObjects` | PASS | raw clone inherited the source PID; `IdMapping`-based remap restored uniqueness |
| P3b | shallow `DBObject.Clone()` | PASS | clone had no extension dictionary/PID; fresh PID assignment produced unique identity |
| P4 | cross-database `WblockCloneObjects` | PASS | raw clone inherited source PID; destination document PID stayed isolated; remap persisted |
| P5 | erase + unerase | PASS | PID remained readable while erased and survived unerase |
| P6 | real AutoCAD UNDO + REDO | PASS | geometry changed/restored/redone while document/entity PID stayed stable |
| P7 | deliberate duplicate corruption | PASS | exact PID collision detected, then remapped; no duplicate remained |
| P8 | native transaction abort | PASS | geometry, appended object and an existing XRecord PID overwrite all rolled back |
| P9 | WBLOCK + INSERT + block definition/reference | PASS | WBLOCK/INSERT copied entity PID; fresh document/definition/entity/reference PIDs persisted and index stayed unique |
| P10 | raw filesystem DWG copy | PASS | byte-identical copy preserved document PID + all entity PIDs; proves PID is lineage identity, not physical-file identity |

## 4. Clone / copy identity policy

The following rules are mandatory for N3+:

1. **Conceptual identity survives ordinary edits.** Geometry/style changes do not allocate a new semantic PID.
2. **Conceptual copies get new PIDs.** COPY/deep clone/cross-document clone/WBLOCK/INSERT may not retain the source semantic PID.
3. **Shallow `Clone()` gets an explicit fresh PID.** In the measured AutoCAD 2027 path, the shallow clone had no extension dictionary and therefore no inherited PID.
4. **Deep clone paths require reconciliation.** `DeepCloneObjects`, `WblockCloneObjects`, WBLOCK and INSERT copied the source entity XRecord/PID in the measured paths. The bridge must remap clone identities before a mutation may become accepted.
5. **Use native clone mapping when available.** `IdMapping` is the preferred source->clone association for deep/cross-database operations; operations without a direct mapping must run a deterministic post-clone identity scan over their known result scope.
6. **Derived/import-created databases get fresh document PIDs.** WBLOCK/cross-database destinations are distinct semantic lineages unless the operation explicitly preserves lineage.
7. **Raw file copies preserve lineage PID.** P10 proved byte-identical filesystem copies preserve `document_pid` and entity PIDs; therefore `document_pid` is a persistent semantic lineage identifier, not a globally unique physical-file identifier.
8. **Document targeting is composite and fail-closed.** N3+ must bind requests using runtime document context + `document_pid` + `expected_parent_fp`; when physical artifact identity matters, include `artifact_fp`. Multiple open documents with the same `document_pid` are ambiguous and block mutation until disambiguated.
9. **Duplicate entity PID is a blocking integrity defect.** A duplicate must be detected and reconciled inside the operation boundary; unresolved ambiguity maps to a blocking state, never to success.
10. **Block definition identity is separate from block-reference identity.** The `BlockTableRecord`, its managed contents, and every inserted `BlockReference` may carry distinct PIDs.

## 5. Rollback evidence relevant to PID storage

P8 intentionally performed three mutations inside one native transaction:

- changed existing line geometry;
- overwrote the existing entity PID XRecord with a different PID;
- appended a new entity and assigned it a duplicate PID.

`Transaction.Abort()` was followed by read-back. The predecessor entity PID and geometry were restored exactly, and entity count was unchanged. This proves that the selected XRecord carrier participates correctly in native transaction abort for the measured case.

This is **supporting N2 evidence only**. NB4 remains open until the N5 Semantic State executor integrates predecessor fingerprints, semantic snapshots and the `COMMITTED_VERIFIED` / `ROLLED_BACK_VERIFIED` state machine.

## 6. WBLOCK / INSERT observations

Measured on AutoCAD 2027:

- selected-object WBLOCK did **not** copy the source document PID NOD record in this probe;
- WBLOCK **did** copy the source entity extension-dictionary PID;
- INSERT **did** copy the WBLOCK entity PID into the inserted block-definition entity;
- after explicit re-identification, cold reopen preserved separate PIDs for destination document, `BlockTableRecord`, definition entity and `BlockReference`;
- final destination PID index had no collision.

Therefore the architecture must not infer identity semantics from whichever metadata AutoCAD happens to clone. **Every clone-like operation declares its identity policy explicitly and validates the result scope before commit/acceptance.**

## 7. Raw file-copy / document-lineage observation

P10 saved the live probe DWG, made a raw filesystem copy, then cold-opened both files. The two files were byte-identical and preserved the same `document_pid` plus the same managed entity PID set.

Therefore:

- `document_pid` identifies persistent semantic lineage, not a unique filesystem artifact;
- physical/checkpoint identity uses `artifact_fp` in addition to semantic state;
- an N3 bridge request may never select a live document by `document_pid` alone;
- the minimum safe mutation guard is runtime document binding + `document_pid` + `expected_parent_fp`;
- if multiple open AutoCAD documents share one lineage PID and the target cannot be uniquely resolved, return a blocking document-ambiguity/state-conflict result before mutation.

This closes a potentially dangerous false assumption before IPC design begins.

## 8. Evidence hashes

Canonical compact evidence committed with this checkpoint:

- `docs/evidence/n2-pid-native-2026-09-10.json`
- raw live summary SHA-256: `a288e1540acef817ba6fb9035bc048937b1f45233ad56df70e05ecdfa48c9a7c`
- final probe DWG SHA-256: `8370a0e6ec43498190f019539a7fa5912bb7ad1cbbd9e6436176ba1f10cfdaa2`
- live probe DLL SHA-256: `6b93b6316817e68c6b6547f903cebf71a5ba31adaf313a6a47efd2e6ec41d413`
- raw live summary status: `PASS`
- AutoCAD runtime reported: `26.0s (LMS Tech)` / AutoCAD 2027 family

The raw runner logs/DWG/DLL remain under ignored `artifacts/` / build output and are not treated as source files.

## 9. Build / secure-load note

Release build succeeds with `0` errors using user-local .NET SDK `10.0.401`. MSBuild still reports assembly-version conflict warnings for dependencies including `Microsoft.VisualBasic`, `System.Drawing` and `WindowsBase` between the SDK reference pack and Autodesk managed assemblies. The probe DLL nevertheless NETLOADed and executed all native probes inside real AutoCAD 2027.

For the N2 dev probe only, the exact output folder was persisted in AutoCAD `TRUSTEDPATHS` while `SECURELOAD=1` remained enabled. AutoCAD was restarted and the full P0–P10 runner NETLOADed the DLL and completed PASS without the publisher/trusted-folder modal interrupting execution. **N3 subsequently implemented the production-style dedicated per-user ApplicationPlugins bundle and explicitly trusts only its `Contents\Windows` directory; the repository tree was not broadly trusted.**

The committed probe reader also rejects malformed/unsupported PID XRecord schema instead of silently accepting an unknown version. N2 did not claim a live malformed-XRecord corruption-injection gate, and N3/NB0 did not add one; an explicit native PID-metadata corruption test remains open under later identity/semantic hardening rather than being retroactively claimed as N2 or N3 evidence.

These warnings were **not silently waived**. N3 carried the same three `MSB3277` warning families as explicit unsuppressed build debt, built with `0` errors, and passed NB0 live acceptance. Reference-model cleanup remains open build debt; it is documented rather than hidden or retroactively treated as a failed N3 gate.

## 10. Decision

**N2 is CLOSED.** The accepted next-phase PID policy is NOD/XRecord document **lineage** identity plus Extension-Dictionary/XRecord DBObject identity, always paired with clone-result reconciliation, duplicate-PID fail-closed validation, and composite runtime-document/state binding.

**Follow-on status:** N3 Managed .NET bridge skeleton + local typed IPC is now CLOSED/LIVE PASS. N3 consumed this PID contract without changing it. **Next phase: N4 — native semantic extractor.** Any later change to the N2 carrier/clone policy still requires reopening N2 evidence.
