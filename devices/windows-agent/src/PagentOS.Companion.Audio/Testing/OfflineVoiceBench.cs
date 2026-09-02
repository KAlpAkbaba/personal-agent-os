using System.Diagnostics;
using System.Text.Json.Nodes;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Fakes;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Orchestration;
using PagentOS.Companion.Audio.Session;
using PagentOS.Companion.Audio.Sideband;
using PagentOS.Companion.Audio.Turn;

namespace PagentOS.Companion.Audio.Testing;

public sealed record BenchOptions(
    int Turns = 3,
    int ProviderResponseDelayMs = 120,
    int ProviderAudioChunkMs = 40,
    int ProviderResponseMs = 600,
    double SimulatedPlaybackStopMs = 0,
    EndOfTurnMode EndOfTurn = EndOfTurnMode.Client,
    int FeedIntervalMs = 1);

/// <summary>
/// The offline bench (spec §8, "numbers for the gate"): the real orchestrator, the real
/// VAD/guard/barge-in/relay code, fake devices, a scripted provider on the fake media leg and
/// the in-process Cloud Core. Produces the M12 metrics without a microphone or a network.
/// Scenario per run: a plain turn; a hesitation turn ("… şey" then a 700 ms pause the guard
/// must not end); a barge-in while the assistant speaks; a long-running tool with preamble and
/// completion. Numbers are real measurements of the client code path; the provider's part of
/// them is the script's delay, stated in the report.
/// </summary>
public sealed class OfflineVoiceBench(BenchOptions? options = null)
{
    private static readonly AudioFormat Format = AudioFormat.Pcm16Mono24k;

    private readonly BenchOptions _options = options ?? new BenchOptions();
    private readonly TimeProvider _time = TimeProvider.System;

    public async Task<JsonObject> RunAsync(CancellationToken cancellationToken = default)
    {
        var stopwatch = Stopwatch.StartNew();
        var catalog = new FakeDeviceCatalog();
        catalog.Set(FakeDeviceCatalog.LaptopMic(), FakeDeviceCatalog.HeadsetMic(), FakeDeviceCatalog.LaptopSpeakers(), FakeDeviceCatalog.HeadsetEarpiece());
        var devices = new FakeDeviceFactory(_time) { SimulatedStopMs = _options.SimulatedPlaybackStopMs };
        var cloud = new InProcessFakeCloudCore("bench-token");
        var pushes = new FakeSidebandPushSource();
        var leg = new FakeMediaLeg(_time);
        var provider = new ScriptedProvider(leg, _time, _options);
        leg.OnCommand = provider.OnCommandAsync;

        var orchestrator = new VoiceSessionOrchestrator(
            new VoiceClientOptions { EndOfTurn = _options.EndOfTurn, ToolSilenceBoundMs = 4000 },
            catalog,
            devices,
            _ => leg,
            cloud.CreateSidebandClient(),
            pushes,
            _time);

        var grant = await orchestrator.StartAsync(cancellationToken).ConfigureAwait(false);
        var synth = new SyntheticAudio(Format);
        var log = new List<string>();

        try
        {
            for (var turn = 1; turn <= _options.Turns; turn++)
            {
                var kind = (turn - 1) % 4;
                switch (kind)
                {
                    case 0:
                        await PlainTurnAsync(orchestrator, devices, synth, provider, log, cancellationToken).ConfigureAwait(false);
                        break;
                    case 1:
                        await HesitationTurnAsync(orchestrator, devices, synth, provider, log, cancellationToken).ConfigureAwait(false);
                        break;
                    case 2:
                        await BargeInTurnAsync(orchestrator, devices, synth, provider, log, cancellationToken).ConfigureAwait(false);
                        break;
                    default:
                        await ToolTurnAsync(orchestrator, devices, synth, provider, pushes, log, cancellationToken).ConfigureAwait(false);
                        break;
                }
            }
        }
        finally
        {
            await orchestrator.StopAsync().ConfigureAwait(false);
        }

        var report = orchestrator.Latency.Summary();
        report["session_id"] = grant.SessionId;
        report["transport"] = leg.Transport;
        report["end_of_turn"] = _options.EndOfTurn.ToString().ToLowerInvariant();
        report["provider_script"] = new JsonObject
        {
            ["response_delay_ms"] = _options.ProviderResponseDelayMs,
            ["audio_chunk_ms"] = _options.ProviderAudioChunkMs,
            ["response_ms"] = _options.ProviderResponseMs,
        };
        report["fsm_events"] = new JsonArray(orchestrator.Fsm.EventKinds().Select(k => (JsonNode)k).ToArray());
        report["cloud_events"] = new JsonArray(cloud.EventNamesFor(grant.SessionId).Select(k => (JsonNode)k).ToArray());
        report["barge_ins"] = orchestrator.Fsm.BargeInCount;
        report["tool_call_requests"] = cloud.ToolCallRequests;
        report["tool_executions"] = cloud.ToolExecutions;
        report["defects"] = new JsonArray(orchestrator.Defects.Select(d => (JsonNode)d).ToArray());
        report["scenario_log"] = new JsonArray(log.Select(l => (JsonNode)l).ToArray());
        report["processing"] = new JsonObject
        {
            ["echo_cancellation"] = orchestrator.ProcessingReport?.EchoCancellation,
            ["noise_suppression"] = orchestrator.ProcessingReport?.NoiseSuppression,
        };
        report["wall_ms"] = stopwatch.ElapsedMilliseconds;
        report["disclaimer"] = "offline gate numbers: real client code path, fake devices, scripted provider — not the owner's machine";
        return report;
    }

    private async Task PlainTurnAsync(VoiceSessionOrchestrator o, FakeDeviceFactory devices, SyntheticAudio synth, ScriptedProvider provider, List<string> log, CancellationToken ct)
    {
        log.Add("plain: speak 600ms, silence until end-of-turn");
        await FeedAsync(devices, synth, voicedMs: 600, ct).ConfigureAwait(false);
        await FeedSilenceUntilAsync(devices, () => !o.Fsm.EventKinds().Contains("owner_speech_ended") ? false : o.Fsm.EventKinds().Count(k => k == "owner_speech_ended") >= TurnsEnded(o), 3000, ct).ConfigureAwait(false);
        await WaitForAsync(() => provider.ResponsesCompleted >= provider.ResponsesStarted && provider.ResponsesStarted > 0, 5000, ct).ConfigureAwait(false);
        await DrainAsync(o, devices, provider, ct).ConfigureAwait(false);
    }

    private async Task HesitationTurnAsync(VoiceSessionOrchestrator o, FakeDeviceFactory devices, SyntheticAudio synth, ScriptedProvider provider, List<string> log, CancellationToken ct)
    {
        log.Add("hesitation: speak 400ms, transcript ends with 'şey', 700ms pause must NOT end the turn, then 300ms more speech");
        var startedBefore = o.Fsm.EventKinds().Count(k => k == "owner_speech_started");
        await FeedAsync(devices, synth, voicedMs: 400, ct).ConfigureAwait(false);
        await WaitForAsync(() => o.Fsm.EventKinds().Count(k => k == "owner_speech_started") > startedBefore, 2000, ct).ConfigureAwait(false);
        provider.EmitTranscript("yarın toplantıyı şey", final: false);
        await Task.Delay(5, ct).ConfigureAwait(false);
        var endedBefore = o.Fsm.EventKinds().Count(k => k == "owner_speech_ended");
        await FeedAsync(devices, synth, 0, 700, ct).ConfigureAwait(false);
        await Task.Delay(20, ct).ConfigureAwait(false);
        var cutOff = o.Fsm.EventKinds().Count(k => k == "owner_speech_ended") > endedBefore;
        log.Add(cutOff ? "DEFECT: hesitation was cut off" : "ok: 700ms pause after 'şey' kept the turn open");
        provider.EmitTranscript("ertele", final: true);
        await FeedAsync(devices, synth, voicedMs: 300, ct).ConfigureAwait(false);
        await FeedSilenceUntilAsync(devices, () => o.Fsm.EventKinds().Count(k => k == "owner_speech_ended") > endedBefore, 4000, ct).ConfigureAwait(false);
        await WaitForAsync(() => provider.ResponsesCompleted >= provider.ResponsesStarted && provider.ResponsesStarted > 0, 5000, ct).ConfigureAwait(false);
        await DrainAsync(o, devices, provider, ct).ConfigureAwait(false);
    }

    private async Task BargeInTurnAsync(VoiceSessionOrchestrator o, FakeDeviceFactory devices, SyntheticAudio synth, ScriptedProvider provider, List<string> log, CancellationToken ct)
    {
        log.Add("barge-in: speak, wait for assistant audio, speak over it; expect playback stop -> cancel -> report");
        var bargeInsBefore = o.Fsm.BargeInCount;
        var endedBefore = o.Fsm.EventKinds().Count(k => k == "owner_speech_ended");
        await FeedAsync(devices, synth, voicedMs: 500, ct).ConfigureAwait(false);
        await FeedSilenceUntilAsync(devices, () => o.Fsm.EventKinds().Count(k => k == "owner_speech_ended") > endedBefore, 3000, ct).ConfigureAwait(false);
        await WaitForAsync(() => o.Fsm.State == VoiceClientState.AssistantSpeaking, 5000, ct).ConfigureAwait(false);
        await Task.Delay(_options.ProviderAudioChunkMs * 2, ct).ConfigureAwait(false);
        await FeedAsync(devices, synth, voicedMs: 300, ct).ConfigureAwait(false);
        await WaitForAsync(() => o.Fsm.BargeInCount > bargeInsBefore, 3000, ct).ConfigureAwait(false);
        var endedBefore2 = o.Fsm.EventKinds().Count(k => k == "owner_speech_ended");
        await FeedSilenceUntilAsync(devices, () => o.Fsm.EventKinds().Count(k => k == "owner_speech_ended") > endedBefore2, 3000, ct).ConfigureAwait(false);
        await WaitForAsync(() => provider.ResponsesCompleted >= provider.ResponsesStarted, 5000, ct).ConfigureAwait(false);
        await DrainAsync(o, devices, provider, ct).ConfigureAwait(false);
    }

    private async Task ToolTurnAsync(VoiceSessionOrchestrator o, FakeDeviceFactory devices, SyntheticAudio synth, ScriptedProvider provider, FakeSidebandPushSource pushes, List<string> log, CancellationToken ct)
    {
        log.Add("tool: provider emits a long-running tool call; expect relay -> preamble audio -> tool_completed push -> resumed speech");
        var endedBefore = o.Fsm.EventKinds().Count(k => k == "owner_speech_ended");
        provider.NextResponseIsToolCall = true;
        await FeedAsync(devices, synth, voicedMs: 500, ct).ConfigureAwait(false);
        await FeedSilenceUntilAsync(devices, () => o.Fsm.EventKinds().Count(k => k == "owner_speech_ended") > endedBefore, 3000, ct).ConfigureAwait(false);
        await WaitForAsync(() => o.Relay!.RunningCalls.Count > 0, 5000, ct).ConfigureAwait(false);
        var callId = o.Relay!.RunningCalls.First();
        await WaitForAsync(() => o.Fsm.EventKinds().Contains("assistant_progress"), 5000, ct).ConfigureAwait(false);
        await Task.Delay(_options.ProviderResponseMs, ct).ConfigureAwait(false);
        await DrainAsync(o, devices, provider, ct).ConfigureAwait(false);
        pushes.Push(SidebandPushKinds.ToolCompleted, new JsonObject { ["call_id"] = callId, ["result"] = new JsonObject { ["summary"] = "araştırma tamam" } });
        await WaitForAsync(() => o.Relay!.RunningCalls.Count == 0, 5000, ct).ConfigureAwait(false);
        await WaitForAsync(() => provider.ResponsesCompleted >= provider.ResponsesStarted, 5000, ct).ConfigureAwait(false);
        await DrainAsync(o, devices, provider, ct).ConfigureAwait(false);
    }

    private static int TurnsEnded(VoiceSessionOrchestrator o) => o.Fsm.EventKinds().Count(k => k == "owner_speech_ended");

    private async Task FeedAsync(FakeDeviceFactory devices, SyntheticAudio synth, int voicedMs, CancellationToken ct)
        => await FeedAsync(devices, synth, voicedMs, 0, ct).ConfigureAwait(false);

    private async Task FeedAsync(FakeDeviceFactory devices, SyntheticAudio synth, int voicedMs, int silenceMs, CancellationToken ct)
    {
        var capture = devices.CurrentCapture!;
        for (var fed = 0; fed < voicedMs; fed += 20)
        {
            capture.Feed(synth.Tone(20, _time.GetTimestamp()));
            await Task.Delay(_options.FeedIntervalMs, ct).ConfigureAwait(false);
        }

        for (var fed = 0; fed < silenceMs; fed += 20)
        {
            capture.Feed(SyntheticAudio.Noise(Format, 20, _time.GetTimestamp()));
            await Task.Delay(_options.FeedIntervalMs, ct).ConfigureAwait(false);
        }
    }

    private async Task FeedSilenceUntilAsync(FakeDeviceFactory devices, Func<bool> condition, int maxMs, CancellationToken ct)
    {
        var capture = devices.CurrentCapture!;
        for (var fed = 0; fed < maxMs && !condition(); fed += 20)
        {
            capture.Feed(SyntheticAudio.Noise(Format, 20, _time.GetTimestamp()));
            await Task.Delay(_options.FeedIntervalMs, ct).ConfigureAwait(false);
        }

        await WaitForAsync(condition, 500, ct).ConfigureAwait(false);
    }

    private async Task DrainAsync(VoiceSessionOrchestrator o, FakeDeviceFactory devices, ScriptedProvider provider, CancellationToken ct)
    {
        // Let the provider finish whatever it started before the next scenario begins, so a
        // late response cannot masquerade as an extra barge-in in the numbers.
        await WaitForAsync(() => provider.Quiet, 5000, ct).ConfigureAwait(false);
        devices.CurrentPlayback!.Drain();
        await Task.Delay(10, ct).ConfigureAwait(false);
        // Keep the uplink alive between scenario steps as a real microphone would.
        await FeedSilenceUntilAsync(devices, () => o.Fsm.State is VoiceClientState.Idle or VoiceClientState.Listening, 1000, ct).ConfigureAwait(false);
        await WaitForAsync(() => provider.Quiet, 5000, ct).ConfigureAwait(false);
        devices.CurrentPlayback!.Drain();
    }

    public static async Task WaitForAsync(Func<bool> condition, int timeoutMs, CancellationToken ct)
    {
        var deadline = Environment.TickCount64 + timeoutMs;
        while (!condition())
        {
            if (Environment.TickCount64 > deadline)
            {
                return;
            }

            await Task.Delay(2, ct).ConfigureAwait(false);
        }
    }

    /// <summary>
    /// A provider on the far side of the fake leg: after a commit (Client mode) or after
    /// enough post-speech silence (Server mode) it starts a response, streams audio chunks
    /// on a timer, honours cancel, and can emit a tool call instead of audio. Its Server-mode
    /// end-of-turn is semantic in the one way that matters for the gate: it applies the same
    /// hesitation guard to the transcript it delivered, as the provider the spec selects for
    /// (<c>end_of_turn == semantic</c>) would not end a turn on "… şey".
    /// </summary>
    private sealed class ScriptedProvider(FakeMediaLeg leg, TimeProvider time, BenchOptions options)
    {
        private readonly object _sync = new();
        private readonly HesitationGuard _guard = new();
        private int _responseCounter;
        private string? _activeResponse;
        private bool _cancelled;
        private int _silentFrames;
        private bool _sawSpeech;
        private int _toolCounter;
        private string? _transcriptTail;

        public int ResponsesStarted { get; private set; }

        public int ResponsesCompleted { get; private set; }

        public bool NextResponseIsToolCall { get; set; }

        public bool Quiet
        {
            get
            {
                lock (_sync)
                {
                    return ResponsesCompleted >= ResponsesStarted;
                }
            }
        }

        public void EmitTranscript(string text, bool final)
        {
            lock (_sync)
            {
                _transcriptTail = ((_transcriptTail ?? string.Empty) + " " + text).Trim();
            }

            leg.Emit(new TranscriptDeltaEvent(time.GetTimestamp(), text, final));
        }

        public Task OnCommandAsync(ProviderCommand command, long at)
        {
            switch (command)
            {
                case AppendAudioCommand audio:
                    if (options.EndOfTurn == EndOfTurnMode.Server)
                    {
                        ObserveServerVad(audio);
                    }

                    break;
                case CommitTurnCommand:
                    StartResponse();
                    break;
                case CancelResponseCommand:
                    lock (_sync)
                    {
                        _cancelled = true;
                    }

                    break;
                case ToolResultCommand:
                    // The provider speaks the provisional/final tool result as a new response.
                    StartResponse(afterTool: true);
                    break;
                case SayCommand:
                    StartResponse();
                    break;
            }

            return Task.CompletedTask;
        }

        private void ObserveServerVad(AppendAudioCommand audio)
        {
            var frame = new AudioFrame(Format, audio.Pcm16, 0);
            var loud = Audio.Processing.AudioEnergy.Dbfs(frame.Samples) > -40;
            if (loud)
            {
                _sawSpeech = true;
                _silentFrames = 0;
                return;
            }

            int requiredFrames;
            lock (_sync)
            {
                requiredFrames = _guard.Evaluate(new HesitationContext(_transcriptTail, 0, 10)).RequiredTrailingSilenceMs / 20;
            }

            if (_sawSpeech && ++_silentFrames >= requiredFrames)
            {
                _sawSpeech = false;
                _silentFrames = 0;
                lock (_sync)
                {
                    _transcriptTail = null;
                }

                StartResponse();
            }
        }

        private void StartResponse(bool afterTool = false)
        {
            string id;
            lock (_sync)
            {
                id = "resp-" + (++_responseCounter).ToString("D3");
                _activeResponse = id;
                _cancelled = false;
                ResponsesStarted++;
            }

            var tool = !afterTool && NextResponseIsToolCall;
            NextResponseIsToolCall = false;
            _ = Task.Run(async () =>
            {
                await Task.Delay(options.ProviderResponseDelayMs).ConfigureAwait(false);
                leg.Emit(new ResponseStartedEvent(time.GetTimestamp(), id));
                if (tool)
                {
                    leg.Emit(new ToolCallEvent(time.GetTimestamp(), "call-" + (++_toolCounter), "research", "{\"topic\":\"ai\"}"));
                    leg.Emit(new ResponseDoneEvent(time.GetTimestamp(), id, "completed"));
                    lock (_sync)
                    {
                        ResponsesCompleted++;
                    }

                    return;
                }

                var synth = new SyntheticAudio(Format);
                for (var sent = 0; sent < options.ProviderResponseMs; sent += options.ProviderAudioChunkMs)
                {
                    bool cancelled;
                    lock (_sync)
                    {
                        cancelled = _cancelled || _activeResponse != id;
                    }

                    if (cancelled)
                    {
                        leg.Emit(new ResponseDoneEvent(time.GetTimestamp(), id, "cancelled"));
                        lock (_sync)
                        {
                            ResponsesCompleted++;
                        }

                        return;
                    }

                    leg.Emit(new AudioDeltaEvent(time.GetTimestamp(), id, synth.Tone(options.ProviderAudioChunkMs, 0, 440, 0.2).Pcm16));
                    await Task.Delay(options.ProviderAudioChunkMs).ConfigureAwait(false);
                }

                leg.Emit(new ResponseDoneEvent(time.GetTimestamp(), id, "completed"));
                lock (_sync)
                {
                    ResponsesCompleted++;
                }
            });
        }
    }
}
