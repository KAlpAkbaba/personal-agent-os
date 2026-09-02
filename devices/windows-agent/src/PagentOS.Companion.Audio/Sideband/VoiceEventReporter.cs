using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using PagentOS.Companion.Audio.Timing;

namespace PagentOS.Companion.Audio.Sideband;

public interface IVoiceEventReporter
{
    /// <summary>Records the event now (monotonic client time) and delivers it when the sideband is reachable. Returns the client_seq.</summary>
    ValueTask<long> ReportAsync(string kind, JsonObject? payload, string? text, CancellationToken cancellationToken);

    ValueTask<long> ReportAsync(string kind, JsonObject? payload, CancellationToken cancellationToken)
        => ReportAsync(kind, payload, text: null, cancellationToken);
}

/// <summary>A refusal the reporter cannot resolve by itself; the orchestrator decides what the session does next.</summary>
public enum SidebandFault
{
    /// <summary>409: another client holds the media leg. Stop the leg; re-attach before posting again.</summary>
    StaleLeg,

    /// <summary>410/404: the session is closed, expired or unknown. Nothing more can be posted.</summary>
    SessionGone,
}

/// <summary>
/// Orders, timestamps and delivers the client's timing/state events to Cloud Core in the
/// server's batch shape (<c>{"events":[{kind,t_ms,turn,payload[,text]}, ...]}</c>, at most 200
/// per request). Delivery is at-least-once: on a transient failure the head of the queue is
/// kept and re-sent unchanged when the network returns, never re-numbered. A 422 is a BUG in
/// this client — it is logged as such, the batch is dropped and delivery continues, because
/// retrying an invalid payload forever would silently stop every later event. A 409/410 is
/// reported to the owner of this reporter as a <see cref="SidebandFault"/>. Reporting is
/// never allowed to throw into the audio path — a lost event is counted, not fatal — except
/// for a payload that breaks the contract locally, which throws at the call site so the
/// test that produced it fails.
/// </summary>
public sealed class VoiceEventReporter(
    ISidebandClient sideband,
    string sessionId,
    TimeProvider time,
    long sessionStartedAt,
    ILogger? logger = null,
    Func<int>? turnProvider = null,
    Action<EventsAck>? onAck = null,
    Action<SidebandFault>? onFault = null) : IVoiceEventReporter
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

    /// <summary>Transient failures (network, 5xx): the batch was kept for re-delivery.</summary>
    public int DeliveryFailures { get; private set; }

    /// <summary>422s: batches Cloud Core refused as invalid. Any non-zero value is a client bug.</summary>
    public int RefusedBatches { get; private set; }

    /// <summary>The server's complaint for the most recent refused batch (for diagnostics and tests).</summary>
    public string? LastRefusal { get; private set; }

    public int StaleLegRefusals { get; private set; }

    /// <summary>Batches actually sent (one HTTP request each).</summary>
    public int BatchesSent { get; private set; }

    public ValueTask<long> ReportAsync(string kind, JsonObject? payload, CancellationToken cancellationToken)
        => ReportAsync(kind, payload, text: null, cancellationToken);

    public async ValueTask<long> ReportAsync(string kind, JsonObject? payload, string? text, CancellationToken cancellationToken)
    {
        var record = Enqueue(kind, payload, text);
        if (Online)
        {
            await FlushAsync(cancellationToken).ConfigureAwait(false);
        }

        return record.ClientSeq;
    }

    /// <summary>Validates against the server contract and queues; throws on a payload the server would refuse.</summary>
    public VoiceClientEventRecord Enqueue(string kind, JsonObject? payload, string? text = null)
    {
        if (!VoiceClientEvents.All.Contains(kind))
        {
            throw new ArgumentException($"'{kind}' is not a client event kind Cloud Core accepts", nameof(kind));
        }

        var body = payload ?? new JsonObject();
        if (RealtimeContract.FindForbiddenKey(body) is { } forbidden)
        {
            throw new ArgumentException($"event payload key '{forbidden}' would be refused by Cloud Core (audio/credential/transcript-shaped)", nameof(payload));
        }

        if (RealtimeContract.EncodedBytes(body) > RealtimeContract.MaxEventPayloadBytes)
        {
            throw new ArgumentException($"event payload exceeds {RealtimeContract.MaxEventPayloadBytes} bytes", nameof(payload));
        }

        if (text is { Length: > RealtimeContract.MaxEventTextChars })
        {
            text = text[..RealtimeContract.MaxEventTextChars];
        }

        var tMs = Math.Clamp((long)Math.Round(time.ElapsedMs(sessionStartedAt)), 0, RealtimeContract.MaxTMs);
        var turn = Math.Clamp(turnProvider?.Invoke() ?? 0, 0, RealtimeContract.MaxTurn);
        lock (_sync)
        {
            var record = new VoiceClientEventRecord(++_seq, kind, tMs, turn, (JsonObject)JsonNode.Parse(body.ToJsonString())!, text);
            _pending.Enqueue(record);
            return record;
        }
    }

    public void SetOnline(bool online) => Online = online;

    /// <summary>Delivers everything pending in order, batch by batch; stops (and goes offline) at the first transient failure.</summary>
    public async Task FlushAsync(CancellationToken cancellationToken)
    {
        await _flushLock.WaitAsync(cancellationToken).ConfigureAwait(false);
        try
        {
            while (true)
            {
                List<VoiceClientEventRecord> batch;
                lock (_sync)
                {
                    batch = _pending.Take(RealtimeContract.MaxEventsPerRequest).ToList();
                }

                if (batch.Count == 0)
                {
                    return;
                }

                EventsAck ack;
                try
                {
                    BatchesSent++;
                    ack = await sideband.ReportEventsAsync(sessionId, batch, cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
                {
                    throw;
                }
                catch (SidebandException ex) when (ex.IsPayloadRefused)
                {
                    RefusedBatches++;
                    LastRefusal = ex.Body;
                    logger?.LogError(
                        "CLIENT BUG: Cloud Core refused an event batch ({Count} events: {Kinds}) as invalid and it was dropped. Fix the client; the server is the contract. Detail: {Detail}",
                        batch.Count,
                        string.Join(",", batch.Select(b => b.Kind)),
                        ex.Body);
                    Dequeue(batch.Count, delivered: false);
                    continue;
                }
                catch (SidebandException ex) when (ex.IsStaleLeg)
                {
                    StaleLegRefusals++;
                    Online = false;
                    logger?.LogWarning("Cloud Core says another client holds the media leg (409); events held until re-attach");
                    Notify(SidebandFault.StaleLeg);
                    return;
                }
                catch (SidebandException ex) when (ex.IsSessionGone)
                {
                    Online = false;
                    logger?.LogWarning("Cloud Core no longer has session {SessionId} ({Status}); reporting stops", sessionId, ex.StatusCode);
                    Notify(SidebandFault.SessionGone);
                    return;
                }
                catch (Exception ex)
                {
                    DeliveryFailures++;
                    Online = false;
                    logger?.LogWarning("voice event delivery paused at client_seq={Seq}: {Reason}", batch[0].ClientSeq, ex.Message);
                    return;
                }

                Dequeue(batch.Count, delivered: true);
                Online = true;
                if (onAck is not null)
                {
                    try
                    {
                        onAck(ack);
                    }
                    catch (Exception ex)
                    {
                        logger?.LogWarning("events ack handler failed: {Reason}", ex.Message);
                    }
                }
            }
        }
        finally
        {
            _flushLock.Release();
        }
    }

    private void Dequeue(int count, bool delivered)
    {
        lock (_sync)
        {
            for (var i = 0; i < count && _pending.Count > 0; i++)
            {
                var record = _pending.Dequeue();
                if (delivered)
                {
                    _delivered.Add(record);
                }
            }
        }
    }

    private void Notify(SidebandFault fault)
    {
        try
        {
            onFault?.Invoke(fault);
        }
        catch (Exception ex)
        {
            logger?.LogWarning("sideband fault handler failed: {Reason}", ex.Message);
        }
    }
}

/// <summary>Test/bench sink: keeps every report in order and can share a trace list with other fakes to prove ordering.</summary>
public sealed class RecordingEventReporter(TimeProvider time, List<string>? trace = null) : IVoiceEventReporter
{
    private readonly List<(long At, string Event, JsonObject Data, string? Text)> _reports = new();
    private long _seq;

    public IReadOnlyList<(long At, string Event, JsonObject Data, string? Text)> Reports
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

    public ValueTask<long> ReportAsync(string kind, JsonObject? payload, string? text, CancellationToken cancellationToken)
    {
        if (!VoiceClientEvents.All.Contains(kind))
        {
            throw new ArgumentException($"'{kind}' is not a client event kind Cloud Core accepts", nameof(kind));
        }

        if (RealtimeContract.FindForbiddenKey(payload) is { } forbidden)
        {
            throw new ArgumentException($"event payload key '{forbidden}' would be refused by Cloud Core", nameof(payload));
        }

        lock (_reports)
        {
            _reports.Add((time.GetTimestamp(), kind, payload ?? new JsonObject(), text));
            trace?.Add("report:" + kind);
            return new ValueTask<long>(++_seq);
        }
    }
}
