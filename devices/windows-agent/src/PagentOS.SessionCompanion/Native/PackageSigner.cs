using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Security.Cryptography.Pkcs;
using System.Security.Cryptography.X509Certificates;

namespace PagentOS.SessionCompanion.Native;

/// <summary>
/// What an independent reader found in a package: whether it carries a signature at all, whether
/// Windows' own verifier found its digests intact, whether the chain is trusted here, and who
/// signed it. Public facts only.
/// </summary>
public sealed record PackageSignatureReadBack(
    bool Signed,
    bool Intact,
    bool ChainTrusted,
    int VerifyStatus,
    string? SignerThumbprint,
    string? SignerSubject,
    DateTimeOffset? SignerNotAfter,
    string Detail)
{
    /// <summary>The verifier's answer as the eight hex digits Windows documents it by.</summary>
    public string VerifyStatusHex => $"0x{unchecked((uint)VerifyStatus):X8}";
}

/// <summary>
/// B33 requirement 473: signing an MSIX IN PROCESS, and reading the signature back through
/// something that did not write it.
/// </summary>
/// <remarks>
/// <para>
/// <b>Signing</b> is <c>SignerSignEx2</c> from <c>mssign32.dll</c> — the function Windows' own
/// packaging tools call — with the package's SIP data (<c>APPX_SIP_CLIENT_DATA</c>), SHA-256,
/// and the certificate context of <see cref="OwnerSigningIdentity"/>, whose key stays inside
/// the key storage provider. No child process exists at any point; every program in
/// <c>NativeCapabilityNames.ForbiddenPrograms</c> remains refused. <b>No timestamp</b> is
/// requested: a timestamp is a network call to a third party, and this device makes none on
/// the owner's behalf — the consequence (a package stops verifying when the certificate
/// expires) is why the identity renews a month early.
/// </para>
/// <para>
/// <b>Read-back</b> is two readers, neither of them the signer: <c>WinVerifyTrust</c> with the
/// generic Authenticode action (Windows' package SIP recomputes every digest in the package; no
/// revocation check, no network retrieval), and the package's own <c>AppxSignature.p7x</c>
/// decoded as CMS with its signer signature checked, which is what names the signer.
/// </para>
/// </remarks>
[SupportedOSPlatform("windows")]
public static class PackageSigner
{
    public const string SignatureEntryName = "AppxSignature.p7x";

    /// <summary><c>PKCX</c>: the four bytes an MSIX signature file starts with, before the DER.</summary>
    public static ReadOnlySpan<byte> P7xMagic => "PKCX"u8;

    public const int TrustSuccess = 0;
    public const int CertEUntrustedRoot = unchecked((int)0x800B0109);
    public const int CertEChaining = unchecked((int)0x800B010A);
    public const int CertEUntrustedCa = unchecked((int)0x800B0112);
    public const int TrustENoSignature = unchecked((int)0x800B0100);
    public const int TrustEBadDigest = unchecked((int)0x80096010);

    /// <summary>
    /// The verifier answers that mean "every digest and the signature verified; only the chain
    /// ends somewhere this machine does not trust" — the normal state of a self-signed package
    /// before the owner's trust step. Anything else (a bad digest, no signature, an explicit
    /// distrust, an expired certificate) is not a signature this device will call intact.
    /// <para>
    /// Intact is NOT trusted. "Signed" in this class (and <c>signed: true</c> on the wire)
    /// means "a signature is there and every digest verifies", possibly by a signer this
    /// machine does not trust. Trust is a separate fact (<see cref="PackageSignatureReadBack.ChainTrusted"/>,
    /// <see cref="OwnerSigningIdentity.IsTrusted"/>) and the install step gates on it on its own,
    /// before Windows is asked (security review 2026-09-17).
    /// </para>
    /// </summary>
    public static bool IsIntact(int status) => status is TrustSuccess or CertEUntrustedRoot or CertEChaining or CertEUntrustedCa;

    /// <summary>Signs the MSIX at <paramref name="packagePath"/> in place. Throws <see cref="CryptographicException"/> with the signer's HRESULT on failure.</summary>
    public static void SignMsix(string packagePath, X509Certificate2 certificate)
    {
        var allocations = new List<IntPtr>();
        IntPtr Alloc(int bytes)
        {
            var pointer = Marshal.AllocHGlobal(bytes);
            allocations.Add(pointer);
            ZeroMemory(pointer, bytes);
            return pointer;
        }

        IntPtr Struct<T>(T value)
            where T : struct
        {
            var pointer = Alloc(Marshal.SizeOf<T>());
            Marshal.StructureToPtr(value, pointer, fDeleteOld: false);
            return pointer;
        }

        var fileName = Marshal.StringToHGlobalUni(Path.GetFullPath(packagePath));
        allocations.Add(fileName);
        IntPtr sipState = IntPtr.Zero;
        IntPtr signerContext = IntPtr.Zero;
        try
        {
            var index = Alloc(sizeof(uint));
            var fileInfo = Struct(new SIGNER_FILE_INFO { cbSize = (uint)Marshal.SizeOf<SIGNER_FILE_INFO>(), pwszFileName = fileName });
            var subject = Struct(new SIGNER_SUBJECT_INFO
            {
                cbSize = (uint)Marshal.SizeOf<SIGNER_SUBJECT_INFO>(),
                pdwIndex = index,
                dwSubjectChoice = SIGNER_SUBJECT_FILE,
                pSignerFileInfo = fileInfo,
            });
            var storeInfo = Struct(new SIGNER_CERT_STORE_INFO
            {
                cbSize = (uint)Marshal.SizeOf<SIGNER_CERT_STORE_INFO>(),
                pSigningCert = certificate.Handle,
                dwCertPolicy = SIGNER_CERT_POLICY_CHAIN_NO_ROOT,
            });
            var signerCert = Struct(new SIGNER_CERT
            {
                cbSize = (uint)Marshal.SizeOf<SIGNER_CERT>(),
                dwCertChoice = SIGNER_CERT_STORE,
                pCertStoreInfo = storeInfo,
            });
            var signatureInfo = Struct(new SIGNER_SIGNATURE_INFO
            {
                cbSize = (uint)Marshal.SizeOf<SIGNER_SIGNATURE_INFO>(),
                algidHash = CALG_SHA_256,
                dwAttrChoice = SIGNER_NO_ATTR,
            });
            var contextSlot = Alloc(IntPtr.Size);
            var parameters = Alloc(Marshal.SizeOf<SIGNER_SIGN_EX2_PARAMS>());
            var sipData = Struct(new APPX_SIP_CLIENT_DATA { pSignerParams = parameters });
            Marshal.StructureToPtr(
                new SIGNER_SIGN_EX2_PARAMS
                {
                    pSubjectInfo = subject,
                    pSigningCert = signerCert,
                    pSignatureInfo = signatureInfo,
                    pSipData = sipData,
                    pSignerContext = contextSlot,
                },
                parameters,
                fDeleteOld: false);

            var hr = SignerSignEx2(
                0,
                subject,
                signerCert,
                signatureInfo,
                IntPtr.Zero,
                0,
                IntPtr.Zero,
                IntPtr.Zero,
                IntPtr.Zero,
                sipData,
                contextSlot,
                IntPtr.Zero,
                IntPtr.Zero);
            signerContext = Marshal.ReadIntPtr(contextSlot);
            sipState = Marshal.ReadIntPtr(sipData, IntPtr.Size);
            if (hr != 0)
            {
                throw new CryptographicException($"the MSIX signer refused the package (0x{unchecked((uint)hr):X8})") { HResult = hr };
            }
        }
        finally
        {
            if (signerContext != IntPtr.Zero)
            {
                _ = SignerFreeSignerContext(signerContext);
            }

            if (sipState != IntPtr.Zero)
            {
                Marshal.Release(sipState);
            }

            foreach (var pointer in allocations)
            {
                Marshal.FreeHGlobal(pointer);
            }
        }
    }

    /// <summary>
    /// Reads the package's signature back without trusting anything the signer said.
    /// <c>Signed</c> in the result means intact (see <see cref="IsIntact"/>), possibly UNTRUSTED;
    /// a caller that installs must also check <c>ChainTrusted</c> or the machine's trust stores.
    /// </summary>
    public static PackageSignatureReadBack Verify(string packagePath)
    {
        var status = WinVerifyTrustFile(Path.GetFullPath(packagePath));
        var intact = IsIntact(status);
        byte[]? p7x;
        try
        {
            p7x = ReadSignatureEntry(packagePath);
        }
        catch (InvalidDataException exception)
        {
            return new PackageSignatureReadBack(false, false, false, status, null, null, null, $"the package is not a readable zip: {exception.Message}");
        }

        if (p7x is null)
        {
            return new PackageSignatureReadBack(false, false, false, status, null, null, null, "the package carries no AppxSignature.p7x");
        }

        if (p7x.Length <= P7xMagic.Length || !p7x.AsSpan(0, P7xMagic.Length).SequenceEqual(P7xMagic))
        {
            return new PackageSignatureReadBack(false, false, false, status, null, null, null, "AppxSignature.p7x does not start with PKCX");
        }

        X509Certificate2? signer;
        try
        {
            var cms = new SignedCms();
            cms.Decode(p7x.AsSpan(P7xMagic.Length));
            cms.CheckSignature(verifySignatureOnly: true);
            signer = cms.SignerInfos.Count == 1 ? cms.SignerInfos[0].Certificate : null;
        }
        catch (CryptographicException exception)
        {
            return new PackageSignatureReadBack(false, false, false, status, null, null, null, $"the signature block does not verify: {exception.Message}");
        }

        if (signer is null)
        {
            return new PackageSignatureReadBack(false, intact, false, status, null, null, null, "the signature block does not name exactly one signer");
        }

        using (signer)
        {
            var detail = intact
                ? status == TrustSuccess ? "intact; the chain is trusted" : "intact; the chain is not trusted on this machine"
                : $"Windows' verifier refused the package ({unchecked((uint)status):X8})";
            return new PackageSignatureReadBack(
                Signed: intact,
                Intact: intact,
                ChainTrusted: status == TrustSuccess,
                VerifyStatus: status,
                SignerThumbprint: signer.Thumbprint,
                SignerSubject: signer.Subject,
                SignerNotAfter: new DateTimeOffset(signer.NotAfter.ToUniversalTime(), TimeSpan.Zero),
                Detail: detail);
        }
    }

    private static byte[]? ReadSignatureEntry(string packagePath)
    {
        using var zip = ZipFile.OpenRead(packagePath);
        var entry = zip.GetEntry(SignatureEntryName);
        if (entry is null)
        {
            return null;
        }

        if (entry.Length > 1024 * 1024)
        {
            throw new InvalidDataException("AppxSignature.p7x is implausibly large");
        }

        using var stream = entry.Open();
        using var buffer = new MemoryStream();
        stream.CopyTo(buffer);
        return buffer.ToArray();
    }

    private static int WinVerifyTrustFile(string path)
    {
        var action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
        var filePath = Marshal.StringToHGlobalUni(path);
        var fileInfo = Marshal.AllocHGlobal(Marshal.SizeOf<WINTRUST_FILE_INFO>());
        var data = Marshal.AllocHGlobal(Marshal.SizeOf<WINTRUST_DATA>());
        try
        {
            Marshal.StructureToPtr(new WINTRUST_FILE_INFO { cbStruct = (uint)Marshal.SizeOf<WINTRUST_FILE_INFO>(), pcwszFilePath = filePath }, fileInfo, false);
            var trust = new WINTRUST_DATA
            {
                cbStruct = (uint)Marshal.SizeOf<WINTRUST_DATA>(),
                dwUIChoice = WTD_UI_NONE,
                fdwRevocationChecks = WTD_REVOKE_NONE,
                dwUnionChoice = WTD_CHOICE_FILE,
                pFile = fileInfo,
                dwStateAction = WTD_STATEACTION_VERIFY,
                dwProvFlags = WTD_REVOCATION_CHECK_NONE | WTD_CACHE_ONLY_URL_RETRIEVAL,
            };
            Marshal.StructureToPtr(trust, data, false);
            var status = WinVerifyTrust(new IntPtr(-1), ref action, data);

            trust = Marshal.PtrToStructure<WINTRUST_DATA>(data);
            trust.dwStateAction = WTD_STATEACTION_CLOSE;
            Marshal.StructureToPtr(trust, data, false);
            _ = WinVerifyTrust(new IntPtr(-1), ref action, data);
            return status;
        }
        finally
        {
            Marshal.FreeHGlobal(data);
            Marshal.FreeHGlobal(fileInfo);
            Marshal.FreeHGlobal(filePath);
        }
    }

    private static void ZeroMemory(IntPtr pointer, int bytes)
    {
        for (var offset = 0; offset < bytes; offset++)
        {
            Marshal.WriteByte(pointer, offset, 0);
        }
    }

    // ------------------------------------------------------------------ interop

    private const uint SIGNER_SUBJECT_FILE = 1;
    private const uint SIGNER_CERT_STORE = 2;
    private const uint SIGNER_CERT_POLICY_CHAIN_NO_ROOT = 8;
    private const uint SIGNER_NO_ATTR = 0;
    private const uint CALG_SHA_256 = 0x0000800c;

    private const uint WTD_UI_NONE = 2;
    private const uint WTD_REVOKE_NONE = 0;
    private const uint WTD_CHOICE_FILE = 1;
    private const uint WTD_STATEACTION_VERIFY = 1;
    private const uint WTD_STATEACTION_CLOSE = 2;
    private const uint WTD_REVOCATION_CHECK_NONE = 0x10;
    private const uint WTD_CACHE_ONLY_URL_RETRIEVAL = 0x1000;

    private static readonly Guid WINTRUST_ACTION_GENERIC_VERIFY_V2 = new("00AAC56B-CD44-11d0-8CC2-00C04FC295EE");

    [StructLayout(LayoutKind.Sequential)]
    private struct SIGNER_FILE_INFO
    {
        public uint cbSize;
        public IntPtr pwszFileName;
        public IntPtr hFile;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SIGNER_SUBJECT_INFO
    {
        public uint cbSize;
        public IntPtr pdwIndex;
        public uint dwSubjectChoice;
        public IntPtr pSignerFileInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SIGNER_CERT_STORE_INFO
    {
        public uint cbSize;
        public IntPtr pSigningCert;
        public uint dwCertPolicy;
        public IntPtr hCertStore;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SIGNER_CERT
    {
        public uint cbSize;
        public uint dwCertChoice;
        public IntPtr pCertStoreInfo;
        public IntPtr hwnd;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SIGNER_SIGNATURE_INFO
    {
        public uint cbSize;
        public uint algidHash;
        public uint dwAttrChoice;
        public IntPtr pAttrAuthcode;
        public IntPtr psAuthenticated;
        public IntPtr psUnauthenticated;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct SIGNER_SIGN_EX2_PARAMS
    {
        public uint dwFlags;
        public IntPtr pSubjectInfo;
        public IntPtr pSigningCert;
        public IntPtr pSignatureInfo;
        public IntPtr pProviderInfo;
        public uint dwTimestampFlags;
        public IntPtr pszAlgorithmOid;
        public IntPtr pwszTimestampURL;
        public IntPtr pCryptAttrs;
        public IntPtr pSipData;
        public IntPtr pSignerContext;
        public IntPtr pCryptoPolicy;
        public IntPtr pReserved;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct APPX_SIP_CLIENT_DATA
    {
        public IntPtr pSignerParams;
        public IntPtr pAppxSipState;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WINTRUST_FILE_INFO
    {
        public uint cbStruct;
        public IntPtr pcwszFilePath;
        public IntPtr hFile;
        public IntPtr pgKnownSubject;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct WINTRUST_DATA
    {
        public uint cbStruct;
        public IntPtr pPolicyCallbackData;
        public IntPtr pSIPClientData;
        public uint dwUIChoice;
        public uint fdwRevocationChecks;
        public uint dwUnionChoice;
        public IntPtr pFile;
        public uint dwStateAction;
        public IntPtr hWVTStateData;
        public IntPtr pwszURLReference;
        public uint dwProvFlags;
        public uint dwUIContext;
        public IntPtr pSignatureSettings;
    }

    [DllImport("mssign32.dll", ExactSpelling = true)]
    private static extern int SignerSignEx2(
        uint dwFlags,
        IntPtr pSubjectInfo,
        IntPtr pSignerCert,
        IntPtr pSignatureInfo,
        IntPtr pProviderInfo,
        uint dwTimestampFlags,
        IntPtr pszTimestampAlgorithmOid,
        IntPtr pwszTimestampServer,
        IntPtr psRequest,
        IntPtr pSipData,
        IntPtr ppSignerContext,
        IntPtr pCryptoPolicy,
        IntPtr pReserved);

    [DllImport("mssign32.dll", ExactSpelling = true)]
    private static extern int SignerFreeSignerContext(IntPtr pSignerContext);

    [DllImport("wintrust.dll", ExactSpelling = true)]
    private static extern int WinVerifyTrust(IntPtr hwnd, ref Guid pgActionID, IntPtr pWVTData);
}
