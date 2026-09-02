namespace PagentOS.Companion.Audio.Audio;

public sealed record DeviceSelection(string DeviceId, string Reason);

/// <summary>
/// A change the orchestrator should apply. <see cref="Immediate"/> means the active device is
/// gone and there is nothing to wait for; otherwise the switch is a preference and should
/// land at a turn boundary so a sentence is not cut in half by a re-open.
/// </summary>
public sealed record DeviceSwitch(string? FromDeviceId, string ToDeviceId, string Reason, bool Immediate);

/// <summary>
/// Pure device policy for one direction. Order of preference:
/// the owner's explicit choice when present → the Windows default *communications*
/// endpoint (Windows itself moves this to a headset when one is plugged in) → a
/// headset-like endpoint → the default endpoint → the first endpoint. Re-evaluated on every
/// catalog change; a vanished active device is an immediate switch, a better candidate
/// appearing is a deferred one. No I/O, so the whole policy is unit-tested.
/// </summary>
public sealed class AudioDeviceSelector(AudioDirection direction, string? preferredDeviceId = null)
{
    public AudioDirection Direction { get; } = direction;

    public string? PreferredDeviceId { get; set; } = preferredDeviceId;

    public string? ActiveDeviceId { get; private set; }

    public void MarkActive(string deviceId) => ActiveDeviceId = deviceId;

    public DeviceSelection? Choose(IReadOnlyList<AudioDeviceInfo> devices)
    {
        var candidates = devices.Where(d => d.Direction == Direction).ToList();
        if (candidates.Count == 0)
        {
            return null;
        }

        if (PreferredDeviceId is not null)
        {
            var preferred = candidates.FirstOrDefault(d => d.Id == PreferredDeviceId);
            if (preferred is not null)
            {
                return new DeviceSelection(preferred.Id, "owner_preference");
            }
        }

        var communications = candidates.FirstOrDefault(d => d.IsDefaultCommunications);
        if (communications is not null)
        {
            return new DeviceSelection(communications.Id, "default_communications");
        }

        var headset = candidates.FirstOrDefault(d => d.IsHeadsetLike);
        if (headset is not null)
        {
            return new DeviceSelection(headset.Id, "headset");
        }

        var fallback = candidates.FirstOrDefault(d => d.IsDefault);
        if (fallback is not null)
        {
            return new DeviceSelection(fallback.Id, "default");
        }

        return new DeviceSelection(candidates[0].Id, "first_available");
    }

    public DeviceSwitch? Reconcile(IReadOnlyList<AudioDeviceInfo> devices)
    {
        var choice = Choose(devices);
        if (choice is null)
        {
            return null;
        }

        if (ActiveDeviceId is null)
        {
            return new DeviceSwitch(null, choice.DeviceId, choice.Reason, Immediate: true);
        }

        if (choice.DeviceId == ActiveDeviceId)
        {
            return null;
        }

        var activeStillPresent = devices.Any(d => d.Direction == Direction && d.Id == ActiveDeviceId);
        return new DeviceSwitch(
            ActiveDeviceId,
            choice.DeviceId,
            activeStillPresent ? choice.Reason : "active_device_removed",
            Immediate: !activeStillPresent);
    }
}
