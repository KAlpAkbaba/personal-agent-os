using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace PagentOS.SessionCompanion.Projects;

/// <summary>
/// B33 (req 468): a Start Menu shortcut written through the shell's own <c>IShellLinkW</c> +
/// <c>IPersistFile</c>. The scripting host's <c>WScript.Shell.CreateShortcut</c> was tried
/// first and could not save a link whose NAME carries a Turkish letter ("Notlarım.lnk" —
/// the owner's own titles), so the Unicode interface is used directly. Windows only.
/// </summary>
internal static class ShellLinkInterop
{
    private const int MaxPath = 260;

    /// <summary>
    /// The shell link object is apartment-threaded, so the whole call runs on its own STA
    /// thread (the companion's capability workers are MTA) and any failure is rethrown on
    /// the caller's. <c>Save</c> answers E_FAIL for a target the shell cannot read as an
    /// executable — that is the shell's own check, surfaced as <c>dependency_unavailable</c>
    /// by the caller, never swallowed.
    /// </summary>
    public static void CreateShortcut(string shortcutPath, string target, string workingDirectory, string description)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                Write(shortcutPath, target, workingDirectory, description);
            }
            catch (Exception exception)
            {
                failure = exception;
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.IsBackground = true;
        thread.Start();
        thread.Join();
        if (failure is not null)
        {
            throw failure;
        }
    }

    private static void Write(string shortcutPath, string target, string workingDirectory, string description)
    {
        var link = (IShellLinkW)new ShellLink();
        try
        {
            link.SetPath(target);
            link.SetWorkingDirectory(workingDirectory);
            link.SetDescription(description.Length > MaxPath - 1 ? description[..(MaxPath - 1)] : description);
            ((IPersistFile)link).Save(shortcutPath, false);
        }
        finally
        {
            Marshal.ReleaseComObject(link);
        }
    }

    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLink
    {
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("000214F9-0000-0000-C000-000000000046")]
    private interface IShellLinkW
    {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cchMaxPath, IntPtr pfd, int fFlags);
        void GetIDList(out IntPtr ppidl);
        void SetIDList(IntPtr pidl);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cchMaxName);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cchMaxPath);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cchMaxPath);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        void GetHotkey(out short pwHotkey);
        void SetHotkey(short wHotkey);
        void GetShowCmd(out int piShowCmd);
        void SetShowCmd(int iShowCmd);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cchIconPath, out int piIcon);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
        void Resolve(IntPtr hwnd, int fFlags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }
}
