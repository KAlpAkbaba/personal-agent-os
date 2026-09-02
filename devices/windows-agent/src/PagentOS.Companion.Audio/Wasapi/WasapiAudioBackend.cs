using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Microsoft.Extensions.Logging;
using NAudio.CoreAudioApi;
using NAudio.CoreAudioApi.Interfaces;
using NAudio.Wave;
using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Orchestration;

namespace PagentOS.Companion.Audio.Wasapi;

/// <summary>
/// WASAPI endpoint enumeration through NAudio's MMDevice API, with change notifications.
/// Runs in the owner's interactive session only (the Session-0 service never loads this
/// assembly's Wasapi types). Enumeration is not capture: listing devices touches no stream.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WasapiDeviceCatalog : IAudioDeviceCatalog, IDisposable
{
    private readonly MMDeviceEnumerator _enumerator = new();
    private readonly NotificationClient _notifications;

    public WasapiDeviceCatalog()
    {
        _notifications = new NotificationClient(() => DevicesChanged?.Invoke());
        _enumerator.RegisterEndpointNotificationCallback(_notifications);
    }

    public event Action? DevicesChanged;

    public IReadOnlyList<AudioDeviceInfo> List(AudioDirection direction)
    {
        var flow = direction == AudioDirection.Capture ? DataFlow.Capture : DataFlow.Render;
        var defaultId = DefaultId(flow, Role.Multimedia);
        var communicationsId = DefaultId(flow, Role.Communications);
        var result = new List<AudioDeviceInfo>();
        foreach (var device in _enumerator.EnumerateAudioEndPoints(flow, DeviceState.Active))
        {
            using (device)
            {
                var name = SafeName(device);
                result.Add(new AudioDeviceInfo(
                    device.ID,
                    name,
                    direction,
                    FormFactor(device, name),
                    device.ID == defaultId,
                    device.ID == communicationsId));
            }
        }

        return result;
    }

    public MMDevice Open(string deviceId) => _enumerator.GetDevice(deviceId);

    public void Dispose()
    {
        try
        {
            _enumerator.UnregisterEndpointNotificationCallback(_notifications);
        }
        catch (Exception)
        {
        }

        _enumerator.Dispose();
    }

    private string? DefaultId(DataFlow flow, Role role)
    {
        try
        {
            if (!_enumerator.HasDefaultAudioEndpoint(flow, role))
            {
                return null;
            }

            using var device = _enumerator.GetDefaultAudioEndpoint(flow, role);
            return device.ID;
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static string SafeName(MMDevice device)
    {
        try
        {
            return device.FriendlyName;
        }
        catch (Exception)
        {
            return device.ID;
        }
    }

    private static AudioFormFactor FormFactor(MMDevice device, string name)
    {
        try
        {
            if (device.Properties.Contains(PropertyKeys.PKEY_AudioEndpoint_FormFactor))
            {
                var raw = device.Properties[PropertyKeys.PKEY_AudioEndpoint_FormFactor].Value;
                var factor = Convert.ToUInt32(raw) switch
                {
                    1 => AudioFormFactor.Speakers,      // Speakers
                    2 => AudioFormFactor.Speakers,      // LineLevel
                    3 => AudioFormFactor.Headphones,    // Headphones
                    4 => AudioFormFactor.Microphone,    // Microphone
                    5 => AudioFormFactor.Headset,       // Headset
                    6 => AudioFormFactor.Handset,       // Handset
                    _ => AudioFormFactor.Unknown,
                };
                if (factor != AudioFormFactor.Unknown)
                {
                    return factor;
                }
            }
        }
        catch (Exception)
        {
            // Fall through to the name heuristic.
        }

        return AudioFormFactorHeuristics.FromName(name);
    }

    private sealed class NotificationClient(Action changed) : IMMNotificationClient
    {
        public void OnDeviceStateChanged(string deviceId, DeviceState newState) => changed();

        public void OnDeviceAdded(string pwstrDeviceId) => changed();

        public void OnDeviceRemoved(string deviceId) => changed();

        public void OnDefaultDeviceChanged(DataFlow flow, Role role, string defaultDeviceId) => changed();

        public void OnPropertyValueChanged(string pwstrDeviceId, PropertyKey key)
        {
        }
    }
}

[SupportedOSPlatform("windows")]
public sealed class WasapiDeviceFactory(WasapiDeviceCatalog catalog, ILogger? logger = null, int frameMs = 20, int playbackLatencyMs = 50) : IAudioDeviceFactory
{
    public IAudioCapture OpenCapture(string deviceId, AudioFormat format)
        => new WasapiCaptureDevice(catalog.Open(deviceId), format, frameMs, logger);

    public IAudioPlayback OpenPlayback(string deviceId, AudioFormat format)
        => new WasapiPlaybackDevice(catalog.Open(deviceId), format, playbackLatencyMs);
}

/// <summary>A raw shared-mode capture stream delivering device-format bytes.</summary>
internal interface IRawCapture : IDisposable
{
    WaveFormat WaveFormat { get; }

    event Action<byte[], int>? DataAvailable;

    void Start();

    void Stop();
}

/// <summary>
/// Shared-mode capture at the engine's mix format, converted to the client's PCM16 mono
/// shape. Two ways of opening the stream, tried in order:
/// 1. <see cref="CommunicationsCategoryCapture"/> — <c>IAudioClient2::SetClientProperties</c>
///    with the <c>Communications</c> category before initialisation. That is the one lever
///    Windows 10 (19045) offers a client for echo cancellation and noise suppression: the
///    driver's own voice-processing APOs (present on most laptop microphone arrays) engage
///    for communications streams. Real, but device dependent.
/// 2. NAudio's plain <see cref="WasapiCapture"/>, used when the category path throws or —
///    guarded by a watchdog — opens but delivers nothing within two seconds.
/// <see cref="DriverApoActive"/> says which path is running; the audit row records it.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WasapiCaptureDevice : IAudioCapture, IReportsDriverProcessing
{
    /// <summary>KSDATAFORMAT_SUBTYPE_IEEE_FLOAT — the engine's usual shared-mode mix format.</summary>
    private static readonly Guid SubtypeIeeeFloat = new("00000003-0000-0010-8000-00aa00389b71");

    private const int WatchdogMs = 2000;

    private readonly MMDevice _device;
    private readonly ILogger? _logger;
    private readonly int _frameMs;
    private readonly TimeProvider _time = TimeProvider.System;
    private readonly object _sync = new();
    private IRawCapture? _capture;
    private PcmConverter? _converter;
    private MemoryStream _pending = new();
    private Timer? _watchdog;
    private long _framesDelivered;

    public WasapiCaptureDevice(MMDevice device, AudioFormat format, int frameMs, ILogger? logger)
    {
        _device = device;
        _logger = logger;
        _frameMs = frameMs;
        Format = format;
        DeviceId = device.ID;
    }

    public string DeviceId { get; }

    public AudioFormat Format { get; }

    public bool DriverApoActive { get; private set; }

    public event Action<AudioFrame>? FrameCaptured;

    public void Start()
    {
        lock (_sync)
        {
            if (_capture is not null)
            {
                return;
            }

            if (!TryStart(communications: true))
            {
                TryStart(communications: false);
            }
        }
    }

    public void Stop()
    {
        IRawCapture? capture;
        lock (_sync)
        {
            capture = _capture;
            _capture = null;
            _watchdog?.Dispose();
            _watchdog = null;
        }

        if (capture is null)
        {
            return;
        }

        capture.DataAvailable -= OnDataAvailable;
        try
        {
            capture.Stop();
        }
        finally
        {
            capture.Dispose();
        }
    }

    public void Dispose()
    {
        Stop();
        _device.Dispose();
        _pending.Dispose();
    }

    private bool TryStart(bool communications)
    {
        IRawCapture? capture = null;
        try
        {
            capture = communications
                ? new CommunicationsCategoryCapture(DeviceId, _frameMs)
                : new PlainCapture(_device, _frameMs);
            var mix = capture.WaveFormat;
            _converter = new PcmConverter(
                mix.SampleRate,
                mix.Channels,
                mix.Encoding == WaveFormatEncoding.IeeeFloat || (mix is WaveFormatExtensible ext && ext.SubFormat == SubtypeIeeeFloat),
                Format);
            _pending = new MemoryStream();
            capture.DataAvailable += OnDataAvailable;
            capture.Start();
            _capture = capture;
            DriverApoActive = communications;
            Interlocked.Exchange(ref _framesDelivered, 0);
            if (communications)
            {
                _watchdog = new Timer(_ => Watchdog(), null, WatchdogMs, Timeout.Infinite);
            }

            _logger?.LogInformation(
                "capture started on {Device}: path={Path} mix={Rate}Hz/{Channels}ch/{Encoding} -> {Target}Hz mono pcm16",
                DeviceId, communications ? "communications_category" : "plain", mix.SampleRate, mix.Channels, mix.Encoding, Format.SampleRate);
            return true;
        }
        catch (Exception ex)
        {
            _logger?.LogInformation(
                "{Path} capture unavailable on {Device} ({Reason})",
                communications ? "communications-category" : "plain", DeviceId, ex.Message);
            if (capture is not null)
            {
                capture.DataAvailable -= OnDataAvailable;
                capture.Dispose();
            }

            DriverApoActive = false;
            return false;
        }
    }

    private void Watchdog()
    {
        if (Interlocked.Read(ref _framesDelivered) > 0)
        {
            return;
        }

        _logger?.LogWarning("communications-category capture on {Device} delivered no audio in {Ms} ms; falling back to plain capture", DeviceId, WatchdogMs);
        lock (_sync)
        {
            var stale = _capture;
            _capture = null;
            _watchdog?.Dispose();
            _watchdog = null;
            if (stale is not null)
            {
                stale.DataAvailable -= OnDataAvailable;
                try
                {
                    stale.Stop();
                }
                catch (Exception)
                {
                }

                stale.Dispose();
            }

            TryStart(communications: false);
        }
    }

    private void OnDataAvailable(byte[] buffer, int count)
    {
        var converter = _converter;
        if (converter is null || count == 0)
        {
            return;
        }

        var capturedAt = _time.GetTimestamp();
        var pcm = converter.Convert(buffer.AsSpan(0, count));
        _pending.Write(pcm, 0, pcm.Length);

        var frameBytes = Format.BytesForMs(_frameMs);
        while (_pending.Length >= frameBytes)
        {
            var all = _pending.GetBuffer();
            var frame = new byte[frameBytes];
            Buffer.BlockCopy(all, 0, frame, 0, frameBytes);
            var remaining = (int)_pending.Length - frameBytes;
            var rest = new MemoryStream();
            rest.Write(all, frameBytes, remaining);
            _pending.Dispose();
            _pending = rest;
            Interlocked.Increment(ref _framesDelivered);
            FrameCaptured?.Invoke(new AudioFrame(Format, frame, capturedAt));
        }
    }

    private sealed class PlainCapture : IRawCapture
    {
        private readonly WasapiCapture _capture;

        public PlainCapture(MMDevice device, int frameMs)
        {
            _capture = new WasapiCapture(device, useEventSync: true, audioBufferMillisecondsLength: frameMs);
            _capture.DataAvailable += (_, e) => DataAvailable?.Invoke(e.Buffer, e.BytesRecorded);
        }

        public WaveFormat WaveFormat => _capture.WaveFormat;

        public event Action<byte[], int>? DataAvailable;

        public void Start() => _capture.StartRecording();

        public void Stop() => _capture.StopRecording();

        public void Dispose() => _capture.Dispose();
    }

    /// <summary>
    /// Event-driven shared-mode capture on an <c>IAudioClient2</c> whose stream category was
    /// set to Communications before <c>Initialize</c>. NAudio's <see cref="WasapiCapture"/>
    /// activates its own client and offers no hook before initialisation, so the activation
    /// is done here through the endpoint's <c>IMMDevice</c>, and NAudio's public
    /// <see cref="AudioClient"/> wrapper is then used for everything else.
    /// </summary>
    private sealed class CommunicationsCategoryCapture : IRawCapture
    {
        private const int BufferMs = 100;
        private readonly AudioClient _client;
        private readonly AudioCaptureClient _captureClient;
        private readonly EventWaitHandle _event = new(false, EventResetMode.AutoReset);
        private Thread? _thread;
        private volatile bool _running;

        public CommunicationsCategoryCapture(string deviceId, int frameMs)
        {
            _client = ComActivation.ActivateCommunicationsAudioClient(deviceId);
            WaveFormat = _client.MixFormat;
            _client.Initialize(
                AudioClientShareMode.Shared,
                AudioClientStreamFlags.EventCallback,
                Math.Max(BufferMs, frameMs * 2) * 10_000L,
                0,
                WaveFormat,
                Guid.Empty);
            _client.SetEventHandle(_event.SafeWaitHandle.DangerousGetHandle());
            _captureClient = _client.AudioCaptureClient;
        }

        public WaveFormat WaveFormat { get; }

        public event Action<byte[], int>? DataAvailable;

        public void Start()
        {
            _running = true;
            _client.Start();
            _thread = new Thread(Loop) { IsBackground = true, Name = "pagentos-voice-capture" };
            _thread.Start();
        }

        public void Stop()
        {
            _running = false;
            _event.Set();
            _thread?.Join(500);
            _client.Stop();
        }

        public void Dispose()
        {
            _running = false;
            _captureClient.Dispose();
            _client.Dispose();
            _event.Dispose();
        }

        private void Loop()
        {
            var blockAlign = WaveFormat.BlockAlign;
            while (_running)
            {
                if (!_event.WaitOne(BufferMs))
                {
                    continue;
                }

                while (_running && _captureClient.GetNextPacketSize() > 0)
                {
                    var pointer = _captureClient.GetBuffer(out var frames, out var flags);
                    var bytes = frames * blockAlign;
                    var buffer = new byte[bytes];
                    if ((flags & AudioClientBufferFlags.Silent) == 0)
                    {
                        Marshal.Copy(pointer, buffer, 0, bytes);
                    }

                    _captureClient.ReleaseBuffer(frames);
                    DataAvailable?.Invoke(buffer, bytes);
                }
            }
        }
    }
}

/// <summary>Just enough of the MMDevice COM surface to activate an interface NAudio's wrapper does not expose pre-initialisation.</summary>
[SupportedOSPlatform("windows")]
internal static class ComActivation
{
    private const int ClsCtxAll = 0x17;
    private static readonly Guid IidAudioClient2 = new("726778CD-F60A-4EDA-82DE-E47610CD78AA");

    public static AudioClient ActivateCommunicationsAudioClient(string deviceId)
    {
        var enumerator = (IMMDeviceEnumeratorCom)new MMDeviceEnumeratorCom();
        Marshal.ThrowExceptionForHR(enumerator.GetDevice(deviceId, out var device));
        var iid = IidAudioClient2;
        Marshal.ThrowExceptionForHR(device.Activate(ref iid, ClsCtxAll, IntPtr.Zero, out var activated));

        var client2 = (IAudioClient2)activated;
        var properties = new AudioClientProperties
        {
            cbSize = (uint)Marshal.SizeOf<AudioClientProperties>(),
            bIsOffload = 0,
            eCategory = AudioStreamCategory.Communications,
            Options = AudioClientStreamOptions.None,
        };
        var pointer = Marshal.AllocHGlobal(Marshal.SizeOf<AudioClientProperties>());
        try
        {
            Marshal.StructureToPtr(properties, pointer, fDeleteOld: false);
            client2.SetClientProperties(pointer);
        }
        finally
        {
            Marshal.FreeHGlobal(pointer);
        }

        return new AudioClient((IAudioClient)activated);
    }

    [ComImport]
    [Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
    private class MMDeviceEnumeratorCom
    {
    }

    [ComImport]
    [Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceEnumeratorCom
    {
        [PreserveSig]
        int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);

        [PreserveSig]
        int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDeviceCom device);

        [PreserveSig]
        int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDeviceCom device);

        [PreserveSig]
        int RegisterEndpointNotificationCallback(IntPtr client);

        [PreserveSig]
        int UnregisterEndpointNotificationCallback(IntPtr client);
    }

    [ComImport]
    [Guid("D666063F-1587-4E43-81F1-B948E807363F")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    private interface IMMDeviceCom
    {
        [PreserveSig]
        int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, [MarshalAs(UnmanagedType.IUnknown)] out object activated);

        [PreserveSig]
        int OpenPropertyStore(int access, out IntPtr properties);

        [PreserveSig]
        int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);

        [PreserveSig]
        int GetState(out int state);
    }
}

/// <summary>
/// Shared-mode render through a buffered provider. <see cref="StopImmediately"/> clears the
/// queue and lets the engine read silence, so the only audio still to be heard is the device
/// period (<c>latency</c>, 50 ms by default) — that residual is reported, not hidden. The
/// stream is kept open between responses so the next first-audio does not pay a start cost.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WasapiPlaybackDevice : IAudioPlayback
{
    private readonly MMDevice _device;
    private readonly int _latencyMs;
    private readonly BufferedWaveProvider _buffer;
    private readonly object _sync = new();
    private WasapiOut? _output;
    private Timer? _drainTimer;
    private bool _playedSomething;
    private bool _drainedRaised = true;

    public WasapiPlaybackDevice(MMDevice device, AudioFormat format, int latencyMs)
    {
        _device = device;
        _latencyMs = latencyMs;
        Format = format;
        DeviceId = device.ID;
        _buffer = new BufferedWaveProvider(new WaveFormat(format.SampleRate, AudioFormat.BitsPerSample, format.Channels))
        {
            ReadFully = true,
            DiscardOnBufferOverflow = false,
            BufferDuration = TimeSpan.FromSeconds(60),
        };
    }

    public string DeviceId { get; }

    public AudioFormat Format { get; }

    public bool IsPlaying => _output?.PlaybackState == PlaybackState.Playing && _buffer.BufferedBytes > 0;

    public int QueuedMs => (int)Format.MsForBytes(_buffer.BufferedBytes);

    public event Action? Drained;

    public void Start()
    {
        lock (_sync)
        {
            if (_output is not null)
            {
                return;
            }

            _output = new WasapiOut(_device, AudioClientShareMode.Shared, useEventSync: true, latency: _latencyMs);
            _output.Init(_buffer);
            _output.Play();
            _drainTimer = new Timer(_ => CheckDrained(), null, 30, 30);
        }
    }

    public void Enqueue(ReadOnlySpan<byte> pcm16)
    {
        var bytes = pcm16.ToArray();
        _buffer.AddSamples(bytes, 0, bytes.Length);
        _playedSomething = true;
        _drainedRaised = false;
    }

    public PlaybackStopReport StopImmediately()
    {
        var discarded = QueuedMs;
        _buffer.ClearBuffer();
        _drainedRaised = true;
        return new PlaybackStopReport(discarded, _latencyMs);
    }

    public void Dispose()
    {
        lock (_sync)
        {
            _drainTimer?.Dispose();
            _output?.Stop();
            _output?.Dispose();
            _output = null;
        }

        _device.Dispose();
    }

    private void CheckDrained()
    {
        if (_playedSomething && !_drainedRaised && _buffer.BufferedBytes == 0)
        {
            _drainedRaised = true;
            Drained?.Invoke();
        }
    }
}
