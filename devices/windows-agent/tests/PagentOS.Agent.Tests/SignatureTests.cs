using System.Formats.Asn1;
using System.Security.Cryptography;
using System.Text;
using PagentOS.Agent.Core.Identity;
using PagentOS.Agent.Tests.Support;
using Xunit;

namespace PagentOS.Agent.Tests;

public class SignatureTests
{
    [Fact]
    public void Signature_verifies_with_independent_p1363_verifier()
    {
        var dir = TestPaths.NewTempDir();
        using var identity = DeviceIdentity.LoadOrCreate(Path.Combine(dir, "device.key"));

        var nonce = RandomNumberGenerator.GetBytes(32);
        var deviceId = Guid.NewGuid().ToString();
        var derSignature = identity.Sign(nonce, deviceId);

        // Independent verification: manually decode the DER SEQUENCE(r, s) into raw P1363 form
        // and verify with a fresh public-key-only ECDsa using the default (P1363) format.
        var p1363 = DerSignatureToP1363(derSignature);
        using var publicKey = ECDsa.Create();
        publicKey.ImportSubjectPublicKeyInfo(Convert.FromBase64String(identity.PublicKeySpkiBase64), out _);

        var deviceIdBytes = Encoding.UTF8.GetBytes(deviceId);
        var data = new byte[nonce.Length + deviceIdBytes.Length];
        nonce.CopyTo(data, 0);
        deviceIdBytes.CopyTo(data, nonce.Length);

        Assert.True(publicKey.VerifyData(data, p1363, HashAlgorithmName.SHA256));

        // Tampered data must fail.
        data[0] ^= 0xFF;
        Assert.False(publicKey.VerifyData(data, p1363, HashAlgorithmName.SHA256));
    }

    [Fact]
    public void Signature_binds_device_id()
    {
        var dir = TestPaths.NewTempDir();
        using var identity = DeviceIdentity.LoadOrCreate(Path.Combine(dir, "device.key"));
        var nonce = RandomNumberGenerator.GetBytes(32);
        var signature = identity.Sign(nonce, "device-a");

        using var publicKey = ECDsa.Create();
        publicKey.ImportSubjectPublicKeyInfo(Convert.FromBase64String(identity.PublicKeySpkiBase64), out _);
        var wrongData = nonce.Concat(Encoding.UTF8.GetBytes("device-b")).ToArray();
        Assert.False(publicKey.VerifyData(wrongData, signature, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence));
    }

    [Fact]
    public void Key_persists_across_reloads()
    {
        var dir = TestPaths.NewTempDir();
        var keyPath = Path.Combine(dir, "device.key");
        string firstSpki;
        using (var first = DeviceIdentity.LoadOrCreate(keyPath))
        {
            firstSpki = first.PublicKeySpkiBase64;
        }

        using var second = DeviceIdentity.LoadOrCreate(keyPath);
        Assert.Equal(firstSpki, second.PublicKeySpkiBase64);
    }

    [Fact]
    public void Fresh_keys_differ_per_device()
    {
        using var a = DeviceIdentity.LoadOrCreate(Path.Combine(TestPaths.NewTempDir(), "device.key"));
        using var b = DeviceIdentity.LoadOrCreate(Path.Combine(TestPaths.NewTempDir(), "device.key"));
        Assert.NotEqual(a.PublicKeySpkiBase64, b.PublicKeySpkiBase64);
    }

    private static byte[] DerSignatureToP1363(byte[] der)
    {
        var reader = new AsnReader(der, AsnEncodingRules.DER);
        var sequence = reader.ReadSequence();
        var r = sequence.ReadIntegerBytes().ToArray();
        var s = sequence.ReadIntegerBytes().ToArray();
        sequence.ThrowIfNotEmpty();
        reader.ThrowIfNotEmpty();

        var result = new byte[64];
        CopyFixed(r, result, 0);
        CopyFixed(s, result, 32);
        return result;

        static void CopyFixed(byte[] integer, byte[] destination, int offset)
        {
            var span = integer.AsSpan();
            while (span.Length > 32)
            {
                Assert.Equal(0, span[0]); // only leading zero padding may be stripped
                span = span[1..];
            }

            span.CopyTo(destination.AsSpan(offset + (32 - span.Length)));
        }
    }
}
