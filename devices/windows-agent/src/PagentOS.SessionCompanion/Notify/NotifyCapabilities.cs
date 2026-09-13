using System.Runtime.Versioning;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Microsoft.Extensions.Logging;

namespace PagentOS.SessionCompanion.Notify;

/// <summary>
/// <c>desktop.notify</c> — the one channel that reaches the owner with the browser closed
/// and the screen locked (B11 requirement 369/370).
/// </summary>
/// <remarks>
/// <para>
/// Only this process runs in the owner's interactive session, so only this process can put
/// something on their screen. The Cloud Core cannot, and the contract exists so it does not
/// pretend to: it sends the shape in <c>packages/protocol/desktop-notify.json</c> and this
/// answers whether a toast was <em>actually shown</em>.
/// </para>
/// <para>
/// <c>shown: false</c> is a real answer, not a failure to report. No interactive session,
/// notifications turned off by the owner in Windows settings, or no shell to ask — each has
/// its own reason, because the owner's remedy differs, and the Cloud Core steps down its
/// fallback ladder rather than recording a delivery that did not happen.
/// </para>
/// <para>
/// The limits below are the contract's own numbers. <c>DesktopNotifyContractTests</c> reads
/// the shared JSON and fails if this table drifts from it — the four capabilities that were
/// not written as a contract first each shipped with both halves' suites green and neither
/// half able to talk to the other.
/// </para>
/// </remarks>
[SupportedOSPlatform("windows")]
public sealed class NotifyCapabilities
{
    /// <summary>Windows truncates a toast title past roughly this.</summary>
    public const int MaxTitleChars = 64;

    /// <summary>Two lines of toast text. Longer belongs in the inbox, which has the whole thing.</summary>
    public const int MaxBodyChars = 256;

    /// <summary>Windows' own limit. Asking for more silently drops the extras.</summary>
    public const int MaxActions = 3;

    public const int MaxActionIdChars = 32;
    public const int MaxActionLabelChars = 24;
    public const int MaxGroupKeyChars = 128;

    public const string ReasonNoInteractiveSession = "no_interactive_session";
    public const string ReasonNotificationsDisabled = "notifications_disabled";
    public const string ReasonShellUnavailable = "shell_unavailable";
    public const string ReasonInvalidPayload = "invalid_payload";

    private static readonly Regex ActionId = new("^[a-z0-9_]+$", RegexOptions.Compiled);

    private readonly IToastSink _sink;
    private readonly ILogger _logger;

    public NotifyCapabilities(IToastSink sink, ILogger logger)
    {
        _sink = sink;
        _logger = logger;
    }

    /// <summary>Raise a toast. Never throws: an unreachable shell is an answer, not a fault.</summary>
    public JsonObject Notify(JsonObject? payload)
    {
        if (!TryParse(payload, out var request, out var problem))
        {
            // Refused here rather than rendered badly: what the owner reads is the Cloud
            // Core's decision, and a silently truncated sentence is a different sentence.
            _logger.LogWarning("desktop.notify refused: {Problem}", problem);
            return Answer(false, ReasonInvalidPayload, request?.NotificationId, problem);
        }

        var outcome = _sink.Show(request!);
        if (!outcome.Shown)
        {
            _logger.LogInformation(
                "desktop.notify not shown ({Reason}) for {NotificationId}",
                outcome.Reason,
                request!.NotificationId);
        }

        return Answer(outcome.Shown, outcome.Reason, request!.NotificationId, outcome.Detail);
    }

    private static JsonObject Answer(bool shown, string? reason, string? notificationId, string? detail)
    {
        var answer = new JsonObject { ["shown"] = shown };
        if (notificationId is not null)
        {
            answer["notification_id"] = notificationId;
        }

        if (!shown)
        {
            answer["reason"] = reason ?? ReasonShellUnavailable;
            if (!string.IsNullOrWhiteSpace(detail))
            {
                answer["detail"] = detail;
            }
        }

        return answer;
    }

    internal static bool TryParse(JsonObject? payload, out ToastRequest? request, out string problem)
    {
        request = null;
        problem = string.Empty;
        if (payload is null)
        {
            problem = "payload is missing";
            return false;
        }

        var id = payload["notification_id"]?.GetValue<string>();
        if (string.IsNullOrWhiteSpace(id) || !Guid.TryParse(id, out _))
        {
            problem = "notification_id must be a uuid";
            return false;
        }

        var title = payload["title"]?.GetValue<string>() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(title))
        {
            problem = "title is required";
            return false;
        }

        if (title.Length > MaxTitleChars)
        {
            problem = $"title is longer than {MaxTitleChars} characters";
            return false;
        }

        var body = payload["body"]?.GetValue<string>() ?? string.Empty;
        if (string.IsNullOrWhiteSpace(body))
        {
            problem = "body is required";
            return false;
        }

        if (body.Length > MaxBodyChars)
        {
            problem = $"body is longer than {MaxBodyChars} characters";
            return false;
        }

        var priority = payload["priority"]?.GetValue<string>() ?? "normal";
        if (priority is not ("urgent" or "normal" or "low"))
        {
            problem = $"priority {priority} is not one of urgent, normal, low";
            return false;
        }

        var groupKey = payload["group_key"]?.GetValue<string>() ?? string.Empty;
        if (groupKey.Length > MaxGroupKeyChars)
        {
            problem = "group_key is too long";
            return false;
        }

        var actions = new List<ToastAction>();
        if (payload["actions"] is JsonArray array)
        {
            if (array.Count > MaxActions)
            {
                problem = $"Windows shows at most {MaxActions} toast buttons";
                return false;
            }

            foreach (var node in array)
            {
                if (node is not JsonObject action)
                {
                    problem = "each action must be an object";
                    return false;
                }

                var actionId = action["id"]?.GetValue<string>() ?? string.Empty;
                if (!ActionId.IsMatch(actionId) || actionId.Length > MaxActionIdChars)
                {
                    problem = $"action id '{actionId}' is not [a-z0-9_]+";
                    return false;
                }

                var label = action["label"]?.GetValue<string>() ?? string.Empty;
                if (string.IsNullOrWhiteSpace(label) || label.Length > MaxActionLabelChars)
                {
                    problem = $"action '{actionId}' needs a label a button can hold";
                    return false;
                }

                actions.Add(new ToastAction(actionId, label));
            }
        }

        request = new ToastRequest(id!, title, body, priority, groupKey, actions);
        return true;
    }
}

/// <summary>One validated toast, as the device understood it.</summary>
public sealed record ToastRequest(
    string NotificationId,
    string Title,
    string Body,
    string Priority,
    string GroupKey,
    IReadOnlyList<ToastAction> Actions);

public sealed record ToastAction(string Id, string Label);

/// <summary>What actually happened when a toast was attempted.</summary>
public sealed record ToastOutcome(bool Shown, string? Reason = null, string? Detail = null)
{
    public static ToastOutcome Ok() => new(true);

    public static ToastOutcome NotShown(string reason, string? detail = null)
        => new(false, reason, detail);
}

/// <summary>
/// The seam between deciding to notify and putting pixels on a screen.
/// </summary>
/// <remarks>
/// A seam rather than a direct call, because the two have different failure modes and
/// different testability: everything above this interface is payload discipline that runs
/// anywhere, and everything below it needs a logged-in Windows session with a shell.
/// </remarks>
public interface IToastSink
{
    ToastOutcome Show(ToastRequest request);
}
