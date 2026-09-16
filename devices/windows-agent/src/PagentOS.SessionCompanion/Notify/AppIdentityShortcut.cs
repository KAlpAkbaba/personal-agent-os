using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text.RegularExpressions;
using PagentOS.SessionCompanion.Projects;

namespace PagentOS.SessionCompanion.Notify;

/// <summary>What <see cref="AppIdentityShortcut.Ensure"/> did.</summary>
public enum ShortcutState
{
    /// <summary>No link was there; one was written.</summary>
    Created,

    /// <summary>A link was there with another target or identity; it was rewritten.</summary>
    Updated,

    /// <summary>The link already said exactly this; nothing was written.</summary>
    Unchanged,
}

/// <summary>
/// The Start Menu shortcut that gives the unpackaged Session Companion an AppUserModelID.
/// </summary>
/// <remarks>
/// <para>
/// Windows raises a toast for an unpackaged desktop process only under an AppUserModelID
/// that a Start Menu shortcut carries (<c>System.AppUserModel.ID</c>). The shortcut is the
/// per-user one (<c>%APPDATA%\Microsoft\Windows\Start Menu\Programs</c>): no HKLM key, no
/// all-users folder, nothing that needs elevation - the companion runs as the owner.
/// </para>
/// <para>
/// <strong>Idempotent.</strong> The link is read first and rewritten only when its target or
/// its identity differs, so a companion start with nothing to change writes nothing. A link
/// that exists but cannot be read (corrupt, not a link) is rewritten. The link is written
/// through <c>IShellLinkW</c>, which needs the target to be a real executable
/// (<c>IPersistFile.Save</c> reads it) - the companion's own image is one.
/// </para>
/// <para>
/// The link's NAME is ASCII on purpose: <c>WScript.Shell</c> drops Turkish letters in link
/// names, and although <c>IShellLinkW</c> does not, a name nobody has to spell is one fewer
/// thing to go wrong in an installer script that may one day remove it.
/// </para>
/// </remarks>
[SupportedOSPlatform("windows")]
public static partial class AppIdentityShortcut
{
    /// <summary>The companion's AppUserModelID. Fixed: a changed id is a new app in Windows' settings, and the owner's choices about the old one would be silently lost.</summary>
    public const string AppUserModelId = "PagentOS.Companion";

    /// <summary>The link's file name in the Programs folder.</summary>
    public const string ShortcutFileName = "PagentOS Companion.lnk";

    /// <summary>What Windows allows in an AppUserModelID, and no more than it allows.</summary>
    [GeneratedRegex("^[A-Za-z0-9][A-Za-z0-9.\\-]{0,127}$")]
    private static partial Regex AppIdShape();

    /// <summary>The owner's own Start Menu Programs folder.</summary>
    public static string DefaultProgramsDirectory()
        => Environment.GetFolderPath(Environment.SpecialFolder.Programs);

    /// <summary>Whether <paramref name="appId"/> is an AppUserModelID Windows would take.</summary>
    public static bool IsValidAppId(string? appId) => appId is not null && AppIdShape().IsMatch(appId);

    /// <summary>
    /// Makes <c>&lt;programsDirectory&gt;\&lt;fileName&gt;</c> a link to <paramref name="target"/>
    /// carrying <paramref name="appId"/>. Throws when the link cannot be written; the caller
    /// decides what an unavailable identity means (the sink falls back to the balloon).
    /// </summary>
    public static ShortcutState Ensure(string programsDirectory, string appId, string target, string fileName = ShortcutFileName)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(programsDirectory);
        ArgumentException.ThrowIfNullOrWhiteSpace(target);
        ArgumentException.ThrowIfNullOrWhiteSpace(fileName);
        if (!IsValidAppId(appId))
        {
            throw new ArgumentException($"'{appId}' is not an AppUserModelID", nameof(appId));
        }

        if (!fileName.EndsWith(".lnk", StringComparison.OrdinalIgnoreCase)
            || fileName.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
        {
            throw new ArgumentException($"'{fileName}' is not a link file name", nameof(fileName));
        }

        var fullTarget = Path.GetFullPath(target);
        if (!File.Exists(fullTarget))
        {
            throw new FileNotFoundException("the shortcut target does not exist", fullTarget);
        }

        var path = PathFor(programsDirectory, fileName);
        var existed = File.Exists(path);
        if (existed)
        {
            try
            {
                var (currentTarget, currentId) = ShellLinkInterop.ReadShortcut(path);
                if (string.Equals(currentId, appId, StringComparison.Ordinal)
                    && string.Equals(Path.GetFullPath(currentTarget), fullTarget, StringComparison.OrdinalIgnoreCase))
                {
                    return ShortcutState.Unchanged;
                }
            }
            catch (Exception ex) when (ex is COMException or ArgumentException or IOException or UnauthorizedAccessException)
            {
                // Not a link we can read: it is rewritten below.
            }
        }

        Directory.CreateDirectory(programsDirectory);
        ShellLinkInterop.CreateShortcut(
            path,
            fullTarget,
            Path.GetDirectoryName(fullTarget) ?? programsDirectory,
            "PagentOS Companion - masaüstü bildirimleri",
            appId);
        return existed ? ShortcutState.Updated : ShortcutState.Created;
    }

    /// <summary>What the link at <paramref name="programsDirectory"/> says, or null when there is none.</summary>
    public static (string Target, string? AppUserModelId)? Read(string programsDirectory, string fileName = ShortcutFileName)
    {
        var path = PathFor(programsDirectory, fileName);
        return File.Exists(path) ? ShellLinkInterop.ReadShortcut(path) : null;
    }

    /// <summary>Removes the link (the lab's clean-up; the product never removes it).</summary>
    public static bool Remove(string programsDirectory, string fileName = ShortcutFileName)
    {
        var path = PathFor(programsDirectory, fileName);
        if (!File.Exists(path))
        {
            return false;
        }

        File.Delete(path);
        return true;
    }

    private static string PathFor(string programsDirectory, string fileName)
        => Path.Combine(Path.GetFullPath(programsDirectory), fileName);
}
