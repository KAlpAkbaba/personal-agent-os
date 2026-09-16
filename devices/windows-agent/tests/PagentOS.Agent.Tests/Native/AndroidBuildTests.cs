using System.Diagnostics;
using System.IO.Compression;
using System.Text;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Documents;
using PagentOS.SessionCompanion.Native;
using PagentOS.SessionCompanion.Projects;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// B49 (ADR-0161): the Android build on the device - three Gradle shapes under the native root,
/// Gradle started as the configured JDK on the distribution's launcher (no shell, no PATH, no
/// JAVA_HOME), the counts read from the JUnit reports this run wrote, and the artefact's identity
/// read out of the APK or bundle itself.
///
/// Everything but <see cref="A_real_Gradle_build_of_the_Cloud_Core_s_own_template_produces_an_APK_and_a_bundle_the_device_reads_back"/>
/// needs no toolchain. That one needs a JDK, a Gradle distribution and an Android SDK named by
/// <c>PAGENTOS_TEST_ANDROID_JAVA_HOME</c>, <c>PAGENTOS_TEST_ANDROID_GRADLE_HOME</c> and (optionally)
/// <c>PAGENTOS_TEST_ANDROID_SDK</c> / <c>PAGENTOS_TEST_ANDROID_GRADLE_USER_HOME</c>, and is skipped by
/// name without them.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class AndroidBuildTests
{
    private const string Gradle = NativeCapabilityNames.GradleProgram;

    private static string Shape(string task) => $"{Gradle} --no-daemon --console=plain {task}";

    private static JsonObject Manifest(IReadOnlyDictionary<string, string> run, IReadOnlyDictionary<string, string>? test = null)
    {
        var manifest = new JsonObject
        {
            ["entry"] = "app/build.gradle.kts",
            ["run"] = new JsonObject(run.Select(kv => new KeyValuePair<string, JsonNode?>(kv.Key, kv.Value))),
        };
        if (test is not null)
        {
            manifest["test"] = new JsonObject(test.Select(kv => new KeyValuePair<string, JsonNode?>(kv.Key, kv.Value)));
        }

        return manifest;
    }

    // ------------------------------------------------------------------ the shapes

    [Fact]
    public void The_three_Gradle_shapes_are_admitted_under_the_native_root_as_an_argument_list()
    {
        var manifest = ProjectManifest.Parse(
            Manifest(
                new Dictionary<string, string> { ["build"] = Shape("assembleDebug"), ["bundle"] = Shape("bundleRelease") },
                new Dictionary<string, string> { ["unit"] = Shape("test") }),
            null,
            ProjectScope.Native);

        foreach (var (key, task) in new[] { ("build", "assembleDebug"), ("bundle", "bundleRelease") })
        {
            var command = manifest.Run[key];
            Assert.Equal(ProjectRuntime.Gradle, command.Runtime);
            Assert.True(command.IsBatch);
            Assert.Equal(["--no-daemon", "--console=plain", task], command.Arguments);
        }

        Assert.Equal(["--no-daemon", "--console=plain", "test"], manifest.Test["unit"].Arguments);
    }

    [Theory]
    [InlineData("gradle --no-daemon --console=plain assembleDebug installDebug", "a second task")]
    [InlineData("gradle --console=plain --no-daemon assembleDebug", "the flags in another order")]
    [InlineData("gradle --no-daemon assembleDebug", "a missing flag")]
    [InlineData("gradle --no-daemon --console=plain installDebug", "a task that installs")]
    [InlineData("gradle --no-daemon --console=plain publish", "a task that publishes")]
    [InlineData("gradle --no-daemon --console=plain -Dorg.gradle.jvmargs=x", "a system property")]
    [InlineData("gradle --no-daemon --console=plain -Pandroid.injected=x", "a project property")]
    [InlineData("gradle --no-daemon --console=plain --init-script=evil.gradle", "an init script")]
    [InlineData("gradle --no-daemon --console=plain -p ../other", "another project directory")]
    [InlineData("gradlew --no-daemon --console=plain assembleDebug", "the wrapper")]
    [InlineData("gradle.bat --no-daemon --console=plain assembleDebug", "the batch file")]
    [InlineData("java -jar gradle-launcher.jar assembleDebug", "java directly")]
    public void Anything_else_is_refused_before_a_process_exists(string command, string what)
    {
        var failure = Assert.Throws<CapabilityException>(() => ProjectManifest.Parse(
            Manifest(new Dictionary<string, string> { ["build"] = command }), null, ProjectScope.Native));
        Assert.True(failure.ErrorClass == ErrorClasses.PermissionDenied, $"{what}: {failure.ErrorClass} {failure.Message}");
        Assert.Contains("nothing was run", failure.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void The_manifest_the_Cloud_Core_writes_is_one_this_parser_admits()
    {
        // packages/protocol/android-manifest.example.json is the file BOTH halves read: the
        // Cloud Core test keeps it equal to android_manifest(), and this one parses it.
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, "packages", "protocol", "android-manifest.example.json")))
        {
            dir = dir.Parent;
        }

        Assert.NotNull(dir);
        var doc = JsonNode.Parse(File.ReadAllText(Path.Combine(dir!.FullName, "packages", "protocol", "android-manifest.example.json")))!.AsObject();
        var manifest = ProjectManifest.Parse((JsonObject)doc["manifest"]!.DeepClone(), null, ProjectScope.Native);

        Assert.Equal(doc["entry"]!.GetValue<string>(), manifest.Entry);
        Assert.Equal(ProjectManifest.NoPort, manifest.Port);
        Assert.Equal(["build", "bundle"], manifest.Run.Keys.Order(StringComparer.Ordinal));
        Assert.All(manifest.Run.Values.Concat(manifest.Test.Values), c => Assert.Equal(ProjectRuntime.Gradle, c.Runtime));
        Assert.Equal("assembleDebug", manifest.Run["build"].Arguments[^1]);
        Assert.Equal("bundleRelease", manifest.Run["bundle"].Arguments[^1]);
        Assert.Equal("test", manifest.Test["unit"].Arguments[^1]);
    }

    [Fact]
    public void A_build_is_not_a_test_suite_and_a_test_is_not_a_build()
    {
        var testAsRun = Assert.Throws<CapabilityException>(() => ProjectManifest.Parse(
            Manifest(new Dictionary<string, string> { ["build"] = Shape("test") }), null, ProjectScope.Native));
        Assert.Contains("is a test command", testAsRun.Message, StringComparison.Ordinal);

        var buildAsTest = Assert.Throws<CapabilityException>(() => ProjectManifest.Parse(
            Manifest(
                new Dictionary<string, string> { ["build"] = Shape("assembleDebug") },
                new Dictionary<string, string> { ["unit"] = Shape("assembleDebug") }),
            null,
            ProjectScope.Native));
        Assert.Contains("is a run command", buildAsTest.Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData(ProjectScope.Web)]
    [InlineData(ProjectScope.ThreeD)]
    public void Gradle_runs_only_under_the_native_root(ProjectScope scope)
    {
        // A web manifest must name a port; with one, the refusal reached is the command's.
        var manifest = Manifest(new Dictionary<string, string> { ["build"] = Shape("assembleDebug") });
        if (scope == ProjectScope.Web)
        {
            manifest["port"] = 8765;
        }

        var failure = Assert.Throws<CapabilityException>(() => ProjectManifest.Parse(manifest, null, scope));
        Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        Assert.Contains("runs only under", failure.Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("apksigner")]
    [InlineData("jarsigner")]
    [InlineData("KEYTOOL.exe")]
    public void The_Android_and_Java_signers_are_refused_by_name(string program)
    {
        Assert.True(NativeCapabilityNames.IsForbiddenProgram(program));
        var failure = Assert.Throws<CapabilityException>(() => ProjectManifest.Parse(
            Manifest(new Dictionary<string, string> { ["build"] = $"{program} sign app.apk" }), null, ProjectScope.Native));
        Assert.Contains("runs no signing program", failure.Message, StringComparison.Ordinal);
        Assert.Throws<CapabilityException>(() => ProjectRunner.RequireNoSigner(Path.Combine("C:\\jdk\\bin", program), []));
    }

    // ------------------------------------------------------------------ the toolchain

    private sealed class FakeToolchain : IDisposable
    {
        public FakeToolchain(string javaVersion = "17.0.20.1")
        {
            Root = Path.Combine(Path.GetTempPath(), "pagentos-android-toolchain", Guid.NewGuid().ToString("N")[..10]);
            JavaHome = Path.Combine(Root, "jdk");
            GradleHome = Path.Combine(Root, "gradle-8.7");
            Sdk = Path.Combine(Root, "sdk");
            Directory.CreateDirectory(Path.Combine(JavaHome, "bin"));
            File.WriteAllText(Path.Combine(JavaHome, "bin", "java.exe"), "not a real java");
            File.WriteAllText(Path.Combine(JavaHome, "release"), $"IMPLEMENTOR=\"Test\"\nJAVA_VERSION=\"{javaVersion}\"\n");
            Directory.CreateDirectory(Path.Combine(GradleHome, "lib", "agents"));
            File.WriteAllText(Path.Combine(GradleHome, "lib", "gradle-launcher-8.7.jar"), "jar");
            File.WriteAllText(Path.Combine(GradleHome, "lib", "agents", "gradle-instrumentation-agent-8.7.jar"), "jar");
            Directory.CreateDirectory(Path.Combine(Sdk, "platforms"));
            _previous = NativeTools.Android;
            NativeTools.Android = new NativeTools.AndroidToolchainOptions(JavaHome, GradleHome, Sdk, Path.Combine(Root, "gh"), Path.Combine(Root, "ah"));
        }

        private readonly NativeTools.AndroidToolchainOptions _previous;

        public string Root { get; }

        public string JavaHome { get; }

        public string GradleHome { get; }

        public string Sdk { get; }

        public void Dispose()
        {
            NativeTools.Android = _previous;
            try
            {
                Directory.Delete(Root, recursive: true);
            }
            catch (Exception)
            {
                // Temp folder; best effort.
            }
        }
    }

    [Fact]
    public void The_configured_toolchain_resolves_to_java_on_the_launcher_jar_and_never_to_a_batch_file()
    {
        using var toolchain = new FakeToolchain();
        var launch = NativeTools.RequireGradle();

        Assert.Equal(Path.Combine(toolchain.JavaHome, "bin", "java.exe"), launch.Java.Executable, StringComparer.OrdinalIgnoreCase);
        Assert.Equal("17.0.20.1", launch.Java.Version);
        Assert.Equal("NativeJavaHome", launch.Java.Source);
        Assert.Equal(launch.Java.Executable, NativeTools.Require(ProjectRuntime.Gradle).Executable);

        var classpath = launch.Prefix[launch.Prefix.ToList().IndexOf("-classpath") + 1];
        Assert.Equal(Path.Combine(toolchain.GradleHome, "lib", "gradle-launcher-8.7.jar"), classpath, StringComparer.OrdinalIgnoreCase);
        Assert.Equal("org.gradle.launcher.GradleMain", launch.Prefix[^1]);
        Assert.DoesNotContain(launch.Prefix, a => a.EndsWith(".bat", StringComparison.OrdinalIgnoreCase) || a.EndsWith(".cmd", StringComparison.OrdinalIgnoreCase));
        Assert.Contains(launch.Prefix, a => a.StartsWith("-javaagent:", StringComparison.Ordinal));

        Assert.Equal(toolchain.JavaHome, launch.Environment["JAVA_HOME"], StringComparer.OrdinalIgnoreCase);
        Assert.Equal(toolchain.Sdk, launch.Environment["ANDROID_HOME"], StringComparer.OrdinalIgnoreCase);
        Assert.Equal(Path.Combine(toolchain.Root, "gh"), launch.Environment["GRADLE_USER_HOME"]);
        Assert.Equal(Path.Combine(toolchain.Root, "ah"), launch.Environment["ANDROID_USER_HOME"]);
        Assert.Contains("gradle=8.7", NativeTools.Describe(), StringComparison.Ordinal);
    }

    [Fact]
    public void The_default_key_and_cache_homes_are_the_companion_s_never_the_owner_s_profile()
    {
        using var toolchain = new FakeToolchain();
        NativeTools.Android = NativeTools.Android with { GradleUserHome = null, AndroidUserHome = null };
        var environment = NativeTools.RequireGradle().Environment;
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        Assert.NotEqual(Path.Combine(profile, ".android"), environment["ANDROID_USER_HOME"], StringComparer.OrdinalIgnoreCase);
        Assert.NotEqual(Path.Combine(profile, ".gradle"), environment["GRADLE_USER_HOME"], StringComparer.OrdinalIgnoreCase);
        Assert.Contains(Path.Combine("PagentOS", "android"), environment["ANDROID_USER_HOME"], StringComparison.OrdinalIgnoreCase);
    }

    [Fact]
    public void A_JDK_older_than_17_is_not_a_JDK_this_build_can_use()
    {
        using var toolchain = new FakeToolchain(javaVersion: "11.0.2");
        var failure = Assert.Throws<CapabilityException>(NativeTools.RequireGradle);
        Assert.Equal(ErrorClasses.DependencyUnavailable, failure.ErrorClass);
        Assert.Contains("no JDK 17+", failure.Message, StringComparison.Ordinal);
        Assert.Contains("nothing was run", failure.Message, StringComparison.Ordinal);
        Assert.Equal(8, NativeTools.JavaFeature("1.8.0_402"));
        Assert.Equal(21, NativeTools.JavaFeature("21.0.4"));
    }

    [Fact]
    public void A_missing_distribution_and_SDK_are_both_named()
    {
        using var toolchain = new FakeToolchain();
        NativeTools.Android = NativeTools.Android with { GradleHome = Path.Combine(toolchain.Root, "absent"), AndroidSdk = Path.Combine(toolchain.Root, "absent-sdk") };
        var failure = Assert.Throws<CapabilityException>(NativeTools.RequireGradle);
        Assert.Contains("no Gradle distribution", failure.Message, StringComparison.Ordinal);
        Assert.Contains("no Android SDK", failure.Message, StringComparison.Ordinal);
        Assert.Equal("runtime_missing", failure.Detail[DocumentErrors.DetailKey]);
    }

    [Fact]
    public void A_JAVA_HOME_variable_does_not_choose_the_JDK_and_is_scrubbed_with_every_code_injecting_variable()
    {
        var environment = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase)
        {
            ["JAVA_HOME"] = @"C:\planted\jdk",
            ["JAVA_TOOL_OPTIONS"] = "-javaagent:evil.jar",
            ["_JAVA_OPTIONS"] = "-Dx=y",
            ["JDK_JAVA_OPTIONS"] = "-Dx=y",
            ["GRADLE_OPTS"] = "-Dx=y",
            ["ORG_GRADLE_PROJECT_signingKey"] = "x",
            ["org_gradle_caching"] = "true",
            ["CLASSPATH"] = @"C:\planted",
            ["PATH"] = @"C:\Windows",
        };
        ProjectRunner.ApplyGradleEnvironment(environment, new Dictionary<string, string> { ["JAVA_HOME"] = @"E:\jdk", ["ANDROID_HOME"] = @"E:\sdk" });

        Assert.Equal(@"E:\jdk", environment["JAVA_HOME"]);
        Assert.Equal(@"E:\sdk", environment["ANDROID_HOME"]);
        Assert.Equal(@"C:\Windows", environment["PATH"]);
        foreach (var removed in new[] { "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS", "GRADLE_OPTS", "ORG_GRADLE_PROJECT_signingKey", "org_gradle_caching", "CLASSPATH" })
        {
            Assert.False(environment.ContainsKey(removed), $"{removed} must not reach the build");
        }

        // The environment variable is ignored when choosing: a planted JAVA_HOME with a valid JDK
        // shape does not become the build's JDK while the configured one is absent.
        using var planted = new FakeToolchain();
        var previous = Environment.GetEnvironmentVariable("JAVA_HOME");
        try
        {
            Environment.SetEnvironmentVariable("JAVA_HOME", planted.JavaHome);
            NativeTools.Android = NativeTools.Android with { JavaHome = Path.Combine(planted.Root, "not-configured") };
            Assert.Null(NativeTools.FindJava());
        }
        finally
        {
            Environment.SetEnvironmentVariable("JAVA_HOME", previous);
        }
    }

    // ------------------------------------------------------------------ the counts

    [Fact]
    public void The_counts_come_from_this_run_s_JUnit_reports_and_nothing_counted_is_null()
    {
        var folder = Path.Combine(Path.GetTempPath(), "pagentos-junit", Guid.NewGuid().ToString("N")[..10]);
        try
        {
            Assert.Equal((null, null), JUnitReports.Count(folder, DateTime.UtcNow.AddMinutes(-1)));

            var debug = Path.Combine(folder, "app", "build", "test-results", "testDebugUnitTest");
            var release = Path.Combine(folder, "app", "build", "test-results", "testReleaseUnitTest");
            Directory.CreateDirectory(debug);
            Directory.CreateDirectory(release);
            File.WriteAllText(Path.Combine(debug, "TEST-a.xml"), "<?xml version=\"1.0\"?><testsuite name=\"a\" tests=\"3\" skipped=\"1\" failures=\"1\" errors=\"0\"><testcase/></testsuite>");
            File.WriteAllText(Path.Combine(release, "TEST-a.xml"), "<?xml version=\"1.0\"?><testsuite name=\"a\" tests=\"2\" skipped=\"0\" failures=\"0\" errors=\"0\"></testsuite>");
            var stale = Path.Combine(debug, "TEST-old.xml");
            File.WriteAllText(stale, "<testsuite tests=\"50\" failures=\"50\" errors=\"0\" skipped=\"0\"/>");
            File.SetLastWriteTimeUtc(stale, DateTime.UtcNow.AddHours(-1));

            // 3 tests - 1 skipped - 1 failed = 1 passed, plus 2 passed; the stale report is not this run's.
            Assert.Equal((3, 1), JUnitReports.Count(folder, DateTime.UtcNow.AddMinutes(-1)));

            File.WriteAllText(Path.Combine(release, "TEST-b.xml"), "<!DOCTYPE x [<!ENTITY e SYSTEM \"file:///c:/windows/win.ini\">]><testsuite tests=\"1\">&e;</testsuite>");
            Assert.Equal((null, null), JUnitReports.Count(folder, DateTime.UtcNow.AddMinutes(-1)));

            JUnitReports.Clear(folder);
            Assert.False(Directory.Exists(Path.Combine(folder, "app", "build", "test-results")));
            Assert.Equal((null, null), JUnitReports.Count(folder, DateTime.UtcNow.AddMinutes(-1)));
        }
        finally
        {
            if (Directory.Exists(folder))
            {
                Directory.Delete(folder, recursive: true);
            }
        }
    }

    // ------------------------------------------------------------------ the artefact

    [Fact]
    public void An_APK_says_which_build_it_is_from_its_own_binary_manifest()
    {
        var apk = SyntheticPackage(".apk", "AndroidManifest.xml", BinaryManifest("com.pagentos.sayac", 1004003, "1.4.2", 23, 33));
        try
        {
            var block = AndroidPackageReader.TryRead(apk)!;
            Assert.Equal("apk", block["format"]!.GetValue<string>());
            Assert.Equal("com.pagentos.sayac", block["package"]!.GetValue<string>());
            Assert.Equal(1004003, block["version_code"]!.GetValue<long>());
            Assert.Equal("1.4.2", block["version_name"]!.GetValue<string>());
            Assert.Equal(23, block["min_sdk"]!.GetValue<long>());
            Assert.Equal(33, block["target_sdk"]!.GetValue<long>());
            Assert.False(block["signed"]!.GetValue<bool>(), "a package with no signature block is not signed");
        }
        finally
        {
            File.Delete(apk);
        }
    }

    [Fact]
    public void A_bundle_says_which_build_it_is_from_its_protobuf_manifest()
    {
        var aab = SyntheticPackage(".aab", "base/manifest/AndroidManifest.xml", ProtoManifest("com.pagentos.sayac", "1004003", "1.4.2"));
        try
        {
            var block = AndroidPackageReader.TryRead(aab)!;
            Assert.Equal("aab", block["format"]!.GetValue<string>());
            Assert.Equal("com.pagentos.sayac", block["package"]!.GetValue<string>());
            Assert.Equal(1004003, block["version_code"]!.GetValue<long>());
            Assert.Equal("1.4.2", block["version_name"]!.GetValue<string>());
        }
        finally
        {
            File.Delete(aab);
        }
    }

    [Fact]
    public void A_file_that_is_not_a_readable_package_has_no_android_block_rather_than_a_guess()
    {
        var garbage = SyntheticPackage(".apk", "AndroidManifest.xml", [0x03, 0x00, 0x08, 0x00, 0xFF, 0xFF, 0xFF, 0x7F, 1, 2, 3]);
        var noManifest = SyntheticPackage(".apk", "classes.dex", [1, 2, 3]);
        var notZip = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString("N") + ".apk");
        File.WriteAllText(notZip, "PK but not really");
        var wrongExtension = SyntheticPackage(".zip", "AndroidManifest.xml", BinaryManifest("com.x", 1, "1", 23, 33));
        try
        {
            Assert.Null(AndroidPackageReader.TryRead(garbage));
            Assert.Null(AndroidPackageReader.TryRead(noManifest));
            Assert.Null(AndroidPackageReader.TryRead(notZip));
            Assert.Null(AndroidPackageReader.TryRead(wrongExtension));
            Assert.Null(AndroidPackageReader.ReadProtoXml([0x0A, 0xFF, 0xFF, 0xFF, 0xFF, 0x0F]));
        }
        finally
        {
            foreach (var f in new[] { garbage, noManifest, notZip, wrongExtension })
            {
                File.Delete(f);
            }
        }
    }

    // ------------------------------------------------------------------ the real build

    public sealed class AndroidToolchainFactAttribute : FactAttribute
    {
        public AndroidToolchainFactAttribute()
        {
            if (!OperatingSystem.IsWindows())
            {
                Skip = "the native lab needs Windows";
            }
            else if (string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("PAGENTOS_TEST_ANDROID_JAVA_HOME"))
                     || string.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable("PAGENTOS_TEST_ANDROID_GRADLE_HOME")))
            {
                Skip = "no Android toolchain was named (PAGENTOS_TEST_ANDROID_JAVA_HOME, PAGENTOS_TEST_ANDROID_GRADLE_HOME); the test needs a real JDK 17, Gradle and SDK";
            }
        }
    }

    [AndroidToolchainFact]
    public void A_real_Gradle_build_of_the_Cloud_Core_s_own_template_produces_an_APK_and_a_bundle_the_device_reads_back()
    {
        var previous = NativeTools.Android;
        NativeTools.Android = new NativeTools.AndroidToolchainOptions(
            Environment.GetEnvironmentVariable("PAGENTOS_TEST_ANDROID_JAVA_HOME"),
            Environment.GetEnvironmentVariable("PAGENTOS_TEST_ANDROID_GRADLE_HOME"),
            Environment.GetEnvironmentVariable("PAGENTOS_TEST_ANDROID_SDK"),
            Environment.GetEnvironmentVariable("PAGENTOS_TEST_ANDROID_GRADLE_USER_HOME"),
            null);
        try
        {
            using var lab = new NativeLab();
            var folder = lab.ScaffoldNative(
                "android-real",
                "sayac",
                run: new Dictionary<string, string> { ["build"] = Shape("assembleDebug"), ["bundle"] = Shape("bundleRelease") },
                test: new Dictionary<string, string> { ["unit"] = Shape("test") },
                files: TemplateFiles(),
                entry: "app/build.gradle.kts");

            var limit = NativeCapabilityNames.RunLimit.TotalSeconds;
            var build = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "android-real", ["command_key"] = "build" }, limit);
            Assert.True(build["exit_code"]!.GetValue<int>() == 0, build["log_tail"]?.GetValue<string>());
            Assert.Equal("gradle", build["runtime"]!.GetValue<string>());
            Assert.True(build["batch"]!.GetValue<bool>());
            Assert.Equal(NativeCapabilityNames.MemoryLimitBytes, lab.Runner.RunOf("android-real")!.Limits?.JobMemoryLimitBytes ?? 0);

            var test = lab.Exec(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "android-real" }, limit);
            Assert.True(test["exit_code"]!.GetValue<int>() == 0, test["report_tail"]?.GetValue<string>());
            Assert.True(test["counts_parsed"]!.GetValue<bool>(), "the counts come from the JUnit reports");
            Assert.True(test["passed"]!.GetValue<int>() >= 2);
            Assert.Equal(0, test["failed"]!.GetValue<int>());

            // A second test run of the unchanged project still counts: the stale reports were cleared.
            var again = lab.Exec(ProjectCapabilityNames.ProjectTest, new JsonObject { ["project_id"] = "android-real" }, limit);
            Assert.True(again["counts_parsed"]!.GetValue<bool>());

            var bundle = lab.Exec(ProjectCapabilityNames.ProjectRun, new JsonObject { ["project_id"] = "android-real", ["command_key"] = "bundle" }, limit);
            Assert.True(bundle["exit_code"]!.GetValue<int>() == 0, bundle["log_tail"]?.GetValue<string>());

            var apk = AndroidPackageReader.TryRead(Path.Combine(folder, "app", "build", "outputs", "apk", "debug", "app-debug.apk"))!;
            Assert.Equal("com.pagentos.sayac", apk["package"]!.GetValue<string>());
            Assert.Equal(1004003, apk["version_code"]!.GetValue<long>());
            Assert.Equal("1.4.2", apk["version_name"]!.GetValue<string>());
            Assert.Equal(33, apk["target_sdk"]!.GetValue<long>());
            Assert.True(apk["signed"]!.GetValue<bool>(), "a debug APK carries the plugin's debug signature, or no device would install it");

            var aab = AndroidPackageReader.TryRead(Path.Combine(folder, "app", "build", "outputs", "bundle", "release", "app-release.aab"))!;
            Assert.Equal("com.pagentos.sayac", aab["package"]!.GetValue<string>());
            Assert.Equal(1004003, aab["version_code"]!.GetValue<long>());
            Assert.False(aab["signed"]!.GetValue<bool>(), "the release bundle is unsigned: nothing here holds the owner's key");

            // The plugin's throwaway debug key was made in the configured home, not the owner's.
            var profileKey = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".android", "debug.keystore");
            var companionKey = Path.Combine(NativeTools.RequireGradle().Environment["ANDROID_USER_HOME"], "debug.keystore");
            Assert.True(File.Exists(companionKey), $"expected the debug key under the companion's home: {companionKey}");
            Assert.NotEqual(profileKey, companionKey, StringComparer.OrdinalIgnoreCase);
        }
        finally
        {
            NativeTools.Android = previous;
        }
    }

    /// <summary>The Cloud Core's counter-mobile template, filled the way <c>app.nativefactory.generator.render</c> fills it for "Sayaç" 1.4.2.</summary>
    private static List<(string Path, string Text)> TemplateFiles()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "services", "api", "app", "nativefactory", "templates")))
        {
            dir = dir.Parent;
        }

        Assert.NotNull(dir);
        var root = Path.Combine(dir!.FullName, "services", "api", "app", "nativefactory", "templates", "counter-mobile");
        var slots = new Dictionary<string, string>
        {
            ["{{ANDROID_PACKAGE}}"] = "com.pagentos.sayac",
            ["{{SLUG}}"] = "sayac",
            ["{{TITLE}}"] = "Sayaç",
            ["{{TITLE_RESOURCE}}"] = "Sayaç",
            ["{{VERSION}}"] = "1.4.2",
            ["{{VERSION_CODE}}"] = "1004003",
        };
        var files = new List<(string, string)>();
        foreach (var file in Directory.EnumerateFiles(root, "*.tmpl", SearchOption.AllDirectories))
        {
            var text = File.ReadAllText(file);
            foreach (var (slot, value) in slots)
            {
                text = text.Replace(slot, value, StringComparison.Ordinal);
            }

            Assert.DoesNotContain("{{", text, StringComparison.Ordinal);
            var relative = Path.GetRelativePath(root, file).Replace('\\', '/');
            files.Add((relative[..^".tmpl".Length], text));
        }

        return files;
    }

    // ------------------------------------------------------------------ synthetic packages

    private static string SyntheticPackage(string extension, string entryName, byte[] content)
    {
        var path = Path.Combine(Path.GetTempPath(), "pagentos-android-" + Guid.NewGuid().ToString("N")[..10] + extension);
        using (var zip = ZipFile.Open(path, ZipArchiveMode.Create))
        {
            using var stream = zip.CreateEntry(entryName).Open();
            stream.Write(content);
        }

        return path;
    }

    /// <summary>A minimal AXML document: a UTF-16 string pool, then &lt;manifest&gt; and &lt;uses-sdk&gt; with the attributes the reader names.</summary>
    private static byte[] BinaryManifest(string package, int versionCode, string versionName, int minSdk, int targetSdk)
    {
        string[] strings = ["package", "versionCode", "versionName", "manifest", "uses-sdk", "minSdkVersion", "targetSdkVersion", package, versionName];
        var pool = new MemoryStream();
        var offsets = new List<int>();
        var chars = new MemoryStream();
        foreach (var s in strings)
        {
            offsets.Add((int)chars.Length);
            chars.Write(BitConverter.GetBytes((ushort)s.Length));
            chars.Write(Encoding.Unicode.GetBytes(s));
            chars.Write([0, 0]);
        }

        while (chars.Length % 4 != 0)
        {
            chars.WriteByte(0);
        }

        const int poolHeader = 28;
        var stringsStart = poolHeader + (strings.Length * 4);
        var poolSize = stringsStart + (int)chars.Length;
        pool.Write(BitConverter.GetBytes((ushort)0x0001));
        pool.Write(BitConverter.GetBytes((ushort)poolHeader));
        pool.Write(BitConverter.GetBytes(poolSize));
        pool.Write(BitConverter.GetBytes(strings.Length));
        pool.Write(BitConverter.GetBytes(0));
        pool.Write(BitConverter.GetBytes(0));
        pool.Write(BitConverter.GetBytes(stringsStart));
        pool.Write(BitConverter.GetBytes(0));
        foreach (var o in offsets)
        {
            pool.Write(BitConverter.GetBytes(o));
        }

        pool.Write(chars.ToArray());

        byte[] Element(int name, (int Name, int Raw, byte Type, uint Data)[] attributes)
        {
            var e = new MemoryStream();
            var size = 16 + 20 + (attributes.Length * 20);
            e.Write(BitConverter.GetBytes((ushort)0x0102));
            e.Write(BitConverter.GetBytes((ushort)16));
            e.Write(BitConverter.GetBytes(size));
            e.Write(BitConverter.GetBytes(1));
            e.Write(BitConverter.GetBytes(-1));
            e.Write(BitConverter.GetBytes(-1));
            e.Write(BitConverter.GetBytes(name));
            e.Write(BitConverter.GetBytes((ushort)20));
            e.Write(BitConverter.GetBytes((ushort)20));
            e.Write(BitConverter.GetBytes((ushort)attributes.Length));
            e.Write(BitConverter.GetBytes((ushort)0));
            e.Write(BitConverter.GetBytes((ushort)0));
            e.Write(BitConverter.GetBytes((ushort)0));
            foreach (var a in attributes)
            {
                e.Write(BitConverter.GetBytes(-1));
                e.Write(BitConverter.GetBytes(a.Name));
                e.Write(BitConverter.GetBytes(a.Raw));
                e.Write(BitConverter.GetBytes((ushort)8));
                e.WriteByte(0);
                e.WriteByte(a.Type);
                e.Write(BitConverter.GetBytes(a.Data));
            }

            return e.ToArray();
        }

        var manifest = Element(3, [(0, 7, 0x03, 7), (1, -1, 0x10, (uint)versionCode), (2, 8, 0x03, 8)]);
        var usesSdk = Element(4, [(5, -1, 0x10, (uint)minSdk), (6, -1, 0x10, (uint)targetSdk)]);
        var body = pool.ToArray().Concat(manifest).Concat(usesSdk).ToArray();
        var document = new MemoryStream();
        document.Write(BitConverter.GetBytes((ushort)0x0003));
        document.Write(BitConverter.GetBytes((ushort)8));
        document.Write(BitConverter.GetBytes(8 + body.Length));
        document.Write(body);
        return document.ToArray();
    }

    /// <summary>A minimal <c>aapt.pb.XmlNode</c>: element "manifest" with three string attributes.</summary>
    private static byte[] ProtoManifest(string package, string versionCode, string versionName)
    {
        static byte[] Field(int number, byte[] value)
        {
            var bytes = new List<byte> { (byte)((number << 3) | 2) };
            var length = value.Length;
            while (length >= 0x80)
            {
                bytes.Add((byte)(length | 0x80));
                length >>= 7;
            }

            bytes.Add((byte)length);
            bytes.AddRange(value);
            return [.. bytes];
        }

        static byte[] Text(int number, string value) => Field(number, Encoding.UTF8.GetBytes(value));

        static byte[] Attribute(string name, string value) => Field(4, [.. Text(2, name), .. Text(3, value)]);

        var element = Text(3, "manifest")
            .Concat(Attribute("package", package))
            .Concat(Attribute("versionCode", versionCode))
            .Concat(Attribute("versionName", versionName))
            .ToArray();
        return Field(1, element);
    }
}
