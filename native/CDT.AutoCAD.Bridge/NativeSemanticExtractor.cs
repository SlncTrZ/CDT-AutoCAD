// NativeSemanticExtractor — authoritative bounded snapshot, including in-transaction provisional reads.
// Wing: code | Topic: native-bridge-n7 | Updated: 2026-09-10 20:30

using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CDT.AutoCAD.Bridge;

internal sealed class NativeSemanticExtractor
{
    internal Dictionary<string, object?> Extract(
        Document document,
        Guid runtimeDocumentId,
        string documentPid,
        int maxEntities = BridgeConstants.MaxSnapshotEntities
    )
    {
        using Transaction transaction = document.Database.TransactionManager.StartOpenCloseTransaction();
        return ExtractWithinTransaction(
            document,
            runtimeDocumentId,
            documentPid,
            transaction,
            maxEntities
        );
    }

    internal Dictionary<string, object?> ExtractWithinTransaction(
        Document document,
        Guid runtimeDocumentId,
        string documentPid,
        Transaction transaction,
        int maxEntities = BridgeConstants.MaxSnapshotEntities
    )
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
        BlockTableRecord space = (BlockTableRecord)transaction.GetObject(
            database.CurrentSpaceId,
            OpenMode.ForRead
        );

        List<Dictionary<string, object?>> entities = [];
        HashSet<string> semanticPids = new(StringComparer.Ordinal);
        List<ObjectId> referencedBlockDefinitions = [];
        foreach (ObjectId objectId in space)
        {
            if (objectId.IsErased)
            {
                continue;
            }
            EnsureCapacity(entities, maxEntities);
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
                visitedDefinitions,
                maxEntities
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
            ["document_fp_schema_version"] = 3,
            ["document_fp"] = documentFingerprint,
        };
    }

    internal Dictionary<string, object?> BuildProvisionalFromAffected(
        Document document,
        Guid runtimeDocumentId,
        string documentPid,
        Dictionary<string, object?> before,
        Transaction transaction,
        IReadOnlyCollection<ObjectId> affectedObjectIds,
        int maxEntities = BridgeConstants.MaxBatchSemanticEntities
    )
    {
        if (!ReferenceEquals(document, AcApplication.DocumentManager.MdiActiveDocument))
        {
            throw new BridgeServiceException(
                "DOCUMENT_NOT_ACTIVE",
                "native provisional semantic rebuild requires the bound document to be active"
            );
        }

        Database database = document.Database;
        int dbmod = Convert.ToInt32(AcApplication.GetSystemVariable("DBMOD"));
        BlockTableRecord space = (BlockTableRecord)transaction.GetObject(
            database.CurrentSpaceId,
            OpenMode.ForRead
        );
        if (before["entities"] is not List<Dictionary<string, object?>> beforeEntities)
        {
            throw new BridgeServiceException(
                "INVALID_SNAPSHOT",
                "semantic snapshot has invalid entity collection"
            );
        }

        Dictionary<string, Dictionary<string, object?>> replacements = new(StringComparer.Ordinal);
        foreach (ObjectId objectId in affectedObjectIds)
        {
            if (objectId.IsNull || objectId.IsErased)
            {
                continue;
            }
            if (transaction.GetObject(objectId, OpenMode.ForRead, false) is not Entity entity)
            {
                continue;
            }
            string semanticPid = EntityPidReader.ReadRequired(entity, transaction);
            if (!replacements.TryAdd(
                semanticPid,
                NativeEntityExtractor.Extract(entity, semanticPid, space.Name, transaction)
            ))
            {
                throw new BridgeServiceException(
                    "DUPLICATE_SEMANTIC_PID",
                    "provisional semantic rebuild contains duplicate affected PIDs"
                );
            }
        }

        List<Dictionary<string, object?>> entities = new(beforeEntities.Count + replacements.Count);
        foreach (Dictionary<string, object?> entity in beforeEntities)
        {
            string pid = Convert.ToString(entity["semantic_pid"]) ?? string.Empty;
            if (!replacements.ContainsKey(pid))
            {
                entities.Add(entity);
            }
        }
        entities.AddRange(replacements.Values);
        if (entities.Count > maxEntities)
        {
            throw new BridgeServiceException(
                "SNAPSHOT_TOO_LARGE",
                "provisional semantic snapshot exceeds the bounded entity limit"
            );
        }
        entities.Sort((left, right) => string.CompareOrdinal(
            (string)left["semantic_pid"]!,
            (string)right["semantic_pid"]!
        ));

        List<Dictionary<string, object?>> currentSpaceEntities = entities
            .Where(item => item.TryGetValue("hierarchy", out object? rawHierarchy)
                && rawHierarchy is Dictionary<string, object?> hierarchy
                && string.Equals(
                    Convert.ToString(hierarchy["owner_space"]),
                    space.Name,
                    StringComparison.Ordinal
                ))
            .ToList();
        Dictionary<string, object?>? extents = SemanticValue.CombineExtents(currentSpaceEntities);

        if (before["document"] is not Dictionary<string, object?> beforeDocument)
        {
            throw new BridgeServiceException(
                "INVALID_SNAPSHOT",
                "semantic snapshot has invalid document payload"
            );
        }
        string units = Convert.ToString(beforeDocument["units"]) ?? string.Empty;
        string currentSpace = Convert.ToString(beforeDocument["current_space"]) ?? string.Empty;
        object relations = before["relations"] ?? Array.Empty<object>();
        List<Dictionary<string, object?>> styles = NativeStyleExtractor.Extract(database, transaction);

        Dictionary<string, object?> fingerprintPayload = new()
        {
            ["schema_version"] = 1,
            ["document_pid"] = documentPid,
            ["units"] = units,
            ["current_space"] = currentSpace,
            ["extents"] = extents,
            ["entities"] = entities.Select(RemoveNativeHandle).ToArray(),
            ["relations"] = relations,
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
                ["saved"] = dbmod == 0,
                ["extents"] = extents,
            },
            ["entities"] = entities,
            ["relations"] = relations,
            ["styles"] = styles,
            ["document_fp_schema_version"] = 3,
            ["document_fp"] = documentFingerprint,
        };
    }

    /// <summary>
    /// Post-commit affected-scoped verification (AC-A01 optimization).
    /// Re-reads ONLY the affected entities in a fresh transaction after commit and
    /// compares each against its provisional entry. Returns the affected-scope
    /// fingerprint. Full-scope proof stays at the pre-batch STATE_DRIFT guard, the
    /// next batch pre-extract (rolling chain) and bridge.recovery.finalize boundary.
    /// </summary>
    internal (bool Verified, string AffectedFp, int VerifiedCount) VerifyAffectedPostCommit(
        Document document,
        IReadOnlyCollection<ObjectId> affectedObjectIds,
        IReadOnlyCollection<string> affectedPids,
        Dictionary<string, object?> provisional
    )
    {
        if (!ReferenceEquals(document, AcApplication.DocumentManager.MdiActiveDocument))
        {
            throw new BridgeServiceException(
                "DOCUMENT_NOT_ACTIVE",
                "native affected post-commit verification requires the bound document to be active"
            );
        }
        if (provisional["entities"] is not List<Dictionary<string, object?>> provisionalEntities)
        {
            throw new BridgeServiceException(
                "INVALID_SNAPSHOT",
                "provisional snapshot has invalid entity collection"
            );
        }
        HashSet<string> expected = new(affectedPids, StringComparer.Ordinal);
        Dictionary<string, string> provisionalByPid = new(StringComparer.Ordinal);
        foreach (Dictionary<string, object?> entity in provisionalEntities)
        {
            string pid = Convert.ToString(entity["semantic_pid"]) ?? string.Empty;
            if (expected.Contains(pid))
            {
                provisionalByPid[pid] = SemanticFingerprint.CanonicalLinearSortKey(
                    RemoveNativeHandle(entity));
            }
        }

        Database database = document.Database;
        using Transaction transaction = database.TransactionManager.StartOpenCloseTransaction();
        BlockTableRecord space = (BlockTableRecord)transaction.GetObject(
            database.CurrentSpaceId,
            OpenMode.ForRead
        );
        List<string> verifiedCanonicals = new(expected.Count);
        int verifiedCount = 0;
        foreach (ObjectId objectId in affectedObjectIds)
        {
            if (objectId.IsNull || objectId.IsErased)
            {
                return (false, string.Empty, verifiedCount);
            }
            if (transaction.GetObject(objectId, OpenMode.ForRead, false) is not Entity entity)
            {
                return (false, string.Empty, verifiedCount);
            }
            string semanticPid = EntityPidReader.ReadRequired(entity, transaction);
            if (!provisionalByPid.TryGetValue(semanticPid, out string? expectedCanonical))
            {
                return (false, string.Empty, verifiedCount);
            }
            Dictionary<string, object?> reread = NativeEntityExtractor.Extract(
                entity, semanticPid, space.Name, transaction);
            string actualCanonical = SemanticFingerprint.CanonicalLinearSortKey(
                RemoveNativeHandle(reread));
            if (!string.Equals(actualCanonical, expectedCanonical, StringComparison.Ordinal))
            {
                return (false, string.Empty, verifiedCount);
            }
            verifiedCanonicals.Add(actualCanonical);
            verifiedCount++;
        }
        if (verifiedCount != expected.Count)
        {
            return (false, string.Empty, verifiedCount);
        }
        verifiedCanonicals.Sort(StringComparer.Ordinal);
        string affectedFp = SemanticFingerprint.Document(new Dictionary<string, object?>
        {
            ["schema_version"] = 1,
            ["scope"] = "affected_post_commit",
            ["entities"] = verifiedCanonicals.ToArray(),
        });
        return (true, affectedFp, verifiedCount);
    }

    private static void AddReferencedBlockDefinition(
        ObjectId definitionId,
        Transaction transaction,
        List<Dictionary<string, object?>> entities,
        HashSet<string> semanticPids,
        HashSet<ObjectId> visitedDefinitions,
        int maxEntities
    )
    {
        if (definitionId.IsNull || definitionId.IsErased || !visitedDefinitions.Add(definitionId))
        {
            return;
        }
        EnsureCapacity(entities, maxEntities);
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
            ["metadata"] = NativeMetadataStore.Read(definition, transaction),
        });

        List<ObjectId> nestedDefinitions = [];
        foreach (ObjectId objectId in definition)
        {
            if (objectId.IsErased)
            {
                continue;
            }
            EnsureCapacity(entities, maxEntities);
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
                visitedDefinitions,
                maxEntities
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

    private static void EnsureCapacity(
        IReadOnlyCollection<Dictionary<string, object?>> entities,
        int maxEntities
    )
    {
        if (maxEntities <= 0 || entities.Count >= maxEntities)
        {
            throw new BridgeServiceException(
                "SNAPSHOT_TOO_LARGE",
                "semantic snapshot exceeds the bounded entity limit"
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
