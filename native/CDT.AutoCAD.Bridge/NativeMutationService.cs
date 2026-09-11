// NativeMutationService — N7 two-phase typed mutation, checkpointed recovery and verified rollback.
// Wing: code | Topic: native-bridge-n7 | Updated: 2026-09-10 20:45

using System.Text.Json;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CDT.AutoCAD.Bridge;

internal sealed record NativeRecoveryContext(
    NativeCheckpoint Checkpoint,
    string Operation,
    string AffectedPid,
    ObjectId AffectedObjectId,
    Dictionary<string, object?>? PredecessorEntity
);

internal sealed record NativeBatchRecoveryContext(
    NativeCheckpoint Checkpoint,
    string[] AffectedPids,
    ObjectId[] AffectedObjectIds,
    Dictionary<string, object?>[]? PredecessorEntities
);

internal sealed record NativeBatchCreatedEntity(string Pid, ObjectId ObjectId);

internal enum NativeR2RecoveryPhase
{
    WaitCheckpointActive,
    WaitRestoredActive,
}

internal sealed class NativeR2RecoveryState
{
    internal NativeR2RecoveryState(
        Guid requestId,
        NativeCheckpoint checkpoint,
        RecoveryResolveParams parameters,
        Document originalDocument,
        Document checkpointDocument
    )
    {
        RequestId = requestId;
        Checkpoint = checkpoint;
        Parameters = parameters;
        OriginalDocument = originalDocument;
        CheckpointDocument = checkpointDocument;
    }

    internal Guid RequestId { get; }
    internal NativeCheckpoint Checkpoint { get; }
    internal RecoveryResolveParams Parameters { get; }
    internal Document OriginalDocument { get; }
    internal Document CheckpointDocument { get; }
    internal Document? RestoredDocument { get; set; }
    internal NativeR2RecoveryPhase Phase { get; set; } = NativeR2RecoveryPhase.WaitCheckpointActive;
}

internal sealed class NativeMutationService
{
    private const double NumericTolerance = 1e-9;
    private readonly NativeSemanticExtractor _semantic;
    private readonly NativeCheckpointStore _checkpoints;
    private readonly DocumentRegistry _documents;
    private readonly Dictionary<string, NativeRecoveryContext> _recoveryContexts = new(StringComparer.Ordinal);
    private readonly Dictionary<string, NativeBatchRecoveryContext> _batchRecoveryContexts = new(StringComparer.Ordinal);
    private readonly Dictionary<Guid, NativeR2RecoveryState> _r2RecoveryStates = new();

    internal NativeMutationService(NativeSemanticExtractor semantic, DocumentRegistry documents)
    {
        _semantic = semantic;
        _documents = documents;
        _checkpoints = new NativeCheckpointStore();
    }

    internal object BeginLogicalBatch(
        Document document,
        Guid requestId,
        LogicalBeginParams parameters
    )
    {
        using DocumentLock documentLock = document.LockDocument();
        Dictionary<string, object?> before = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid,
            BridgeConstants.MaxBatchSemanticEntities
        );
        string parentFp = Fingerprint(before);
        if (!string.Equals(parentFp, parameters.ExpectedParentFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "STATE_DRIFT",
                "current semantic fingerprint does not match expected_parent_fp"
            );
        }
        NativeCheckpoint checkpoint = _checkpoints.Create(
            document,
            parameters.DocumentPid,
            parentFp,
            "logical.batch",
            requestId
        );
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["status"] = "OPEN",
            ["document_pid"] = parameters.DocumentPid,
            ["pre_document_fp"] = parentFp,
            ["logical_transaction"] = CheckpointPayload(checkpoint),
        };
    }

    internal object MetadataGet(Document document, MetadataGetParams parameters)
    {
        using DocumentLock documentLock = document.LockDocument();
        using Transaction transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(
            document.Database,
            parameters.SemanticPid,
            transaction
        );
        DBObject target = transaction.GetObject(objectId, OpenMode.ForRead, false);
        bool found = NativeMetadataStore.TryGet(
            target,
            transaction,
            parameters.Namespace,
            out JsonElement value
        );
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["document_pid"] = parameters.DocumentPid,
            ["semantic_pid"] = parameters.SemanticPid,
            ["namespace"] = parameters.Namespace,
            ["found"] = found,
            ["value"] = found ? value : null,
        };
    }

    internal object MetadataQuery(Document document, MetadataQueryParams parameters)
    {
        using DocumentLock documentLock = document.LockDocument();
        using Transaction transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
        BlockTableRecord space = (BlockTableRecord)transaction.GetObject(
            document.Database.CurrentSpaceId,
            OpenMode.ForRead
        );
        List<Dictionary<string, object?>> matches = [];
        bool truncated = false;
        int scannedEntities = 0;
        foreach (ObjectId objectId in space)
        {
            scannedEntities++;
            if (scannedEntities > BridgeConstants.MaxBatchSemanticEntities)
            {
                throw new BridgeServiceException(
                    "SNAPSHOT_CAPACITY_EXCEEDED",
                    "metadata query exceeds the bounded semantic scan capacity"
                );
            }
            if (objectId.IsErased
                || transaction.GetObject(objectId, OpenMode.ForRead, false) is not Entity entity)
            {
                continue;
            }
            string semanticPid = EntityPidReader.ReadRequired(entity, transaction);
            if (!NativeMetadataStore.TryGet(
                entity,
                transaction,
                parameters.Namespace,
                out JsonElement rootValue
            ))
            {
                continue;
            }
            JsonElement selected = rootValue;
            if (parameters.Path is not null)
            {
                if (!NativeMetadataStore.TryResolvePath(rootValue, parameters.Path, out selected)
                    || parameters.MatchValue is null
                    || !NativeMetadataStore.JsonEquals(selected, parameters.MatchValue.Value))
                {
                    continue;
                }
            }
            if (matches.Count >= parameters.Limit)
            {
                truncated = true;
                break;
            }
            matches.Add(new Dictionary<string, object?>
            {
                ["semantic_pid"] = semanticPid,
                ["value"] = rootValue.Clone(),
            });
        }
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["document_pid"] = parameters.DocumentPid,
            ["namespace"] = parameters.Namespace,
            ["path"] = parameters.Path,
            ["count"] = matches.Count,
            ["scanned_entities"] = scannedEntities,
            ["limit"] = parameters.Limit,
            ["truncated"] = truncated,
            ["items"] = matches.ToArray(),
        };
    }

    internal object MetadataSet(
        Document document,
        Guid requestId,
        MetadataSetParams parameters
    )
    {
        const string operation = "metadata.set";
        using DocumentLock documentLock = document.LockDocument();
        Dictionary<string, object?> before = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid,
            BridgeConstants.MaxBatchSemanticEntities
        );
        string parentFp = Fingerprint(before);
        if (!string.Equals(parentFp, parameters.ExpectedParentFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "STATE_DRIFT",
                "current semantic fingerprint does not match expected_parent_fp"
            );
        }
        Dictionary<string, object?> predecessorEntity = FindEntity(before, parameters.SemanticPid);
        if (predecessorEntity["metadata"] is Dictionary<string, object?> predecessorMetadata
            && predecessorMetadata.TryGetValue(parameters.Namespace, out object? previousValue)
            && previousValue is not null
            && SemanticFingerprint.CanonicalMetadataSortKey(previousValue)
                == SemanticFingerprint.CanonicalMetadataSortKey(parameters.Value))
        {
            throw new BridgeServiceException(
                "NO_EFFECT",
                "metadata set value already matches the protected predecessor state"
            );
        }

        NativeCheckpoint checkpoint = _checkpoints.Create(
            document,
            parameters.DocumentPid,
            parentFp,
            operation,
            requestId
        );
        ObjectId affectedObjectId = ObjectId.Null;
        Dictionary<string, object?>? provisional = null;
        bool committed = false;
        string? r0Reason = null;
        try
        {
            using (Transaction transaction = document.Database.TransactionManager.StartTransaction())
            {
                affectedObjectId = EntityPidStore.ResolveCurrentSpaceEntity(
                    document.Database,
                    parameters.SemanticPid,
                    transaction
                );
                DBObject target = transaction.GetObject(
                    affectedObjectId,
                    OpenMode.ForWrite,
                    false
                );
                NativeMetadataStore.SetNamespace(
                    target,
                    transaction,
                    parameters.Namespace,
                    parameters.Value
                );

                if (string.Equals(
                    parameters.FaultStage,
                    "after_apply_before_commit",
                    StringComparison.Ordinal
                ))
                {
                    r0Reason = "FAULT_INJECTED";
                    transaction.Abort();
                }
                else
                {
                    try
                    {
                        provisional = _semantic.BuildProvisionalFromAffected(
                            document,
                            parameters.RuntimeDocumentId,
                            parameters.DocumentPid,
                            before,
                            transaction,
                            new[] { affectedObjectId },
                            BridgeConstants.MaxBatchSemanticEntities
                        );
                        NativeMutationValidator.ValidateMetadataSet(parameters, before, provisional);
                    }
                    catch (BridgeServiceException exc) when (
                        exc.Code is "PROVISIONAL_VALIDATION_FAILED" or "SNAPSHOT_TOO_LARGE"
                    )
                    {
                        r0Reason = "PROVISIONAL_VALIDATION_FAILED";
                    }

                    if (r0Reason is not null)
                    {
                        transaction.Abort();
                    }
                    else
                    {
                        transaction.Commit();
                        committed = true;
                    }
                }
            }

            if (!committed)
            {
                Dictionary<string, object?> afterAbort = _semantic.Extract(
                    document,
                    parameters.RuntimeDocumentId,
                    parameters.DocumentPid,
                    BridgeConstants.MaxBatchSemanticEntities
                );
                string restoreFp = Fingerprint(afterAbort);
                bool restored = string.Equals(restoreFp, parentFp, StringComparison.Ordinal);
                if (restored)
                {
                    _checkpoints.Finalize(checkpoint);
                }
                return RollbackResult(
                    operation,
                    parameters.DocumentPid,
                    parameters.SemanticPid,
                    checkpoint,
                    "R0_ABORT",
                    r0Reason ?? "EXECUTION_FAILED",
                    parentFp,
                    restoreFp,
                    restored,
                    parameters.RuntimeDocumentId
                );
            }

            if (provisional is null)
            {
                throw new BridgeServiceException(
                    "COMMIT_INTEGRITY_FAIL",
                    "provisional semantic state is unavailable after metadata commit"
                );
            }
            string provisionalFp = Fingerprint(provisional);
            _recoveryContexts[checkpoint.CheckpointId] = new NativeRecoveryContext(
                checkpoint,
                operation,
                parameters.SemanticPid,
                affectedObjectId,
                predecessorEntity
            );
            Dictionary<string, object?> persisted = _semantic.Extract(
                document,
                parameters.RuntimeDocumentId,
                parameters.DocumentPid,
                BridgeConstants.MaxBatchSemanticEntities
            );
            string persistedFp = Fingerprint(persisted);
            bool integrityOk = string.Equals(provisionalFp, persistedFp, StringComparison.Ordinal);
            return new Dictionary<string, object?>
            {
                ["schema_version"] = 1,
                ["operation"] = operation,
                ["outcome"] = integrityOk ? "COMMITTED_VERIFIED" : "COMMIT_INTEGRITY_FAIL",
                ["document_pid"] = parameters.DocumentPid,
                ["pre_document_fp"] = parentFp,
                ["provisional_document_fp"] = provisionalFp,
                ["post_document_fp"] = persistedFp,
                ["affected_semantic_pid"] = parameters.SemanticPid,
                ["namespace"] = parameters.Namespace,
                ["recovery_checkpoint"] = CheckpointPayload(checkpoint),
            };
        }
        catch
        {
            if (!committed)
            {
                try { _checkpoints.Finalize(checkpoint); }
                catch { }
            }
            throw;
        }
    }

    internal object Execute(
        Document document,
        Guid requestId,
        string operation,
        EntityMutationParams parameters
    )
    {
        using DocumentLock documentLock = document.LockDocument();
        Dictionary<string, object?> before = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        string parentFp = Fingerprint(before);
        if (!string.Equals(parentFp, parameters.ExpectedParentFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "STATE_DRIFT",
                "current semantic fingerprint does not match expected_parent_fp"
            );
        }
        if (operation.StartsWith("entity.create.", StringComparison.Ordinal))
        {
            List<Dictionary<string, object?>> entities = SnapshotEntities(before);
            if (entities.Count >= BridgeConstants.MaxSnapshotEntities)
            {
                throw new BridgeServiceException(
                    "SNAPSHOT_CAPACITY_EXCEEDED",
                    "create would exceed the bounded semantic snapshot capacity"
                );
            }
        }

        NativeCheckpoint checkpoint = _checkpoints.Create(
            document,
            parameters.DocumentPid,
            parentFp,
            operation,
            requestId
        );

        string affectedPid = string.Empty;
        ObjectId affectedObjectId = ObjectId.Null;
        Dictionary<string, object?>? predecessorEntity = null;
        Dictionary<string, object?>? provisional = null;
        bool committed = false;
        string? r0Reason = null;
        try
        {
            using (Transaction transaction = document.Database.TransactionManager.StartTransaction())
            {
                if (!operation.StartsWith("entity.create.", StringComparison.Ordinal))
                {
                    string targetPid = RequireTargetPid(parameters);
                    predecessorEntity = FindEntity(before, targetPid);
                    affectedObjectId = EntityPidStore.ResolveCurrentSpaceEntity(
                        document.Database,
                        targetPid,
                        transaction
                    );
                }

                affectedPid = ApplyMutation(document.Database, transaction, operation, parameters);
                if (affectedObjectId.IsNull)
                {
                    affectedObjectId = EntityPidStore.ResolveCurrentSpaceEntity(
                        document.Database,
                        affectedPid,
                        transaction
                    );
                }

                if (string.Equals(parameters.FaultStage, "before_commit_add_stray", StringComparison.Ordinal))
                {
                    AddStrayEntity(document.Database, transaction);
                }

                if (string.Equals(parameters.FaultStage, "after_apply_before_commit", StringComparison.Ordinal))
                {
                    r0Reason = "FAULT_INJECTED";
                    transaction.Abort();
                }
                else
                {
                    try
                    {
                        provisional = _semantic.ExtractWithinTransaction(
                            document,
                            parameters.RuntimeDocumentId,
                            parameters.DocumentPid,
                            transaction
                        );
                        NativeMutationValidator.Validate(
                            operation,
                            affectedPid,
                            parameters,
                            before,
                            provisional
                        );
                    }
                    catch (BridgeServiceException exc) when (
                        exc.Code is "PROVISIONAL_VALIDATION_FAILED" or "SNAPSHOT_TOO_LARGE")
                    {
                        r0Reason = "PROVISIONAL_VALIDATION_FAILED";
                    }

                    if (r0Reason is not null)
                    {
                        transaction.Abort();
                    }
                    else
                    {
                        transaction.Commit();
                        committed = true;
                    }
                }
            }

            if (!committed)
            {
                Dictionary<string, object?> afterAbort = _semantic.Extract(
                    document,
                    parameters.RuntimeDocumentId,
                    parameters.DocumentPid
                );
                string restoreFp = Fingerprint(afterAbort);
                bool restored = string.Equals(restoreFp, parentFp, StringComparison.Ordinal);
                if (restored)
                {
                    _checkpoints.Finalize(checkpoint);
                }
                return RollbackResult(
                    operation,
                    parameters.DocumentPid,
                    affectedPid,
                    checkpoint,
                    "R0_ABORT",
                    r0Reason ?? "EXECUTION_FAILED",
                    parentFp,
                    restoreFp,
                    restored,
                    parameters.RuntimeDocumentId
                );
            }

            if (provisional is null)
            {
                throw new BridgeServiceException(
                    "COMMIT_INTEGRITY_FAIL",
                    "provisional semantic state is unavailable after native commit"
                );
            }
            string provisionalFp = Fingerprint(provisional);
            _recoveryContexts[checkpoint.CheckpointId] = new NativeRecoveryContext(
                checkpoint,
                operation,
                affectedPid,
                affectedObjectId,
                predecessorEntity
            );

            if (string.Equals(parameters.FaultStage, "after_commit_corrupt_target", StringComparison.Ordinal))
            {
                CorruptAffectedEntity(document.Database, affectedObjectId);
            }
            else if (string.Equals(parameters.FaultStage, "after_commit_add_stray", StringComparison.Ordinal))
            {
                using Transaction faultTransaction = document.Database.TransactionManager.StartTransaction();
                AddStrayEntity(document.Database, faultTransaction);
                faultTransaction.Commit();
            }

            Dictionary<string, object?> persisted = _semantic.Extract(
                document,
                parameters.RuntimeDocumentId,
                parameters.DocumentPid
            );
            string persistedFp = Fingerprint(persisted);
            bool integrityOk = string.Equals(provisionalFp, persistedFp, StringComparison.Ordinal);
            return new Dictionary<string, object?>
            {
                ["schema_version"] = 1,
                ["operation"] = operation,
                ["outcome"] = integrityOk ? "COMMITTED_VERIFIED" : "COMMIT_INTEGRITY_FAIL",
                ["document_pid"] = parameters.DocumentPid,
                ["pre_document_fp"] = parentFp,
                ["provisional_document_fp"] = provisionalFp,
                ["post_document_fp"] = persistedFp,
                ["affected_semantic_pid"] = affectedPid,
                ["recovery_checkpoint"] = CheckpointPayload(checkpoint),
            };
        }
        catch
        {
            if (!committed)
            {
                try { _checkpoints.Finalize(checkpoint); }
                catch { }
            }
            throw;
        }
    }

    internal object ExecuteBatchCreate(
        Document document,
        Guid requestId,
        BatchCreateParams parameters
    )
    {
        const string operation = "entity.batch.create";
        using DocumentLock documentLock = document.LockDocument();
        Dictionary<string, object?> before = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid,
            BridgeConstants.MaxBatchSemanticEntities
        );
        string parentFp = Fingerprint(before);
        if (!string.Equals(parentFp, parameters.ExpectedParentFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "STATE_DRIFT",
                "current semantic fingerprint does not match expected_parent_fp"
            );
        }
        if (SnapshotEntities(before).Count + parameters.Entities.Length > BridgeConstants.MaxBatchSemanticEntities)
        {
            throw new BridgeServiceException(
                "SNAPSHOT_CAPACITY_EXCEEDED",
                "batch create would exceed the bounded G1 semantic verification capacity"
            );
        }

        (NativeCheckpoint checkpoint, bool ownsCheckpoint) = AcquireBatchCheckpoint(
            document,
            parameters.DocumentPid,
            parentFp,
            operation,
            requestId,
            parameters.LogicalTransaction
        );
        List<string> affectedPids = [];
        List<ObjectId> affectedObjectIds = [];
        Dictionary<string, object?>? provisional = null;
        bool committed = false;
        string? r0Reason = null;
        try
        {
            using (Transaction transaction = document.Database.TransactionManager.StartTransaction())
            {
                foreach (BatchCreateEntitySpec spec in parameters.Entities)
                {
                    NativeBatchCreatedEntity created = ApplyBatchCreateEntity(
                        document.Database,
                        transaction,
                        spec
                    );
                    affectedPids.Add(created.Pid);
                    affectedObjectIds.Add(created.ObjectId);
                }

                if (string.Equals(parameters.FaultStage, "after_apply_before_commit", StringComparison.Ordinal))
                {
                    r0Reason = "FAULT_INJECTED";
                    transaction.Abort();
                }
                else
                {
                    try
                    {
                        provisional = _semantic.BuildProvisionalFromAffected(
                            document,
                            parameters.RuntimeDocumentId,
                            parameters.DocumentPid,
                            before,
                            transaction,
                            affectedObjectIds,
                            BridgeConstants.MaxBatchSemanticEntities
                        );
                        NativeMutationValidator.ValidateBatchCreate(
                            affectedPids,
                            parameters,
                            before,
                            provisional
                        );
                    }
                    catch (BridgeServiceException exc) when (
                        exc.Code is "PROVISIONAL_VALIDATION_FAILED" or "SNAPSHOT_TOO_LARGE")
                    {
                        r0Reason = "PROVISIONAL_VALIDATION_FAILED";
                    }

                    if (r0Reason is not null)
                    {
                        transaction.Abort();
                    }
                    else
                    {
                        transaction.Commit();
                        committed = true;
                    }
                }
            }

            if (!committed)
            {
                Dictionary<string, object?> afterAbort = _semantic.Extract(
                    document,
                    parameters.RuntimeDocumentId,
                    parameters.DocumentPid,
                    BridgeConstants.MaxBatchSemanticEntities
                );
                string restoreFp = Fingerprint(afterAbort);
                bool restored = string.Equals(restoreFp, parentFp, StringComparison.Ordinal);
                if (restored && ownsCheckpoint)
                {
                    _checkpoints.Finalize(checkpoint);
                }
                return BatchRollbackResult(
                    operation,
                    parameters.DocumentPid,
                    affectedPids,
                    checkpoint,
                    "R0_ABORT",
                    r0Reason ?? "EXECUTION_FAILED",
                    parentFp,
                    restoreFp,
                    restored,
                    parameters.RuntimeDocumentId
                );
            }

            if (provisional is null)
            {
                throw new BridgeServiceException(
                    "COMMIT_INTEGRITY_FAIL",
                    "provisional semantic state is unavailable after batch commit"
                );
            }
            string provisionalFp = Fingerprint(provisional);
            if (ownsCheckpoint)
            {
                _batchRecoveryContexts[checkpoint.CheckpointId] = new NativeBatchRecoveryContext(
                    checkpoint,
                    affectedPids.ToArray(),
                    affectedObjectIds.ToArray(),
                    null
                );
            }

            Dictionary<string, object?> persisted = _semantic.Extract(
                document,
                parameters.RuntimeDocumentId,
                parameters.DocumentPid,
                BridgeConstants.MaxBatchSemanticEntities
            );
            string persistedFp = Fingerprint(persisted);
            bool integrityOk = string.Equals(provisionalFp, persistedFp, StringComparison.Ordinal);
            return new Dictionary<string, object?>
            {
                ["schema_version"] = 1,
                ["operation"] = operation,
                ["outcome"] = integrityOk ? "COMMITTED_VERIFIED" : "COMMIT_INTEGRITY_FAIL",
                ["document_pid"] = parameters.DocumentPid,
                ["pre_document_fp"] = parentFp,
                ["provisional_document_fp"] = provisionalFp,
                ["post_document_fp"] = persistedFp,
                ["affected_semantic_pids"] = affectedPids.ToArray(),
                ["recovery_checkpoint"] = CheckpointPayload(checkpoint),
            };
        }
        catch
        {
            if (!committed && ownsCheckpoint)
            {
                try { _checkpoints.Finalize(checkpoint); }
                catch { }
            }
            throw;
        }
    }

    internal object ExecuteBatchInsertBlocks(
        Document document,
        Guid requestId,
        BatchInsertBlocksParams parameters
    )
    {
        const string operation = "entity.batch.insert_blocks";
        using DocumentLock documentLock = document.LockDocument();
        Dictionary<string, object?> before = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid,
            BridgeConstants.MaxBatchSemanticEntities
        );
        string parentFp = Fingerprint(before);
        if (!string.Equals(parentFp, parameters.ExpectedParentFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "STATE_DRIFT",
                "current semantic fingerprint does not match expected_parent_fp"
            );
        }
        if (SnapshotEntities(before).Count + parameters.Inserts.Length > BridgeConstants.MaxBatchSemanticEntities)
        {
            throw new BridgeServiceException(
                "SNAPSHOT_CAPACITY_EXCEEDED",
                "block insert batch would exceed the bounded G1 semantic verification capacity"
            );
        }
        foreach (string definitionPid in parameters.Inserts.Select(item => item.DefinitionPid).Distinct(StringComparer.Ordinal))
        {
            _ = FindVisibleBlockDefinition(before, definitionPid);
        }

        (NativeCheckpoint checkpoint, bool ownsCheckpoint) = AcquireBatchCheckpoint(
            document,
            parameters.DocumentPid,
            parentFp,
            operation,
            requestId,
            parameters.LogicalTransaction
        );
        List<string> affectedPids = [];
        List<ObjectId> affectedObjectIds = [];
        Dictionary<string, object?>? provisional = null;
        bool committed = false;
        string? r0Reason = null;
        try
        {
            using (Transaction transaction = document.Database.TransactionManager.StartTransaction())
            {
                string[] definitionPids = parameters.Inserts
                    .Select(item => item.DefinitionPid)
                    .Distinct(StringComparer.Ordinal)
                    .ToArray();
                IReadOnlyDictionary<string, ObjectId> definitions = EntityPidStore.ResolveBlockDefinitions(
                    document.Database,
                    definitionPids,
                    transaction
                );
                BlockTableRecord space = CurrentSpace(document.Database, transaction);
                foreach (BatchInsertBlockSpec spec in parameters.Inserts)
                {
                    BlockReference blockReference = new(
                        Point(spec.Position, "position"),
                        definitions[spec.DefinitionPid]
                    )
                    {
                        LayerId = document.Database.Clayer,
                        Rotation = spec.Rotation,
                        ScaleFactors = new Scale3d(spec.Scale),
                    };
                    NativeBatchCreatedEntity created = AppendBatchWithPid(
                        space,
                        blockReference,
                        transaction
                    );
                    affectedPids.Add(created.Pid);
                    affectedObjectIds.Add(created.ObjectId);
                }

                if (string.Equals(parameters.FaultStage, "after_apply_before_commit", StringComparison.Ordinal))
                {
                    r0Reason = "FAULT_INJECTED";
                    transaction.Abort();
                }
                else
                {
                    try
                    {
                        provisional = _semantic.BuildProvisionalFromAffected(
                            document,
                            parameters.RuntimeDocumentId,
                            parameters.DocumentPid,
                            before,
                            transaction,
                            affectedObjectIds,
                            BridgeConstants.MaxBatchSemanticEntities
                        );
                        NativeMutationValidator.ValidateBatchInsertBlocks(
                            affectedPids,
                            parameters,
                            before,
                            provisional
                        );
                    }
                    catch (BridgeServiceException exc) when (
                        exc.Code is "PROVISIONAL_VALIDATION_FAILED" or "SNAPSHOT_TOO_LARGE")
                    {
                        r0Reason = "PROVISIONAL_VALIDATION_FAILED";
                    }

                    if (r0Reason is not null)
                    {
                        transaction.Abort();
                    }
                    else
                    {
                        transaction.Commit();
                        committed = true;
                    }
                }
            }

            if (!committed)
            {
                Dictionary<string, object?> afterAbort = _semantic.Extract(
                    document,
                    parameters.RuntimeDocumentId,
                    parameters.DocumentPid,
                    BridgeConstants.MaxBatchSemanticEntities
                );
                string restoreFp = Fingerprint(afterAbort);
                bool restored = string.Equals(restoreFp, parentFp, StringComparison.Ordinal);
                if (restored && ownsCheckpoint)
                {
                    _checkpoints.Finalize(checkpoint);
                }
                return BatchRollbackResult(
                    operation,
                    parameters.DocumentPid,
                    affectedPids,
                    checkpoint,
                    "R0_ABORT",
                    r0Reason ?? "EXECUTION_FAILED",
                    parentFp,
                    restoreFp,
                    restored,
                    parameters.RuntimeDocumentId
                );
            }

            if (provisional is null)
            {
                throw new BridgeServiceException(
                    "COMMIT_INTEGRITY_FAIL",
                    "provisional semantic state is unavailable after block insert batch commit"
                );
            }
            string provisionalFp = Fingerprint(provisional);
            if (ownsCheckpoint)
            {
                _batchRecoveryContexts[checkpoint.CheckpointId] = new NativeBatchRecoveryContext(
                    checkpoint,
                    affectedPids.ToArray(),
                    affectedObjectIds.ToArray(),
                    null
                );
            }

            Dictionary<string, object?> persisted = _semantic.Extract(
                document,
                parameters.RuntimeDocumentId,
                parameters.DocumentPid,
                BridgeConstants.MaxBatchSemanticEntities
            );
            string persistedFp = Fingerprint(persisted);
            bool integrityOk = string.Equals(provisionalFp, persistedFp, StringComparison.Ordinal);
            return new Dictionary<string, object?>
            {
                ["schema_version"] = 1,
                ["operation"] = operation,
                ["outcome"] = integrityOk ? "COMMITTED_VERIFIED" : "COMMIT_INTEGRITY_FAIL",
                ["document_pid"] = parameters.DocumentPid,
                ["pre_document_fp"] = parentFp,
                ["provisional_document_fp"] = provisionalFp,
                ["post_document_fp"] = persistedFp,
                ["affected_semantic_pids"] = affectedPids.ToArray(),
                ["recovery_checkpoint"] = CheckpointPayload(checkpoint),
            };
        }
        catch
        {
            if (!committed && ownsCheckpoint)
            {
                try { _checkpoints.Finalize(checkpoint); }
                catch { }
            }
            throw;
        }
    }

    internal object ExecuteBatchTransform(
        Document document,
        Guid requestId,
        BatchTransformParams parameters
    )
    {
        const string operation = "entity.batch.transform";
        using DocumentLock documentLock = document.LockDocument();
        Dictionary<string, object?> before = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid,
            BridgeConstants.MaxBatchSemanticEntities
        );
        string parentFp = Fingerprint(before);
        if (!string.Equals(parentFp, parameters.ExpectedParentFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "STATE_DRIFT",
                "current semantic fingerprint does not match expected_parent_fp"
            );
        }

        (NativeCheckpoint checkpoint, bool ownsCheckpoint) = AcquireBatchCheckpoint(
            document,
            parameters.DocumentPid,
            parentFp,
            operation,
            requestId,
            parameters.LogicalTransaction
        );
        List<ObjectId> affectedObjectIds = [];
        List<Dictionary<string, object?>> predecessorEntities = [];
        Dictionary<string, object?>? provisional = null;
        bool committed = false;
        string? r0Reason = null;
        try
        {
            Matrix3d transform = BatchTransformMatrix(parameters.Transform);
            using (Transaction transaction = document.Database.TransactionManager.StartTransaction())
            {
                IReadOnlyDictionary<string, ObjectId> targets = EntityPidStore.ResolveCurrentSpaceEntities(
                    document.Database,
                    parameters.SemanticPids,
                    transaction
                );
                foreach (string pid in parameters.SemanticPids)
                {
                    Dictionary<string, object?> predecessor = FindEntity(before, pid);
                    ObjectId objectId = targets[pid];
                    Entity target = (Entity)transaction.GetObject(objectId, OpenMode.ForWrite, false);
                    EnsureBatchTransformTarget(target, predecessor);
                    target.TransformBy(transform);
                    affectedObjectIds.Add(objectId);
                    predecessorEntities.Add(predecessor);
                }

                if (string.Equals(parameters.FaultStage, "after_apply_before_commit", StringComparison.Ordinal))
                {
                    r0Reason = "FAULT_INJECTED";
                    transaction.Abort();
                }
                else
                {
                    try
                    {
                        provisional = _semantic.BuildProvisionalFromAffected(
                            document,
                            parameters.RuntimeDocumentId,
                            parameters.DocumentPid,
                            before,
                            transaction,
                            affectedObjectIds,
                            BridgeConstants.MaxBatchSemanticEntities
                        );
                        NativeMutationValidator.ValidateBatchTransform(
                            parameters.SemanticPids,
                            parameters,
                            before,
                            provisional
                        );
                    }
                    catch (BridgeServiceException exc) when (
                        exc.Code is "PROVISIONAL_VALIDATION_FAILED" or "SNAPSHOT_TOO_LARGE")
                    {
                        r0Reason = "PROVISIONAL_VALIDATION_FAILED";
                    }

                    if (r0Reason is not null)
                    {
                        transaction.Abort();
                    }
                    else
                    {
                        transaction.Commit();
                        committed = true;
                    }
                }
            }

            if (!committed)
            {
                Dictionary<string, object?> afterAbort = _semantic.Extract(
                    document,
                    parameters.RuntimeDocumentId,
                    parameters.DocumentPid,
                    BridgeConstants.MaxBatchSemanticEntities
                );
                string restoreFp = Fingerprint(afterAbort);
                bool restored = string.Equals(restoreFp, parentFp, StringComparison.Ordinal);
                if (restored && ownsCheckpoint)
                {
                    _checkpoints.Finalize(checkpoint);
                }
                return BatchRollbackResult(
                    operation,
                    parameters.DocumentPid,
                    parameters.SemanticPids,
                    checkpoint,
                    "R0_ABORT",
                    r0Reason ?? "EXECUTION_FAILED",
                    parentFp,
                    restoreFp,
                    restored,
                    parameters.RuntimeDocumentId
                );
            }

            if (provisional is null)
            {
                throw new BridgeServiceException(
                    "COMMIT_INTEGRITY_FAIL",
                    "provisional semantic state is unavailable after batch transform commit"
                );
            }
            string provisionalFp = Fingerprint(provisional);
            if (ownsCheckpoint)
            {
                _batchRecoveryContexts[checkpoint.CheckpointId] = new NativeBatchRecoveryContext(
                    checkpoint,
                    parameters.SemanticPids,
                    affectedObjectIds.ToArray(),
                    predecessorEntities.ToArray()
                );
            }

            Dictionary<string, object?> persisted = _semantic.Extract(
                document,
                parameters.RuntimeDocumentId,
                parameters.DocumentPid,
                BridgeConstants.MaxBatchSemanticEntities
            );
            string persistedFp = Fingerprint(persisted);
            bool integrityOk = string.Equals(provisionalFp, persistedFp, StringComparison.Ordinal);
            return new Dictionary<string, object?>
            {
                ["schema_version"] = 1,
                ["operation"] = operation,
                ["outcome"] = integrityOk ? "COMMITTED_VERIFIED" : "COMMIT_INTEGRITY_FAIL",
                ["document_pid"] = parameters.DocumentPid,
                ["pre_document_fp"] = parentFp,
                ["provisional_document_fp"] = provisionalFp,
                ["post_document_fp"] = persistedFp,
                ["affected_semantic_pids"] = parameters.SemanticPids,
                ["recovery_checkpoint"] = CheckpointPayload(checkpoint),
            };
        }
        catch
        {
            if (!committed && ownsCheckpoint)
            {
                try { _checkpoints.Finalize(checkpoint); }
                catch { }
            }
            throw;
        }
    }

    internal object ResolveRecovery(Guid requestId, RecoveryResolveParams parameters)
    {
        NativeCheckpoint checkpoint = _checkpoints.Require(
            parameters.CheckpointId,
            parameters.DocumentPid,
            parameters.CheckpointArtifactFp,
            parameters.ExpectedRestoreFp
        );
        if (string.Equals(parameters.Strategy, "R1_COMPENSATE", StringComparison.Ordinal))
        {
            EnsureNoR2InProgress(checkpoint.CheckpointId);
            Document document = _documents.ResolveDocument(
                parameters.RuntimeDocumentId,
                parameters.DocumentPid
            );
            return ResolveR1(document, parameters, checkpoint);
        }

        if (_r2RecoveryStates.TryGetValue(requestId, out NativeR2RecoveryState? state))
        {
            if (!string.Equals(
                state.Checkpoint.CheckpointId,
                checkpoint.CheckpointId,
                StringComparison.Ordinal
            ))
            {
                throw new BridgeServiceException(
                    "RECOVERY_BINDING_MISMATCH",
                    "deferred recovery request no longer matches its checkpoint"
                );
            }
            return ContinueR2(state);
        }
        EnsureNoR2InProgress(checkpoint.CheckpointId);
        Document originalDocument = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return BeginR2(requestId, originalDocument, parameters, checkpoint);
    }

    internal void CancelRecoveryRequest(Guid requestId)
    {
        if (!_r2RecoveryStates.Remove(requestId, out NativeR2RecoveryState? state))
        {
            return;
        }
        // Never close the restored document on timeout/cancellation: its activation may still be
        // pending. The persisted checkpoint remains authoritative and the operator can reconcile.
        TryCloseInactiveDocument(state.CheckpointDocument);
    }

    internal object FinalizeRecovery(Document document, RecoveryFinalizeParams parameters)
    {
        NativeCheckpoint checkpoint = _checkpoints.Require(
            parameters.CheckpointId,
            parameters.DocumentPid,
            parameters.CheckpointArtifactFp,
            null
        );
        EnsureNoR2InProgress(checkpoint.CheckpointId);
        using DocumentLock documentLock = document.LockDocument();
        string actualFp = Fingerprint(_semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid,
            RecoverySemanticLimit(checkpoint)
        ));
        if (!string.Equals(actualFp, parameters.AcceptedPostFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "RECOVERY_FINALIZE_STATE_MISMATCH",
                "checkpoint cannot be finalized because current state is not the accepted post-state"
            );
        }
        _checkpoints.Finalize(checkpoint);
        _recoveryContexts.Remove(checkpoint.CheckpointId);
        _batchRecoveryContexts.Remove(checkpoint.CheckpointId);
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["status"] = "FINALIZED",
            ["checkpoint_id"] = checkpoint.CheckpointId,
            ["document_pid"] = checkpoint.DocumentPid,
            ["accepted_post_fp"] = actualFp,
        };
    }

    internal object ListRecoveries()
    {
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["recoveries"] = _checkpoints.List().Select(checkpoint => new Dictionary<string, object?>
            {
                ["checkpoint_id"] = checkpoint.CheckpointId,
                ["document_pid"] = checkpoint.DocumentPid,
                ["expected_restore_fp"] = checkpoint.ExpectedParentFp,
                ["checkpoint_artifact_fp"] = checkpoint.ArtifactFp,
                ["operation"] = checkpoint.Operation,
                ["r1_context_available"] = _recoveryContexts.ContainsKey(checkpoint.CheckpointId)
                    || _batchRecoveryContexts.ContainsKey(checkpoint.CheckpointId),
                ["r2_in_progress"] = _r2RecoveryStates.Values.Any(
                    state => string.Equals(
                        state.Checkpoint.CheckpointId,
                        checkpoint.CheckpointId,
                        StringComparison.Ordinal
                    )
                ),
            }).ToArray(),
        };
    }

    private object ResolveR1(
        Document document,
        RecoveryResolveParams parameters,
        NativeCheckpoint checkpoint
    )
    {
        if (_batchRecoveryContexts.TryGetValue(
            checkpoint.CheckpointId,
            out NativeBatchRecoveryContext? batchContext
        ))
        {
            return ResolveBatchR1(document, parameters, checkpoint, batchContext);
        }
        if (!_recoveryContexts.TryGetValue(checkpoint.CheckpointId, out NativeRecoveryContext? context))
        {
            string currentFp = Fingerprint(_semantic.Extract(
                document,
                parameters.RuntimeDocumentId,
                parameters.DocumentPid,
                RecoverySemanticLimit(checkpoint)
            ));
            return RollbackResult(
                checkpoint.Operation,
                checkpoint.DocumentPid,
                string.Empty,
                checkpoint,
                "R1_COMPENSATE",
                "R1_CONTEXT_UNAVAILABLE",
                parameters.ExpectedRestoreFp,
                currentFp,
                false,
                parameters.RuntimeDocumentId
            );
        }

        using DocumentLock documentLock = document.LockDocument();
        try
        {
            using Transaction transaction = document.Database.TransactionManager.StartTransaction();
            if (string.Equals(context.Operation, "metadata.set", StringComparison.Ordinal))
            {
                if (context.PredecessorEntity is null
                    || context.PredecessorEntity["metadata"] is not Dictionary<string, object?> metadata)
                {
                    throw new BridgeServiceException(
                        "R1_UNAVAILABLE",
                        "metadata predecessor state is unavailable"
                    );
                }
                DBObject target = transaction.GetObject(
                    context.AffectedObjectId,
                    OpenMode.ForWrite,
                    false
                );
                NativeMetadataStore.ReplaceAll(target, transaction, metadata);
            }
            else
            {
                string verb = context.Operation.Split('.')[1];
                if (verb == "create")
            {
                DBObject created = transaction.GetObject(
                    context.AffectedObjectId,
                    OpenMode.ForWrite,
                    false
                );
                created.Erase(true);
            }
            else if (verb == "update")
            {
                if (context.PredecessorEntity is null)
                {
                    throw new BridgeServiceException("R1_UNAVAILABLE", "update predecessor state is unavailable");
                }
                Entity target = (Entity)transaction.GetObject(
                    context.AffectedObjectId,
                    OpenMode.ForWrite,
                    false
                );
                RestoreGeometry(target, context.PredecessorEntity);
            }
            else if (verb == "delete")
            {
                DBObject deleted = transaction.GetObject(
                    context.AffectedObjectId,
                    OpenMode.ForWrite,
                    true
                );
                deleted.Erase(false);
            }
                else
                {
                    throw new BridgeServiceException("R1_UNAVAILABLE", "mutation family has no deterministic inverse");
                }
            }
            transaction.Commit();
        }
        catch (Exception exc) when (exc is not BridgeServiceException)
        {
            throw new BridgeServiceException(
                "R1_COMPENSATION_FAILED",
                $"deterministic inverse could not execute: {exc.GetType().Name}"
            );
        }

        string restoredFp = Fingerprint(_semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid,
            RecoverySemanticLimit(checkpoint)
        ));
        bool restored = string.Equals(
            restoredFp,
            parameters.ExpectedRestoreFp,
            StringComparison.Ordinal
        );
        if (restored)
        {
            _checkpoints.Finalize(checkpoint);
            _recoveryContexts.Remove(checkpoint.CheckpointId);
        }
        return RollbackResult(
            checkpoint.Operation,
            checkpoint.DocumentPid,
            context.AffectedPid,
            checkpoint,
            "R1_COMPENSATE",
            "COMMIT_INTEGRITY_FAIL",
            parameters.ExpectedRestoreFp,
            restoredFp,
            restored,
            parameters.RuntimeDocumentId
        );
    }

    private object ResolveBatchR1(
        Document document,
        RecoveryResolveParams parameters,
        NativeCheckpoint checkpoint,
        NativeBatchRecoveryContext context
    )
    {
        using DocumentLock documentLock = document.LockDocument();
        try
        {
            using Transaction transaction = document.Database.TransactionManager.StartTransaction();
            if (context.PredecessorEntities is null)
            {
                foreach (ObjectId objectId in context.AffectedObjectIds)
                {
                    DBObject created = transaction.GetObject(objectId, OpenMode.ForWrite, false);
                    created.Erase(true);
                }
            }
            else
            {
                if (context.PredecessorEntities.Length != context.AffectedObjectIds.Length)
                {
                    throw new BridgeServiceException(
                        "R1_UNAVAILABLE",
                        "batch transform predecessor state is incomplete"
                    );
                }
                for (int index = 0; index < context.AffectedObjectIds.Length; index++)
                {
                    Entity target = (Entity)transaction.GetObject(
                        context.AffectedObjectIds[index],
                        OpenMode.ForWrite,
                        false
                    );
                    RestoreGeometry(target, context.PredecessorEntities[index]);
                }
            }
            transaction.Commit();
        }
        catch (Exception exc) when (exc is not BridgeServiceException)
        {
            throw new BridgeServiceException(
                "R1_COMPENSATION_FAILED",
                $"batch inverse could not execute: {exc.GetType().Name}"
            );
        }

        string restoredFp = Fingerprint(_semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid,
            BridgeConstants.MaxBatchSemanticEntities
        ));
        bool restored = string.Equals(
            restoredFp,
            parameters.ExpectedRestoreFp,
            StringComparison.Ordinal
        );
        if (restored)
        {
            _checkpoints.Finalize(checkpoint);
            _batchRecoveryContexts.Remove(checkpoint.CheckpointId);
        }
        return BatchRollbackResult(
            checkpoint.Operation,
            checkpoint.DocumentPid,
            context.AffectedPids,
            checkpoint,
            "R1_COMPENSATE",
            "COMMIT_INTEGRITY_FAIL",
            parameters.ExpectedRestoreFp,
            restoredFp,
            restored,
            parameters.RuntimeDocumentId
        );
    }

    private object BeginR2(
        Guid requestId,
        Document originalDocument,
        RecoveryResolveParams parameters,
        NativeCheckpoint checkpoint
    )
    {
        Document? checkpointDocument = null;
        try
        {
            if (!string.Equals(
                NativeCheckpointStore.FingerprintFile(checkpoint.CheckpointPath),
                checkpoint.ArtifactFp,
                StringComparison.Ordinal
            ))
            {
                throw new BridgeServiceException(
                    "RECOVERY_ARTIFACT_MISMATCH",
                    "checkpoint artifact changed before R2 restore"
                );
            }

            string originalPath = Path.GetFullPath(checkpoint.OriginalPath);
            string boundPath = Path.GetFullPath(originalDocument.Database.Filename);
            if (!string.Equals(originalPath, boundPath, StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeServiceException(
                    "RECOVERY_BINDING_MISMATCH",
                    "bound runtime document path does not match the recovery manifest"
                );
            }

            checkpointDocument = AcApplication.DocumentManager.Open(
                checkpoint.CheckpointPath,
                true
            );
            NativeR2RecoveryState state = new(
                requestId,
                checkpoint,
                parameters,
                originalDocument,
                checkpointDocument
            );
            _r2RecoveryStates.Add(requestId, state);
            AcApplication.DocumentManager.MdiActiveDocument = checkpointDocument;
            return DeferredBridgeResult.Instance;
        }
        catch (Exception exc)
        {
            _r2RecoveryStates.Remove(requestId);
            TryCloseInactiveDocument(checkpointDocument);
            return R2Failure(checkpoint, parameters, exc, null, null, "BEGIN");
        }
    }

    private object ContinueR2(NativeR2RecoveryState state)
    {
        NativeCheckpoint checkpoint = state.Checkpoint;
        RecoveryResolveParams parameters = state.Parameters;
        string? actualRestoreFp = null;
        Guid? restoredRuntimeId = null;
        try
        {
            Document? active = AcApplication.DocumentManager.MdiActiveDocument;
            if (state.Phase == NativeR2RecoveryPhase.WaitCheckpointActive)
            {
                if (!ReferenceEquals(active, state.CheckpointDocument))
                {
                    throw new BridgeServiceException(
                        "RECOVERY_ACTIVATION_FAILED",
                        "checkpoint document did not become active before original close"
                    );
                }
                if (ReferenceEquals(active, state.OriginalDocument))
                {
                    throw new BridgeServiceException(
                        "RECOVERY_ACTIVATION_FAILED",
                        "original document is still active and cannot be closed safely"
                    );
                }

                state.OriginalDocument.CloseAndDiscard();
                if (!ReferenceEquals(
                    AcApplication.DocumentManager.MdiActiveDocument,
                    state.CheckpointDocument
                ))
                {
                    throw new BridgeServiceException(
                        "RECOVERY_ACTIVATION_FAILED",
                        "checkpoint document lost activation after original close"
                    );
                }

                File.Copy(checkpoint.CheckpointPath, checkpoint.OriginalPath, true);
                Document restoredDocument = AcApplication.DocumentManager.Open(
                    checkpoint.OriginalPath,
                    false
                );
                state.RestoredDocument = restoredDocument;
                state.Phase = NativeR2RecoveryPhase.WaitRestoredActive;
                AcApplication.DocumentManager.MdiActiveDocument = restoredDocument;
                return DeferredBridgeResult.Instance;
            }

            Document restored = state.RestoredDocument
                ?? throw new BridgeServiceException(
                    "RECOVERY_STATE_INVALID",
                    "R2 restore lost its reopened document binding"
                );
            if (!ReferenceEquals(active, restored))
            {
                throw new BridgeServiceException(
                    "RECOVERY_ACTIVATION_FAILED",
                    "restored original document did not become active before checkpoint close"
                );
            }
            if (ReferenceEquals(active, state.CheckpointDocument))
            {
                throw new BridgeServiceException(
                    "RECOVERY_ACTIVATION_FAILED",
                    "checkpoint document is still active and cannot be closed safely"
                );
            }

            state.CheckpointDocument.CloseAndDiscard();
            if (!ReferenceEquals(AcApplication.DocumentManager.MdiActiveDocument, restored))
            {
                throw new BridgeServiceException(
                    "RECOVERY_ACTIVATION_FAILED",
                    "restored original lost activation after checkpoint close"
                );
            }

            restoredRuntimeId = _documents.RuntimeIdFor(restored);
            string restoredDocumentPid = DocumentPidReader.Read(restored.Database)
                ?? throw new BridgeServiceException(
                    "DOCUMENT_PID_MISSING",
                    "restored checkpoint is missing document lineage PID"
                );
            if (!string.Equals(restoredDocumentPid, parameters.DocumentPid, StringComparison.Ordinal))
            {
                throw new BridgeServiceException(
                    "RECOVERY_BINDING_MISMATCH",
                    "restored checkpoint document PID does not match recovery binding"
                );
            }

            actualRestoreFp = Fingerprint(_semantic.Extract(
                restored,
                restoredRuntimeId.Value,
                parameters.DocumentPid,
                RecoverySemanticLimit(checkpoint)
            ));
            bool restoredExact = string.Equals(
                actualRestoreFp,
                parameters.ExpectedRestoreFp,
                StringComparison.Ordinal
            );
            _recoveryContexts.TryGetValue(
                checkpoint.CheckpointId,
                out NativeRecoveryContext? singleContext
            );
            _batchRecoveryContexts.TryGetValue(
                checkpoint.CheckpointId,
                out NativeBatchRecoveryContext? batchContext
            );
            if (restoredExact)
            {
                _checkpoints.Finalize(checkpoint);
                _recoveryContexts.Remove(checkpoint.CheckpointId);
                _batchRecoveryContexts.Remove(checkpoint.CheckpointId);
            }
            _r2RecoveryStates.Remove(state.RequestId);
            if (batchContext is not null)
            {
                return BatchRollbackResult(
                    checkpoint.Operation,
                    checkpoint.DocumentPid,
                    batchContext.AffectedPids,
                    checkpoint,
                    "R2_CHECKPOINT_RESTORE",
                    "COMMIT_INTEGRITY_FAIL",
                    parameters.ExpectedRestoreFp,
                    actualRestoreFp,
                    restoredExact,
                    restoredRuntimeId.Value
                );
            }
            return RollbackResult(
                checkpoint.Operation,
                checkpoint.DocumentPid,
                singleContext?.AffectedPid ?? string.Empty,
                checkpoint,
                "R2_CHECKPOINT_RESTORE",
                "COMMIT_INTEGRITY_FAIL",
                parameters.ExpectedRestoreFp,
                actualRestoreFp,
                restoredExact,
                restoredRuntimeId.Value
            );
        }
        catch (Exception exc)
        {
            _r2RecoveryStates.Remove(state.RequestId);
            TryCloseInactiveDocument(state.CheckpointDocument);
            return R2Failure(
                checkpoint,
                parameters,
                exc,
                actualRestoreFp,
                restoredRuntimeId,
                state.Phase.ToString()
            );
        }
    }

    private static object R2Failure(
        NativeCheckpoint checkpoint,
        RecoveryResolveParams parameters,
        Exception exc,
        string? actualRestoreFp,
        Guid? restoredRuntimeId,
        string phase
    )
    {
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["operation"] = checkpoint.Operation,
            ["outcome"] = "ROLLBACK_FAILED",
            ["document_pid"] = checkpoint.DocumentPid,
            ["runtime_document_id"] = restoredRuntimeId?.ToString("D"),
            ["checkpoint_id"] = checkpoint.CheckpointId,
            ["rollback"] = new Dictionary<string, object?>
            {
                ["rollback_id"] = "rb:" + Guid.NewGuid().ToString("D"),
                ["reason"] = "COMMIT_INTEGRITY_FAIL",
                ["strategy"] = "R2_CHECKPOINT_RESTORE",
                ["phase"] = phase,
                ["expected_restore_fp"] = parameters.ExpectedRestoreFp,
                ["actual_restore_fp"] = actualRestoreFp,
                ["status"] = "ROLLBACK_FAILED",
                ["error_type"] = exc.GetType().Name,
                ["error_code"] = exc is BridgeServiceException bridgeError
                    ? bridgeError.Code
                    : null,
                ["error_message"] = exc is BridgeServiceException
                    ? exc.Message
                    : "R2 checkpoint restore failed",
            },
        };
    }

    private void EnsureNoR2InProgress(string checkpointId)
    {
        if (_r2RecoveryStates.Values.Any(
            state => string.Equals(
                state.Checkpoint.CheckpointId,
                checkpointId,
                StringComparison.Ordinal
            )
        ))
        {
            throw new BridgeServiceException(
                "RECOVERY_IN_PROGRESS",
                "checkpoint already has an activation-safe R2 restore in progress"
            );
        }
    }

    private static void TryCloseInactiveDocument(Document? document)
    {
        if (document is null
            || ReferenceEquals(document, AcApplication.DocumentManager.MdiActiveDocument))
        {
            return;
        }
        try
        {
            document.CloseAndDiscard();
        }
        catch
        {
            // Cleanup is best-effort only. The persisted checkpoint remains authoritative.
        }
    }

    private (NativeCheckpoint Checkpoint, bool OwnsCheckpoint) AcquireBatchCheckpoint(
        Document document,
        string documentPid,
        string parentFp,
        string operation,
        Guid requestId,
        LogicalBatchBinding? logicalTransaction
    )
    {
        if (logicalTransaction is null)
        {
            return (
                _checkpoints.Create(document, documentPid, parentFp, operation, requestId),
                true
            );
        }
        NativeCheckpoint checkpoint = _checkpoints.Require(
            logicalTransaction.CheckpointId,
            documentPid,
            logicalTransaction.CheckpointArtifactFp,
            logicalTransaction.ExpectedRestoreFp
        );
        if (!string.Equals(checkpoint.Operation, "logical.batch", StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "RECOVERY_BINDING_MISMATCH",
                "logical_transaction must reference a logical.batch predecessor checkpoint"
            );
        }
        EnsureNoR2InProgress(checkpoint.CheckpointId);
        return (checkpoint, false);
    }

    private static int RecoverySemanticLimit(NativeCheckpoint checkpoint) =>
        checkpoint.Operation.StartsWith("entity.batch.", StringComparison.Ordinal)
            || string.Equals(checkpoint.Operation, "logical.batch", StringComparison.Ordinal)
            || string.Equals(checkpoint.Operation, "metadata.set", StringComparison.Ordinal)
            ? BridgeConstants.MaxBatchSemanticEntities
            : BridgeConstants.MaxSnapshotEntities;

    private static string ApplyMutation(
        Database database,
        Transaction transaction,
        string operation,
        EntityMutationParams parameters
    )
    {
        return operation switch
        {
            "entity.create.line" => CreateLine(database, transaction, parameters),
            "entity.update.line" => UpdateLine(database, transaction, parameters),
            "entity.delete.line" => DeleteLine(database, transaction, parameters),
            "entity.create.circle" => CreateCircle(database, transaction, parameters),
            "entity.update.circle" => UpdateCircle(database, transaction, parameters),
            "entity.delete.circle" => DeleteCircle(database, transaction, parameters),
            "entity.create.arc" => CreateArc(database, transaction, parameters),
            "entity.update.arc" => UpdateArc(database, transaction, parameters),
            "entity.delete.arc" => DeleteArc(database, transaction, parameters),
            "entity.create.lwpolyline" => CreatePolyline(database, transaction, parameters),
            "entity.update.lwpolyline" => UpdatePolyline(database, transaction, parameters),
            "entity.delete.lwpolyline" => DeletePolyline(database, transaction, parameters),
            _ => throw new BridgeServiceException("UNSUPPORTED_OPERATION", "mutation operation is not enabled"),
        };
    }

    private static Matrix3d BatchTransformMatrix(BatchTransformSpec transform)
    {
        return transform.Kind switch
        {
            "translate" => Matrix3d.Displacement(new Vector3d(
                transform.Delta![0],
                transform.Delta[1],
                0.0
            )),
            "rotate_z" => Matrix3d.Rotation(
                transform.Angle!.Value,
                Vector3d.ZAxis,
                Point(transform.Center, "center")
            ),
            "scale_uniform" => Matrix3d.Scaling(
                transform.Factor!.Value,
                Point(transform.Center, "center")
            ),
            _ => throw new BridgeServiceException(
                "UNSUPPORTED_OPERATION",
                "batch transform kind is not enabled"
            ),
        };
    }

    private static void EnsureBatchTransformTarget(
        Entity target,
        Dictionary<string, object?> predecessor
    )
    {
        string type = Convert.ToString(predecessor["entity_type"]) ?? string.Empty;
        switch (target)
        {
            case Line when type == "LINE":
                return;
            case Circle circle when type == "CIRCLE":
                if (!SameVector(circle.Normal, Vector3d.ZAxis))
                {
                    throw new BridgeServiceException(
                        "UNSUPPORTED_TARGET_GEOMETRY",
                        "batch transform supports only +Z planar circles"
                    );
                }
                return;
            case Arc arc when type == "ARC":
                if (!SameVector(arc.Normal, Vector3d.ZAxis))
                {
                    throw new BridgeServiceException(
                        "UNSUPPORTED_TARGET_GEOMETRY",
                        "batch transform supports only +Z planar arcs"
                    );
                }
                return;
            case Polyline polyline when type == "LWPOLYLINE":
                EnsureSimplePolyline(polyline);
                return;
            default:
                throw new BridgeServiceException(
                    "UNSUPPORTED_TARGET_GEOMETRY",
                    "batch transform supports only LINE/CIRCLE/ARC/simple-LWPOLYLINE targets"
                );
        }
    }

    private static NativeBatchCreatedEntity ApplyBatchCreateEntity(
        Database database,
        Transaction transaction,
        BatchCreateEntitySpec spec
    )
    {
        BlockTableRecord space = CurrentSpace(database, transaction);
        return spec.Kind switch
        {
            "line" => AppendBatchWithPid(
                space,
                new Line(Point(spec.Start, "start"), Point(spec.End, "end"))
                {
                    LayerId = database.Clayer,
                },
                transaction
            ),
            "circle" => AppendBatchWithPid(
                space,
                new Circle(
                    Point(spec.Center, "center"),
                    Vector3d.ZAxis,
                    Positive(spec.Radius, "radius")
                )
                {
                    LayerId = database.Clayer,
                },
                transaction
            ),
            "arc" => AppendBatchWithPid(
                space,
                new Arc(
                    Point(spec.Center, "center"),
                    Vector3d.ZAxis,
                    Positive(spec.Radius, "radius"),
                    Angle(spec.StartAngle, "start_angle"),
                    Angle(spec.EndAngle, "end_angle")
                )
                {
                    LayerId = database.Clayer,
                },
                transaction
            ),
            "lwpolyline" => CreateBatchPolyline(space, transaction, spec),
            _ => throw new BridgeServiceException(
                "UNSUPPORTED_OPERATION",
                "batch create family is not enabled"
            ),
        };
    }

    private static NativeBatchCreatedEntity AppendBatchWithPid(
        BlockTableRecord space,
        Entity entity,
        Transaction transaction
    )
    {
        ObjectId objectId = space.AppendEntity(entity);
        transaction.AddNewlyCreatedDBObject(entity, true);
        string pid = EntityPidStore.NewEntityPid();
        EntityPidStore.Set(entity, pid, transaction);
        return new NativeBatchCreatedEntity(pid, objectId);
    }

    private static NativeBatchCreatedEntity CreateBatchPolyline(
        BlockTableRecord space,
        Transaction transaction,
        BatchCreateEntitySpec spec
    )
    {
        double[][] points = Points(spec.Points);
        Polyline polyline = new(points.Length)
        {
            LayerId = space.Database.Clayer,
            Closed = Closed(spec.Closed),
            Elevation = 0.0,
            Normal = Vector3d.ZAxis,
        };
        for (int index = 0; index < points.Length; index++)
        {
            polyline.AddVertexAt(
                index,
                new Point2d(points[index][0], points[index][1]),
                0.0,
                0.0,
                0.0
            );
        }
        return AppendBatchWithPid(space, polyline, transaction);
    }

    private static string CreateLine(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        Line line = new(Point(parameters.Start, "start"), Point(parameters.End, "end")) { LayerId = database.Clayer };
        return AppendWithPid(CurrentSpace(database, transaction), line, transaction);
    }

    private static string UpdateLine(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Line line) throw TypeMismatch("LINE");
        line.StartPoint = Point(parameters.Start, "start"); line.EndPoint = Point(parameters.End, "end"); return pid;
    }

    private static string DeleteLine(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        string pid = RequireTargetPid(parameters); ObjectId id = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(id, OpenMode.ForWrite, false) is not Line line) throw TypeMismatch("LINE");
        line.Erase(true); return pid;
    }

    private static string CreateCircle(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        Circle circle = new(Point(parameters.Center, "center"), Vector3d.ZAxis, Positive(parameters.Radius, "radius")) { LayerId = database.Clayer };
        return AppendWithPid(CurrentSpace(database, transaction), circle, transaction);
    }

    private static string UpdateCircle(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        string pid = RequireTargetPid(parameters); ObjectId id = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(id, OpenMode.ForWrite, false) is not Circle circle) throw TypeMismatch("CIRCLE");
        circle.Center = Point(parameters.Center, "center"); circle.Radius = Positive(parameters.Radius, "radius"); return pid;
    }

    private static string DeleteCircle(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        string pid = RequireTargetPid(parameters); ObjectId id = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(id, OpenMode.ForWrite, false) is not Circle circle) throw TypeMismatch("CIRCLE");
        circle.Erase(true); return pid;
    }

    private static string CreateArc(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        Arc arc = new(Point(parameters.Center, "center"), Vector3d.ZAxis, Positive(parameters.Radius, "radius"), Angle(parameters.StartAngle, "start_angle"), Angle(parameters.EndAngle, "end_angle")) { LayerId = database.Clayer };
        return AppendWithPid(CurrentSpace(database, transaction), arc, transaction);
    }

    private static string UpdateArc(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        string pid = RequireTargetPid(parameters); ObjectId id = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(id, OpenMode.ForWrite, false) is not Arc arc) throw TypeMismatch("ARC");
        arc.Center = Point(parameters.Center, "center"); arc.Radius = Positive(parameters.Radius, "radius"); arc.StartAngle = Angle(parameters.StartAngle, "start_angle"); arc.EndAngle = Angle(parameters.EndAngle, "end_angle"); return pid;
    }

    private static string DeleteArc(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        string pid = RequireTargetPid(parameters); ObjectId id = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(id, OpenMode.ForWrite, false) is not Arc arc) throw TypeMismatch("ARC");
        arc.Erase(true); return pid;
    }

    private static string CreatePolyline(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        double[][] points = Points(parameters.Points); bool closed = Closed(parameters.Closed);
        Polyline polyline = new(points.Length) { LayerId = database.Clayer, Closed = closed, Elevation = 0.0, Normal = Vector3d.ZAxis };
        for (int i = 0; i < points.Length; i++) polyline.AddVertexAt(i, new Point2d(points[i][0], points[i][1]), 0.0, 0.0, 0.0);
        return AppendWithPid(CurrentSpace(database, transaction), polyline, transaction);
    }

    private static string UpdatePolyline(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        string pid = RequireTargetPid(parameters); ObjectId id = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(id, OpenMode.ForWrite, false) is not Polyline polyline) throw TypeMismatch("LWPOLYLINE");
        EnsureSimplePolyline(polyline); double[][] points = Points(parameters.Points); polyline.Closed = false;
        int existingCount = polyline.NumberOfVertices; int sharedCount = Math.Min(existingCount, points.Length);
        for (int i = 0; i < sharedCount; i++) polyline.SetPointAt(i, new Point2d(points[i][0], points[i][1]));
        for (int i = existingCount - 1; i >= points.Length; i--) polyline.RemoveVertexAt(i);
        for (int i = existingCount; i < points.Length; i++) polyline.AddVertexAt(i, new Point2d(points[i][0], points[i][1]), 0.0, 0.0, 0.0);
        polyline.Closed = Closed(parameters.Closed); return pid;
    }

    private static string DeletePolyline(Database database, Transaction transaction, EntityMutationParams parameters)
    {
        string pid = RequireTargetPid(parameters); ObjectId id = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(id, OpenMode.ForWrite, false) is not Polyline polyline) throw TypeMismatch("LWPOLYLINE");
        polyline.Erase(true); return pid;
    }

    private static void AddStrayEntity(Database database, Transaction transaction)
    {
        Line stray = new(new Point3d(987654, 987654, 0), new Point3d(987655, 987654, 0)) { LayerId = database.Clayer };
        _ = AppendWithPid(CurrentSpace(database, transaction), stray, transaction);
    }

    private static void CorruptAffectedEntity(Database database, ObjectId objectId)
    {
        using Transaction transaction = database.TransactionManager.StartTransaction();
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Entity entity)
        {
            throw new BridgeServiceException("FAULT_INJECTION_FAILED", "affected entity could not be reopened");
        }
        switch (entity)
        {
            case Line line:
                line.EndPoint = line.EndPoint + new Vector3d(1, 2, 0);
                break;
            case Circle circle:
                circle.Radius += 1.0;
                break;
            case Arc arc:
                arc.Radius += 1.0;
                break;
            case Polyline polyline:
                Point2d first = polyline.GetPoint2dAt(0);
                polyline.SetPointAt(0, new Point2d(first.X + 1.0, first.Y + 2.0));
                break;
            default:
                throw new BridgeServiceException("FAULT_INJECTION_FAILED", "affected family has no bounded N7 corruption probe");
        }
        transaction.Commit();
    }

    private static void RestoreGeometry(Entity target, Dictionary<string, object?> predecessor)
    {
        if (predecessor["geometry"] is not Dictionary<string, object?> geometry)
        {
            throw new BridgeServiceException("R1_UNAVAILABLE", "predecessor geometry is unavailable");
        }
        switch (target)
        {
            case Line line when predecessor["entity_type"] as string == "LINE":
                line.StartPoint = PointValue(geometry["start"], "start");
                line.EndPoint = PointValue(geometry["end"], "end");
                return;
            case Circle circle when predecessor["entity_type"] as string == "CIRCLE":
                circle.Center = PointValue(geometry["center"], "center");
                circle.Radius = Convert.ToDouble(geometry["radius"]);
                return;
            case Arc arc when predecessor["entity_type"] as string == "ARC":
                arc.Center = PointValue(geometry["center"], "center");
                arc.Radius = Convert.ToDouble(geometry["radius"]);
                arc.StartAngle = Convert.ToDouble(geometry["start_angle"]);
                arc.EndAngle = Convert.ToDouble(geometry["end_angle"]);
                return;
            case Polyline polyline when predecessor["entity_type"] as string == "LWPOLYLINE":
                EnsureSimplePolyline(polyline);
                if (geometry["vertices"] is not List<Dictionary<string, object?>> vertices)
                    throw new BridgeServiceException("R1_UNAVAILABLE", "polyline predecessor vertices are unavailable");
                double[][] points = vertices.Select(vertex => (double[])vertex["point"]!).ToArray();
                polyline.Closed = false;
                int existing = polyline.NumberOfVertices; int shared = Math.Min(existing, points.Length);
                for (int i = 0; i < shared; i++) polyline.SetPointAt(i, new Point2d(points[i][0], points[i][1]));
                for (int i = existing - 1; i >= points.Length; i--) polyline.RemoveVertexAt(i);
                for (int i = existing; i < points.Length; i++) polyline.AddVertexAt(i, new Point2d(points[i][0], points[i][1]), 0.0, 0.0, 0.0);
                polyline.Closed = (bool)geometry["closed"]!;
                return;
            default:
                throw new BridgeServiceException("R1_UNAVAILABLE", "predecessor type does not match recovery target");
        }
    }

    private static Dictionary<string, object?> BatchRollbackResult(
        string operation,
        string documentPid,
        IEnumerable<string> affectedPids,
        NativeCheckpoint checkpoint,
        string strategy,
        string reason,
        string expectedRestoreFp,
        string? actualRestoreFp,
        bool restored,
        Guid runtimeDocumentId
    )
    {
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["operation"] = operation,
            ["outcome"] = restored ? "ROLLED_BACK_VERIFIED" : "ROLLBACK_FAILED",
            ["document_pid"] = documentPid,
            ["runtime_document_id"] = runtimeDocumentId.ToString("D"),
            ["pre_document_fp"] = expectedRestoreFp,
            ["post_document_fp"] = actualRestoreFp,
            ["checkpoint_id"] = checkpoint.CheckpointId,
            ["affected_semantic_pids"] = affectedPids.ToArray(),
            ["rollback"] = new Dictionary<string, object?>
            {
                ["rollback_id"] = "rb:" + Guid.NewGuid().ToString("D"),
                ["reason"] = reason,
                ["strategy"] = strategy,
                ["expected_restore_fp"] = expectedRestoreFp,
                ["actual_restore_fp"] = actualRestoreFp,
                ["status"] = restored ? "ROLLED_BACK_VERIFIED" : "ROLLBACK_FAILED",
            },
        };
    }

    private static Dictionary<string, object?> RollbackResult(
        string operation,
        string documentPid,
        string affectedPid,
        NativeCheckpoint checkpoint,
        string strategy,
        string reason,
        string expectedRestoreFp,
        string? actualRestoreFp,
        bool restored,
        Guid runtimeDocumentId
    )
    {
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["operation"] = operation,
            ["outcome"] = restored ? "ROLLED_BACK_VERIFIED" : "ROLLBACK_FAILED",
            ["document_pid"] = documentPid,
            ["runtime_document_id"] = runtimeDocumentId.ToString("D"),
            ["pre_document_fp"] = expectedRestoreFp,
            ["post_document_fp"] = actualRestoreFp,
            ["checkpoint_id"] = checkpoint.CheckpointId,
            ["affected_semantic_pid"] = affectedPid,
            ["rollback"] = new Dictionary<string, object?>
            {
                ["rollback_id"] = "rb:" + Guid.NewGuid().ToString("D"),
                ["reason"] = reason,
                ["strategy"] = strategy,
                ["expected_restore_fp"] = expectedRestoreFp,
                ["actual_restore_fp"] = actualRestoreFp,
                ["status"] = restored ? "ROLLED_BACK_VERIFIED" : "ROLLBACK_FAILED",
            },
        };
    }

    private static Dictionary<string, object?> CheckpointPayload(NativeCheckpoint checkpoint) => new()
    {
        ["checkpoint_id"] = checkpoint.CheckpointId,
        ["checkpoint_artifact_fp"] = checkpoint.ArtifactFp,
        ["expected_restore_fp"] = checkpoint.ExpectedParentFp,
    };

    private static List<Dictionary<string, object?>> SnapshotEntities(Dictionary<string, object?> snapshot) =>
        snapshot["entities"] as List<Dictionary<string, object?>>
        ?? throw new BridgeServiceException("INVALID_SNAPSHOT", "semantic snapshot has invalid entity collection");

    private static Dictionary<string, object?> FindEntity(Dictionary<string, object?> snapshot, string pid) =>
        SnapshotEntities(snapshot).FirstOrDefault(item => string.Equals(Convert.ToString(item["semantic_pid"]), pid, StringComparison.Ordinal))
        ?? throw new BridgeServiceException("ENTITY_NOT_FOUND", "target semantic PID is not present in the bound current space");

    private static Dictionary<string, object?> FindVisibleBlockDefinition(
        Dictionary<string, object?> snapshot,
        string pid
    )
    {
        Dictionary<string, object?> entity = FindEntity(snapshot, pid);
        if (!string.Equals(
            Convert.ToString(entity["entity_type"]),
            "BLOCK_DEFINITION",
            StringComparison.Ordinal
        ))
        {
            throw new BridgeServiceException(
                "BLOCK_DEFINITION_NOT_VISIBLE",
                "definition_pid must identify a block definition already protected by expected_parent_fp"
            );
        }
        return entity;
    }

    private static BlockTableRecord CurrentSpace(Database database, Transaction transaction) =>
        (BlockTableRecord)transaction.GetObject(database.CurrentSpaceId, OpenMode.ForWrite);

    private static string AppendWithPid(BlockTableRecord space, Entity entity, Transaction transaction)
    {
        space.AppendEntity(entity); transaction.AddNewlyCreatedDBObject(entity, true);
        string pid = EntityPidStore.NewEntityPid(); EntityPidStore.Set(entity, pid, transaction); return pid;
    }

    private static void EnsureSimplePolyline(Polyline polyline)
    {
        if (Math.Abs(polyline.Elevation) > NumericTolerance || !SameVector(polyline.Normal, Vector3d.ZAxis))
            throw new BridgeServiceException("UNSUPPORTED_TARGET_GEOMETRY", "LWPOLYLINE update supports only zero-elevation +Z simple polylines");
        for (int i = 0; i < polyline.NumberOfVertices; i++)
            if (Math.Abs(polyline.GetBulgeAt(i)) > NumericTolerance || Math.Abs(polyline.GetStartWidthAt(i)) > NumericTolerance || Math.Abs(polyline.GetEndWidthAt(i)) > NumericTolerance)
                throw new BridgeServiceException("UNSUPPORTED_TARGET_GEOMETRY", "LWPOLYLINE update supports only zero-bulge zero-width vertices");
    }

    private static bool SameVector(Vector3d left, Vector3d right) =>
        Math.Abs(left.X - right.X) <= NumericTolerance && Math.Abs(left.Y - right.Y) <= NumericTolerance && Math.Abs(left.Z - right.Z) <= NumericTolerance;

    private static string Fingerprint(Dictionary<string, object?> snapshot) =>
        snapshot.TryGetValue("document_fp", out object? value) && value is string fp
            ? fp
            : throw new BridgeServiceException("SNAPSHOT_FINGERPRINT_MISSING", "native semantic snapshot did not produce document_fp");

    private static string RequireTargetPid(EntityMutationParams parameters) =>
        !string.IsNullOrWhiteSpace(parameters.SemanticPid)
            ? parameters.SemanticPid
            : throw new BridgeServiceException("INVALID_PARAMS", "semantic_pid is required for target mutation");

    private static Point3d Point(double[]? coordinates, string name)
    {
        if (coordinates is null || coordinates.Length != 3 || coordinates.Any(value => !double.IsFinite(value)))
            throw new BridgeServiceException("INVALID_PARAMS", $"{name} must contain three finite coordinates");
        return new Point3d(coordinates[0], coordinates[1], coordinates[2]);
    }

    private static Point3d PointValue(object? value, string name) =>
        value is double[] coordinates ? Point(coordinates, name) : throw new BridgeServiceException("R1_UNAVAILABLE", $"predecessor {name} is invalid");

    private static double Positive(double? value, string name)
    {
        if (!value.HasValue || !double.IsFinite(value.Value) || value.Value <= 0.0)
            throw new BridgeServiceException("INVALID_PARAMS", $"{name} must be a positive finite number");
        return value.Value;
    }

    private static double Angle(double? value, string name)
    {
        if (!value.HasValue || !double.IsFinite(value.Value) || value.Value < 0.0 || value.Value >= Math.PI * 2.0)
            throw new BridgeServiceException("INVALID_PARAMS", $"{name} must be in [0, 2pi) radians");
        return value.Value;
    }

    private static double[][] Points(double[][]? points)
    {
        if (points is null || points.Length < 2 || points.Length > BridgeConstants.MaxSimplePolylineVertices || points.Any(point => point.Length != 2 || point.Any(value => !double.IsFinite(value))))
            throw new BridgeServiceException("INVALID_PARAMS", "points must be bounded finite [x, y] pairs");
        return points;
    }

    private static bool Closed(bool? value) => value ?? throw new BridgeServiceException("INVALID_PARAMS", "closed must be a boolean");

    private static BridgeServiceException TypeMismatch(string expectedType) =>
        new("ENTITY_TYPE_MISMATCH", $"target semantic PID is not a {expectedType}");
}
