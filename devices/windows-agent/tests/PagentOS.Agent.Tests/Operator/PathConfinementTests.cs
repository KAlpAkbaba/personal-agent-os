using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// A temp root with a REAL junction inside it (<c>mklink /J</c>, no privilege needed) that
/// points at a directory outside the root holding <c>secret.txt</c>; a genuine file under the
/// root; and a second root that is itself a junction to a real directory. Removed on dispose —
/// the junctions as links, never through them.
/// </summary>
public sealed class JunctionFixture : IDisposable
{
    public JunctionFixture()
    {
        var stamp = Guid.NewGuid().ToString("N");
        Root = Path.Combine(Path.GetTempPath(), "pagentos-sec-root-" + stamp);
        Outside = Path.Combine(Path.GetTempPath(), "pagentos-sec-outside-" + stamp);
        RealRoot = Path.Combine(Path.GetTempPath(), "pagentos-sec-real-" + stamp);
        Directory.CreateDirectory(Root);
        Directory.CreateDirectory(Outside);
        Directory.CreateDirectory(RealRoot);
        GenuineFile = Path.Combine(Root, "genuine.txt");
        File.WriteAllText(GenuineFile, "inside the root\n");
        SecretFile = Path.Combine(Outside, "secret.txt");
        File.WriteAllText(SecretFile, "outside the root\n");
        File.WriteAllText(Path.Combine(RealRoot, "a.txt"), "under the real root\n");

        Junction = Path.Combine(Root, "jump");
        CreateJunction(Junction, Outside);
        RootLink = Path.Combine(Path.GetTempPath(), "pagentos-sec-rootlink-" + stamp);
        CreateJunction(RootLink, RealRoot);
    }

    public string Root { get; }

    public string Outside { get; }

    public string Junction { get; }

    public string GenuineFile { get; }

    public string SecretFile { get; }

    /// <summary><c>secret.txt</c> spelled THROUGH the junction — lexically under the root, physically outside it.</summary>
    public string SecretThroughJunction => Path.Combine(Junction, "secret.txt");

    public string RealRoot { get; }

    public string RootLink { get; }

    public static void CreateJunction(string link, string target)
    {
        var startInfo = new ProcessStartInfo(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "System32", "cmd.exe"))
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            Arguments = "/c mklink /J \"" + link + "\" \"" + target + "\"",
        };
        using var process = Process.Start(startInfo)!;
        var stdout = process.StandardOutput.ReadToEnd();
        var stderr = process.StandardError.ReadToEnd();
        process.WaitForExit(10_000);
        Assert.True(process.ExitCode == 0, $"mklink /J failed ({process.ExitCode}): {stdout} {stderr}");
        Assert.True((new DirectoryInfo(link).Attributes & FileAttributes.ReparsePoint) != 0, $"{link} is not a reparse point");
    }

    public void Dispose()
    {
        foreach (var link in new[] { Junction, RootLink })
        {
            try
            {
                // Directory.Delete on a junction removes the link, not the target.
                Directory.Delete(link);
            }
            catch (Exception)
            {
                // Best-effort teardown.
            }
        }

        foreach (var dir in new[] { Root, Outside, RealRoot })
        {
            try
            {
                Directory.Delete(dir, recursive: true);
            }
            catch (Exception)
            {
                // Best-effort teardown.
            }
        }
    }
}

/// <summary>
/// ADR-0082 addendum 2, finding 1: root confinement is resolve-then-contain. A junction inside
/// a root pointing outside it is refused everywhere a path enters the operator — the terminal's
/// <c>&lt;path&gt;</c> token, <c>file.open</c>, <c>file.reveal</c> — and the refusal comes before
/// any process exists; a genuine file under the root still passes; a root that is itself a
/// junction admits its target; an unresolvable path is refused, never admitted lexically.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class PathConfinementTests : IDisposable
{
    private readonly JunctionFixture _fx = new();

    public void Dispose() => _fx.Dispose();

    [Fact]
    public void A_junction_inside_the_root_resolves_to_the_outside_and_is_not_under_the_root()
    {
        var roots = new AuthorisedRoots([_fx.Root]);

        // The lexical check this replaces would have said yes to every one of these.
        Assert.StartsWith(_fx.Root, _fx.SecretThroughJunction, StringComparison.OrdinalIgnoreCase);
        Assert.Equal(Path.GetFullPath(_fx.Outside).TrimEnd('\\'), AuthorisedRoots.ResolveFinal(_fx.Junction), StringComparer.OrdinalIgnoreCase);
        Assert.Equal(Path.GetFullPath(_fx.SecretFile), AuthorisedRoots.ResolveFinal(_fx.SecretThroughJunction), StringComparer.OrdinalIgnoreCase);

        Assert.False(roots.Contains(_fx.Junction));
        Assert.False(roots.Contains(_fx.SecretThroughJunction));
        Assert.Null(roots.Confine(_fx.SecretThroughJunction));
        Assert.False(roots.Contains(_fx.Outside));
        Assert.False(roots.Contains(_fx.SecretFile));

        // A genuine file under the root passes, and comes back in its resolved form.
        Assert.Equal(Path.GetFullPath(_fx.GenuineFile), roots.Confine(_fx.GenuineFile), StringComparer.OrdinalIgnoreCase);
        Assert.True(roots.Contains(_fx.Root));

        // A long-path prefix on the input is not a way around anything, and never leaks out.
        var prefixed = roots.Confine(@"\\?\" + _fx.GenuineFile);
        Assert.NotNull(prefixed);
        Assert.DoesNotContain(@"\\?\", prefixed, StringComparison.Ordinal);
        Assert.Null(roots.Confine(@"\\?\" + _fx.SecretThroughJunction));
    }

    [Fact]
    public void A_path_that_cannot_be_resolved_is_refused_and_a_sibling_prefix_is_not_the_root()
    {
        var roots = new AuthorisedRoots([_fx.Root]);
        Assert.False(roots.Contains(Path.Combine(_fx.Root, "missing.txt")), "a missing file reads as under the root but cannot be resolved, so it is refused");
        Assert.False(roots.Contains("relative\\path.txt"));
        Assert.False(roots.Contains(_fx.Root + "2"), "C:\\root2 is not under C:\\root");
        Assert.False(roots.Contains(string.Empty));
        Assert.True(AuthorisedRoots.IsWithin(@"C:\Owner\a.txt", @"C:\Owner"));
        Assert.True(AuthorisedRoots.IsWithin(@"C:\a.txt", @"C:\"));
        Assert.False(AuthorisedRoots.IsWithin(@"C:\Owner2\a.txt", @"C:\Owner"));
        Assert.Equal(@"C:\x\y", AuthorisedRoots.StripPrefix(@"\\?\C:\x\y"));
        Assert.Equal(@"\\server\share\y", AuthorisedRoots.StripPrefix(@"\\?\UNC\server\share\y"));
    }

    [Fact]
    public void A_root_that_is_itself_a_junction_admits_its_target_spelled_either_way()
    {
        var roots = new AuthorisedRoots([_fx.RootLink]);
        var real = Path.GetFullPath(_fx.RealRoot);
        Assert.Contains(roots.Resolved, r => r.Equals(real, StringComparison.OrdinalIgnoreCase));

        var throughLink = roots.Confine(Path.Combine(_fx.RootLink, "a.txt"));
        var direct = roots.Confine(Path.Combine(_fx.RealRoot, "a.txt"));
        Assert.Equal(Path.Combine(real, "a.txt"), throughLink, StringComparer.OrdinalIgnoreCase);
        Assert.Equal(throughLink, direct, StringComparer.OrdinalIgnoreCase);

        // The link root does not admit its neighbour, nor the junction fixture's outside.
        Assert.False(roots.Contains(_fx.GenuineFile));
        Assert.False(roots.Contains(_fx.SecretFile));
    }

    [Fact]
    public async Task Terminal_Get_ChildItem_through_the_junction_is_refused_before_a_process_starts()
    {
        var runner = new TerminalRunner(TerminalRunner.DefaultAllowlist, [_fx.Root], new ListLogger());

        Assert.Null(runner.Authorise($"Get-ChildItem \"{_fx.Junction}\""));
        Assert.Null(runner.Authorise($"Get-ChildItem \"{_fx.SecretThroughJunction}\""));
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => runner.ExecuteAsync($"Get-ChildItem \"{_fx.Junction}\"", TimeSpan.FromSeconds(5), CancellationToken.None));
        Assert.Equal(ErrorClasses.PermissionDenied, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Equal(0, runner.ProcessesStarted);

        // The root itself, and a real directory under it, still list.
        Assert.Equal("Get-ChildItem <path>", runner.Authorise($"Get-ChildItem \"{_fx.Root}\""));
        var listed = await runner.ExecuteAsync($"Get-ChildItem \"{_fx.Root}\"", TimeSpan.FromSeconds(30), CancellationToken.None);
        Assert.Equal(0, listed["exit_code"]!.GetValue<int>());
        Assert.Contains("genuine.txt", listed["stdout"]!.GetValue<string>(), StringComparison.Ordinal);
        Assert.Equal(1, runner.ProcessesStarted);
    }

    [Fact]
    public void File_open_and_file_reveal_through_the_junction_are_permission_denied_and_nothing_starts()
    {
        using var lab = new OperatorLab(roots: [_fx.Root]);

        var open = lab.ExpectFailure(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = _fx.SecretThroughJunction });
        Assert.Equal(ErrorClasses.PermissionDenied, open.ErrorClass);
        Assert.False(open.Retryable);
        Assert.Contains("does not resolve to a path inside", open.Message, StringComparison.Ordinal);

        var revealDir = lab.ExpectFailure(OperatorCapabilityNames.FileReveal, new JsonObject { ["path"] = _fx.Junction });
        Assert.Equal(ErrorClasses.PermissionDenied, revealDir.ErrorClass);
        var revealFile = lab.ExpectFailure(OperatorCapabilityNames.FileReveal, new JsonObject { ["path"] = _fx.SecretThroughJunction });
        Assert.Equal(ErrorClasses.PermissionDenied, revealFile.ErrorClass);

        // Through the terminal family too, with the same roots.
        var listed = lab.ExpectFailure(OperatorCapabilityNames.TerminalExecute, new JsonObject { ["command"] = $"Get-ChildItem \"{_fx.Junction}\"" });
        Assert.Equal(ErrorClasses.PermissionDenied, listed.ErrorClass);

        // A missing file under the root is refused the same way, not admitted by its spelling.
        var missing = lab.ExpectFailure(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = Path.Combine(_fx.Root, "missing.txt") });
        Assert.Equal(ErrorClasses.PermissionDenied, missing.ErrorClass);

        Assert.Empty(lab.Operator.StartedPids);
        Assert.Equal(0, lab.Operator.Terminal.ProcessesStarted);
    }

    [Fact]
    public void The_default_roots_are_the_document_folders_and_the_fixture_root_not_the_profile()
    {
        var defaults = OperatorOptions.DefaultRoots();
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

        Assert.DoesNotContain(defaults, r => r.Equals(profile, StringComparison.OrdinalIgnoreCase));
        // The fixture root is the one deliberate exception: %TEMP% lives under AppData\Local.
        Assert.DoesNotContain(defaults, r => r != OperatorOptions.FixtureRoot && (AuthorisedRoots.IsWithin(r, appData) || AuthorisedRoots.IsWithin(r, localAppData)));
        Assert.Contains(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), defaults);
        Assert.Contains(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), defaults);
        Assert.Contains(OperatorOptions.FixtureRoot, defaults);
        Assert.Contains(defaults, r => r.EndsWith("Downloads", StringComparison.OrdinalIgnoreCase));

        // Enforced: nothing under AppData or the profile root is admitted; the fixture is.
        var (_, fixtureFile) = OperatorLab.Fixture();
        var roots = new AuthorisedRoots(defaults);
        Assert.True(roots.Contains(fixtureFile));
        Assert.False(roots.Contains(Path.Combine(profile, "NTUSER.DAT")));
        Assert.False(roots.Contains(localAppData));
        Assert.False(roots.Contains(profile));
    }

    [LabFact]
    public void With_the_default_roots_a_genuine_file_under_the_fixture_root_still_opens()
    {
        using var lab = new OperatorLab(roots: OperatorOptions.DefaultRoots());
        var (_, file) = OperatorLab.Fixture();

        var opened = lab.Exec(OperatorCapabilityNames.FileOpen, new JsonObject { ["path"] = file, ["application"] = "notepad" });
        Assert.True(opened["opened"]!.GetValue<bool>());
        var pid = opened["pid"]!.GetValue<int>();
        lab.TrackPid(pid);
        Assert.True(opened["observed"]!["window_appeared"]!.GetValue<bool>());
        // The path the operator acted on is the resolved one, inside the fixture root.
        Assert.True(AuthorisedRoots.IsWithin(opened["path"]!.GetValue<string>(), AuthorisedRoots.ResolveFinal(OperatorOptions.FixtureRoot)!));

        var closed = lab.Exec(OperatorCapabilityNames.AppClose, new JsonObject { ["pid"] = pid, ["force"] = true });
        Assert.True(closed["closed"]!.GetValue<bool>());
    }
}
