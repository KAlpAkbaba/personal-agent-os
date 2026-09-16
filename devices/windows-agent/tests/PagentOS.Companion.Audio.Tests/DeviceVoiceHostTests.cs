using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Listening;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Orchestration;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Testing;
using PagentOS.Companion.Audio.Tests.Support;
using PagentOS.Companion.Audio.Turn;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

/// <summary>
/// B47 rows 239/240/250-252: the device composition end to end - the listener, the gate's tap,
/// the real orchestrator, a fake provider leg and the in-process Cloud Core. No microphone.
/// </summary>
public sealed class DeviceVoiceHostTests : IAsyncDisposable
{
    private const string Token = "pagentos_sess_b47";
    private readonly TimeProvider _time = TimeProvider.System;
    private readonly FakeDeviceCatalog _catalog = new();
    private readonly FakeDeviceFactory _devices;
    private readonly InProcessFakeCloudCore _cloud = new(Token);
    private readonly FakeSidebandPushSource _pushes = new();
    private readonly List<FakeMediaLeg> _legs = [];
    private readonly DeviceVoiceHealth _health = new();
    private readonly RecordingPrivacyIndicator _indicator = new();
    private DeviceListeningService? _listening;

    public DeviceVoiceHostTests()
    {
        _devices = new FakeDeviceFactory(_time);
        _catalog.Set(FakeDeviceCatalog.LaptopMic(), FakeDeviceCatalog.LaptopSpeakers());
    }

    private static VoiceClientOptions DeviceOptions
        => VoiceCompanionHost.ClientOptionsFor(VoiceCompanionOptions.Parse("true", "http://cloud-core.fake", null, null, "server", "dev-1"));

    public async ValueTask DisposeAsync()
    {
        if (_listening is not null)
        {
            await _listening.DisposeAsync();
        }
    }

    private DeviceListeningService NewListening() => _listening = new DeviceListeningService(
        new DeviceListeningOptions { AutoTick = false },
        _catalog,
        _devices,
        new InMemoryListeningSettingsStore(),
        new FakeMicMuteMonitor(),
        null,
        new Listening.Spotting.NoKeywordSpotter("test", AudioFormat.Pcm16Mono24k.SampleRate),
        _indicator,
        null,
        _health,
        _time);

    private VoiceSessionOrchestrator NewSession(IAudioDeviceFactory gated) => new(
        DeviceOptions,
        _catalog,
        gated,
        _ =>
        {
            var leg = new FakeMediaLeg(_time);
            lock (_legs)
            {
                _legs.Add(leg);
            }

            return leg;
        },
        _cloud.CreateSidebandClient(),
        _pushes,
        _time);

    [Fact]
    public void The_device_client_never_streams_between_turns_and_commits_its_own_turns()
    {
        Assert.False(DeviceOptions.StreamWhileIdle);
        Assert.Equal(EndOfTurnMode.Client, DeviceOptions.EndOfTurn);
        Assert.Equal("dev-1", DeviceOptions.DeviceId);
    }

    /// <summary>
    /// The defect B47 found: before it, an enabled companion handed the realtime client the raw
    /// microphone with <c>StreamWhileIdle = true</c>, so every captured frame - the silent room
    /// included - was uplinked to the provider for as long as voice was on.
    /// </summary>
    [Fact]
    public async Task The_device_composition_uplinks_nothing_while_the_room_is_silent_and_the_utterance_when_the_owner_speaks()
    {
        var listening = NewListening();
        await listening.StartAsync(CancellationToken.None);
        var mic = _devices.CurrentCapture!;
        await using var session = NewSession(new GatedCaptureFactory(listening, _devices));
        await session.StartAsync(CancellationToken.None);
        Assert.True(session.DeviceGated);
        listening.SetCloudConnected(true);
        await listening.DrainAsync();
        var leg = _legs.Single();

        foreach (var frame in Phrases.Quiet(3000))
        {
            mic.Feed(frame);
        }

        await listening.DrainAsync();
        await session.DrainAsync();
        Assert.Equal(0, leg.AudioFramesSent);

        var speech = TestSupport.ModulatedSpeech(new SyntheticAudio(AudioFormat.Pcm16Mono24k), 400, _time.GetTimestamp).ToList();
        foreach (var frame in speech.Concat(Phrases.Quiet(3000)))
        {
            mic.Feed(frame);
        }

        await listening.DrainAsync();
        Assert.True(await TestSupport.WaitForAsync(() => leg.Commands.OfType<CommitTurnCommand>().Any()));
        await session.DrainAsync();

        var sent = leg.AudioFramesSent;
        // Pre-roll (<= 300 ms) + 400 ms of speech + the trailing silence the detector needed.
        Assert.InRange(sent, speech.Count, speech.Count + 15 + 125);
        Assert.Single(leg.Commands.OfType<CommitTurnCommand>());

        // And the room goes quiet again: nothing more leaves.
        foreach (var frame in Phrases.Quiet(2000))
        {
            mic.Feed(frame);
        }

        await listening.DrainAsync();
        await session.DrainAsync();
        Assert.Equal(sent, leg.AudioFramesSent);

        Assert.True(await TestSupport.WaitForAsync(() => _cloud.EventKindsFor(session.Grant!.SessionId).Contains(VoiceClientEvents.EndOfTurn)));
        var events = _cloud.EventsFor(session.Grant!.SessionId);
        var start = events.First(e => e["kind"]!.GetValue<string>() == VoiceClientEvents.MicSpeechStart);
        Assert.Equal("device_gate:vad_onset", start["payload"]!["reason"]!.GetValue<string>());
        var uplink = events.First(e => e["kind"]!.GetValue<string>() == VoiceClientEvents.UplinkFirstPacket);
        // Measured from the gate's decision, not from the oldest pre-roll frame (captured at t=0).
        Assert.InRange(uplink["payload"]!["mic_to_uplink_ms"]!.GetValue<double>(), 0, 5000);
    }

    [Fact]
    public async Task A_session_that_fails_is_replaced_the_failure_is_counted_and_listening_never_stops()
    {
        var listening = NewListening();
        var attempts = 0;
        var host = new DeviceVoiceHost(
            listening,
            _health,
            gated =>
            {
                attempts++;
                if (attempts == 1)
                {
                    throw new InvalidOperationException("provider refused the first session");
                }

                return NewSession(gated);
            },
            new StaticOwnerSessionTokenSource(Token),
            _devices,
            _time,
            NullLogger.Instance,
            new DeviceVoiceHostOptions { MinRestartDelay = TimeSpan.FromMilliseconds(10), MaxRestartDelay = TimeSpan.FromMilliseconds(50), SupervisePeriod = TimeSpan.FromMilliseconds(10) });
        using var cts = new CancellationTokenSource();
        var run = host.RunAsync(cts.Token);

        Assert.True(await TestSupport.WaitForAsync(() => host.SessionsOpened == 1 && _health.CloudConnected));
        Assert.Equal(1, _health.Restarts);
        Assert.Equal("session:InvalidOperationException", _health.LastError);
        Assert.Equal(DeviceVoiceContract.StateRunning, _health.State);
        Assert.NotNull(listening.Capture);

        // The Cloud Core closes the session (expiry): a new one is opened, the old leg is gone.
        _cloud.CloseSession(_cloud.Sessions.Keys.Single());
        foreach (var frame in TestSupport.ModulatedSpeech(new SyntheticAudio(AudioFormat.Pcm16Mono24k), 200, _time.GetTimestamp).Concat(Phrases.Quiet(1500)))
        {
            _devices.CurrentCapture!.Feed(frame);
        }

        Assert.True(await TestSupport.WaitForAsync(() => host.SessionsOpened == 2));
        Assert.Equal(1, _health.Restarts);

        cts.Cancel();
        await run;
        Assert.Equal(DeviceVoiceContract.StateStopped, _health.State);
        Assert.False(_health.CloudConnected);
        Assert.Equal(DeviceVoiceContract.IndicatorOff, _indicator.Current);
        _listening = null;
    }

    [Fact]
    public async Task Without_an_owner_token_the_device_keeps_listening_offline_and_looks_again()
    {
        var listening = NewListening();
        var token = new SwitchableTokens();
        var host = new DeviceVoiceHost(
            listening,
            _health,
            NewSession,
            token,
            _devices,
            _time,
            NullLogger.Instance,
            new DeviceVoiceHostOptions { TokenRetry = TimeSpan.FromMilliseconds(20), SupervisePeriod = TimeSpan.FromMilliseconds(10) });
        using var cts = new CancellationTokenSource();
        var run = host.RunAsync(cts.Token);

        Assert.True(await TestSupport.WaitForAsync(() => _health.LastError == "no_owner_token"));
        Assert.Equal(DeviceVoiceContract.StateOffline, _health.State);
        Assert.NotNull(listening.Capture);
        Assert.Equal(0, host.SessionsOpened);

        token.Value = Token;
        Assert.True(await TestSupport.WaitForAsync(() => host.SessionsOpened == 1 && _health.State == DeviceVoiceContract.StateRunning));

        cts.Cancel();
        await run;
        _listening = null;
    }

    [Fact]
    public async Task A_gated_session_uplinks_nothing_outside_a_turn_the_gate_opened()
    {
        // Defence in depth behind the gate: frames that reach the session without an
        // UtteranceStarted (a boundary lost, a tap attached late) are not guessed into a turn.
        var gate = new BareGate();
        await using var session = NewSession(new BareGateFactory(gate, _devices));
        await session.StartAsync(CancellationToken.None);
        var leg = _legs.Single();

        foreach (var frame in TestSupport.ModulatedSpeech(new SyntheticAudio(AudioFormat.Pcm16Mono24k), 400, _time.GetTimestamp))
        {
            gate.Emit(frame);
        }

        await session.DrainAsync();
        Assert.Equal(0, leg.AudioFramesSent);

        gate.Emit(new UtteranceStarted(_time.GetTimestamp(), "test"));
        foreach (var frame in TestSupport.ModulatedSpeech(new SyntheticAudio(AudioFormat.Pcm16Mono24k), 200, _time.GetTimestamp))
        {
            gate.Emit(frame);
        }

        await session.DrainAsync();
        Assert.Equal(10, leg.AudioFramesSent);
    }

    private sealed class BareGate : IAudioCapture, IUtteranceBoundarySource
    {
        public string DeviceId => "bare";

        public AudioFormat Format => AudioFormat.Pcm16Mono24k;

        public event Action<AudioFrame>? FrameCaptured;

        public event Action<UtteranceBoundary>? Boundary;

        public IReadOnlyList<string> UpstreamProcessors => ["bare"];

        public void Emit(AudioFrame frame) => FrameCaptured?.Invoke(frame);

        public void Emit(UtteranceBoundary boundary) => Boundary?.Invoke(boundary);

        public void ObserveTranscript(string text)
        {
        }

        public void Start()
        {
        }

        public void Stop()
        {
        }

        public void Dispose()
        {
        }
    }

    private sealed class BareGateFactory(BareGate gate, IAudioDeviceFactory playback) : IAudioDeviceFactory
    {
        public IAudioCapture OpenCapture(string deviceId, AudioFormat format) => gate;

        public IAudioPlayback OpenPlayback(string deviceId, AudioFormat format) => playback.OpenPlayback(deviceId, format);
    }

    [Fact]
    public void The_production_media_leg_notices_a_dead_network_in_seconds_not_minutes()
    {
        // Regression (found by B47): no keep-alive timeout meant a pulled cable left the device
        // believing the Cloud Core was reachable until TCP gave up, and offline commands stayed
        // off for that whole time.
        using var socket = WebSocketMediaLeg.NewSocket();
        Assert.Equal(TimeSpan.FromSeconds(5), socket.Options.KeepAliveInterval);
        Assert.Equal(TimeSpan.FromSeconds(10), socket.Options.KeepAliveTimeout);
        Assert.True(WebSocketMediaLeg.KeepAliveInterval + WebSocketMediaLeg.KeepAliveTimeout <= TimeSpan.FromSeconds(20));
    }

    [Fact]
    public void A_template_file_at_another_sample_rate_is_not_an_engine()
    {
        var set = new Listening.Spotting.KeywordTemplateSet(16000);
        var spotter = VoiceCompanionHost.BuildSpotter(set, AudioFormat.Pcm16Mono24k);
        Assert.IsType<Listening.Spotting.NoKeywordSpotter>(spotter);
        Assert.Contains("16000", spotter.UnavailableReason);
        Assert.IsType<Listening.Spotting.NoKeywordSpotter>(VoiceCompanionHost.BuildSpotter(null, AudioFormat.Pcm16Mono24k));
    }

    private sealed class SwitchableTokens : IOwnerSessionTokenSource
    {
        public volatile string? Value;

        public string? GetToken() => Value;
    }
}
