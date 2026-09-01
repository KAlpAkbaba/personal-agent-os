using System.Security.Cryptography;
using System.Text;
using PagentOS.Agent.Core.Security;

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

    /// <summary>
    /// Load the device key, or create one.
    ///
    /// <paramref name="developerRun"/> selects the posture for a NEW key: the installed
    /// service protects it as machine material (SYSTEM read, Administrators full, nobody
    /// else), while a developer console run also keeps access for the account that started
    /// it — because there, the owner IS the service account. Named explicitly rather than
    /// inferred, and false by default, so production cannot inherit the weaker posture by
    /// forgetting an argument.
    /// </summary>
    public static DeviceIdentity LoadOrCreate(string keyFilePath, bool developerRun = false)
    {
        if (File.Exists(keyFilePath))
        {
            var key = ECDsa.Create();
            try
            {
                key.ImportFromPem(File.ReadAllText(keyFilePath));
                return new DeviceIdentity(key);
            }
            catch (UnauthorizedAccessException ex)
            {
                key.Dispose();
                // The exact failure that killed the service under LocalSystem, turned from a
                // bare access-denied into something an operator can act on. The report names
                // principals and rights only — never a byte of the key.
                var report = MachineMaterial.Inspect(keyFilePath, MachineMaterialKind.Secret);
                throw new DeviceIdentityAccessException(
                    $"the device private key exists but this account cannot read it. {report.Describe()}. " +
                    "Repair it with scripts\\repair-device-material.ps1 (elevated); the key itself is preserved.",
                    ex);
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
            File.Move(tempPath, keyFilePath, overwrite: true);
            // Protected after the move, not before: the final DACL deliberately excludes the
            // creating account, and File.Move needs DELETE on the source, so protecting first
            // makes the move fail for any non-administrator. The temp file lives in the data
            // directory, which is itself machine-protected, so it inherits the same
            // restriction in the meantime.
            // Explicit, never the process-wide posture: the key is the one file where an
            // accidental fallback would be worst, so the caller always says which it means.
            MachineMaterial.Protect(keyFilePath, MachineMaterialKind.Secret, includeCurrentUser: developerRun);

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

    /// <summary>
    /// Is the key present and protected as service-owned machine material? Used by the
    /// service's startup preflight so a misprotected key is diagnosed rather than thrown.
    /// </summary>
    public static MachineMaterialReport InspectKey(string keyFilePath, bool? developerRun = null)
        => MachineMaterial.Inspect(keyFilePath, MachineMaterialKind.Secret, developerRun);

    /// <summary>
    /// Repair the key's protection in place, preserving the key itself. Returns true when
    /// something was changed. Requires an account that can rewrite the descriptor —
    /// Administrators, or the file's owner.
    /// </summary>
    public static bool RepairKeyProtection(string keyFilePath, bool? developerRun = null)
        => MachineMaterial.Repair(keyFilePath, MachineMaterialKind.Secret, developerRun);
}

/// <summary>
/// The device key exists but the current account cannot read it. Distinct from a corrupt key:
/// this one is repaired by fixing an ACL, never by issuing a new identity.
/// </summary>
public sealed class DeviceIdentityAccessException : Exception
{
    public DeviceIdentityAccessException(string message, Exception inner) : base(message, inner)
    {
    }
}
