// ReadOnlyBridgeService — N3 fixed read-only operation allowlist executed on AutoCAD Idle.
// Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:58

using Autodesk.AutoCAD.ApplicationServices.Core;

namespace CDT.AutoCAD.Bridge;

internal sealed class ReadOnlyBridgeService
{
    private readonly Guid _bridgeInstanceId;
    private readonly string _pipeName;
    private readonly DocumentRegistry _documents;

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
            _ => throw new BridgeServiceException(
                "UNSUPPORTED_OPERATION",
                "operation is not enabled in N3"
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
            active_document = Application.DocumentManager.MdiActiveDocument?.Name,
        };
    }

    private object DocumentIdentity(BridgeRequest request)
    {
        DocumentIdentityParams parameters = request.DocumentIdentity
            ?? throw new BridgeServiceException("INVALID_PARAMS", "document identity params are required");
        return _documents.Resolve(parameters.RuntimeDocumentId, parameters.DocumentPid);
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
