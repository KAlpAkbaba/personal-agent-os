using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// B34 (requirements 153–165, 674; DEVICE_PROTOCOL.md §6m): the documents family's managed
/// MUTATIONS — write, append, rename, move, copy, restore — and the backup every one of
/// them takes first, so that each is reversible from the device's own store.
/// </summary>
/// <remarks>
/// <para>Rules, all of them structural rather than hoped for:</para>
/// <list type="bullet">
/// <item>Every path is confined by <see cref="AuthorisedRoots"/> first (the caller resolves
/// it); a destination that does not exist yet is confined through its PARENT, the way
/// <c>ConfineFile</c>'s <c>not_found</c> branch proves a spelling is inside.</item>
/// <item>A write is never in place: the bytes go to a temporary file beside the target and
/// are moved over it in one rename (<see cref="WriteAtomic"/>), so a crash leaves either
/// the old file or the new one — never half of each (165).</item>
/// <item>Before an existing file is overwritten, appended to, or moved to the Recycle Bin, a
/// copy of it goes to the undo store — <c>&lt;its root&gt;\.pagentos-undo\</c>, hidden, a
/// <c>.bak</c> beside a sidecar that records where it came from and its hash (160). The
/// store keeps the newest <see cref="MaxBackups"/> entries.</item>
/// <item>Every answer carries the record BEFORE and AFTER, with sha256 (163), read from the
/// disk after the act — the receipt is a read-back, never a claim.</item>
/// <item>Only text-like kinds are written or appended to (an Office document is not a text
/// file, and a byte-level edit of one would corrupt it); every kind can be renamed, moved,
/// copied and restored.</item>
/// <item>Nothing here deletes permanently; the delete the family offers is <c>file.trash</c>
/// (the Recycle Bin), which now takes its backup first.</item>
/// </list>
/// </remarks>
public static class FileMutations
{
    /// <summary>The undo store under each authorised root; skipped by <c>file.search</c>.</summary>
    public const string UndoFolderName = ".pagentos-undo";

    /// <summary>The longest text one write or append accepts (characters).</summary>
    public const int MaxTextChars = 2_000_000;

    /// <summary>The newest entries the undo store keeps per root; older ones are pruned on the next backup.</summary>
    public const int MaxBackups = 500;

    public const string BackupIdPrefix = "bak:";
    public const string MethodAtomicReplace = "atomic_replace";

    private static readonly UTF8Encoding Utf8NoBom = new(encoderShouldEmitUTF8Identifier: false);
    private static readonly byte[] Bom = [0xEF, 0xBB, 0xBF];

    /// <summary>One backup in the undo store: the copy, where it came from, and what it hashed to.</summary>
    public sealed record BackupRecord(
        string BackupId,
        string BackupPath,
        string SourcePath,
        string SourceName,
        string Sha256,
        long Size,
        DateTimeOffset TakenAt,
        string Reason)
    {
        public JsonObject ToJson() => new()
        {
            ["backup_id"] = BackupId,
            ["backup_path"] = BackupPath,
            ["source_path"] = SourcePath,
            ["source_name"] = SourceName,
            ["sha256"] = Sha256,
            ["size"] = Size,
            ["taken_at"] = TakenAt.UtcDateTime.ToString("o", CultureInfo.InvariantCulture),
            ["reason"] = Reason,
        };

        public static BackupRecord? FromJson(JsonObject json)
        {
            try
            {
                return new BackupRecord(
                    json["backup_id"]!.GetValue<string>(),
                    json["backup_path"]!.GetValue<string>(),
                    json["source_path"]!.GetValue<string>(),
                    json["source_name"]!.GetValue<string>(),
                    json["sha256"]!.GetValue<string>(),
                    json["size"]!.GetValue<long>(),
                    DateTimeOffset.Parse(json["taken_at"]!.GetValue<string>(), CultureInfo.InvariantCulture),
                    json["reason"]?.GetValue<string>() ?? "");
            }
            catch (Exception)
            {
                return null;
            }
        }
    }

    // ================================================================== backup / undo store

    /// <summary>The undo store of the root that contains <paramref name="resolvedPath"/>.</summary>
    public static string UndoStoreFor(AuthorisedRoots roots, string resolvedPath)
    {
        foreach (var root in roots.Resolved)
        {
            if (AuthorisedRoots.IsWithin(resolvedPath, root))
            {
                return Path.Combine(root, UndoFolderName);
            }
        }

        throw DocumentErrors.Denied("the file is not inside any authorised root; no undo store exists for it");
    }

    /// <summary>Copy <paramref name="resolvedPath"/> into its root's undo store and record it. The file must exist.</summary>
    public static BackupRecord Backup(AuthorisedRoots roots, string resolvedPath, string reason)
    {
        var info = new FileInfo(resolvedPath);
        if (!info.Exists)
        {
            throw DocumentErrors.NotFound("the file to back up is not there");
        }

        if (info.Length > DocumentCapabilityNames.MaxFileBytes)
        {
            throw DocumentErrors.Unsupported($"'{info.Name}' is {info.Length} bytes, over the {DocumentCapabilityNames.MaxFileBytes} byte bound a mutation may back up", DocumentErrors.TooLarge);
        }

        var store = UndoStoreFor(roots, resolvedPath);
        var directory = Directory.CreateDirectory(store);
        try
        {
            directory.Attributes |= FileAttributes.Hidden;
        }
        catch (Exception)
        {
            // Visible is acceptable; hidden is the courtesy.
        }

        var takenAt = DateTimeOffset.UtcNow;
        var stamp = takenAt.UtcDateTime.ToString("yyyyMMdd'T'HHmmssfff", CultureInfo.InvariantCulture);
        var token = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(resolvedPath + "|" + takenAt.UtcTicks)))[..16].ToLowerInvariant();
        var backupId = BackupIdPrefix + token;
        var backupPath = Path.Combine(store, $"{stamp}-{token}.bak");
        File.Copy(resolvedPath, backupPath, overwrite: false);
        var record = new BackupRecord(
            backupId,
            backupPath,
            resolvedPath,
            info.Name,
            FileIdentity.Sha256Hex(backupPath),
            new FileInfo(backupPath).Length,
            takenAt,
            reason);
        File.WriteAllText(SidecarPath(backupPath), record.ToJson().ToJsonString(new JsonSerializerOptions { WriteIndented = true }), Utf8NoBom);
        Prune(store);
        return record;
    }

    /// <summary>The backup with this id in any root's undo store, or null.</summary>
    public static BackupRecord? FindBackup(AuthorisedRoots roots, string backupId)
    {
        if (!backupId.StartsWith(BackupIdPrefix, StringComparison.Ordinal))
        {
            return null;
        }

        foreach (var root in roots.Resolved)
        {
            var store = Path.Combine(root, UndoFolderName);
            if (!Directory.Exists(store))
            {
                continue;
            }

            foreach (var sidecar in Directory.EnumerateFiles(store, "*.json"))
            {
                BackupRecord? record;
                try
                {
                    record = JsonNode.Parse(File.ReadAllText(sidecar)) is JsonObject json ? BackupRecord.FromJson(json) : null;
                }
                catch (Exception)
                {
                    continue;
                }

                if (record is not null && string.Equals(record.BackupId, backupId, StringComparison.Ordinal))
                {
                    return record;
                }
            }
        }

        return null;
    }

    private static string SidecarPath(string backupPath) => Path.ChangeExtension(backupPath, ".json");

    private static void Prune(string store)
    {
        var sidecars = new DirectoryInfo(store).EnumerateFiles("*.json").OrderByDescending(f => f.Name, StringComparer.Ordinal).ToList();
        foreach (var stale in sidecars.Skip(MaxBackups))
        {
            try
            {
                File.Delete(Path.ChangeExtension(stale.FullName, ".bak"));
                stale.Delete();
            }
            catch (Exception)
            {
                // Best effort; the next backup tries again.
            }
        }
    }

    // ================================================================== write / append

    /// <summary>
    /// Replace (or create) <paramref name="resolvedPath"/> with <paramref name="text"/> as UTF-8,
    /// keeping a BOM the file already had. <paramref name="before"/> is null for a file that does
    /// not exist yet. <paramref name="expectedSha256"/>, when given, must equal the file's current
    /// hash — the optimistic check that refuses to overwrite what changed since it was read.
    /// </summary>
    public static JsonObject Write(AuthorisedRoots roots, string resolvedPath, FileRecord? before, string text, string? expectedSha256)
    {
        RequireText(text);
        RequireTextLike(resolvedPath);
        RequireExpected(before, expectedSha256);
        BackupRecord? backup = null;
        var keepBom = false;
        if (before is not null)
        {
            backup = Backup(roots, resolvedPath, "write");
            keepBom = StartsWithBom(resolvedPath);
        }

        var bytes = keepBom ? [.. Bom, .. Utf8NoBom.GetBytes(text)] : Utf8NoBom.GetBytes(text);
        WriteAtomic(resolvedPath, bytes);
        var after = FileRecord.From(resolvedPath, hashContent: true);
        return Result("written", before, after, backup, new JsonObject
        {
            ["created"] = before is null,
            ["chars"] = text.Length,
        });
    }

    /// <summary>Append <paramref name="text"/> to an existing text-like file, atomically, after backing it up.</summary>
    public static JsonObject Append(AuthorisedRoots roots, string resolvedPath, FileRecord before, string text, string? expectedSha256)
    {
        RequireText(text);
        RequireTextLike(resolvedPath);
        RequireExpected(before, expectedSha256);
        if (before.Size > DocumentCapabilityNames.MaxFileBytes)
        {
            throw DocumentErrors.Unsupported($"'{before.Name}' is {before.Size} bytes, over the bound", DocumentErrors.TooLarge);
        }

        var backup = Backup(roots, resolvedPath, "append");
        var existing = File.ReadAllBytes(resolvedPath);
        var bytes = new byte[existing.Length + Utf8NoBom.GetByteCount(text)];
        existing.CopyTo(bytes, 0);
        Utf8NoBom.GetBytes(text, 0, text.Length, bytes, existing.Length);
        WriteAtomic(resolvedPath, bytes);
        var after = FileRecord.From(resolvedPath, hashContent: true);
        return Result("appended", before, after, backup, new JsonObject { ["chars"] = text.Length });
    }

    // ================================================================== rename / move / copy

    /// <summary>A new name in the same folder; the target must not exist.</summary>
    public static JsonObject Rename(string resolvedPath, FileRecord before, string newName)
    {
        var name = RequireFileName(newName, "payload.new_name");
        var target = Path.Combine(Path.GetDirectoryName(resolvedPath)!, name);
        RequireVacant(target);
        File.Move(resolvedPath, target);
        var after = FileRecord.From(target, hashContent: true);
        return Result("renamed", before, after, null, new JsonObject
        {
            ["observed"] = new JsonObject { ["source_exists"] = File.Exists(resolvedPath), ["target_exists"] = File.Exists(target) },
        });
    }

    /// <summary>Into another folder inside the roots (resolved by the caller), same name; the target must not exist.</summary>
    public static JsonObject Move(string resolvedPath, FileRecord before, string resolvedDestinationDir)
    {
        if (!Directory.Exists(resolvedDestinationDir))
        {
            throw DocumentErrors.NotFound("the destination folder does not exist");
        }

        var target = Path.Combine(resolvedDestinationDir, before.Name);
        if (string.Equals(target, resolvedPath, StringComparison.OrdinalIgnoreCase))
        {
            throw DocumentErrors.Invalid("the file is already in that folder");
        }

        RequireVacant(target);
        File.Move(resolvedPath, target);
        var after = FileRecord.From(target, hashContent: true);
        return Result("moved", before, after, null, new JsonObject
        {
            ["observed"] = new JsonObject { ["source_exists"] = File.Exists(resolvedPath), ["target_exists"] = File.Exists(target) },
        });
    }

    /// <summary>A copy at <paramref name="resolvedTarget"/> (a full file path the caller confined); never over an existing file.</summary>
    public static JsonObject Copy(string resolvedPath, FileRecord before, string resolvedTarget)
    {
        if (string.Equals(resolvedTarget, resolvedPath, StringComparison.OrdinalIgnoreCase))
        {
            throw DocumentErrors.Invalid("a file cannot be copied onto itself");
        }

        if (!Directory.Exists(Path.GetDirectoryName(resolvedTarget)))
        {
            throw DocumentErrors.NotFound("the destination folder does not exist");
        }

        RequireVacant(resolvedTarget);
        File.Copy(resolvedPath, resolvedTarget, overwrite: false);
        var after = FileRecord.From(resolvedTarget, hashContent: true);
        return Result("copied", before, after, null, new JsonObject
        {
            ["observed"] = new JsonObject { ["source_exists"] = File.Exists(resolvedPath), ["target_exists"] = File.Exists(resolvedTarget) },
        });
    }

    // ================================================================== restore

    /// <summary>
    /// Put a backup back: over its source path, or over <paramref name="resolvedTarget"/> when the
    /// caller names one (a file that was moved or renamed since). Atomic; the current target, if
    /// any, is backed up first so a restore is itself undoable.
    /// </summary>
    public static JsonObject Restore(AuthorisedRoots roots, BackupRecord backup, string? resolvedTarget)
    {
        if (!File.Exists(backup.BackupPath))
        {
            throw DocumentErrors.NotFound($"the backup {backup.BackupId} is no longer in the undo store");
        }

        var actual = FileIdentity.Sha256Hex(backup.BackupPath);
        if (!string.Equals(actual, backup.Sha256, StringComparison.OrdinalIgnoreCase))
        {
            throw DocumentErrors.Unsupported($"the backup {backup.BackupId} does not hash to what its record says; it is not restored", "backup_corrupt");
        }

        var target = resolvedTarget ?? backup.SourcePath;
        if (roots.Confine(Path.GetDirectoryName(target)!) is null)
        {
            throw DocumentErrors.Denied("the restore target is not inside the owner's authorised roots");
        }

        FileRecord? before = null;
        BackupRecord? displaced = null;
        if (File.Exists(target))
        {
            before = FileRecord.From(target, hashContent: true);
            displaced = Backup(roots, target, "restore");
        }
        else if (!Directory.Exists(Path.GetDirectoryName(target)))
        {
            throw DocumentErrors.NotFound("the folder the backup came from no longer exists");
        }

        WriteAtomic(target, File.ReadAllBytes(backup.BackupPath));
        var after = FileRecord.From(target, hashContent: true);
        var result = Result("restored", before, after, displaced, new JsonObject { ["backup"] = backup.ToJson() });
        return result;
    }

    // ================================================================== helpers

    /// <summary>Temporary file beside the target, then one rename over it: the old file or the new one, never half.</summary>
    public static void WriteAtomic(string target, byte[] bytes)
    {
        var directory = Path.GetDirectoryName(target) ?? throw DocumentErrors.Invalid("the target has no folder");
        var temp = Path.Combine(directory, $".{Path.GetFileName(target)}.pagentos-tmp-{Guid.NewGuid():N}");
        try
        {
            File.WriteAllBytes(temp, bytes);
            File.Move(temp, target, overwrite: true);
        }
        finally
        {
            if (File.Exists(temp))
            {
                try
                {
                    File.Delete(temp);
                }
                catch (Exception)
                {
                    // Left for the next run; never the target.
                }
            }
        }
    }

    public static bool IsTextLikePath(string path) => FileKinds.IsTextLike(FileKinds.Of(Path.GetExtension(path).ToLowerInvariant()));

    private static void RequireTextLike(string resolvedPath)
    {
        if (!IsTextLikePath(resolvedPath))
        {
            throw DocumentErrors.Unsupported($"'{Path.GetFileName(resolvedPath)}' is not a text file; only text-like kinds are written or appended to (an Office document would be corrupted by a byte-level edit)", DocumentErrors.NotText);
        }
    }

    private static void RequireText(string text)
    {
        if (text.Length > MaxTextChars)
        {
            throw DocumentErrors.Unsupported($"payload.text is {text.Length} characters, over the {MaxTextChars} bound", DocumentErrors.TooLarge);
        }
    }

    private static void RequireExpected(FileRecord? before, string? expectedSha256)
    {
        if (string.IsNullOrEmpty(expectedSha256))
        {
            return;
        }

        if (before is null)
        {
            throw DocumentErrors.Invalid("payload.expected_sha256 was given but the file does not exist yet");
        }

        if (before.Sha256 is null || !string.Equals(before.Sha256, expectedSha256, StringComparison.OrdinalIgnoreCase))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"'{before.Name}' changed since it was read (its hash is no longer the expected one); nothing was written", retryable: false, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "content_changed" });
        }
    }

    private static void RequireVacant(string target)
    {
        if (File.Exists(target) || Directory.Exists(target))
        {
            throw new CapabilityException(ErrorClasses.ValidationError, $"'{Path.GetFileName(target)}' already exists there; nothing was overwritten", retryable: false, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "already_exists" });
        }
    }

    public static string RequireFileName(string? name, string where)
    {
        if (string.IsNullOrWhiteSpace(name))
        {
            throw DocumentErrors.Invalid($"{where} is required");
        }

        var trimmed = name.Trim();
        if (trimmed.Length > DocumentCapabilityNames.MaxFetchNameChars
            || trimmed.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0
            || trimmed.Contains(Path.DirectorySeparatorChar)
            || trimmed.Contains(Path.AltDirectorySeparatorChar)
            || trimmed is "." or "..")
        {
            throw DocumentErrors.Invalid($"{where} must be a plain file name");
        }

        if (SecretNames.IsSecretBearing(trimmed))
        {
            throw DocumentErrors.Denied($"'{trimmed}' is a secret-bearing name and is never written", SecretNames.Detail);
        }

        return trimmed;
    }

    private static bool StartsWithBom(string path)
    {
        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            Span<byte> head = stackalloc byte[3];
            return stream.Read(head) == 3 && head[0] == Bom[0] && head[1] == Bom[1] && head[2] == Bom[2];
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static JsonObject Result(string verb, FileRecord? before, FileRecord after, BackupRecord? backup, JsonObject extra)
    {
        var result = new JsonObject
        {
            [verb] = true,
            ["method"] = MethodAtomicReplace,
            ["before"] = before?.ToJson(),
            ["after"] = after.ToJson(),
            ["backup"] = backup?.ToJson(),
        };
        foreach (var pair in extra)
        {
            result[pair.Key] = pair.Value?.DeepClone();
        }

        result["observed"] ??= new JsonObject { ["exists"] = File.Exists(after.Path), ["sha256"] = after.Sha256 };
        return result;
    }
}
