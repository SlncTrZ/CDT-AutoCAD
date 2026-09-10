// PipeClientBoundary — prove named-pipe client is local and in the bridge Windows session.
// Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:58

using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace CDT.AutoCAD.Bridge;

internal static class PipeClientBoundary
{
    private const int MaxComputerNameChars = 256;
    private const int ErrorPipeLocal = 229;

    internal static void Validate(NamedPipeServerStream pipe)
    {
        SafePipeHandle handle = pipe.SafePipeHandle;
        StringBuilder computer = new(MaxComputerNameChars);
        bool remoteComputerReported = GetNamedPipeClientComputerName(
            handle,
            computer,
            checked((uint)(computer.Capacity * sizeof(char)))
        );
        if (remoteComputerReported)
        {
            // A computer name means Windows classified this as a remote SMB pipe client.
            // Never compare that name with Environment.MachineName: a self-SMB connection
            // can report the local host name while still traversing the remote pipe path.
            throw new BridgeClientBoundaryException("REMOTE_CLIENT_REJECTED");
        }

        int computerError = Marshal.GetLastWin32Error();
        if (computerError != ErrorPipeLocal)
        {
            throw new BridgeClientBoundaryException("CLIENT_COMPUTER_UNVERIFIED");
        }

        if (!GetNamedPipeClientSessionId(handle, out uint clientSessionId))
        {
            throw new BridgeClientBoundaryException("CLIENT_SESSION_UNVERIFIED");
        }
        int serverSessionId = Process.GetCurrentProcess().SessionId;
        if (clientSessionId != checked((uint)serverSessionId))
        {
            throw new BridgeClientBoundaryException("CLIENT_SESSION_MISMATCH");
        }
    }

    [DllImport("kernel32.dll", EntryPoint = "GetNamedPipeClientComputerNameW", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetNamedPipeClientComputerName(
        SafePipeHandle pipe,
        StringBuilder clientComputerName,
        uint clientComputerNameLength
    );

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetNamedPipeClientSessionId(
        SafePipeHandle pipe,
        out uint clientSessionId
    );
}

internal sealed class BridgeClientBoundaryException : Exception
{
    internal BridgeClientBoundaryException(string code) : base("pipe client failed local user/session validation")
    {
        Code = code;
    }

    internal string Code { get; }
}
