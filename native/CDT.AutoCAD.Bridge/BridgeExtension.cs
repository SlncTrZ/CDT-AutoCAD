// BridgeExtension — AutoCAD IExtensionApplication lifecycle for the staged N3 bridge.
// Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:58

using Autodesk.AutoCAD.ApplicationServices.Core;
using Autodesk.AutoCAD.Runtime;
using System.Diagnostics;

namespace CDT.AutoCAD.Bridge;

public sealed class BridgeExtension : IExtensionApplication
{
    private Guid _bridgeInstanceId;
    private BridgeDispatcher? _dispatcher;
    private PipeHost? _pipeHost;

    public void Initialize()
    {
        if (_pipeHost is not null)
        {
            return;
        }

        _bridgeInstanceId = Guid.NewGuid();
        int sessionId = Process.GetCurrentProcess().SessionId;
        string pipeName = $"SlncTrZ.CDT.AutoCAD.Bridge.v1.s{sessionId}";
        DocumentRegistry documents = new();
        ReadOnlyBridgeService service = new(_bridgeInstanceId, pipeName, documents);
        _dispatcher = new BridgeDispatcher(service);
        _pipeHost = new PipeHost(pipeName, _dispatcher);
        _pipeHost.Start();
        Application.Idle += OnIdle;
    }

    public void Terminate()
    {
        Application.Idle -= OnIdle;
        _dispatcher?.Complete();
        if (_pipeHost is not null)
        {
            try
            {
                _pipeHost.DisposeAsync().AsTask().GetAwaiter().GetResult();
            }
            catch
            {
                // AutoCAD termination must not be blocked by transport shutdown errors.
            }
        }
        _pipeHost = null;
        _dispatcher = null;
    }

    private void OnIdle(object? sender, EventArgs args)
    {
        _dispatcher?.DrainIdle();
    }
}
