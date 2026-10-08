// PidProbeExtendedCommands — close N2 shallow-clone, WBLOCK, INSERT and block identity gaps.
// Wing: code | Topic: semantic-state-n2 | Updated: 2026-09-10 10:05

using System.Security.Cryptography;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Application;

[assembly: CommandClass(typeof(CDT.AutoCAD.PidProbe.PidProbeExtendedCommands))]

namespace CDT.AutoCAD.PidProbe;

public static class PidProbeExtendedCommands
{
    private static string WblockDwgPath =>
        Path.Combine(ProbeReport.ReportDirectory, "pid-probe-wblock.dwg");

    private static string InsertDwgPath =>
        Path.Combine(ProbeReport.ReportDirectory, "pid-probe-insert.dwg");

    private static string LineageSourceDwgPath =>
        Path.Combine(ProbeReport.ReportDirectory, "pid-probe-lineage-source.dwg");

    private static string LineageCopyDwgPath =>
        Path.Combine(ProbeReport.ReportDirectory, "pid-probe-lineage-copy.dwg");

    [CommandMethod("CDT_PID_P3_SHALLOW_CLONE", CommandFlags.Modal)]
    public static void P3ShallowClone()
    {
        Run("p3b-shallow-clone", () =>
        {
            Database database = CurrentDocument().Database;
            string sourcePid;
            string sourceHandle;
            bool rawExtensionDictionaryNull;
            bool rawExtensionDictionaryAliasesSource;
            string? rawPidAfterAppend = null;
            string? assignedClonePid = null;
            string? cloneHandle = null;
            bool appendRejectedForAlias = false;

            using (Transaction transaction = database.TransactionManager.StartTransaction())
            {
                ObjectId sourceId = RequiredPrimaryId(database, transaction);
                Entity source = (Entity)transaction.GetObject(sourceId, OpenMode.ForRead);
                sourcePid = RequiredEntityPid(source, transaction);
                sourceHandle = source.Handle.ToString();
                ObjectId sourceExtensionDictionary = source.ExtensionDictionary;

                Entity clone = (Entity)source.Clone();
                rawExtensionDictionaryNull = clone.ExtensionDictionary.IsNull;
                rawExtensionDictionaryAliasesSource = !clone.ExtensionDictionary.IsNull
                    && clone.ExtensionDictionary == sourceExtensionDictionary;

                if (rawExtensionDictionaryAliasesSource)
                {
                    clone.Dispose();
                    appendRejectedForAlias = true;
                    transaction.Abort();
                }
                else
                {
                    BlockTableRecord currentSpace = (BlockTableRecord)transaction.GetObject(
                        database.CurrentSpaceId,
                        OpenMode.ForWrite
                    );
                    ObjectId cloneId = currentSpace.AppendEntity(clone);
                    transaction.AddNewlyCreatedDBObject(clone, true);
                    cloneHandle = cloneId.Handle.ToString();
                    rawPidAfterAppend = PidStorage.ReadEntityPid(clone, transaction);
                    assignedClonePid = PidStorage.NewEntityPid();
                    PidStorage.SetEntityPid(clone, assignedClonePid, transaction);
                    clone.TransformBy(Matrix3d.Displacement(new Vector3d(0, 6, 0)));
                    transaction.Commit();
                }
            }

            IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> index = ReadPidIndex(database);
            bool duplicateAfter = index.Any(item => item.Value.Count > 1);
            bool pass = !duplicateAfter
                && (appendRejectedForAlias || assignedClonePid is not null)
                && rawPidAfterAppend != sourcePid;

            return new
            {
                status = pass ? "PASS" : "FAIL",
                source_pid = sourcePid,
                source_handle = sourceHandle,
                raw_extension_dictionary_null = rawExtensionDictionaryNull,
                raw_extension_dictionary_aliases_source = rawExtensionDictionaryAliasesSource,
                append_rejected_for_alias = appendRejectedForAlias,
                raw_pid_after_append = rawPidAfterAppend,
                assigned_clone_pid = assignedClonePid,
                clone_handle = cloneHandle,
                checks = new
                {
                    shallow_clone_did_not_silently_duplicate_pid = rawPidAfterAppend != sourcePid,
                    alias_would_be_rejected = !rawExtensionDictionaryAliasesSource
                        || appendRejectedForAlias,
                    pid_index_unique_after_policy = !duplicateAfter,
                },
                pid_index = SerializablePidIndex(index),
            };
        });
    }

    [CommandMethod("CDT_PID_P9_WBLOCK_INSERT", CommandFlags.Modal)]
    public static void P9WblockInsertAndBlockIdentity()
    {
        Run("p9-wblock-insert-blocks", () =>
        {
            Database sourceDatabase = CurrentDocument().Database;
            ObjectId sourceId;
            string sourceDocumentPid;
            string sourceEntityPid;
            using (Transaction transaction = sourceDatabase.TransactionManager.StartOpenCloseTransaction())
            {
                sourceId = RequiredPrimaryId(sourceDatabase, transaction);
                sourceDocumentPid = PidStorage.ReadDocumentPid(sourceDatabase, transaction)
                    ?? throw new InvalidOperationException("source document PID missing");
                sourceEntityPid = RequiredEntityPid(
                    transaction.GetObject(sourceId, OpenMode.ForRead),
                    transaction
                );
            }

            using Database wblock = sourceDatabase.Wblock(
                new ObjectIdCollection([sourceId]),
                Point3d.Origin
            );

            string? rawWblockDocumentPid;
            string? rawWblockEntityPid;
            string wblockDocumentPid = PidStorage.NewDocumentPid();
            string wblockEntityPid = PidStorage.NewEntityPid();
            using (Transaction transaction = wblock.TransactionManager.StartTransaction())
            {
                rawWblockDocumentPid = PidStorage.ReadDocumentPid(wblock, transaction);
                ObjectId wblockEntityId = FirstModelSpaceEntityId(wblock, transaction);
                DBObject wblockEntity = transaction.GetObject(wblockEntityId, OpenMode.ForWrite);
                rawWblockEntityPid = PidStorage.ReadEntityPid(wblockEntity, transaction);
                PidStorage.SetDocumentPid(wblock, transaction, wblockDocumentPid);
                PidStorage.SetEntityPid(wblockEntity, wblockEntityPid, transaction);
                transaction.Commit();
            }
            wblock.SaveAs(WblockDwgPath, DwgVersion.Current);

            using Database coldWblock = OpenColdDatabase(WblockDwgPath);
            string coldWblockDocumentPid;
            IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> coldWblockIndex;
            using (Transaction transaction = coldWblock.TransactionManager.StartOpenCloseTransaction())
            {
                coldWblockDocumentPid = PidStorage.ReadDocumentPid(coldWblock, transaction)
                    ?? throw new InvalidOperationException("cold WBLOCK document PID missing");
                coldWblockIndex = PidStorage.ScanPidIndex(coldWblock, transaction);
            }

            using Database destination = new(true, true);
            string destinationDocumentPid;
            using (Transaction transaction = destination.TransactionManager.StartTransaction())
            {
                destinationDocumentPid = PidStorage.GetOrCreateDocumentPid(destination, transaction);
                transaction.Commit();
            }

            ObjectId insertedDefinitionId = destination.Insert(
                "CDT_PID_IMPORTED",
                coldWblock,
                true
            );

            string? rawInsertedDefinitionEntityPid;
            string definitionRecordPid = PidStorage.NewEntityPid();
            string definitionEntityPid = PidStorage.NewEntityPid();
            string blockReferencePid = PidStorage.NewEntityPid();
            string insertedDefinitionHandle;
            string insertedEntityHandle;
            string blockReferenceHandle;
            using (Transaction transaction = destination.TransactionManager.StartTransaction())
            {
                BlockTableRecord definition = (BlockTableRecord)transaction.GetObject(
                    insertedDefinitionId,
                    OpenMode.ForWrite
                );
                insertedDefinitionHandle = definition.Handle.ToString();
                PidStorage.SetEntityPid(definition, definitionRecordPid, transaction);

                ObjectId definitionEntityId = definition.Cast<ObjectId>().FirstOrDefault();
                if (definitionEntityId.IsNull)
                {
                    throw new InvalidOperationException("INSERT block definition has no entity");
                }
                DBObject definitionEntity = transaction.GetObject(
                    definitionEntityId,
                    OpenMode.ForWrite
                );
                insertedEntityHandle = definitionEntity.Handle.ToString();
                rawInsertedDefinitionEntityPid = PidStorage.ReadEntityPid(
                    definitionEntity,
                    transaction
                );
                PidStorage.SetEntityPid(definitionEntity, definitionEntityPid, transaction);

                BlockTableRecord modelSpace = (BlockTableRecord)transaction.GetObject(
                    destination.CurrentSpaceId,
                    OpenMode.ForWrite
                );
                BlockReference reference = new(Point3d.Origin, insertedDefinitionId);
                ObjectId referenceId = modelSpace.AppendEntity(reference);
                transaction.AddNewlyCreatedDBObject(reference, true);
                blockReferenceHandle = referenceId.Handle.ToString();
                PidStorage.SetEntityPid(reference, blockReferencePid, transaction);
                transaction.Commit();
            }

            IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> destinationIndex =
                ReadPidIndex(destination);
            destination.SaveAs(InsertDwgPath, DwgVersion.Current);

            using Database coldDestination = OpenColdDatabase(InsertDwgPath);
            string coldDestinationDocumentPid;
            IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> coldDestinationIndex;
            Dictionary<string, string[]> coldTypes;
            using (Transaction transaction = coldDestination.TransactionManager.StartOpenCloseTransaction())
            {
                coldDestinationDocumentPid = PidStorage.ReadDocumentPid(
                    coldDestination,
                    transaction
                ) ?? throw new InvalidOperationException("cold INSERT document PID missing");
                coldDestinationIndex = PidStorage.ScanPidIndex(coldDestination, transaction);
                coldTypes = PidTypeIndex(coldDestinationIndex, transaction);
            }

            bool wblockUnique = coldWblockIndex.All(item => item.Value.Count == 1);
            bool destinationUnique = coldDestinationIndex.All(item => item.Value.Count == 1);
            bool blockDefinitionTypePresent = coldTypes.TryGetValue(
                definitionRecordPid,
                out string[]? definitionTypes
            ) && definitionTypes.Contains(nameof(BlockTableRecord));
            bool blockReferenceTypePresent = coldTypes.TryGetValue(
                blockReferencePid,
                out string[]? referenceTypes
            ) && referenceTypes.Contains(nameof(BlockReference));
            bool definitionEntityPidPresent = coldDestinationIndex.ContainsKey(definitionEntityPid);

            bool pass = wblockDocumentPid != sourceDocumentPid
                && coldWblockDocumentPid == wblockDocumentPid
                && coldWblockIndex.ContainsKey(wblockEntityPid)
                && wblockUnique
                && destinationDocumentPid != sourceDocumentPid
                && destinationDocumentPid != wblockDocumentPid
                && coldDestinationDocumentPid == destinationDocumentPid
                && definitionEntityPidPresent
                && blockDefinitionTypePresent
                && blockReferenceTypePresent
                && destinationUnique;

            return new
            {
                status = pass ? "PASS" : "FAIL",
                source = new
                {
                    document_pid = sourceDocumentPid,
                    entity_pid = sourceEntityPid,
                },
                wblock = new
                {
                    raw_document_pid = rawWblockDocumentPid,
                    raw_entity_pid = rawWblockEntityPid,
                    assigned_document_pid = wblockDocumentPid,
                    assigned_entity_pid = wblockEntityPid,
                    cold_document_pid = coldWblockDocumentPid,
                    pid_index = SerializablePidIndex(coldWblockIndex),
                    path = WblockDwgPath,
                },
                insert = new
                {
                    destination_document_pid = destinationDocumentPid,
                    cold_document_pid = coldDestinationDocumentPid,
                    raw_definition_entity_pid = rawInsertedDefinitionEntityPid,
                    definition_record_pid = definitionRecordPid,
                    definition_entity_pid = definitionEntityPid,
                    block_reference_pid = blockReferencePid,
                    definition_handle = insertedDefinitionHandle,
                    definition_entity_handle = insertedEntityHandle,
                    block_reference_handle = blockReferenceHandle,
                    pid_index = SerializablePidIndex(destinationIndex),
                    cold_pid_index = SerializablePidIndex(coldDestinationIndex),
                    cold_pid_types = coldTypes,
                    path = InsertDwgPath,
                },
                observations = new
                {
                    wblock_raw_document_pid_copied_from_source = rawWblockDocumentPid
                        == sourceDocumentPid,
                    wblock_raw_entity_pid_copied_from_source = rawWblockEntityPid
                        == sourceEntityPid,
                    insert_raw_entity_pid_copied_from_wblock = rawInsertedDefinitionEntityPid
                        == wblockEntityPid,
                },
                checks = new
                {
                    wblock_document_reidentified = wblockDocumentPid != sourceDocumentPid,
                    wblock_identity_persisted = coldWblockDocumentPid == wblockDocumentPid,
                    wblock_pid_index_unique = wblockUnique,
                    insert_document_isolated = destinationDocumentPid != sourceDocumentPid
                        && destinationDocumentPid != wblockDocumentPid,
                    insert_document_pid_persisted = coldDestinationDocumentPid
                        == destinationDocumentPid,
                    block_definition_record_pid_persisted = blockDefinitionTypePresent,
                    block_definition_entity_pid_persisted = definitionEntityPidPresent,
                    block_reference_pid_persisted = blockReferenceTypePresent,
                    destination_pid_index_unique = destinationUnique,
                },
            };
        });
    }

    [CommandMethod("CDT_PID_P10_FILE_COPY", CommandFlags.Modal)]
    public static void P10RawFileCopyLineage()
    {
        Run("p10-file-copy-lineage", () =>
        {
            Database liveDatabase = CurrentDocument().Database;
            string liveDocumentPid;
            string[] liveEntityPids;
            using (Transaction transaction = liveDatabase.TransactionManager.StartOpenCloseTransaction())
            {
                liveDocumentPid = PidStorage.ReadDocumentPid(liveDatabase, transaction)
                    ?? throw new InvalidOperationException("live document PID missing");
                liveEntityPids = PidStorage.ScanPidIndex(liveDatabase, transaction)
                    .Keys.OrderBy(value => value, StringComparer.Ordinal).ToArray();
            }

            liveDatabase.SaveAs(LineageSourceDwgPath, DwgVersion.Current);
            File.Copy(LineageSourceDwgPath, LineageCopyDwgPath, true);

            using Database coldSource = OpenColdDatabase(LineageSourceDwgPath);
            using Database coldCopy = OpenColdDatabase(LineageCopyDwgPath);
            (string SourcePid, string[] SourceEntityPids) = ReadDocumentIdentity(coldSource);
            (string CopyPid, string[] CopyEntityPids) = ReadDocumentIdentity(coldCopy);

            string sourceArtifactSha = FileSha256(LineageSourceDwgPath);
            string copyArtifactSha = FileSha256(LineageCopyDwgPath);
            bool byteIdentical = sourceArtifactSha == copyArtifactSha;
            bool sameDocumentPid = SourcePid == CopyPid && SourcePid == liveDocumentPid;
            bool sameEntityPids = SourceEntityPids.SequenceEqual(CopyEntityPids)
                && liveEntityPids.SequenceEqual(SourceEntityPids);
            bool pass = byteIdentical && sameDocumentPid && sameEntityPids;

            return new
            {
                status = pass ? "PASS" : "FAIL",
                source_path = LineageSourceDwgPath,
                copy_path = LineageCopyDwgPath,
                source_artifact_sha256 = sourceArtifactSha,
                copy_artifact_sha256 = copyArtifactSha,
                live_document_pid = liveDocumentPid,
                source_document_pid = SourcePid,
                copy_document_pid = CopyPid,
                source_entity_pids = SourceEntityPids,
                copy_entity_pids = CopyEntityPids,
                checks = new
                {
                    raw_copy_is_byte_identical = byteIdentical,
                    raw_copy_preserves_document_pid = sameDocumentPid,
                    raw_copy_preserves_entity_pids = sameEntityPids,
                },
                policy_conclusion = new
                {
                    document_pid_semantics = "persistent semantic lineage identity, not globally unique physical-file identity",
                    runtime_document_binding_required = true,
                    artifact_fingerprint_required_for_checkpoint_identity = true,
                    duplicate_open_document_pid_must_fail_closed_until_target_is_disambiguated = true,
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

    private static ObjectId FirstModelSpaceEntityId(Database database, Transaction transaction)
    {
        BlockTable blockTable = (BlockTable)transaction.GetObject(
            database.BlockTableId,
            OpenMode.ForRead
        );
        BlockTableRecord modelSpace = (BlockTableRecord)transaction.GetObject(
            blockTable[BlockTableRecord.ModelSpace],
            OpenMode.ForRead
        );
        ObjectId id = modelSpace.Cast<ObjectId>().FirstOrDefault();
        if (id.IsNull)
        {
            throw new InvalidOperationException("database Model Space has no entity");
        }
        return id;
    }

    private static Database OpenColdDatabase(string path)
    {
        Database database = new(false, true);
        database.ReadDwgFile(path, FileOpenMode.OpenForReadAndAllShare, true, null);
        database.CloseInput(true);
        return database;
    }

    private static (string DocumentPid, string[] EntityPids) ReadDocumentIdentity(Database database)
    {
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        string documentPid = PidStorage.ReadDocumentPid(database, transaction)
            ?? throw new InvalidOperationException("cold document PID missing");
        string[] entityPids = PidStorage.ScanPidIndex(database, transaction)
            .Keys.OrderBy(value => value, StringComparer.Ordinal).ToArray();
        return (documentPid, entityPids);
    }

    private static string FileSha256(string path)
    {
        using FileStream stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> ReadPidIndex(Database database)
    {
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        return PidStorage.ScanPidIndex(database, transaction);
    }

    private static Dictionary<string, string[]> PidTypeIndex(
        IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> index,
        Transaction transaction
    ) => index.ToDictionary(
        item => item.Key,
        item => item.Value
            .Select(id => transaction.GetObject(id, OpenMode.ForRead, false).GetType().Name)
            .OrderBy(value => value, StringComparer.Ordinal)
            .ToArray(),
        StringComparer.Ordinal
    );

    private static Dictionary<string, string[]> SerializablePidIndex(
        IReadOnlyDictionary<string, IReadOnlyList<ObjectId>> index
    ) => index.ToDictionary(
        item => item.Key,
        item => item.Value.Select(id => id.Handle.ToString()).OrderBy(value => value).ToArray(),
        StringComparer.Ordinal
    );

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
}
