using System.Runtime.Versioning;
using System.Security.Cryptography.X509Certificates;

namespace PagentOS.SessionCompanion.Native;

/// <summary>
/// B33 requirement 473: what the lifecycle needs to sign and to install a signed package — the
/// owner's self-signed identity, the in-process MSIX signer and Windows' per-user deployment.
/// Production builds it with <see cref="ForOwner"/>; nothing is created until a package is
/// actually signed. A lab may wrap the signer (to prove that a signature which does not read
/// back is never answered as one); the read-back itself is never replaceable.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class NativeSigning(OwnerSigningIdentity identity, IMsixDeployer deployer, Action<string, X509Certificate2>? signMsix = null)
{
    public OwnerSigningIdentity Identity { get; } = identity;

    public IMsixDeployer Deployer { get; } = deployer;

    /// <summary>Signs the package at the path in place; <see cref="PackageSigner.SignMsix"/> unless a lab wrapped it.</summary>
    public Action<string, X509Certificate2> SignMsix { get; } = signMsix ?? PackageSigner.SignMsix;

    public static NativeSigning ForOwner() => new(new OwnerSigningIdentity(SigningIdentityOptions.Owner()), new WindowsPackageDeployer());
}
