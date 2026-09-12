// BridgeProtocol — strict bounded staged native request/response JSON contract.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:10

using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace CDT.AutoCAD.Bridge;

internal sealed record DocumentIdentityParams(Guid RuntimeDocumentId, string? DocumentPid);

internal sealed record ViewportVisualStyleSetParams(
    Guid RuntimeDocumentId,
    string? DocumentPid,
    string VisualStyleHandle,
    string ExpectedCurrentHandle
);

internal sealed record LogicalBeginParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string ExpectedParentFp
);

internal sealed record LogicalBatchBinding(
    string CheckpointId,
    string CheckpointArtifactFp,
    string ExpectedRestoreFp
);

internal sealed record MetadataGetParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string SemanticPid,
    string Namespace
);

internal sealed record MetadataSetParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string ExpectedParentFp,
    string SemanticPid,
    string Namespace,
    JsonElement Value,
    string? FaultStage
);

internal sealed record MetadataQueryParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string Namespace,
    string? Path,
    JsonElement? MatchValue,
    int Limit
);

internal sealed record EntityMutationParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string ExpectedParentFp,
    string? SemanticPid,
    double[]? Start,
    double[]? End,
    double[]? Center,
    double? Radius,
    double? StartAngle,
    double? EndAngle,
    double[][]? Points,
    bool? Closed,
    string? FaultStage
);

internal sealed record BatchCreateEntitySpec(
    string Kind,
    double[]? Start,
    double[]? End,
    double[]? Center,
    double? Radius,
    double? StartAngle,
    double? EndAngle,
    double[][]? Points,
    bool? Closed
);

internal sealed record BatchCreateParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string ExpectedParentFp,
    BatchCreateEntitySpec[] Entities,
    string? FaultStage,
    LogicalBatchBinding? LogicalTransaction
);

internal sealed record BatchTransformSpec(
    string Kind,
    double[]? Delta,
    double[]? Center,
    double? Angle,
    double? Factor
);

internal sealed record BatchTransformParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string ExpectedParentFp,
    string[] SemanticPids,
    BatchTransformSpec Transform,
    string? FaultStage,
    LogicalBatchBinding? LogicalTransaction
);

internal sealed record BatchInsertBlockSpec(
    string DefinitionPid,
    double[] Position,
    double Rotation,
    double Scale
);

internal sealed record BatchInsertBlocksParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string ExpectedParentFp,
    BatchInsertBlockSpec[] Inserts,
    string? FaultStage,
    LogicalBatchBinding? LogicalTransaction
);

internal sealed record RecoveryResolveParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string CheckpointId,
    string CheckpointArtifactFp,
    string ExpectedRestoreFp,
    string Strategy
);

internal sealed record RecoveryFinalizeParams(
    Guid RuntimeDocumentId,
    string DocumentPid,
    string CheckpointId,
    string CheckpointArtifactFp,
    string AcceptedPostFp
);

internal sealed record BridgeRequest(
    Guid RequestId,
    string Operation,
    DocumentIdentityParams? DocumentIdentity,
    EntityMutationParams? Mutation,
    RecoveryResolveParams? RecoveryResolve = null,
    RecoveryFinalizeParams? RecoveryFinalize = null,
    BatchCreateParams? BatchCreate = null,
    BatchTransformParams? BatchTransform = null,
    BatchInsertBlocksParams? BatchInsertBlocks = null,
    MetadataGetParams? MetadataGet = null,
    MetadataSetParams? MetadataSet = null,
    MetadataQueryParams? MetadataQuery = null,
    LogicalBeginParams? LogicalBegin = null,
    ViewportVisualStyleSetParams? VisualStyleSet = null
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
        "bridge.document.state",
        "viewport.visual_style.get",
        "viewport.visual_style.set",
        "bridge.recovery.list",
        "bridge.recovery.resolve",
        "bridge.recovery.finalize",
        "bridge.logical.begin",
        "metadata.get",
        "metadata.set",
        "metadata.query",
        "entity.batch.create",
        "entity.batch.insert_blocks",
        "entity.batch.transform",
        "entity.create.line",
        "entity.update.line",
        "entity.delete.line",
        "entity.create.circle",
        "entity.update.circle",
        "entity.delete.circle",
        "entity.create.arc",
        "entity.update.arc",
        "entity.delete.arc",
        "entity.create.lwpolyline",
        "entity.update.lwpolyline",
        "entity.delete.lwpolyline",
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

    private static readonly HashSet<string> VisualStyleSetFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "visual_style_handle",
        "expected_current_handle",
    };

    private static readonly HashSet<string> LogicalBeginFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
    };

    private static readonly HashSet<string> LogicalBindingFields = new(StringComparer.Ordinal)
    {
        "checkpoint_id",
        "checkpoint_artifact_fp",
        "expected_restore_fp",
    };

    private static readonly HashSet<string> MetadataGetFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "semantic_pid",
        "namespace",
    };

    private static readonly HashSet<string> MetadataSetFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "semantic_pid",
        "namespace",
        "value",
        "fault_stage",
    };

    private static readonly HashSet<string> MetadataQueryFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "namespace",
        "path",
        "equals",
        "limit",
    };

    private static readonly HashSet<string> BatchCreateFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "entities",
        "fault_stage",
        "logical_transaction",
    };

    private static readonly HashSet<string> BatchInsertBlocksFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "inserts",
        "fault_stage",
        "logical_transaction",
    };

    private static readonly HashSet<string> BatchInsertBlockFields = new(StringComparer.Ordinal)
    {
        "definition_pid",
        "position",
        "rotation",
        "scale",
    };

    private static readonly HashSet<string> BatchTransformFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "semantic_pids",
        "transform",
        "fault_stage",
        "logical_transaction",
    };

    private static readonly HashSet<string> TranslateTransformFields = new(StringComparer.Ordinal)
    {
        "kind",
        "delta",
    };

    private static readonly HashSet<string> RotateTransformFields = new(StringComparer.Ordinal)
    {
        "kind",
        "center",
        "angle",
    };

    private static readonly HashSet<string> ScaleTransformFields = new(StringComparer.Ordinal)
    {
        "kind",
        "center",
        "factor",
    };

    private static readonly HashSet<string> BatchLineFields = new(StringComparer.Ordinal)
    {
        "kind",
        "start",
        "end",
    };

    private static readonly HashSet<string> BatchCircleFields = new(StringComparer.Ordinal)
    {
        "kind",
        "center",
        "radius",
    };

    private static readonly HashSet<string> BatchArcFields = new(StringComparer.Ordinal)
    {
        "kind",
        "center",
        "radius",
        "start_angle",
        "end_angle",
    };

    private static readonly HashSet<string> BatchPolylineFields = new(StringComparer.Ordinal)
    {
        "kind",
        "points",
        "closed",
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

    private static readonly HashSet<string> CreateCircleFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "center",
        "radius",
        "fault_stage",
    };

    private static readonly HashSet<string> UpdateCircleFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "semantic_pid",
        "center",
        "radius",
        "fault_stage",
    };

    private static readonly HashSet<string> CreateArcFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "center",
        "radius",
        "start_angle",
        "end_angle",
        "fault_stage",
    };

    private static readonly HashSet<string> UpdateArcFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "semantic_pid",
        "center",
        "radius",
        "start_angle",
        "end_angle",
        "fault_stage",
    };

    private static readonly HashSet<string> CreatePolylineFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "points",
        "closed",
        "fault_stage",
    };

    private static readonly HashSet<string> UpdatePolylineFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "expected_parent_fp",
        "semantic_pid",
        "points",
        "closed",
        "fault_stage",
    };

    private static readonly HashSet<string> RecoveryResolveFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "checkpoint_id",
        "checkpoint_artifact_fp",
        "expected_restore_fp",
        "strategy",
    };

    private static readonly HashSet<string> RecoveryFinalizeFields = new(StringComparer.Ordinal)
    {
        "runtime_document_id",
        "document_pid",
        "checkpoint_id",
        "checkpoint_artifact_fp",
        "accepted_post_fp",
    };

    private static readonly Regex ObjectHandleRegex = new(
        "^[0-9A-F]+$",
        RegexOptions.CultureInvariant | RegexOptions.NonBacktracking
    );
    private static readonly Regex MetadataNamespaceRegex = new(
        "^[a-z][a-z0-9_-]{0,31}(?:\\.[a-z0-9][a-z0-9_-]{0,31}){1,7}$",
        RegexOptions.CultureInvariant | RegexOptions.NonBacktracking
    );
    private static readonly Regex MetadataPathRegex = new(
        "^[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+){0,7}$",
        RegexOptions.CultureInvariant | RegexOptions.NonBacktracking
    );
    private static readonly string[] ReservedMetadataPrefixes =
    [
        "slnctrz.",
        "cdt.",
        "provider.",
    ];

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
            if (string.Equals(operation, "viewport.visual_style.set", StringComparison.Ordinal))
            {
                return ParseVisualStyleSet(requestId, operation, parameters);
            }
            if (string.Equals(operation, "bridge.logical.begin", StringComparison.Ordinal))
            {
                return ParseLogicalBegin(requestId, operation, parameters);
            }
            if (string.Equals(operation, "metadata.get", StringComparison.Ordinal))
            {
                return ParseMetadataGet(requestId, operation, parameters);
            }
            if (string.Equals(operation, "metadata.set", StringComparison.Ordinal))
            {
                return ParseMetadataSet(requestId, operation, parameters);
            }
            if (string.Equals(operation, "metadata.query", StringComparison.Ordinal))
            {
                return ParseMetadataQuery(requestId, operation, parameters);
            }
            if (string.Equals(operation, "entity.batch.create", StringComparison.Ordinal))
            {
                return ParseBatchCreate(requestId, operation, parameters);
            }
            if (string.Equals(operation, "entity.batch.insert_blocks", StringComparison.Ordinal))
            {
                return ParseBatchInsertBlocks(requestId, operation, parameters);
            }
            if (string.Equals(operation, "entity.batch.transform", StringComparison.Ordinal))
            {
                return ParseBatchTransform(requestId, operation, parameters);
            }
            if (operation.StartsWith("entity.", StringComparison.Ordinal))
            {
                return ParseEntityMutation(requestId, operation, parameters);
            }
            if (string.Equals(operation, "bridge.recovery.resolve", StringComparison.Ordinal))
            {
                return ParseRecoveryResolve(requestId, operation, parameters);
            }
            if (string.Equals(operation, "bridge.recovery.finalize", StringComparison.Ordinal))
            {
                return ParseRecoveryFinalize(requestId, operation, parameters);
            }
            if (!string.Equals(operation, "bridge.document.identity", StringComparison.Ordinal)
                && !string.Equals(operation, "bridge.document.snapshot", StringComparison.Ordinal)
                && !string.Equals(operation, "bridge.document.state", StringComparison.Ordinal)
                && !string.Equals(operation, "viewport.visual_style.get", StringComparison.Ordinal))
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

    private static BridgeRequest ParseVisualStyleSet(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        HashSet<string> fields = EnumerateUniqueFieldNames(parameters, "INVALID_PARAMS", requestId);
        if (fields.Any(name => !VisualStyleSetFields.Contains(name))
            || !fields.Contains("runtime_document_id")
            || !fields.Contains("visual_style_handle")
            || !fields.Contains("expected_current_handle"))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "visual style params must use the exact typed schema",
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
            if (pidElement.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(pidElement.GetString()))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "document_pid must be a non-empty string",
                    requestId
                );
            }
            documentPid = pidElement.GetString();
        }
        string visualStyleHandle = RequireString(
            parameters,
            "visual_style_handle",
            "INVALID_PARAMS",
            requestId
        );
        string expectedCurrentHandle = RequireString(
            parameters,
            "expected_current_handle",
            "INVALID_PARAMS",
            requestId
        );
        if (!ObjectHandleRegex.IsMatch(visualStyleHandle)
            || !ObjectHandleRegex.IsMatch(expectedCurrentHandle))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "visual style handles must be uppercase hexadecimal AutoCAD handles",
                requestId
            );
        }
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            VisualStyleSet: new ViewportVisualStyleSetParams(
                runtimeDocumentId,
                documentPid,
                visualStyleHandle,
                expectedCurrentHandle
            )
        );
    }

    private static BridgeRequest ParseLogicalBegin(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactFieldsWithMessage(
            parameters,
            LogicalBeginFields,
            "INVALID_PARAMS",
            requestId,
            "logical begin params must use the exact typed schema"
        );
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string expectedParentFp = RequireFingerprint(parameters, "expected_parent_fp", requestId);
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            LogicalBegin: new LogicalBeginParams(
                runtimeDocumentId,
                documentPid,
                expectedParentFp
            )
        );
    }

    private static LogicalBatchBinding? ParseLogicalBinding(
        JsonElement parameters,
        Guid requestId
    )
    {
        if (!parameters.TryGetProperty("logical_transaction", out JsonElement logical))
        {
            return null;
        }
        if (logical.ValueKind != JsonValueKind.Object)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "logical_transaction must be a checkpoint binding object",
                requestId
            );
        }
        ValidateExactFieldsWithMessage(
            logical,
            LogicalBindingFields,
            "INVALID_PARAMS",
            requestId,
            "logical_transaction must use the exact checkpoint binding schema"
        );
        return new LogicalBatchBinding(
            RequireCheckpointId(logical, "checkpoint_id", requestId),
            RequireFingerprint(logical, "checkpoint_artifact_fp", requestId),
            RequireFingerprint(logical, "expected_restore_fp", requestId)
        );
    }

    private static BridgeRequest ParseMetadataGet(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactFieldsWithMessage(
            parameters,
            MetadataGetFields,
            "INVALID_PARAMS",
            requestId,
            "metadata get params must use the exact typed schema"
        );
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        if (!parameters.TryGetProperty("semantic_pid", out JsonElement semanticPidElement))
        {
            throw new BridgeProtocolException("INVALID_PARAMS", "semantic_pid is required", requestId);
        }
        string semanticPid = RequireCanonicalEntityPid(semanticPidElement, requestId);
        string metadataNamespace = RequireMetadataNamespace(parameters, "namespace", requestId);
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            MetadataGet: new MetadataGetParams(
                runtimeDocumentId,
                documentPid,
                semanticPid,
                metadataNamespace
            )
        );
    }

    private static BridgeRequest ParseMetadataSet(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactOrSubsetFields(parameters, MetadataSetFields, "INVALID_PARAMS", requestId);
        string[] required =
        [
            "runtime_document_id",
            "document_pid",
            "expected_parent_fp",
            "semantic_pid",
            "namespace",
            "value",
        ];
        foreach (string field in required)
        {
            if (!parameters.TryGetProperty(field, out _))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "metadata set params are incomplete",
                    requestId
                );
            }
        }
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string expectedParentFp = RequireFingerprint(parameters, "expected_parent_fp", requestId);
        if (!parameters.TryGetProperty("semantic_pid", out JsonElement semanticPidElement))
        {
            throw new BridgeProtocolException("INVALID_PARAMS", "semantic_pid is required", requestId);
        }
        string semanticPid = RequireCanonicalEntityPid(semanticPidElement, requestId);
        string metadataNamespace = RequireMetadataNamespace(parameters, "namespace", requestId);
        JsonElement value = RequireMetadataValue(parameters, "value", requestId);
        string? faultStage = null;
        if (parameters.TryGetProperty("fault_stage", out JsonElement faultElement))
        {
            faultStage = faultElement.ValueKind == JsonValueKind.String ? faultElement.GetString() : null;
            if (string.IsNullOrEmpty(faultStage))
            {
                throw new BridgeProtocolException("INVALID_PARAMS", "fault_stage must be a string", requestId);
            }
        }
        if (faultStage is not null
            && !string.Equals(faultStage, "after_apply_before_commit", StringComparison.Ordinal))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "metadata set only enables the pre-commit fault stage",
                requestId
            );
        }
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            MetadataSet: new MetadataSetParams(
                runtimeDocumentId,
                documentPid,
                expectedParentFp,
                semanticPid,
                metadataNamespace,
                value,
                faultStage
            )
        );
    }

    private static BridgeRequest ParseMetadataQuery(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactOrSubsetFields(parameters, MetadataQueryFields, "INVALID_PARAMS", requestId);
        foreach (string field in new[] { "runtime_document_id", "document_pid", "namespace" })
        {
            if (!parameters.TryGetProperty(field, out _))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "metadata query params are incomplete",
                    requestId
                );
            }
        }
        bool hasPath = parameters.TryGetProperty("path", out JsonElement pathElement);
        bool hasEquals = parameters.TryGetProperty("equals", out JsonElement equalsElement);
        if (hasPath != hasEquals)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "metadata query path and equals must be supplied together",
                requestId
            );
        }
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string metadataNamespace = RequireMetadataNamespace(parameters, "namespace", requestId);
        string? path = null;
        JsonElement? equals = null;
        if (hasPath)
        {
            if (pathElement.ValueKind != JsonValueKind.String
                || string.IsNullOrWhiteSpace(pathElement.GetString())
                || !MetadataPathRegex.IsMatch(pathElement.GetString()!))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "metadata query path must be a dotted JSON object path",
                    requestId
                );
            }
            path = pathElement.GetString();
            ValidateMetadataValue(equalsElement, requestId);
            equals = equalsElement.Clone();
        }
        int limit = 200;
        if (parameters.TryGetProperty("limit", out JsonElement limitElement))
        {
            if (limitElement.ValueKind != JsonValueKind.Number
                || !limitElement.TryGetInt32(out limit)
                || limit < 1
                || limit > BridgeConstants.MaxMetadataQueryResults)
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    $"metadata query limit must be 1..{BridgeConstants.MaxMetadataQueryResults}",
                    requestId
                );
            }
        }
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            MetadataQuery: new MetadataQueryParams(
                runtimeDocumentId,
                documentPid,
                metadataNamespace,
                path,
                equals,
                limit
            )
        );
    }

    private static BridgeRequest ParseRecoveryResolve(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactOrSubsetFields(parameters, RecoveryResolveFields, "INVALID_PARAMS", requestId);
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string checkpointId = RequireCheckpointId(parameters, "checkpoint_id", requestId);
        string artifactFp = RequireFingerprint(parameters, "checkpoint_artifact_fp", requestId);
        string expectedRestoreFp = RequireFingerprint(parameters, "expected_restore_fp", requestId);
        string strategy = RequireString(parameters, "strategy", "INVALID_PARAMS", requestId);
        if (!string.Equals(strategy, "R1_COMPENSATE", StringComparison.Ordinal)
            && !string.Equals(strategy, "R2_CHECKPOINT_RESTORE", StringComparison.Ordinal))
        {
            throw new BridgeProtocolException("INVALID_PARAMS", "recovery strategy is not enabled", requestId);
        }
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            new RecoveryResolveParams(
                runtimeDocumentId,
                documentPid,
                checkpointId,
                artifactFp,
                expectedRestoreFp,
                strategy
            )
        );
    }

    private static BridgeRequest ParseRecoveryFinalize(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactOrSubsetFields(parameters, RecoveryFinalizeFields, "INVALID_PARAMS", requestId);
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string checkpointId = RequireCheckpointId(parameters, "checkpoint_id", requestId);
        string artifactFp = RequireFingerprint(parameters, "checkpoint_artifact_fp", requestId);
        string acceptedPostFp = RequireFingerprint(parameters, "accepted_post_fp", requestId);
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            null,
            new RecoveryFinalizeParams(
                runtimeDocumentId,
                documentPid,
                checkpointId,
                artifactFp,
                acceptedPostFp
            )
        );
    }

    private static BridgeRequest ParseBatchCreate(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactOrSubsetFields(parameters, BatchCreateFields, "INVALID_PARAMS", requestId);
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string expectedParentFp = RequireFingerprint(parameters, "expected_parent_fp", requestId);
        if (!parameters.TryGetProperty("entities", out JsonElement entitiesElement)
            || entitiesElement.ValueKind != JsonValueKind.Array)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "entities must be an array of typed primitives",
                requestId
            );
        }
        JsonElement[] rawEntities = entitiesElement.EnumerateArray().ToArray();
        if (rawEntities.Length < 1 || rawEntities.Length > BridgeConstants.MaxBatchChunkEntities)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"entities must contain 1..{BridgeConstants.MaxBatchChunkEntities} typed primitives",
                requestId
            );
        }
        BatchCreateEntitySpec[] entities = rawEntities
            .Select(item => ParseBatchCreateEntity(item, requestId))
            .ToArray();

        string? faultStage = null;
        if (parameters.TryGetProperty("fault_stage", out JsonElement faultElement))
        {
            faultStage = faultElement.ValueKind == JsonValueKind.String
                ? faultElement.GetString()
                : null;
            if (!string.Equals(faultStage, "after_apply_before_commit", StringComparison.Ordinal))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "batch create only enables the pre-commit fault stage",
                    requestId
                );
            }
        }
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            null,
            null,
            new BatchCreateParams(
                runtimeDocumentId,
                documentPid,
                expectedParentFp,
                entities,
                faultStage,
                ParseLogicalBinding(parameters, requestId)
            )
        );
    }

    private static BatchCreateEntitySpec ParseBatchCreateEntity(JsonElement item, Guid requestId)
    {
        if (item.ValueKind != JsonValueKind.Object)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "batch entity must be a JSON object",
                requestId
            );
        }
        string kind = RequireString(item, "kind", "INVALID_PARAMS", requestId);
        switch (kind)
        {
            case "line":
            {
                ValidateExactFields(item, BatchLineFields, "INVALID_PARAMS", requestId);
                double[] start = RequirePoint3(item, "start", requestId);
                double[] end = RequirePoint3(item, "end", requestId);
                if (start.SequenceEqual(end))
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "line start and end must differ",
                        requestId
                    );
                }
                return new BatchCreateEntitySpec(kind, start, end, null, null, null, null, null, null);
            }
            case "circle":
                ValidateExactFields(item, BatchCircleFields, "INVALID_PARAMS", requestId);
                return new BatchCreateEntitySpec(
                    kind,
                    null,
                    null,
                    RequirePoint3(item, "center", requestId),
                    RequirePositiveDouble(item, "radius", requestId),
                    null,
                    null,
                    null,
                    null
                );
            case "arc":
            {
                ValidateExactFields(item, BatchArcFields, "INVALID_PARAMS", requestId);
                double startAngle = RequireArcAngle(item, "start_angle", requestId);
                double endAngle = RequireArcAngle(item, "end_angle", requestId);
                if (Math.Abs(startAngle - endAngle) <= 1e-12)
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "arc start_angle and end_angle must differ",
                        requestId
                    );
                }
                return new BatchCreateEntitySpec(
                    kind,
                    null,
                    null,
                    RequirePoint3(item, "center", requestId),
                    RequirePositiveDouble(item, "radius", requestId),
                    startAngle,
                    endAngle,
                    null,
                    null
                );
            }
            case "lwpolyline":
            {
                ValidateExactFields(item, BatchPolylineFields, "INVALID_PARAMS", requestId);
                double[][] points = RequirePoint2Array(item, "points", requestId);
                bool closed = RequireBoolean(item, "closed", requestId);
                if (closed && (points.Length < 3 || SamePoint2(points[0], points[^1])))
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "closed polyline requires at least three vertices and must not repeat the first point",
                        requestId
                    );
                }
                return new BatchCreateEntitySpec(kind, null, null, null, null, null, null, points, closed);
            }
            default:
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "batch entity kind is not enabled",
                    requestId
                );
        }
    }

    private static BridgeRequest ParseBatchInsertBlocks(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactOrSubsetFields(parameters, BatchInsertBlocksFields, "INVALID_PARAMS", requestId);
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string expectedParentFp = RequireFingerprint(parameters, "expected_parent_fp", requestId);
        if (!parameters.TryGetProperty("inserts", out JsonElement insertsElement)
            || insertsElement.ValueKind != JsonValueKind.Array)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "inserts must be an array of typed block insert specs",
                requestId
            );
        }
        JsonElement[] rawInserts = insertsElement.EnumerateArray().ToArray();
        if (rawInserts.Length < 1 || rawInserts.Length > BridgeConstants.MaxBatchChunkEntities)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"inserts must contain 1..{BridgeConstants.MaxBatchChunkEntities} block insert specs",
                requestId
            );
        }
        BatchInsertBlockSpec[] inserts = rawInserts
            .Select(item => ParseBatchInsertBlock(item, requestId))
            .ToArray();
        string? faultStage = null;
        if (parameters.TryGetProperty("fault_stage", out JsonElement faultElement))
        {
            faultStage = faultElement.ValueKind == JsonValueKind.String
                ? faultElement.GetString()
                : null;
            if (!string.Equals(faultStage, "after_apply_before_commit", StringComparison.Ordinal))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "batch block insert only enables the pre-commit fault stage",
                    requestId
                );
            }
        }
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            null,
            null,
            null,
            null,
            new BatchInsertBlocksParams(
                runtimeDocumentId,
                documentPid,
                expectedParentFp,
                inserts,
                faultStage,
                ParseLogicalBinding(parameters, requestId)
            )
        );
    }

    private static BatchInsertBlockSpec ParseBatchInsertBlock(JsonElement item, Guid requestId)
    {
        if (item.ValueKind != JsonValueKind.Object)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "block insert must be a JSON object",
                requestId
            );
        }
        ValidateExactFields(item, BatchInsertBlockFields, "INVALID_PARAMS", requestId);
        if (!item.TryGetProperty("definition_pid", out JsonElement definitionPidElement))
        {
            throw new BridgeProtocolException("INVALID_PARAMS", "definition_pid is required", requestId);
        }
        string definitionPid = RequireCanonicalEntityPid(definitionPidElement, requestId);
        double[] position = RequirePoint3(item, "position", requestId);
        if (position[2] != 0.0)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "block insert position must be planar with z=0",
                requestId
            );
        }
        double rotation = RequireFiniteDouble(item, "rotation", requestId);
        if (Math.Abs(rotation) > Math.PI * 2.0)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "block insert rotation must be finite within [-2pi, 2pi]",
                requestId
            );
        }
        double scale = RequireFiniteDouble(item, "scale", requestId);
        if (scale < 1e-6 || scale > 1e6)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "block insert scale must be finite within [1e-6, 1e6]",
                requestId
            );
        }
        return new BatchInsertBlockSpec(definitionPid, position, rotation, scale);
    }

    private static BridgeRequest ParseBatchTransform(
        Guid requestId,
        string operation,
        JsonElement parameters
    )
    {
        ValidateExactOrSubsetFields(parameters, BatchTransformFields, "INVALID_PARAMS", requestId);
        Guid runtimeDocumentId = RequireCanonicalGuid(
            parameters,
            "runtime_document_id",
            "INVALID_RUNTIME_DOCUMENT_ID",
            requestId
        );
        string documentPid = RequireString(parameters, "document_pid", "INVALID_PARAMS", requestId);
        string expectedParentFp = RequireFingerprint(parameters, "expected_parent_fp", requestId);
        if (!parameters.TryGetProperty("semantic_pids", out JsonElement pidsElement)
            || pidsElement.ValueKind != JsonValueKind.Array)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "semantic_pids must be an array of canonical pid:<uuid> values",
                requestId
            );
        }
        JsonElement[] rawPids = pidsElement.EnumerateArray().ToArray();
        if (rawPids.Length < 1 || rawPids.Length > BridgeConstants.MaxBatchChunkEntities)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"semantic_pids must contain 1..{BridgeConstants.MaxBatchChunkEntities} targets",
                requestId
            );
        }
        string[] semanticPids = rawPids
            .Select(item => RequireCanonicalEntityPid(item, requestId))
            .ToArray();
        if (semanticPids.Distinct(StringComparer.Ordinal).Count() != semanticPids.Length)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "semantic_pids must be unique",
                requestId
            );
        }
        JsonElement transformElement = RequireObject(
            parameters,
            "transform",
            "INVALID_PARAMS",
            requestId
        );
        BatchTransformSpec transform = ParseBatchTransformSpec(transformElement, requestId);
        string? faultStage = null;
        if (parameters.TryGetProperty("fault_stage", out JsonElement faultElement))
        {
            faultStage = faultElement.ValueKind == JsonValueKind.String
                ? faultElement.GetString()
                : null;
            if (!string.Equals(faultStage, "after_apply_before_commit", StringComparison.Ordinal))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "batch transform only enables the pre-commit fault stage",
                    requestId
                );
            }
        }
        return new BridgeRequest(
            requestId,
            operation,
            null,
            null,
            null,
            null,
            null,
            new BatchTransformParams(
                runtimeDocumentId,
                documentPid,
                expectedParentFp,
                semanticPids,
                transform,
                faultStage,
                ParseLogicalBinding(parameters, requestId)
            )
        );
    }

    private static BatchTransformSpec ParseBatchTransformSpec(
        JsonElement transform,
        Guid requestId
    )
    {
        string kind = RequireString(transform, "kind", "INVALID_PARAMS", requestId);
        switch (kind)
        {
            case "translate":
            {
                ValidateExactFields(transform, TranslateTransformFields, "INVALID_PARAMS", requestId);
                double[] delta = RequirePoint3(transform, "delta", requestId);
                if (delta[2] != 0.0 || (delta[0] == 0.0 && delta[1] == 0.0))
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "translate must be a non-zero planar XY displacement with z=0",
                        requestId
                    );
                }
                return new BatchTransformSpec(kind, delta, null, null, null);
            }
            case "rotate_z":
            {
                ValidateExactFields(transform, RotateTransformFields, "INVALID_PARAMS", requestId);
                double[] center = RequirePoint3(transform, "center", requestId);
                double angle = RequireFiniteDouble(transform, "angle", requestId);
                if (center[2] != 0.0 || Math.Abs(angle) <= 1e-12 || Math.Abs(angle) > Math.PI * 2.0)
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "rotate_z requires center.z=0 and a non-zero finite angle within [-2pi, 2pi]",
                        requestId
                    );
                }
                return new BatchTransformSpec(kind, null, center, angle, null);
            }
            case "scale_uniform":
            {
                ValidateExactFields(transform, ScaleTransformFields, "INVALID_PARAMS", requestId);
                double[] center = RequirePoint3(transform, "center", requestId);
                double factor = RequireFiniteDouble(transform, "factor", requestId);
                if (center[2] != 0.0
                    || factor < 1e-6
                    || factor > 1e6
                    || Math.Abs(factor - 1.0) <= 1e-12)
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "scale_uniform requires center.z=0 and factor in [1e-6, 1e6] excluding 1",
                        requestId
                    );
                }
                return new BatchTransformSpec(kind, null, center, null, factor);
            }
            default:
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "transform kind is not enabled",
                    requestId
                );
        }
    }

    private static BridgeRequest ParseEntityMutation(
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
            "entity.create.circle" => CreateCircleFields,
            "entity.update.circle" => UpdateCircleFields,
            "entity.delete.circle" => DeleteLineFields,
            "entity.create.arc" => CreateArcFields,
            "entity.update.arc" => UpdateArcFields,
            "entity.delete.arc" => DeleteLineFields,
            "entity.create.lwpolyline" => CreatePolylineFields,
            "entity.update.lwpolyline" => UpdatePolylineFields,
            "entity.delete.lwpolyline" => DeleteLineFields,
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

        bool isCreate = operation.StartsWith("entity.create.", StringComparison.Ordinal);
        bool isDelete = operation.StartsWith("entity.delete.", StringComparison.Ordinal);
        string family = operation.Split('.')[2];
        string? semanticPid = isCreate
            ? null
            : RequireString(parameters, "semantic_pid", "INVALID_PARAMS", requestId);

        double[]? start = null;
        double[]? end = null;
        double[]? center = null;
        double? radius = null;
        double? startAngle = null;
        double? endAngle = null;
        double[][]? points = null;
        bool? closed = null;
        if (!isDelete)
        {
            switch (family)
            {
                case "line":
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
                    break;
                case "circle":
                    center = RequirePoint3(parameters, "center", requestId);
                    radius = RequirePositiveDouble(parameters, "radius", requestId);
                    break;
                case "arc":
                    center = RequirePoint3(parameters, "center", requestId);
                    radius = RequirePositiveDouble(parameters, "radius", requestId);
                    startAngle = RequireArcAngle(parameters, "start_angle", requestId);
                    endAngle = RequireArcAngle(parameters, "end_angle", requestId);
                    if (Math.Abs(startAngle.Value - endAngle.Value) <= 1e-12)
                    {
                        throw new BridgeProtocolException(
                            "INVALID_PARAMS",
                            "arc start_angle and end_angle must differ",
                            requestId
                        );
                    }
                    break;
                case "lwpolyline":
                    points = RequirePoint2Array(parameters, "points", requestId);
                    closed = RequireBoolean(parameters, "closed", requestId);
                    if (closed.Value && (points.Length < 3 || SamePoint2(points[0], points[^1])))
                    {
                        throw new BridgeProtocolException(
                            "INVALID_PARAMS",
                            "closed polyline requires at least three vertices and must not repeat the first point",
                            requestId
                        );
                    }
                    break;
                default:
                    throw new BridgeProtocolException(
                        "UNSUPPORTED_OPERATION",
                        "mutation operation family is not enabled",
                        requestId
                    );
            }
        }

        string? faultStage = null;
        if (parameters.TryGetProperty("fault_stage", out JsonElement faultElement))
        {
            string? requestedFault = faultElement.ValueKind == JsonValueKind.String
                ? faultElement.GetString()
                : null;
            if (requestedFault is not (
                "after_apply_before_commit"
                or "before_commit_add_stray"
                or "after_commit_corrupt_target"
                or "after_commit_add_stray"
            ))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "fault_stage is not enabled",
                    requestId
                );
            }
            faultStage = requestedFault;
        }

        return new BridgeRequest(
            requestId,
            operation,
            null,
            new EntityMutationParams(
                runtimeDocumentId,
                documentPid,
                expectedParentFp,
                semanticPid,
                start,
                end,
                center,
                radius,
                startAngle,
                endAngle,
                points,
                closed,
                faultStage
            )
        );
    }

    private static string RequireCheckpointId(JsonElement element, string name, Guid requestId)
    {
        string value = RequireString(element, name, "INVALID_PARAMS", requestId);
        if (!value.StartsWith("cp:", StringComparison.Ordinal)
            || !Guid.TryParseExact(value[3..], "D", out Guid parsed)
            || !string.Equals(value, "cp:" + parsed.ToString("D"), StringComparison.Ordinal))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must be canonical cp:<uuid>",
                requestId
            );
        }
        return value;
    }

    private static string RequireCanonicalEntityPid(JsonElement value, Guid requestId)
    {
        if (value.ValueKind != JsonValueKind.String)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "semantic_pids must contain canonical pid:<uuid> values",
                requestId
            );
        }
        string text = value.GetString() ?? string.Empty;
        if (!text.StartsWith("pid:", StringComparison.Ordinal)
            || text.Length != 40
            || !Guid.TryParseExact(text[4..], "D", out Guid parsed)
            || !string.Equals(text, "pid:" + parsed.ToString("D"), StringComparison.Ordinal)
            || text[18] != '4'
            || "89ab".IndexOf(text[23]) < 0)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "semantic_pids must contain canonical pid:<uuid> values",
                requestId
            );
        }
        return text;
    }

    private static string RequireFingerprint(JsonElement element, string name, Guid requestId)
    {
        string value = RequireString(element, name, "INVALID_PARAMS", requestId);
        if (!IsCanonicalFingerprint(value))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must be a lowercase sha256 fingerprint",
                requestId
            );
        }
        return value;
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

    private static double RequireFiniteDouble(
        JsonElement element,
        string name,
        Guid requestId
    )
    {
        if (!element.TryGetProperty(name, out JsonElement value)
            || value.ValueKind != JsonValueKind.Number
            || !value.TryGetDouble(out double result)
            || !double.IsFinite(result))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must be a finite number",
                requestId
            );
        }
        return result;
    }

    private static double RequirePositiveDouble(
        JsonElement element,
        string name,
        Guid requestId
    )
    {
        if (!element.TryGetProperty(name, out JsonElement value)
            || value.ValueKind != JsonValueKind.Number
            || !value.TryGetDouble(out double result)
            || !double.IsFinite(result)
            || result <= 0.0)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must be a positive finite number",
                requestId
            );
        }
        return result;
    }

    private static double RequireArcAngle(
        JsonElement element,
        string name,
        Guid requestId
    )
    {
        if (!element.TryGetProperty(name, out JsonElement value)
            || value.ValueKind != JsonValueKind.Number
            || !value.TryGetDouble(out double result)
            || !double.IsFinite(result)
            || result < 0.0
            || result >= Math.PI * 2.0)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must be in the half-open range [0, 2pi) radians",
                requestId
            );
        }
        return result;
    }

    private static double[][] RequirePoint2Array(
        JsonElement element,
        string name,
        Guid requestId
    )
    {
        if (!element.TryGetProperty(name, out JsonElement value)
            || value.ValueKind != JsonValueKind.Array)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must be an array of [x, y] pairs",
                requestId
            );
        }
        JsonElement[] items = value.EnumerateArray().ToArray();
        if (items.Length < 2 || items.Length > BridgeConstants.MaxSimplePolylineVertices)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                $"{name} must contain 2..{BridgeConstants.MaxSimplePolylineVertices} vertices",
                requestId
            );
        }
        double[][] result = new double[items.Length][];
        for (int index = 0; index < items.Length; index++)
        {
            if (items[index].ValueKind != JsonValueKind.Array)
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "each polyline point must be exactly [x, y]",
                    requestId
                );
            }
            JsonElement[] coordinates = items[index].EnumerateArray().ToArray();
            if (coordinates.Length != 2)
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "each polyline point must be exactly [x, y]",
                    requestId
                );
            }
            result[index] = new double[2];
            for (int coordinateIndex = 0; coordinateIndex < 2; coordinateIndex++)
            {
                if (coordinates[coordinateIndex].ValueKind != JsonValueKind.Number
                    || !coordinates[coordinateIndex].TryGetDouble(out double coordinate)
                    || !double.IsFinite(coordinate))
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "polyline coordinates must be finite numbers",
                        requestId
                    );
                }
                result[index][coordinateIndex] = coordinate;
            }
            if (index > 0 && SamePoint2(result[index - 1], result[index]))
            {
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "consecutive polyline points must differ",
                    requestId
                );
            }
        }
        return result;
    }

    private static bool RequireBoolean(JsonElement element, string name, Guid requestId)
    {
        if (!element.TryGetProperty(name, out JsonElement value)
            || (value.ValueKind != JsonValueKind.True && value.ValueKind != JsonValueKind.False))
        {
            throw new BridgeProtocolException("INVALID_PARAMS", $"{name} must be a boolean", requestId);
        }
        return value.GetBoolean();
    }

    private static bool SamePoint2(double[] left, double[] right)
    {
        return left.Length == 2
            && right.Length == 2
            && left[0] == right[0]
            && left[1] == right[1];
    }

    private static string RequireMetadataNamespace(
        JsonElement element,
        string name,
        Guid requestId
    )
    {
        string value = RequireString(element, name, "INVALID_PARAMS", requestId);
        if (value.Length > 128 || !MetadataNamespaceRegex.IsMatch(value))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "metadata namespace must be a lowercase dotted identifier such as customer.mechanical.v1",
                requestId
            );
        }
        if (ReservedMetadataPrefixes.Any(prefix => value.StartsWith(prefix, StringComparison.Ordinal)))
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "metadata namespace is reserved by the provider",
                requestId
            );
        }
        return value;
    }

    private static JsonElement RequireMetadataValue(
        JsonElement element,
        string name,
        Guid requestId
    )
    {
        if (!element.TryGetProperty(name, out JsonElement value))
        {
            throw new BridgeProtocolException("INVALID_PARAMS", $"{name} is required", requestId);
        }
        ValidateMetadataValue(value, requestId);
        return value.Clone();
    }

    private static void ValidateMetadataValue(JsonElement value, Guid requestId)
    {
        int keyCount = 0;
        ValidateMetadataNode(value, 1, ref keyCount, requestId);
        int encodedBytes = Encoding.UTF8.GetByteCount(value.GetRawText());
        if (encodedBytes > BridgeConstants.MaxMetadataJsonBytes)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "metadata JSON exceeds encoded-size limit",
                requestId
            );
        }
    }

    private static void ValidateMetadataNode(
        JsonElement value,
        int depth,
        ref int keyCount,
        Guid requestId
    )
    {
        if (depth > BridgeConstants.MaxMetadataDepth)
        {
            throw new BridgeProtocolException(
                "INVALID_PARAMS",
                "metadata JSON exceeds maximum nesting depth",
                requestId
            );
        }
        switch (value.ValueKind)
        {
            case JsonValueKind.Object:
                foreach (JsonProperty property in value.EnumerateObject())
                {
                    if (string.IsNullOrEmpty(property.Name) || property.Name.Length > 128)
                    {
                        throw new BridgeProtocolException(
                            "INVALID_PARAMS",
                            "metadata JSON object keys must be non-empty strings <=128 chars",
                            requestId
                        );
                    }
                    keyCount++;
                    if (keyCount > BridgeConstants.MaxMetadataKeys)
                    {
                        throw new BridgeProtocolException(
                            "INVALID_PARAMS",
                            "metadata JSON exceeds maximum key count",
                            requestId
                        );
                    }
                    ValidateMetadataNode(property.Value, depth + 1, ref keyCount, requestId);
                }
                return;
            case JsonValueKind.Array:
                foreach (JsonElement item in value.EnumerateArray())
                {
                    ValidateMetadataNode(item, depth + 1, ref keyCount, requestId);
                }
                return;
            case JsonValueKind.String:
                if ((value.GetString() ?? string.Empty).Length > BridgeConstants.MaxMetadataStringChars)
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "metadata JSON string exceeds maximum length",
                        requestId
                    );
                }
                return;
            case JsonValueKind.Number:
                if (!value.TryGetDecimal(out _))
                {
                    throw new BridgeProtocolException(
                        "INVALID_PARAMS",
                        "metadata JSON numbers must be finite and within native decimal range",
                        requestId
                    );
                }
                return;
            case JsonValueKind.True:
            case JsonValueKind.False:
            case JsonValueKind.Null:
                return;
            default:
                throw new BridgeProtocolException(
                    "INVALID_PARAMS",
                    "metadata value must be JSON-compatible",
                    requestId
                );
        }
    }

    private static void ValidateExactFieldsWithMessage(
        JsonElement element,
        HashSet<string> expected,
        string code,
        Guid? requestId,
        string message
    )
    {
        HashSet<string> actual = EnumerateUniqueFieldNames(element, code, requestId);
        if (!actual.SetEquals(expected))
        {
            throw new BridgeProtocolException(code, message, requestId);
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
