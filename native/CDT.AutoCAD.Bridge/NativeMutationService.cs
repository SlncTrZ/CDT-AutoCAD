// NativeMutationService — fixed-schema typed entity mutations with parent guard and verified R0 rollback.
// Wing: code | Topic: native-bridge-o1 | Updated: 2026-09-10 15:35

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace CDT.AutoCAD.Bridge;

internal sealed class NativeMutationService
{
    private const double NumericTolerance = 1e-9;
    private readonly NativeSemanticExtractor _semantic;

    internal NativeMutationService(NativeSemanticExtractor semantic)
    {
        _semantic = semantic;
    }

    internal object Execute(Document document, string operation, EntityMutationParams parameters)
    {
        using DocumentLock documentLock = document.LockDocument();
        Dictionary<string, object?> before = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        string parentFp = Fingerprint(before);
        if (!string.Equals(parentFp, parameters.ExpectedParentFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "STATE_DRIFT",
                "current semantic fingerprint does not match expected_parent_fp"
            );
        }
        if (operation.StartsWith("entity.create.", StringComparison.Ordinal))
        {
            List<Dictionary<string, object?>> entities = before["entities"]
                as List<Dictionary<string, object?>>
                ?? throw new BridgeServiceException(
                    "INVALID_SNAPSHOT",
                    "pre-mutation semantic snapshot has invalid entity collection"
                );
            if (entities.Count >= BridgeConstants.MaxSnapshotEntities)
            {
                throw new BridgeServiceException(
                    "SNAPSHOT_CAPACITY_EXCEEDED",
                    "create would exceed the bounded semantic snapshot capacity"
                );
            }
        }

        string affectedPid;
        using (Transaction transaction = document.Database.TransactionManager.StartTransaction())
        {
            affectedPid = operation switch
            {
                "entity.create.line" => CreateLine(document.Database, transaction, parameters),
                "entity.update.line" => UpdateLine(document.Database, transaction, parameters),
                "entity.delete.line" => DeleteLine(document.Database, transaction, parameters),
                "entity.create.circle" => CreateCircle(document.Database, transaction, parameters),
                "entity.update.circle" => UpdateCircle(document.Database, transaction, parameters),
                "entity.delete.circle" => DeleteCircle(document.Database, transaction, parameters),
                "entity.create.arc" => CreateArc(document.Database, transaction, parameters),
                "entity.update.arc" => UpdateArc(document.Database, transaction, parameters),
                "entity.delete.arc" => DeleteArc(document.Database, transaction, parameters),
                "entity.create.lwpolyline" => CreatePolyline(document.Database, transaction, parameters),
                "entity.update.lwpolyline" => UpdatePolyline(document.Database, transaction, parameters),
                "entity.delete.lwpolyline" => DeletePolyline(document.Database, transaction, parameters),
                _ => throw new BridgeServiceException(
                    "UNSUPPORTED_OPERATION",
                    "mutation operation is not enabled"
                ),
            };

            if (string.Equals(
                parameters.FaultStage,
                "after_apply_before_commit",
                StringComparison.Ordinal
            ))
            {
                transaction.Abort();
            }
            else
            {
                transaction.Commit();
            }
        }

        Dictionary<string, object?> after = _semantic.Extract(
            document,
            parameters.RuntimeDocumentId,
            parameters.DocumentPid
        );
        string postFp = Fingerprint(after);

        if (string.Equals(
            parameters.FaultStage,
            "after_apply_before_commit",
            StringComparison.Ordinal
        ))
        {
            bool restored = string.Equals(postFp, parentFp, StringComparison.Ordinal);
            return new Dictionary<string, object?>
            {
                ["schema_version"] = 1,
                ["operation"] = operation,
                ["outcome"] = restored ? "ROLLED_BACK_VERIFIED" : "ROLLBACK_FAILED",
                ["document_pid"] = parameters.DocumentPid,
                ["pre_document_fp"] = parentFp,
                ["post_document_fp"] = postFp,
                ["affected_semantic_pid"] = affectedPid,
                ["rollback"] = new Dictionary<string, object?>
                {
                    ["strategy"] = "R0_ABORT",
                    ["expected_restore_fp"] = parentFp,
                    ["actual_restore_fp"] = postFp,
                    ["status"] = restored ? "ROLLED_BACK_VERIFIED" : "ROLLBACK_FAILED",
                },
            };
        }

        VerifyCommittedEffect(operation, affectedPid, parameters, after);
        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["operation"] = operation,
            ["outcome"] = "COMMITTED_VERIFIED",
            ["document_pid"] = parameters.DocumentPid,
            ["pre_document_fp"] = parentFp,
            ["post_document_fp"] = postFp,
            ["affected_semantic_pid"] = affectedPid,
        };
    }

    private static string CreateLine(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        Point3d start = Point(parameters.Start, "start");
        Point3d end = Point(parameters.End, "end");
        BlockTableRecord space = CurrentSpace(database, transaction);
        Line line = new(start, end) { LayerId = database.Clayer };
        return AppendWithPid(space, line, transaction);
    }

    private static string UpdateLine(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Line line)
        {
            throw TypeMismatch("LINE");
        }
        line.StartPoint = Point(parameters.Start, "start");
        line.EndPoint = Point(parameters.End, "end");
        return pid;
    }

    private static string DeleteLine(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Line line)
        {
            throw TypeMismatch("LINE");
        }
        line.Erase(true);
        return pid;
    }

    private static string CreateCircle(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        Circle circle = new(
            Point(parameters.Center, "center"),
            Vector3d.ZAxis,
            Positive(parameters.Radius, "radius")
        )
        {
            LayerId = database.Clayer,
        };
        return AppendWithPid(CurrentSpace(database, transaction), circle, transaction);
    }

    private static string UpdateCircle(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Circle circle)
        {
            throw TypeMismatch("CIRCLE");
        }
        circle.Center = Point(parameters.Center, "center");
        circle.Radius = Positive(parameters.Radius, "radius");
        return pid;
    }

    private static string DeleteCircle(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Circle circle)
        {
            throw TypeMismatch("CIRCLE");
        }
        circle.Erase(true);
        return pid;
    }

    private static string CreateArc(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        Arc arc = new(
            Point(parameters.Center, "center"),
            Vector3d.ZAxis,
            Positive(parameters.Radius, "radius"),
            Angle(parameters.StartAngle, "start_angle"),
            Angle(parameters.EndAngle, "end_angle")
        )
        {
            LayerId = database.Clayer,
        };
        return AppendWithPid(CurrentSpace(database, transaction), arc, transaction);
    }

    private static string UpdateArc(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Arc arc)
        {
            throw TypeMismatch("ARC");
        }
        arc.Center = Point(parameters.Center, "center");
        arc.Radius = Positive(parameters.Radius, "radius");
        arc.StartAngle = Angle(parameters.StartAngle, "start_angle");
        arc.EndAngle = Angle(parameters.EndAngle, "end_angle");
        return pid;
    }

    private static string DeleteArc(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Arc arc)
        {
            throw TypeMismatch("ARC");
        }
        arc.Erase(true);
        return pid;
    }

    private static string CreatePolyline(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        double[][] points = Points(parameters.Points);
        bool closed = Closed(parameters.Closed);
        Polyline polyline = new(points.Length)
        {
            LayerId = database.Clayer,
            Closed = closed,
            Elevation = 0.0,
            Normal = Vector3d.ZAxis,
        };
        for (int index = 0; index < points.Length; index++)
        {
            polyline.AddVertexAt(index, new Point2d(points[index][0], points[index][1]), 0.0, 0.0, 0.0);
        }
        return AppendWithPid(CurrentSpace(database, transaction), polyline, transaction);
    }

    private static string UpdatePolyline(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Polyline polyline)
        {
            throw TypeMismatch("LWPOLYLINE");
        }
        EnsureSimplePolyline(polyline);
        double[][] points = Points(parameters.Points);
        polyline.Closed = false;
        int existingCount = polyline.NumberOfVertices;
        int sharedCount = Math.Min(existingCount, points.Length);
        for (int index = 0; index < sharedCount; index++)
        {
            polyline.SetPointAt(index, new Point2d(points[index][0], points[index][1]));
        }
        for (int index = existingCount - 1; index >= points.Length; index--)
        {
            polyline.RemoveVertexAt(index);
        }
        for (int index = existingCount; index < points.Length; index++)
        {
            polyline.AddVertexAt(index, new Point2d(points[index][0], points[index][1]), 0.0, 0.0, 0.0);
        }
        polyline.Closed = Closed(parameters.Closed);
        return pid;
    }

    private static string DeletePolyline(
        Database database,
        Transaction transaction,
        EntityMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Polyline polyline)
        {
            throw TypeMismatch("LWPOLYLINE");
        }
        polyline.Erase(true);
        return pid;
    }

    private static BlockTableRecord CurrentSpace(Database database, Transaction transaction)
    {
        return (BlockTableRecord)transaction.GetObject(database.CurrentSpaceId, OpenMode.ForWrite);
    }

    private static string AppendWithPid(
        BlockTableRecord space,
        Entity entity,
        Transaction transaction
    )
    {
        space.AppendEntity(entity);
        transaction.AddNewlyCreatedDBObject(entity, true);
        string pid = EntityPidStore.NewEntityPid();
        EntityPidStore.Set(entity, pid, transaction);
        return pid;
    }

    private static void EnsureSimplePolyline(Polyline polyline)
    {
        if (Math.Abs(polyline.Elevation) > NumericTolerance
            || !SameVector(polyline.Normal, Vector3d.ZAxis))
        {
            throw new BridgeServiceException(
                "UNSUPPORTED_TARGET_GEOMETRY",
                "LWPOLYLINE update supports only zero-elevation +Z simple polylines"
            );
        }
        for (int index = 0; index < polyline.NumberOfVertices; index++)
        {
            if (Math.Abs(polyline.GetBulgeAt(index)) > NumericTolerance
                || Math.Abs(polyline.GetStartWidthAt(index)) > NumericTolerance
                || Math.Abs(polyline.GetEndWidthAt(index)) > NumericTolerance)
            {
                throw new BridgeServiceException(
                    "UNSUPPORTED_TARGET_GEOMETRY",
                    "LWPOLYLINE update supports only zero-bulge zero-width vertices"
                );
            }
        }
    }

    private static void VerifyCommittedEffect(
        string operation,
        string affectedPid,
        EntityMutationParams parameters,
        Dictionary<string, object?> snapshot
    )
    {
        List<Dictionary<string, object?>> entities = snapshot["entities"]
            as List<Dictionary<string, object?>>
            ?? throw new BridgeServiceException(
                "COMMIT_INTEGRITY_FAIL",
                "post-commit semantic snapshot has invalid entity collection"
            );
        Dictionary<string, object?>? entity = entities.FirstOrDefault(
            item => string.Equals(
                Convert.ToString(item["semantic_pid"]),
                affectedPid,
                StringComparison.Ordinal
            )
        );
        bool isDelete = operation.StartsWith("entity.delete.", StringComparison.Ordinal);
        if (isDelete)
        {
            if (entity is not null)
            {
                throw new BridgeServiceException(
                    "COMMIT_INTEGRITY_FAIL",
                    "deleted entity remains present after commit"
                );
            }
            return;
        }

        string family = operation.Split('.')[2];
        string expectedType = family switch
        {
            "line" => "LINE",
            "circle" => "CIRCLE",
            "arc" => "ARC",
            "lwpolyline" => "LWPOLYLINE",
            _ => throw new BridgeServiceException(
                "COMMIT_INTEGRITY_FAIL",
                "post-commit operation family is unknown"
            ),
        };
        if (entity is null
            || !string.Equals(Convert.ToString(entity["entity_type"]), expectedType, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "COMMIT_INTEGRITY_FAIL",
                $"affected {expectedType} is missing after commit"
            );
        }
        Dictionary<string, object?> geometry = entity["geometry"]
            as Dictionary<string, object?>
            ?? throw new BridgeServiceException(
                "COMMIT_INTEGRITY_FAIL",
                $"affected {expectedType} geometry is missing after commit"
            );

        bool matches = family switch
        {
            "line" => SameEndpoints(geometry, parameters.Start, parameters.End),
            "circle" => SameCircle(geometry, parameters.Center, parameters.Radius),
            "arc" => SameArc(
                geometry,
                parameters.Center,
                parameters.Radius,
                parameters.StartAngle,
                parameters.EndAngle
            ),
            "lwpolyline" => SamePolyline(geometry, parameters.Points, parameters.Closed),
            _ => false,
        };
        if (!matches)
        {
            throw new BridgeServiceException(
                "COMMIT_INTEGRITY_FAIL",
                $"affected {expectedType} geometry does not match the committed request"
            );
        }
    }

    private static bool SameEndpoints(
        Dictionary<string, object?> geometry,
        double[]? requestedStart,
        double[]? requestedEnd
    )
    {
        if (geometry["start"] is not double[] actualStart
            || geometry["end"] is not double[] actualEnd
            || requestedStart is null
            || requestedEnd is null)
        {
            return false;
        }
        return (SamePoint(actualStart, requestedStart) && SamePoint(actualEnd, requestedEnd))
            || (SamePoint(actualStart, requestedEnd) && SamePoint(actualEnd, requestedStart));
    }

    private static bool SameCircle(
        Dictionary<string, object?> geometry,
        double[]? requestedCenter,
        double? requestedRadius
    )
    {
        return geometry["center"] is double[] actualCenter
            && requestedCenter is not null
            && SamePoint(actualCenter, requestedCenter)
            && TryDouble(geometry["radius"], out double actualRadius)
            && requestedRadius.HasValue
            && NearlyEqual(actualRadius, requestedRadius.Value);
    }

    private static bool SameArc(
        Dictionary<string, object?> geometry,
        double[]? requestedCenter,
        double? requestedRadius,
        double? requestedStartAngle,
        double? requestedEndAngle
    )
    {
        return SameCircle(geometry, requestedCenter, requestedRadius)
            && TryDouble(geometry["start_angle"], out double actualStartAngle)
            && TryDouble(geometry["end_angle"], out double actualEndAngle)
            && requestedStartAngle.HasValue
            && requestedEndAngle.HasValue
            && NearlyEqual(actualStartAngle, requestedStartAngle.Value)
            && NearlyEqual(actualEndAngle, requestedEndAngle.Value);
    }

    private static bool SamePolyline(
        Dictionary<string, object?> geometry,
        double[][]? requestedPoints,
        bool? requestedClosed
    )
    {
        if (requestedPoints is null
            || !requestedClosed.HasValue
            || geometry["vertices"] is not List<Dictionary<string, object?>> vertices
            || vertices.Count != requestedPoints.Length
            || geometry["closed"] is not bool actualClosed
            || actualClosed != requestedClosed.Value
            || !TryDouble(geometry["elevation"], out double elevation)
            || Math.Abs(elevation) > NumericTolerance
            || geometry["normal"] is not double[] normal
            || !SamePoint(normal, new[] { 0.0, 0.0, 1.0 }))
        {
            return false;
        }
        for (int index = 0; index < vertices.Count; index++)
        {
            Dictionary<string, object?> vertex = vertices[index];
            if (vertex["point"] is not double[] point
                || !SamePoint2(point, requestedPoints[index])
                || !Zero(vertex["bulge"])
                || !Zero(vertex["start_width"])
                || !Zero(vertex["end_width"]))
            {
                return false;
            }
        }
        return true;
    }

    private static bool SamePoint(double[] left, double[] right)
    {
        return left.Length == 3
            && right.Length == 3
            && NearlyEqual(left[0], right[0])
            && NearlyEqual(left[1], right[1])
            && NearlyEqual(left[2], right[2]);
    }

    private static bool SamePoint2(double[] left, double[] right)
    {
        return left.Length == 2
            && right.Length == 2
            && NearlyEqual(left[0], right[0])
            && NearlyEqual(left[1], right[1]);
    }

    private static bool SameVector(Vector3d left, Vector3d right)
    {
        return NearlyEqual(left.X, right.X)
            && NearlyEqual(left.Y, right.Y)
            && NearlyEqual(left.Z, right.Z);
    }

    private static bool Zero(object? value)
    {
        return TryDouble(value, out double number) && Math.Abs(number) <= NumericTolerance;
    }

    private static bool TryDouble(object? value, out double number)
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

    private static bool NearlyEqual(double left, double right)
    {
        return Math.Abs(left - right) <= NumericTolerance;
    }

    private static string Fingerprint(Dictionary<string, object?> snapshot)
    {
        return snapshot.TryGetValue("document_fp", out object? value)
            && value is string fingerprint
            ? fingerprint
            : throw new BridgeServiceException(
                "SNAPSHOT_FINGERPRINT_MISSING",
                "native semantic snapshot did not produce document_fp"
            );
    }

    private static string RequireTargetPid(EntityMutationParams parameters)
    {
        return !string.IsNullOrWhiteSpace(parameters.SemanticPid)
            ? parameters.SemanticPid
            : throw new BridgeServiceException(
                "INVALID_PARAMS",
                "semantic_pid is required for target mutation"
            );
    }

    private static Point3d Point(double[]? coordinates, string name)
    {
        if (coordinates is null
            || coordinates.Length != 3
            || coordinates.Any(value => !double.IsFinite(value)))
        {
            throw new BridgeServiceException("INVALID_PARAMS", $"{name} must contain three finite coordinates");
        }
        return new Point3d(coordinates[0], coordinates[1], coordinates[2]);
    }

    private static double Positive(double? value, string name)
    {
        if (!value.HasValue || !double.IsFinite(value.Value) || value.Value <= 0.0)
        {
            throw new BridgeServiceException("INVALID_PARAMS", $"{name} must be a positive finite number");
        }
        return value.Value;
    }

    private static double Angle(double? value, string name)
    {
        if (!value.HasValue
            || !double.IsFinite(value.Value)
            || value.Value < 0.0
            || value.Value >= Math.PI * 2.0)
        {
            throw new BridgeServiceException("INVALID_PARAMS", $"{name} must be in [0, 2pi) radians");
        }
        return value.Value;
    }

    private static double[][] Points(double[][]? points)
    {
        if (points is null
            || points.Length < 2
            || points.Length > BridgeConstants.MaxSimplePolylineVertices
            || points.Any(point => point.Length != 2 || point.Any(value => !double.IsFinite(value))))
        {
            throw new BridgeServiceException("INVALID_PARAMS", "points must be bounded finite [x, y] pairs");
        }
        return points;
    }

    private static bool Closed(bool? value)
    {
        return value ?? throw new BridgeServiceException("INVALID_PARAMS", "closed must be a boolean");
    }

    private static BridgeServiceException TypeMismatch(string expectedType)
    {
        return new BridgeServiceException(
            "ENTITY_TYPE_MISMATCH",
            $"target semantic PID is not a {expectedType}"
        );
    }
}
