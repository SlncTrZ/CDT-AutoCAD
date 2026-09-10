// EntityPidStore — bounded production PID writes and current-space PID target resolution for N5.
// Wing: code | Topic: native-bridge-n5 | Updated: 2026-09-10 14:00

using Autodesk.AutoCAD.DatabaseServices;

namespace CDT.AutoCAD.Bridge;

internal static class EntityPidStore
{
    internal static string NewEntityPid() => $"pid:{Guid.NewGuid():D}";

    internal static void Set(DBObject databaseObject, string semanticPid, Transaction transaction)
    {
        if (string.IsNullOrWhiteSpace(semanticPid))
        {
            throw new BridgeServiceException("INVALID_PID", "semantic PID must not be empty");
        }
        if (databaseObject.ExtensionDictionary.IsNull)
        {
            if (!databaseObject.IsWriteEnabled)
            {
                databaseObject.UpgradeOpen();
            }
            databaseObject.CreateExtensionDictionary();
        }
        DBDictionary extensionDictionary = (DBDictionary)transaction.GetObject(
            databaseObject.ExtensionDictionary,
            OpenMode.ForWrite
        );
        using ResultBuffer payload = new(
            new TypedValue((int)DxfCode.Int16, BridgeConstants.PidSchemaVersion),
            new TypedValue((int)DxfCode.Text, semanticPid)
        );
        if (extensionDictionary.Contains(BridgeConstants.EntityPidRecordKey))
        {
            Xrecord record = (Xrecord)transaction.GetObject(
                extensionDictionary.GetAt(BridgeConstants.EntityPidRecordKey),
                OpenMode.ForWrite
            );
            record.Data = payload;
            return;
        }
        Xrecord created = new() { Data = payload };
        extensionDictionary.SetAt(BridgeConstants.EntityPidRecordKey, created);
        transaction.AddNewlyCreatedDBObject(created, true);
    }

    internal static ObjectId ResolveCurrentSpaceEntity(
        Database database,
        string semanticPid,
        Transaction transaction
    )
    {
        BlockTableRecord space = (BlockTableRecord)transaction.GetObject(
            database.CurrentSpaceId,
            OpenMode.ForRead
        );
        ObjectId match = ObjectId.Null;
        foreach (ObjectId objectId in space)
        {
            if (transaction.GetObject(objectId, OpenMode.ForRead, false) is not Entity entity)
            {
                continue;
            }
            string pid = EntityPidReader.ReadRequired(entity, transaction);
            if (!string.Equals(pid, semanticPid, StringComparison.Ordinal))
            {
                continue;
            }
            if (!match.IsNull)
            {
                throw new BridgeServiceException(
                    "DUPLICATE_SEMANTIC_PID",
                    "multiple current-space entities share the target semantic PID"
                );
            }
            match = objectId;
        }
        if (match.IsNull)
        {
            throw new BridgeServiceException(
                "ENTITY_NOT_FOUND",
                "target semantic PID is not present in the active current space"
            );
        }
        return match;
    }
}
