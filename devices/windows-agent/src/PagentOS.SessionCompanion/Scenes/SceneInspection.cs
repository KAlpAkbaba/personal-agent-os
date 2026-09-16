using System.Globalization;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.SessionCompanion.Scenes;

/// <summary>The render a driver declared and the companion verified: the path it named, its size and the hash both sides agree on.</summary>
public sealed record SceneRender(string RelativePath, string ResolvedPath, long Bytes, string Sha256, byte[] Content);

/// <summary>B44 (req 527): an exported scene file the companion verified in place — its format, the path it named, its size and the hash both sides agree on.</summary>
public sealed record SceneExport(string Format, string RelativePath, long Bytes, string Sha256);

/// <summary>
/// M25_CREATIVE_3D_SPEC.md §3/§4/§7, ADR-0088 — the device half of the read-back. The rule
/// the whole milestone rests on: <b>what the tool wrote is what is reported</b>. The driver
/// (Blender's Python, Unity's editor script) writes <c>out.json</c> — every object, its
/// transform, the camera, the engine, and the render it made with that render's size and
/// SHA-256 — and this class hands that file back, bounded, having proved it is JSON and that
/// the PNG on disk is byte for byte the one the inspection declares.
///
/// Every bound is checked before the bytes are read, and every path is resolved inside the
/// project folder (<see cref="AuthorisedRoots"/>'s resolve-then-contain), so an
/// <c>out.json</c> that names <c>..\..\Desktop\anything.png</c> — or a link planted where the
/// render should be — is <c>permission_denied</c> and nothing is read.
/// </summary>
[SupportedOSPlatform("windows")]
public static class SceneInspection
{
    /// <summary>The key <c>out.json</c> carries its render under.</summary>
    public const string RenderKey = "render";

    /// <summary>The most a render's declared size may be — the same bound the bytes are read under.</summary>
    public static long MaxRenderBytes => SceneCapabilityNames.MaxRenderBytes;

    /// <summary>
    /// The driver's inspection, parsed. <c>postcondition_failed</c> when the file is absent
    /// (nothing has run yet, or the run did not get that far), larger than the bound, or not
    /// JSON — the whole point of the file is that it IS the tool's answer, so a file that
    /// cannot be parsed is not an answer.
    /// </summary>
    public static JsonObject ReadInspection(string projectFolder, string slug)
    {
        var path = Confine(projectFolder, SceneCapabilityNames.InspectionFileName, "the inspection");
        var info = new FileInfo(path);
        if (!info.Exists)
        {
            throw Postcondition(
                $"'{slug}' has no {SceneCapabilityNames.InspectionFileName} yet: nothing has been run in it, or the run ended before the driver wrote its read-back",
                "inspection_missing");
        }

        if (info.Length > SceneCapabilityNames.MaxInspectionBytes)
        {
            throw Postcondition(
                $"'{slug}' has an {SceneCapabilityNames.InspectionFileName} of {info.Length.ToString(CultureInfo.InvariantCulture)} bytes, past the {SceneCapabilityNames.MaxInspectionBytes.ToString(CultureInfo.InvariantCulture)} byte bound; it was not read",
                "inspection_bound");
        }

        string text;
        try
        {
            text = File.ReadAllText(path, Encoding.UTF8);
        }
        catch (IOException ex)
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"'{slug}' has an {SceneCapabilityNames.InspectionFileName} that could not be read: {ex.Message}", retryable: true);
        }

        JsonNode? node;
        try
        {
            node = JsonNode.Parse(text);
        }
        catch (JsonException ex)
        {
            throw Postcondition($"'{slug}' has an {SceneCapabilityNames.InspectionFileName} that is not JSON ({ex.Message}); the tool did not finish writing its read-back", "inspection_not_json");
        }

        if (node is not JsonObject inspection)
        {
            throw Postcondition($"'{slug}' has an {SceneCapabilityNames.InspectionFileName} whose top level is not a JSON object", "inspection_not_json");
        }

        return (JsonObject)inspection.DeepClone();
    }

    /// <summary>
    /// The render the inspection declares (<c>render: {path, bytes|size, sha256}</c>), read
    /// and verified — or null when the inspection declares none (a plan that only inspected).
    /// The hash is the driver's own: a PNG that no longer matches it is
    /// <c>postcondition_failed</c>, not a picture handed on with a shrug.
    /// </summary>
    public static SceneRender? ReadRender(string projectFolder, string slug, JsonObject inspection)
    {
        if (inspection[RenderKey] is not JsonObject render)
        {
            return null;
        }

        var relative = StringOf(render, "path")
            ?? throw Postcondition($"'{slug}' declares a {RenderKey} with no 'path'", "render_undeclared");
        var declaredSha = StringOf(render, "sha256")
            ?? throw Postcondition($"'{slug}' declares a {RenderKey} with no 'sha256'; a render nobody hashed is not a proof", "render_undeclared");
        if (declaredSha.Length != 64 || !declaredSha.All(Uri.IsHexDigit))
        {
            throw Postcondition($"'{slug}' declares a {RenderKey} whose sha256 is not 64 hex characters", "render_undeclared");
        }

        var path = Confine(projectFolder, relative, "the render");
        var info = new FileInfo(path);
        if (!info.Exists)
        {
            throw Postcondition($"'{slug}' declares a {RenderKey} at '{relative}' that is not there", "render_missing");
        }

        if (info.Length > SceneCapabilityNames.MaxRenderBytes)
        {
            throw Postcondition(
                $"'{slug}' declares a {RenderKey} of {info.Length.ToString(CultureInfo.InvariantCulture)} bytes, past the {SceneCapabilityNames.MaxRenderBytes.ToString(CultureInfo.InvariantCulture)} byte bound; it was not read",
                "render_bound");
        }

        byte[] content;
        try
        {
            content = File.ReadAllBytes(path);
        }
        catch (IOException ex)
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"'{slug}' has a render that could not be read: {ex.Message}", retryable: true);
        }

        var actual = Convert.ToHexStringLower(SHA256.HashData(content));
        if (!string.Equals(actual, declaredSha, StringComparison.OrdinalIgnoreCase))
        {
            throw Postcondition(
                $"'{slug}' declares a {RenderKey} at '{relative}' whose sha256 is not the file's ({actual[..12]}… on disk); the picture is not the one the tool made",
                "render_sha256_mismatch");
        }

        return new SceneRender(relative.Replace('\\', '/'), path, content.LongLength, actual, content);
    }

    /// <summary>The key <c>out.json</c> lists its exported scene files under.</summary>
    public const string ExportsKey = "exports";

    /// <summary>
    /// B44 (req 527): the exported scene files the inspection declares
    /// (<c>exports: [{format, path, bytes, sha256}]</c>), each verified IN PLACE — resolved
    /// inside the project, bounded, re-hashed against the driver's own sha256, and its format
    /// signature read (a GLB's <c>glTF</c> header, version 2, declaring the file's own length;
    /// an FBX binary's <c>Kaydara FBX Binary</c> magic). The bytes stay on the owner's disk;
    /// what travels is the proof. Nothing declared is an empty list, not a failure.
    /// </summary>
    public static IReadOnlyList<SceneExport> ReadExports(string projectFolder, string slug, JsonObject inspection)
    {
        if (inspection[ExportsKey] is not JsonArray declared || declared.Count == 0)
        {
            return [];
        }

        if (declared.Count > SceneCapabilityNames.MaxExports)
        {
            throw Postcondition(
                $"'{slug}' declares {declared.Count.ToString(CultureInfo.InvariantCulture)} exports, past the bound of {SceneCapabilityNames.MaxExports.ToString(CultureInfo.InvariantCulture)}",
                "exports_bound");
        }

        var exports = new List<SceneExport>();
        foreach (var node in declared)
        {
            if (node is not JsonObject entry)
            {
                throw Postcondition($"'{slug}' declares an export that is not an object", "export_undeclared");
            }

            var format = StringOf(entry, "format");
            if (format is not ("glb" or "fbx"))
            {
                throw Postcondition($"'{slug}' declares an export whose format is not glb or fbx", "export_undeclared");
            }

            var relative = StringOf(entry, "path")
                ?? throw Postcondition($"'{slug}' declares a {format} export with no 'path'", "export_undeclared");
            var declaredSha = StringOf(entry, "sha256")
                ?? throw Postcondition($"'{slug}' declares a {format} export with no 'sha256'; a file nobody hashed is not a proof", "export_undeclared");
            if (declaredSha.Length != 64 || !declaredSha.All(Uri.IsHexDigit))
            {
                throw Postcondition($"'{slug}' declares a {format} export whose sha256 is not 64 hex characters", "export_undeclared");
            }

            var path = Confine(projectFolder, relative, "the export");
            var info = new FileInfo(path);
            if (!info.Exists)
            {
                throw Postcondition($"'{slug}' declares a {format} export at '{relative}' that is not there", "export_missing");
            }

            if (info.Length > SceneCapabilityNames.MaxExportBytes)
            {
                throw Postcondition(
                    $"'{slug}' declares a {format} export of {info.Length.ToString(CultureInfo.InvariantCulture)} bytes, past the {SceneCapabilityNames.MaxExportBytes.ToString(CultureInfo.InvariantCulture)} byte bound; it was not read",
                    "export_bound");
            }

            var head = new byte[32];
            int headLength;
            string actual;
            try
            {
                using var stream = File.OpenRead(path);
                headLength = stream.ReadAtLeast(head, head.Length, throwOnEndOfStream: false);
                stream.Position = 0;
                actual = Convert.ToHexStringLower(SHA256.HashData(stream));
            }
            catch (IOException ex)
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"'{slug}' has an export that could not be read: {ex.Message}", retryable: true);
            }

            if (!string.Equals(actual, declaredSha, StringComparison.OrdinalIgnoreCase))
            {
                throw Postcondition(
                    $"'{slug}' declares a {format} export at '{relative}' whose sha256 is not the file's ({actual[..12]}… on disk); the file is not the one the tool wrote",
                    "export_sha256_mismatch");
            }

            if (!SignatureOk(format, head.AsSpan(0, headLength), info.Length))
            {
                throw Postcondition(
                    $"'{slug}' declares a {format} export at '{relative}' whose bytes do not begin the way a {format} file does",
                    "export_signature_mismatch");
            }

            exports.Add(new SceneExport(format, relative.Replace('\\', '/'), info.Length, actual));
        }

        return exports;
    }

    /// <summary>A format's own first bytes: a GLB header (<c>glTF</c>, version 2, declaring the file's length); an FBX binary magic.</summary>
    public static bool SignatureOk(string format, ReadOnlySpan<byte> head, long length)
    {
        if (format == "glb")
        {
            return head.Length >= 12
                && head[..4].SequenceEqual("glTF"u8)
                && BitConverter.ToUInt32(head[4..8]) == 2
                && BitConverter.ToUInt32(head[8..12]) == length;
        }

        if (format == "fbx")
        {
            return head.Length >= 21 && head[..21].SequenceEqual("Kaydara FBX Binary  \0"u8);
        }

        return false;
    }

    /// <summary>
    /// One path INSIDE the project folder: relative, normalised the way the scaffold
    /// normalises a file path (no <c>..</c>, no drive, no leading separator), then RESOLVED
    /// (every junction and link followed) and required to lie inside the folder. A path the
    /// inspection invented that points anywhere else is <c>permission_denied</c> and nothing
    /// is opened.
    ///
    /// The DIRECTORY is what is resolved, so a file that is not there yet still gets a path
    /// and the caller can say "there is no inspection yet" rather than "you may not look" —
    /// the two are different answers and the Cloud Core needs to tell them apart. When the
    /// file does exist it is resolved as well, so a link planted where the render belongs is
    /// followed and refused before a byte is read.
    /// </summary>
    public static string Confine(string projectFolder, string relative, string what)
    {
        string normalised;
        try
        {
            normalised = Projects.ProjectScaffold.NormalisePath(relative, what);
        }
        catch (CapabilityException)
        {
            throw DocumentErrors.Denied($"{what} must be a relative path inside the project (no '..', no drive, no separator first); nothing was read");
        }

        var candidate = Path.Combine(projectFolder, normalised.Replace('/', Path.DirectorySeparatorChar));
        var directory = Path.GetDirectoryName(candidate);
        var resolvedDirectory = directory is null ? null : AuthorisedRoots.ResolveFinal(directory);
        if (resolvedDirectory is null || !Directory.Exists(resolvedDirectory) || !AuthorisedRoots.IsWithin(resolvedDirectory, projectFolder))
        {
            throw Outside(what);
        }

        var resolved = Path.Combine(resolvedDirectory, Path.GetFileName(candidate));
        var final = AuthorisedRoots.ResolveFinal(resolved);
        if (final is null)
        {
            // Nothing opens there yet: the path is inside, and the caller reports the absence.
            return resolved;
        }

        return AuthorisedRoots.IsWithin(final, projectFolder) ? final : throw Outside(what);
    }

    private static CapabilityException Outside(string what)
        => DocumentErrors.Denied($"{what} does not resolve to a path inside the project folder; every junction and link is followed before the comparison; nothing was read");

    private static string? StringOf(JsonObject node, string key)
        => node[key]?.GetValueKind() == JsonValueKind.String ? node[key]!.GetValue<string>() : null;

    private static CapabilityException Postcondition(string message, string detail)
        => new(ErrorClasses.PostconditionFailed, message, retryable: false, new Dictionary<string, object?> { [DocumentErrors.DetailKey] = detail });
}
