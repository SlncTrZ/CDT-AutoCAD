// NativeMetadataStore — schema-agnostic bounded metadata persistence over entity XRecord storage.
// Wing: code | Topic: native-g2-metadata | Updated: 2026-09-11 17:55

using System.Text;
using System.Text.Json;
using Autodesk.AutoCAD.DatabaseServices;

namespace CDT.AutoCAD.Bridge;

internal static class NativeMetadataStore
{
    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = null,
        WriteIndented = false,
    };

    internal static Dictionary<string, object?> Read(
        DBObject databaseObject,
        Transaction transaction
    )
    {
        Dictionary<string, JsonElement> raw = ReadJson(databaseObject, transaction);
        return raw.ToDictionary(
            pair => pair.Key,
            pair => (object?)pair.Value.Clone(),
            StringComparer.Ordinal
        );
    }

    internal static bool TryGet(
        DBObject databaseObject,
        Transaction transaction,
        string metadataNamespace,
        out JsonElement value
    )
    {
        Dictionary<string, JsonElement> raw = ReadJson(databaseObject, transaction);
        if (raw.TryGetValue(metadataNamespace, out JsonElement found))
        {
            value = found.Clone();
            return true;
        }
        value = default;
        return false;
    }

    internal static void SetNamespace(
        DBObject databaseObject,
        Transaction transaction,
        string metadataNamespace,
        JsonElement value
    )
    {
        Dictionary<string, JsonElement> raw = ReadJson(databaseObject, transaction);
        raw[metadataNamespace] = value.Clone();
        WriteJson(databaseObject, transaction, raw);
    }

    internal static void ReplaceAll(
        DBObject databaseObject,
        Transaction transaction,
        IReadOnlyDictionary<string, object?> metadata
    )
    {
        Dictionary<string, JsonElement> normalized = new(StringComparer.Ordinal);
        foreach ((string key, object? value) in metadata)
        {
            JsonElement element = JsonSerializer.SerializeToElement(value, JsonOptions);
            normalized[key] = element.Clone();
        }
        WriteJson(databaseObject, transaction, normalized);
    }

    internal static bool JsonEquals(JsonElement left, JsonElement right) =>
        string.Equals(
            SemanticFingerprint.CanonicalMetadataSortKey(left),
            SemanticFingerprint.CanonicalMetadataSortKey(right),
            StringComparison.Ordinal
        );

    internal static bool TryResolvePath(JsonElement root, string? path, out JsonElement value)
    {
        if (path is null)
        {
            value = root.Clone();
            return true;
        }
        JsonElement current = root;
        foreach (string segment in path.Split('.', StringSplitOptions.RemoveEmptyEntries))
        {
            if (current.ValueKind != JsonValueKind.Object
                || !current.TryGetProperty(segment, out JsonElement child))
            {
                value = default;
                return false;
            }
            current = child;
        }
        value = current.Clone();
        return true;
    }

    private static Dictionary<string, JsonElement> ReadJson(
        DBObject databaseObject,
        Transaction transaction
    )
    {
        if (databaseObject.ExtensionDictionary.IsNull)
        {
            return new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        }
        DBDictionary extensionDictionary = (DBDictionary)transaction.GetObject(
            databaseObject.ExtensionDictionary,
            OpenMode.ForRead
        );
        if (!extensionDictionary.Contains(BridgeConstants.MetadataRecordKey))
        {
            return new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        }
        Xrecord record = (Xrecord)transaction.GetObject(
            extensionDictionary.GetAt(BridgeConstants.MetadataRecordKey),
            OpenMode.ForRead
        );
        ResultBuffer? data = record.Data;
        if (data is null)
        {
            throw new BridgeServiceException("METADATA_CORRUPT", "metadata XRecord is empty");
        }
        TypedValue[] values = data.AsArray();
        if (values.Length > BridgeConstants.MaxMetadataXRecordValues)
        {
            throw new BridgeServiceException(
                "METADATA_CORRUPT",
                "metadata XRecord contains too many bounded chunks"
            );
        }
        if (values.Length < 2
            || values[0].TypeCode != (int)DxfCode.Int16
            || Convert.ToInt16(values[0].Value) != BridgeConstants.MetadataSchemaVersion)
        {
            throw new BridgeServiceException(
                "METADATA_CORRUPT",
                "metadata XRecord schema is invalid or unsupported"
            );
        }
        StringBuilder base64 = new();
        for (int index = 1; index < values.Length; index++)
        {
            if (values[index].TypeCode != (int)DxfCode.Text || values[index].Value is not string chunk)
            {
                throw new BridgeServiceException("METADATA_CORRUPT", "metadata XRecord chunk type is invalid");
            }
            base64.Append(chunk);
            if (base64.Length > BridgeConstants.MaxMetadataStoredBase64Chars)
            {
                throw new BridgeServiceException("METADATA_CORRUPT", "metadata XRecord exceeds bounded storage size");
            }
        }

        try
        {
            byte[] jsonBytes = Convert.FromBase64String(base64.ToString());
            if (jsonBytes.Length > BridgeConstants.MaxMetadataStoredJsonBytes)
            {
                throw new BridgeServiceException("METADATA_CORRUPT", "metadata JSON exceeds bounded storage size");
            }
            string json = StrictUtf8.GetString(jsonBytes);
            Dictionary<string, JsonElement>? parsed = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(
                json,
                JsonOptions
            );
            if (parsed is null)
            {
                throw new BridgeServiceException("METADATA_CORRUPT", "metadata JSON root is not an object");
            }
            return new Dictionary<string, JsonElement>(parsed, StringComparer.Ordinal);
        }
        catch (BridgeServiceException)
        {
            throw;
        }
        catch (Exception exc) when (exc is FormatException or DecoderFallbackException or JsonException)
        {
            throw new BridgeServiceException(
                "METADATA_CORRUPT",
                $"metadata XRecord cannot be decoded: {exc.GetType().Name}"
            );
        }
    }

    private static void WriteJson(
        DBObject databaseObject,
        Transaction transaction,
        IReadOnlyDictionary<string, JsonElement> metadata
    )
    {
        byte[] jsonBytes = JsonSerializer.SerializeToUtf8Bytes(metadata, JsonOptions);
        if (jsonBytes.Length > BridgeConstants.MaxMetadataStoredJsonBytes)
        {
            throw new BridgeServiceException(
                "METADATA_CAPACITY_EXCEEDED",
                "combined metadata on one entity exceeds the bounded storage size"
            );
        }
        string encoded = Convert.ToBase64String(jsonBytes);
        if (encoded.Length > BridgeConstants.MaxMetadataStoredBase64Chars)
        {
            throw new BridgeServiceException(
                "METADATA_CAPACITY_EXCEEDED",
                "combined metadata on one entity exceeds the bounded storage size"
            );
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
        List<TypedValue> values =
        [
            new TypedValue((int)DxfCode.Int16, BridgeConstants.MetadataSchemaVersion),
        ];
        for (int offset = 0; offset < encoded.Length; offset += BridgeConstants.MetadataXRecordChunkChars)
        {
            int length = Math.Min(BridgeConstants.MetadataXRecordChunkChars, encoded.Length - offset);
            values.Add(new TypedValue((int)DxfCode.Text, encoded.Substring(offset, length)));
        }
        using ResultBuffer payload = new(values.ToArray());
        if (extensionDictionary.Contains(BridgeConstants.MetadataRecordKey))
        {
            Xrecord record = (Xrecord)transaction.GetObject(
                extensionDictionary.GetAt(BridgeConstants.MetadataRecordKey),
                OpenMode.ForWrite
            );
            record.Data = payload;
            return;
        }
        Xrecord created = new() { Data = payload };
        extensionDictionary.SetAt(BridgeConstants.MetadataRecordKey, created);
        transaction.AddNewlyCreatedDBObject(created, true);
    }
}
