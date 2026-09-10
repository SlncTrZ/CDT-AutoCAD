// NativeSemanticExtractor — authoritative read-only N4 snapshot from the active AutoCAD Database.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:07

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CDT.AutoCAD.Bridge;

internal sealed class NativeSemanticExtractor
{
    internal object Extract(Document document, Guid runtimeDocumentId, string documentPid)
    {
        if (!ReferenceEquals(document, AcApplication.DocumentManager.MdiActiveDocument))
        {
            throw new BridgeServiceException(
                "DOCUMENT_NOT_ACTIVE",
                "native semantic snapshot currently requires the bound document to be active"
            );
        }

        Database database = document.Database;
        int dbmod = Convert.ToInt32(AcApplication.GetSystemVariable("DBMOD"));
        string currentSpace = database.TileMode ? "Model" : "Paper";

        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        BlockTableRecord space = (BlockTableRecord)transaction.GetObject(
            database.CurrentSpaceId,
            OpenMode.ForRead
        );

        List<Dictionary<string, object?>> entities = [];
        HashSet<string> semanticPids = new(StringComparer.Ordinal);
        List<ObjectId> referencedBlockDefinitions = [];
        foreach (ObjectId objectId in space)
        {
            if (entities.Count >= BridgeConstants.MaxSnapshotEntities)
            {
                throw new BridgeServiceException(
                    "SNAPSHOT_TOO_LARGE",
                    "semantic snapshot exceeds the bounded N4 entity limit"
                );
            }
            if (transaction.GetObject(objectId, OpenMode.ForRead, false) is not Entity entity)
            {
                continue;
            }
            string semanticPid = EntityPidReader.ReadRequired(entity, transaction);
            if (!semanticPids.Add(semanticPid))
            {
                throw new BridgeServiceException(
                    "DUPLICATE_SEMANTIC_PID",
                    "semantic snapshot contains duplicate persistent entity PIDs"
                );
            }
            entities.Add(NativeEntityExtractor.Extract(entity, semanticPid, space.Name, transaction));
            if (entity is BlockReference blockReference)
            {
                referencedBlockDefinitions.Add(blockReference.BlockTableRecord);
            }
        }

        Dictionary<string, object?>? extents = SemanticValue.CombineExtents(entities);
        HashSet<ObjectId> visitedDefinitions = [];
        foreach (ObjectId definitionId in referencedBlockDefinitions)
        {
            AddReferencedBlockDefinition(
                definitionId,
                transaction,
                entities,
                semanticPids,
                visitedDefinitions
            );
        }
        entities.Sort((left, right) => string.CompareOrdinal(
            (string)left["semantic_pid"]!,
            (string)right["semantic_pid"]!
        ));

        List<Dictionary<string, object?>> styles = NativeStyleExtractor.Extract(database, transaction);
        string units = SemanticValue.UnitName(database.Insunits);
        bool saved = dbmod == 0;

        Dictionary<string, object?> fingerprintPayload = new()
        {
            ["schema_version"] = 1,
            ["document_pid"] = documentPid,
            ["units"] = units,
            ["current_space"] = currentSpace,
            ["saved"] = saved,
            ["extents"] = extents,
            ["entities"] = entities.Select(RemoveNativeHandle).ToArray(),
            ["relations"] = Array.Empty<object>(),
            ["styles"] = styles,
        };
        string documentFingerprint = SemanticFingerprint.Document(fingerprintPayload);

        return new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["runtime_document_id"] = runtimeDocumentId.ToString("D"),
            ["document_pid"] = documentPid,
            ["document"] = new Dictionary<string, object?>
            {
                ["units"] = units,
                ["current_space"] = currentSpace,
                ["saved"] = saved,
                ["extents"] = extents,
            },
            ["entities"] = entities,
            ["relations"] = Array.Empty<object>(),
            ["styles"] = styles,
            ["document_fp"] = documentFingerprint,
        };
    }

    private static void AddReferencedBlockDefinition(
        ObjectId definitionId,
        Transaction transaction,
        List<Dictionary<string, object?>> entities,
        HashSet<string> semanticPids,
        HashSet<ObjectId> visitedDefinitions
    )
    {
        if (definitionId.IsNull || !visitedDefinitions.Add(definitionId))
        {
            return;
        }
        EnsureCapacity(entities);
        BlockTableRecord definition = (BlockTableRecord)transaction.GetObject(
            definitionId,
            OpenMode.ForRead,
            false
        );
        string definitionPid = EntityPidReader.ReadRequired(definition, transaction);
        AddPidOrThrow(definitionPid, semanticPids);
        entities.Add(new Dictionary<string, object?>
        {
            ["semantic_pid"] = definitionPid,
            ["native_handle"] = definition.Handle.ToString(),
            ["entity_type"] = "BLOCK_DEFINITION",
            ["layer"] = "__BLOCK_DEFINITION__",
            ["geometry"] = new Dictionary<string, object?>
            {
                ["name"] = definition.Name,
                ["is_anonymous"] = definition.IsAnonymous,
                ["is_dynamic_block"] = definition.IsDynamicBlock,
            },
            ["bbox"] = null,
            ["metrics"] = new Dictionary<string, object?>(),
            ["style"] = new Dictionary<string, object?>(),
            ["hierarchy"] = new Dictionary<string, object?>
            {
                ["owner_space"] = "BlockTable",
            },
        });

        List<ObjectId> nestedDefinitions = [];
        foreach (ObjectId objectId in definition)
        {
            EnsureCapacity(entities);
            if (transaction.GetObject(objectId, OpenMode.ForRead, false) is not Entity entity)
            {
                continue;
            }
            string semanticPid = EntityPidReader.ReadRequired(entity, transaction);
            AddPidOrThrow(semanticPid, semanticPids);
            entities.Add(NativeEntityExtractor.Extract(
                entity,
                semanticPid,
                definition.Name,
                transaction
            ));
            if (entity is BlockReference nestedReference)
            {
                nestedDefinitions.Add(nestedReference.BlockTableRecord);
            }
        }
        foreach (ObjectId nestedDefinitionId in nestedDefinitions)
        {
            AddReferencedBlockDefinition(
                nestedDefinitionId,
                transaction,
                entities,
                semanticPids,
                visitedDefinitions
            );
        }
    }

    private static void AddPidOrThrow(string semanticPid, HashSet<string> semanticPids)
    {
        if (!semanticPids.Add(semanticPid))
        {
            throw new BridgeServiceException(
                "DUPLICATE_SEMANTIC_PID",
                "semantic snapshot contains duplicate persistent entity PIDs"
            );
        }
    }

    private static void EnsureCapacity(IReadOnlyCollection<Dictionary<string, object?>> entities)
    {
        if (entities.Count >= BridgeConstants.MaxSnapshotEntities)
        {
            throw new BridgeServiceException(
                "SNAPSHOT_TOO_LARGE",
                "semantic snapshot exceeds the bounded N4 entity limit"
            );
        }
    }

    private static Dictionary<string, object?> RemoveNativeHandle(
        Dictionary<string, object?> entity
    )
    {
        Dictionary<string, object?> result = new(entity, StringComparer.Ordinal);
        result.Remove("native_handle");
        return result;
    }
}
