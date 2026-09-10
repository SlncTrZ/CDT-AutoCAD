// DocumentRegistry — bridge-instance runtime IDs and bound native Document resolution.
// Wing: code | Topic: native-bridge-n4 | Updated: 2026-09-10 13:09

using Autodesk.AutoCAD.ApplicationServices;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CDT.AutoCAD.Bridge;

internal sealed class DocumentRegistry
{
    private readonly Dictionary<Document, Guid> _byDocument = new(ReferenceEqualityComparer.Instance);

    internal IReadOnlyList<DocumentIdentity> List()
    {
        Refresh();
        Document? active = AcApplication.DocumentManager.MdiActiveDocument;
        List<DocumentIdentity> identities = [];
        foreach ((Document document, Guid runtimeId) in _byDocument)
        {
            identities.Add(ToIdentity(document, runtimeId, ReferenceEquals(document, active)));
        }

        Dictionary<string, int> lineageCounts = identities
            .Where(item => item.DocumentPid is not null)
            .GroupBy(item => item.DocumentPid!, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);

        return identities
            .Select(item => item with
            {
                OpenLineageCount = item.DocumentPid is null
                    ? 0
                    : lineageCounts[item.DocumentPid]
            })
            .OrderBy(item => item.RuntimeDocumentId, StringComparer.Ordinal)
            .ToArray();
    }

    internal DocumentIdentity Resolve(Guid runtimeDocumentId, string? assertedDocumentPid)
    {
        IReadOnlyList<DocumentIdentity> identities = List();
        DocumentIdentity? identity = identities.FirstOrDefault(
            item => string.Equals(
                item.RuntimeDocumentId,
                runtimeDocumentId.ToString("D"),
                StringComparison.Ordinal
            )
        );
        if (identity is null)
        {
            throw new BridgeServiceException(
                "DOCUMENT_NOT_FOUND",
                "runtime document binding is no longer present"
            );
        }
        if (assertedDocumentPid is not null
            && !string.Equals(assertedDocumentPid, identity.DocumentPid, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "DOCUMENT_BINDING_MISMATCH",
                "document lineage assertion does not match the runtime document"
            );
        }
        return identity;
    }

    internal Document ResolveDocument(Guid runtimeDocumentId, string? assertedDocumentPid)
    {
        Refresh();
        Document? document = _byDocument
            .Where(item => item.Value == runtimeDocumentId)
            .Select(item => item.Key)
            .FirstOrDefault();
        if (document is null)
        {
            throw new BridgeServiceException(
                "DOCUMENT_NOT_FOUND",
                "runtime document binding is no longer present"
            );
        }
        string? documentPid = DocumentPidReader.Read(document.Database);
        if (string.IsNullOrWhiteSpace(documentPid))
        {
            throw new BridgeServiceException(
                "DOCUMENT_PID_MISSING",
                "native semantic operations require persistent document lineage PID metadata"
            );
        }
        if (assertedDocumentPid is not null
            && !string.Equals(assertedDocumentPid, documentPid, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "DOCUMENT_BINDING_MISMATCH",
                "document lineage assertion does not match the runtime document"
            );
        }
        return document;
    }

    private void Refresh()
    {
        HashSet<Document> current = new(ReferenceEqualityComparer.Instance);
        foreach (Document document in AcApplication.DocumentManager)
        {
            current.Add(document);
            if (!_byDocument.ContainsKey(document))
            {
                _byDocument.Add(document, Guid.NewGuid());
            }
        }

        foreach (Document stale in _byDocument.Keys.Where(item => !current.Contains(item)).ToArray())
        {
            _byDocument.Remove(stale);
        }
    }

    private static DocumentIdentity ToIdentity(Document document, Guid runtimeId, bool isActive)
    {
        string? documentPid = DocumentPidReader.Read(document.Database);
        return new DocumentIdentity(
            runtimeId.ToString("D"),
            documentPid,
            document.Name,
            document.Database.Filename,
            isActive,
            0
        );
    }
}

internal sealed record DocumentIdentity(
    string RuntimeDocumentId,
    string? DocumentPid,
    string Name,
    string FileName,
    bool IsActive,
    int OpenLineageCount
);
