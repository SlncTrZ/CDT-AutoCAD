// PendingRequest — cancellable bounded handoff from pipe I/O to AutoCAD Idle dispatch.
// Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:58

namespace CDT.AutoCAD.Bridge;

internal sealed class PendingRequest
{
    private int _cancelled;

    internal PendingRequest(BridgeRequest request)
    {
        Request = request;
        Completion = new TaskCompletionSource<BridgeResponse>(
            TaskCreationOptions.RunContinuationsAsynchronously
        );
    }

    internal BridgeRequest Request { get; }
    internal TaskCompletionSource<BridgeResponse> Completion { get; }
    internal bool IsCancelled => Volatile.Read(ref _cancelled) != 0;

    internal void Cancel()
    {
        Interlocked.Exchange(ref _cancelled, 1);
    }
}
