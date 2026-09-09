using System.Globalization;
using System.Runtime.Versioning;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Projects;

namespace PagentOS.SessionCompanion.Native;

/// <summary>Where a native build tool was found, and what it is — for the log and for a lab that reports which toolchain it drove.</summary>
public sealed record NativeTool(string Executable, string Version, string Source);

/// <summary>
/// M28_NATIVE_APP_FACTORY_SPEC.md §1/§5, ADR-0095 decisions 1 and 4 — finding the two build
/// tools on the owner's machine. The Cloud Core measures the same two in
/// <c>app/nativefactory/stacks.py</c> (<c>detect</c>, <c>_windows_kit_tool</c>) and this is the
/// device's half of the same measurement, deliberately looking in the same places.
///
/// <c>dotnet</c> is looked for where the .NET installer puts it FIRST (<c>%ProgramFiles%\
/// dotnet\dotnet.exe</c>) and only then on PATH, which is the Cloud Core's order and the safer
/// one: a <c>dotnet.exe</c> planted earlier on PATH would otherwise be what "dotnet" means.
/// <c>makeappx</c> is never looked for on PATH at all — it is not there on a normal machine,
/// and the Windows Kits' own layout says exactly where it is.
///
/// Nothing here starts a process. The Cloud Core runs <c>dotnet --version</c> to learn the SDK
/// version; this class does not, because the device does not need the number and a detection
/// that spawns a process is a detection that can hang.
///
/// <c>signtool.exe</c> sits in the same Windows Kits directory as <c>makeappx.exe</c> and this
/// class deliberately has no function that returns it (§9): the device signs nothing, and the
/// way to keep that true is to have no code that could hand a signer its path.
/// </summary>
[SupportedOSPlatform("windows")]
public static class NativeTools
{
    /// <summary>The .NET installer's directory under Program Files.</summary>
    public const string DotnetFolderName = "dotnet";

    public const string DotnetExecutable = "dotnet.exe";

    public const string MakeAppxExecutable = "makeappx.exe";

    /// <summary>The Windows Kits' binary directory, under <c>%ProgramFiles(x86)%</c> — the Cloud Core's <c>_windows_kit_tool</c> walks exactly this.</summary>
    public static readonly string[] WindowsKitSegments = ["Windows Kits", "10", "bin"];

    /// <summary>The architectures a Kits version directory may hold the tool under, in the order the Cloud Core tries them.</summary>
    public static readonly string[] WindowsKitArchitectures = ["x64", "x86"];

    /// <summary>
    /// The installed .NET SDK's <c>dotnet.exe</c>, or null. The installer's own location first,
    /// then the companion's PATH (with the Store's <c>WindowsApps</c> aliases skipped, as
    /// everywhere else in this agent).
    /// </summary>
    public static NativeTool? FindDotnet()
    {
        foreach (var programFiles in ProgramFilesDirectories())
        {
            var candidate = Path.Combine(programFiles, DotnetFolderName, DotnetExecutable);
            if (File.Exists(candidate) && !ProjectRoots.IsReparsePoint(candidate))
            {
                return new NativeTool(Path.GetFullPath(candidate), SdkVersion(Path.Combine(programFiles, DotnetFolderName)), "the .NET installer's directory");
            }
        }

        var onPath = ProjectRunner.FindOnPath(DotnetExecutable);
        return onPath is null ? null : new NativeTool(onPath, SdkVersion(Path.GetDirectoryName(onPath)), "PATH");
    }

    /// <summary>
    /// The Windows Kits' <c>makeappx.exe</c>, or null: the highest version directory under
    /// <c>&lt;Program Files (x86)&gt;\Windows Kits\10\bin</c> that holds it, x64 before x86.
    /// </summary>
    public static NativeTool? FindMakeAppx()
    {
        foreach (var programFiles in ProgramFilesDirectories())
        {
            var bin = Path.Combine([programFiles, .. WindowsKitSegments]);
            if (!Directory.Exists(bin))
            {
                continue;
            }

            (string Executable, string Version, long[] Key)? best = null;
            IEnumerable<string> versions;
            try
            {
                versions = Directory.EnumerateDirectories(bin);
            }
            catch (Exception)
            {
                continue;
            }

            foreach (var version in versions)
            {
                foreach (var architecture in WindowsKitArchitectures)
                {
                    var candidate = Path.Combine(version, architecture, MakeAppxExecutable);
                    if (!File.Exists(candidate))
                    {
                        continue;
                    }

                    var name = Path.GetFileName(version) ?? string.Empty;
                    var key = VersionKey(name);
                    if (best is null || Compare(key, best.Value.Key) > 0)
                    {
                        best = (Path.GetFullPath(candidate), $"{name} {architecture}", key);
                    }

                    break;
                }
            }

            if (best is not null)
            {
                return new NativeTool(best.Value.Executable, best.Value.Version, "Windows Kits 10 bin directory");
            }
        }

        return null;
    }

    /// <summary>The tool a native runtime resolves to, or <c>dependency_unavailable</c> naming what is missing and where it was looked for.</summary>
    public static NativeTool Require(ProjectRuntime runtime)
        => runtime switch
        {
            ProjectRuntime.Dotnet => FindDotnet() ?? throw Missing(
                "The .NET SDK",
                @"no dotnet.exe was found (Program Files\dotnet, then PATH without the Store aliases)"),
            ProjectRuntime.MakeAppx => FindMakeAppx() ?? throw Missing(
                "makeappx",
                @"no makeappx.exe was found (Windows Kits\10\bin\<version>\x64 under Program Files); PATH is never searched for it"),
            _ => throw new CapabilityException(ErrorClasses.InternalBug, $"{runtime} is not a native build runtime", retryable: false),
        };

    /// <summary>The tools this machine has, as one line for the companion's startup log.</summary>
    public static string Describe()
    {
        var dotnet = FindDotnet();
        var makeappx = FindMakeAppx();
        return string.Create(
            CultureInfo.InvariantCulture,
            $"dotnet={(dotnet is null ? "(not installed)" : dotnet.Version + " @ " + dotnet.Executable)} makeappx={(makeappx is null ? "(not installed)" : makeappx.Version + " @ " + makeappx.Executable)}");
    }

    /// <summary>The highest SDK directory name under <c>&lt;dotnet root&gt;\sdk</c> — read from the file system, never by running the CLI.</summary>
    private static string SdkVersion(string? dotnetRoot)
    {
        if (string.IsNullOrWhiteSpace(dotnetRoot))
        {
            return string.Empty;
        }

        try
        {
            var sdk = Path.Combine(dotnetRoot, "sdk");
            if (!Directory.Exists(sdk))
            {
                return string.Empty;
            }

            string best = string.Empty;
            long[] bestKey = [];
            foreach (var child in Directory.EnumerateDirectories(sdk))
            {
                var name = Path.GetFileName(child) ?? string.Empty;
                var key = VersionKey(name);
                if (best.Length == 0 || Compare(key, bestKey) > 0)
                {
                    best = name;
                    bestKey = key;
                }
            }

            return best;
        }
        catch (Exception)
        {
            return string.Empty;
        }
    }

    private static CapabilityException Missing(string what, string detail)
        => new(
            ErrorClasses.DependencyUnavailable,
            $"{what} is not on this machine: {detail}; nothing was run",
            retryable: false,
            new Dictionary<string, object?> { [DocumentErrors.DetailKey] = "runtime_missing" });

    private static IEnumerable<string> ProgramFilesDirectories()
    {
        var seen = new List<string>();
        foreach (var folder in new[] { Environment.SpecialFolder.ProgramFiles, Environment.SpecialFolder.ProgramFilesX86 })
        {
            var path = Environment.GetFolderPath(folder);
            if (!string.IsNullOrWhiteSpace(path) && !seen.Contains(path, StringComparer.OrdinalIgnoreCase))
            {
                seen.Add(path);
                yield return path;
            }
        }
    }

    /// <summary>The numeric components of a version-shaped directory name (<c>10.0.26100.0</c>, <c>10.0.400</c>), for ordering only.</summary>
    private static long[] VersionKey(string text)
    {
        var parts = new List<long>();
        var digits = 0;
        long value = 0;
        foreach (var ch in text)
        {
            if (char.IsAsciiDigit(ch))
            {
                value = Math.Min((value * 10) + (ch - '0'), int.MaxValue);
                digits++;
                continue;
            }

            if (digits > 0)
            {
                parts.Add(value);
                value = 0;
                digits = 0;
            }

            if (ch != '.' && parts.Count > 0)
            {
                break;
            }
        }

        if (digits > 0)
        {
            parts.Add(value);
        }

        return [.. parts];
    }

    private static int Compare(long[] left, long[] right)
    {
        for (var i = 0; i < Math.Max(left.Length, right.Length); i++)
        {
            var a = i < left.Length ? left[i] : 0;
            var b = i < right.Length ? right[i] : 0;
            if (a != b)
            {
                return a.CompareTo(b);
            }
        }

        return 0;
    }
}
