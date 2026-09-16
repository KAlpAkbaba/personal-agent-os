"""B33 req 472/473: the signing policy, written down as code rather than implied.

**Owner decision 2026-09-16** (verbatim): "win uygulamada da kendinden imzalı olsun" - the
Windows applications this factory produces are signed with a SELF-SIGNED certificate. So:

- ``test_certificate`` (the DEFAULT) is a real, applied mode. The Cloud Core scaffolds the
  MSIX manifest with ``Publisher`` = :data:`TEST_SIGNING_SUBJECT` and asks the device's
  ``project.package`` for ``signing_mode: "test_certificate"``. The Session Companion signs
  the package IN ITS OWN PROCESS (``SignerSignEx2``) with a self-signed, non-exportable
  identity it keeps in the owner's ``CurrentUser\\My`` store, reads the signature back
  (``WinVerifyTrust`` + the package's own signature block) and only then answers
  ``signed: true`` with ``signer_thumbprint``, ``signer_subject`` and ``trusted``. No
  signing PROGRAM ever runs (``NativeCapabilityNames.ForbiddenPrograms`` still refuses
  signtool and friends by name), no private key ever leaves the device, and no timestamp
  server is contacted.
- Installing an MSIX needs its signer trusted on the machine (``LocalMachine\\TrustedPeople``),
  which is an elevated change the device never makes. That is the owner's ONE step,
  :data:`TRUST_SCRIPT`; until it is taken the install is refused with
  :data:`ERROR_SIGNING_CERT_UNTRUSTED` and the sentence names the script.
- ``unsigned`` stays available as an explicit opt-out (packages carry
  ``UNSIGNED_PUBLISHER`` and an MSIX install is refused as ``package_unsigned``).
- ``owner_certificate`` - the owner's REAL code-signing identity - stays refused by name.
  A real identity is a credential this system never reaches for; packages are produced
  unsigned under that setting and every answer says so.
- A portable zip is never signed, in any mode: the device already read the EXE back and
  hashed it, and signing it afterwards would turn that hash into a statement about a file
  that no longer exists.

Every receipt speaks what the DEVICE answered (``signed``/``trusted``), never what the
policy intended: a device older than B33 ignores ``signing_mode`` and answers
``signed: false``, and the owner hears exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

MODE_UNSIGNED: Final = "unsigned"
MODE_TEST_CERTIFICATE: Final = "test_certificate"
MODE_OWNER_CERTIFICATE: Final = "owner_certificate"
SIGNING_MODES: Final[tuple[str, ...]] = (
    MODE_UNSIGNED,
    MODE_TEST_CERTIFICATE,
    MODE_OWNER_CERTIFICATE,
)

#: The owner's decision of 2026-09-16 (the setting still overrides it).
DEFAULT_MODE: Final = MODE_TEST_CERTIFICATE

#: The modes this build applies. ``owner_certificate`` is refused by name.
IMPLEMENTED_MODES: Final[frozenset[str]] = frozenset({MODE_UNSIGNED, MODE_TEST_CERTIFICATE})

#: The subject of the device's self-signed certificate and therefore the ``Publisher`` of every
#: signed ``AppxManifest.xml``. The MSIX signer refuses a package whose Publisher differs by a
#: single byte; the device half (``NativeCapabilityNames.TestSigningSubject``) is compared with
#: this line by a C# test that reads this file.
TEST_SIGNING_SUBJECT: Final = "CN=PagentOS Owner Test Signing"
TEST_SIGNING_PUBLISHER_DISPLAY: Final = "PagentOS (kendinden imzalı)"

#: The owner's one elevated step (repository-relative), and the exact line to run.
TRUST_SCRIPT: Final = "scripts\\trust-native-signing-cert.ps1"
TRUST_COMMAND: Final = (
    "powershell -NoProfile -ExecutionPolicy Bypass -File .\\scripts\\trust-native-signing-cert.ps1"
)

#: The first word of the device's ``project.install`` refusals (DEVICE_PROTOCOL.md §6n): the
#: wire error carries class, message and retryable only, so the refusal is recognised by these.
DEVICE_UNTRUSTED_MARKER: Final = "signing_cert_untrusted"
DEVICE_UNSIGNED_MARKER: Final = "package_unsigned"

ERROR_SIGNING_CERT_UNTRUSTED: Final = "signing_cert_untrusted"
ERROR_PACKAGE_UNSIGNED: Final = "package_unsigned"
ERROR_SIGNING_MODE_REFUSED: Final = "signing_mode_refused"
#: Kept for readers of receipts written before 2026-09-16; no longer produced.
ERROR_SIGNING_NOT_DECIDED: Final = "signing_not_decided"

SPEECH_UNSIGNED_PORTABLE: Final = (
    "Taşınabilir paket imzasız efendim; olduğu gibi çalışır, imza istemez."
)
SPEECH_UNSIGNED_MSIX: Final = (
    "MSIX imzasız efendim: bu kurulumda imzalama kapalı (native_signing_mode=unsigned). "
    "İmzasız paket bu makinede kurulmaz; taşınabilir paketi kullanabilirsiniz."
)
SPEECH_SIGNED_TRUSTED: Final = (
    "MSIX kendinden imzalı sertifikayla imzalandı efendim ve bu makine sertifikaya "
    "güveniyor; kurulabilir."
)
SPEECH_SIGNED_UNTRUSTED: Final = (
    "MSIX kendinden imzalı sertifikayla imzalandı efendim, ama bu makine sertifikaya henüz "
    "güvenmiyor. Kurabilmem için bir kez yönetici olarak "
    "scripts\\trust-native-signing-cert.ps1 betiğini çalıştırmanız gerekiyor; ben yetki "
    "yükseltmem."
)
SPEECH_SIGNING_NOT_APPLIED: Final = (
    "MSIX imzalanmadı efendim: cihaz imzalı paket döndürmedi, bu yüzden imzalı demiyorum."
)
SPEECH_OWNER_CERTIFICATE_REFUSED: Final = (
    "İmza politikası 'owner_certificate' uygulanmıyor efendim: gerçek imza kimliğiniz size "
    "ait ve ona uzanmam. Bu ayarla paketler imzasız çıkıyor; kendinden imzalı sertifika "
    "için politikayı 'test_certificate' yapın."
)
SPEECH_INSTALL_UNTRUSTED: Final = (
    "{name} kurulamadı efendim: paketi imzalayan sertifikaya bu makine henüz güvenmiyor. "
    "Bir kez yönetici PowerShell'de, depo kökünde şunu çalıştırın: "
    "scripts\\trust-native-signing-cert.ps1. Ben yetki yükseltmem; sonra tekrar kurarım."
)
SPEECH_INSTALL_UNSIGNED: Final = (
    "{name} kurulamadı efendim: MSIX imzalı değil, imzasız paketi kurmam. "
    "Önce imzalı olarak yeniden paketleyeyim mi?"
)


@dataclass(frozen=True, slots=True)
class SigningPolicy:
    mode: str = DEFAULT_MODE

    @property
    def implemented(self) -> bool:
        return self.mode in IMPLEMENTED_MODES

    @property
    def signs(self) -> bool:
        """Whether this policy ASKS the device to sign an MSIX. What was actually signed is
        the device's answer, never this property."""
        return self.mode == MODE_TEST_CERTIFICATE

    @property
    def device_mode(self) -> str:
        """The ``signing_mode`` sent to ``project.package``: only a mode the device applies."""
        return MODE_TEST_CERTIFICATE if self.signs else MODE_UNSIGNED

    @property
    def publisher(self) -> str:
        """The ``Publisher`` the scaffolded manifest names under this policy."""
        from app.nativefactory.packaging import UNSIGNED_PUBLISHER

        return TEST_SIGNING_SUBJECT if self.signs else UNSIGNED_PUBLISHER

    def speech_for(
        self, kind: str, *, signed: bool | None = None, trusted: bool | None = None
    ) -> str:
        """What the owner hears about the signature of a package of ``kind``.

        ``signed``/``trusted`` are the DEVICE's answer; a package is never described as signed
        because the policy asked for it.
        """
        if self.mode == MODE_OWNER_CERTIFICATE:
            return SPEECH_OWNER_CERTIFICATE_REFUSED
        if kind != "msix":
            return SPEECH_UNSIGNED_PORTABLE
        if not self.signs:
            return SPEECH_UNSIGNED_MSIX
        if signed is not True:
            return SPEECH_SIGNING_NOT_APPLIED
        return SPEECH_SIGNED_TRUSTED if trusted is True else SPEECH_SIGNED_UNTRUSTED

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "implemented": self.implemented,
            "signs": self.signs,
            "subject": TEST_SIGNING_SUBJECT if self.signs else None,
            "trust_script": TRUST_SCRIPT if self.signs else None,
            "timestamped": False,
            "owner_decision_required": self.mode == MODE_OWNER_CERTIFICATE,
            "device_runs_no_signer": True,
        }


def policy_from_settings(settings: Any) -> SigningPolicy:
    mode = str(getattr(settings, "native_signing_mode", DEFAULT_MODE) or DEFAULT_MODE)
    if mode not in SIGNING_MODES:
        mode = DEFAULT_MODE
    return SigningPolicy(mode=mode)


def install_refusal(message: str) -> str | None:
    """The policy error a device ``project.install`` refusal means, read from its first word."""
    head = (message or "").strip()
    if head.startswith(DEVICE_UNTRUSTED_MARKER):
        return ERROR_SIGNING_CERT_UNTRUSTED
    if head.startswith(DEVICE_UNSIGNED_MARKER):
        return ERROR_PACKAGE_UNSIGNED
    return None


__all__ = [
    "DEFAULT_MODE",
    "DEVICE_UNSIGNED_MARKER",
    "DEVICE_UNTRUSTED_MARKER",
    "ERROR_PACKAGE_UNSIGNED",
    "ERROR_SIGNING_CERT_UNTRUSTED",
    "ERROR_SIGNING_MODE_REFUSED",
    "ERROR_SIGNING_NOT_DECIDED",
    "IMPLEMENTED_MODES",
    "MODE_OWNER_CERTIFICATE",
    "MODE_TEST_CERTIFICATE",
    "MODE_UNSIGNED",
    "SIGNING_MODES",
    "SPEECH_INSTALL_UNSIGNED",
    "SPEECH_INSTALL_UNTRUSTED",
    "SPEECH_OWNER_CERTIFICATE_REFUSED",
    "SPEECH_SIGNED_TRUSTED",
    "SPEECH_SIGNED_UNTRUSTED",
    "SPEECH_SIGNING_NOT_APPLIED",
    "SPEECH_UNSIGNED_MSIX",
    "SPEECH_UNSIGNED_PORTABLE",
    "TEST_SIGNING_PUBLISHER_DISPLAY",
    "TEST_SIGNING_SUBJECT",
    "TRUST_COMMAND",
    "TRUST_SCRIPT",
    "SigningPolicy",
    "install_refusal",
    "policy_from_settings",
]
