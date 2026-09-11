// NativeCheckpointStore — provider-owned immutable DWG checkpoints for N7 post-commit recovery.
// Wing: code | Topic: native-bridge-n7 | Updated: 2026-09-10 20:38

using System.Security.Cryptography;
using System.Text.Json;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using AcApplication = Autodesk.AutoCAD.ApplicationServices.Core.Application;

namespace CDT.AutoCAD.Bridge;

internal sealed record NativeCheckpoint(
    string CheckpointId,
    string DocumentPid,
    string ExpectedParentFp,
    string ArtifactFp,
    string CheckpointPath,
    string ManifestPath,
    string OriginalPath,
    string Operation,
    string RequestId
);

internal sealed record NativeCheckpointManifest(
    int SchemaVersion,
    string CheckpointId,
    string DocumentPid,
    string ExpectedParentFp,
    string ArtifactFp,
    string CheckpointPath,
    string OriginalPath,
    string Operation,
    string RequestId,
    string CreatedUtc
);

internal sealed class NativeCheckpointStore
{
    private readonly string _root;
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    internal NativeCheckpointStore()
    {
        _root = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "CDT-AutoCAD",
            "native-recovery"
        );
        Directory.CreateDirectory(_root);
    }

    internal NativeCheckpoint Create(
        Document document,
        string documentPid,
        string expectedParentFp,
        string operation,
        Guid requestId
    )
    {
        Database database = document.Database;
        if (List().Any(item => string.Equals(item.DocumentPid, documentPid, StringComparison.Ordinal)))
        {
            throw new BridgeServiceException(
                "RECOVERY_PENDING",
                "document already has an unresolved native recovery checkpoint"
            );
        }
        string originalPath = database.Filename;
        if (string.IsNullOrWhiteSpace(originalPath) || !Path.IsPathFullyQualified(originalPath))
        {
            throw new BridgeServiceException(
                "CHECKPOINT_UNAVAILABLE",
                "N7 mutation requires a named document so an immutable recovery checkpoint can be created"
            );
        }

        string id = Guid.NewGuid().ToString("D");
        string checkpointId = "cp:" + id;
        string checkpointPath = Path.Combine(_root, id + ".dwg");
        string manifestPath = Path.Combine(_root, id + ".json");
        int dbmodBefore = Convert.ToInt32(AcApplication.GetSystemVariable("DBMOD"));
        bool dbmodPushed = false;
        try
        {
            document.PushDbmod();
            dbmodPushed = true;
            database.SaveAs(
                checkpointPath,
                false,
                DwgVersion.Current,
                database.SecurityParameters
            );
        }
        catch (Exception exc)
        {
            throw new BridgeServiceException(
                "CHECKPOINT_CREATE_FAILED",
                $"native recovery checkpoint could not be created: {exc.GetType().Name}"
            );
        }
        finally
        {
            if (dbmodPushed)
            {
                document.PopDbmod();
            }
        }
        int dbmodAfter = Convert.ToInt32(AcApplication.GetSystemVariable("DBMOD"));
        string pathAfter = database.Filename;
        if (dbmodAfter != dbmodBefore
            || !string.Equals(pathAfter, originalPath, StringComparison.OrdinalIgnoreCase)
            || !File.Exists(checkpointPath))
        {
            TryDelete(checkpointPath);
            throw new BridgeServiceException(
                "CHECKPOINT_CREATE_FAILED",
                $"checkpoint creation changed DBMOD {dbmodBefore}->{dbmodAfter}, rebound the source database path, or did not produce an artifact"
            );
        }

        string artifactFp = FingerprintFile(checkpointPath);
        NativeCheckpointManifest manifest = new(
            1,
            checkpointId,
            documentPid,
            expectedParentFp,
            artifactFp,
            checkpointPath,
            originalPath,
            operation,
            requestId.ToString("D"),
            DateTimeOffset.UtcNow.ToString("O")
        );
        try
        {
            WriteManifestAtomic(manifestPath, manifest);
        }
        catch
        {
            TryDelete(checkpointPath);
            throw;
        }
        return FromManifest(manifest, manifestPath);
    }

    internal IReadOnlyList<NativeCheckpoint> List()
    {
        List<NativeCheckpoint> result = [];
        foreach (string manifestPath in Directory.EnumerateFiles(_root, "*.json", SearchOption.TopDirectoryOnly))
        {
            try
            {
                NativeCheckpointManifest? manifest = JsonSerializer.Deserialize<NativeCheckpointManifest>(
                    File.ReadAllText(manifestPath),
                    JsonOptions
                );
                if (manifest is null || manifest.SchemaVersion != 1)
                {
                    throw new InvalidDataException("native recovery manifest has an unsupported schema");
                }
                result.Add(FromManifest(manifest, manifestPath));
            }
            catch (Exception exc) when (exc is not BridgeServiceException)
            {
                throw new BridgeServiceException(
                    "RECOVERY_MANIFEST_INVALID",
                    $"native recovery manifest index is invalid: {exc.GetType().Name}"
                );
            }
        }
        return result.OrderBy(item => item.CheckpointId, StringComparer.Ordinal).ToArray();
    }

    internal NativeCheckpoint Require(
        string checkpointId,
        string documentPid,
        string artifactFp,
        string? expectedParentFp
    )
    {
        string id = ParseCheckpointId(checkpointId);
        string manifestPath = Path.Combine(_root, id + ".json");
        if (!File.Exists(manifestPath))
        {
            throw new BridgeServiceException("RECOVERY_NOT_FOUND", "native recovery checkpoint is not present");
        }
        NativeCheckpointManifest? manifest;
        try
        {
            manifest = JsonSerializer.Deserialize<NativeCheckpointManifest>(
                File.ReadAllText(manifestPath),
                JsonOptions
            );
        }
        catch
        {
            throw new BridgeServiceException("RECOVERY_MANIFEST_INVALID", "native recovery manifest is invalid");
        }
        if (manifest is null
            || manifest.SchemaVersion != 1
            || !string.Equals(manifest.CheckpointId, checkpointId, StringComparison.Ordinal)
            || !string.Equals(manifest.DocumentPid, documentPid, StringComparison.Ordinal)
            || (expectedParentFp is not null
                && !string.Equals(manifest.ExpectedParentFp, expectedParentFp, StringComparison.Ordinal))
            || !string.Equals(manifest.ArtifactFp, artifactFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "RECOVERY_BINDING_MISMATCH",
                "recovery request does not match the persisted checkpoint manifest"
            );
        }
        NativeCheckpoint checkpoint = FromManifest(manifest, manifestPath);
        if (!File.Exists(checkpoint.CheckpointPath)
            || !string.Equals(FingerprintFile(checkpoint.CheckpointPath), artifactFp, StringComparison.Ordinal))
        {
            throw new BridgeServiceException(
                "RECOVERY_ARTIFACT_MISMATCH",
                "recovery checkpoint artifact fingerprint does not match its manifest"
            );
        }
        return checkpoint;
    }

    internal void Finalize(NativeCheckpoint checkpoint)
    {
        if (File.Exists(checkpoint.CheckpointPath)
            && !string.Equals(
                FingerprintFile(checkpoint.CheckpointPath),
                checkpoint.ArtifactFp,
                StringComparison.Ordinal
            ))
        {
            throw new BridgeServiceException(
                "RECOVERY_ARTIFACT_MISMATCH",
                "cannot finalize a checkpoint whose artifact fingerprint changed"
            );
        }
        TryDelete(checkpoint.CheckpointPath);
        TryDelete(checkpoint.ManifestPath);
    }

    internal static string FingerprintFile(string path)
    {
        using FileStream stream = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        byte[] digest = SHA256.HashData(stream);
        return "sha256:" + Convert.ToHexString(digest).ToLowerInvariant();
    }

    private static string ParseCheckpointId(string checkpointId)
    {
        if (!checkpointId.StartsWith("cp:", StringComparison.Ordinal)
            || !Guid.TryParseExact(checkpointId[3..], "D", out Guid parsed)
            || !string.Equals(checkpointId, "cp:" + parsed.ToString("D"), StringComparison.Ordinal))
        {
            throw new BridgeServiceException("INVALID_PARAMS", "checkpoint_id must be canonical cp:<uuid>");
        }
        return parsed.ToString("D");
    }

    private static NativeCheckpoint FromManifest(
        NativeCheckpointManifest manifest,
        string manifestPath
    ) => new(
        manifest.CheckpointId,
        manifest.DocumentPid,
        manifest.ExpectedParentFp,
        manifest.ArtifactFp,
        manifest.CheckpointPath,
        manifestPath,
        manifest.OriginalPath,
        manifest.Operation,
        manifest.RequestId
    );

    private static void WriteManifestAtomic(string path, NativeCheckpointManifest manifest)
    {
        string temp = path + ".tmp";
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(manifest, JsonOptions);
        try
        {
            using (FileStream stream = new(
                temp,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None,
                4096,
                FileOptions.WriteThrough
            ))
            {
                stream.Write(bytes);
                stream.Flush(true);
            }
            File.Move(temp, path, false);
        }
        catch (Exception exc)
        {
            TryDelete(temp);
            throw new BridgeServiceException(
                "CHECKPOINT_CREATE_FAILED",
                $"native recovery manifest could not be persisted: {exc.GetType().Name}"
            );
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path))
            {
                File.SetAttributes(path, FileAttributes.Normal);
                File.Delete(path);
            }
        }
        catch (Exception exc)
        {
            throw new BridgeServiceException(
                "RECOVERY_CLEANUP_FAILED",
                $"native recovery checkpoint cleanup failed: {exc.GetType().Name}"
            );
        }
    }
}
