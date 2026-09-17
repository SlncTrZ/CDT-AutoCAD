// DocumentPidStore — explicit bounded document-lineage bootstrap for empty native current space.
// Wing: code | Topic: native-bridge-u1 | Updated: 2026-09-17 16:20

using Autodesk.AutoCAD.DatabaseServices;

namespace CDT.AutoCAD.Bridge;

internal sealed record DocumentPidInitialization(string DocumentPid, bool Initialized);

internal static class DocumentPidStore
{
    internal static DocumentPidInitialization InitializeEmptyCurrentSpace(Database database)
    {
        using Transaction transaction = database.TransactionManager.StartTransaction();
        BlockTableRecord currentSpace = (BlockTableRecord)transaction.GetObject(
            database.CurrentSpaceId,
            OpenMode.ForRead
        );
        if (currentSpace.Cast<ObjectId>().Any())
        {
            throw new BridgeServiceException(
                "DOCUMENT_NOT_EMPTY",
                "document identity bootstrap is limited to an empty current space"
            );
        }

        DBDictionary namedObjects = (DBDictionary)transaction.GetObject(
            database.NamedObjectsDictionaryId,
            OpenMode.ForWrite
        );
        DBDictionary appDictionary;
        if (namedObjects.Contains(BridgeConstants.AppDictionaryKey))
        {
            appDictionary = (DBDictionary)transaction.GetObject(
                namedObjects.GetAt(BridgeConstants.AppDictionaryKey),
                OpenMode.ForWrite
            );
        }
        else
        {
            appDictionary = new DBDictionary();
            namedObjects.SetAt(BridgeConstants.AppDictionaryKey, appDictionary);
            transaction.AddNewlyCreatedDBObject(appDictionary, true);
        }

        if (appDictionary.Contains(BridgeConstants.DocumentPidRecordKey))
        {
            Xrecord existing = (Xrecord)transaction.GetObject(
                appDictionary.GetAt(BridgeConstants.DocumentPidRecordKey),
                OpenMode.ForRead
            );
            string pid = ReadPid(existing);
            transaction.Commit();
            return new DocumentPidInitialization(pid, false);
        }

        string documentPid = $"doc:{Guid.NewGuid():D}";
        using ResultBuffer payload = new(
            new TypedValue((int)DxfCode.Int16, BridgeConstants.PidSchemaVersion),
            new TypedValue((int)DxfCode.Text, documentPid)
        );
        Xrecord record = new() { Data = payload };
        appDictionary.SetAt(BridgeConstants.DocumentPidRecordKey, record);
        transaction.AddNewlyCreatedDBObject(record, true);
        transaction.Commit();
        return new DocumentPidInitialization(documentPid, true);
    }

    private static string ReadPid(Xrecord record)
    {
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
