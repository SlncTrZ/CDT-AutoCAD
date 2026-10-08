// EntityPidReader — read-only N2 DBObject PID carrier reader used by native semantic extraction.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 12:48

using Autodesk.AutoCAD.DatabaseServices;

namespace CDT.AutoCAD.Bridge;

internal static class EntityPidReader
{
    internal static string ReadRequired(DBObject databaseObject, Transaction transaction) =>
        ReadOptional(databaseObject, transaction)
        ?? throw new BridgeServiceException(
            "UNMANAGED_ENTITY_PRESENT",
            "semantic scope contains an entity outside provider PID ownership; explicit adoption is required before native semantic read or mutation"
        );

    internal static string? ReadOptional(DBObject databaseObject, Transaction transaction)
    {
        if (databaseObject.ExtensionDictionary.IsNull)
        {
            return null;
        }

        DBDictionary extensionDictionary = (DBDictionary)transaction.GetObject(
            databaseObject.ExtensionDictionary,
            OpenMode.ForRead
        );
        if (!extensionDictionary.Contains(BridgeConstants.EntityPidRecordKey))
        {
            return null;
        }

        Xrecord record = (Xrecord)transaction.GetObject(
            extensionDictionary.GetAt(BridgeConstants.EntityPidRecordKey),
            OpenMode.ForRead
        );
        ResultBuffer? data = record.Data;
        if (data is null)
        {
            throw new BridgeServiceException("PID_METADATA_INVALID", "entity PID metadata is empty");
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
                    "entity PID metadata has an unsupported or malformed schema"
                );
            }
            string? pid = values[1].Value?.ToString();
            if (string.IsNullOrWhiteSpace(pid))
            {
                throw new BridgeServiceException(
                    "PID_METADATA_INVALID",
                    "entity PID metadata contains an empty identifier"
                );
            }
            return pid;
        }
    }
}
