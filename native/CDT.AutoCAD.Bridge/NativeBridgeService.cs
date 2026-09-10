// NativeBridgeService — staged N5 read + fixed-schema mutation allowlist executed on AutoCAD Idle.
// Wing: code | Topic: native-bridge-n5 | Updated: 2026-09-10 14:08

using Autodesk.AutoCAD.ApplicationServices;
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
        _mutations = new NativeMutationService(_semantic);
    }

    internal object Handle(BridgeRequest request)
    {
        return request.Operation switch
        {
            "bridge.health" => Health(),
            "bridge.documents.list" => new { documents = _documents.List() },
            "bridge.document.identity" => DocumentIdentity(request),
            "bridge.document.snapshot" => DocumentSnapshot(request),
            "entity.create.line" => LineMutation(request),
            "entity.update.line" => LineMutation(request),
            "entity.delete.line" => LineMutation(request),
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
            document_fp_schema_version = 2,
            mutation_operations = new[]
            {
                "entity.create.line",
                "entity.update.line",
                "entity.delete.line",
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

    private object LineMutation(BridgeRequest request)
    {
        LineMutationParams parameters = request.LineMutation
            ?? throw new BridgeServiceException("INVALID_PARAMS", "line mutation params are required");
        Document document = _documents.ResolveDocument(
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        return _mutations.Execute(document, request.Operation, parameters);
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
