// BridgeConstants — bounded staged native bridge contract constants.
// Wing: code | Topic: native-bridge-n5 | Updated: 2026-09-10 14:10

namespace CDT.AutoCAD.Bridge;

internal static class BridgeConstants
{
    internal const string ProtocolVersion = "cdt-autocad-native-v1";
    internal const string BridgeVersion = "0.8.2-mp7";
    internal const int MaxFrameBytes = 65_536;
    internal const int MaxErrorMessageChars = 512;
    internal const int MaxPendingRequests = 32;
    internal const int MaxRequestsPerIdleTick = 4;
    internal const int MaxSnapshotEntities = 32;
    internal const int MaxBatchChunkEntities = 32;
    internal const int MaxBatchSemanticEntities = 12_288;
    internal const int MaxSimplePolylineVertices = 128;
    internal const int MaxMetadataJsonBytes = 8_192;
    internal const int MaxMetadataDepth = 8;
    internal const int MaxMetadataKeys = 256;
    internal const int MaxMetadataStringChars = 2_048;
    internal const int MaxMetadataQueryResults = 1_000;
    internal const int MaxMetadataStoredJsonBytes = 32_768;
    internal const int MaxMetadataStoredBase64Chars = 43_700;
    internal const int MetadataXRecordChunkChars = 240;
    internal const int MaxMetadataXRecordValues = 184;
    internal static readonly TimeSpan RequestTimeout = TimeSpan.FromSeconds(8);
    internal static readonly TimeSpan IoTimeout = TimeSpan.FromSeconds(8);

    internal const string AppDictionaryKey = "SLNCTRZ_CDT";
    internal const string DocumentPidRecordKey = "DOCUMENT_PID";
    internal const string EntityPidRecordKey = "SLNCTRZ_CDT_PID";
    internal const string MetadataRecordKey = "SLNCTRZ_CDT_METADATA_V1";
    internal const short PidSchemaVersion = 1;
    internal const short MetadataSchemaVersion = 1;
}
