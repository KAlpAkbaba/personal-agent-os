"""B33 req 473: the Cloud Core's signing half, read against the device's SOURCE.

The Cloud Core recognises the device's two install refusals by their first word, scaffolds a
Publisher the device's signer must equal byte for byte, and copies answer keys the device
writes. Both suites could stay green while those drifted, which this repository has recorded
more than once, so this test reads the C# files and fails when a word the Cloud Core relies on
is not the word the device writes. A missing file FAILS (a guard that reads nothing and passes
is worse than no guard).
"""

from __future__ import annotations

import re
from pathlib import Path

from app.nativefactory import device_lifecycle, signing
from app.nativefactory.device_build import signature_facts
from app.voice.realtime_sessions import tools_native

_AGENT = Path("devices") / "windows-agent" / "src"


def _find(relative: Path) -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / relative
        if candidate.is_file():
            return candidate
    raise AssertionError(f"{relative} was not found above {__file__}")


def _source(*parts: str) -> str:
    return _find(_AGENT.joinpath(*parts)).read_text(encoding="utf-8")


def _csharp_const(text: str, name: str) -> str:
    match = re.search(rf'public const string {name} = @?"([^"]*)";', text)
    assert match, f"{name} is not declared"
    return match.group(1)


def test_the_markers_subject_and_script_are_the_device_s_own_words() -> None:
    constants = _source("PagentOS.Agent.Core", "Protocol", "ProtocolConstants.cs")
    assert _csharp_const(constants, "UntrustedSignerMarker") == signing.DEVICE_UNTRUSTED_MARKER
    assert _csharp_const(constants, "UnsignedPackageMarker") == signing.DEVICE_UNSIGNED_MARKER
    assert _csharp_const(constants, "TestSigningSubject") == signing.TEST_SIGNING_SUBJECT
    assert _csharp_const(constants, "TrustScript") == signing.TRUST_SCRIPT
    assert _csharp_const(constants, "SigningModeTestCertificate") == signing.MODE_TEST_CERTIFICATE
    assert _csharp_const(constants, "SigningModeUnsigned") == signing.MODE_UNSIGNED
    assert _csharp_const(constants, "SigningModeOwnerCertificate") == signing.MODE_OWNER_CERTIFICATE
    assert signing.TRUST_SCRIPT.replace("\\", "/") in signing.TRUST_COMMAND.replace("\\", "/")


def test_the_install_kinds_and_the_proof_keys_are_the_ones_the_device_writes() -> None:
    lifecycle = _source("PagentOS.SessionCompanion", "Projects", "NativeLifecycle.cs")
    assert _csharp_const(lifecycle, "InstallKindMsix") == device_lifecycle.INSTALL_KIND_MSIX
    assert _csharp_const(lifecycle, "InstallKindShortcut") == device_lifecycle.INSTALL_KIND_SHORTCUT
    # The proofs the Cloud Core accepts are keys the device sets from what it READ.
    for key in ("package_registered", "shortcut_exists", "signature_intact", "verify_status"):
        assert f'["{key}"]' in lifecycle, key
    # The refusals start with the markers (the Cloud Core matches the first word only).
    assert "{NativeCapabilityNames.UntrustedSignerMarker}: " in lifecycle
    assert "{NativeCapabilityNames.UnsignedPackageMarker}: " in lifecycle
    assert "ErrorClasses.PostconditionFailed" in lifecycle


def test_every_signature_key_the_cloud_core_copies_is_one_the_device_writes() -> None:
    lifecycle = _source("PagentOS.SessionCompanion", "Projects", "NativeLifecycle.cs")
    identity = _source("PagentOS.SessionCompanion", "Native", "OwnerSigningIdentity.cs")
    device_keys = set(re.findall(r'\["([a-z_]+)"\]', lifecycle + identity))
    copied = set(
        signature_facts(
            {
                "signed": True,
                "signing_mode": "test_certificate",
                "signer_thumbprint": "A" * 40,
                "signer_subject": signing.TEST_SIGNING_SUBJECT,
                "signer_not_after": "2028-01-01T00:00:00Z",
                "trusted": False,
            }
        )
    )
    assert copied <= device_keys, copied - device_keys
    # ...and every raw signature key the voice tool strips before storing is a device key.
    assert tools_native._SIGNATURE_KEYS <= device_keys, tools_native._SIGNATURE_KEYS - device_keys
