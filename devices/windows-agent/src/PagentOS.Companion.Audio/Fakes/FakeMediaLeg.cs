using System.Threading.Channels;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Media;
using PagentOS.Companion.Audio.Sideband;

namespace PagentOS.Companion.Audio.Fakes;

/// <summary>
/// Deterministic provider transport: records every command with a timestamp, lets a test or
/// the bench emit provider events, and can be told to fail sends (network loss). No network,
/// no timers of its own — behaviour is scripted by whoever holds it.
/// </summary>
public sealed class FakeMediaLeg(TimeProvider time) : IMediaLeg
{
    private readonly Channel<ProviderEvent> _events = Channel.CreateUnbounded<ProviderEvent>();
    private readonly object _sync = new();
    private readonly List<(long At, ProviderCommand Command)> _sent = new();

    public string Transport => "fake";

    public bool IsOpen { get; private set; }

    public ChannelReader<ProviderEvent> Events => _events.Reader;

    public RealtimeSessionGrant? Grant { get; private set; }

    public MediaLegOptions? Options { get; private set; }

    public int OpenCount { get; private set; }

    /// <summary>When set, sends throw and the leg reports itself disconnected on the next send.</summary>
    public bool SendsFail { get; set; }

    /// <summary>Optional hook run for every command (the bench's scripted provider lives here).</summary>
    public Func<ProviderCommand, long, Task>? OnCommand { get; set; }

    public IReadOnlyList<(long At, ProviderCommand Command)> Sent
    {
        get
        {
            lock (_sync)
            {
                return _sent.ToList();
            }
        }
    }

    public int AudioFramesSent => Sent.Count(s => s.Command is AppendAudioCommand);

    public IReadOnlyList<ProviderCommand> Commands => Sent.Select(s => s.Command).ToList();

    public void Emit(ProviderEvent providerEvent) => _events.Writer.TryWrite(providerEvent);

    public void EmitDisconnect(string reason = "simulated")
    {
        IsOpen = false;
        Emit(new DisconnectedEvent(time.GetTimestamp(), reason));
    }

    public Task OpenAsync(RealtimeSessionGrant grant, MediaLegOptions options, CancellationToken cancellationToken)
    {
        Grant = grant;
        Options = options;
        IsOpen = true;
        OpenCount++;
        SendsFail = false;
        Emit(new SessionReadyEvent(time.GetTimestamp(), "fake-" + OpenCount));
        return Task.CompletedTask;
    }

    public ValueTask SendAudioAsync(AudioFrame frame, CancellationToken cancellationToken)
        => new(RecordAsync(new AppendAudioCommand(frame.Pcm16)));

    public Task CommitTurnAsync(CancellationToken cancellationToken) => RecordAsync(new CommitTurnCommand());

    public Task CancelResponseAsync(CancellationToken cancellationToken) => RecordAsync(new CancelResponseCommand());

    public Task SubmitToolResultAsync(string callId, string outputJson, bool final, bool followUp, CancellationToken cancellationToken)
        => RecordAsync(new ToolResultCommand(callId, outputJson, final, followUp));

    public Task SayAsync(string text, CancellationToken cancellationToken) => RecordAsync(new SayCommand(text));

    public Task CloseAsync(CancellationToken cancellationToken)
    {
        IsOpen = false;
        return Task.CompletedTask;
    }

    public ValueTask DisposeAsync()
    {
        IsOpen = false;
        return ValueTask.CompletedTask;
    }

    private async Task RecordAsync(ProviderCommand command)
    {
        if (!IsOpen)
        {
            throw new InvalidOperationException("fake media leg is not open");
        }

        if (SendsFail)
        {
            EmitDisconnect("send_failed");
            throw new IOException("simulated network loss");
        }

        var at = time.GetTimestamp();
        lock (_sync)
        {
            _sent.Add((at, command));
        }

        if (OnCommand is { } hook)
        {
            await hook(command, at).ConfigureAwait(false);
        }
    }
}
