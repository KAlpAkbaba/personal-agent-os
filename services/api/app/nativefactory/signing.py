"""B33 req 472/473: the signing policy, written down as code rather than implied.

What is true today, and enforced on both halves:

- Every artefact this factory produces is UNSIGNED. The device refuses the signing programs
  by name at manifest-parse time (``NativeCapabilityNames.ForbiddenPrograms``: signtool,
  certutil, certmgr, makecert, pvk2pfx, certreq) and ``makeappx`` runs with ``/nv`` so no
  validation can demand a signature; the Cloud Core's packager stamps
  ``UNSIGNED_PUBLISHER``. A portable zip runs as it is; an unsigned MSIX installs only where
  its publisher is trusted, which on the owner's machine means: not without the owner's own
  certificate decision.
- Signing with the owner's real code-signing identity is a decision only the owner can make
  (a certificate is a credential; this system never creates, stores or uses one on its own),
  and a self-signed TEST certificate is the same decision in a smaller coat: it has to be
  installed into the owner's trust store to mean anything, which is a UAC-gated change to the
  machine's security settings. Both are therefore ``READY_FOR_OWNER`` here: the policy names
  them, the factory speaks them, nothing implements them until the owner says so.

``native_signing_mode`` in settings selects the mode; only ``unsigned`` is implemented, and
asking for another is a typed refusal that names the owner's checkpoint - never a package
quietly produced unsigned under a name that promised a signature.
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

#: The modes this build implements. The other two are the owner's checkpoint (472/473).
IMPLEMENTED_MODES: Final[frozenset[str]] = frozenset({MODE_UNSIGNED})

ERROR_SIGNING_NOT_DECIDED: Final = "signing_not_decided"

SPEECH_UNSIGNED_PORTABLE: Final = (
    "Taşınabilir paket imzasız efendim; olduğu gibi çalışır, imza istemez."
)
SPEECH_UNSIGNED_MSIX: Final = (
    "MSIX imzasız efendim: imzalamak bir sertifika ister ve sertifika kararı sizin. "
    "İmzasız paket yalnız yayıncısına güvenilen makinelerde kurulur; bu makinede kurmak "
    "için ya taşınabilir paketi kullanın ya da bir imza kararı verin."
)
SPEECH_SIGNING_NOT_DECIDED: Final = (
    "İmza politikası '{mode}' henüz uygulanmadı efendim: bir sertifika kararı gerektiriyor "
    "ve o karar sizin. Bu yüzden imzalı paket ürettim demiyorum; paketler imzasız çıkıyor."
)


@dataclass(frozen=True, slots=True)
class SigningPolicy:
    mode: str = MODE_UNSIGNED

    @property
    def implemented(self) -> bool:
        return self.mode in IMPLEMENTED_MODES

    @property
    def signs(self) -> bool:
        """Whether an artefact under this policy carries a signature. Never, today."""
        return False

    def speech_for(self, kind: str) -> str:
        """What the owner hears about the signature of a package of ``kind``."""
        if self.mode != MODE_UNSIGNED:
            return SPEECH_SIGNING_NOT_DECIDED.format(mode=self.mode)
        return SPEECH_UNSIGNED_MSIX if kind == "msix" else SPEECH_UNSIGNED_PORTABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "implemented": self.implemented,
            "signs": self.signs,
            "owner_decision_required": self.mode != MODE_UNSIGNED,
            "device_refuses_signers": True,
        }


def policy_from_settings(settings: Any) -> SigningPolicy:
    mode = str(getattr(settings, "native_signing_mode", MODE_UNSIGNED) or MODE_UNSIGNED)
    if mode not in SIGNING_MODES:
        mode = MODE_UNSIGNED
    return SigningPolicy(mode=mode)


__all__ = [
    "ERROR_SIGNING_NOT_DECIDED",
    "IMPLEMENTED_MODES",
    "MODE_OWNER_CERTIFICATE",
    "MODE_TEST_CERTIFICATE",
    "MODE_UNSIGNED",
    "SIGNING_MODES",
    "SPEECH_SIGNING_NOT_DECIDED",
    "SPEECH_UNSIGNED_MSIX",
    "SPEECH_UNSIGNED_PORTABLE",
    "SigningPolicy",
    "policy_from_settings",
]
