using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;
using PagentOS.Companion.Audio.Timing;
using PagentOS.SessionCompanion.Native;
using Xunit;

namespace PagentOS.Agent.Tests.Native;

/// <summary>
/// B33 requirement 473 (owner decision 2026-09-16): the Session Companion's self-signed
/// code-signing identity — made by <see cref="CertificateRequest"/> in process, kept in a
/// current-user store, its key persisted and NOT exportable, reused by thumbprint, replaced
/// honestly near expiry. Every test works in a throwaway store (<see cref="SigningLab"/>);
/// the owner's <c>CurrentUser\My</c> is never opened for writing.
/// </summary>
[Collection(NativeLabCollection.Name)]
public sealed class NativeSigningIdentityTests
{
    [Fact]
    public void The_first_need_creates_an_end_entity_RSA_3072_code_signing_certificate_with_the_fixed_subject()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var lab = new SigningLab();
        using var certificate = lab.Identity.Acquire(out var facts);

        Assert.True(facts.Created);
        Assert.Equal(NativeCapabilityNames.TestSigningSubject, certificate.Subject);
        Assert.Equal("CN=PagentOS Owner Test Signing", certificate.Subject);
        Assert.Equal(certificate.Subject, certificate.Issuer);
        Assert.Equal(certificate.Thumbprint, facts.Thumbprint);

        var basic = Assert.Single(certificate.Extensions.OfType<X509BasicConstraintsExtension>());
        Assert.False(basic.CertificateAuthority);
        Assert.True(basic.Critical);
        var eku = Assert.Single(certificate.Extensions.OfType<X509EnhancedKeyUsageExtension>());
        Assert.Equal(new[] { NativeCapabilityNames.CodeSigningOid }, eku.EnhancedKeyUsages.Cast<Oid>().Select(o => o.Value ?? string.Empty).ToArray());
        var usage = Assert.Single(certificate.Extensions.OfType<X509KeyUsageExtension>());
        Assert.Equal(X509KeyUsageFlags.DigitalSignature, usage.KeyUsages);
        Assert.True(OwnerSigningIdentity.IsEndEntityCodeSigning(certificate));

        using var publicKey = certificate.GetRSAPublicKey();
        Assert.Equal(OwnerSigningIdentity.RsaKeyBits, publicKey!.KeySize);
        Assert.Equal(SigningIdentityOptions.DefaultValidity, certificate.NotAfter - certificate.NotBefore - TimeSpan.FromMinutes(5), new ToleranceComparer(TimeSpan.FromSeconds(2)));

        // It is in the lab store, and only there.
        var stored = lab.StoreContents();
        Assert.Contains(stored, c => c.Thumbprint == certificate.Thumbprint && c.HasPrivateKey);
    }

    [Fact]
    public void The_private_key_is_persisted_in_the_key_storage_provider_and_cannot_be_exported_in_any_form()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var lab = new SigningLab();
        using var certificate = lab.Identity.Acquire(out _);
        using var rsa = certificate.GetRSAPrivateKey();

        var cng = Assert.IsType<RSACng>(rsa);
        Assert.Equal(CngExportPolicies.None, cng.Key.ExportPolicy);
        Assert.False(cng.Key.IsEphemeral);
        Assert.StartsWith(lab.Options.KeyNamePrefix, cng.Key.KeyName, StringComparison.Ordinal);
        Assert.Equal(CngProvider.MicrosoftSoftwareKeyStorageProvider, cng.Key.Provider);
        Assert.False(cng.Key.IsMachineKey);

        Assert.ThrowsAny<CryptographicException>(() => cng.ExportParameters(includePrivateParameters: true));
        Assert.ThrowsAny<CryptographicException>(() => cng.ExportPkcs8PrivateKey());
        Assert.ThrowsAny<CryptographicException>(() => cng.ExportRSAPrivateKey());
        Assert.ThrowsAny<CryptographicException>(() => certificate.Export(X509ContentType.Pfx, "lab"));

        // The key still SIGNS: usable, never readable.
        var signature = cng.SignData("b33"u8.ToArray(), HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        using var verifier = certificate.GetRSAPublicKey()!;
        Assert.True(verifier.VerifyData("b33"u8.ToArray(), signature, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1));
    }

    [Fact]
    public void Only_public_facts_are_written_to_disk_the_der_certificate_and_a_state_file_naming_the_thumbprint()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var lab = new SigningLab();
        using var certificate = lab.Identity.Acquire(out var facts);

        var files = System.IO.Directory.GetFiles(lab.Directory).Select(f => Path.GetFileName(f)).OrderBy(n => n, StringComparer.Ordinal).ToArray();
        Assert.Equal(new[] { OwnerSigningIdentity.StateFileName, OwnerSigningIdentity.CertificateFileName }, files);

        var der = File.ReadAllBytes(lab.Identity.CertificatePath);
        using var exported = X509CertificateLoader.LoadCertificate(der);
        Assert.False(exported.HasPrivateKey);
        Assert.Equal(certificate.Thumbprint, exported.Thumbprint);
        Assert.Equal(certificate.RawData, der);
        Assert.Equal(lab.Identity.CertificatePath, facts.CertificatePath);

        var state = JsonNode.Parse(File.ReadAllText(lab.Identity.StatePath))!.AsObject();
        Assert.Equal(certificate.Thumbprint, state["thumbprint"]!.GetValue<string>());
        Assert.Equal(NativeCapabilityNames.TestSigningSubject, state["subject"]!.GetValue<string>());
        Assert.DoesNotContain("PRIVATE", File.ReadAllText(lab.Identity.StatePath), StringComparison.OrdinalIgnoreCase);

        // The answer's facts carry no key material and no path: thumbprint, subject, expiry.
        var json = facts.ToJson();
        Assert.Equal(["signer_thumbprint", "signer_subject", "signer_not_after"], json.Select(p => p.Key).ToArray());
        Assert.Null(BrowserFindForbidden(json));
    }

    [Fact]
    public void A_second_need_reuses_the_recorded_certificate_by_thumbprint_and_creates_nothing()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var lab = new SigningLab();
        string first;
        using (var certificate = lab.Identity.Acquire(out var created))
        {
            Assert.True(created.Created);
            first = certificate.Thumbprint;
        }

        using var again = lab.Identity.Acquire(out var reused);
        Assert.False(reused.Created);
        Assert.Equal(first, again.Thumbprint);
        Assert.Single(lab.StoreContents());
        Assert.Equal(first, lab.Identity.Describe()!.Thumbprint);

        // A second identity object over the same store and state — a companion restart — finds it too.
        var restarted = new OwnerSigningIdentity(lab.Options);
        using var afterRestart = restarted.Acquire(out var restartedFacts);
        Assert.False(restartedFacts.Created);
        Assert.Equal(first, afterRestart.Thumbprint);
        Assert.True(afterRestart.HasPrivateKey);
    }

    [Fact]
    public void Describe_creates_nothing_when_there_is_no_identity_yet()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var lab = new SigningLab();
        Assert.Null(lab.Identity.Describe());
        Assert.False(System.IO.Directory.Exists(lab.Directory));
    }

    [Fact]
    public void An_identity_within_the_renewal_margin_is_replaced_and_the_replacement_is_new_and_untrusted()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var clock = new ManualTimeProvider();
        clock.Advance(DateTimeOffset.UtcNow - clock.GetUtcNow());
        using var lab = new SigningLab(clock);
        string first;
        using (var certificate = lab.Identity.Acquire(out _))
        {
            first = certificate.Thumbprint;
            lab.TrustOnThisMachine(first);
            Assert.True(lab.Identity.IsTrusted(first));
        }

        // Still usable 699 days in; replaced at 701 (the margin is 30 days of a 730-day life).
        clock.Advance(TimeSpan.FromDays(699));
        using (var stillFirst = lab.Identity.Acquire(out var facts))
        {
            Assert.False(facts.Created);
            Assert.Equal(first, stillFirst.Thumbprint);
        }

        clock.Advance(TimeSpan.FromDays(2));
        using var renewed = lab.Identity.Acquire(out var renewedFacts);
        Assert.True(renewedFacts.Created);
        Assert.NotEqual(first, renewed.Thumbprint);
        Assert.False(lab.Identity.IsTrusted(renewed.Thumbprint));
        Assert.Equal(renewed.Thumbprint, lab.Identity.Describe()!.Thumbprint);
        Assert.Equal(renewed.RawData, File.ReadAllBytes(lab.Identity.CertificatePath));
    }

    [Fact]
    public void A_recorded_thumbprint_that_is_no_longer_in_the_store_is_replaced_not_trusted_blindly()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        using var lab = new SigningLab();
        string first;
        using (var certificate = lab.Identity.Acquire(out _))
        {
            first = certificate.Thumbprint;
        }

        using (var store = new X509Store(lab.StoreName, StoreLocation.CurrentUser))
        {
            store.Open(OpenFlags.ReadWrite);
            foreach (var certificate in store.Certificates)
            {
                using var rsa = certificate.GetRSAPrivateKey() as RSACng;
                rsa?.Key.Delete();
                store.Remove(certificate);
            }
        }

        Assert.Null(lab.Identity.Describe());
        using var replaced = lab.Identity.Acquire(out var facts);
        Assert.True(facts.Created);
        Assert.NotEqual(first, replaced.Thumbprint);
    }

    [Fact]
    public void A_certificate_of_the_wrong_shape_is_not_usable_for_the_reason_named()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var options = SigningIdentityOptions.Owner();
        var now = DateTimeOffset.UtcNow;
        using var rsa = RSA.Create(2048);

        X509Certificate2 Make(string subject, bool ca, string eku, TimeSpan life)
        {
            var request = new CertificateRequest(subject, rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
            request.CertificateExtensions.Add(new X509BasicConstraintsExtension(ca, false, 0, true));
            request.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension([new Oid(eku)], false));
            return request.CreateSelfSigned(now.AddMinutes(-5), now + life);
        }

        using var wrongSubject = Make("CN=Someone Else", false, NativeCapabilityNames.CodeSigningOid, TimeSpan.FromDays(365));
        Assert.False(OwnerSigningIdentity.IsUsable(wrongSubject, options, now, out var reason));
        Assert.Equal("subject differs", reason);

        using var authority = Make(options.Subject, true, NativeCapabilityNames.CodeSigningOid, TimeSpan.FromDays(365));
        Assert.False(OwnerSigningIdentity.IsUsable(authority, options, now, out reason));
        Assert.Equal("not an end-entity code-signing certificate", reason);

        using var serverAuth = Make(options.Subject, false, "1.3.6.1.5.5.7.3.1", TimeSpan.FromDays(365));
        Assert.False(OwnerSigningIdentity.IsUsable(serverAuth, options, now, out reason));
        Assert.Equal("not an end-entity code-signing certificate", reason);

        using var shortLived = Make(options.Subject, false, NativeCapabilityNames.CodeSigningOid, TimeSpan.FromDays(10));
        Assert.False(OwnerSigningIdentity.IsUsable(shortLived, options, now, out reason));
        Assert.Equal("expired or about to expire", reason);

        // Right shape, but its key was generated in memory and is exportable: refused.
        using var exportable = Make(options.Subject, false, NativeCapabilityNames.CodeSigningOid, TimeSpan.FromDays(365));
        Assert.False(OwnerSigningIdentity.IsUsable(exportable, options, now, out reason));
        Assert.Equal("the private key is exportable", reason);
    }

    [Fact]
    public void The_owner_identity_is_never_removed_by_the_companion()
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        var owner = new OwnerSigningIdentity(SigningIdentityOptions.Owner(), trustLookup: _ => false);
        Assert.Throws<InvalidOperationException>(owner.RemoveAllForLab);
        Assert.Equal("My", SigningIdentityOptions.Owner().StoreName);
        Assert.EndsWith(Path.Combine("PagentOS", "signing"), SigningIdentityOptions.Owner().StateDirectory, StringComparison.OrdinalIgnoreCase);
    }

    private static string? BrowserFindForbidden(JsonObject json)
        => json.Select(p => p.Key).FirstOrDefault(BrowserCapabilities.IsForbiddenKey);

    private sealed class ToleranceComparer(TimeSpan tolerance) : IEqualityComparer<TimeSpan>
    {
        public bool Equals(TimeSpan x, TimeSpan y) => (x - y).Duration() <= tolerance;

        public int GetHashCode(TimeSpan obj) => 0;
    }
}
