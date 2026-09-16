using System.Security.Principal;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// B30 requirements 119-122: processes and services, each answer READ back from the machine
/// after acting, each policy refused before anything is touched.
/// </summary>
[Collection(OperatorLabCollection.Name)]
public sealed class ProcessServiceTests : IDisposable
{
    private readonly OperatorLab _lab = new();

    public void Dispose() => _lab.Dispose();

    private static bool Elevated()
    {
        using var identity = WindowsIdentity.GetCurrent();
        return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
    }

    [Fact]
    public void Process_list_reads_this_very_test_process_and_filters_by_image()
    {
        // Filtered by an image that certainly runs: the test host itself.
        var self = Environment.ProcessPath is null ? "dotnet.exe" : Path.GetFileName(Environment.ProcessPath);
        var listed = _lab.Exec(OperatorCapabilityNames.ProcessList, new JsonObject { ["name"] = self });
        var rows = (JsonArray)listed["processes"]!;
        Assert.NotEmpty(rows);
        Assert.Contains(rows, row => row!["pid"]!.GetValue<int>() == Environment.ProcessId);
        Assert.All(rows, row => Assert.Equal(self, row!["image"]!.GetValue<string>(), ignoreCase: true));
        Assert.Equal(rows.Count, listed["observed"]!["count"]!.GetValue<int>());

        // Unfiltered: more than the filtered set, and a capped, window-first ordering.
        var all = (JsonArray)_lab.Exec(OperatorCapabilityNames.ProcessList, new JsonObject())["processes"]!;
        Assert.True(all.Count >= rows.Count);
        Assert.True(all.Count <= 200);
    }

    [Fact]
    public void Process_stop_refuses_an_image_outside_the_policy_before_looking_at_any_process()
    {
        var failure = _lab.ExpectFailure(OperatorCapabilityNames.ProcessStop, new JsonObject { ["name"] = "powershell.exe" });
        Assert.Equal(ErrorClasses.PermissionDenied, failure.ErrorClass);
        Assert.False(failure.Retryable);
        Assert.Contains("nothing was stopped", failure.Message, StringComparison.Ordinal);

        // A path is reduced to its image before the policy reads it: the same refusal.
        var pathed = _lab.ExpectFailure(OperatorCapabilityNames.ProcessStop, new JsonObject { ["name"] = @"C:\Windows\System32\svchost.exe" });
        Assert.Equal(ErrorClasses.PermissionDenied, pathed.ErrorClass);
    }

    [LabFact]
    public void Process_stop_closes_a_launched_notepad_by_image_and_the_list_then_shows_none()
    {
        // process.stop is BY IMAGE, which is the product's promise ("Not Defteri'ni sonlandır"
        // ends every Notepad). On the owner's own desktop that would reach a Notepad the owner
        // has open — the first run of this test did exactly that (2026-09-14): it sent
        // WM_CLOSE to an unsaved a.txt and, correctly, reported the save prompt as ``modal``
        // and answered nothing. A lab must not do that twice, so with a foreign Notepad
        // running this test proves nothing and says so rather than touching it.
        var foreign = (JsonArray)_lab.Exec(OperatorCapabilityNames.ProcessList, new JsonObject { ["name"] = "notepad.exe" })["processes"]!;
        if (foreign.Count > 0)
        {
            _lab.Log.LogInformation($"skipped: {foreign.Count} Notepad process(es) already running on this desktop, not the lab's to stop");
            return;
        }

        var (pid, _, _) = _lab.LaunchNotepad();
        var before = (JsonArray)_lab.Exec(OperatorCapabilityNames.ProcessList, new JsonObject { ["name"] = "notepad.exe" })["processes"]!;
        Assert.Contains(before, row => row!["pid"]!.GetValue<int>() == pid);

        var stopped = _lab.Exec(OperatorCapabilityNames.ProcessStop, new JsonObject { ["name"] = "notepad.exe" });
        Assert.True(stopped["stopped"]!.GetValue<bool>(), stopped.ToJsonString());
        Assert.Equal("wm_close", stopped["method"]!.GetValue<string>());
        Assert.Contains(((JsonArray)stopped["pids"]!).Select(p => p!.GetValue<int>()), p => p == pid);
        Assert.Equal(0, stopped["observed"]!["remaining"]!.GetValue<int>());
        Assert.True(OperatorLab.WaitForExit(pid, TimeSpan.FromSeconds(5)));

        var after = (JsonArray)_lab.Exec(OperatorCapabilityNames.ProcessList, new JsonObject { ["name"] = "notepad.exe" })["processes"]!;
        Assert.DoesNotContain(after, row => row!["pid"]!.GetValue<int>() == pid);
    }

    [Fact]
    public void Service_status_reads_the_spooler_from_the_service_control_manager()
    {
        var status = _lab.Exec(OperatorCapabilityNames.ServiceStatus, new JsonObject { ["name"] = "Spooler" });
        Assert.Equal("Spooler", status["name"]!.GetValue<string>());
        var state = status["state"]!.GetValue<string>();
        Assert.Contains(state, new[] { "Running", "Stopped", "Start Pending", "Stop Pending", "Paused" });
        Assert.Equal(state, status["observed"]!["state"]!.GetValue<string>());

        var missing = _lab.ExpectFailure(OperatorCapabilityNames.ServiceStatus, new JsonObject { ["name"] = "NoSuchServiceB30" });
        Assert.Equal(ErrorClasses.UiTargetNotFound, missing.ErrorClass);

        var injected = _lab.ExpectFailure(OperatorCapabilityNames.ServiceStatus, new JsonObject { ["name"] = "Spooler' OR Name='wuauserv" });
        Assert.Equal(ErrorClasses.ValidationError, injected.ErrorClass);
    }

    [Fact]
    public void Service_restart_refuses_a_service_outside_the_policy_and_an_unelevated_companion()
    {
        var policy = _lab.ExpectFailure(OperatorCapabilityNames.ServiceRestart, new JsonObject { ["name"] = "bthserv" });
        Assert.Equal(ErrorClasses.PermissionDenied, policy.ErrorClass);
        Assert.Contains("nothing was restarted", policy.Message, StringComparison.Ordinal);

        if (Elevated())
        {
            // An elevated runner (CI) genuinely restarts the spooler and reads it Running.
            var restarted = _lab.Exec(OperatorCapabilityNames.ServiceRestart, new JsonObject { ["name"] = "Spooler" }, budgetSeconds: 60);
            Assert.True(restarted["restarted"]!.GetValue<bool>(), restarted.ToJsonString());
            Assert.Equal("Running", restarted["state"]!.GetValue<string>());
            Assert.Equal("Running", restarted["observed"]!["state"]!.GetValue<string>());
            return;
        }

        // The owner's companion runs unelevated: the honest answer names UAC, and touches nothing.
        var stateBefore = _lab.Exec(OperatorCapabilityNames.ServiceStatus, new JsonObject { ["name"] = "Spooler" })["state"]!.GetValue<string>();
        var elevation = _lab.ExpectFailure(OperatorCapabilityNames.ServiceRestart, new JsonObject { ["name"] = "Spooler" });
        Assert.Equal(ErrorClasses.PermissionDenied, elevation.ErrorClass);
        Assert.Contains("UAC", elevation.Message, StringComparison.Ordinal);
        var stateAfter = _lab.Exec(OperatorCapabilityNames.ServiceStatus, new JsonObject { ["name"] = "Spooler" })["state"]!.GetValue<string>();
        Assert.Equal(stateBefore, stateAfter);
    }
}
