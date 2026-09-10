// BridgeConstants — frozen N3 read-only bridge contract constants.
// Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:58

namespace CDT.AutoCAD.Bridge;

internal static class BridgeConstants
{
    internal const string ProtocolVersion = "cdt-autocad-native-v1";
    internal const string BridgeVersion = "0.1.0-n3";
    internal const int MaxFrameBytes = 65_536;
    internal const int MaxErrorMessageChars = 512;
    internal const int MaxPendingRequests = 32;
    internal const int MaxRequestsPerIdleTick = 4;
    internal static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(8);
    internal static readonly TimeSpan IoTimeout = TimeSpan.FromSeconds(8);

    internal const string AppDictionaryKey = "SLNCTRZ_CDT";
    internal const string DocumentPidRecordKey = "DOCUMENT_PID";
    internal const short PidSchemaVersion = 1;
}
