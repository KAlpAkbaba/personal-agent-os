using System.Diagnostics;
using System.Globalization;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// The orphan-Chrome reap of the 2026-09-03 incident: a killed Browser Worker left the
/// PagentOS-profile Chrome running, every later launch on the locked profile opened one
/// more window in it, and the owner's desktop filled with Chrome. These tests prove the
/// two halves of the fix with REAL child processes standing in for Chrome: a process whose
/// command line carries <c>--user-data-dir=&lt;profile&gt;</c> is terminated by the reap
/// (directly, before a worker start, and after the host kills a worker), and a process of
/// the same image WITHOUT that marker — the owner's own Chrome, in production — is never
/// touched.
/// </summary>
public sealed class ChromeOrphanReaperTests : IDisposable
{
    private const string Profile = @"C:\ProgramData\PagentOS\companion\browser\profile";

    private readonly string _dir = TestPaths.NewTempDir();
    private readonly ListLogger _log = new();
    private readonly List<Process> _children = [];

    public void Dispose()
    {
        foreach (var child in _children)
        {
            try
            {
                if (!child.HasExited)
                {
                    child.Kill(entireProcessTree: true);
                }
            }
            catch (Exception)
            {
                // Already gone.
            }

            child.Dispose();
        }

        try
        {
            Directory.Delete(_dir, recursive: true);
        }
        catch (Exception)
        {
            // Best-effort cleanup.
        }
    }

    // ------------------------------------------------------------ the rule, pure

    [Theory]
    [InlineData(@"""C:\Program Files\Google\Chrome\Application\chrome.exe"" --user-data-dir=C:\ProgramData\PagentOS\companion\browser\profile --no-first-run", true)]
    [InlineData(@"""C:\Program Files\Google\Chrome\Application\chrome.exe"" --user-data-dir=""C:\ProgramData\PagentOS\companion\browser\profile"" --no-first-run", true)]
    [InlineData(@"""C:\Program Files\Google\Chrome\Application\chrome.exe"" ""--user-data-dir=C:\ProgramData\PagentOS\companion\browser\profile"" --no-first-run", true)]
    [InlineData(@"chrome.exe --user-data-dir=C:\ProgramData\PagentOS\companion\browser\profile\", true)]
    [InlineData(@"chrome.exe --USER-DATA-DIR=c:\programdata\pagentos\COMPANION\browser\PROFILE", true)]
    [InlineData(@"chrome.exe --user-data-dir=C:\ProgramData\PagentOS\companion\browser\profile", true)]
    [InlineData(@"chrome.exe --headless --user-data-dir=C:\ProgramData\PagentOS\companion\browser\profile2", false)]
    [InlineData(@"chrome.exe --user-data-dir=C:\ProgramData\PagentOS\companion\browser", false)]
    [InlineData(@"chrome.exe --user-data-dir=C:\ProgramData\PagentOS\companion\browser\profile-old", false)]
    [InlineData(@"chrome.exe --user-data-dir=C:\Users\owner\AppData\Local\Google\Chrome\User Data", false)]
    [InlineData(@"""C:\Program Files\Google\Chrome\Application\chrome.exe"" --flag-switches-begin --flag-switches-end", false)]
    [InlineData(@"""C:\Program Files\Google\Chrome\Application\chrome.exe""", false)]
    [InlineData("", false)]
    [InlineData(null, false)]
    public void Only_a_command_line_naming_exactly_the_PagentOS_profile_matches(string? commandLine, bool expected)
    {
        Assert.Equal(expected, ChromeOrphanReaper.CommandLineTargetsProfile(commandLine, Profile));
    }

    [Theory]
    [InlineData(@"chrome.exe --user-data-dir=C:\p --type=renderer --lang=tr", false)]
    [InlineData(@"chrome.exe --type=gpu-process --user-data-dir=C:\p", false)]
    [InlineData(@"chrome.exe --user-data-dir=C:\p --no-first-run", true)]
    [InlineData(null, true)]
    public void A_main_process_has_no_type_switch(string? commandLine, bool expected)
    {
        Assert.Equal(expected, ChromeOrphanReaper.IsMainProcess(commandLine));
    }

    [Fact]
    public void The_reap_rule_needs_the_image_name_a_main_process_and_this_profile_together()
    {
        var reaper = new ChromeOrphanReaper(Profile, _log);
        Assert.Equal("chrome.exe", reaper.ProcessName);
        Assert.Equal(Profile, reaper.ProfileDir);

        var marker = $"--user-data-dir={Profile}";
        Assert.True(reaper.IsOrphan("chrome.exe", $"chrome.exe {marker}"));
        Assert.True(reaper.IsOrphan("CHROME.EXE", $"chrome.exe {marker}"));
        Assert.False(reaper.IsOrphan("msedge.exe", $"msedge.exe {marker}"), "another browser on the same path is not chrome.exe");
        Assert.False(reaper.IsOrphan("python.exe", $"python.exe -m browser_agent.worker {marker}"), "only the browser image, never the worker");
        Assert.False(reaper.IsOrphan("chrome.exe", $"chrome.exe {marker} --type=renderer"), "children go with their main process");
        Assert.False(reaper.IsOrphan("chrome.exe", "chrome.exe --profile-directory=Default"), "the owner's Chrome carries no PagentOS path");
        Assert.False(reaper.IsOrphan("chrome.exe", null));
    }

    [Fact]
    public void The_profile_is_normalised_once_so_trailing_separators_and_quotes_do_not_matter()
    {
        var reaper = new ChromeOrphanReaper($"\"{Profile}\\\"", _log);
        Assert.Equal(Profile, reaper.ProfileDir);
        Assert.True(reaper.IsOrphan("chrome.exe", $"chrome.exe --user-data-dir={Profile}"));
    }

    // ------------------------------------------------------------ real processes

    [Fact]
    public void The_reap_terminates_a_real_process_carrying_the_marker_and_leaves_one_without_it_alone()
    {
        var profile = Path.Combine(_dir, "profile");
        var marked = LaunchFakeChrome($"--user-data-dir={profile}");
        var otherProfile = LaunchFakeChrome($"--user-data-dir={profile}-other");
        var unmarked = LaunchFakeChrome(null);
        var reaper = new ChromeOrphanReaper(profile, _log, FakeChromeImageName());

        var listed = reaper.Enumerate();
        Assert.Contains(listed, entry => entry.Pid == marked.Id);
        Assert.Contains(listed, entry => entry.Pid == unmarked.Id);

        var reaped = reaper.Reap("unit test");

        Assert.Equal([marked.Id], reaped);
        Assert.True(marked.WaitForExit(5000), "the marked process must be gone");
        Assert.False(otherProfile.HasExited, "a process on a different profile must not be touched");
        Assert.False(unmarked.HasExited, "a process without the marker must not be touched");
        Assert.True(_log.Any($"pid={marked.Id.ToString(CultureInfo.InvariantCulture)}"), "the reaped pid is logged");
        Assert.True(_log.Any("unit test"), "the reason is logged");
        Assert.False(_log.Any($"pid={unmarked.Id.ToString(CultureInfo.InvariantCulture)}"));

        // Idempotent: nothing left to reap.
        Assert.Empty(reaper.Reap("again"));
    }

    [Fact]
    public async Task Before_a_worker_starts_the_host_reaps_the_orphan_and_the_worker_itself_is_not_mistaken_for_one()
    {
        var profile = Path.Combine(_dir, "profile");
        var orphan = LaunchFakeChrome($"--user-data-dir={profile}");
        var bystander = LaunchFakeChrome(null);
        await using var host = NewHost(profile);

        await host.StartAsync(CancellationToken.None);

        Assert.True(host.WorkerRunning, "the worker (same image, no marker) is untouched");
        Assert.Equal(1, host.OrphanChromesReaped);
        Assert.True(orphan.WaitForExit(5000), "the orphan must be gone before the worker starts");
        Assert.False(bystander.HasExited);
        Assert.True(_log.Any("before worker start"));
        Assert.True(_log.Any($"pids={orphan.Id.ToString(CultureInfo.InvariantCulture)}"));
        Assert.True(_log.Any("browser_lifecycle_violation"), "an orphan found is a lifecycle violation, named as such in the log");
    }

    [Fact]
    public async Task After_the_host_kills_a_worker_it_reaps_the_chrome_that_worker_left_behind()
    {
        var profile = Path.Combine(_dir, "profile");
        var bystander = LaunchFakeChrome(null);
        await using var host = NewHost(profile, extraArgs: "--no-pong", pingInterval: TimeSpan.FromMilliseconds(100));
        await host.StartAsync(CancellationToken.None);
        Assert.Equal(0, host.OrphanChromesReaped);

        // "The worker's Chrome": appears while the worker is up, survives the worker.
        var orphan = LaunchFakeChrome($"--user-data-dir={profile}");

        await WaitUntilAsync(() => host.LivenessKills >= 1 && host.OrphanChromesReaped >= 1, TimeSpan.FromSeconds(15));

        Assert.True(orphan.WaitForExit(5000), "the orphan must be reaped on the kill path");
        Assert.False(bystander.HasExited, "the unmarked process is never touched");
        Assert.Equal(1, host.OrphanChromesReaped);
        Assert.True(_log.Any("after killing the worker: missed"));
        Assert.True(_log.Any($"pids={orphan.Id.ToString(CultureInfo.InvariantCulture)}"));
    }

    // ------------------------------------------------------------ support

    private BrowserWorkerHost NewHost(string profile, string extraArgs = "", TimeSpan? pingInterval = null)
    {
        var options = FakeWorkerLauncher.Options(_dir, extraArgs) with { ProfileDir = profile };
        return new BrowserWorkerHost(
            options,
            _log,
            restartBackoff: new PagentOS.Agent.Core.Connection.BackoffPolicy(baseSeconds: 0.02, maxSeconds: 0.1),
            helloTimeout: TimeSpan.FromSeconds(20),
            pingInterval: pingInterval,
            shutdownGrace: TimeSpan.FromSeconds(5),
            reaper: new ChromeOrphanReaper(profile, _log, FakeChromeImageName()));
    }

    /// <summary>The image name the fake stand-in runs under (the apphost beside the tests, or dotnet.exe).</summary>
    private static string FakeChromeImageName() => Path.GetFileName(FakeWorkerLauncher.Launcher().Command);

    /// <summary>
    /// A real child that sits there like an orphaned Chrome would: the fake worker with
    /// <c>--no-hello</c> blocks on stdin forever, and <paramref name="markerArgument"/>
    /// (or nothing) is what its command line carries.
    /// </summary>
    private Process LaunchFakeChrome(string? markerArgument)
    {
        var (command, args) = FakeWorkerLauncher.Launcher();
        var startInfo = new ProcessStartInfo
        {
            FileName = command,
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };
        foreach (var argument in BrowserWorkerOptions.SplitArguments(args))
        {
            startInfo.ArgumentList.Add(argument);
        }

        startInfo.ArgumentList.Add("--no-hello");
        if (markerArgument is not null)
        {
            startInfo.ArgumentList.Add(markerArgument);
        }

        var process = Process.Start(startInfo) ?? throw new InvalidOperationException("Process.Start returned null");
        _children.Add(process);
        return process;
    }

    private static async Task WaitUntilAsync(Func<bool> condition, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (!condition())
        {
            if (DateTime.UtcNow > deadline)
            {
                throw new TimeoutException("condition not met within " + timeout);
            }

            await Task.Delay(50);
        }
    }
}
