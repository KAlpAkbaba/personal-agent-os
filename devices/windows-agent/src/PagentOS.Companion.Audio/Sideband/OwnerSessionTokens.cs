using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text;

namespace PagentOS.Companion.Audio.Sideband;

public interface IOwnerSessionTokenSource
{
    /// <summary>The current owner session token, or null when none is stored. Never cached across rotations.</summary>
    string? GetToken();
}

public sealed class StaticOwnerSessionTokenSource(string? token) : IOwnerSessionTokenSource
{
    public string? GetToken() => token;
}

/// <summary>
/// Reads the owner-session token the enrollment and rotation scripts already store —
/// <c>%LOCALAPPDATA%\PagentOS\secrets\PAGENTOS_OWNER_SESSION_TOKEN.dpapi</c>, written by
/// <c>ConvertFrom-SecureString</c> — from the owner's interactive session, which is the only
/// place it can be decrypted. This keeps the two-domain rule from QUALIFICATION 2.6 intact:
/// owner material stays owner-scoped and the Session-0 service still touches no DPAPI.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class DpapiOwnerSessionTokenSource(string? path = null) : IOwnerSessionTokenSource
{
    public const string SecretName = "PAGENTOS_OWNER_SESSION_TOKEN";

    public string Path { get; } = path ?? DpapiSecretStore.PathFor(SecretName);

    public string? GetToken()
    {
        try
        {
            return DpapiSecretStore.ReadSecureStringFile(Path);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or CryptographicFailureException or FormatException)
        {
            return null;
        }
    }
}

public sealed class CryptographicFailureException(string message, int win32Error) : Exception(message)
{
    public int Win32Error { get; } = win32Error;
}

/// <summary>
/// The exact on-disk format PowerShell's <c>ConvertFrom-SecureString</c> (no key) produces:
/// a hex string of a DPAPI blob over the UTF-16LE characters, protected to the current
/// user. Decoding it here means the companion reuses the owner's existing secret store
/// rather than inventing a second one. Uses crypt32 directly so no extra package is needed.
/// </summary>
[SupportedOSPlatform("windows")]
public static class DpapiSecretStore
{
    private const int CryptProtectUiForbidden = 0x1;

    public static string DefaultStoreRoot => System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PagentOS", "secrets");

    public static string PathFor(string secretName) => System.IO.Path.Combine(DefaultStoreRoot, secretName + ".dpapi");

    public static string? ReadSecureStringFile(string path)
    {
        if (!File.Exists(path))
        {
            return null;
        }

        var hex = File.ReadAllText(path).Trim();
        return hex.Length == 0 ? null : DecodeSecureString(hex);
    }

    public static string DecodeSecureString(string hex)
    {
        var blob = Convert.FromHexString(hex.Trim());
        var plain = Unprotect(blob);
        try
        {
            return Encoding.Unicode.GetString(plain).TrimEnd('\0');
        }
        finally
        {
            Array.Clear(plain);
        }
    }

    /// <summary>The inverse, byte-compatible with <c>ConvertTo-SecureString</c>; used by tests and any future companion-side writer.</summary>
    public static string EncodeSecureString(string plaintext)
    {
        var bytes = Encoding.Unicode.GetBytes(plaintext);
        try
        {
            return Convert.ToHexString(Protect(bytes)).ToLowerInvariant();
        }
        finally
        {
            Array.Clear(bytes);
        }
    }

    private static byte[] Protect(byte[] data) => Transform(data, protect: true);

    private static byte[] Unprotect(byte[] data) => Transform(data, protect: false);

    private static byte[] Transform(byte[] data, bool protect)
    {
        var input = new DataBlob { cbData = data.Length, pbData = Marshal.AllocHGlobal(Math.Max(1, data.Length)) };
        var output = default(DataBlob);
        try
        {
            Marshal.Copy(data, 0, input.pbData, data.Length);
            var ok = protect
                ? CryptProtectData(ref input, null, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, CryptProtectUiForbidden, ref output)
                : CryptUnprotectData(ref input, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, CryptProtectUiForbidden, ref output);
            if (!ok)
            {
                var error = Marshal.GetLastWin32Error();
                throw new CryptographicFailureException(
                    (protect ? "CryptProtectData" : "CryptUnprotectData") + " failed with Win32 error " + error,
                    error);
            }

            var result = new byte[output.cbData];
            Marshal.Copy(output.pbData, result, 0, output.cbData);
            return result;
        }
        finally
        {
            Marshal.FreeHGlobal(input.pbData);
            if (output.pbData != IntPtr.Zero)
            {
                LocalFree(output.pbData);
            }
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct DataBlob
    {
        public int cbData;
        public IntPtr pbData;
    }

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptProtectData(
        ref DataBlob pDataIn,
        string? szDataDescr,
        IntPtr pOptionalEntropy,
        IntPtr pvReserved,
        IntPtr pPromptStruct,
        int dwFlags,
        ref DataBlob pDataOut);

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptUnprotectData(
        ref DataBlob pDataIn,
        IntPtr ppszDataDescr,
        IntPtr pOptionalEntropy,
        IntPtr pvReserved,
        IntPtr pPromptStruct,
        int dwFlags,
        ref DataBlob pDataOut);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr hMem);
}
