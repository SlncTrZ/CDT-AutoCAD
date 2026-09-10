// ReadOnlyBridgeService — staged read-only operation allowlist executed on AutoCAD Idle.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:11

using Autodesk.AutoCAD.ApplicationServices;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CDT.AutoCAD.Bridge;

internal sealed class ReadOnlyBridgeService
{
    private readonly Guid _bridgeInstanceId;
    private readonly string _pipeName;
    private readonly DocumentRegistry _documents;
    private readonly NativeSemanticExtractor _semantic = new();

    internal ReadOnlyBridgeService(Guid bridgeInstanceId, string pipeName, DocumentRegistry documents)
    {
        _bridgeInstanceId = bridgeInstanceId;
        _pipeName = pipeName;
        _documents = documents;
    }

    internal object Handle(BridgeRequest request)
    {
        return request.Operation switch
        {
            "bridge.health" => Health(),
            "bridge.documents.list" => new { documents = _documents.List() },
            "bridge.document.identity" => DocumentIdentity(request),
            "bridge.document.snapshot" => DocumentSnapshot(request),
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
            mutation_enabled = false,
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
