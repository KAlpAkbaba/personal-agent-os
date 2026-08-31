using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;

namespace PagentOS.Agent.Core.Identity;

/// <summary>
/// Device ECDSA P-256 identity. The private key is generated on first run and stored as an
/// unencrypted PKCS#8 PEM restricted to the current user via OS file ACLs (DPAPI hardening is a
/// later milestone per DEVICE_PROTOCOL.md §2). The private key never leaves the device.
/// </summary>
public sealed class DeviceIdentity : IDisposable
{
    private readonly ECDsa _key;

    private DeviceIdentity(ECDsa key)
    {
        _key = key;
    }

    /// <summary>Base64 of the SubjectPublicKeyInfo (SPKI) DER encoding of the public key.</summary>
    public string PublicKeySpkiBase64 => Convert.ToBase64String(_key.ExportSubjectPublicKeyInfo());

    public static DeviceIdentity LoadOrCreate(string keyFilePath)
    {
        if (File.Exists(keyFilePath))
        {
            var key = ECDsa.Create();
            try
            {
                key.ImportFromPem(File.ReadAllText(keyFilePath));
                return new DeviceIdentity(key);
            }
            catch
            {
                key.Dispose();
                throw;
            }
        }

        var created = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        try
        {
            var directory = Path.GetDirectoryName(Path.GetFullPath(keyFilePath));
            if (!string.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var tempPath = keyFilePath + ".tmp";
            File.WriteAllText(tempPath, created.ExportPkcs8PrivateKeyPem());
            RestrictToCurrentUser(tempPath);
            File.Move(tempPath, keyFilePath, overwrite: true);
            return new DeviceIdentity(created);
        }
        catch
        {
            created.Dispose();
            throw;
        }
    }

    /// <summary>
    /// ECDSA-SHA256 signature over nonce_bytes || device_id_utf8 (DEVICE_PROTOCOL.md §3).
    /// Signature bytes are DER-encoded (RFC 3279 SEQUENCE of r, s), the default encoding
    /// expected by the Python `cryptography` verifier on the broker side.
    /// </summary>
    public byte[] Sign(byte[] nonceBytes, string deviceId)
    {
        var deviceIdBytes = Encoding.UTF8.GetBytes(deviceId);
        var data = new byte[nonceBytes.Length + deviceIdBytes.Length];
        nonceBytes.CopyTo(data, 0);
        deviceIdBytes.CopyTo(data, nonceBytes.Length);
        return _key.SignData(data, HashAlgorithmName.SHA256, DSASignatureFormat.Rfc3279DerSequence);
    }

    public void Dispose() => _key.Dispose();

    private static void RestrictToCurrentUser(string path)
    {
        if (!OperatingSystem.IsWindows())
        {
            return;
        }

        try
        {
            var user = WindowsIdentity.GetCurrent().User;
            if (user is null)
            {
                return;
            }

            var info = new FileInfo(path);
            var security = new FileSecurity();
            security.SetAccessRuleProtection(isProtected: true, preserveInheritance: false);
            security.AddAccessRule(new FileSystemAccessRule(user, FileSystemRights.FullControl, AccessControlType.Allow));
            info.SetAccessControl(security);
        }
        catch (Exception)
        {
            // Best-effort in dev; the key file still lives under the user profile.
        }
    }
}
