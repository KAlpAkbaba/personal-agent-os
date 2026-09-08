using System.Globalization;
using System.Runtime.Versioning;
using Microsoft.Win32;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Projects;

namespace PagentOS.SessionCompanion.Scenes;

/// <summary>Where a 3D runtime was found, and what version it says it is — for the log and for a lab that reports which editor it drove.</summary>
public sealed record SceneTool(string Executable, string Version, string Source);

/// <summary>
/// M25_CREATIVE_3D_SPEC.md §1/§3, ADR-0088 decision 3 — finding the two editors on the
/// owner's machine. This is DETECTION, never a PATH search: <c>blender</c> and <c>unity</c>
/// are the manifest allowlist's first tokens, and the executable they resolve to is the one
/// this class found in the places an installer puts it. A PATH search would let anything
/// named <c>blender.exe</c> earlier on PATH be what "blender" means; here the only answers
/// are an installed editor or <c>dependency_unavailable</c>.
///
/// Nothing here starts a process: the version is read from the install's own metadata (the
/// registry's <c>DisplayVersion</c>, the Hub's directory name), never by running the editor —
/// running Blender or Unity to ask its version would open a process on the owner's desk for
/// a question the file system already answers.
/// </summary>
[SupportedOSPlatform("windows")]
public static class SceneTools
{
    /// <summary>The Blender installer's vendor directory under Program Files.</summary>
    public const string BlenderVendorFolder = "Blender Foundation";

    /// <summary>The Unity Hub's editor directory under Program Files.</summary>
    public static readonly string[] UnityHubSegments = ["Unity", "Hub", "Editor"];

    private const string BlenderExecutable = "blender.exe";
    private const string UnityExecutable = "Unity.exe";

    private static readonly string[] UninstallKeys =
    [
        @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        @"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ];

    /// <summary>
    /// The installed Blender, or null. First the uninstall registry's <c>InstallLocation</c>
    /// for a product whose <c>DisplayName</c> is Blender (what the official installer writes),
    /// then the <c>Blender Foundation</c> directory under Program Files — the highest version
    /// directory that actually holds a <c>blender.exe</c>.
    /// </summary>
    public static SceneTool? FindBlender()
    {
        foreach (var hive in UninstallKeys)
        {
            var found = FromUninstallKey(hive, "Blender", BlenderExecutable);
            if (found is not null)
            {
                return found;
            }
        }

        foreach (var programFiles in ProgramFilesDirectories())
        {
            var vendor = Path.Combine(programFiles, BlenderVendorFolder);
            var best = HighestVersionedChild(vendor, child => Path.Combine(child, BlenderExecutable));
            if (best is not null)
            {
                return new SceneTool(best.Value.Executable, best.Value.Version, "Blender Foundation directory");
            }
        }

        return null;
    }

    /// <summary>
    /// The Unity Hub's installed editor, or null: <c>&lt;Program Files&gt;\Unity\Hub\Editor\
    /// &lt;version&gt;\Editor\Unity.exe</c>, the highest version that exists. The Hub's
    /// directory name IS the editor version (<c>6000.5.0f1</c>), so nothing is executed to
    /// learn it.
    /// </summary>
    public static SceneTool? FindUnity()
    {
        foreach (var programFiles in ProgramFilesDirectories())
        {
            var hub = Path.Combine([programFiles, .. UnityHubSegments]);
            var best = HighestVersionedChild(hub, child => Path.Combine(child, "Editor", UnityExecutable));
            if (best is not null)
            {
                return new SceneTool(best.Value.Executable, best.Value.Version, "Unity Hub editor directory");
            }
        }

        return null;
    }

    /// <summary>The tool a 3D runtime resolves to, or <c>dependency_unavailable</c> naming what is missing and where it was looked for.</summary>
    public static SceneTool Require(ProjectRuntime runtime)
        => runtime switch
        {
            ProjectRuntime.Blender => FindBlender() ?? throw Missing(
                "Blender",
                $"no installed Blender was found (the uninstall registry's InstallLocation, then '{BlenderVendorFolder}' under Program Files); PATH is never searched for an editor"),
            ProjectRuntime.Unity => FindUnity() ?? throw Missing(
                "Unity",
                @"no installed Unity editor was found (Unity\Hub\Editor\<version>\Editor\Unity.exe under Program Files); PATH is never searched for an editor"),
            _ => throw new CapabilityException(ErrorClasses.InternalBug, $"{runtime} is not a 3D runtime", retryable: false),
        };

    private static CapabilityException Missing(string what, string detail)
        => new(
            ErrorClasses.DependencyUnavailable,
            $"{what} is not installed on this machine: {detail}; nothing was run",
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

    private static SceneTool? FromUninstallKey(string hive, string displayNamePrefix, string executable)
    {
        try
        {
            using var root = Registry.LocalMachine.OpenSubKey(hive);
            if (root is null)
            {
                return null;
            }

            (SceneTool Tool, long[] Version)? best = null;
            foreach (var name in root.GetSubKeyNames())
            {
                using var product = root.OpenSubKey(name);
                if (product?.GetValue("DisplayName") is not string displayName
                    || !displayName.StartsWith(displayNamePrefix, StringComparison.OrdinalIgnoreCase)
                    || product.GetValue("InstallLocation") is not string location
                    || string.IsNullOrWhiteSpace(location))
                {
                    continue;
                }

                var candidate = SafeCombine(location, executable);
                if (candidate is null || !File.Exists(candidate))
                {
                    continue;
                }

                var version = product.GetValue("DisplayVersion") as string ?? string.Empty;
                var ordering = VersionKey(version);
                if (best is null || Compare(ordering, best.Value.Version) > 0)
                {
                    best = (new SceneTool(Path.GetFullPath(candidate), version, $@"HKLM\{hive}\{name}"), ordering);
                }
            }

            return best?.Tool;
        }
        catch (Exception)
        {
            // An unreadable hive is no installation; the directory probe still gets a turn.
            return null;
        }
    }

    /// <summary>The child of <paramref name="parent"/> with the highest version-shaped name whose <paramref name="executablePath"/> exists.</summary>
    private static (string Executable, string Version)? HighestVersionedChild(string parent, Func<string, string> executablePath)
    {
        try
        {
            if (!Directory.Exists(parent))
            {
                return null;
            }

            (string Executable, string Version, long[] Key)? best = null;
            foreach (var child in Directory.EnumerateDirectories(parent))
            {
                var executable = executablePath(child);
                if (!File.Exists(executable))
                {
                    continue;
                }

                var version = Path.GetFileName(child) ?? string.Empty;
                var key = VersionKey(version);
                if (best is null || Compare(key, best.Value.Key) > 0)
                {
                    best = (Path.GetFullPath(executable), version, key);
                }
            }

            return best is null ? null : (best.Value.Executable, best.Value.Version);
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static string? SafeCombine(string directory, string file)
    {
        try
        {
            return Path.Combine(directory.Trim().TrimEnd('"'), file);
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>The leading numeric components of a version-shaped name (<c>Blender 4.5</c>, <c>4.5.4</c>, <c>6000.5.0f1</c>), for ordering only.</summary>
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

    /// <summary>The tools this machine has, as one line for the companion's startup log.</summary>
    public static string Describe()
    {
        var blender = FindBlender();
        var unity = FindUnity();
        return string.Create(
            CultureInfo.InvariantCulture,
            $"blender={(blender is null ? "(not installed)" : blender.Version + " @ " + blender.Executable)} unity={(unity is null ? "(not installed)" : unity.Version + " @ " + unity.Executable)}");
    }
}
