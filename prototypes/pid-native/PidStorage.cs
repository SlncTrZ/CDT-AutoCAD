// PidStorage — native DWG PID persistence candidate using NOD/extension-dictionary XRecords.
// Wing: code | Topic: semantic-state-n2 | Updated: 2026-09-10 13:55

using Autodesk.AutoCAD.DatabaseServices;

namespace CDT.AutoCAD.PidProbe;

internal static class PidStorage
{
    internal const string AppDictionaryKey = "SLNCTRZ_CDT";
    internal const string DocumentPidRecordKey = "DOCUMENT_PID";
    internal const string EntityPidRecordKey = "SLNCTRZ_CDT_PID";
    internal const string PrimaryHandleRecordKey = "PID_PROBE_PRIMARY_HANDLE";
    internal const short SchemaVersion = 1;

    internal static string NewDocumentPid() => $"doc:{Guid.NewGuid():D}";

    internal static string NewEntityPid() => $"pid:{Guid.NewGuid():D}";

    internal static string GetOrCreateDocumentPid(Database database, Transaction transaction)
    {
        DBDictionary appDictionary = GetOrCreateAppDictionary(database, transaction);
        string? existing = ReadStringRecord(appDictionary, DocumentPidRecordKey, transaction);
        if (!string.IsNullOrWhiteSpace(existing))
        {
            return existing;
        }

        string created = NewDocumentPid();
        WriteStringRecord(appDictionary, DocumentPidRecordKey, created, transaction);
        return created;
    }

    internal static void SetDocumentPid(
        Database database,
        Transaction transaction,
        string documentPid
    )
    {
        if (string.IsNullOrWhiteSpace(documentPid))
        {
            throw new ArgumentException("documentPid must not be empty", nameof(documentPid));
        }

        DBDictionary appDictionary = GetOrCreateAppDictionary(database, transaction);
        WriteStringRecord(appDictionary, DocumentPidRecordKey, documentPid, transaction);
    }

    internal static string? ReadDocumentPid(Database database, Transaction transaction)
    {
        DBDictionary namedObjects = (DBDictionary)transaction.GetObject(
            database.NamedObjectsDictionaryId,
            OpenMode.ForRead
        );
        if (!namedObjects.Contains(AppDictionaryKey))
        {
            return null;
        }

        DBDictionary appDictionary = (DBDictionary)transaction.GetObject(
            namedObjects.GetAt(AppDictionaryKey),
            OpenMode.ForRead
        );
        return ReadStringRecord(appDictionary, DocumentPidRecordKey, transaction);
    }

    internal static void RememberPrimaryHandle(
        Database database,
        Transaction transaction,
        Handle handle
    )
    {
        DBDictionary appDictionary = GetOrCreateAppDictionary(database, transaction);
        WriteStringRecord(
            appDictionary,
            PrimaryHandleRecordKey,
            handle.ToString(),
            transaction
        );
    }

    internal static ObjectId ResolvePrimaryEntityId(Database database, Transaction transaction)
    {
        DBDictionary namedObjects = (DBDictionary)transaction.GetObject(
            database.NamedObjectsDictionaryId,
            OpenMode.ForRead
        );
        if (!namedObjects.Contains(AppDictionaryKey))
        {
            return ObjectId.Null;
        }

        DBDictionary appDictionary = (DBDictionary)transaction.GetObject(
            namedObjects.GetAt(AppDictionaryKey),
            OpenMode.ForRead
        );
        string? handleText = ReadStringRecord(
            appDictionary,
            PrimaryHandleRecordKey,
            transaction
        );
        if (string.IsNullOrWhiteSpace(handleText))
        {
            return ObjectId.Null;
        }

        long handleValue = Convert.ToInt64(handleText, 16);
        return database.GetObjectId(false, new Handle(handleValue), 0);
    }

    internal static string GetOrCreateEntityPid(DBObject databaseObject, Transaction transaction)
    {
        string? existing = ReadEntityPid(databaseObject, transaction);
        if (!string.IsNullOrWhiteSpace(existing))
        {
            return existing;
        }

        string created = NewEntityPid();
        SetEntityPid(databaseObject, created, transaction);
        return created;
    }

    internal static string? ReadEntityPid(
        DBObject databaseObject,
        Transaction transaction,
        bool openErased = false
    )
    {
        if (databaseObject.ExtensionDictionary.IsNull)
        {
            return null;
        }

        DBDictionary extensionDictionary = (DBDictionary)transaction.GetObject(
            databaseObject.ExtensionDictionary,
            OpenMode.ForRead,
            openErased
        );
        return ReadStringRecord(
            extensionDictionary,
            EntityPidRecordKey,
            transaction,
            openErased
        );
    }

    internal static void SetEntityPid(
        DBObject databaseObject,
        string semanticPid,
        Transaction transaction
    )
    {
        if (string.IsNullOrWhiteSpace(semanticPid))
        {
            throw new ArgumentException("semanticPid must not be empty", nameof(semanticPid));
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
        WriteStringRecord(
            extensionDictionary,
            EntityPidRecordKey,
            semanticPid,
            transaction
        );
    }

    internal static IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> ScanPidIndex(
        Database database,
        Transaction transaction
    )
    {
        Dictionary<string, List<ObjectId>> mutable = new(StringComparer.Ordinal);
        BlockTable blockTable = (BlockTable)transaction.GetObject(
            database.BlockTableId,
            OpenMode.ForRead
        );

        foreach (ObjectId blockRecordId in blockTable)
        {
            BlockTableRecord blockRecord = (BlockTableRecord)transaction.GetObject(
                blockRecordId,
                OpenMode.ForRead
            );
            AddPidIndexEntry(mutable, blockRecord, blockRecordId, transaction);
            foreach (ObjectId objectId in blockRecord)
            {
                DBObject obj = transaction.GetObject(objectId, OpenMode.ForRead, false);
                AddPidIndexEntry(mutable, obj, objectId, transaction);
            }
        }

        return mutable.ToDictionary(
            item => item.Key,
            item => (IReadOnlyList<ObjectId>)item.Value.AsReadOnly(),
            StringComparer.Ordinal
        );
    }

    private static void AddPidIndexEntry(
        Dictionary<string, List<ObjectId>> index,
        DBObject obj,
        ObjectId objectId,
        Transaction transaction
    )
    {
        string? pid = ReadEntityPid(obj, transaction);
        if (string.IsNullOrWhiteSpace(pid))
        {
            return;
        }

        if (!index.TryGetValue(pid, out List<ObjectId>? ids))
        {
            ids = [];
            index.Add(pid, ids);
        }
        ids.Add(objectId);
    }

    private static DBDictionary GetOrCreateAppDictionary(
        Database database,
        Transaction transaction
    )
    {
        DBDictionary namedObjects = (DBDictionary)transaction.GetObject(
            database.NamedObjectsDictionaryId,
            OpenMode.ForRead
        );
        if (namedObjects.Contains(AppDictionaryKey))
        {
            return (DBDictionary)transaction.GetObject(
                namedObjects.GetAt(AppDictionaryKey),
                OpenMode.ForWrite
            );
        }

        namedObjects.UpgradeOpen();
        DBDictionary appDictionary = new();
        namedObjects.SetAt(AppDictionaryKey, appDictionary);
        transaction.AddNewlyCreatedDBObject(appDictionary, true);
        return appDictionary;
    }

    private static string? ReadStringRecord(
        DBDictionary dictionary,
        string key,
        Transaction transaction,
        bool openErased = false
    )
    {
        if (!dictionary.Contains(key))
        {
            return null;
        }

        Xrecord record = (Xrecord)transaction.GetObject(
            dictionary.GetAt(key),
            OpenMode.ForRead,
            openErased
        );
        ResultBuffer? data = record.Data;
        if (data is null)
        {
            return null;
        }

        using (data)
        {
            TypedValue[] values = data.AsArray();
            if (values.Length != 2
                || values[0].TypeCode != (int)DxfCode.Int16
                || Convert.ToInt16(values[0].Value) != SchemaVersion
                || values[1].TypeCode != (int)DxfCode.Text)
            {
                throw new InvalidDataException(
                    $"PID XRecord {key} has an unsupported or malformed schema"
                );
            }

            string? result = values[1].Value?.ToString();
            if (string.IsNullOrWhiteSpace(result))
            {
                throw new InvalidDataException($"PID XRecord {key} contains an empty identifier");
            }
            return result;
        }
    }

    private static void WriteStringRecord(
        DBDictionary dictionary,
        string key,
        string value,
        Transaction transaction
    )
    {
        using ResultBuffer payload = new(
            new TypedValue((int)DxfCode.Int16, SchemaVersion),
            new TypedValue((int)DxfCode.Text, value)
        );

        if (dictionary.Contains(key))
        {
            Xrecord existing = (Xrecord)transaction.GetObject(
                dictionary.GetAt(key),
                OpenMode.ForWrite
            );
            existing.Data = payload;
            return;
        }

        Xrecord created = new() { Data = payload };
        dictionary.SetAt(key, created);
        transaction.AddNewlyCreatedDBObject(created, true);
    }
}
