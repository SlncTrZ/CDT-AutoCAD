// DocumentPidReader — read-only N2 document-lineage PID reader for the N3 bridge.
// Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 10:58

using Autodesk.AutoCAD.DatabaseServices;

namespace CDT.AutoCAD.Bridge;

internal static class DocumentPidReader
{
    internal static string? Read(Database database)
    {
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        DBDictionary namedObjects = (DBDictionary)transaction.GetObject(
            database.NamedObjectsDictionaryId,
            OpenMode.ForRead
        );
        if (!namedObjects.Contains(BridgeConstants.AppDictionaryKey))
        {
            return null;
        }

        DBDictionary appDictionary = (DBDictionary)transaction.GetObject(
            namedObjects.GetAt(BridgeConstants.AppDictionaryKey),
            OpenMode.ForRead
        );
        if (!appDictionary.Contains(BridgeConstants.DocumentPidRecordKey))
        {
            return null;
        }

        Xrecord record = (Xrecord)transaction.GetObject(
            appDictionary.GetAt(BridgeConstants.DocumentPidRecordKey),
            OpenMode.ForRead
        );
        ResultBuffer? data = record.Data;
        if (data is null)
        {
            throw new BridgeServiceException(
                "PID_METADATA_INVALID",
                "document PID metadata is empty"
            );
        }

        using (data)
        {
            TypedValue[] values = data.AsArray();
            if (values.Length != 2
                || values[0].TypeCode != (int)DxfCode.Int16
                || Convert.ToInt16(values[0].Value) != BridgeConstants.PidSchemaVersion
                || values[1].TypeCode != (int)DxfCode.Text)
            {
                throw new BridgeServiceException(
                    "PID_METADATA_INVALID",
                    "document PID metadata has an unsupported or malformed schema"
                );
            }
            string? pid = values[1].Value?.ToString();
            if (string.IsNullOrWhiteSpace(pid))
            {
                throw new BridgeServiceException(
                    "PID_METADATA_INVALID",
                    "document PID metadata contains an empty identifier"
                );
            }
            return pid;
        }
    }
}
