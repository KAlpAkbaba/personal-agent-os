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
/// contained by the roots); nothing here takes an absolute path from the payload. No signing
/// PROGRAM is ever run (<see cref="NativeCapabilityNames.ForbiddenPrograms"/> stands). Since B33
/// req 473 an MSIX asked for with <c>signing_mode: "test_certificate"</c> is signed in this
/// process with the owner's self-signed identity and answers <c>signed: true</c> only after the
/// signature READ BACK intact; every other package says <c>signed: false</c>. A default
/// "install" is deliberately the smallest true thing: the executable stays where it was built,
/// the shortcut points at it, and <c>installed.json</c> under the native root is the record the
/// uninstall reads; an <c>msix</c> install is a per-user package registration, refused with the
/// owner's trust step named while the signer is not trusted on this machine.
/// </remarks>
[System.Runtime.Versioning.SupportedOSPlatform("windows")]
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

    /// <summary><c>project.install</c>'s default: a Start Menu shortcut to the built executable.</summary>
    public const string InstallKindShortcut = "shortcut";

    /// <summary>B33 req 473: the signed MSIX, installed for the current user.</summary>
    public const string InstallKindMsix = "msix";

    // ================================================================== project.package

    public static JsonObject Package(ProjectContext project, JsonObject payload, NativeSigning signing, ILogger logger, CancellationToken cancellationToken)
    {
        var kind = OptionalString(payload, "kind") ?? "portable";
        if (kind is not ("portable" or "msix"))
        {
            throw DocumentErrors.Invalid("payload.kind must be \"portable\" or \"msix\"");
        }

        var signingMode = SigningModeOf(payload);

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

            if (signingMode == NativeCapabilityNames.SigningModeTestCertificate)
            {
                RequirePublisherIsTheSigner(manifest, manifestRelative, signing.Identity.Options.Subject);
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

            if (signingMode == NativeCapabilityNames.SigningModeTestCertificate)
            {
                var signed = SignAndReadBack(outPath, signing, logger);
                return PackageAnswer(kind, outPath, signed);
            }
        }

        var answer = PackageAnswer(kind, outPath, signature: null);
        if (kind == "portable" && signingMode != NativeCapabilityNames.SigningModeUnsigned)
        {
            // The portable zip is not signed, whatever was asked (DEVICE_PROTOCOL.md §6n): its
            // executable was already read back and hashed by the Cloud Core, and signing it
            // afterwards would make that hash a statement about a file that no longer exists.
            answer["signing_note"] = "portable_not_signed";
        }

        return answer;
    }

    /// <summary>What a package answer says: the file as observed, and its signature as READ BACK — never as intended.</summary>
    private static JsonObject PackageAnswer(string kind, string outPath, SignedPackage? signature)
    {
        var info = new FileInfo(outPath);
        var observed = new JsonObject { ["exists"] = true, ["bytes"] = info.Length };
        var answer = new JsonObject
        {
            ["kind"] = kind,
            ["path"] = outPath,
            ["name"] = info.Name,
            ["bytes"] = info.Length,
            ["sha256"] = Sha256Of(outPath),
            ["signed"] = signature is not null,
            ["signing_mode"] = signature is null ? NativeCapabilityNames.SigningModeUnsigned : NativeCapabilityNames.SigningModeTestCertificate,
        };
        if (signature is not null)
        {
            foreach (var (key, value) in signature.Facts.ToJson())
            {
                answer[key] = value?.DeepClone();
            }

            answer["trusted"] = signature.Trusted;
            if (!signature.Trusted)
            {
                answer["trust_step"] = NativeCapabilityNames.TrustScript;
            }

            observed["signature_intact"] = signature.ReadBack.Intact;
            observed["verify_status"] = signature.ReadBack.VerifyStatusHex;
        }

        answer["observed"] = observed;
        return answer;
    }

    private sealed record SignedPackage(SigningCertificateFacts Facts, PackageSignatureReadBack ReadBack, bool Trusted);

    /// <summary>
    /// Signs the packed MSIX in this process and reads the signature back through two readers
    /// that did not write it. A package whose signature does not read back is DELETED, so no
    /// file is left at the answered path claiming what it is not.
    /// </summary>
    private static SignedPackage SignAndReadBack(string outPath, NativeSigning signing, ILogger logger)
    {
        SigningCertificateFacts facts;
        try
        {
            using var certificate = signing.Identity.Acquire(out facts);
            if (facts.Created)
            {
                logger.LogWarning(
                    "project.package created the self-signed signing identity {Thumbprint} (valid to {NotAfter:yyyy-MM-dd}); it is not trusted on this machine until the owner runs {Script}",
                    facts.Thumbprint,
                    facts.NotAfter,
                    NativeCapabilityNames.TrustScript);
            }

            signing.SignMsix(outPath, certificate);
        }
        catch (CryptographicException exception)
        {
            TryDelete(outPath);
            throw new CapabilityException(ErrorClasses.DependencyUnavailable, $"signing_failed: {exception.Message}; the unsigned package was removed", retryable: false);
        }

        var readBack = PackageSigner.Verify(outPath);
        if (!readBack.Signed || !string.Equals(readBack.SignerThumbprint, facts.Thumbprint, StringComparison.OrdinalIgnoreCase))
        {
            TryDelete(outPath);
            throw new CapabilityException(
                ErrorClasses.PostconditionFailed,
                $"the package was signed but the signature did not read back ({readBack.VerifyStatusHex}: {readBack.Detail}); the package was removed rather than answered as signed",
                retryable: false);
        }

        var trusted = readBack.ChainTrusted || signing.Identity.IsTrusted(facts.Thumbprint);
        logger.LogInformation("project.package signed by {Thumbprint}; read back {Status}; trusted={Trusted}", facts.Thumbprint, readBack.VerifyStatusHex, trusted);
        return new SignedPackage(facts, readBack, trusted);
    }

    private static string SigningModeOf(JsonObject payload)
    {
        var mode = OptionalString(payload, "signing_mode") ?? NativeCapabilityNames.SigningModeUnsigned;
        if (mode == NativeCapabilityNames.SigningModeOwnerCertificate)
        {
            throw DocumentErrors.Denied($"signing_mode '{NativeCapabilityNames.SigningModeOwnerCertificate}' is refused: the owner's own code-signing identity is theirs and this device never reaches for it");
        }

        if (mode is not (NativeCapabilityNames.SigningModeUnsigned or NativeCapabilityNames.SigningModeTestCertificate))
        {
            throw DocumentErrors.Invalid($"payload.signing_mode must be \"{NativeCapabilityNames.SigningModeUnsigned}\" or \"{NativeCapabilityNames.SigningModeTestCertificate}\"");
        }

        return mode;
    }

    private static void RequirePublisherIsTheSigner(string manifestPath, string manifestRelative, string subject)
    {
        string publisher;
        try
        {
            using var stream = new FileStream(manifestPath, FileMode.Open, FileAccess.Read, FileShare.Read);
            publisher = MsixIdentity.FromManifest(stream).Publisher;
        }
        catch (Exception exception) when (exception is InvalidDataException or System.Xml.XmlException)
        {
            throw DocumentErrors.Invalid($"{manifestRelative} does not declare a readable package identity: {exception.Message}");
        }

        if (!string.Equals(publisher, subject, StringComparison.Ordinal))
        {
            throw DocumentErrors.Invalid($"{manifestRelative} names Publisher '{publisher}'; a package this device signs must name '{subject}', its signing certificate's subject");
        }
    }

    private static void TryDelete(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch (IOException)
        {
            // The answer is a refusal either way.
        }
    }

    // ================================================================== project.install

    public static JsonObject Install(ProjectContext project, JsonObject payload, string nativeRoot, NativeSigning signing)
    {
        var kind = OptionalString(payload, "kind") ?? InstallKindShortcut;
        if (kind == InstallKindMsix)
        {
            return InstallMsix(project, payload, nativeRoot, signing);
        }

        if (kind != InstallKindShortcut)
        {
            throw DocumentErrors.Invalid($"payload.kind must be \"{InstallKindShortcut}\" or \"{InstallKindMsix}\"");
        }

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

    public static JsonObject Uninstall(ProjectContext project, string nativeRoot, NativeSigning signing)
    {
        var record = ReadRecord(nativeRoot);
        if (record[project.ProjectId] is not JsonObject entry)
        {
            throw DocumentErrors.NotFound($"'{project.Slug}' is not installed by this system; nothing to remove");
        }

        if (entry["method"]?.GetValue<string>() == InstallKindMsix)
        {
            return UninstallMsix(project, nativeRoot, record, entry, signing);
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

    // ================================================ project.install / uninstall (msix)

    /// <summary>
    /// A signed MSIX, installed for the current user by Windows' own package manager. Three
    /// refusals come first and each names its reason: no package; a package whose signature
    /// does not read back intact (<see cref="NativeCapabilityNames.UnsignedPackageMarker"/>);
    /// a signer this machine does not trust (<see cref="NativeCapabilityNames.UntrustedSignerMarker"/>,
    /// naming the owner's one elevated step). This code never elevates and never writes a
    /// LocalMachine store; Windows' own trust check stays the backstop.
    /// </summary>
    private static JsonObject InstallMsix(ProjectContext project, JsonObject payload, string nativeRoot, NativeSigning signing)
    {
        var relative = Path.Combine(DistDirName, project.Slug + ".msix");
        var package = Path.Combine(project.Folder, relative);
        if (!File.Exists(package))
        {
            throw DocumentErrors.NotFound($"{relative} does not exist; package the MSIX first");
        }

        var readBack = PackageSigner.Verify(package);
        if (!readBack.Signed)
        {
            throw DocumentErrors.Denied($"{NativeCapabilityNames.UnsignedPackageMarker}: {relative} carries no intact signature ({readBack.VerifyStatusHex}: {readBack.Detail}); this device installs only a package it signed");
        }

        if (!string.Equals(readBack.SignerSubject, signing.Identity.Options.Subject, StringComparison.Ordinal))
        {
            throw DocumentErrors.Denied($"{NativeCapabilityNames.UnsignedPackageMarker}: {relative} is signed by '{readBack.SignerSubject}', not by this device's signing identity");
        }

        var thumbprint = readBack.SignerThumbprint!;
        if (!readBack.ChainTrusted && !signing.Identity.IsTrusted(thumbprint))
        {
            throw DocumentErrors.Denied(
                $"{NativeCapabilityNames.UntrustedSignerMarker}: the package's signer {thumbprint} is not trusted on this machine; "
                + $"the owner runs {NativeCapabilityNames.TrustScript} once, elevated, to trust it (this device never elevates)");
        }

        MsixIdentity identity;
        string fullName;
        string familyName;
        try
        {
            identity = MsixIdentity.Read(package);
            (fullName, familyName) = MsixPackageNames.For(identity);
        }
        catch (Exception exception) when (exception is InvalidDataException or System.Xml.XmlException)
        {
            throw DocumentErrors.Invalid($"{relative} does not declare a readable package identity: {exception.Message}");
        }

        var outcome = signing.Deployer.Add(package);
        if (!outcome.Ok)
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"msix_install_failed ({outcome.HResultHex}): {Trim(outcome.ErrorText) ?? "Windows gave no reason"}",
                retryable: false);
        }

        if (!signing.Deployer.IsRegistered(familyName, fullName))
        {
            throw new CapabilityException(ErrorClasses.PostconditionFailed, $"Windows reported the install of {fullName} but does not list it for this user", retryable: false);
        }

        var name = SafeName(OptionalString(payload, "name") ?? project.Slug);
        var record = ReadRecord(nativeRoot);
        record[project.ProjectId] = new JsonObject
        {
            ["project_id"] = project.ProjectId,
            ["slug"] = project.Slug,
            ["name"] = name,
            ["method"] = InstallKindMsix,
            ["package"] = package,
            ["package_full_name"] = fullName,
            ["package_family_name"] = familyName,
            ["signer_thumbprint"] = thumbprint,
            ["sha256"] = Sha256Of(package),
            ["installed_at"] = DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", System.Globalization.CultureInfo.InvariantCulture),
        };
        WriteRecord(nativeRoot, record);

        return new JsonObject
        {
            ["installed"] = true,
            ["method"] = InstallKindMsix,
            ["name"] = name,
            ["package_full_name"] = fullName,
            ["package_family_name"] = familyName,
            ["signed"] = true,
            ["signer_thumbprint"] = thumbprint,
            ["trusted"] = true,
            ["observed"] = new JsonObject { ["package_registered"] = true },
        };
    }

    private static JsonObject UninstallMsix(ProjectContext project, string nativeRoot, JsonObject record, JsonObject entry, NativeSigning signing)
    {
        var fullName = entry["package_full_name"]?.GetValue<string>();
        var familyName = entry["package_family_name"]?.GetValue<string>();
        if (string.IsNullOrEmpty(fullName) || string.IsNullOrEmpty(familyName))
        {
            throw DocumentErrors.Invalid($"the install record of '{project.Slug}' names no package; it was not written by this device");
        }

        var wasRegistered = signing.Deployer.IsRegistered(familyName, fullName);
        DeploymentOutcome? outcome = null;
        if (wasRegistered)
        {
            outcome = signing.Deployer.Remove(fullName);
        }

        var stillRegistered = signing.Deployer.IsRegistered(familyName, fullName);
        if (stillRegistered)
        {
            throw new CapabilityException(
                ErrorClasses.DependencyUnavailable,
                $"msix_uninstall_failed ({outcome?.HResultHex ?? "not attempted"}): {Trim(outcome?.ErrorText) ?? "the package is still registered"}",
                retryable: false);
        }

        record.Remove(project.ProjectId);
        WriteRecord(nativeRoot, record);
        return new JsonObject
        {
            ["uninstalled"] = true,
            ["method"] = InstallKindMsix,
            ["package_full_name"] = fullName,
            ["package_removed"] = wasRegistered,
            ["build_kept"] = true,
            ["observed"] = new JsonObject { ["package_registered"] = false, ["shortcut_exists"] = false },
        };
    }

    private static string? Trim(string? text)
        => string.IsNullOrWhiteSpace(text) ? null : (text.Length > 400 ? text[..400] : text).Trim();

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
