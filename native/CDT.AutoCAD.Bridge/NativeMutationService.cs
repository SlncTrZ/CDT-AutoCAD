// NativeMutationService — N5 fixed-schema LINE mutations with parent guard and verified R0 rollback.
// Wing: code | Topic: native-bridge-n5 | Updated: 2026-09-10 14:05

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;

namespace CDT.AutoCAD.Bridge;

internal sealed class NativeMutationService
{
    private readonly NativeSemanticExtractor _semantic;

    internal NativeMutationService(NativeSemanticExtractor semantic)
    {
        _semantic = semantic;
    }

    internal object Execute(Document document, string operation, LineMutationParams parameters)
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
        if (string.Equals(operation, "entity.create.line", StringComparison.Ordinal))
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
        LineMutationParams parameters
    )
    {
        Point3d start = Point(parameters.Start, "start");
        Point3d end = Point(parameters.End, "end");
        BlockTableRecord space = (BlockTableRecord)transaction.GetObject(
            database.CurrentSpaceId,
            OpenMode.ForWrite
        );
        Line line = new(start, end) { LayerId = database.Clayer };
        space.AppendEntity(line);
        transaction.AddNewlyCreatedDBObject(line, true);
        string pid = EntityPidStore.NewEntityPid();
        EntityPidStore.Set(line, pid, transaction);
        return pid;
    }

    private static string UpdateLine(
        Database database,
        Transaction transaction,
        LineMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Line line)
        {
            throw new BridgeServiceException(
                "ENTITY_TYPE_MISMATCH",
                "target semantic PID is not a LINE"
            );
        }
        line.StartPoint = Point(parameters.Start, "start");
        line.EndPoint = Point(parameters.End, "end");
        return pid;
    }

    private static string DeleteLine(
        Database database,
        Transaction transaction,
        LineMutationParams parameters
    )
    {
        string pid = RequireTargetPid(parameters);
        ObjectId objectId = EntityPidStore.ResolveCurrentSpaceEntity(database, pid, transaction);
        if (transaction.GetObject(objectId, OpenMode.ForWrite, false) is not Line line)
        {
            throw new BridgeServiceException(
                "ENTITY_TYPE_MISMATCH",
                "target semantic PID is not a LINE"
            );
        }
        line.Erase(true);
        return pid;
    }

    private static void VerifyCommittedEffect(
        string operation,
        string affectedPid,
        LineMutationParams parameters,
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
        if (string.Equals(operation, "entity.delete.line", StringComparison.Ordinal))
        {
            if (entity is not null)
            {
                throw new BridgeServiceException(
                    "COMMIT_INTEGRITY_FAIL",
                    "deleted LINE remains present after commit"
                );
            }
            return;
        }
        if (entity is null
            || !string.Equals(Convert.ToString(entity["entity_type"]), "LINE", StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "COMMIT_INTEGRITY_FAIL",
                "affected LINE is missing after commit"
            );
        }
        Dictionary<string, object?> geometry = entity["geometry"]
            as Dictionary<string, object?>
            ?? throw new BridgeServiceException(
                "COMMIT_INTEGRITY_FAIL",
                "affected LINE geometry is missing after commit"
            );
        if (!SameEndpoints(geometry, parameters.Start, parameters.End))
        {
            throw new BridgeServiceException(
                "COMMIT_INTEGRITY_FAIL",
                "affected LINE geometry does not match the committed request"
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

    private static bool SamePoint(double[] left, double[] right)
    {
        if (left.Length != 3 || right.Length != 3)
        {
            return false;
        }
        const double tolerance = 1e-9;
        return Math.Abs(left[0] - right[0]) <= tolerance
            && Math.Abs(left[1] - right[1]) <= tolerance
            && Math.Abs(left[2] - right[2]) <= tolerance;
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

    private static string RequireTargetPid(LineMutationParams parameters)
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
        if (coordinates is null || coordinates.Length != 3)
        {
            throw new BridgeServiceException("INVALID_PARAMS", $"{name} must contain three coordinates");
        }
        return new Point3d(coordinates[0], coordinates[1], coordinates[2]);
    }
}
