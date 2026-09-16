using System.Diagnostics;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Native;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>
/// B33 requirements 456/457/468/469/471 (DEVICE_PROTOCOL.md §6l, appended): what happens to a
/// native application AFTER it is built — packaged (a portable zip, or an MSIX through
/// Windows' own <c>makeappx</c>), installed (a Start Menu shortcut to the built executable,
/// recorded), uninstalled (the shortcut and the record removed; the build folder stays), and
/// its artefact read back in bounded chunks so a Cloud Core that cannot see this disk can
/// still hand the file to the owner.
/// </summary>
/// <remarks>
/// Every path is the project's own (<see cref="ProjectContext.Folder"/>, already resolved and
/// contained by the roots); nothing here takes an absolute path from the payload. Nothing
/// signs anything: <see cref="NativeCapabilityNames.ForbiddenPrograms"/> stands, and the
/// package result says <c>signed: false</c> in as many words. An "install" is deliberately the
/// smallest true thing: the executable stays where it was built, the shortcut points at it,
/// and <c>installed.json</c> under the native root is the record the uninstall reads.
/// </remarks>
public static class NativeLifecycle
{
    public const string PublishDirName = "out";
    public const string DistDirName = "dist";
    public const string StagingDirName = "staging";
    public const string InstalledRecordName = "installed.json";
    public const string ProgramsFolderName = "PagentOS";
    /// <summary>A lab overrides the Start Menu folder through this variable; the owner's session never sets it.</summary>
    public const string ProgramsDirEnvironmentVariable = "PAGENTOS_NATIVE_PROGRAMS_DIR";
    /// <summary>One <c>project.artifact</c> chunk, base64-encoded, stays well under the 48 KiB result cap.</summary>
    public const int MaxChunkBytes = 32 * 1024;
    public static readonly TimeSpan MakeAppxTimeout = TimeSpan.FromMinutes(5);

    // ================================================================== project.package

    public static JsonObject Package(ProjectContext project, JsonObject payload, ILogger logger, CancellationToken cancellationToken)
    {
        var kind = OptionalString(payload, "kind") ?? "portable";
        if (kind is not ("portable" or "msix"))
        {
            throw DocumentErrors.Invalid("payload.kind must be \"portable\" or \"msix\"");
        }

        var publishDir = Path.Combine(project.Folder, OptionalString(payload, "publish_dir") ?? PublishDirName);
        if (!Directory.Exists(publishDir))
        {
            throw DocumentErrors.NotFound($"no publish output at {Path.GetRelativePath(project.Folder, publishDir)}; publish first");
        }

        var exe = Path.Combine(publishDir, project.Slug + ".exe");
        if (!File.Exists(exe))
        {
            throw DocumentErrors.NotFound($"{project.Slug}.exe is not in the publish output; nothing to package");
        }

        var dist = Path.Combine(project.Folder, DistDirName);
        Directory.CreateDirectory(dist);
        string outPath;
        if (kind == "portable")
        {
            outPath = Path.Combine(dist, project.Slug + "-portable.zip");
            if (File.Exists(outPath))
            {
                File.Delete(outPath);
            }

            ZipFile.CreateFromDirectory(publishDir, outPath, CompressionLevel.Optimal, includeBaseDirectory: false);
        }
        else
        {
            var manifestRelative = OptionalString(payload, "manifest_path") ?? Path.Combine(StagingDirName, "AppxManifest.xml");
            var manifest = Path.Combine(project.Folder, manifestRelative);
            if (!File.Exists(manifest))
            {
                throw DocumentErrors.NotFound($"no AppxManifest.xml at {manifestRelative}; the Cloud Core scaffolds it for an MSIX target");
            }

            var makeappx = NativeTools.FindMakeAppx()
                ?? throw new CapabilityException(ErrorClasses.DependencyUnavailable, "makeappx.exe is not installed on this device (Windows 10 SDK); an MSIX cannot be packed here", retryable: false);
            var staging = Path.Combine(project.Folder, StagingDirName, "pack");
            if (Directory.Exists(staging))
            {
                Directory.Delete(staging, recursive: true);
            }

            CopyDirectory(publishDir, staging);
            File.Copy(manifest, Path.Combine(staging, "AppxManifest.xml"), overwrite: true);
            var assets = Path.Combine(project.Folder, StagingDirName, "Assets");
            if (Directory.Exists(assets))
            {
                CopyDirectory(assets, Path.Combine(staging, "Assets"));
            }

            outPath = Path.Combine(dist, project.Slug + ".msix");
            if (File.Exists(outPath))
            {
                File.Delete(outPath);
            }

            RunMakeAppx(makeappx.Executable, staging, outPath, logger, cancellationToken);
            if (!File.Exists(outPath))
            {
                throw new CapabilityException(ErrorClasses.DependencyUnavailable, "makeappx exited without producing the package", retryable: false);
            }
        }

        var info = new FileInfo(outPath);
        return new JsonObject
        {
            ["kind"] = kind,
            ["path"] = outPath,
            ["name"] = info.Name,
            ["bytes"] = info.Length,
            ["sha256"] = Sha256Of(outPath),
            ["signed"] = false,
            ["observed"] = new JsonObject { ["exists"] = true, ["bytes"] = info.Length },
        };
    }

    // ================================================================== project.install

    public static JsonObject Install(ProjectContext project, JsonObject payload, string nativeRoot)
    {
        var exeRelative = OptionalString(payload, "exe") ?? Path.Combine(PublishDirName, project.Slug + ".exe");
        var exe = Path.GetFullPath(Path.Combine(project.Folder, exeRelative));
        if (!exe.StartsWith(Path.GetFullPath(project.Folder) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
        {
            throw DocumentErrors.Denied("payload.exe must name a file inside the project's own folder");
        }

        if (!File.Exists(exe))
        {
            throw DocumentErrors.NotFound($"{exeRelative} does not exist; build and publish first");
        }

        var name = SafeName(OptionalString(payload, "name") ?? project.Slug);
        var programs = ProgramsDir();
        Directory.CreateDirectory(programs);
        var shortcut = Path.Combine(programs, name + ".lnk");
        CreateShortcut(shortcut, exe, Path.GetDirectoryName(exe)!, name);

        var record = ReadRecord(nativeRoot);
        record[project.ProjectId] = new JsonObject
        {
            ["project_id"] = project.ProjectId,
            ["slug"] = project.Slug,
            ["name"] = name,
            ["exe"] = exe,
            ["shortcut"] = shortcut,
            ["sha256"] = Sha256Of(exe),
            ["installed_at"] = DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", System.Globalization.CultureInfo.InvariantCulture),
        };
        WriteRecord(nativeRoot, record);

        return new JsonObject
        {
            ["installed"] = File.Exists(shortcut),
            ["method"] = "start_menu_shortcut",
            ["name"] = name,
            ["exe"] = exe,
            ["shortcut"] = shortcut,
            ["observed"] = new JsonObject { ["shortcut_exists"] = File.Exists(shortcut), ["exe_exists"] = File.Exists(exe) },
        };
    }

    // ================================================================== project.uninstall

    public static JsonObject Uninstall(ProjectContext project, string nativeRoot)
    {
        var record = ReadRecord(nativeRoot);
        if (record[project.ProjectId] is not JsonObject entry)
        {
            throw DocumentErrors.NotFound($"'{project.Slug}' is not installed by this system; nothing to remove");
        }

        var shortcut = entry["shortcut"]?.GetValue<string>();
        var removed = false;
        if (!string.IsNullOrEmpty(shortcut) && File.Exists(shortcut))
        {
            File.Delete(shortcut);
            removed = true;
        }

        record.Remove(project.ProjectId);
        WriteRecord(nativeRoot, record);
        return new JsonObject
        {
            ["uninstalled"] = true,
            ["shortcut_removed"] = removed,
            ["shortcut"] = shortcut,
            ["build_kept"] = true,
            ["observed"] = new JsonObject { ["shortcut_exists"] = !string.IsNullOrEmpty(shortcut) && File.Exists(shortcut) },
        };
    }

    // ================================================================== project.artifact

    public static JsonObject Artifact(ProjectContext project, JsonObject payload)
    {
        var relative = OptionalString(payload, "path") ?? Path.Combine(PublishDirName, project.Slug + ".exe");
        var full = Path.GetFullPath(Path.Combine(project.Folder, relative));
        if (!full.StartsWith(Path.GetFullPath(project.Folder) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
        {
            throw DocumentErrors.Denied("payload.path must name a file inside the project's own folder");
        }

        if (!File.Exists(full))
        {
            throw DocumentErrors.NotFound($"{relative} does not exist");
        }

        var info = new FileInfo(full);
        if (info.Length > DocumentCapabilityNames.MaxFileBytes)
        {
            throw DocumentErrors.Unsupported($"{relative} is {info.Length} bytes, over the {DocumentCapabilityNames.MaxFileBytes} byte bound", DocumentErrors.TooLarge);
        }

        var offset = Math.Max(0L, payload["offset"]?.GetValue<long>() ?? 0L);
        var length = Math.Clamp(payload["length"]?.GetValue<int>() ?? MaxChunkBytes, 1, MaxChunkBytes);
        var buffer = new byte[length];
        var read = 0;
        using (var stream = new FileStream(full, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            if (offset < stream.Length)
            {
                stream.Seek(offset, SeekOrigin.Begin);
                read = stream.Read(buffer, 0, length);
            }
        }

        return new JsonObject
        {
            ["path"] = full,
            ["name"] = info.Name,
            ["bytes"] = info.Length,
            ["sha256"] = Sha256Of(full),
            ["offset"] = offset,
            ["length"] = read,
            ["base64"] = Convert.ToBase64String(buffer, 0, read),
            ["eof"] = offset + read >= info.Length,
        };
    }

    // ================================================================== helpers

    private static string ProgramsDir()
    {
        var fromEnvironment = Environment.GetEnvironmentVariable(ProgramsDirEnvironmentVariable);
        if (!string.IsNullOrWhiteSpace(fromEnvironment))
        {
            return fromEnvironment;
        }

        return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), ProgramsFolderName);
    }

    private static string RecordPath(string nativeRoot) => Path.Combine(nativeRoot, InstalledRecordName);

    private static JsonObject ReadRecord(string nativeRoot)
    {
        var path = RecordPath(nativeRoot);
        if (!File.Exists(path))
        {
            return new JsonObject();
        }

        try
        {
            return JsonNode.Parse(File.ReadAllText(path)) as JsonObject ?? new JsonObject();
        }
        catch (System.Text.Json.JsonException)
        {
            return new JsonObject();
        }
    }

    private static void WriteRecord(string nativeRoot, JsonObject record)
    {
        Directory.CreateDirectory(nativeRoot);
        File.WriteAllText(RecordPath(nativeRoot), record.ToJsonString(new System.Text.Json.JsonSerializerOptions { WriteIndented = true }));
    }

    private static void CreateShortcut(string shortcutPath, string target, string workingDirectory, string description)
    {
        try
        {
            ShellLinkInterop.CreateShortcut(shortcutPath, target, workingDirectory, description);
        }
        catch (COMException exception)
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"the shell could not write the shortcut (0x{exception.HResult:X8}): {exception.Message}", retryable: false);
        }
    }

    private static void RunMakeAppx(string makeappx, string staging, string outPath, ILogger logger, CancellationToken cancellationToken)
    {
        var startInfo = new ProcessStartInfo(makeappx)
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        foreach (var argument in new[] { "pack", "/d", staging, "/p", outPath, "/o", "/nv" })
        {
            startInfo.ArgumentList.Add(argument);
        }

        using var process = Process.Start(startInfo)
            ?? throw new CapabilityException(ErrorClasses.DependencyUnavailable, "makeappx did not start", retryable: true);
        var stdout = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var stderr = process.StandardError.ReadToEndAsync(cancellationToken);
        if (!process.WaitForExit((int)MakeAppxTimeout.TotalMilliseconds))
        {
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch (Exception)
            {
                // Already gone.
            }

            throw new CapabilityException(ErrorClasses.Timeout, $"makeappx did not finish within {MakeAppxTimeout.TotalMinutes:0} min", retryable: true);
        }

        var log = (stdout.GetAwaiter().GetResult() + stderr.GetAwaiter().GetResult()).Trim();
        logger.LogInformation("project.package makeappx exit={ExitCode}", process.ExitCode);
        if (process.ExitCode != 0)
        {
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"makeappx failed ({process.ExitCode}): {(log.Length > 600 ? log[^600..] : log)}", retryable: false);
        }
    }

    private static void CopyDirectory(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (var file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
        {
            var relative = Path.GetRelativePath(source, file);
            var target = Path.Combine(destination, relative);
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.Copy(file, target, overwrite: true);
        }
    }

    private static string Sha256Of(string path)
    {
        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static string SafeName(string name)
    {
        var cleaned = new string(name.Where(ch => !Path.GetInvalidFileNameChars().Contains(ch)).ToArray()).Trim();
        return string.IsNullOrEmpty(cleaned) ? "uygulama" : (cleaned.Length > 64 ? cleaned[..64] : cleaned);
    }

    private static string? OptionalString(JsonObject payload, string key)
    {
        var node = payload[key];
        if (node is null)
        {
            return null;
        }

        var value = node.GetValue<string>();
        return string.IsNullOrWhiteSpace(value) ? null : value;
    }
}
