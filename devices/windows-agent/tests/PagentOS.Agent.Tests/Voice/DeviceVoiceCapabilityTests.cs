using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Logging.Abstractions;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Agent.Tests.Support;
using PagentOS.Companion.Audio.Listening;
using PagentOS.Companion.Audio.Timing;
using PagentOS.DeviceService;
using PagentOS.SessionCompanion;
using PagentOS.SessionCompanion.Voice;
using Xunit;

namespace PagentOS.Agent.Tests.Voice;

/// <summary>
/// B47 rows 239 and 250-255 on the agent side: the manifest name, the routing, the heartbeat
/// fields, the Session-0 rule and the companion's composition.
/// </summary>
public sealed class DeviceVoiceCapabilityTests
{
    [Fact]
    public void The_microphone_provider_is_advertised_by_name_and_routed_to_the_companion()
    {
        Assert.Equal("desktop.voice_status", AgentCapabilities.DesktopVoiceStatus);
        Assert.Contains(AgentCapabilities.DesktopVoiceStatus, AgentCapabilities.Ambient);
        Assert.Contains(AgentCapabilities.DesktopVoiceStatus, AgentCapabilities.Compose(browserEnabled: false));
        Assert.True(AgentCapabilities.IsInteractive(AgentCapabilities.DesktopVoiceStatus));
        Assert.Equal(TimeSpan.FromSeconds(60), InteractiveCapabilityExecutor.TimeoutCapFor(AgentCapabilities.DesktopVoiceStatus));
    }

    [Fact]
    public void No_capability_name_can_turn_a_microphone_on_from_the_cloud()
    {
        var superset = AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true);
        var voice = superset.Where(n => Regex.IsMatch(n, "voice|mic|listen|audio_capture|record", RegexOptions.IgnoreCase)).ToList();

        Assert.Equal([AgentCapabilities.DesktopVoiceStatus], voice);
        Assert.False(DeviceVoiceContract.RemoteEnableAllowed);
    }

    [Fact]
    public void The_superset_manifest_grows_by_exactly_the_one_voice_name()
    {
        // The full (every gate on) manifest was 102 names before B47 and is 103 with it; the
        // always-on part was 11 and is 12. DEVICE_PROTOCOL.md §6o and the owner scripts carry the
        // same numbers. (The "85" in §8 is the 2026-09-11 deployed count, a historical fact.)
        // B48 then appended `desktop.camera_mode` (§6p), one more name everywhere: 104 / 13.
        Assert.Equal(104, AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true, operatorEnabled: true).Count);
        Assert.Equal(13, AgentCapabilities.Compose(browserEnabled: false).Count);
        Assert.Equal(13 + 1 + 30, AgentCapabilities.Compose(browserEnabled: true, displayPowerEnabled: true).Count);
        Assert.Equal(1, AgentCapabilities.Compose(browserEnabled: false).Count(n => n == AgentCapabilities.DesktopVoiceStatus));
    }

    [Fact]
    public void A_companion_without_voice_answers_disabled_rather_than_capability_missing()
    {
        var report = VoiceStatus.Disabled().Report();

        Assert.Equal(DeviceVoiceContract.StateDisabled, report["state"]!.GetValue<string>());
        Assert.Equal(DeviceVoiceContract.IndicatorOff, report["indicator"]!.GetValue<string>());
        Assert.False(report["listening"]!.GetValue<bool>());
        Assert.False(report["privacy"]!["raw_audio_persisted"]!.GetValue<bool>());
    }

    [Fact]
    public async Task The_runtime_answers_voice_status_from_the_voice_service_it_was_given()
    {
        var health = new DeviceVoiceHealth();
        health.SetState(DeviceVoiceContract.StateRunning);
        health.SetListening(true, DeviceVoiceContract.ModeContinuous, DeviceVoiceContract.IndicatorListening);
        health.SetMicrophone(present: true, running: true, muted: false, digitalSilence: false);

        var configured = await ExecuteThroughPipeAsync(health.Report);
        var unconfigured = await ExecuteThroughPipeAsync(null);

        Assert.Equal("running", configured!["state"]!.GetValue<string>());
        Assert.Equal("listening", configured["indicator"]!.GetValue<string>());
        Assert.True(configured["microphone"]!["capturing"]!.GetValue<bool>());
        Assert.Equal("disabled", unconfigured!["state"]!.GetValue<string>());
    }

    private static async Task<JsonObject?> ExecuteThroughPipeAsync(Func<JsonObject>? voiceStatus)
    {
        var pipeName = IpcTestSupport.NewPipeName();
        var server = await IpcTestSupport.NewListeningServerAsync(pipeName);
        using var companionCts = new CancellationTokenSource();
        var runtime = new CompanionRuntime(
            pipeName,
            new AppLauncher(new Dictionary<string, string>()),
            new ArtifactOpener([Path.GetTempPath()], new NoOpener()),
            NullLogger.Instance,
            new PagentOS.Agent.Core.Connection.BackoffPolicy(baseSeconds: 0.05, maxSeconds: 0.2),
            PagentOS.Agent.Core.Ipc.ServiceAdmissionPolicy.DeveloperMode(IpcTestSupport.CurrentSid()),
            voiceStatus: voiceStatus);
        var companion = Task.Run(() => runtime.RunAsync(companionCts.Token));
        try
        {
            Assert.True(await TestWait.UntilAsync(() => server.CompanionConnected, 15_000), "companion did not connect");
            return await server.ExecuteCapabilityAsync(
                AgentCapabilities.DesktopVoiceStatus,
                [],
                TimeSpan.FromSeconds(15),
                CancellationToken.None);
        }
        finally
        {
            companionCts.Cancel();
            try
            {
                await companion.WaitAsync(TimeSpan.FromSeconds(5));
            }
            catch (Exception)
            {
                // Teardown only.
            }

            await server.StopAsync(CancellationToken.None);
        }
    }

    [Fact]
    public void The_heartbeat_names_are_the_device_voice_contract_s()
    {
        Assert.Equal(DeviceVoiceContract.HeartbeatField, HeartbeatStatus.Voice);
        Assert.Equal(DeviceVoiceContract.LocalSnoozeField, HeartbeatStatus.LocalAlarmSnoozed);
        Assert.Contains(HeartbeatStatus.Voice, HeartbeatStatus.Fields);
        Assert.Contains(HeartbeatStatus.LocalAlarmSnoozed, HeartbeatStatus.Fields);

        var projected = HeartbeatStatus.Project(new JsonObject
        {
            ["voice"] = new DeviceVoiceHealth().Heartbeat(),
            ["local_alarm_snoozed"] = new JsonArray(),
            ["transcript"] = "never forwarded",
        })!;
        Assert.NotNull(projected["voice"]);
        Assert.False(projected.ContainsKey("transcript"));
    }

    [Fact]
    public void The_activity_status_carries_the_voice_health_and_drains_local_snoozes_once()
    {
        var clock = new ManualTimeProvider();
        var devices = new PagentOS.Companion.Audio.Fakes.FakeDeviceFactory(clock);
        using var alarm = new AlarmController(devices, () => "ren-laptop", NullLogger.Instance, clock, autoPump: false);
        using var arms = new AlarmArmController(
            new ArmedAlarmStore(Path.Combine(TestPaths.NewTempDir(), "armed.json"), NullLogger.Instance),
            alarm,
            NullLogger.Instance,
            clock,
            autoTick: false);
        var health = new DeviceVoiceHealth();
        health.SetState(DeviceVoiceContract.StateOffline, "no_owner_token");
        var reporter = new ActivityStatusReporter(new NoInput(), new NoDisplay(), () => alarm.RingingAlarmId, arms, voice: health.Heartbeat);

        alarm.Start(new JsonObject { ["alarm_id"] = "wake-1", ["snooze_minutes"] = 9, ["snoozes_left"] = 5 });
        Assert.True(arms.SnoozeRinging("test").Snoozed);
        var first = reporter.Compose();
        var second = reporter.Compose();

        // B48's camera pair is sent only by a companion with a camera path; this one has none.
        Assert.Equal(
            HeartbeatStatus.Fields.Where(f => f is not (HeartbeatStatus.Camera or HeartbeatStatus.Presence or HeartbeatStatus.NotifyActions)).Order(StringComparer.Ordinal),
            first.Select(p => p.Key).Order(StringComparer.Ordinal));
        Assert.Equal("offline", first["voice"]!["state"]!.GetValue<string>());
        Assert.Equal("no_owner_token", first["voice"]!["last_error"]!.GetValue<string>());
        var entry = Assert.Single(first["local_alarm_snoozed"]!.AsArray())!;
        Assert.Equal("wake-1", entry["alarm_id"]!.GetValue<string>());
        Assert.Equal(clock.GetUtcNow().AddMinutes(9), DateTimeOffset.Parse(entry["until"]!.GetValue<string>(), System.Globalization.CultureInfo.InvariantCulture));
        Assert.Empty(second["local_alarm_snoozed"]!.AsArray());

        // A companion without voice still reports, truthfully.
        var bare = new ActivityStatusReporter(new NoInput(), new NoDisplay(), () => null).Compose();
        Assert.Equal("disabled", bare["voice"]!["state"]!.GetValue<string>());
    }

    [Fact]
    public void The_session_0_service_never_references_the_audio_assembly()
    {
        // CLAUDE.md "Windows Agent rule" and device-voice.json privacy.capture_process: the
        // microphone is opened by the owner-session companion only.
        var root = Path.GetDirectoryName(CompanionSources.Directory())!;
        var project = File.ReadAllText(Path.Combine(root, "PagentOS.DeviceService", "PagentOS.DeviceService.csproj"));
        Assert.DoesNotContain("Companion.Audio", project, StringComparison.Ordinal);
        Assert.DoesNotContain("NAudio", project, StringComparison.Ordinal);
        foreach (var file in Directory.GetFiles(Path.Combine(root, "PagentOS.DeviceService"), "*.cs", SearchOption.AllDirectories))
        {
            var source = File.ReadAllText(file);
            Assert.DoesNotContain("WasapiCapture", source, StringComparison.Ordinal);
            Assert.DoesNotContain("DeviceListeningService", source, StringComparison.Ordinal);
        }
    }

    /// <summary>
    /// Regression (found by B47): every companion advertises <c>desktop.notify</c>, but the shipped
    /// <c>Program</c> never built its executor, so the real process answered its own advertised
    /// name with capability_missing (B11 requirement 369 was only ever true in tests that built
    /// the runtime by hand). The voice status has the same shape of risk.
    /// </summary>
    [Fact]
    public void The_shipped_companion_hands_the_runtime_every_always_advertised_executor()
    {
        var program = CompanionSources.Read("Program.cs");
        var call = program[program.IndexOf("new CompanionRuntime(", StringComparison.Ordinal)..];
        call = call[..call.IndexOf(");", StringComparison.Ordinal)];

        foreach (var argument in new[] { "notify:", "voiceStatus:", "alarm:", "alarmArms:", "activityStatus:", "greeting:" })
        {
            Assert.True(call.Contains(argument, StringComparison.Ordinal), $"Program.cs builds CompanionRuntime without '{argument}'");
        }

        Assert.Matches(@"new\s+Notify\.NotifyCapabilities\(", program);
        Assert.Matches(@"voice:\s*voiceHealth\.Heartbeat", program);
    }

    [Fact]
    public async Task A_crashed_voice_service_is_restarted_and_every_crash_is_counted()
    {
        var health = new DeviceVoiceHealth();
        health.SetState(DeviceVoiceContract.StateStarting);
        using var cts = new CancellationTokenSource();
        var runs = 0;

        var supervised = PagentOS.SessionCompanion.Program.SuperviseVoiceAsync(
            async ct =>
            {
                runs++;
                if (runs <= 2)
                {
                    throw new IOException("capture endpoint vanished");
                }

                health.SetState(DeviceVoiceContract.StateRunning);
                await Task.Delay(Timeout.Infinite, ct);
            },
            health,
            NullLogger.Instance,
            TimeProvider.System,
            cts.Token,
            maxDelay: TimeSpan.FromMilliseconds(20));

        Assert.True(await TestWait.UntilAsync(() => runs == 3 && health.State == DeviceVoiceContract.StateRunning));
        Assert.Equal(2, health.Restarts);
        Assert.Equal("service:IOException", health.LastError);
        cts.Cancel();
        await supervised;
    }

    [Fact]
    public async Task A_voice_service_that_is_configured_off_is_not_restarted()
    {
        var health = new DeviceVoiceHealth();
        var runs = 0;
        await PagentOS.SessionCompanion.Program.SuperviseVoiceAsync(
            _ =>
            {
                runs++;
                return Task.CompletedTask;
            },
            health,
            NullLogger.Instance,
            TimeProvider.System,
            CancellationToken.None,
            maxDelay: TimeSpan.FromMilliseconds(20));

        Assert.Equal(1, runs);
    }

    [Fact]
    public void Every_indicator_state_has_a_turkish_label_and_its_own_colour()
    {
        var labels = DeviceVoiceContract.IndicatorStates.Select(TrayPrivacyIndicator.LabelFor).ToList();
        Assert.DoesNotContain("Bilinmeyen durum", labels);
        Assert.Equal(labels.Count, labels.Distinct().Count());
        Assert.Equal(
            DeviceVoiceContract.IndicatorStates.Count,
            DeviceVoiceContract.IndicatorStates.Select(s => TrayPrivacyIndicator.ColourFor(s).ToArgb()).Distinct().Count());
    }

    private sealed class NoOpener : IFileOpener
    {
        public void Open(string fullPath)
        {
        }
    }

    private static class TestWait
    {
        /// <summary>A hang guard, not a latency claim: the bound only decides how long a stuck case takes to report.</summary>
        public static async Task<bool> UntilAsync(Func<bool> condition, int timeoutMs = 30_000)
        {
            var deadline = Environment.TickCount64 + timeoutMs;
            while (!condition())
            {
                if (Environment.TickCount64 > deadline)
                {
                    return false;
                }

                await Task.Delay(10);
            }

            return true;
        }
    }

    private sealed class NoInput : IInputActivitySource
    {
        public TimeSpan? IdleTime => null;
    }

    private sealed class NoDisplay : IDisplayStateObserver
    {
        public DisplayObservation Current => DisplayObservation.Nothing;
    }
}
