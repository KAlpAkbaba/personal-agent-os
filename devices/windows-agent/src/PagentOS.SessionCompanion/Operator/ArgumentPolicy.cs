using System.Runtime.Versioning;
using PagentOS.Agent.Core.Commands;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Operator;

/// <summary>
/// What an allowlisted application may be started WITH (ADR-0082 addendum 2, finding 3). The
/// application allowlist says which programs may run; this says which arguments each may be
/// given, because a program's arguments change what the program IS — <c>chrome
/// --remote-debugging-port</c> is a remote-controllable browser, <c>--load-extension</c> an
/// extended one, <c>powershell -Command</c> an unallowlisted terminal. The policy is keyed by
/// the allowlist NAME; an absolute executable path and any name without a policy get
/// <see cref="None"/>. Every refusal is a <c>validation_error</c> naming the argument, raised
/// before a process exists, and what passes is returned in the form it will be passed on
/// (a path in its resolved final form, as the roots check requires).
/// </summary>
[SupportedOSPlatform("windows")]
public abstract class ArgumentPolicy
{
    public const string NewWindowFlag = "--new-window";

    /// <summary>No arguments at all: <c>calc</c>, <c>powershell</c> (the governed path is <c>terminal.execute</c>), an absolute executable path, an unknown name.</summary>
    public static readonly ArgumentPolicy None = new NonePolicy();

    /// <summary>At most one argument, an absolute path inside the authorised roots, resolved: <c>notepad</c>, <c>explorer</c>, <c>mspaint</c>.</summary>
    public static readonly ArgumentPolicy OnePathUnderRoots = new OnePathPolicy();

    /// <summary>Only <c>http://</c> / <c>https://</c> URLs and <c>--new-window</c>: <c>chrome</c>, <c>msedge</c>.</summary>
    public static readonly ArgumentPolicy BrowserUrls = new BrowserPolicy();

    /// <summary>A sentence for messages and documentation.</summary>
    public abstract string Description { get; }

    /// <summary>The policy an allowlist name gets; anything not named here gets <see cref="None"/>.</summary>
    public static ArgumentPolicy For(string application)
        => application.ToLowerInvariant() switch
        {
            // mspaint takes the same shape as notepad for the same reason: the only argument
            // it is ever given is one image to open, and it must be a real path inside the
            // authorised roots (M27's export check opens a PNG the device itself produced).
            "notepad" or "explorer" or "mspaint" => OnePathUnderRoots,
            "chrome" or "msedge" => BrowserUrls,
            _ => None,
        };

    /// <summary>Validate <paramref name="args"/> for <paramref name="application"/>; the returned list is what the process receives.</summary>
    public abstract IReadOnlyList<string> Apply(string application, IReadOnlyList<string> args, AuthorisedRoots roots);

    protected static CapabilityException Refuse(string application, string argument, string reason)
        => new(
            ErrorClasses.ValidationError,
            $"argument '{Truncate(argument)}' is not allowed for {application}: {reason}; nothing was started",
            retryable: false);

    private static string Truncate(string text) => text.Length <= 200 ? text : text[..200] + "…";

    private sealed class NonePolicy : ArgumentPolicy
    {
        public override string Description => "no arguments";

        public override IReadOnlyList<string> Apply(string application, IReadOnlyList<string> args, AuthorisedRoots roots)
        {
            if (args.Count > 0)
            {
                throw Refuse(application, args[0], $"{application} takes no arguments");
            }

            return [];
        }
    }

    private sealed class OnePathPolicy : ArgumentPolicy
    {
        public override string Description => "at most one argument, an absolute path inside the authorised roots";

        public override IReadOnlyList<string> Apply(string application, IReadOnlyList<string> args, AuthorisedRoots roots)
        {
            if (args.Count == 0)
            {
                return [];
            }

            if (args.Count > 1)
            {
                throw Refuse(application, args[1], $"{application} takes at most one argument, a path inside the authorised roots");
            }

            var argument = args[0];
            if (!Path.IsPathRooted(argument))
            {
                throw Refuse(application, argument, "it is not an absolute path inside the authorised roots");
            }

            var resolved = roots.Confine(argument)
                           ?? throw Refuse(application, argument, $"it does not resolve to a path inside the authorised roots ({string.Join(";", roots.Configured)})");
            return [resolved];
        }
    }

    private sealed class BrowserPolicy : ArgumentPolicy
    {
        public override string Description => "http:// or https:// URLs and --new-window only";

        public override IReadOnlyList<string> Apply(string application, IReadOnlyList<string> args, AuthorisedRoots roots)
        {
            var accepted = new List<string>(args.Count);
            foreach (var argument in args)
            {
                if (string.Equals(argument, NewWindowFlag, StringComparison.OrdinalIgnoreCase))
                {
                    accepted.Add(NewWindowFlag);
                    continue;
                }

                if (IsWebUrl(argument))
                {
                    accepted.Add(argument);
                    continue;
                }

                throw Refuse(application, argument, $"{application} accepts http:// or https:// URLs and {NewWindowFlag} only");
            }

            return accepted;
        }

        /// <summary>An absolute http(s) URL with a host and no whitespace; nothing that starts like a flag.</summary>
        private static bool IsWebUrl(string argument)
        {
            if (argument.Length == 0 || argument[0] == '-' || argument.Any(char.IsWhiteSpace))
            {
                return false;
            }

            return Uri.TryCreate(argument, UriKind.Absolute, out var uri)
                   && (uri.Scheme == Uri.UriSchemeHttp || uri.Scheme == Uri.UriSchemeHttps)
                   && !string.IsNullOrEmpty(uri.Host);
        }
    }
}
