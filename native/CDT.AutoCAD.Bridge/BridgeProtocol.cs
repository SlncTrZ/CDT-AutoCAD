// BridgeProtocol — strict bounded staged native request/response JSON contract.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:10

using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace CDT.AutoCAD.Bridge;

internal sealed record DocumentIdentityParams(Guid RuntimeDocumentId, string? DocumentPid);

internal sealed record BridgeRequest(
    Guid RequestId,
    string Operation,
    DocumentIdentityParams? DocumentIdentity
);

internal sealed record BridgeResponse(
    string Protocol,
    string? RequestId,
    bool Ok,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] object? Result,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)] BridgeError? Error
)
{
    internal static BridgeResponse Success(Guid requestId, object result) =>
        new(
            BridgeConstants.ProtocolVersion,
            requestId.ToString("D"),
            true,
            result,
            null
        );

    internal static BridgeResponse Failure(Guid? requestId, string code, string message) =>
        new(
            BridgeConstants.ProtocolVersion,
            requestId?.ToString("D"),
            false,
            null,
            new BridgeError(code, BoundMessage(message))
        );

    private static string BoundMessage(string message)
    {
        string safe = message ?? string.Empty;
        return safe.Length <= BridgeConstants.MaxErrorMessageChars
            ? safe
            : safe[..BridgeConstants.MaxErrorMessageChars];
    }
}

internal sealed record BridgeError(string Code, string Message);

internal sealed class BridgeProtocolException : Exception
{
    internal BridgeProtocolException(
        string code,
        string message,
        Guid? requestId = null
    ) : base(message)
    {
        Code = code;
        RequestId = requestId;
    }

    internal string Code { get; }
    internal Guid? RequestId { get; }
}

internal static class BridgeProtocol
{
    private static readonly HashSet<string> AllowedOperations = new(StringComparer.Ordinal)
    {
        "bridge.health",
        "bridge.documents.list",
        "bridge.document.identity",
        "bridge.document.snapshot",
    };

    private static readonly HashSet<string> EnvelopeFields = new(StringComparer.Ordinal)
    {
        "protocol",
        "request_id",
        "operation",
        "params",
    };

    private static readonly HashSet<string> IdentityFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
    };

    private static readonly JsonSerializerOptions ResponseOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = false,
    };

    internal static BridgeRequest ParseRequest(byte[] utf8Json)
    {
        string json;
        try
        {
            json = new UTF8Encoding(false, true).GetString(utf8Json);
        }
        catch (DecoderFallbackException)
        {
            throw new BridgeProtocolException("INVALID_UTF8", "request body is not valid UTF-8");
        }

        JsonDocument document;
        try
        {
            document = JsonDocument.Parse(
                json,
                new JsonDocumentOptions
                {
                    AllowTrailingCommas = false,
                    CommentHandling = JsonCommentHandling.Disallow,
                    MaxDepth = 16,
                }
            );
        }
        catch (JsonException)
        {
            throw new BridgeProtocolException("INVALID_JSON", "request body is not valid JSON");
        }

        using (document)
        {
            JsonElement root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
            {
                throw new BridgeProtocolException(
                    "INVALID_JSON_OBJECT",
                    "request JSON root must be an object"
                );
            }

            Guid? safeRequestId = TryReadCanonicalRequestId(root);
            ValidateExactFields(root, EnvelopeFields, "INVALID_REQUEST", safeRequestId);

            string protocol = RequireString(root, "protocol", "INVALID_REQUEST", safeRequestId);
            if (!string.Equals(protocol, BridgeConstants.ProtocolVersion, StringComparison.Ordinal))
            {
                throw new BridgeProtocolException(
                    "PROTOCOL_MISMATCH",
                    "unsupported native bridge protocol",
                    safeRequestId
                );
            }

            Guid requestId = RequireCanonicalGuid(
                root,
                "request_id",
                "INVALID_REQUEST_ID",
                null
            );
            string operation = RequireString(root, "operation", "INVALID_REQUEST", requestId);
            if (!AllowedOperations.Contains(operation))
            {
                throw new BridgeProtocolException(
                    "UNSUPPORTED_OPERATION",
                    "operation is not enabled",
                    requestId
                );
            }

            JsonElement parameters = RequireObject(root, "params", "INVALID_PARAMS", requestId);
            if (!string.Equals(operation, "bridge.document.identity", StringComparison.Ordinal)
                && !string.Equals(operation, "bridge.document.snapshot", StringComparison.Ordinal))
            {
                if (parameters.EnumerateObject().Any())
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        $"{operation} does not accept parameters",
                        requestId
                    );
                }
                return new BridgeRequest(requestId, operation, null);
            }

            ValidateExactOrSubsetFields(
                parameters,
                IdentityFields,
                "INVALID_PARAMS",
                requestId
            );
            if (!parameters.TryGetProperty("runtime_document_id", out _))
            {
                throw new BridgeProtocolException(
                    "RUNTIME_DOCUMENT_ID_REQUIRED",
                    "runtime_document_id is required; document_pid alone cannot select a live document",
                    requestId
                );
            }
            Guid runtimeDocumentId = RequireCanonicalGuid(
                parameters,
                "runtime_document_id",
                "INVALID_RUNTIME_DOCUMENT_ID",
                requestId
            );
            string? documentPid = null;
            if (parameters.TryGetProperty("document_pid", out JsonElement pidElement))
            {
                if (pidElement.ValueKind != JsonValueKind.String)
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "document_pid must be a non-empty string",
                        requestId
                    );
                }
                documentPid = pidElement.GetString();
                if (string.IsNullOrWhiteSpace(documentPid))
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "document_pid must be a non-empty string",
                        requestId
                    );
                }
            }

            return new BridgeRequest(
                requestId,
                operation,
                new DocumentIdentityParams(runtimeDocumentId, documentPid)
            );
        }
    }

    internal static byte[] SerializeResponse(BridgeResponse response)
    {
        return JsonSerializer.SerializeToUtf8Bytes(response, ResponseOptions);
    }

    private static Guid? TryReadCanonicalRequestId(JsonElement root)
    {
        if (!root.TryGetProperty("request_id", out JsonElement element)
            || element.ValueKind != JsonValueKind.String)
        {
            return null;
        }
        string? value = element.GetString();
        if (value is null || !Guid.TryParseExact(value, "D", out Guid parsed))
        {
            return null;
        }
        return string.Equals(value, parsed.ToString("D"), StringComparison.Ordinal)
            ? parsed
            : null;
    }

    private static void ValidateExactFields(
        JsonElement element,
        HashSet<string> expected,
        string code,
        Guid? requestId
    )
    {
        HashSet<string> actual = EnumerateUniqueFieldNames(element, code, requestId);
        if (!actual.SetEquals(expected))
        {
            throw new BridgeProtocolException(
                code,
                "request must contain exactly protocol, request_id, operation and params",
                requestId
            );
        }
    }

    private static void ValidateExactOrSubsetFields(
        JsonElement element,
        HashSet<string> allowed,
        string code,
        Guid? requestId
    )
    {
        HashSet<string> actual = EnumerateUniqueFieldNames(element, code, requestId);
        if (actual.Any(name => !allowed.Contains(name)))
        {
            throw new BridgeProtocolException(
                code,
                "document binding params contain unknown fields",
                requestId
            );
        }
    }

    private static HashSet<string> EnumerateUniqueFieldNames(
        JsonElement element,
        string code,
        Guid? requestId
    )
    {
        HashSet<string> names = new(StringComparer.Ordinal);
        foreach (JsonProperty property in element.EnumerateObject())
        {
            if (!names.Add(property.Name))
            {
                throw new BridgeProtocolException(
                    code,
                    "duplicate JSON fields are not allowed",
                    requestId
                );
            }
        }
        return names;
    }

    private static string RequireString(
        JsonElement element,
        string name,
        string code,
        Guid? requestId
    )
    {
        if (!element.TryGetProperty(name, out JsonElement value)
            || value.ValueKind != JsonValueKind.String)
        {
            throw new BridgeProtocolException(code, $"{name} must be a string", requestId);
        }
        string? text = value.GetString();
        if (string.IsNullOrWhiteSpace(text))
        {
            throw new BridgeProtocolException(code, $"{name} must not be empty", requestId);
        }
        return text;
    }

    private static JsonElement RequireObject(
        JsonElement element,
        string name,
        string code,
        Guid? requestId
    )
    {
        if (!element.TryGetProperty(name, out JsonElement value)
            || value.ValueKind != JsonValueKind.Object)
        {
            throw new BridgeProtocolException(code, $"{name} must be a JSON object", requestId);
        }
        return value;
    }

    private static Guid RequireCanonicalGuid(
        JsonElement element,
        string name,
        string code,
        Guid? requestId
    )
    {
        string value = RequireString(element, name, code, requestId);
        if (!Guid.TryParseExact(value, "D", out Guid parsed)
            || !string.Equals(value, parsed.ToString("D"), StringComparison.Ordinal))
        {
            throw new BridgeProtocolException(
                code,
                $"{name} must use lowercase canonical UUID form",
                requestId
            );
        }
        return parsed;
    }
}
