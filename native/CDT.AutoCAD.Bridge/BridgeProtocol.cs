// BridgeProtocol — strict bounded staged native request/response JSON contract.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:10

using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace CDT.AutoCAD.Bridge;

internal sealed record DocumentIdentityParams(Guid RuntimeDocumentId, string? DocumentPid);

internal sealed record LineMutationParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string ExpectedParentFp,
    string? SemanticPid,
    double[]? Start,
    double[]? End,
    string? FaultStage
);

internal sealed record BridgeRequest(
    Guid RequestId,
    string Operation,
    DocumentIdentityParams? DocumentIdentity,
    LineMutationParams? LineMutation
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
        "entity.create.line",
        "entity.update.line",
        "entity.delete.line",
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

    private static readonly HashSet<string> CreateLineFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "start",
        "end",
        "fault_stage",
    };

    private static readonly HashSet<string> UpdateLineFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "semantic_pid",
        "start",
        "end",
        "fault_stage",
    };

    private static readonly HashSet<string> DeleteLineFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "semantic_pid",
        "fault_stage",
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
            if (operation.StartsWith("entity.", StringComparison.Ordinal))
            {
                return ParseLineMutation(requestId, operation, parameters);
            }
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
                return new BridgeRequest(requestId, operation, null, null);
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
                new DocumentIdentityParams(runtimeDocumentId, documentPid),
                null
            );
        }
    }

    private static BridgeRequest ParseLineMutation(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        HashSet<string> allowed = operation switch
        {
            "entity.create.line" => CreateLineFields,
            "entity.update.line" => UpdateLineFields,
            "entity.delete.line" => DeleteLineFields,
            _ => throw new BridgeProtocolException(
                "UNSUPPORTED_OPERATION",
                "operation is not enabled",
                requestId
            ),
        };
        ValidateExactOrSubsetFields(parameters, allowed, "INVALID_PARAMS", requestId);
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string expectedParentFp = RequireString(
            parameters,
            "expected_parent_fp",
            "INVALID_PARAMS",
            requestId
        );
        if (!IsCanonicalFingerprint(expectedParentFp))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "expected_parent_fp must be a lowercase sha256 fingerprint",
                requestId
            );
        }

        string? semanticPid = null;
        if (!string.Equals(operation, "entity.create.line", StringComparison.Ordinal))
        {
            semanticPid = RequireString(parameters, "semantic_pid", "INVALID_PARAMS", requestId);
        }
        double[]? start = null;
        double[]? end = null;
        if (!string.Equals(operation, "entity.delete.line", StringComparison.Ordinal))
        {
            start = RequirePoint3(parameters, "start", requestId);
            end = RequirePoint3(parameters, "end", requestId);
            if (start.SequenceEqual(end))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "line start and end must differ",
                    requestId
                );
            }
        }

        string? faultStage = null;
        if (parameters.TryGetProperty("fault_stage", out JsonElement faultElement))
        {
            if (faultElement.ValueKind != JsonValueKind.String
                || !string.Equals(
                    faultElement.GetString(),
                    "after_apply_before_commit",
                    StringComparison.Ordinal
                ))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "fault_stage is not enabled",
                    requestId
                );
            }
            faultStage = "after_apply_before_commit";
        }

        return new BridgeRequest(
            requestId,
            operation,
            null,
            new LineMutationParams(
                runtimeDocumentId,
                documentPid,
                expectedParentFp,
                semanticPid,
                start,
                end,
                faultStage
            )
        );
    }

    private static bool IsCanonicalFingerprint(string value)
    {
        if (!value.StartsWith("sha256:", StringComparison.Ordinal) || value.Length != 71)
        {
            return false;
        }
        return value.AsSpan(7).ToArray().All(
            character => (character >= '0' && character <= '9')
                || (character >= 'a' && character <= 'f')
        );
    }

    private static double[] RequirePoint3(JsonElement element, string name, Guid requestId)
    {
        if (!element.TryGetProperty(name, out JsonElement value)
            || value.ValueKind != JsonValueKind.Array)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must be a three-coordinate array",
                requestId
            );
        }
        JsonElement[] items = value.EnumerateArray().ToArray();
        if (items.Length != 3)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must be a three-coordinate array",
                requestId
            );
        }
        double[] result = new double[3];
        for (int index = 0; index < result.Length; index++)
        {
            if (items[index].ValueKind != JsonValueKind.Number
                || !items[index].TryGetDouble(out double coordinate)
                || !double.IsFinite(coordinate))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    $"{name} coordinates must be finite numbers",
                    requestId
                );
            }
            result[index] = coordinate;
        }
        return result;
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
