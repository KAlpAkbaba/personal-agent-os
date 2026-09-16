using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Xml;

namespace PagentOS.SessionCompanion.Notify;

/// <summary>The surfaces a <c>desktop.notify</c> answer can name.</summary>
public static class ToastSurfaces
{
    /// <summary>A real Windows toast (<c>Windows.UI.Notifications</c>), with its buttons.</summary>
    public const string Toast = "toast";

    /// <summary>The tray balloon fallback. It has no buttons.</summary>
    public const string Balloon = "balloon";
}

/// <summary>
/// <c>ToastNotifier.Setting</c> as the tokens the answer carries. <see cref="Unknown"/> is
/// what a platform that could not be asked reports - never "enabled".
/// </summary>
public static class ToastNotifierSettings
{
    public const string Enabled = "enabled";
    public const string DisabledForApplication = "disabled_for_application";
    public const string DisabledForUser = "disabled_for_user";
    public const string DisabledByGroupPolicy = "disabled_by_group_policy";
    public const string DisabledByManifest = "disabled_by_manifest";
    public const string Unknown = "unknown";

    public static readonly IReadOnlyList<string> All =
    [
        Enabled, DisabledForApplication, DisabledForUser, DisabledByGroupPolicy, DisabledByManifest, Unknown,
    ];
}

/// <summary>
/// What a button press carries back into this process, parsed. A button's arguments are
/// DATA - an action id from the Cloud Core's closed vocabulary and the notification it
/// belongs to - and nothing in this process ever runs, opens or launches them.
/// </summary>
public sealed record ToastActivation(string NotificationId, string? ActionId);

/// <summary>
/// The toast XML for one <see cref="ToastRequest"/>, built by hand (the Community Toolkit's
/// <c>ToastContentBuilder</c> is a package this repository does not take).
/// </summary>
/// <remarks>
/// <para>
/// <strong>Every string reaches the document through <see cref="XmlWriter"/></strong>, so a
/// title such as <c>&lt;/text&gt;&lt;action …</c> is text on the screen and never an element.
/// Characters XML cannot carry at all (control characters, lone surrogates) are replaced by a
/// space rather than making the whole toast fail.
/// </para>
/// <para>
/// <strong>Buttons use <c>activationType="foreground"</c> and an <c>arguments</c> string of
/// the form <c>action=&lt;id&gt;;notification=&lt;id&gt;</c>.</strong> No
/// <c>protocol</c> activation, no URL, no launch path: Windows hands the string back to this
/// process's <c>Activated</c> handler and <see cref="ParseArguments"/> accepts that exact form
/// only. The toast body's own <c>launch</c> string carries the notification id alone.
/// </para>
/// <para>
/// <strong>Priority.</strong> <c>urgent</c> becomes the <c>reminder</c> scenario (the toast
/// stays until the owner acts; Windows needs at least one button for that, so a system
/// "dismiss" button is added, which Windows handles itself and never reports to this
/// process) and, on the notifier, high priority. The <c>alarm</c> scenario is not used: it
/// loops alarm audio, and ringing is the alarm subsystem's job, not a notification's.
/// <c>low</c> is silent; <c>normal</c> is Windows' default.
/// </para>
/// </remarks>
public static class ToastXml
{
    /// <summary>Windows' own limit on a tag and a group, in characters (Windows 10 1703+).</summary>
    public const int MaxTagChars = 64;

    /// <summary>Windows shows at most five actions; the contract spends three, the system dismiss one.</summary>
    public const int MaxWindowsActions = 5;

    /// <summary>The group every PagentOS toast is in, so a tag names one toast of ours.</summary>
    public const string Group = "pagentos";

    private const string ActionKey = "action=";
    private const string NotificationKey = "notification=";

    /// <summary>
    /// The tag a request's toast carries. With a <c>group_key</c> it is derived from the key, so
    /// a newer notification in the same group REPLACES the older toast, as the Cloud Core's
    /// grouping replaces the older unread row; without one it is the notification id, so a
    /// repeated send of one notification replaces itself instead of stacking.
    /// </summary>
    public static string Tag(ToastRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (!string.IsNullOrEmpty(request.GroupKey))
        {
            return "g-" + ShortHash(request.GroupKey);
        }

        return request.NotificationId.Length <= MaxTagChars
            ? request.NotificationId
            : "n-" + ShortHash(request.NotificationId);
    }

    /// <summary>Whether Windows should be asked for a high-priority toast.</summary>
    public static bool IsHighPriority(ToastRequest request) => request?.Priority == "urgent";

    /// <summary>The <c>arguments</c> string of one button.</summary>
    public static string ButtonArguments(string notificationId, string actionId)
        => ActionKey + actionId + ";" + NotificationKey + notificationId;

    /// <summary>The <c>launch</c> string of the toast body.</summary>
    public static string BodyArguments(string notificationId) => NotificationKey + notificationId;

    /// <summary>
    /// Reads an activation string back. Only the two exact shapes this class writes are
    /// accepted; anything else (a system activation, a string from an older build, a
    /// crafted one) is null.
    /// </summary>
    public static ToastActivation? ParseArguments(string? arguments)
    {
        if (string.IsNullOrEmpty(arguments) || arguments.Length > 256)
        {
            return null;
        }

        if (arguments.StartsWith(NotificationKey, StringComparison.Ordinal))
        {
            var id = arguments[NotificationKey.Length..];
            return IsNotificationToken(id) ? new ToastActivation(id, null) : null;
        }

        var parts = arguments.Split(';');
        if (parts.Length != 2
            || !parts[0].StartsWith(ActionKey, StringComparison.Ordinal)
            || !parts[1].StartsWith(NotificationKey, StringComparison.Ordinal))
        {
            return null;
        }

        var actionId = parts[0][ActionKey.Length..];
        var notificationId = parts[1][NotificationKey.Length..];
        if (!NotifyCapabilities.IsActionId(actionId) || !IsNotificationToken(notificationId))
        {
            return null;
        }

        return new ToastActivation(notificationId, actionId);
    }

    /// <summary>The toast document for <paramref name="request"/>.</summary>
    /// <exception cref="ArgumentException">A request the contract would refuse (the capability validates first; this is the second fence).</exception>
    public static string Build(ToastRequest request)
    {
        ArgumentNullException.ThrowIfNull(request);
        if (request.Actions.Count > NotifyCapabilities.MaxActions)
        {
            throw new ArgumentException($"at most {NotifyCapabilities.MaxActions} buttons", nameof(request));
        }

        foreach (var action in request.Actions)
        {
            if (!NotifyCapabilities.IsActionId(action.Id))
            {
                throw new ArgumentException($"action id '{action.Id}' is not [a-z0-9_]+", nameof(request));
            }

            if (string.IsNullOrWhiteSpace(action.Label) || action.Label.Length > NotifyCapabilities.MaxActionLabelChars)
            {
                throw new ArgumentException($"action '{action.Id}' label does not fit a button", nameof(request));
            }
        }

        if (!IsNotificationToken(request.NotificationId))
        {
            throw new ArgumentException("the notification id cannot ride in a button's arguments", nameof(request));
        }

        var urgent = IsHighPriority(request);
        var builder = new StringBuilder();
        var settings = new XmlWriterSettings
        {
            OmitXmlDeclaration = true,
            ConformanceLevel = ConformanceLevel.Document,
            Indent = false,
        };
        using (var writer = XmlWriter.Create(new StringWriter(builder, CultureInfo.InvariantCulture), settings))
        {
            writer.WriteStartElement("toast");
            writer.WriteAttributeString("launch", BodyArguments(request.NotificationId));
            if (urgent)
            {
                writer.WriteAttributeString("scenario", "reminder");
            }

            writer.WriteStartElement("visual");
            writer.WriteStartElement("binding");
            writer.WriteAttributeString("template", "ToastGeneric");

            writer.WriteStartElement("text");
            writer.WriteAttributeString("hint-maxLines", "1");
            writer.WriteString(Clean(request.Title));
            writer.WriteEndElement();

            writer.WriteStartElement("text");
            writer.WriteString(Clean(request.Body));
            writer.WriteEndElement();

            writer.WriteEndElement(); // binding
            writer.WriteEndElement(); // visual

            if (request.Actions.Count > 0 || urgent)
            {
                writer.WriteStartElement("actions");
                foreach (var action in request.Actions)
                {
                    writer.WriteStartElement("action");
                    writer.WriteAttributeString("content", Clean(action.Label));
                    writer.WriteAttributeString("arguments", ButtonArguments(request.NotificationId, action.Id));
                    writer.WriteAttributeString("activationType", "foreground");
                    writer.WriteEndElement();
                }

                if (urgent)
                {
                    // Handled by Windows itself: it closes the toast and tells this process
                    // nothing, so it can never be mistaken for one of the Cloud Core's actions.
                    writer.WriteStartElement("action");
                    writer.WriteAttributeString("activationType", "system");
                    writer.WriteAttributeString("arguments", "dismiss");
                    writer.WriteAttributeString("content", string.Empty);
                    writer.WriteEndElement();
                }

                writer.WriteEndElement(); // actions
            }

            if (request.Priority == "low")
            {
                writer.WriteStartElement("audio");
                writer.WriteAttributeString("silent", "true");
                writer.WriteEndElement();
            }

            writer.WriteEndElement(); // toast
        }

        return builder.ToString();
    }

    /// <summary>
    /// A notification id may ride in a button's arguments only if it cannot change their
    /// shape: letters, digits and hyphens (a uuid, or the offline voice path's own ids).
    /// </summary>
    internal static bool IsNotificationToken(string? value)
    {
        if (string.IsNullOrEmpty(value) || value.Length > MaxTagChars)
        {
            return false;
        }

        foreach (var ch in value)
        {
            if (!(char.IsAsciiLetterOrDigit(ch) || ch == '-'))
            {
                return false;
            }
        }

        return true;
    }

    /// <summary>Every character XML can carry, kept; every other one, a space.</summary>
    internal static string Clean(string text)
    {
        var builder = new StringBuilder(text.Length);
        for (var i = 0; i < text.Length; i++)
        {
            var ch = text[i];
            if (char.IsHighSurrogate(ch) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
            {
                builder.Append(ch).Append(text[i + 1]);
                i++;
            }
            else if (XmlConvert.IsXmlChar(ch))
            {
                builder.Append(ch);
            }
            else
            {
                builder.Append(' ');
            }
        }

        return builder.ToString();
    }

    private static string ShortHash(string value)
        => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(value)))[..32];
}
