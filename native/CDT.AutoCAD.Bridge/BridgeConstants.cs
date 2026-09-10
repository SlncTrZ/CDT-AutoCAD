// BridgeConstants — bounded staged native bridge contract constants.
// Wing: code | Topic: native-bridge-n5 | Updated: 2026-09-10 14:10

namespace CDT.AutoCAD.Bridge;

internal static class BridgeConstants
{
    internal const string ProtocolVersion = "cdt-autocad-native-v1";
    internal const string BridgeVersion = "0.4.0-o1";
    internal const int MaxFrameBytes = 65_536;
    internal const int MaxErrorMessageChars = 512;
    internal const int MaxPendingRequests = 32;
    internal const int MaxRequestsPerIdleTick = 4;
    internal const int MaxSnapshotEntities = 32;
    internal const int MaxSimplePolylineVertices = 128;
    internal static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(8);
    internal static readonly TimeSpan IoTimeout = TimeSpan.FromSeconds(8);

    internal const string AppDictionaryKey = "SLNCTRZ_CDT";
    internal const string DocumentPidRecordKey = "DOCUMENT_PID";
    internal const string EntityPidRecordKey = "SLNCTRZ_CDT_PID";
    internal const short PidSchemaVersion = 1;
}
