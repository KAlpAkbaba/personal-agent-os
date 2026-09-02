using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Companion.Audio.Timing;

namespace PagentOS.Companion.Audio.Sideband;

public interface IVoiceEventReporter
{
    /// <summary>Records the event now (monotonic client time) and delivers it when the sideband is reachable. Returns the client_seq.</summary>
    ValueTask<long> ReportAsync(string eventName, JsonObject? data, CancellationToken cancellationToken);
}

/// <summary>
/// Orders, timestamps and delivers the client's timing/state events to Cloud Core. Delivery
/// is at-least-once with a per-session <c>client_seq</c> so Cloud Core can make it
/// exactly-once: on failure the head of the queue is kept and re-sent unchanged when the
/// network returns, never re-numbered. Reporting is never allowed to throw into the audio
/// path — a lost event is counted, not fatal.
/// </summary>
public sealed class VoiceEventReporter(
    ISidebandClient sideband,
    string sessionId,
    TimeProvider time,
    long sessionStartedAt,
    ILogger? logger = null) : IVoiceEventReporter
{
    private readonly SemaphoreSlim _flushLock = new(1, 1);
    private readonly Queue<VoiceClientEventRecord> _pending = new();
    private readonly List<VoiceClientEventRecord> _delivered = new();
    private readonly object _sync = new();
    private long _seq;

    public bool Online { get; private set; } = true;

    public int PendingCount
    {
        get
        {
            lock (_sync)
            {
                return _pending.Count;
            }
        }
    }

    public IReadOnlyList<VoiceClientEventRecord> Delivered
    {
        get
        {
            lock (_sync)
            {
                return _delivered.ToList();
            }
        }
    }

    public int DeliveryFailures { get; private set; }

    public async ValueTask<long> ReportAsync(string eventName, JsonObject? data, CancellationToken cancellationToken)
    {
        if (!VoiceClientEvents.All.Contains(eventName))
        {
            throw new ArgumentException($"'{eventName}' is not a client event name the contract knows", nameof(eventName));
        }

        VoiceClientEventRecord record;
        lock (_sync)
        {
            record = new VoiceClientEventRecord(++_seq, eventName, time.ElapsedMs(sessionStartedAt), data ?? new JsonObject());
            _pending.Enqueue(record);
        }

        if (Online)
        {
            await FlushAsync(cancellationToken).ConfigureAwait(false);
        }

        return record.ClientSeq;
    }

    public void SetOnline(bool online) => Online = online;

    /// <summary>Delivers everything pending in order; stops (and goes offline) at the first failure.</summary>
    public async Task FlushAsync(CancellationToken cancellationToken)
    {
        await _flushLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            while (true)
            {
                VoiceClientEventRecord? head;
                lock (_sync)
                {
                    head = _pending.Count == 0 ? null : _pending.Peek();
                }

                if (head is null)
                {
                    return;
                }

                try
                {
                    await sideband.ReportEventAsync(sessionId, head, cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    DeliveryFailures++;
                    Online = false;
                    logger?.LogWarning("voice event delivery paused at client_seq={Seq}: {Reason}", head.ClientSeq, ex.Message);
                    return;
                }

                lock (_sync)
                {
                    _pending.Dequeue();
                    _delivered.Add(head);
                }

                Online = true;
            }
        }
        finally
        {
            _flushLock.Release();
        }
    }
}

/// <summary>Test/bench sink: keeps every report in order and can share a trace list with other fakes to prove ordering.</summary>
public sealed class RecordingEventReporter(TimeProvider time, List<string>? trace = null) : IVoiceEventReporter
{
    private readonly List<(long At, string Event, JsonObject Data)> _reports = new();
    private long _seq;

    public IReadOnlyList<(long At, string Event, JsonObject Data)> Reports
    {
        get
        {
            lock (_reports)
            {
                return _reports.ToList();
            }
        }
    }

    public IReadOnlyList<string> EventNames => Reports.Select(r => r.Event).ToList();

    public ValueTask<long> ReportAsync(string eventName, JsonObject? data, CancellationToken cancellationToken)
    {
        lock (_reports)
        {
            _reports.Add((time.GetTimestamp(), eventName, data ?? new JsonObject()));
            trace?.Add("report:" + eventName);
            return new ValueTask<long>(++_seq);
        }
    }
}
