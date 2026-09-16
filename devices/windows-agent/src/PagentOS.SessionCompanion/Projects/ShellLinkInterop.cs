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
/// <remarks>
/// B11-toast adds the link's <c>System.AppUserModel.ID</c> through the link's own
/// <c>IPropertyStore</c>: an unpackaged process may raise a Windows toast only under an
/// AppUserModelID that a Start Menu shortcut carries.
/// </remarks>
internal static class ShellLinkInterop
{
    private const int MaxPath = 260;
    private const int StgmRead = 0;
    private const ushort VtEmpty = 0;
    private const ushort VtLpwstr = 31;

    /// <summary><c>PKEY_AppUserModel_ID</c> = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, 5.</summary>
    private static readonly Guid AppUserModelFormat = new("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
    private const uint AppUserModelIdPid = 5;

    /// <summary>
    /// The shell link object is apartment-threaded, so the whole call runs on its own STA
    /// thread (the companion's capability workers are MTA) and any failure is rethrown on
    /// the caller's. <c>Save</c> answers E_FAIL for a target the shell cannot read as an
    /// executable — that is the shell's own check, surfaced as <c>dependency_unavailable</c>
    /// by the caller, never swallowed.
    /// </summary>
    public static void CreateShortcut(string shortcutPath, string target, string workingDirectory, string description)
        => OnSta(() => Write(shortcutPath, target, workingDirectory, description, appUserModelId: null));

    /// <summary>
    /// As <see cref="CreateShortcut(string, string, string, string)"/>, and the link carries
    /// <paramref name="appUserModelId"/> as its <c>System.AppUserModel.ID</c>.
    /// </summary>
    public static void CreateShortcut(string shortcutPath, string target, string workingDirectory, string description, string appUserModelId)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(appUserModelId);
        OnSta(() => Write(shortcutPath, target, workingDirectory, description, appUserModelId));
    }

    /// <summary>
    /// What an existing link says: its target path and its AppUserModelID (null when it has
    /// none). Reads only; the link is opened read-only.
    /// </summary>
    public static (string Target, string? AppUserModelId) ReadShortcut(string shortcutPath)
    {
        (string, string?) result = (string.Empty, null);
        OnSta(() => result = Read(shortcutPath));
        return result;
    }

    private static void OnSta(Action body)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                body();
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

    private static void Write(string shortcutPath, string target, string workingDirectory, string description, string? appUserModelId)
    {
        var link = (IShellLinkW)new ShellLink();
        try
        {
            link.SetPath(target);
            link.SetWorkingDirectory(workingDirectory);
            link.SetDescription(description.Length > MaxPath - 1 ? description[..(MaxPath - 1)] : description);
            if (appUserModelId is not null)
            {
                SetAppUserModelId((IPropertyStore)link, appUserModelId);
            }

            ((IPersistFile)link).Save(shortcutPath, false);
        }
        finally
        {
            Marshal.ReleaseComObject(link);
        }
    }

    private static (string Target, string? AppUserModelId) Read(string shortcutPath)
    {
        var link = (IShellLinkW)new ShellLink();
        try
        {
            ((IPersistFile)link).Load(shortcutPath, StgmRead);
            var target = new StringBuilder(MaxPath);
            // SLGP_RAWPATH (4): the path as stored, not resolved against the current machine.
            link.GetPath(target, target.Capacity, IntPtr.Zero, 4);
            return (target.ToString(), GetAppUserModelId((IPropertyStore)link));
        }
        finally
        {
            Marshal.ReleaseComObject(link);
        }
    }

    private static void SetAppUserModelId(IPropertyStore store, string value)
    {
        var key = new PropertyKey { FormatId = AppUserModelFormat, PropertyId = AppUserModelIdPid };
        var variant = new PropVariant { VarType = VtLpwstr, Pointer = Marshal.StringToCoTaskMemUni(value) };
        try
        {
            Marshal.ThrowExceptionForHR(store.SetValue(ref key, ref variant));
            Marshal.ThrowExceptionForHR(store.Commit());
        }
        finally
        {
            _ = PropVariantClear(ref variant);
        }
    }

    private static string? GetAppUserModelId(IPropertyStore store)
    {
        var key = new PropertyKey { FormatId = AppUserModelFormat, PropertyId = AppUserModelIdPid };
        var hr = store.GetValue(ref key, out var variant);
        try
        {
            if (hr < 0 || variant.VarType == VtEmpty)
            {
                return null;
            }

            return variant.VarType == VtLpwstr && variant.Pointer != IntPtr.Zero
                ? Marshal.PtrToStringUni(variant.Pointer)
                : null;
        }
        finally
        {
            _ = PropVariantClear(ref variant);
        }
    }

    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(ref PropVariant pvar);

    [StructLayout(LayoutKind.Sequential)]
    private struct PropertyKey
    {
        public Guid FormatId;
        public uint PropertyId;
    }

    /// <summary>A PROPVARIANT holding at most one pointer (16 bytes on x86, 24 on x64).</summary>
    [StructLayout(LayoutKind.Sequential)]
    private struct PropVariant
    {
        public ushort VarType;
        public ushort Reserved1;
        public ushort Reserved2;
        public ushort Reserved3;
        public IntPtr Pointer;
        public IntPtr Pointer2;
    }

    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    private class ShellLink
    {
    }

    [ComImport]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
    private interface IPropertyStore
    {
        [PreserveSig]
        int GetCount(out uint count);

        [PreserveSig]
        int GetAt(uint index, out PropertyKey key);

        [PreserveSig]
        int GetValue(ref PropertyKey key, out PropVariant value);

        [PreserveSig]
        int SetValue(ref PropertyKey key, ref PropVariant value);

        [PreserveSig]
        int Commit();
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
