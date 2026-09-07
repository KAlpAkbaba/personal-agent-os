using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// <c>terminal.execute</c> (M19_DIGITAL_OPERATOR_SPEC.md §2, ADR-0082 decision 5): the
/// allowlist decides before a process exists. These run a real headless powershell.exe and
/// need no desktop.
/// </summary>
public sealed class TerminalRunnerTests
{
    private static TerminalRunner NewRunner(IReadOnlyList<string>? allowlist = null)
        => new(allowlist ?? OperatorLab.TestAllowlist, [Path.GetTempPath()], new ListLogger());

    [Fact]
    public async Task Hostname_runs_headless_and_answers_the_machines_name_with_exit_0()
    {
        var runner = NewRunner();
        var result = await runner.ExecuteAsync("hostname", TimeSpan.FromSeconds(30), CancellationToken.None);

        Assert.True(
            result["exit_code"]!.GetValue<int>() == 0,
            $"exit={result["exit_code"]} stdout=[{result["stdout"]}] stderr=[{result["stderr"]}]");
        Assert.Equal(Environment.MachineName, result["stdout"]!.GetValue<string>().Trim(), StringComparer.OrdinalIgnoreCase);
        Assert.Equal(string.Empty, result["stderr"]!.GetValue<string>().Trim());
        Assert.True(result["duration_ms"]!.GetValue<int>() >= 0);
        Assert.Equal("hostname", result["matched"]!.GetValue<string>());
        Assert.Equal(1, runner.ProcessesStarted);
    }

    [Fact]
    public async Task A_command_outside_the_allowlist_is_permission_denied_and_no_process_is_started()
    {
        var runner = NewRunner();
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => runner.ExecuteAsync("Remove-Item C:\\Windows\\Temp\\x", TimeSpan.FromSeconds(5), CancellationToken.None));

        Assert.Equal(ErrorClasses.PermissionDenied, ex.ErrorClass);
        Assert.False(ex.Retryable);
        Assert.Contains("nothing was run", ex.Message, StringComparison.Ordinal);
        Assert.Equal(0, runner.ProcessesStarted);
    }

    [Theory]
    [InlineData("hostname; Remove-Item x")]
    [InlineData("hostname | Out-File x")]
    [InlineData("hostname && whoami")]
    [InlineData("Get-Process -Name $(whoami)")]
    [InlineData("Get-Date `n whoami")]
    [InlineData("hostname extra")]
    [InlineData("Get-ChildItem C:\\Windows")]
    [InlineData("Get-ChildItem")]
    [InlineData("")]
    public void Composition_extra_tokens_and_paths_outside_the_roots_are_not_allowlisted(string command)
    {
        var runner = NewRunner();
        Assert.Null(runner.Authorise(command));
    }

    [Fact]
    public void The_allowlist_grammar_admits_wildcards_and_authorised_paths_only()
    {
        var runner = NewRunner();
        Assert.Equal("hostname", runner.Authorise("HOSTNAME"));
        Assert.Equal("Get-Process -Name *", runner.Authorise("Get-Process -Name notepad"));
        Assert.Equal("Get-ComputerInfo -Property *", runner.Authorise("Get-ComputerInfo -Property OsName"));
        Assert.Equal("Get-ChildItem <path>", runner.Authorise($"Get-ChildItem \"{Path.GetTempPath()}\""));
        Assert.Equal("Get-ChildItem <path>", runner.Authorise($"Get-ChildItem {Path.Combine(Path.GetTempPath(), "sub")}"));
        Assert.Null(runner.Authorise($"Get-ChildItem {Path.Combine(Path.GetTempPath(), "..", "..")}"));
        Assert.Equal("Start-Sleep *", runner.Authorise("Start-Sleep 20"));

        // The owner's default list does not carry the test-only Start-Sleep.
        var owner = new TerminalRunner(TerminalRunner.DefaultAllowlist, [Path.GetTempPath()], new ListLogger());
        Assert.Null(owner.Authorise("Start-Sleep 20"));
        Assert.DoesNotContain("Start-Sleep *", owner.Allowlist);
    }

    [Fact]
    public async Task A_command_that_runs_past_its_timeout_is_terminated_and_reported_as_timeout()
    {
        var runner = NewRunner();
        var started = DateTime.UtcNow;
        var ex = await Assert.ThrowsAsync<CapabilityException>(() => runner.ExecuteAsync("Start-Sleep 20", TimeSpan.FromSeconds(1), CancellationToken.None));

        Assert.Equal(ErrorClasses.Timeout, ex.ErrorClass);
        Assert.True(ex.Retryable);
        Assert.True(DateTime.UtcNow - started < TimeSpan.FromSeconds(10), "the runner must not wait for the sleep to finish");
    }

    [Fact]
    public async Task Get_ChildItem_on_an_authorised_root_lists_it()
    {
        var (dir, _) = OperatorLab.Fixture();
        var runner = NewRunner();
        var result = await runner.ExecuteAsync($"Get-ChildItem \"{dir}\"", TimeSpan.FromSeconds(30), CancellationToken.None);

        Assert.Equal(0, result["exit_code"]!.GetValue<int>());
        Assert.Contains("fixture.txt", result["stdout"]!.GetValue<string>(), StringComparison.Ordinal);
    }
}
