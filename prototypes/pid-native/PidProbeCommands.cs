// PidProbeCommands — N2 native PID persistence/clone/rollback experiments for AutoCAD 2027.
// Wing: code | Topic: semantic-state-n2 | Updated: 2026-09-10 14:10

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

[assembly: CommandClass(typeof(CDT.AutoCAD.PidProbe.PidProbeCommands))]

namespace CDT.AutoCAD.PidProbe;

public static class PidProbeCommands
{
    private const CommandFlags ReadOnlyFlags =
        CommandFlags.Modal | CommandFlags.NoHistory | CommandFlags.NoUndoMarker;

    private static string ProbeDwgPath =>
        Path.Combine(ProbeReport.ReportDirectory, "pid-probe.dwg");

    private static string CrossDwgPath =>
        Path.Combine(ProbeReport.ReportDirectory, "pid-probe-cross.dwg");

    [CommandMethod("CDT_PID_P0_CREATE", CommandFlags.Modal)]
    public static void P0Create()
    {
        Run("p0-create", () =>
        {
            Document document = CurrentDocument();
            Database database = document.Database;
            string documentPid;
            string entityPid;
            ObjectId entityId;

            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                documentPid = PidStorage.GetOrCreateDocumentPid(database, transaction);
                BlockTableRecord currentSpace = (BlockTableRecord)transaction.GetObject(
                    database.CurrentSpaceId,
                    OpenMode.ForWrite
                );
                Line line = new(new Point3d(0, 0, 0), new Point3d(10, 0, 0));
                entityId = currentSpace.AppendEntity(line);
                transaction.AddNewlyCreatedDBObject(line, true);
                entityPid = PidStorage.GetOrCreateEntityPid(line, transaction);
                PidStorage.RememberPrimaryHandle(database, transaction, line.Handle);
                transaction.Commit();
            }

            EntityEvidence readBack = ReadPrimaryEvidence(database);
            database.SaveAs(ProbeDwgPath, DwgVersion.Current);

            bool readBackPass = readBack.DocumentPid == documentPid
                && readBack.EntityPid == entityPid
                && PointEquals(readBack.Start, 0d, 0d, 0d)
                && PointEquals(readBack.End, 10d, 0d, 0d);
            return new
            {
                status = readBackPass ? "PASS" : "FAIL",
                candidate = new
                {
                    document_pid = "NOD/SLNCTRZ_CDT/DOCUMENT_PID XRecord",
                    entity_pid = "Entity ExtensionDictionary/SLNCTRZ_CDT_PID XRecord",
                },
                document_pid = documentPid,
                entity_pid = entityPid,
                native_handle = entityId.Handle.ToString(),
                read_back = readBack,
                saved_path = ProbeDwgPath,
                checks = new
                {
                    document_pid_read_back = readBack.DocumentPid == documentPid,
                    entity_pid_read_back = readBack.EntityPid == entityPid,
                    geometry_read_back = PointEquals(readBack.Start, 0d, 0d, 0d)
                        && PointEquals(readBack.End, 10d, 0d, 0d),
                },
            };
        });
    }

    [CommandMethod("CDT_PID_P1_DISK_REOPEN", ReadOnlyFlags)]
    public static void P1DiskReopen()
    {
        Run("p1-disk-reopen", () =>
        {
            Database liveDatabase = CurrentDocument().Database;
            EntityEvidence live = ReadPrimaryEvidence(liveDatabase);
            liveDatabase.SaveAs(ProbeDwgPath, DwgVersion.Current);

            using Database reopened = new(false, true);
            reopened.ReadDwgFile(
                ProbeDwgPath,
                FileOpenMode.OpenForReadAndAllShare,
                true,
                null
            );
            reopened.CloseInput(true);
            EntityEvidence cold = ReadPrimaryEvidence(reopened);

            bool pass = live.DocumentPid == cold.DocumentPid
                && live.EntityPid == cold.EntityPid
                && live.NativeHandle == cold.NativeHandle;
            return new
            {
                status = pass ? "PASS" : "FAIL",
                saved_path = ProbeDwgPath,
                live,
                cold_reopen = cold,
                checks = new
                {
                    document_pid_persisted = live.DocumentPid == cold.DocumentPid,
                    entity_pid_persisted = live.EntityPid == cold.EntityPid,
                    handle_persisted = live.NativeHandle == cold.NativeHandle,
                },
            };
        });
    }

    [CommandMethod("CDT_PID_P2_EDIT", CommandFlags.Modal)]
    public static void P2OrdinaryEdit()
    {
        Run("p2-ordinary-edit", () =>
        {
            Database database = CurrentDocument().Database;
            EntityEvidence before = ReadPrimaryEvidence(database);

            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                ObjectId primaryId = RequiredPrimaryId(database, transaction);
                Line line = (Line)transaction.GetObject(primaryId, OpenMode.ForWrite);
                line.EndPoint = new Point3d(12, 0, 0);
                transaction.Commit();
            }

            EntityEvidence after = ReadPrimaryEvidence(database);
            database.SaveAs(ProbeDwgPath, DwgVersion.Current);
            bool pass = before.DocumentPid == after.DocumentPid
                && before.EntityPid == after.EntityPid
                && PointEquals(after.End, 12d, 0d, 0d);
            return new
            {
                status = pass ? "PASS" : "FAIL",
                before,
                after,
                checks = new
                {
                    document_pid_stable = before.DocumentPid == after.DocumentPid,
                    entity_pid_stable = before.EntityPid == after.EntityPid,
                    geometry_changed = !before.End.SequenceEqual(after.End),
                    expected_geometry = PointEquals(after.End, 12d, 0d, 0d),
                },
            };
        });
    }

    [CommandMethod("CDT_PID_P3_DEEPCLONE", CommandFlags.Modal)]
    public static void P3SameDatabaseClone()
    {
        Run("p3-same-db-deepclone", () =>
        {
            Database database = CurrentDocument().Database;
            ObjectId sourceId;
            string sourcePid;
            using (Transaction transaction = database.TransactionManager.StartOpenCloseTransaction())
            {
                sourceId = RequiredPrimaryId(database, transaction);
                DBObject source = transaction.GetObject(sourceId, OpenMode.ForRead);
                sourcePid = RequiredEntityPid(source, transaction);
            }

            ObjectIdCollection identifiers = new([sourceId]);
            using IdMapping mapping = new();
            database.DeepCloneObjects(identifiers, database.CurrentSpaceId, mapping, false);
            IdPair pair = mapping[sourceId];
            if (!pair.IsCloned || pair.Value.IsNull)
            {
                throw new InvalidOperationException("DeepCloneObjects did not produce a clone mapping");
            }
            ObjectId cloneId = pair.Value;

            string? rawClonePid;
            string remappedClonePid;
            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                Entity clone = (Entity)transaction.GetObject(cloneId, OpenMode.ForWrite);
                rawClonePid = PidStorage.ReadEntityPid(clone, transaction);
                remappedClonePid = PidStorage.NewEntityPid();
                PidStorage.SetEntityPid(clone, remappedClonePid, transaction);
                clone.TransformBy(Matrix3d.Displacement(new Vector3d(0, 2, 0)));
                transaction.Commit();
            }

            IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> index = ReadPidIndex(database);
            bool duplicateAfterRepair = index.Any(item => item.Value.Count > 1);
            database.SaveAs(ProbeDwgPath, DwgVersion.Current);
            bool rawMetadataCloned = rawClonePid == sourcePid;

            return new
            {
                status = !duplicateAfterRepair ? "PASS" : "FAIL",
                source_pid = sourcePid,
                source_handle = sourceId.Handle.ToString(),
                clone_handle = cloneId.Handle.ToString(),
                raw_clone_pid = rawClonePid,
                raw_extension_metadata_cloned = rawMetadataCloned,
                remapped_clone_pid = remappedClonePid,
                checks = new
                {
                    id_mapping_cloned = pair.IsCloned,
                    source_and_clone_object_ids_differ = sourceId != cloneId,
                    clone_pid_is_unique_after_reconciliation = !duplicateAfterRepair,
                    clone_policy_requires_remap = rawMetadataCloned,
                },
                pid_index = SerializablePidIndex(index),
            };
        });
    }

    [CommandMethod("CDT_PID_P4_WBLOCKCLONE", CommandFlags.Modal)]
    public static void P4CrossDatabaseClone()
    {
        Run("p4-cross-db-wblockclone", () =>
        {
            Database sourceDatabase = CurrentDocument().Database;
            ObjectId sourceId;
            string sourcePid;
            string sourceDocumentPid;
            using (Transaction transaction = sourceDatabase.TransactionManager.StartOpenCloseTransaction())
            {
                sourceId = RequiredPrimaryId(sourceDatabase, transaction);
                DBObject source = transaction.GetObject(sourceId, OpenMode.ForRead);
                sourcePid = RequiredEntityPid(source, transaction);
                sourceDocumentPid = PidStorage.ReadDocumentPid(sourceDatabase, transaction)
                    ?? throw new InvalidOperationException("source document PID missing");
            }

            using Database target = new(true, true);
            string targetDocumentPid;
            using (Transaction transaction = target.TransactionManager.StartTransaction())
            {
                targetDocumentPid = PidStorage.GetOrCreateDocumentPid(target, transaction);
                transaction.Commit();
            }

            ObjectIdCollection identifiers = new([sourceId]);
            using IdMapping mapping = new();
            sourceDatabase.WblockCloneObjects(
                identifiers,
                target.CurrentSpaceId,
                mapping,
                DuplicateRecordCloning.Ignore,
                false
            );
            IdPair pair = mapping[sourceId];
            if (!pair.IsCloned || pair.Value.IsNull)
            {
                throw new InvalidOperationException("WblockCloneObjects did not produce a clone mapping");
            }

            ObjectId cloneId = pair.Value;
            string? rawClonePid;
            string remappedClonePid;
            using (Transaction transaction = target.TransactionManager.StartTransaction())
            {
                Entity clone = (Entity)transaction.GetObject(cloneId, OpenMode.ForWrite);
                rawClonePid = PidStorage.ReadEntityPid(clone, transaction);
                remappedClonePid = PidStorage.NewEntityPid();
                PidStorage.SetEntityPid(clone, remappedClonePid, transaction);
                PidStorage.RememberPrimaryHandle(target, transaction, clone.Handle);
                transaction.Commit();
            }
            target.SaveAs(CrossDwgPath, DwgVersion.Current);

            using Database cold = new(false, true);
            cold.ReadDwgFile(
                CrossDwgPath,
                FileOpenMode.OpenForReadAndAllShare,
                true,
                null
            );
            cold.CloseInput(true);
            EntityEvidence coldEvidence = ReadPrimaryEvidence(cold);

            bool rawMetadataCloned = rawClonePid == sourcePid;
            bool pass = sourceDocumentPid != targetDocumentPid
                && coldEvidence.DocumentPid == targetDocumentPid
                && coldEvidence.EntityPid == remappedClonePid;

            return new
            {
                status = pass ? "PASS" : "FAIL",
                source_document_pid = sourceDocumentPid,
                destination_document_pid = targetDocumentPid,
                source_entity_pid = sourcePid,
                raw_clone_pid = rawClonePid,
                raw_extension_metadata_cloned = rawMetadataCloned,
                remapped_clone_pid = remappedClonePid,
                cross_dwg_path = CrossDwgPath,
                cold_reopen = coldEvidence,
                checks = new
                {
                    destination_document_pid_isolated = sourceDocumentPid != targetDocumentPid,
                    destination_document_pid_persisted = coldEvidence.DocumentPid == targetDocumentPid,
                    destination_entity_pid_persisted = coldEvidence.EntityPid == remappedClonePid,
                    clone_policy_requires_remap = rawMetadataCloned,
                },
            };
        });
    }

    [CommandMethod("CDT_PID_P5_ERASE", CommandFlags.Modal)]
    public static void P5EraseUnerase()
    {
        Run("p5-erase-unerase", () =>
        {
            Database database = CurrentDocument().Database;
            EntityEvidence before = ReadPrimaryEvidence(database);
            ObjectId primaryId;

            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                primaryId = RequiredPrimaryId(database, transaction);
                DBObject obj = transaction.GetObject(primaryId, OpenMode.ForWrite);
                obj.Erase(true);
                transaction.Commit();
            }

            string? erasedPid = null;
            bool erasedFlag;
            using (Transaction transaction = database.TransactionManager.StartOpenCloseTransaction())
            {
                DBObject erased = transaction.GetObject(
                    primaryId,
                    OpenMode.ForRead,
                    true
                );
                erasedFlag = erased.IsErased;
                erasedPid = PidStorage.ReadEntityPid(erased, transaction, true);
            }

            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                DBObject erased = transaction.GetObject(
                    primaryId,
                    OpenMode.ForWrite,
                    true
                );
                erased.Erase(false);
                transaction.Commit();
            }

            EntityEvidence after = ReadPrimaryEvidence(database);
            database.SaveAs(ProbeDwgPath, DwgVersion.Current);
            bool pass = erasedFlag
                && erasedPid == before.EntityPid
                && after.EntityPid == before.EntityPid
                && after.NativeHandle == before.NativeHandle;
            return new
            {
                status = pass ? "PASS" : "FAIL",
                before,
                erased = new { is_erased = erasedFlag, entity_pid = erasedPid },
                after_unerase = after,
                checks = new
                {
                    object_was_erased = erasedFlag,
                    pid_readable_while_erased = erasedPid == before.EntityPid,
                    pid_preserved_after_unerase = after.EntityPid == before.EntityPid,
                    handle_preserved_after_unerase = after.NativeHandle == before.NativeHandle,
                },
            };
        });
    }

    [CommandMethod("CDT_PID_P6_MUTATE", CommandFlags.Modal)]
    public static void P6MutateForUndo()
    {
        Run("p6a-mutate-before-undo", () =>
        {
            Database database = CurrentDocument().Database;
            EntityEvidence before = ReadPrimaryEvidence(database);
            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                ObjectId primaryId = RequiredPrimaryId(database, transaction);
                Line line = (Line)transaction.GetObject(primaryId, OpenMode.ForWrite);
                line.EndPoint = new Point3d(14, 3, 0);
                transaction.Commit();
            }
            EntityEvidence after = ReadPrimaryEvidence(database);
            return new
            {
                status = after.EntityPid == before.EntityPid ? "PASS" : "FAIL",
                before,
                after,
                expected_undo_end = before.End,
                expected_redo_end = after.End,
            };
        });
    }

    [CommandMethod("CDT_PID_P6_VERIFY_UNDO", ReadOnlyFlags)]
    public static void P6VerifyUndo()
    {
        Run("p6b-verify-undo", () => new { status = "OBSERVED", state = ReadPrimaryEvidence(CurrentDocument().Database) });
    }

    [CommandMethod("CDT_PID_P6_VERIFY_REDO", ReadOnlyFlags)]
    public static void P6VerifyRedo()
    {
        Run("p6c-verify-redo", () => new { status = "OBSERVED", state = ReadPrimaryEvidence(CurrentDocument().Database) });
    }

    [CommandMethod("CDT_PID_P7_DUPLICATE", CommandFlags.Modal)]
    public static void P7DuplicatePidDetection()
    {
        Run("p7-duplicate-pid-detection", () =>
        {
            Database database = CurrentDocument().Database;
            ObjectId sourceId;
            string sourcePid;
            using (Transaction transaction = database.TransactionManager.StartOpenCloseTransaction())
            {
                sourceId = RequiredPrimaryId(database, transaction);
                sourcePid = RequiredEntityPid(
                    transaction.GetObject(sourceId, OpenMode.ForRead),
                    transaction
                );
            }

            ObjectIdCollection identifiers = new([sourceId]);
            using IdMapping mapping = new();
            database.DeepCloneObjects(identifiers, database.CurrentSpaceId, mapping, false);
            ObjectId cloneId = mapping[sourceId].Value;

            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                Entity clone = (Entity)transaction.GetObject(cloneId, OpenMode.ForWrite);
                PidStorage.SetEntityPid(clone, sourcePid, transaction);
                clone.TransformBy(Matrix3d.Displacement(new Vector3d(0, 4, 0)));
                transaction.Commit();
            }

            IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> corruptIndex = ReadPidIndex(database);
            bool duplicateDetected = corruptIndex.TryGetValue(sourcePid, out IReadOnlyList<ObjectId>? collisions)
                && collisions.Count >= 2;

            string repairedPid = PidStorage.NewEntityPid();
            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                DBObject clone = transaction.GetObject(cloneId, OpenMode.ForWrite);
                PidStorage.SetEntityPid(clone, repairedPid, transaction);
                transaction.Commit();
            }
            IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> repairedIndex = ReadPidIndex(database);
            bool duplicateRemains = repairedIndex.Any(item => item.Value.Count > 1);

            return new
            {
                status = duplicateDetected && !duplicateRemains ? "PASS" : "FAIL",
                corrupted_pid = sourcePid,
                corrupt_index = SerializablePidIndex(corruptIndex),
                duplicate_detected = duplicateDetected,
                repaired_clone_pid = repairedPid,
                repaired_index = SerializablePidIndex(repairedIndex),
                duplicate_remains_after_repair = duplicateRemains,
            };
        });
    }

    [CommandMethod("CDT_PID_P8_ABORT", CommandFlags.Modal)]
    public static void P8TransactionAbort()
    {
        Run("p8-transaction-abort", () =>
        {
            Database database = CurrentDocument().Database;
            EntityEvidence before = ReadPrimaryEvidence(database);
            int countBefore = CountCurrentSpaceEntities(database);
            string attemptedReplacementPid = PidStorage.NewEntityPid();

            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                ObjectId primaryId = RequiredPrimaryId(database, transaction);
                Line primary = (Line)transaction.GetObject(primaryId, OpenMode.ForWrite);
                primary.EndPoint = new Point3d(99, 99, 0);
                PidStorage.SetEntityPid(primary, attemptedReplacementPid, transaction);

                BlockTableRecord currentSpace = (BlockTableRecord)transaction.GetObject(
                    database.CurrentSpaceId,
                    OpenMode.ForWrite
                );
                Line leakedCandidate = new(
                    new Point3d(50, 50, 0),
                    new Point3d(60, 60, 0)
                );
                currentSpace.AppendEntity(leakedCandidate);
                transaction.AddNewlyCreatedDBObject(leakedCandidate, true);
                PidStorage.SetEntityPid(leakedCandidate, before.EntityPid, transaction);
                transaction.Abort();
            }

            EntityEvidence after = ReadPrimaryEvidence(database);
            int countAfter = CountCurrentSpaceEntities(database);
            bool pass = EvidenceEquals(before, after) && countBefore == countAfter;
            return new
            {
                status = pass ? "PASS" : "FAIL",
                rollback_strategy = "R0_ABORT",
                attempted_replacement_pid = attemptedReplacementPid,
                before,
                after,
                count_before = countBefore,
                count_after = countAfter,
                checks = new
                {
                    primary_state_restored = EvidenceEquals(before, after),
                    no_appended_entity_leaked = countBefore == countAfter,
                    existing_xrecord_pid_write_rolled_back = before.EntityPid == after.EntityPid
                        && after.EntityPid != attemptedReplacementPid,
                    pid_restored = before.EntityPid == after.EntityPid,
                    geometry_restored = before.Start.SequenceEqual(after.Start)
                        && before.End.SequenceEqual(after.End),
                },
            };
        });
    }

    private static Document CurrentDocument() =>
        AcApplication.DocumentManager.MdiActiveDocument
        ?? throw new InvalidOperationException("No active AutoCAD document");

    private static ObjectId RequiredPrimaryId(Database database, Transaction transaction)
    {
        ObjectId id = PidStorage.ResolvePrimaryEntityId(database, transaction);
        if (id.IsNull || !id.IsValid)
        {
            throw new InvalidOperationException("PID probe primary entity is missing");
        }
        return id;
    }

    private static string RequiredEntityPid(DBObject obj, Transaction transaction) =>
        PidStorage.ReadEntityPid(obj, transaction)
        ?? throw new InvalidOperationException("entity semantic PID is missing");

    private static EntityEvidence ReadPrimaryEvidence(Database database)
    {
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        string documentPid = PidStorage.ReadDocumentPid(database, transaction)
            ?? throw new InvalidOperationException("document semantic PID is missing");
        ObjectId id = RequiredPrimaryId(database, transaction);
        Line line = (Line)transaction.GetObject(id, OpenMode.ForRead);
        string entityPid = PidStorage.ReadEntityPid(line, transaction)
            ?? throw new InvalidOperationException("entity semantic PID is missing");
        return new EntityEvidence(
            documentPid,
            entityPid,
            line.Handle.ToString(),
            PointArray(line.StartPoint),
            PointArray(line.EndPoint),
            line.Layer
        );
    }

    private static IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> ReadPidIndex(Database database)
    {
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        return PidStorage.ScanPidIndex(database, transaction);
    }

    private static Dictionary<string, string[]> SerializablePidIndex(
        IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> index
    ) => index.ToDictionary(
        item => item.Key,
        item => item.Value.Select(id => id.Handle.ToString()).OrderBy(value => value).ToArray(),
        StringComparer.Ordinal
    );

    private static int CountCurrentSpaceEntities(Database database)
    {
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        BlockTableRecord currentSpace = (BlockTableRecord)transaction.GetObject(
            database.CurrentSpaceId,
            OpenMode.ForRead
        );
        return currentSpace.Cast<ObjectId>().Count();
    }

    private static double[] PointArray(Point3d point) => [point.X, point.Y, point.Z];

    private static bool PointEquals(double[] point, double x, double y, double z) =>
        point.Length == 3 && point[0] == x && point[1] == y && point[2] == z;

    private static bool EvidenceEquals(EntityEvidence left, EntityEvidence right) =>
        left.DocumentPid == right.DocumentPid
        && left.EntityPid == right.EntityPid
        && left.NativeHandle == right.NativeHandle
        && left.Layer == right.Layer
        && left.Start.SequenceEqual(right.Start)
        && left.End.SequenceEqual(right.End);

    private static void Run(string probeId, Func<object> action)
    {
        Document? document = AcApplication.DocumentManager.MdiActiveDocument;
        try
        {
            object result = action();
            string report = ProbeReport.Write(probeId, result);
            document?.Editor.WriteMessage($"\nCDT PID probe {probeId}: {report}");
        }
        catch (System.Exception exception)
        {
            string report = ProbeReport.Write(
                probeId,
                new
                {
                    status = "ERROR",
                    exception = exception.GetType().FullName,
                    exception.Message,
                    stack_trace = exception.StackTrace,
                }
            );
            document?.Editor.WriteMessage(
                $"\nCDT PID probe {probeId} ERROR: {exception.Message} ({report})"
            );
        }
    }

    private sealed record EntityEvidence(
        string DocumentPid,
        string EntityPid,
        string NativeHandle,
        double[] Start,
        double[] End,
        string Layer
    );
}
