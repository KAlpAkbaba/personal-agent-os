using System.Globalization;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// The two identities M20 keeps (M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §2, ADR-0083
/// decision 1): <c>file:&lt;…&gt;</c> names a LOCATION — the volume the file sits on and its
/// full resolved path, so a file keeps its id across edits and two same-named files in two
/// folders never share one; <c>doc:&lt;…&gt;</c> names a CONTENT VERSION — the SHA-256 of the
/// bytes, so a byte-identical copy at another path has the same doc id and an edit changes
/// it. Neither is ever derived from anything a caller typed: the path is the RESOLVED one
/// <see cref="Operator.AuthorisedRoots"/> handed back.
/// </summary>
[SupportedOSPlatform("windows")]
public static class FileIdentity
{
    public const string FilePrefix = "file:";
    public const string DocPrefix = "doc:";

    /// <summary>The number of hex characters of the SHA-256 a <c>file_id</c> keeps (128 bits: enough to never collide on one machine, short enough to speak).</summary>
    public const int FileIdHexLength = 32;

    /// <summary>
    /// <c>"file:" + first 32 hex of SHA-256( lower(volume serial as 8 hex) + "|" + casefold(resolved path) )</c>.
    /// The casefold is the invariant culture's lower-casing — never the Turkish culture's,
    /// whose <c>I→ı</c> would give one file two ids depending on the process's locale. NTFS
    /// paths are case-insensitive, so two spellings of one file fold to one id.
    /// </summary>
    public static string FileId(string resolvedPath)
    {
        var serial = VolumeSerial(resolvedPath);
        var material = serial.ToString("x8", CultureInfo.InvariantCulture) + "|" + Casefold(resolvedPath);
        var hash = SHA256.HashData(Encoding.UTF8.GetBytes(material));
        return FilePrefix + Convert.ToHexStringLower(hash)[..FileIdHexLength];
    }

    /// <summary><c>"doc:" + SHA-256(bytes)</c>, streamed — the file is read once, never held whole.</summary>
    public static string DocId(string resolvedPath)
    {
        using var stream = new FileStream(resolvedPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16, FileOptions.SequentialScan);
        return DocPrefix + Convert.ToHexStringLower(SHA256.HashData(stream));
    }

    /// <summary>The plain SHA-256 hex of the bytes, for a record's <c>sha256</c> field.</summary>
    public static string Sha256Hex(string resolvedPath)
    {
        using var stream = new FileStream(resolvedPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete, 1 << 16, FileOptions.SequentialScan);
        return Convert.ToHexStringLower(SHA256.HashData(stream));
    }

    /// <summary>The invariant-culture casefold of a path with its separators made canonical (<c>/</c> → <c>\</c>).</summary>
    public static string Casefold(string path) => path.Replace('/', '\\').ToLowerInvariant();

    /// <summary>
    /// The volume serial number of the volume the path's root names (<c>C:\</c>,
    /// <c>\\server\share\</c>), from <c>GetVolumeInformationW</c>. A root that has no serial
    /// (a volume that is gone, a root that is not a volume) is a typed failure — an id that
    /// silently used <c>0</c> would collide with every other such file.
    /// </summary>
    public static uint VolumeSerial(string resolvedPath)
    {
        var root = Path.GetPathRoot(resolvedPath);
        if (string.IsNullOrEmpty(root))
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, "the path has no volume root, so it has no file identity", retryable: false);
        }

        if (!root.EndsWith(Path.DirectorySeparatorChar))
        {
            root += Path.DirectorySeparatorChar;
        }

        if (!GetVolumeInformationW(root, null, 0, out var serial, out _, out _, null, 0))
        {
            var error = Marshal.GetLastWin32Error();
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"the volume serial of the file's root could not be read (win32 error {error}), so it has no file identity", retryable: true);
        }

        return serial;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetVolumeInformationW(
        string rootPathName,
        StringBuilder? volumeNameBuffer,
        uint volumeNameSize,
        out uint volumeSerialNumber,
        out uint maximumComponentLength,
        out uint fileSystemFlags,
        StringBuilder? fileSystemNameBuffer,
        uint fileSystemNameSize);
}

/// <summary>
/// §2: the file record every documents result carries —
/// <c>{file_id, path, name, extension, size, mtime, sha256?}</c>. <c>path</c> is always the
/// resolved path inside the roots; <c>mtime</c> is ISO-8601 UTC with a <c>Z</c>; <c>sha256</c>
/// is present only when the file is at most <see cref="DocumentCapabilityNames.Sha256SizeLimit"/>
/// AND the record was built by a single-file capability (a search never opens a file, so its
/// records carry no content hash — <c>file.locate</c> on the hit does).
/// </summary>
[SupportedOSPlatform("windows")]
public sealed record FileRecord(
    string FileId,
    string Path,
    string Name,
    string Extension,
    long Size,
    DateTimeOffset MtimeUtc,
    string? Sha256)
{
    /// <summary>Build the record for a resolved path; <paramref name="hashContent"/> false is what a search asks for.</summary>
    public static FileRecord From(string resolvedPath, bool hashContent)
    {
        var info = new FileInfo(resolvedPath);
        return From(info, hashContent);
    }

    public static FileRecord From(FileInfo info, bool hashContent)
    {
        var extension = info.Extension.ToLowerInvariant();
        var sha = hashContent && info.Length <= DocumentCapabilityNames.Sha256SizeLimit ? FileIdentity.Sha256Hex(info.FullName) : null;
        return new FileRecord(
            FileIdentity.FileId(info.FullName),
            info.FullName,
            info.Name,
            extension,
            info.Length,
            new DateTimeOffset(info.LastWriteTimeUtc, TimeSpan.Zero),
            sha);
    }

    public string Kind => FileKinds.Of(Extension);

    public JsonObject ToJson()
    {
        var json = new JsonObject
        {
            ["file_id"] = FileId,
            ["path"] = Path,
            ["name"] = Name,
            ["extension"] = Extension,
            ["size"] = Size,
            ["mtime"] = MtimeUtc.UtcDateTime.ToString("o", CultureInfo.InvariantCulture),
        };
        if (Sha256 is not null)
        {
            json["sha256"] = Sha256;
        }

        return json;
    }
}
