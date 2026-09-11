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

    private static Dictionary<string, Dictionary<string, object?>> EntitiesByPid(Dictionary<string, object?> snapshot)
    {
        if (snapshot["entities"] is not List<Dictionary<string, object?>> entities)
        {
            throw new BridgeServiceException("PROVISIONAL_VALIDATION_FAILED", "semantic snapshot entity collection is invalid");
        }
        return entities.ToDictionary(item => Convert.ToString(item["semantic_pid"])!, StringComparer.Ordinal);
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

    private static bool Nearly(double left, double right) => Math.Abs(left - right) <= Tolerance;

    private static void Require(bool condition, string message)
    {
        if (!condition)
        {
            throw new BridgeServiceException("PROVISIONAL_VALIDATION_FAILED", message);
        }
    }
}
