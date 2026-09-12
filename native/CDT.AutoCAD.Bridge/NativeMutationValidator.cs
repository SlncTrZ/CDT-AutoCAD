// NativeMutationValidator — deterministic in-transaction semantic validation for N7 typed mutations.
// Wing: code | Topic: native-bridge-n7 | Updated: 2026-09-10 20:34

namespace CDT.AutoCAD.Bridge;

internal static class NativeMutationValidator
{
    private const double Tolerance = 1e-9;

    internal static void Validate(
        string operation,
        string affectedPid,
        EntityMutationParams parameters,
        Dictionary<string, object?> before,
        Dictionary<string, object?> provisional
    )
    {
        Dictionary<string, Dictionary<string, object?>> beforeEntities = EntitiesByPid(before);
        Dictionary<string, Dictionary<string, object?>> afterEntities = EntitiesByPid(provisional);
        string[] created = afterEntities.Keys.Except(beforeEntities.Keys).Order().ToArray();
        string[] deleted = beforeEntities.Keys.Except(afterEntities.Keys).Order().ToArray();
        string[] modified = beforeEntities.Keys.Intersect(afterEntities.Keys)
            .Where(pid => EntityKey(beforeEntities[pid]) != EntityKey(afterEntities[pid]))
            .Order()
            .ToArray();

        string verb = operation.Split('.')[1];
        string family = operation.Split('.')[2];
        bool exactEffect = verb switch
        {
            "create" => created.SequenceEqual(new[] { affectedPid }) && modified.Length == 0 && deleted.Length == 0,
            "update" => modified.SequenceEqual(new[] { affectedPid }) && created.Length == 0 && deleted.Length == 0,
            "delete" => deleted.SequenceEqual(new[] { affectedPid }) && created.Length == 0 && modified.Length == 0,
            _ => false,
        };
        Require(exactEffect, "typed mutation produced an unexpected PID delta");
        Require(DocumentInvariantsEqual(before, provisional), "typed mutation changed invariant document/resource state");
        Require(!IntroducedDuplicate(beforeEntities, afterEntities), "typed mutation introduced duplicate geometry");

        if (verb == "delete")
        {
            return;
        }
        Require(afterEntities.TryGetValue(affectedPid, out Dictionary<string, object?>? target), "affected entity is missing from provisional state");
        string expectedType = family switch
        {
            "line" => "LINE",
            "circle" => "CIRCLE",
            "arc" => "ARC",
            "lwpolyline" => "LWPOLYLINE",
            _ => throw new BridgeServiceException("PROVISIONAL_VALIDATION_FAILED", "unsupported typed mutation family"),
        };
        Require(string.Equals(Convert.ToString(target!["entity_type"]), expectedType, StringComparison.Ordinal), "affected entity type differs from requested family");
        Require(GeometryMatches(family, target, parameters), "provisional geometry differs from requested geometry");

        if (verb == "update")
        {
            Require(beforeEntities.TryGetValue(affectedPid, out Dictionary<string, object?>? predecessor), "update predecessor entity is missing");
            Require(EnvelopeKey(predecessor!) == EnvelopeKey(target), "geometry-only update changed layer/style/hierarchy/type");
        }
    }

    internal static void ValidateMetadataSet(
        MetadataSetParams parameters,
        Dictionary<string, object?> before,
        Dictionary<string, object?> provisional
    )
    {
        Dictionary<string, Dictionary<string, object?>> beforeEntities = EntitiesByPid(before);
        Dictionary<string, Dictionary<string, object?>> afterEntities = EntitiesByPid(provisional);
        string[] created = afterEntities.Keys.Except(beforeEntities.Keys).Order().ToArray();
        string[] deleted = beforeEntities.Keys.Except(afterEntities.Keys).Order().ToArray();
        string[] modified = beforeEntities.Keys.Intersect(afterEntities.Keys)
            .Where(pid => EntityKey(beforeEntities[pid]) != EntityKey(afterEntities[pid]))
            .Order()
            .ToArray();

        Require(created.Length == 0 && deleted.Length == 0, "metadata set changed entity identity membership");
        Require(
            modified.SequenceEqual(new[] { parameters.SemanticPid }),
            "metadata set modified an unexpected semantic PID"
        );
        Require(
            DocumentInvariantsEqual(before, provisional),
            "metadata set changed invariant document/resource state"
        );
        Require(
            beforeEntities.TryGetValue(parameters.SemanticPid, out Dictionary<string, object?>? predecessor),
            "metadata predecessor entity is missing"
        );
        Require(
            afterEntities.TryGetValue(parameters.SemanticPid, out Dictionary<string, object?>? target),
            "metadata target entity is missing"
        );
        Require(
            EntityWithoutMetadataKey(predecessor!) == EntityWithoutMetadataKey(target!),
            "metadata set changed non-metadata entity state"
        );
        if (target!["metadata"] is not Dictionary<string, object?> metadata
            || !metadata.TryGetValue(parameters.Namespace, out object? actualValue))
        {
            throw new BridgeServiceException(
                "PROVISIONAL_VALIDATION_FAILED",
                "metadata namespace is missing from provisional semantic state"
            );
        }
        Require(
            SemanticFingerprint.CanonicalMetadataSortKey(actualValue!)
                == SemanticFingerprint.CanonicalMetadataSortKey(parameters.Value),
            "provisional metadata differs from requested value"
        );
    }

    internal static void ValidateBatchCreate(
        IReadOnlyList<string> affectedPids,
        BatchCreateParams parameters,
        Dictionary<string, object?> before,
        Dictionary<string, object?> provisional
    )
    {
        if (affectedPids.Count != parameters.Entities.Length)
        {
            throw new BridgeServiceException(
                "PROVISIONAL_VALIDATION_FAILED",
                "batch create PID count differs from requested entity count"
            );
        }

        Dictionary<string, Dictionary<string, object?>> beforeEntities = EntitiesByPid(before);
        Dictionary<string, Dictionary<string, object?>> afterEntities = EntitiesByPid(provisional);
        string[] created = afterEntities.Keys.Except(beforeEntities.Keys).Order().ToArray();
        string[] expectedCreated = affectedPids.Order(StringComparer.Ordinal).ToArray();
        string[] deleted = beforeEntities.Keys.Except(afterEntities.Keys).Order().ToArray();
        string[] modified = beforeEntities.Keys.Intersect(afterEntities.Keys)
            .Where(pid => EntityKey(beforeEntities[pid]) != EntityKey(afterEntities[pid]))
            .Order()
            .ToArray();

        Require(
            created.SequenceEqual(expectedCreated) && modified.Length == 0 && deleted.Length == 0,
            "batch create produced an unexpected PID delta"
        );
        Require(
            DocumentInvariantsEqual(before, provisional),
            "batch create changed invariant document/resource state"
        );
        Require(
            !IntroducedDuplicate(beforeEntities, afterEntities),
            "batch create introduced duplicate geometry"
        );

        for (int index = 0; index < affectedPids.Count; index++)
        {
            string pid = affectedPids[index];
            BatchCreateEntitySpec spec = parameters.Entities[index];
            Require(
                afterEntities.TryGetValue(pid, out Dictionary<string, object?>? target),
                "batch-created entity is missing from provisional state"
            );
            string expectedType = spec.Kind switch
            {
                "line" => "LINE",
                "circle" => "CIRCLE",
                "arc" => "ARC",
                "lwpolyline" => "LWPOLYLINE",
                _ => throw new BridgeServiceException(
                    "PROVISIONAL_VALIDATION_FAILED",
                    "unsupported batch create family"
                ),
            };
            Require(
                string.Equals(Convert.ToString(target!["entity_type"]), expectedType, StringComparison.Ordinal),
                "batch-created entity type differs from requested family"
            );
            Require(
                GeometryMatches(spec, target),
                "batch-created provisional geometry differs from requested geometry"
            );
        }
    }

    internal static void ValidateBatchInsertBlocks(
        IReadOnlyList<string> affectedPids,
        BatchInsertBlocksParams parameters,
        Dictionary<string, object?> before,
        Dictionary<string, object?> provisional
    )
    {
        if (affectedPids.Count != parameters.Inserts.Length)
        {
            throw new BridgeServiceException(
                "PROVISIONAL_VALIDATION_FAILED",
                "block insert PID count differs from requested insert count"
            );
        }
        Dictionary<string, Dictionary<string, object?>> beforeEntities = EntitiesByPid(before);
        Dictionary<string, Dictionary<string, object?>> afterEntities = EntitiesByPid(provisional);
        string[] created = afterEntities.Keys.Except(beforeEntities.Keys).Order().ToArray();
        string[] expectedCreated = affectedPids.Order(StringComparer.Ordinal).ToArray();
        string[] deleted = beforeEntities.Keys.Except(afterEntities.Keys).Order().ToArray();
        string[] modified = beforeEntities.Keys.Intersect(afterEntities.Keys)
            .Where(pid => EntityKey(beforeEntities[pid]) != EntityKey(afterEntities[pid]))
            .Order()
            .ToArray();

        Require(
            created.SequenceEqual(expectedCreated) && deleted.Length == 0 && modified.Length == 0,
            "block insert batch produced an unexpected semantic PID delta"
        );
        Require(
            DocumentInvariantsEqual(before, provisional),
            "block insert batch changed invariant document/resource state"
        );
        Require(
            !IntroducedDuplicate(beforeEntities, afterEntities),
            "block insert batch introduced duplicate geometry"
        );

        for (int index = 0; index < affectedPids.Count; index++)
        {
            BatchInsertBlockSpec spec = parameters.Inserts[index];
            Require(
                beforeEntities.TryGetValue(spec.DefinitionPid, out Dictionary<string, object?>? definition),
                "block definition PID is not visible in predecessor semantic state"
            );
            Require(
                string.Equals(
                    Convert.ToString(definition!["entity_type"]),
                    "BLOCK_DEFINITION",
                    StringComparison.Ordinal
                ),
                "definition_pid does not identify a block definition"
            );
            Require(
                afterEntities.TryGetValue(affectedPids[index], out Dictionary<string, object?>? inserted),
                "inserted block reference is missing from provisional state"
            );
            Require(
                BlockInsertMatches(definition!, inserted!, spec),
                "inserted block reference differs from requested definition/transform"
            );
        }
    }

    internal static void ValidateBatchTransform(
        IReadOnlyList<string> affectedPids,
        BatchTransformParams parameters,
        Dictionary<string, object?> before,
        Dictionary<string, object?> provisional
    )
    {
        Dictionary<string, Dictionary<string, object?>> beforeEntities = EntitiesByPid(before);
        Dictionary<string, Dictionary<string, object?>> afterEntities = EntitiesByPid(provisional);
        HashSet<string> targets = new(affectedPids, StringComparer.Ordinal);
        string[] created = afterEntities.Keys.Except(beforeEntities.Keys).Order().ToArray();
        string[] deleted = beforeEntities.Keys.Except(afterEntities.Keys).Order().ToArray();
        string[] modified = beforeEntities.Keys.Intersect(afterEntities.Keys)
            .Where(pid => EntityKey(beforeEntities[pid]) != EntityKey(afterEntities[pid]))
            .Order()
            .ToArray();

        Require(created.Length == 0 && deleted.Length == 0, "batch transform changed entity identity membership");
        Require(
            modified.All(pid => targets.Contains(pid)),
            "batch transform modified a non-target semantic PID"
        );
        Require(
            DocumentInvariantsEqual(before, provisional),
            "batch transform changed invariant document/resource state"
        );
        Require(
            !IntroducedDuplicate(beforeEntities, afterEntities),
            "batch transform introduced duplicate geometry"
        );

        foreach (string pid in affectedPids)
        {
            Require(beforeEntities.TryGetValue(pid, out Dictionary<string, object?>? predecessor), "batch transform predecessor is missing");
            Require(afterEntities.TryGetValue(pid, out Dictionary<string, object?>? target), "batch transform target is missing");
            Require(EnvelopeKey(predecessor!) == EnvelopeKey(target!), "batch transform changed layer/style/hierarchy/type");
            Require(
                GeometryMatchesTransform(predecessor!, target!, parameters.Transform),
                "batch transform provisional geometry differs from requested transform"
            );
        }
    }

    private static Dictionary<string, Dictionary<string, object?>> EntitiesByPid(Dictionary<string, object?> snapshot)
    {
        if (snapshot["entities"] is not List<Dictionary<string, object?>> entities)
        {
            throw new BridgeServiceException("PROVISIONAL_VALIDATION_FAILED", "semantic snapshot entity collection is invalid");
        }
        return entities.ToDictionary(item => Convert.ToString(item["semantic_pid"])!, StringComparer.Ordinal);
    }

    private static string EntityWithoutMetadataKey(Dictionary<string, object?> entity)
    {
        Dictionary<string, object?> semantic = new(entity, StringComparer.Ordinal);
        semantic.Remove("native_handle");
        semantic.Remove("metadata");
        return SemanticFingerprint.CanonicalLinearSortKey(semantic);
    }

    private static string EntityKey(Dictionary<string, object?> entity)
    {
        Dictionary<string, object?> semantic = new(entity, StringComparer.Ordinal);
        semantic.Remove("native_handle");
        return SemanticFingerprint.CanonicalLinearSortKey(semantic);
    }

    private static string EnvelopeKey(Dictionary<string, object?> entity)
    {
        Dictionary<string, object?> envelope = new()
        {
            ["entity_type"] = entity["entity_type"],
            ["layer"] = entity["layer"],
            ["style"] = entity["style"],
            ["hierarchy"] = entity["hierarchy"],
        };
        return SemanticFingerprint.CanonicalScalarSortKey(envelope);
    }

    private static bool DocumentInvariantsEqual(
        Dictionary<string, object?> before,
        Dictionary<string, object?> after
    )
    {
        if (!Equals(before["schema_version"], after["schema_version"])
            || !Equals(before["document_pid"], after["document_pid"]))
        {
            return false;
        }
        if (before["document"] is not Dictionary<string, object?> beforeDocument
            || after["document"] is not Dictionary<string, object?> afterDocument)
        {
            return false;
        }
        foreach (string field in new[] { "units", "current_space" })
        {
            if (!Equals(beforeDocument[field], afterDocument[field]))
            {
                return false;
            }
        }
        return SemanticFingerprint.CanonicalScalarSortKey(before["styles"]!)
            == SemanticFingerprint.CanonicalScalarSortKey(after["styles"]!);
    }

    private static bool IntroducedDuplicate(
        Dictionary<string, Dictionary<string, object?>> before,
        Dictionary<string, Dictionary<string, object?>> after
    )
    {
        Dictionary<string, HashSet<string>> beforeGroups = GeometryGroups(before);
        Dictionary<string, HashSet<string>> afterGroups = GeometryGroups(after);
        foreach ((string key, HashSet<string> afterPids) in afterGroups)
        {
            if (afterPids.Count < 2)
            {
                continue;
            }
            beforeGroups.TryGetValue(key, out HashSet<string>? beforePids);
            beforePids ??= [];
            if (afterPids.Except(beforePids).Any())
            {
                return true;
            }
        }
        return false;
    }

    private static Dictionary<string, HashSet<string>> GeometryGroups(
        Dictionary<string, Dictionary<string, object?>> entities
    )
    {
        Dictionary<string, HashSet<string>> groups = new(StringComparer.Ordinal);
        foreach ((string pid, Dictionary<string, object?> entity) in entities)
        {
            Dictionary<string, object?> payload = new()
            {
                ["type"] = entity["entity_type"],
                ["geometry"] = entity["geometry"],
            };
            string key = SemanticFingerprint.CanonicalLinearSortKey(payload);
            if (!groups.TryGetValue(key, out HashSet<string>? pids))
            {
                pids = new HashSet<string>(StringComparer.Ordinal);
                groups[key] = pids;
            }
            pids.Add(pid);
        }
        return groups;
    }

    private static bool GeometryMatches(
        BatchCreateEntitySpec spec,
        Dictionary<string, object?> entity
    )
    {
        if (entity["geometry"] is not Dictionary<string, object?> geometry)
        {
            return false;
        }
        return spec.Kind switch
        {
            "line" => SameLine(geometry, spec.Start, spec.End),
            "circle" => SameCircle(geometry, spec.Center, spec.Radius),
            "arc" => SameArc(geometry, spec.Center, spec.Radius, spec.StartAngle, spec.EndAngle),
            "lwpolyline" => SamePolyline(geometry, spec.Points, spec.Closed),
            _ => false,
        };
    }

    private static bool GeometryMatchesTransform(
        Dictionary<string, object?> predecessor,
        Dictionary<string, object?> target,
        BatchTransformSpec transform
    )
    {
        if (predecessor["geometry"] is not Dictionary<string, object?> beforeGeometry
            || target["geometry"] is not Dictionary<string, object?> afterGeometry)
        {
            return false;
        }
        string type = Convert.ToString(predecessor["entity_type"]) ?? string.Empty;
        switch (type)
        {
            case "LINE":
                return beforeGeometry["start"] is double[] start
                    && beforeGeometry["end"] is double[] end
                    && SameLine(
                        afterGeometry,
                        TransformPoint3(start, transform),
                        TransformPoint3(end, transform)
                    );
            case "CIRCLE":
                return beforeGeometry["center"] is double[] circleCenter
                    && Number(beforeGeometry["radius"], out double circleRadius)
                    && SameCircle(
                        afterGeometry,
                        TransformPoint3(circleCenter, transform),
                        circleRadius * ScaleFactor(transform)
                    );
            case "ARC":
                return beforeGeometry["center"] is double[] arcCenter
                    && Number(beforeGeometry["radius"], out double arcRadius)
                    && Number(beforeGeometry["start_angle"], out double startAngle)
                    && Number(beforeGeometry["end_angle"], out double endAngle)
                    && SameArc(
                        afterGeometry,
                        TransformPoint3(arcCenter, transform),
                        arcRadius * ScaleFactor(transform),
                        NormalizeAngle(startAngle + RotationAngle(transform)),
                        NormalizeAngle(endAngle + RotationAngle(transform))
                    );
            case "LWPOLYLINE":
                if (beforeGeometry["vertices"] is not List<Dictionary<string, object?>> vertices
                    || beforeGeometry["closed"] is not bool closed)
                {
                    return false;
                }
                double[][] points = vertices
                    .Select(vertex => vertex["point"] is double[] point
                        ? TransformPoint2(point, transform)
                        : Array.Empty<double>())
                    .ToArray();
                return points.All(point => point.Length == 2)
                    && SamePolyline(afterGeometry, points, closed);
            case "3DSOLID":
                return SolidMatchesTranslation(beforeGeometry, afterGeometry, transform);
            default:
                return false;
        }
    }

    private static bool SolidMatchesTranslation(
        Dictionary<string, object?> before,
        Dictionary<string, object?> after,
        BatchTransformSpec transform
    )
    {
        if (transform.Kind != "translate"
            || !Equals(before["solid_fingerprint_schema_version"], after["solid_fingerprint_schema_version"])
            || before["centroid"] is not double[] beforeCentroid
            || after["centroid"] is not double[] afterCentroid
            || !Same3(afterCentroid, TransformPoint3(beforeCentroid, transform))
            || !Number(before["volume"], out double beforeVolume)
            || !Number(after["volume"], out double afterVolume)
            || !Nearly(beforeVolume, afterVolume)
            || before["geometric_extents"] is not Dictionary<string, object?> beforeExtents
            || after["geometric_extents"] is not Dictionary<string, object?> afterExtents
            || beforeExtents["min"] is not double[] beforeMin
            || beforeExtents["max"] is not double[] beforeMax
            || afterExtents["min"] is not double[] afterMin
            || afterExtents["max"] is not double[] afterMax
            || !Same3(afterMin, TransformPoint3(beforeMin, transform))
            || !Same3(afterMax, TransformPoint3(beforeMax, transform))
            || before["principal_moments"] is not double[] beforePrincipal
            || after["principal_moments"] is not double[] afterPrincipal
            || !Same3(beforePrincipal, afterPrincipal))
        {
            return false;
        }
        return true;
    }

    private static double[] TransformPoint3(double[] point, BatchTransformSpec transform)
    {
        if (point.Length != 3)
        {
            return [];
        }
        double[] xy = TransformPoint2([point[0], point[1]], transform);
        return xy.Length == 2 ? [xy[0], xy[1], point[2]] : [];
    }

    private static double[] TransformPoint2(double[] point, BatchTransformSpec transform)
    {
        if (point.Length < 2)
        {
            return [];
        }
        double x = point[0];
        double y = point[1];
        return transform.Kind switch
        {
            "translate" => [x + transform.Delta![0], y + transform.Delta[1]],
            "rotate_z" => RotatePoint(x, y, transform.Center!, transform.Angle!.Value),
            "scale_uniform" => ScalePoint(x, y, transform.Center!, transform.Factor!.Value),
            _ => [],
        };
    }

    private static double[] RotatePoint(double x, double y, double[] center, double angle)
    {
        double dx = x - center[0];
        double dy = y - center[1];
        double cosine = Math.Cos(angle);
        double sine = Math.Sin(angle);
        return [
            center[0] + dx * cosine - dy * sine,
            center[1] + dx * sine + dy * cosine,
        ];
    }

    private static double[] ScalePoint(double x, double y, double[] center, double factor) =>
        [center[0] + (x - center[0]) * factor, center[1] + (y - center[1]) * factor];

    private static double ScaleFactor(BatchTransformSpec transform) =>
        string.Equals(transform.Kind, "scale_uniform", StringComparison.Ordinal)
            ? transform.Factor!.Value
            : 1.0;

    private static double RotationAngle(BatchTransformSpec transform) =>
        string.Equals(transform.Kind, "rotate_z", StringComparison.Ordinal)
            ? transform.Angle!.Value
            : 0.0;

    private static double NormalizeAngle(double angle)
    {
        double result = angle % (Math.PI * 2.0);
        return result < 0.0 ? result + Math.PI * 2.0 : result;
    }

    private static bool GeometryMatches(
        string family,
        Dictionary<string, object?> entity,
        EntityMutationParams parameters
    )
    {
        if (entity["geometry"] is not Dictionary<string, object?> geometry)
        {
            return false;
        }
        return family switch
        {
            "line" => SameLine(geometry, parameters.Start, parameters.End),
            "circle" => SameCircle(geometry, parameters.Center, parameters.Radius),
            "arc" => SameArc(geometry, parameters.Center, parameters.Radius, parameters.StartAngle, parameters.EndAngle),
            "lwpolyline" => SamePolyline(geometry, parameters.Points, parameters.Closed),
            _ => false,
        };
    }

    private static bool BlockInsertMatches(
        Dictionary<string, object?> definition,
        Dictionary<string, object?> inserted,
        BatchInsertBlockSpec spec
    )
    {
        if (!string.Equals(Convert.ToString(inserted["entity_type"]), "INSERT", StringComparison.Ordinal)
            || definition["geometry"] is not Dictionary<string, object?> definitionGeometry
            || inserted["geometry"] is not Dictionary<string, object?> geometry
            || definitionGeometry["name"] is not string definitionName
            || geometry["definition"] is not string actualDefinition
            || !string.Equals(actualDefinition, definitionName, StringComparison.Ordinal)
            || geometry["position"] is not double[] position
            || !Same3(position, spec.Position)
            || !Number(geometry["rotation"], out double rotation)
            || !SameAngle(rotation, spec.Rotation)
            || geometry["scale"] is not double[] scale
            || scale.Length != 3
            || !scale.All(value => Nearly(value, spec.Scale))
            || geometry["normal"] is not double[] normal
            || !Same3(normal, new[] { 0.0, 0.0, 1.0 })
            || geometry["attributes"] is not List<Dictionary<string, object?>> attributes
            || attributes.Count != 0)
        {
            return false;
        }
        return true;
    }

    private static bool SameLine(Dictionary<string, object?> geometry, double[]? start, double[]? end)
    {
        return geometry["start"] is double[] actualStart
            && geometry["end"] is double[] actualEnd
            && start is not null
            && end is not null
            && ((Same3(actualStart, start) && Same3(actualEnd, end))
                || (Same3(actualStart, end) && Same3(actualEnd, start)));
    }

    private static bool SameCircle(Dictionary<string, object?> geometry, double[]? center, double? radius)
    {
        return geometry["center"] is double[] actualCenter
            && geometry["normal"] is double[] normal
            && center is not null
            && radius.HasValue
            && Same3(actualCenter, center)
            && Same3(normal, new[] { 0.0, 0.0, 1.0 })
            && Number(geometry["radius"], out double actualRadius)
            && Nearly(actualRadius, radius.Value);
    }

    private static bool SameArc(
        Dictionary<string, object?> geometry,
        double[]? center,
        double? radius,
        double? startAngle,
        double? endAngle
    )
    {
        if (!SameCircle(geometry, center, radius)
            || !startAngle.HasValue
            || !endAngle.HasValue
            || !Number(geometry["start_angle"], out double actualStart)
            || !Number(geometry["end_angle"], out double actualEnd)
            || !Number(geometry["sweep_angle"], out double actualSweep))
        {
            return false;
        }
        double expectedSweep = (endAngle.Value - startAngle.Value) % (Math.PI * 2.0);
        if (expectedSweep < 0.0)
        {
            expectedSweep += Math.PI * 2.0;
        }
        return Nearly(actualStart, startAngle.Value)
            && Nearly(actualEnd, endAngle.Value)
            && Nearly(actualSweep, expectedSweep);
    }

    private static bool SamePolyline(
        Dictionary<string, object?> geometry,
        double[][]? points,
        bool? closed
    )
    {
        if (points is null
            || !closed.HasValue
            || geometry["vertices"] is not List<Dictionary<string, object?>> vertices
            || vertices.Count != points.Length
            || geometry["closed"] is not bool actualClosed
            || actualClosed != closed.Value
            || !Number(geometry["elevation"], out double elevation)
            || !Nearly(elevation, 0.0)
            || geometry["normal"] is not double[] normal
            || !Same3(normal, new[] { 0.0, 0.0, 1.0 }))
        {
            return false;
        }
        for (int index = 0; index < vertices.Count; index++)
        {
            Dictionary<string, object?> vertex = vertices[index];
            if (vertex["point"] is not double[] point
                || !Same2(point, points[index])
                || !Number(vertex["bulge"], out double bulge)
                || !Number(vertex["start_width"], out double startWidth)
                || !Number(vertex["end_width"], out double endWidth)
                || !Nearly(bulge, 0.0)
                || !Nearly(startWidth, 0.0)
                || !Nearly(endWidth, 0.0))
            {
                return false;
            }
        }
        return true;
    }

    private static bool Same3(double[] left, double[] right) =>
        left.Length == 3 && right.Length == 3
        && Nearly(left[0], right[0]) && Nearly(left[1], right[1]) && Nearly(left[2], right[2]);

    private static bool Same2(double[] left, double[] right) =>
        left.Length == 2 && right.Length == 2
        && Nearly(left[0], right[0]) && Nearly(left[1], right[1]);

    private static bool Number(object? value, out double number)
    {
        try
        {
            number = Convert.ToDouble(value);
            return double.IsFinite(number);
        }
        catch
        {
            number = 0.0;
            return false;
        }
    }

    private static bool SameAngle(double left, double right)
    {
        double period = Math.PI * 2.0;
        double delta = (left - right) % period;
        if (delta > Math.PI) delta -= period;
        if (delta < -Math.PI) delta += period;
        return Math.Abs(delta) <= Tolerance;
    }

    private static bool Nearly(double left, double right) => Math.Abs(left - right) <= Tolerance;

    private static void Require(bool condition, string message)
    {
        if (!condition)
        {
            throw new BridgeServiceException("PROVISIONAL_VALIDATION_FAILED", message);
        }
    }
}
