using System.Drawing;
using System.Runtime.Versioning;
using System.Windows.Forms;
using Microsoft.Extensions.Logging;
using PagentOS.Companion.Audio.Listening;

namespace PagentOS.SessionCompanion.Voice;

/// <summary>What the owner can do from the indicator. Every action runs off the UI thread.</summary>
public sealed record TrayActions(
    Func<bool, Task> SetListening,
    Func<ListeningMode, Task<string?>> SetMode,
    Func<Task<string>> SnoozeAlarm,
    Func<Task<string>> StopAlarm);

/// <summary>
/// Row 254: the microphone's state, always visible while the voice service runs - a tray icon
/// whose colour and tooltip say off / muted / listening / waiting for the wake word / push-to-talk
/// / sending, and whose menu is the owner's switch (row 242), the mode, and a local "Ertele"
/// for a ringing alarm. The icon lives on its own STA thread with its own message loop, so the
/// listening loop never waits for the shell.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class TrayPrivacyIndicator : IPrivacyIndicator, IDisposable
{
    private readonly ILogger _logger;
    private readonly TrayActions _actions;
    private readonly ManualResetEventSlim _ready = new();
    private readonly Thread _thread;
    private readonly Dictionary<string, Icon> _icons = new(StringComparer.Ordinal);
    private Control? _marshal;
    private NotifyIcon? _icon;
    private ToolStripMenuItem? _listeningItem;
    private ToolStripMenuItem? _continuousItem;
    private ToolStripMenuItem? _wakeItem;
    private ToolStripMenuItem? _pttItem;
    private string _state = DeviceVoiceContract.IndicatorOff;

    public TrayPrivacyIndicator(ILogger logger, TrayActions actions)
    {
        _logger = logger;
        _actions = actions;
        _thread = new Thread(Run) { IsBackground = true, Name = "voice-privacy-indicator" };
        _thread.SetApartmentState(ApartmentState.STA);
        _thread.Start();
        _ready.Wait(TimeSpan.FromSeconds(10));
    }

    /// <summary>The Turkish label for a state; pure, so the words are tested.</summary>
    public static string LabelFor(string state) => state switch
    {
        DeviceVoiceContract.IndicatorOff => "Mikrofon kapalı",
        DeviceVoiceContract.IndicatorMuted => "Mikrofon donanımda susturuldu",
        DeviceVoiceContract.IndicatorListening => "Dinliyor",
        DeviceVoiceContract.IndicatorWakeWord => "Uyandırma sözcüğünü bekliyor",
        DeviceVoiceContract.IndicatorPushToTalk => "Bas-konuş",
        DeviceVoiceContract.IndicatorSending => "Konuşma gönderiliyor",
        _ => "Bilinmeyen durum",
    };

    public static Color ColourFor(string state) => state switch
    {
        DeviceVoiceContract.IndicatorSending => Color.FromArgb(220, 38, 38),
        DeviceVoiceContract.IndicatorListening => Color.FromArgb(22, 163, 74),
        DeviceVoiceContract.IndicatorWakeWord => Color.FromArgb(37, 99, 235),
        DeviceVoiceContract.IndicatorPushToTalk => Color.FromArgb(100, 116, 139),
        DeviceVoiceContract.IndicatorMuted => Color.FromArgb(234, 88, 12),
        _ => Color.FromArgb(156, 163, 175),
    };

    public void Show(string indicatorState, string detail)
    {
        var marshal = _marshal;
        if (marshal is null || marshal.IsDisposed)
        {
            return;
        }

        marshal.BeginInvoke(() =>
        {
            if (_icon is null)
            {
                return;
            }

            _state = indicatorState;
            _icon.Icon = IconFor(indicatorState);
            var text = "PagentOS · " + LabelFor(indicatorState) + " · " + detail;
            _icon.Text = text.Length <= 127 ? text : text[..127];
            _listeningItem!.Checked = indicatorState != DeviceVoiceContract.IndicatorOff;
        });
    }

    public void ShowMode(ListeningMode mode)
    {
        _marshal?.BeginInvoke(() =>
        {
            if (_continuousItem is null)
            {
                return;
            }

            _continuousItem.Checked = mode == ListeningMode.Continuous;
            _wakeItem!.Checked = mode == ListeningMode.WakeWord;
            _pttItem!.Checked = mode == ListeningMode.PushToTalk;
        });
    }

    public void Dispose()
    {
        var marshal = _marshal;
        if (marshal is not null && !marshal.IsDisposed)
        {
            try
            {
                marshal.Invoke(() =>
                {
                    if (_icon is not null)
                    {
                        _icon.Visible = false;
                        _icon.Dispose();
                        _icon = null;
                    }

                    Application.ExitThread();
                });
            }
            catch (Exception)
            {
                // The shell is already gone; nothing is left to hide.
            }
        }

        _thread.Join(TimeSpan.FromSeconds(2));
        foreach (var icon in _icons.Values)
        {
            icon.Dispose();
        }

        _ready.Dispose();
    }

    private void Run()
    {
        try
        {
            _marshal = new Control();
            _ = _marshal.Handle;
            var menu = new ContextMenuStrip();
            _listeningItem = new ToolStripMenuItem("Dinleme açık") { CheckOnClick = false };
            _listeningItem.Click += (_, _) => Fire(() => _actions.SetListening(_state == DeviceVoiceContract.IndicatorOff));
            var modes = new ToolStripMenuItem("Dinleme biçimi");
            _continuousItem = new ToolStripMenuItem("Sürekli (yerel konuşma algılama)");
            _wakeItem = new ToolStripMenuItem("Uyandırma sözcüğü");
            _pttItem = new ToolStripMenuItem("Bas-konuş (sağ Ctrl)");
            _continuousItem.Click += (_, _) => FireMode(ListeningMode.Continuous);
            _wakeItem.Click += (_, _) => FireMode(ListeningMode.WakeWord);
            _pttItem.Click += (_, _) => FireMode(ListeningMode.PushToTalk);
            modes.DropDownItems.AddRange([_continuousItem, _wakeItem, _pttItem]);
            var snooze = new ToolStripMenuItem("Çalan alarmı ertele");
            snooze.Click += (_, _) => Fire(async () =>
            {
                var detail = await _actions.SnoozeAlarm().ConfigureAwait(false);
                Balloon("Alarm", detail);
            });
            var stop = new ToolStripMenuItem("Çalan alarmı durdur");
            stop.Click += (_, _) => Fire(async () =>
            {
                var detail = await _actions.StopAlarm().ConfigureAwait(false);
                Balloon("Alarm", detail);
            });
            menu.Items.AddRange([_listeningItem, modes, new ToolStripSeparator(), snooze, stop]);
            _icon = new NotifyIcon
            {
                Icon = IconFor(DeviceVoiceContract.IndicatorOff),
                Text = "PagentOS · " + LabelFor(DeviceVoiceContract.IndicatorOff),
                ContextMenuStrip = menu,
                Visible = true,
            };
            _ready.Set();
            Application.Run();
        }
        catch (Exception ex)
        {
            _logger.LogWarning(ex, "voice: the privacy indicator could not be created; the state is still in the log and in desktop.voice_status");
            _ready.Set();
        }
    }

    private void FireMode(ListeningMode mode) => Fire(async () =>
    {
        var refused = await _actions.SetMode(mode).ConfigureAwait(false);
        if (refused is null)
        {
            ShowMode(mode);
        }
        else
        {
            Balloon("Dinleme biçimi değişmedi", refused);
        }
    });

    private void Balloon(string title, string text)
    {
        _marshal?.BeginInvoke(() => _icon?.ShowBalloonTip(5000, title, text, ToolTipIcon.Info));
    }

    private void Fire(Func<Task> action)
    {
        _ = Task.Run(async () =>
        {
            try
            {
                await action().ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                _logger.LogWarning("voice: indicator action failed: {Reason}", ex.Message);
            }
        });
    }

    private Icon IconFor(string state)
    {
        if (_icons.TryGetValue(state, out var cached))
        {
            return cached;
        }

        using var bitmap = new Bitmap(16, 16);
        using (var graphics = Graphics.FromImage(bitmap))
        {
            graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            graphics.Clear(Color.Transparent);
            using var brush = new SolidBrush(ColourFor(state));
            graphics.FillEllipse(brush, 1, 1, 14, 14);
            if (state is DeviceVoiceContract.IndicatorOff or DeviceVoiceContract.IndicatorMuted)
            {
                using var pen = new Pen(Color.White, 2);
                graphics.DrawLine(pen, 4, 12, 12, 4);
            }
        }

        var icon = Icon.FromHandle(bitmap.GetHicon());
        _icons[state] = icon;
        return icon;
    }
}
