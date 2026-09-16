using System.Runtime.Versioning;
using System.Windows.Forms;
using Microsoft.Extensions.Logging;
using Microsoft.Win32;
using NAudio.CoreAudioApi;

namespace PagentOS.SessionCompanion.Camera;

/// <summary>
/// Row 671: Windows' own camera permission, READ before anything is opened. Three switches can
/// deny a desktop application the camera - the device-wide one (HKLM), the per-user "let apps
/// use my camera" one, and the per-user "let desktop apps use my camera" one. Any of them set to
/// <c>Deny</c> is reported by name; nothing here writes the registry.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class WindowsCameraConsent : ICameraConsent
{
    private const string Store = @"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam";

    public string? DeniedBy()
    {
        if (IsDenied(Registry.LocalMachine, Store))
        {
            return "privacy_device_off";
        }

        if (IsDenied(Registry.CurrentUser, Store))
        {
            return "privacy_apps_off";
        }

        return IsDenied(Registry.CurrentUser, Store + @"\NonPackaged") ? "privacy_desktop_apps_off" : null;
    }

    private static bool IsDenied(RegistryKey hive, string path)
    {
        using var key = hive.OpenSubKey(path, writable: false);
        return string.Equals(key?.GetValue("Value") as string, "Deny", StringComparison.OrdinalIgnoreCase);
    }
}

/// <summary>
/// Whether sound is playing on the default render endpoint, from its peak meter - a READ of a
/// level, never a change to one. A film is not sleep (ADR-0155 decision 1): while sound plays,
/// the camera path never reports a resting posture.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class RenderPeakMediaProbe(double threshold) : IMediaActivityProbe
{
    public bool? IsPlaying()
    {
        try
        {
            using var enumerator = new MMDeviceEnumerator();
            if (!enumerator.HasDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia))
            {
                return null;
            }

            using var device = enumerator.GetDefaultAudioEndpoint(DataFlow.Render, Role.Multimedia);
            var peak = 0f;
            for (var i = 0; i < 3; i++)
            {
                peak = Math.Max(peak, device.AudioMeterInformation.MasterPeakValue);
                Thread.Sleep(40);
            }

            return peak > threshold;
        }
        catch (Exception)
        {
            return null;
        }
    }
}

/// <summary>
/// The owner-visible camera indicator: a tray icon on its own STA thread with a message loop.
/// Armed (a mode is on, the camera closed) and Open (the camera is open right now) have
/// different icons and words; its one menu item closes the camera on this device - which
/// outranks the cloud's mode until the owner re-allows it from the same menu.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class TrayCameraIndicator : ICameraIndicator, IDisposable
{
    private readonly ILogger _logger;
    private readonly Thread _thread;
    private readonly ManualResetEventSlim _ready = new();
    private readonly object _sync = new();
    private NotifyIcon? _icon;
    private ToolStripMenuItem? _toggle;
    private Control? _marshal;
    private bool _vetoed;

    public TrayCameraIndicator(ILogger logger)
    {
        _logger = logger;
        _thread = new Thread(Run) { IsBackground = true, Name = "camera-indicator" };
        _thread.SetApartmentState(ApartmentState.STA);
        _thread.Start();
        _ready.Wait(TimeSpan.FromSeconds(5));
    }

    public CameraIndicatorState State { get; private set; } = CameraIndicatorState.Hidden;

    public event Action<bool>? OwnerVeto;

    public void Set(CameraIndicatorState state, string mode)
    {
        lock (_sync)
        {
            State = state;
        }

        var marshal = _marshal;
        if (marshal is null || !marshal.IsHandleCreated)
        {
            return;
        }

        try
        {
            // Synchronous on purpose: when this returns, the owner can see the new state. The
            // monitor raises Open BEFORE opening the device, so "before" must mean before.
            marshal.Invoke(() => Apply(state, mode));
        }
        catch (Exception ex)
        {
            _logger.LogWarning("camera indicator update failed: {Reason}", ex.GetType().Name);
        }
    }

    public void Dispose()
    {
        var marshal = _marshal;
        if (marshal is not null && marshal.IsHandleCreated)
        {
            try
            {
                marshal.Invoke(() =>
                {
                    if (_icon is not null)
                    {
                        _icon.Visible = false;
                        _icon.Dispose();
                    }

                    Application.ExitThread();
                });
            }
            catch (Exception)
            {
                // The loop is already gone.
            }
        }

        _ready.Dispose();
    }

    private void Run()
    {
        try
        {
            _marshal = new Control();
            _ = _marshal.Handle;
            _toggle = new ToolStripMenuItem("Kamerayı bu cihazda kapat");
            _toggle.Click += (_, _) =>
            {
                _vetoed = !_vetoed;
                _toggle.Text = _vetoed ? "Kameraya yeniden izin ver" : "Kamerayı bu cihazda kapat";
                OwnerVeto?.Invoke(_vetoed);
            };
            var menu = new ContextMenuStrip();
            menu.Items.Add(_toggle);
            _icon = new NotifyIcon { Visible = false, ContextMenuStrip = menu };
        }
        catch (Exception ex)
        {
            _logger.LogWarning("camera indicator could not start: {Reason}", ex.GetType().Name);
        }
        finally
        {
            _ready.Set();
        }

        Application.Run();
    }

    private void Apply(CameraIndicatorState state, string mode)
    {
        if (_icon is null)
        {
            return;
        }

        if (state == CameraIndicatorState.Hidden)
        {
            _icon.Visible = false;
            return;
        }

        var modeText = mode == CameraModes.Continuous ? "sürekli izleme" : "periyodik kontrol";
        _icon.Icon = state == CameraIndicatorState.Open
            ? System.Drawing.SystemIcons.Warning
            : System.Drawing.SystemIcons.Shield;
        _icon.Text = state == CameraIndicatorState.Open
            ? $"PagentOS kamerası AÇIK ({modeText})"
            : $"PagentOS kamera: {modeText} (şu an kapalı)";
        _icon.Visible = true;
    }
}
