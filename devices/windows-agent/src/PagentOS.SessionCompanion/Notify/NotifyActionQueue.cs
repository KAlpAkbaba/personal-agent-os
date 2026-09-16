using System.Globalization;
using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Notify;

/// <summary>
/// Toast button presses waiting to reach the Cloud Core, as the heartbeat's
/// <c>notify_actions</c> list (<c>packages/protocol/desktop-notify.json</c>
/// <c>action_event</c>).
/// </summary>
/// <remarks>
/// <para>
/// A press arrives minutes after the <c>desktop.notify</c> command was answered, so it cannot
/// ride on that answer, and holding the command open for a human is how a queue fills up. The
/// heartbeat's <c>status</c> already carries this device's own events the same way
/// (<c>local_alarm_fired</c>, <c>local_alarm_snoozed</c>); this is the third such list.
/// </para>
/// <para>
/// <strong>Each press is sent in <see cref="Transmissions"/> consecutive reports, then
/// dropped.</strong> A heartbeat is fire-and-forget: a press drained into one that never
/// reached the broker would be gone. Three reports, ten seconds apart, survive a dropped
/// frame or a reconnect, and the Cloud Core records a press once however often it hears it
/// (it keys on notification, action and <c>pressed_at</c>). What this does NOT promise is
/// delivery across a companion restart: the queue is memory, and a press is not worth a file.
/// </para>
/// <para>
/// Bounded at <see cref="MaxEntries"/>; the oldest press is dropped first and counted.
/// </para>
/// </remarks>
public sealed class NotifyActionQueue(TimeProvider? time = null)
{
    /// <summary>At most this many presses wait at once (the contract's <c>max_entries</c>).</summary>
    public const int MaxEntries = 16;

    /// <summary>How many reports carry each press (the contract's <c>transmissions</c>).</summary>
    public const int Transmissions = 3;

    private readonly TimeProvider _time = time ?? TimeProvider.System;
    private readonly object _gate = new();
    private readonly List<Entry> _entries = [];
    private long _dropped;
    private long _recorded;

    /// <summary>Presses that still have a report to ride in.</summary>
    public int Pending
    {
        get
        {
            lock (_gate)
            {
                return _entries.Count;
            }
        }
    }

    /// <summary>Presses dropped because the queue was full.</summary>
    public long Dropped => Interlocked.Read(ref _dropped);

    /// <summary>Presses recorded since start.</summary>
    public long Recorded => Interlocked.Read(ref _recorded);

    /// <summary>Remembers one press. The caller has already checked that the toast and the action are ours.</summary>
    public void Record(string notificationId, string actionId)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(notificationId);
        ArgumentException.ThrowIfNullOrWhiteSpace(actionId);
        var pressedAt = _time.GetUtcNow().ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture);
        lock (_gate)
        {
            if (_entries.Count >= MaxEntries)
            {
                _entries.RemoveAt(0);
                Interlocked.Increment(ref _dropped);
            }

            _entries.Add(new Entry(notificationId, actionId, pressedAt));
        }

        Interlocked.Increment(ref _recorded);
    }

    /// <summary>
    /// The list for one report: every waiting press, each counted as sent once more; a press
    /// that has now been sent <see cref="Transmissions"/> times leaves the queue.
    /// </summary>
    public JsonArray Report()
    {
        var list = new JsonArray();
        lock (_gate)
        {
            foreach (var entry in _entries)
            {
                list.Add(new JsonObject
                {
                    ["notification_id"] = entry.NotificationId,
                    ["action_id"] = entry.ActionId,
                    ["pressed_at"] = entry.PressedAt,
                });
                entry.Sent++;
            }

            _entries.RemoveAll(entry => entry.Sent >= Transmissions);
        }

        return list;
    }

    private sealed class Entry(string notificationId, string actionId, string pressedAt)
    {
        public string NotificationId { get; } = notificationId;

        public string ActionId { get; } = actionId;

        public string PressedAt { get; } = pressedAt;

        public int Sent { get; set; }
    }
}
