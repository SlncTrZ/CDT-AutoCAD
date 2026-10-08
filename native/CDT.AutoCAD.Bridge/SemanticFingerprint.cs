// SemanticFingerprint — versioned canonical document fingerprint for native parent-state checks.
// Wing: code | Topic: native-bridge-n5 | Updated: 2026-09-10 14:15

using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace CDT.AutoCAD.Bridge;

internal static class SemanticFingerprint
{
    private const string Namespace = "cdt-autocad-semantic";
    private const string ProfileSignature =
        "v1|cad-default-v1|linear=0.000001|angular=1E-9|scalar=1E-9";

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        WriteIndented = false,
    };

    internal static string Document(object payload)
    {
        string prefix = string.Join(
            '\0',
            Namespace,
            "v3",
            "document",
            ProfileSignature,
            "linear",
            string.Empty
        );
        string body = CanonicalJson(payload, 6);
        byte[] digest = SHA256.HashData(Encoding.UTF8.GetBytes(prefix + body));
        return "sha256:" + Convert.ToHexString(digest).ToLowerInvariant();
    }

    internal static string CanonicalLinearSortKey(object payload) => CanonicalJson(payload, 6);

    internal static string CanonicalScalarSortKey(object payload) => CanonicalJson(payload, 9);

    internal static string CanonicalMetadataSortKey(object payload) => CanonicalJson(payload, 9);

    private static string CanonicalJson(object payload, int decimalPlaces)
    {
        JsonElement root = JsonSerializer.SerializeToElement(payload, JsonOptions);
        StringBuilder builder = new();
        WriteCanonical(root, builder, decimalPlaces);
        return builder.ToString();
    }

    private static void WriteCanonical(
        JsonElement element,
        StringBuilder builder,
        int decimalPlaces
    )
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.Object:
                builder.Append('{');
                bool firstProperty = true;
                foreach (JsonProperty property in element.EnumerateObject().OrderBy(
                    item => item.Name,
                    StringComparer.Ordinal
                ))
                {
                    if (!firstProperty)
                    {
                        builder.Append(',');
                    }
                    firstProperty = false;
                    builder.Append(JsonSerializer.Serialize(property.Name, JsonOptions));
                    builder.Append(':');
                    WriteCanonical(property.Value, builder, decimalPlaces);
                }
                builder.Append('}');
                return;

            case JsonValueKind.Array:
                builder.Append('[');
                bool firstItem = true;
                foreach (JsonElement item in element.EnumerateArray())
                {
                    if (!firstItem)
                    {
                        builder.Append(',');
                    }
                    firstItem = false;
                    WriteCanonical(item, builder, decimalPlaces);
                }
                builder.Append(']');
                return;

            case JsonValueKind.Number:
                builder.Append("{\"$number\":");
                builder.Append(JsonSerializer.Serialize(RenderNumber(element, decimalPlaces), JsonOptions));
                builder.Append('}');
                return;

            case JsonValueKind.String:
                builder.Append(JsonSerializer.Serialize(element.GetString(), JsonOptions));
                return;

            case JsonValueKind.True:
                builder.Append("true");
                return;

            case JsonValueKind.False:
                builder.Append("false");
                return;

            case JsonValueKind.Null:
                builder.Append("null");
                return;

            default:
                throw new BridgeServiceException(
                    "CANONICALIZATION_FAILED",
                    "native semantic payload contains an unsupported JSON value"
                );
        }
    }

    private static string RenderNumber(JsonElement element, int decimalPlaces)
    {
        string raw = element.GetRawText();
        if (!decimal.TryParse(
            raw,
            NumberStyles.Float,
            CultureInfo.InvariantCulture,
            out decimal value
        ))
        {
            throw new BridgeServiceException(
                "CANONICALIZATION_FAILED",
                "native semantic number cannot be canonicalized"
            );
        }
        decimal quantized = decimal.Round(value, decimalPlaces, MidpointRounding.ToEven);
        if (quantized == decimal.Zero)
        {
            quantized = decimal.Zero;
        }
        return quantized.ToString($"F{decimalPlaces}", CultureInfo.InvariantCulture);
    }
}
