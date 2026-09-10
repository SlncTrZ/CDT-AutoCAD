// BridgeDispatcher — bounded queue drained only from AutoCAD Application.Idle.
// Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:58

using System.Threading.Channels;

namespace CDT.AutoCAD.Bridge;

internal sealed class BridgeDispatcher
{
    private readonly Channel<PendingRequest> _queue;
    private readonly ReadOnlyBridgeService _service;

    internal BridgeDispatcher(ReadOnlyBridgeService service)
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
        for (int i = 0; i < BridgeConstants.MaxRequestsPerIdleTick; i++)
        {
            if (!_queue.Reader.TryRead(out PendingRequest? pending))
            {
                return;
            }
            if (pending.IsCancelled)
            {
                continue;
            }

            BridgeResponse response;
            try
            {
                object result = _service.Handle(pending.Request);
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
                    "NATIVE_READ_FAILED",
                    "native read operation failed"
                );
            }
            pending.Completion.TrySetResult(response);
        }
    }

    internal void Complete()
    {
        _queue.Writer.TryComplete();
        while (_queue.Reader.TryRead(out PendingRequest? pending))
        {
            pending.Cancel();
            pending.Completion.TrySetResult(
                BridgeResponse.Failure(
                    pending.Request.RequestId,
                    "BRIDGE_STOPPING",
                    "native bridge is stopping"
                )
            );
        }
    }
}
