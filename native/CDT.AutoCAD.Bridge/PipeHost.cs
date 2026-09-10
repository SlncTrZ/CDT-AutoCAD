// PipeHost — same-user/local-session bounded one-request-per-connection Named Pipe host.
// Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:58

using System.Buffers.Binary;
using System.IO.Pipes;

namespace CDT.AutoCAD.Bridge;

internal sealed class PipeHost : IAsyncDisposable
{
    private readonly string _pipeName;
    private readonly BridgeDispatcher _dispatcher;
    private readonly CancellationTokenSource _shutdown = new();
    private Task? _loop;

    internal PipeHost(string pipeName, BridgeDispatcher dispatcher)
    {
        _pipeName = pipeName;
        _dispatcher = dispatcher;
    }

    internal void Start()
    {
        if (_loop is not null)
        {
            throw new InvalidOperationException("pipe host already started");
        }
        NamedPipeServerStream firstPipe = CreatePipe();
        try
        {
            _loop = Task.Run(() => AcceptLoopAsync(firstPipe, _shutdown.Token));
        }
        catch
        {
            firstPipe.Dispose();
            throw;
        }
    }

    private async Task AcceptLoopAsync(
        NamedPipeServerStream firstPipe,
        CancellationToken cancellationToken
    )
    {
        NamedPipeServerStream? reservedPipe = firstPipe;
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                try
                {
                    await using NamedPipeServerStream pipe = reservedPipe ?? CreatePipe();
                    reservedPipe = null;
                    await pipe.WaitForConnectionAsync(cancellationToken).ConfigureAwait(false);
                    try
                    {
                        PipeClientBoundary.Validate(pipe);
                    }
                    catch (BridgeClientBoundaryException exc)
                    {
                        await WriteResponseAsync(
                            pipe,
                            BridgeResponse.Failure(
                                null,
                                exc.Code,
                                "pipe client failed local user/session validation"
                            ),
                            cancellationToken
                        ).ConfigureAwait(false);
                        continue;
                    }

                    await HandleConnectionAsync(pipe, cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    return;
                }
                catch
                {
                    // Keep the read-only server alive after a malformed/failed connection.
                    await Task.Delay(TimeSpan.FromMilliseconds(100), cancellationToken).ConfigureAwait(false);
                }
            }
        }
        finally
        {
            if (reservedPipe is not null)
            {
                await reservedPipe.DisposeAsync().ConfigureAwait(false);
            }
        }
    }

    private NamedPipeServerStream CreatePipe()
    {
        return new NamedPipeServerStream(
            _pipeName,
            PipeDirection.InOut,
            1,
            PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly | PipeOptions.FirstPipeInstance,
            BridgeConstants.MaxFrameBytes + sizeof(uint),
            BridgeConstants.MaxFrameBytes + sizeof(uint)
        );
    }

    private async Task HandleConnectionAsync(
        NamedPipeServerStream pipe,
        CancellationToken serverCancellation
    )
    {
        Guid? requestId = null;
        try
        {
            using CancellationTokenSource ioTimeout = CancellationTokenSource.CreateLinkedTokenSource(
                serverCancellation
            );
            ioTimeout.CancelAfter(BridgeConstants.IoTimeout);
            byte[] body = await ReadFrameAsync(pipe, ioTimeout.Token).ConfigureAwait(false);
            BridgeRequest request = BridgeProtocol.ParseRequest(body);
            requestId = request.RequestId;

            PendingRequest pending = new(request);
            if (!_dispatcher.TryEnqueue(pending))
            {
                await WriteResponseAsync(
                    pipe,
                    BridgeResponse.Failure(
                        request.RequestId,
                        "SERVER_BUSY",
                        "native bridge request queue is full"
                    ),
                    ioTimeout.Token
                ).ConfigureAwait(false);
                return;
            }

            try
            {
                BridgeResponse response = await pending.Completion.Task
                    .WaitAsync(BridgeConstants.RequestTimeout, serverCancellation)
                    .ConfigureAwait(false);
                await WriteResponseAsync(pipe, response, ioTimeout.Token).ConfigureAwait(false);
            }
            catch (TimeoutException)
            {
                pending.Cancel();
                await WriteResponseAsync(
                    pipe,
                    BridgeResponse.Failure(
                        request.RequestId,
                        "REQUEST_TIMEOUT",
                        "native bridge request timed out before dispatch completed"
                    ),
                    ioTimeout.Token
                ).ConfigureAwait(false);
            }
        }
        catch (BridgeProtocolException exc)
        {
            await TryWriteFailureAsync(
                pipe,
                BridgeResponse.Failure(exc.RequestId ?? requestId, exc.Code, exc.Message),
                serverCancellation
            ).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!serverCancellation.IsCancellationRequested)
        {
            await TryWriteFailureAsync(
                pipe,
                BridgeResponse.Failure(requestId, "IO_TIMEOUT", "native bridge I/O timed out"),
                serverCancellation
            ).ConfigureAwait(false);
        }
    }

    private static async Task<byte[]> ReadFrameAsync(Stream stream, CancellationToken cancellationToken)
    {
        byte[] header = new byte[sizeof(uint)];
        await ReadExactAsync(stream, header, cancellationToken).ConfigureAwait(false);
        uint length = BinaryPrimitives.ReadUInt32LittleEndian(header);
        if (length == 0)
        {
            throw new BridgeProtocolException(
                "INVALID_FRAME_LENGTH",
                "frame payload length must be positive"
            );
        }
        if (length > BridgeConstants.MaxFrameBytes)
        {
            throw new BridgeProtocolException(
                "FRAME_TOO_LARGE",
                "frame exceeds maximum payload size"
            );
        }
        byte[] body = new byte[checked((int)length)];
        await ReadExactAsync(stream, body, cancellationToken).ConfigureAwait(false);
        return body;
    }

    private static async Task ReadExactAsync(
        Stream stream,
        Memory<byte> buffer,
        CancellationToken cancellationToken
    )
    {
        int read = 0;
        while (read < buffer.Length)
        {
            int chunk = await stream.ReadAsync(buffer[read..], cancellationToken).ConfigureAwait(false);
            if (chunk == 0)
            {
                throw new BridgeProtocolException(
                    "TRUNCATED_FRAME",
                    "frame ended before declared length"
                );
            }
            read += chunk;
        }
    }

    private static async Task WriteResponseAsync(
        Stream stream,
        BridgeResponse response,
        CancellationToken cancellationToken
    )
    {
        byte[] body = BridgeProtocol.SerializeResponse(response);
        if (body.Length == 0 || body.Length > BridgeConstants.MaxFrameBytes)
        {
            throw new InvalidOperationException("bridge generated an invalid response frame size");
        }
        byte[] header = new byte[sizeof(uint)];
        BinaryPrimitives.WriteUInt32LittleEndian(header, checked((uint)body.Length));
        await stream.WriteAsync(header, cancellationToken).ConfigureAwait(false);
        await stream.WriteAsync(body, cancellationToken).ConfigureAwait(false);
        await stream.FlushAsync(cancellationToken).ConfigureAwait(false);
    }

    private static async Task TryWriteFailureAsync(
        Stream stream,
        BridgeResponse response,
        CancellationToken serverCancellation
    )
    {
        if (!stream.CanWrite)
        {
            return;
        }
        try
        {
            using CancellationTokenSource timeout = CancellationTokenSource.CreateLinkedTokenSource(
                serverCancellation
            );
            timeout.CancelAfter(BridgeConstants.IoTimeout);
            await WriteResponseAsync(stream, response, timeout.Token).ConfigureAwait(false);
        }
        catch
        {
            // Connection is already unusable; never leak transport exception detail.
        }
    }

    public async ValueTask DisposeAsync()
    {
        _shutdown.Cancel();
        if (_loop is not null)
        {
            try
            {
                await _loop.ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                // Expected during shutdown.
            }
        }
        _shutdown.Dispose();
    }
}
