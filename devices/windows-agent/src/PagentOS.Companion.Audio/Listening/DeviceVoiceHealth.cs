using System.Globalization;
using System.Text.Json.Nodes;

namespace PagentOS.Companion.Audio.Listening;

/// <summary>
/// Rows 250-252: the device voice service's health, written by the host and the listening
/// loop and read by the heartbeat and <c>desktop.voice_status</c>. One object, two
/// projections: <see cref="Heartbeat"/> is the compact, closed set of
/// <see cref="DeviceVoiceContract.HeartbeatKeys"/>; <see cref="Report"/> adds what an owner
/// harness needs to judge the device. Neither ever carries a transcript, a phrase, a device
/// name or audio - only states, flags, counters and error CLASSES.
/// </summary>
public sealed class DeviceVoiceHealth
{
    private readonly object _sync = new();
    private readonly TimeProvider _time;
    private string _state = DeviceVoiceContract.StateDisabled;
    private string _indicator = DeviceVoiceContract.IndicatorOff;
    private string _mode = ListeningSettings.Default.Mode.ToWire();
    private bool _listening;
    private bool? _micMuted;
    private bool _cloudConnected;
    private int _restarts;
    private string? _lastError;
    private bool _capturePresent;
    private bool _captureRunning;
    private bool _wakeWordAvailable;
    private string? _wakeWordReason;
    private IReadOnlyList<string> _offlinePhrases = [];
    private string? _offlineReason;
    private int _offlineExecuted;
    private int _offlineRefused;
    private JsonObject? _lastOfflineCommand;
    private long _utterances;
    private long _framesAdmitted;
    private long _framesDropped;
    private bool _digitalSilence;
    private string? _pushToTalkKey;
    private DateTimeOffset? _startedAt;
    private DateTimeOffset? _sessionStartedAt;
    private DateTimeOffset? _updatedAt;

    public DeviceVoiceHealth(TimeProvider? time = null)
    {
        _time = time ?? TimeProvider.System;
    }

    public string State
    {
        get
        {
            lock (_sync)
            {
                return _state;
            }
        }
    }

    public string Indicator
    {
        get
        {
            lock (_sync)
            {
                return _indicator;
            }
        }
    }

    public bool CloudConnected
    {
        get
        {
            lock (_sync)
            {
                return _cloudConnected;
            }
        }
    }

    public int Restarts
    {
        get
        {
            lock (_sync)
            {
                return _restarts;
            }
        }
    }

    public string? LastError
    {
        get
        {
            lock (_sync)
            {
                return _lastError;
            }
        }
    }

    public void SetState(string state, string? error = null)
    {
        if (!DeviceVoiceContract.ServiceStates.Contains(state))
        {
            throw new ArgumentException($"'{state}' is not a service state the contract names", nameof(state));
        }

        lock (_sync)
        {
            _state = state;
            if (error is not null)
            {
                _lastError = error;
            }

            if (state == DeviceVoiceContract.StateStarting && _startedAt is null)
            {
                _startedAt = _time.GetUtcNow();
            }

            Touch();
        }
    }

    public void CountRestart(string errorClass)
    {
        lock (_sync)
        {
            _restarts++;
            _lastError = errorClass;
            Touch();
        }
    }

    public void SetCloudConnected(bool connected)
    {
        lock (_sync)
        {
            _cloudConnected = connected;
            if (connected)
            {
                _sessionStartedAt = _time.GetUtcNow();
            }

            Touch();
        }
    }

    public void SetListening(bool listening, string mode, string indicator)
    {
        if (!DeviceVoiceContract.IndicatorStates.Contains(indicator))
        {
            throw new ArgumentException($"'{indicator}' is not an indicator state the contract names", nameof(indicator));
        }

        lock (_sync)
        {
            _listening = listening;
            _mode = mode;
            _indicator = indicator;
            Touch();
        }
    }

    public void SetMicrophone(bool present, bool running, bool? muted, bool digitalSilence)
    {
        lock (_sync)
        {
            _capturePresent = present;
            _captureRunning = running;
            _micMuted = muted;
            _digitalSilence = digitalSilence;
            Touch();
        }
    }

    public void SetEngine(bool wakeWordAvailable, string? wakeWordReason, IReadOnlyList<string> offlinePhrases, string? offlineReason, string? pushToTalkKey)
    {
        lock (_sync)
        {
            _wakeWordAvailable = wakeWordAvailable;
            _wakeWordReason = wakeWordReason;
            _offlinePhrases = offlinePhrases;
            _offlineReason = offlineReason;
            _pushToTalkKey = pushToTalkKey;
            Touch();
        }
    }

    public void RecordOfflineCommand(string commandId, bool executed, string detail)
    {
        lock (_sync)
        {
            if (executed)
            {
                _offlineExecuted++;
            }
            else
            {
                _offlineRefused++;
            }

            _lastOfflineCommand = new JsonObject
            {
                ["id"] = commandId,
                ["executed"] = executed,
                ["detail"] = detail,
                ["at"] = _time.GetUtcNow().ToString("O", CultureInfo.InvariantCulture),
            };
            Touch();
        }
    }

    public void CountUtterance()
    {
        lock (_sync)
        {
            _utterances++;
        }
    }

    public void CountFrames(long admitted, long dropped)
    {
        lock (_sync)
        {
            _framesAdmitted += admitted;
            _framesDropped += dropped;
        }
    }

    /// <summary>The heartbeat's <c>voice</c> object: exactly <see cref="DeviceVoiceContract.HeartbeatKeys"/>.</summary>
    public JsonObject Heartbeat()
    {
        lock (_sync)
        {
            return new JsonObject
            {
                ["state"] = _state,
                ["indicator"] = _indicator,
                ["mode"] = _mode,
                ["listening"] = _listening,
                ["mic_muted"] = _micMuted,
                ["cloud_connected"] = _cloudConnected,
                ["restarts"] = _restarts,
                ["last_error"] = _lastError,
            };
        }
    }

    /// <summary><c>desktop.voice_status</c>: the heartbeat keys plus what a qualification needs.</summary>
    public JsonObject Report()
    {
        var report = Heartbeat();
        lock (_sync)
        {
            report["microphone"] = new JsonObject
            {
                ["present"] = _capturePresent,
                ["capturing"] = _captureRunning,
                ["muted"] = _micMuted,
                ["digital_silence"] = _digitalSilence,
                ["capture_process"] = "session_companion",
            };
            report["wake_word"] = new JsonObject
            {
                ["available"] = _wakeWordAvailable,
                ["engine"] = DeviceVoiceContract.OfflineEngine,
                ["reason"] = _wakeWordReason,
            };
            report["offline_commands"] = new JsonObject
            {
                ["engine"] = DeviceVoiceContract.OfflineEngine,
                ["available"] = new JsonArray(_offlinePhrases.Select(p => (JsonNode)JsonValue.Create(p)).ToArray()),
                ["reason"] = _offlineReason,
                ["executed"] = _offlineExecuted,
                ["refused"] = _offlineRefused,
                ["last"] = _lastOfflineCommand?.DeepClone(),
            };
            report["push_to_talk_key"] = _pushToTalkKey;
            report["privacy"] = new JsonObject
            {
                ["preroll_max_ms"] = DeviceVoiceContract.MaxPreRollMs,
                ["segment_max_ms"] = DeviceVoiceContract.MaxSegmentMs,
                ["raw_audio_persisted"] = false,
                ["silence_leaves_device"] = false,
                ["remote_enable_allowed"] = DeviceVoiceContract.RemoteEnableAllowed,
            };
            report["counters"] = new JsonObject
            {
                ["utterances"] = _utterances,
                ["frames_admitted"] = _framesAdmitted,
                ["frames_dropped"] = _framesDropped,
            };
            report["started_at"] = _startedAt?.ToString("O", CultureInfo.InvariantCulture);
            report["session_started_at"] = _sessionStartedAt?.ToString("O", CultureInfo.InvariantCulture);
            report["updated_at"] = _updatedAt?.ToString("O", CultureInfo.InvariantCulture);
        }

        return report;
    }

    private void Touch() => _updatedAt = _time.GetUtcNow();
}
