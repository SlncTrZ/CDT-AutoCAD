// NativeBridgeService — staged N5 read + fixed-schema mutation allowlist executed on AutoCAD Idle.
// Wing: code | Topic: native-bridge-n5 | Updated: 2026-09-10 14:08

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CDT.AutoCAD.Bridge;

internal sealed class NativeBridgeService
{
    private readonly Guid _bridgeInstanceId;
    private readonly string _pipeName;
    private readonly DocumentRegistry _documents;
    private readonly NativeSemanticExtractor _semantic = new();
    private readonly NativeMutationService _mutations;

    internal NativeBridgeService(Guid bridgeInstanceId, string pipeName, DocumentRegistry documents)
    {
        _bridgeInstanceId = bridgeInstanceId;
        _pipeName = pipeName;
        _documents = documents;
        _mutations = new NativeMutationService(_semantic, _documents);
    }

    internal object Handle(BridgeRequest request)
    {
        return request.Operation switch
        {
            "bridge.health" => Health(),
            "bridge.documents.list" => new { documents = _documents.List() },
            "bridge.document.identity" => DocumentIdentity(request),
            "bridge.document.snapshot" => DocumentSnapshot(request),
            "bridge.document.state" => DocumentState(request),
            "viewport.visual_style.get" => ViewportVisualStyleGet(request),
            "viewport.visual_style.set" => ViewportVisualStyleSet(request),
            "bridge.recovery.list" => _mutations.ListRecoveries(),
            "bridge.recovery.resolve" => RecoveryResolve(request),
            "bridge.recovery.finalize" => RecoveryFinalize(request),
            "bridge.logical.begin" => LogicalBegin(request),
            "metadata.get" => MetadataGet(request),
            "metadata.set" => MetadataSet(request),
            "metadata.query" => MetadataQuery(request),
            "entity.batch.create" => BatchCreate(request),
            "entity.batch.insert_blocks" => BatchInsertBlocks(request),
            "entity.batch.transform" => BatchTransform(request),
            "entity.create.line" => EntityMutation(request),
            "entity.update.line" => EntityMutation(request),
            "entity.delete.line" => EntityMutation(request),
            "entity.create.circle" => EntityMutation(request),
            "entity.update.circle" => EntityMutation(request),
            "entity.delete.circle" => EntityMutation(request),
            "entity.create.arc" => EntityMutation(request),
            "entity.update.arc" => EntityMutation(request),
            "entity.delete.arc" => EntityMutation(request),
            "entity.create.lwpolyline" => EntityMutation(request),
            "entity.update.lwpolyline" => EntityMutation(request),
            "entity.delete.lwpolyline" => EntityMutation(request),
            _ => throw new BridgeServiceException(
                "UNSUPPORTED_OPERATION",
                "operation is not enabled"
            ),
        };
    }

    private object Health()
    {
        IReadOnlyList<DocumentIdentity> documents = _documents.List();
        return new
        {
            protocol = BridgeConstants.ProtocolVersion,
            bridge_version = BridgeConstants.BridgeVersion,
            bridge_instance_id = _bridgeInstanceId.ToString("D"),
            pipe_name = _pipeName,
            process_id = Environment.ProcessId,
            windows_session_id = System.Diagnostics.Process.GetCurrentProcess().SessionId,
            transport = "windows-named-pipe",
            dispatch_context = "AutoCAD.Application.Idle",
            current_user_only = true,
            local_computer_only = true,
            same_windows_session_only = true,
            mutation_enabled = true,
            document_fp_schema_version = 3,
            metadata_schema_version = BridgeConstants.MetadataSchemaVersion,
            metadata_in_document_fp = true,
            two_phase_commit_integrity = true,
            batch_chunk_atomic = true,
            batch_yield_per_idle = true,
            cross_chunk_atomic = false,
            logical_batch_atomic = true,
            logical_batch_recovery = "single-r2-checkpoint",
            max_batch_chunk_entities = BridgeConstants.MaxBatchChunkEntities,
            max_batch_semantic_entities = BridgeConstants.MaxBatchSemanticEntities,
            recovery_operations = new[]
            {
                "bridge.recovery.list",
                "bridge.recovery.resolve",
                "bridge.recovery.finalize",
                "bridge.logical.begin",
            },
            read_operations = new[]
            {
                "bridge.documents.list",
                "bridge.document.identity",
                "bridge.document.snapshot",
                "bridge.document.state",
                "viewport.visual_style.get",
                "bridge.recovery.list",
                "metadata.get",
                "metadata.query",
            },
            mutation_operations = new[]
            {
                "metadata.set",
                "viewport.visual_style.set",
                "entity.batch.create",
                "entity.batch.insert_blocks",
                "entity.batch.transform",
                "entity.create.line",
                "entity.update.line",
                "entity.delete.line",
                "entity.create.circle",
                "entity.update.circle",
                "entity.delete.circle",
                "entity.create.arc",
                "entity.update.arc",
                "entity.delete.arc",
                "entity.create.lwpolyline",
                "entity.update.lwpolyline",
                "entity.delete.lwpolyline",
            },
            document_count = documents.Count,
            active_document = AcApplication.DocumentManager.MdiActiveDocument?.Name,
        };
    }

    private object DocumentIdentity(BridgeRequest request)
    {
        DocumentIdentityParams parameters = request.DocumentIdentity
            ?? throw new BridgeServiceException("INVALID_PARAMS", "document identity params are required");
        return _documents.Resolve(parameters.RuntimeDocumentId, parameters.DocumentPid);
    }

    private object ViewportVisualStyleGet(BridgeRequest request)
    {
        DocumentIdentityParams parameters = request.DocumentIdentity
            ?? throw new BridgeServiceException("INVALID_PARAMS", "visual style document binding is required");
        Document document = _documents.ResolveTransientDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        VisualStyleState state = ReadCurrentVisualStyle(document);
        return new
        {
            runtime_document_id = parameters.RuntimeDocumentId.ToString("D"),
            document_pid = DocumentPidReader.Read(document.Database),
            visual_style_handle = state.Handle,
            visual_style_name = state.Name,
        };
    }

    private object ViewportVisualStyleSet(BridgeRequest request)
    {
        ViewportVisualStyleSetParams parameters = request.VisualStyleSet
            ?? throw new BridgeServiceException("INVALID_PARAMS", "visual style set params are required");
        Document document = _documents.ResolveTransientDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        VisualStyleState before = ReadCurrentVisualStyle(document);
        if (!string.Equals(before.Handle, parameters.ExpectedCurrentHandle, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "VIEW_STATE_DRIFT",
                "current visual style changed after the caller observation; restore is refused"
            );
        }
        ObjectId targetId = ResolveVisualStyleHandle(document.Database, parameters.VisualStyleHandle);
        using (ViewTableRecord view = document.Editor.GetCurrentView())
        {
            view.VisualStyleId = targetId;
            document.Editor.SetCurrentView(view);
        }
        VisualStyleState after = ReadCurrentVisualStyle(document);
        if (!string.Equals(after.Handle, parameters.VisualStyleHandle, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "VISUAL_STYLE_READBACK_MISMATCH",
                "current visual style does not match the requested typed restore handle"
            );
        }
        return new
        {
            runtime_document_id = parameters.RuntimeDocumentId.ToString("D"),
            document_pid = DocumentPidReader.Read(document.Database),
            previous_visual_style_handle = before.Handle,
            visual_style_handle = after.Handle,
            visual_style_name = after.Name,
            readback_verified = true,
        };
    }

    private static VisualStyleState ReadCurrentVisualStyle(Document document)
    {
        using ViewTableRecord view = document.Editor.GetCurrentView();
        ObjectId styleId = view.VisualStyleId;
        if (styleId.IsNull || !styleId.IsValid)
        {
            throw new BridgeServiceException(
                "VISUAL_STYLE_UNAVAILABLE",
                "current viewport does not expose a valid managed visual style id"
            );
        }
        string? name = FindVisualStyleName(document.Database, styleId);
        if (string.IsNullOrWhiteSpace(name))
        {
            throw new BridgeServiceException(
                "VISUAL_STYLE_UNAVAILABLE",
                "current viewport visual style is not present in the drawing visual-style dictionary"
            );
        }
        return new VisualStyleState(styleId.Handle.ToString(), name);
    }

    private static ObjectId ResolveVisualStyleHandle(Database database, string handle)
    {
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        DBDictionary styles = (DBDictionary)transaction.GetObject(
            database.VisualStyleDictionaryId,
            OpenMode.ForRead
        );
        foreach (DBDictionaryEntry entry in styles)
        {
            if (string.Equals(entry.Value.Handle.ToString(), handle, StringComparison.Ordinal))
            {
                return entry.Value;
            }
        }
        throw new BridgeServiceException(
            "VISUAL_STYLE_NOT_FOUND",
            "requested visual-style handle is not present in the active drawing"
        );
    }

    private static string? FindVisualStyleName(Database database, ObjectId styleId)
    {
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        DBDictionary styles = (DBDictionary)transaction.GetObject(
            database.VisualStyleDictionaryId,
            OpenMode.ForRead
        );
        foreach (DBDictionaryEntry entry in styles)
        {
            if (entry.Value == styleId)
            {
                return entry.Key;
            }
        }
        return null;
    }

    private sealed record VisualStyleState(string Handle, string Name);

    private object LogicalBegin(BridgeRequest request)
    {
        LogicalBeginParams parameters = request.LogicalBegin
            ?? throw new BridgeServiceException("INVALID_PARAMS", "logical begin params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.BeginLogicalBatch(document, request.RequestId, parameters);
    }

    private object MetadataGet(BridgeRequest request)
    {
        MetadataGetParams parameters = request.MetadataGet
            ?? throw new BridgeServiceException("INVALID_PARAMS", "metadata get params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.MetadataGet(document, parameters);
    }

    private object MetadataSet(BridgeRequest request)
    {
        MetadataSetParams parameters = request.MetadataSet
            ?? throw new BridgeServiceException("INVALID_PARAMS", "metadata set params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.MetadataSet(document, request.RequestId, parameters);
    }

    private object MetadataQuery(BridgeRequest request)
    {
        MetadataQueryParams parameters = request.MetadataQuery
            ?? throw new BridgeServiceException("INVALID_PARAMS", "metadata query params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.MetadataQuery(document, parameters);
    }

    private object BatchCreate(BridgeRequest request)
    {
        BatchCreateParams parameters = request.BatchCreate
            ?? throw new BridgeServiceException("INVALID_PARAMS", "batch create params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.ExecuteBatchCreate(document, request.RequestId, parameters);
    }

    private object BatchInsertBlocks(BridgeRequest request)
    {
        BatchInsertBlocksParams parameters = request.BatchInsertBlocks
            ?? throw new BridgeServiceException("INVALID_PARAMS", "batch insert blocks params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.ExecuteBatchInsertBlocks(document, request.RequestId, parameters);
    }

    private object BatchTransform(BridgeRequest request)
    {
        BatchTransformParams parameters = request.BatchTransform
            ?? throw new BridgeServiceException("INVALID_PARAMS", "batch transform params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.ExecuteBatchTransform(document, request.RequestId, parameters);
    }

    private object EntityMutation(BridgeRequest request)
    {
        EntityMutationParams parameters = request.Mutation
            ?? throw new BridgeServiceException("INVALID_PARAMS", "entity mutation params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.Execute(document, request.RequestId, request.Operation, parameters);
    }

    internal void Cancel(BridgeRequest request)
    {
        if (string.Equals(request.Operation, "bridge.recovery.resolve", StringComparison.Ordinal))
        {
            _mutations.CancelRecoveryRequest(request.RequestId);
        }
    }

    private object RecoveryResolve(BridgeRequest request)
    {
        RecoveryResolveParams parameters = request.RecoveryResolve
            ?? throw new BridgeServiceException("INVALID_PARAMS", "recovery resolve params are required");
        return _mutations.ResolveRecovery(request.RequestId, parameters);
    }

    private object RecoveryFinalize(BridgeRequest request)
    {
        RecoveryFinalizeParams parameters = request.RecoveryFinalize
            ?? throw new BridgeServiceException("INVALID_PARAMS", "recovery finalize params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.FinalizeRecovery(document, parameters);
    }

    private object DocumentState(BridgeRequest request)
    {
        DocumentIdentityParams parameters = request.DocumentIdentity
            ?? throw new BridgeServiceException("INVALID_PARAMS", "document state params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        string documentPid = DocumentPidReader.Read(document.Database)
            ?? throw new BridgeServiceException(
                "DOCUMENT_PID_MISSING",
                "native compact document state requires persistent document lineage PID metadata"
            );
        Dictionary<string, object?> snapshot = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            documentPid,
            BridgeConstants.MaxBatchSemanticEntities
        );
        List<Dictionary<string, object?>> entities = snapshot["entities"]
            as List<Dictionary<string, object?>>
            ?? throw new BridgeServiceException(
                "INVALID_SNAPSHOT",
                "semantic snapshot has invalid entity collection"
            );
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["runtime_document_id"] = parameters.RuntimeDocumentId.ToString("D"),
            ["document_pid"] = documentPid,
            ["document_fp_schema_version"] = 3,
            ["document_fp"] = snapshot["document_fp"],
            ["entity_count"] = entities.Count,
        };
    }

    private object DocumentSnapshot(BridgeRequest request)
    {
        DocumentIdentityParams parameters = request.DocumentIdentity
            ?? throw new BridgeServiceException("INVALID_PARAMS", "document snapshot params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        string documentPid = DocumentPidReader.Read(document.Database)
            ?? throw new BridgeServiceException(
                "DOCUMENT_PID_MISSING",
                "native semantic snapshot requires persistent document lineage PID metadata"
            );
        return _semantic.Extract(document, parameters.RuntimeDocumentId, documentPid);
    }
}

internal sealed class BridgeServiceException : Exception
{
    internal BridgeServiceException(string code, string message) : base(message)
    {
        Code = code;
    }

    internal string Code { get; }
}
