using System.Runtime.Versioning;
using PagentOS.Companion.Audio.Listening;

namespace PagentOS.Companion.Audio.Wasapi;

/// <summary>
/// Row 255: the capture endpoint's own mute state (<c>IAudioEndpointVolume::GetMute</c>), plus
/// a master level of exactly zero, which several laptop mic-mute keys set instead of the mute
/// flag. Reading it opens no stream. Anything that cannot be read is null - "cannot tell" -
/// and never "not muted".
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WasapiMuteMonitor(WasapiDeviceCatalog catalog) : IMicMuteMonitor
{
    public bool? IsMuted(string deviceId)
    {
        try
        {
            using var device = catalog.Open(deviceId);
            var volume = device.AudioEndpointVolume;
            return volume.Mute || volume.MasterVolumeLevelScalar <= 0f;
        }
        catch (Exception)
        {
            return null;
        }
    }
}
