// BridgeDispatcher — bounded queue drained only from AutoCAD Application.Idle.
// Wing: code | Topic: native-bridge-n7 | Updated: 2026-09-11 08:20

using System.Threading.Channels;

namespace CDT.AutoCAD.Bridge;

internal sealed class DeferredBridgeResult
{
    internal static readonly DeferredBridgeResult Instance = new();

    private DeferredBridgeResult() { }
}

internal sealed class BridgeDispatcher
{
    private readonly Channel<PendingRequest> _queue;
    private readonly Queue<PendingRequest> _deferred = new();
    private readonly NativeBridgeService _service;

    internal BridgeDispatcher(NativeBridgeService service)
    {
        _service = service;
        _queue = Channel.CreateBounded<PendingRequest>(
            new BoundedChannelOptions(BridgeConstants.MaxPendingRequests)
            {
                SingleReader = true,
                SingleWriter = false,
                FullMode = BoundedChannelFullMode.Wait,
                AllowSynchronousContinuations = false,
            }
        );
    }

    internal bool TryEnqueue(PendingRequest request)
    {
        return _queue.Writer.TryWrite(request);
    }

    internal void DrainIdle()
    {
        int deferredAtStart = _deferred.Count;
        for (int i = 0; i < BridgeConstants.MaxRequestsPerIdleTick; i++)
        {
            PendingRequest? pending;
            if (deferredAtStart > 0)
            {
                pending = _deferred.Dequeue();
                deferredAtStart--;
            }
            else if (!_queue.Reader.TryRead(out pending))
            {
                return;
            }

            if (pending.IsCancelled)
            {
                _service.Cancel(pending.Request);
                continue;
            }

            BridgeResponse response;
            try
            {
                object result = _service.Handle(pending.Request);
                if (ReferenceEquals(result, DeferredBridgeResult.Instance))
                {
                    _deferred.Enqueue(pending);
                    continue;
                }
                response = BridgeResponse.Success(pending.Request.RequestId, result);
            }
            catch (BridgeServiceException exc)
            {
                response = BridgeResponse.Failure(
                    pending.Request.RequestId,
                    exc.Code,
                    exc.Message
                );
            }
            catch
            {
                response = BridgeResponse.Failure(
                    pending.Request.RequestId,
                    "NATIVE_OPERATION_FAILED",
                    "native bridge operation failed"
                );
            }
            pending.Completion.TrySetResult(response);
        }
    }

    internal void Complete()
    {
        _queue.Writer.TryComplete();
        while (_deferred.Count > 0)
        {
            CompleteStopping(_deferred.Dequeue());
        }
        while (_queue.Reader.TryRead(out PendingRequest? pending))
        {
            CompleteStopping(pending);
        }
    }

    private void CompleteStopping(PendingRequest pending)
    {
        pending.Cancel();
        _service.Cancel(pending.Request);
        pending.Completion.TrySetResult(
            BridgeResponse.Failure(
                pending.Request.RequestId,
                "BRIDGE_STOPPING",
                "native bridge is stopping"
            )
        );
    }
}
