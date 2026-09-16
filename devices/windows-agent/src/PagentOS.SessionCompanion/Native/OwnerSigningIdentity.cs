using System.Globalization;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.SessionCompanion.Native;

/// <summary>Where the identity lives and how long it is good for. <see cref="Owner"/> is the production shape; a lab passes its own store and directory.</summary>
public sealed record SigningIdentityOptions(
    string StoreName,
    string StateDirectory,
    string Subject,
    TimeSpan Validity,
    TimeSpan RenewBefore,
    string KeyNamePrefix)
{
    /// <summary>Two years: long enough that the owner's one elevated trust step is rare, short enough that a lost profile does not sign for a decade.</summary>
    public static readonly TimeSpan DefaultValidity = TimeSpan.FromDays(730);

    /// <summary>An identity this close to expiry is replaced rather than used: a package signed a day before expiry would stop installing the day after (no timestamp is applied).</summary>
    public static readonly TimeSpan DefaultRenewBefore = TimeSpan.FromDays(30);

    public const string OwnerKeyNamePrefix = "PagentOS-Owner-Test-Signing";

    /// <summary>The owner's personal store (<c>CurrentUser\My</c>) and <c>%LOCALAPPDATA%\PagentOS\signing</c>.</summary>
    public static SigningIdentityOptions Owner() => new(
        "My",
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PagentOS", "signing"),
        NativeCapabilityNames.TestSigningSubject,
        DefaultValidity,
        DefaultRenewBefore,
        OwnerKeyNamePrefix);
}

/// <summary>What may be said about the identity: public facts only. There is no field here a private key could travel in.</summary>
public sealed record SigningCertificateFacts(
    string Thumbprint,
    string Subject,
    DateTimeOffset NotBefore,
    DateTimeOffset NotAfter,
    string CertificatePath,
    bool Created)
{
    public JsonObject ToJson() => new()
    {
        ["signer_thumbprint"] = Thumbprint,
        ["signer_subject"] = Subject,
        ["signer_not_after"] = NotAfter.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture),
    };
}

/// <summary>
/// B33 requirement 473 (owner decision 2026-09-16, "win uygulamada da kendinden imzalı olsun"):
/// the self-signed code-signing identity the Session Companion signs MSIX packages with.
/// </summary>
/// <remarks>
/// <para>
/// It lives where the owner lives: the key is a persisted CNG key of the owner's own profile
/// (Microsoft Software Key Storage Provider — Windows protects user keys with DPAPI), created
/// with <see cref="CngExportPolicies.None"/> so the private half can be USED but never read,
/// and the certificate sits in the owner's <c>CurrentUser\My</c> store pointing at it. The
/// Session-0 service never touches either: it has no owner profile to find them in.
/// </para>
/// <para>
/// Nothing but public facts leaves this class: the thumbprint, the subject, the validity and
/// the DER certificate exported beside <c>identity.json</c> so the owner's elevated trust step
/// (<see cref="NativeCapabilityNames.TrustScript"/>) can import exactly this certificate. No
/// certificate tool is run (<see cref="NativeCapabilityNames.ForbiddenPrograms"/> stands); the
/// certificate is made by <see cref="CertificateRequest"/> in this process.
/// </para>
/// <para>
/// It is created on first need and reused afterwards, identified by the thumbprint recorded in
/// <c>identity.json</c>. One that is missing, expired, within <see cref="SigningIdentityOptions.RenewBefore"/>
/// of expiry, or no longer the shape this class makes is replaced by a new one — which is NOT
/// trusted until the owner runs the trust step again, and every answer says so.
/// </para>
/// </remarks>
[SupportedOSPlatform("windows")]
public sealed class OwnerSigningIdentity
{
    public const string StateFileName = "identity.json";
    public const string CertificateFileName = "owner-test-signing.cer";
    public const int RsaKeyBits = 3072;

    private static readonly Lock Gate = new();

    private readonly TimeProvider _time;
    private readonly Func<string, bool> _isTrustedOnMachine;

    public OwnerSigningIdentity(SigningIdentityOptions options, TimeProvider? time = null, Func<string, bool>? trustLookup = null)
    {
        Options = options;
        _time = time ?? TimeProvider.System;
        _isTrustedOnMachine = trustLookup ?? IsInLocalMachineTrust;
    }

    public SigningIdentityOptions Options { get; }

    public string StatePath => Path.Combine(Options.StateDirectory, StateFileName);

    public string CertificatePath => Path.Combine(Options.StateDirectory, CertificateFileName);

    /// <summary>
    /// The identity to sign with, created if there is no usable one. The caller owns (and
    /// disposes) the returned certificate, which carries a handle to the non-exportable key.
    /// </summary>
    public X509Certificate2 Acquire(out SigningCertificateFacts facts)
    {
        lock (Gate)
        {
            var now = _time.GetUtcNow();
            var existing = LoadRecorded(now);
            if (existing is not null)
            {
                facts = Facts(existing, created: false);
                ExportPublic(existing);
                return existing;
            }

            var made = Create(now);
            facts = Facts(made, created: true);
            return made;
        }
    }

    /// <summary>The recorded identity's public facts without creating anything; null when there is no usable one.</summary>
    public SigningCertificateFacts? Describe()
    {
        lock (Gate)
        {
            using var existing = LoadRecorded(_time.GetUtcNow());
            return existing is null ? null : Facts(existing, created: false);
        }
    }

    /// <summary>
    /// Whether a certificate with this thumbprint is trusted for package installation on this
    /// machine: present in <c>LocalMachine\TrustedPeople</c> (or <c>LocalMachine\Root</c>),
    /// which is where the owner's trust step puts it and what Windows' package deployment
    /// consults. Read-only; this class never writes a LocalMachine store.
    /// </summary>
    public bool IsTrusted(string thumbprint) => _isTrustedOnMachine(thumbprint);

    /// <summary>
    /// Whether <paramref name="certificate"/> is the shape this class makes: the exact subject,
    /// self-issued, an end entity, the code-signing EKU, RSA, a private key present and not
    /// exportable, and valid for longer than the renewal margin.
    /// </summary>
    public static bool IsUsable(X509Certificate2 certificate, SigningIdentityOptions options, DateTimeOffset now, out string reason)
    {
        if (!string.Equals(certificate.Subject, options.Subject, StringComparison.Ordinal))
        {
            reason = "subject differs";
            return false;
        }

        if (!string.Equals(certificate.Issuer, certificate.Subject, StringComparison.Ordinal))
        {
            reason = "not self-issued";
            return false;
        }

        if (certificate.NotBefore.ToUniversalTime() > now.UtcDateTime)
        {
            reason = "not yet valid";
            return false;
        }

        if (certificate.NotAfter.ToUniversalTime() <= (now + options.RenewBefore).UtcDateTime)
        {
            reason = "expired or about to expire";
            return false;
        }

        if (!IsEndEntityCodeSigning(certificate))
        {
            reason = "not an end-entity code-signing certificate";
            return false;
        }

        if (!certificate.HasPrivateKey)
        {
            reason = "no private key";
            return false;
        }

        using var rsa = certificate.GetRSAPrivateKey();
        if (rsa is not RSACng cng)
        {
            reason = "not an RSA key in the key storage provider";
            return false;
        }

        if (cng.Key.ExportPolicy != CngExportPolicies.None)
        {
            reason = "the private key is exportable";
            return false;
        }

        reason = "usable";
        return true;
    }

    /// <summary>The extension checks the owner's trust script repeats: basic constraints say end entity, and code signing is the only EKU.</summary>
    public static bool IsEndEntityCodeSigning(X509Certificate2 certificate)
    {
        var basic = certificate.Extensions.OfType<X509BasicConstraintsExtension>().FirstOrDefault();
        if (basic is null || basic.CertificateAuthority)
        {
            return false;
        }

        var eku = certificate.Extensions.OfType<X509EnhancedKeyUsageExtension>().FirstOrDefault();
        if (eku is null || eku.EnhancedKeyUsages.Count != 1)
        {
            return false;
        }

        return eku.EnhancedKeyUsages[0].Value == NativeCapabilityNames.CodeSigningOid;
    }

    /// <summary>
    /// Removes every certificate of this identity's subject from its store, deletes each one's
    /// key, and deletes the state directory. For a LAB identity only: the owner's identity is
    /// never removed by this code (a refusal, not a convention).
    /// </summary>
    public void RemoveAllForLab()
    {
        if (string.Equals(Options.StoreName, "My", StringComparison.OrdinalIgnoreCase)
            || string.Equals(Options.KeyNamePrefix, SigningIdentityOptions.OwnerKeyNamePrefix, StringComparison.Ordinal))
        {
            throw new InvalidOperationException("the owner's signing identity is never removed by the companion");
        }

        lock (Gate)
        {
            using (var store = new X509Store(Options.StoreName, StoreLocation.CurrentUser))
            {
                var opened = true;
                try
                {
                    store.Open(OpenFlags.ReadWrite | OpenFlags.OpenExistingOnly);
                }
                catch (CryptographicException)
                {
                    opened = false;
                }

                if (opened)
                {
                    foreach (var certificate in store.Certificates.Find(X509FindType.FindBySubjectDistinguishedName, Options.Subject, validOnly: false))
                    {
                        using (certificate)
                        {
                            DeleteKeyOf(certificate, Options.KeyNamePrefix);
                            store.Remove(certificate);
                        }
                    }
                }
            }

            if (Directory.Exists(Options.StateDirectory))
            {
                Directory.Delete(Options.StateDirectory, recursive: true);
            }
        }
    }

    // ------------------------------------------------------------------ internals

    private X509Certificate2? LoadRecorded(DateTimeOffset now)
    {
        var thumbprint = ReadRecordedThumbprint();
        if (thumbprint is null)
        {
            return null;
        }

        using var store = new X509Store(Options.StoreName, StoreLocation.CurrentUser);
        try
        {
            store.Open(OpenFlags.ReadOnly | OpenFlags.OpenExistingOnly);
        }
        catch (CryptographicException)
        {
            return null;
        }

        var found = store.Certificates.Find(X509FindType.FindByThumbprint, thumbprint, validOnly: false);
        X509Certificate2? keep = null;
        foreach (var certificate in found)
        {
            if (keep is null && IsUsable(certificate, Options, now, out _))
            {
                keep = certificate;
            }
            else
            {
                certificate.Dispose();
            }
        }

        return keep;
    }

    private string? ReadRecordedThumbprint()
    {
        if (!File.Exists(StatePath))
        {
            return null;
        }

        try
        {
            var value = (JsonNode.Parse(File.ReadAllText(StatePath)) as JsonObject)?["thumbprint"]?.GetValue<string>();
            return value is { Length: 40 } && value.All(Uri.IsHexDigit) ? value.ToUpperInvariant() : null;
        }
        catch (Exception exception) when (exception is JsonException or InvalidOperationException or FormatException)
        {
            return null;
        }
    }

    private X509Certificate2 Create(DateTimeOffset now)
    {
        var keyName = $"{Options.KeyNamePrefix}-{now:yyyyMMdd}-{Guid.NewGuid():N}";
        var parameters = new CngKeyCreationParameters
        {
            ExportPolicy = CngExportPolicies.None,
            KeyUsage = CngKeyUsages.Signing,
            KeyCreationOptions = CngKeyCreationOptions.None,
            Provider = CngProvider.MicrosoftSoftwareKeyStorageProvider,
            UIPolicy = new CngUIPolicy(CngUIProtectionLevels.None),
        };
        parameters.Parameters.Add(new CngProperty("Length", BitConverter.GetBytes(RsaKeyBits), CngPropertyOptions.None));

        var key = CngKey.Create(CngAlgorithm.Rsa, keyName, parameters);
        string? added = null;
        try
        {
            string thumbprint;
            using (var rsa = new RSACng(key))
            {
                var request = new CertificateRequest(new X500DistinguishedName(Options.Subject), rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
                request.CertificateExtensions.Add(new X509BasicConstraintsExtension(certificateAuthority: false, hasPathLengthConstraint: false, pathLengthConstraint: 0, critical: true));
                request.CertificateExtensions.Add(new X509KeyUsageExtension(X509KeyUsageFlags.DigitalSignature, critical: true));
                request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension([new Oid(NativeCapabilityNames.CodeSigningOid)], critical: false));
                request.CertificateExtensions.Add(new X509SubjectKeyIdentifierExtension(request.PublicKey, critical: false));

                // CreateSelfSigned over a PERSISTED CNG key records the key's name on the
                // certificate (CERT_KEY_PROV_INFO) instead of copying key material into it,
                // which is what lets the store entry find the key again after a restart.
                using var made = request.CreateSelfSigned(now.AddMinutes(-5), now + Options.Validity);
                using var store = new X509Store(Options.StoreName, StoreLocation.CurrentUser);
                store.Open(OpenFlags.ReadWrite);
                store.Add(made);
                thumbprint = made.Thumbprint;
                added = thumbprint;
            }

            WriteState(thumbprint, keyName);
            var stored = LoadRecorded(now)
                ?? throw new CryptographicException("the signing certificate was added to the store but does not read back as usable");
            ExportPublic(stored);
            return stored;
        }
        catch
        {
            // Nothing half-made is left behind (found 2026-09-17): without this, a certificate
            // that did not read back stayed in the store with its key deleted - an orphan in
            // the owner's CurrentUser\My - and the state file still named it.
            TryDelete(key);
            if (added is not null)
            {
                RemoveFromStore(added);
                if (string.Equals(ReadRecordedThumbprint(), added, StringComparison.OrdinalIgnoreCase))
                {
                    File.Delete(StatePath);
                }
            }

            throw;
        }
        finally
        {
            key.Dispose();
        }
    }

    private void WriteState(string thumbprint, string keyName)
    {
        Directory.CreateDirectory(Options.StateDirectory);
        var state = new JsonObject
        {
            ["thumbprint"] = thumbprint,
            ["subject"] = Options.Subject,
            ["store"] = $"CurrentUser\\{Options.StoreName}",
            ["key_name"] = keyName,
            ["certificate_file"] = CertificateFileName,
            ["created_at"] = _time.GetUtcNow().UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture),
        };
        var temporary = StatePath + ".tmp";
        File.WriteAllText(temporary, state.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temporary, StatePath, overwrite: true);
    }

    /// <summary>The public certificate, DER, beside the state file — the only certificate bytes this class ever writes.</summary>
    private void ExportPublic(X509Certificate2 certificate)
    {
        Directory.CreateDirectory(Options.StateDirectory);
        var der = certificate.Export(X509ContentType.Cert);
        if (File.Exists(CertificatePath) && File.ReadAllBytes(CertificatePath).AsSpan().SequenceEqual(der))
        {
            return;
        }

        File.WriteAllBytes(CertificatePath, der);
    }

    private SigningCertificateFacts Facts(X509Certificate2 certificate, bool created) => new(
        certificate.Thumbprint,
        certificate.Subject,
        new DateTimeOffset(certificate.NotBefore.ToUniversalTime(), TimeSpan.Zero),
        new DateTimeOffset(certificate.NotAfter.ToUniversalTime(), TimeSpan.Zero),
        CertificatePath,
        created);

    private static void DeleteKeyOf(X509Certificate2 certificate, string prefix)
    {
        if (!certificate.HasPrivateKey)
        {
            return;
        }

        try
        {
            using var rsa = certificate.GetRSAPrivateKey();
            if (rsa is RSACng cng && cng.Key.KeyName is { } name && name.StartsWith(prefix, StringComparison.Ordinal))
            {
                TryDelete(cng.Key);
            }
        }
        catch (CryptographicException)
        {
            // The certificate still names a key that is already gone (found 2026-09-17: this
            // aborted the lab's cleanup and left its store behind). The certificate is removed
            // by the caller either way.
        }
    }

    private void RemoveFromStore(string thumbprint)
    {
        using var store = new X509Store(Options.StoreName, StoreLocation.CurrentUser);
        try
        {
            store.Open(OpenFlags.ReadWrite | OpenFlags.OpenExistingOnly);
        }
        catch (CryptographicException)
        {
            return;
        }

        foreach (var certificate in store.Certificates.Find(X509FindType.FindByThumbprint, thumbprint, validOnly: false))
        {
            using (certificate)
            {
                store.Remove(certificate);
            }
        }
    }

    private static void TryDelete(CngKey key)
    {
        try
        {
            key.Delete();
        }
        catch (CryptographicException)
        {
            // Already gone.
        }
    }

    private static bool IsInLocalMachineTrust(string thumbprint)
    {
        foreach (var name in new[] { StoreName.TrustedPeople, StoreName.Root })
        {
            using var store = new X509Store(name, StoreLocation.LocalMachine);
            try
            {
                store.Open(OpenFlags.ReadOnly | OpenFlags.OpenExistingOnly);
            }
            catch (CryptographicException)
            {
                continue;
            }

            var found = store.Certificates.Find(X509FindType.FindByThumbprint, thumbprint, validOnly: false);
            var hit = found.Count > 0;
            foreach (var certificate in found)
            {
                certificate.Dispose();
            }

            if (hit)
            {
                return true;
            }
        }

        return false;
    }
}
