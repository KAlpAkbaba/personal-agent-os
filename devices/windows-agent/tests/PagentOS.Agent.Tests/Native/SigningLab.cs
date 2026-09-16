using System.Runtime.InteropServices;
using System.Security.Cryptography.X509Certificates;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion.Native;
using PagentOS.SessionCompanion.Operator;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// A throwaway signing identity for the B33 lab. The owner's store is never touched: every lab
/// gets its OWN current-user certificate store (<c>PagentOSLab-&lt;run-id&gt;</c>, created on
/// first use), its own key-name prefix and its own state directory under the operator fixture;
/// <see cref="Dispose"/> deletes the certificate, its CNG key, the directory and the store
/// itself. The subject is the production one on purpose — the MSIX signer compares it with the
/// manifest's Publisher, and that comparison is part of what is proved. Nothing here opens a
/// LocalMachine store for writing: trust is simulated through the identity's lookup seam.
/// </summary>
public sealed class SigningLab : IDisposable
{
    public const string KeyNamePrefix = "PagentOS-Lab-Signing";

    private readonly TrustSwitch _trust = new();

    public SigningLab(TimeProvider? time = null, TimeSpan? validity = null, TimeSpan? renewBefore = null)
    {
        RunId = Guid.NewGuid().ToString("N")[..12];
        StoreName = "PagentOSLab-" + RunId;
        Directory = Path.Combine(OperatorOptions.FixtureRoot, "signing", RunId);
        Options = new SigningIdentityOptions(
            StoreName,
            Directory,
            NativeCapabilityNames.TestSigningSubject,
            validity ?? SigningIdentityOptions.DefaultValidity,
            renewBefore ?? SigningIdentityOptions.DefaultRenewBefore,
            KeyNamePrefix + "-" + RunId);
        Identity = new OwnerSigningIdentity(Options, time, thumbprint => _trust.Trusted.Contains(thumbprint));
    }

    public string RunId { get; }

    public string StoreName { get; }

    public string Directory { get; }

    public SigningIdentityOptions Options { get; }

    public OwnerSigningIdentity Identity { get; }

    /// <summary>Makes the lab's trust lookup answer "trusted" for this thumbprint — a simulation of the owner's elevated step.</summary>
    public void TrustOnThisMachine(string thumbprint) => _trust.Trusted.Add(thumbprint.ToUpperInvariant());

    /// <summary>Every certificate in the lab store, for assertions.</summary>
    public List<X509Certificate2> StoreContents()
    {
        using var store = new X509Store(StoreName, StoreLocation.CurrentUser);
        store.Open(OpenFlags.ReadOnly);
        return [.. store.Certificates];
    }

    /// <summary>The registry key a current-user system store lives under.</summary>
    public string RegistryPath => @"Software\Microsoft\SystemCertificates\" + StoreName;

    public bool StoreExists()
    {
        using var key = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(RegistryPath);
        return key is not null;
    }

    public void Dispose()
    {
        try
        {
            Identity.RemoveAllForLab();
        }
        finally
        {
            _ = CertUnregisterSystemStore(StoreName, CertSystemStoreCurrentUser | CertStoreDeleteFlag);

            // Belt and braces (found 2026-09-17: a failed cleanup left three lab stores behind):
            // whatever happened above, the lab's own store and directory do not outlive it.
            Microsoft.Win32.Registry.CurrentUser.DeleteSubKeyTree(RegistryPath, throwOnMissingSubKey: false);
            if (System.IO.Directory.Exists(Directory))
            {
                System.IO.Directory.Delete(Directory, recursive: true);
            }
        }
    }

    private const uint CertSystemStoreCurrentUser = 0x00010000;
    private const uint CertStoreDeleteFlag = 0x00000010;

    [DllImport("crypt32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CertUnregisterSystemStore(string pvSystemStore, uint dwFlags);

    private sealed class TrustSwitch
    {
        public HashSet<string> Trusted { get; } = new(StringComparer.OrdinalIgnoreCase);
    }
}
