namespace PagentOS.Companion.Audio.Audio;

/// <summary>
/// WASAPI exposes a form factor for many endpoints but not all; when it does not, the
/// friendly name is the only hint. This is a heuristic and is labelled as one: a wrong
/// guess costs a slightly more conservative barge-in threshold, never a wrong device.
/// Turkish Windows names are included because that is the owner's locale.
/// </summary>
public static class AudioFormFactorHeuristics
{
    private static readonly (string Needle, AudioFormFactor Factor)[] Rules =
    {
        ("headset", AudioFormFactor.Headset),
        ("kulaklık", AudioFormFactor.Headset),
        ("hands-free", AudioFormFactor.Handset),
        ("handsfree", AudioFormFactor.Handset),
        ("headphone", AudioFormFactor.Headphones),
        ("earphone", AudioFormFactor.Headphones),
        ("earbud", AudioFormFactor.Headphones),
        ("airpods", AudioFormFactor.Bluetooth),
        ("bluetooth", AudioFormFactor.Bluetooth),
        ("bt ", AudioFormFactor.Bluetooth),
        ("usb", AudioFormFactor.Usb),
        ("microphone array", AudioFormFactor.BuiltIn),
        ("mikrofon dizisi", AudioFormFactor.BuiltIn),
        ("internal", AudioFormFactor.BuiltIn),
        ("dahili", AudioFormFactor.BuiltIn),
        ("speaker", AudioFormFactor.Speakers),
        ("hoparlör", AudioFormFactor.Speakers),
        ("microphone", AudioFormFactor.Microphone),
        ("mikrofon", AudioFormFactor.Microphone),
    };

    public static AudioFormFactor FromName(string? friendlyName)
    {
        if (string.IsNullOrWhiteSpace(friendlyName))
        {
            return AudioFormFactor.Unknown;
        }

        var folded = Turn.TurkishText.Fold(friendlyName);
        foreach (var (needle, factor) in Rules)
        {
            if (folded.Contains(needle, StringComparison.Ordinal))
            {
                return factor;
            }
        }

        return AudioFormFactor.Unknown;
    }
}
