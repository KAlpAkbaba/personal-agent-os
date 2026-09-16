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
            ProjectRuntime.Gradle => RequireGradle().Java,
            _ => throw new CapabilityException(ErrorClasses.InternalBug, $"{runtime} is not a native build runtime", retryable: false),
        };

    /// <summary>The tools this machine has, as one line for the companion's startup log.</summary>
    public static string Describe()
    {
        var dotnet = FindDotnet();
        var makeappx = FindMakeAppx();
        var java = FindJava();
        var gradle = FindGradle();
        var sdk = FindAndroidSdk();
        return string.Create(
            CultureInfo.InvariantCulture,
            $"dotnet={Say(dotnet)} makeappx={Say(makeappx)} java={Say(java)} gradle={Say(gradle)} android_sdk={sdk ?? "(not installed)"}");

        static string Say(NativeTool? tool) => tool is null ? "(not installed)" : tool.Version + " @ " + tool.Executable;
    }

    // ================================================================ B49: Android (ADR-0161)

    /// <summary>The lowest JDK feature release the Android Gradle plugin the factory's template pins (8.5) accepts.</summary>
    public const int MinimumJavaFeature = 17;

    /// <summary>
    /// Where the Android toolchain lives, as the companion was configured
    /// (<c>PAGENTOS_AGENT_NativeJavaHome</c>, <c>NativeGradleHome</c>, <c>NativeAndroidSdk</c>,
    /// <c>NativeGradleUserHome</c>, <c>NativeAndroidUserHome</c>). Empty entries fall back to the
    /// installers' own directories — never to PATH and never to <c>JAVA_HOME</c>: a variable any
    /// process of the owner's could have set is not what decides which Java compiles here, for
    /// the same reason <c>makeappx</c> is never searched for on PATH.
    /// </summary>
    public sealed record AndroidToolchainOptions(
        string? JavaHome = null,
        string? GradleHome = null,
        string? AndroidSdk = null,
        string? GradleUserHome = null,
        string? AndroidUserHome = null)
    {
        public static AndroidToolchainOptions FromConfiguration(Microsoft.Extensions.Configuration.IConfiguration configuration)
            => new(
                Trimmed(configuration["NativeJavaHome"]),
                Trimmed(configuration["NativeGradleHome"]),
                Trimmed(configuration["NativeAndroidSdk"]),
                Trimmed(configuration["NativeGradleUserHome"]),
                Trimmed(configuration["NativeAndroidUserHome"]));

        private static string? Trimmed(string? value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
    }

    /// <summary>What a Gradle run is started with: the JDK's <c>java.exe</c>, the arguments that make it Gradle, and the environment the build needs.</summary>
    public sealed record GradleLaunch(NativeTool Java, IReadOnlyList<string> Prefix, IReadOnlyDictionary<string, string> Environment);

    /// <summary>
    /// Variables removed from a Gradle child's environment. Each of them can put code into the
    /// JVM or into the build without appearing in the command: an agent, a property, an init
    /// hook. The manifest shape admits no such thing, so the environment must not either.
    /// </summary>
    public static readonly IReadOnlyList<string> GradleScrubbedVariables =
    [
        "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "JAVA_OPTS", "GRADLE_OPTS", "CLASSPATH", "JAVA_HOME",
    ];

    /// <summary>Prefixes removed from a Gradle child's environment: project and system properties Gradle reads from variables.</summary>
    public static readonly IReadOnlyList<string> GradleScrubbedPrefixes = ["ORG_GRADLE_"];

    private static AndroidToolchainOptions _android = new();

    /// <summary>Set once at companion startup; the native lab sets it to its own toolchain and restores it.</summary>
    public static AndroidToolchainOptions Android
    {
        get => _android;
        set => _android = value ?? new AndroidToolchainOptions();
    }

    /// <summary>The configured JDK's <c>java.exe</c> (feature release 17 or later, read from the JDK's own <c>release</c> file), else one under the installers' directories, else null.</summary>
    public static NativeTool? FindJava()
    {
        foreach (var (home, source) in JavaHomeCandidates())
        {
            var java = Path.Combine(home, "bin", "java.exe");
            if (!File.Exists(java) || ProjectRoots.IsReparsePoint(java))
            {
                continue;
            }

            var version = JavaVersion(home);
            if (JavaFeature(version) < MinimumJavaFeature)
            {
                continue;
            }

            return new NativeTool(Path.GetFullPath(java), version, source);
        }

        return null;
    }

    /// <summary>The configured Gradle distribution's launcher jar, else one under <c>%ProgramFiles%\Gradle</c>, else null. <see cref="NativeTool.Executable"/> is the JAR: Gradle is started by Java, never by <c>gradle.bat</c>.</summary>
    public static NativeTool? FindGradle()
    {
        foreach (var (home, source) in GradleHomeCandidates())
        {
            var lib = Path.Combine(home, "lib");
            if (!Directory.Exists(lib))
            {
                continue;
            }

            string? launcher;
            try
            {
                launcher = Directory.EnumerateFiles(lib, "gradle-launcher-*.jar").OrderByDescending(f => f, StringComparer.OrdinalIgnoreCase).FirstOrDefault();
            }
            catch (Exception)
            {
                continue;
            }

            if (launcher is null || ProjectRoots.IsReparsePoint(launcher))
            {
                continue;
            }

            var name = Path.GetFileNameWithoutExtension(launcher);
            return new NativeTool(Path.GetFullPath(launcher), name["gradle-launcher-".Length..], source);
        }

        return null;
    }

    /// <summary>The Android SDK: the configured one, else <c>%LOCALAPPDATA%\Android\Sdk</c> (where the Android installers put it); null unless it holds <c>platforms</c>.</summary>
    public static string? FindAndroidSdk()
    {
        var candidates = new List<string>();
        if (Android.AndroidSdk is { } configured)
        {
            candidates.Add(configured);
        }
        else
        {
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (!string.IsNullOrWhiteSpace(local))
            {
                candidates.Add(Path.Combine(local, "Android", "Sdk"));
            }
        }

        return candidates.Select(Path.GetFullPath).FirstOrDefault(c => Directory.Exists(Path.Combine(c, "platforms")));
    }

    /// <summary>
    /// Everything a Gradle run needs, or <c>dependency_unavailable</c> naming each missing piece.
    /// The prefix is what <c>gradle.bat</c> itself passes (the same heap, the same agent, the
    /// same main class), so the build behaves as the distribution intends without a shell.
    /// </summary>
    public static GradleLaunch RequireGradle()
    {
        var java = FindJava();
        var gradle = FindGradle();
        var sdk = FindAndroidSdk();
        var missing = new List<string>();
        if (java is null)
        {
            missing.Add($@"no JDK {MinimumJavaFeature}+ (NativeJavaHome, then Program Files\Eclipse Adoptium, Microsoft\jdk-*, Android\Android Studio\jbr; PATH and JAVA_HOME are never used)");
        }

        if (gradle is null)
        {
            missing.Add(@"no Gradle distribution (NativeGradleHome, then Program Files\Gradle\gradle-*)");
        }

        if (sdk is null)
        {
            missing.Add(@"no Android SDK (NativeAndroidSdk, then %LOCALAPPDATA%\Android\Sdk)");
        }

        if (missing.Count > 0)
        {
            throw Missing("The Android build toolchain", string.Join("; ", missing));
        }

        var lib = Path.GetDirectoryName(gradle!.Executable)!;
        var prefix = new List<string> { "-Xmx64m", "-Xms64m" };
        var agent = FirstFile(Path.Combine(lib, "agents"), "gradle-instrumentation-agent-*.jar");
        if (agent is not null)
        {
            prefix.Add("-javaagent:" + agent);
        }

        prefix.AddRange(["-Dorg.gradle.appname=gradle", "-classpath", gradle.Executable, "org.gradle.launcher.GradleMain"]);

        var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var javaHome = Path.GetDirectoryName(Path.GetDirectoryName(java!.Executable))!;
        var environment = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["JAVA_HOME"] = javaHome,
            ["ANDROID_HOME"] = sdk!,
            ["ANDROID_SDK_ROOT"] = sdk!,
            // The build's own caches and the Android plugin's throwaway debug key live in the
            // companion's folders, never in the owner's %USERPROFILE%\.gradle or \.android: a
            // debug build is signed with a key the plugin generates there, and it is not the
            // owner's identity (ADR-0161).
            ["GRADLE_USER_HOME"] = Android.GradleUserHome ?? Path.Combine(local, "PagentOS", "gradle"),
            ["ANDROID_USER_HOME"] = Android.AndroidUserHome ?? Path.Combine(local, "PagentOS", "android"),
        };
        return new GradleLaunch(java, prefix, environment);
    }

    private static IEnumerable<(string Home, string Source)> JavaHomeCandidates()
    {
        if (Android.JavaHome is { } configured)
        {
            yield return (configured, "NativeJavaHome");
            yield break;
        }

        foreach (var programFiles in ProgramFilesDirectories())
        {
            foreach (var pattern in new[] { (Path.Combine(programFiles, "Eclipse Adoptium"), "jdk-*"), (Path.Combine(programFiles, "Microsoft"), "jdk-*") })
            {
                foreach (var home in Children(pattern.Item1, pattern.Item2))
                {
                    yield return (home, pattern.Item1);
                }
            }

            yield return (Path.Combine(programFiles, "Android", "Android Studio", "jbr"), "Android Studio's bundled JDK");
        }
    }

    private static IEnumerable<(string Home, string Source)> GradleHomeCandidates()
    {
        if (Android.GradleHome is { } configured)
        {
            yield return (configured, "NativeGradleHome");
            yield break;
        }

        foreach (var programFiles in ProgramFilesDirectories())
        {
            foreach (var home in Children(Path.Combine(programFiles, "Gradle"), "gradle-*"))
            {
                yield return (home, @"Program Files\Gradle");
            }
        }
    }

    /// <summary>Child directories matching a pattern, highest version first.</summary>
    private static IEnumerable<string> Children(string parent, string pattern)
    {
        if (!Directory.Exists(parent))
        {
            return [];
        }

        try
        {
            return Directory.EnumerateDirectories(parent, pattern)
                .OrderByDescending(d => VersionKey(Path.GetFileName(d) ?? string.Empty), Comparer<long[]>.Create(Compare))
                .ToList();
        }
        catch (Exception)
        {
            return [];
        }
    }

    private static string? FirstFile(string directory, string pattern)
    {
        try
        {
            return Directory.Exists(directory) ? Directory.EnumerateFiles(directory, pattern).FirstOrDefault() : null;
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary><c>JAVA_VERSION="17.0.20.1"</c> from the JDK's <c>release</c> file — read, never run.</summary>
    private static string JavaVersion(string home)
    {
        try
        {
            var release = Path.Combine(home, "release");
            if (!File.Exists(release))
            {
                return string.Empty;
            }

            foreach (var line in File.ReadLines(release).Take(64))
            {
                if (line.StartsWith("JAVA_VERSION=", StringComparison.Ordinal))
                {
                    return line["JAVA_VERSION=".Length..].Trim().Trim('"');
                }
            }
        }
        catch (Exception)
        {
            // An unreadable release file is an unknown version, which is below the minimum.
        }

        return string.Empty;
    }

    /// <summary>The feature release of a Java version string (<c>17.0.20.1</c> → 17, the old <c>1.8.0</c> → 8); 0 when unknown.</summary>
    public static int JavaFeature(string version)
    {
        var key = VersionKey(version);
        if (key.Length == 0)
        {
            return 0;
        }

        return (int)(key[0] == 1 && key.Length > 1 ? key[1] : key[0]);
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
