"""Risk markers: the words on a control that make acting on it irreversible
(contract v1.6 item 10, ADR-0207).

Until v1.6 the HIGH_IMPACT markers were matched as SUBSTRINGS of the accessible
name. ``"sil"`` is inside "silver" and "silgi", ``"ode"`` inside "mode" and
"kodet", ``"pay"`` inside "paylaş" (share), ``"send"`` inside "sending options".
That errs on the safe side - it asks too often - but a loop that stops to ask
about a "Paylaş" button teaches its owner to say yes without listening, and
the list missed what is actually written on a Turkish checkout page
("siparişi tamamla", "onayla ve öde", "abone ol"). This is the repository's
recurring Turkish defect - a stem match is a guess about the suffix - so the
rule here is the closed one: WHOLE words and WHOLE phrases, every inflected
form that should match written out.

``HIGH_IMPACT`` and ``EXTERNAL_COMMUNICATION`` are written verbatim from
``packages/protocol/browser-risk-markers.json`` - the ONE source both sides
read - and ``tests/unit/test_risk_markers.py`` holds the two byte-identical,
exactly as ``injection.MARKERS`` is held to its own JSON file. The worker
carries the list in its own source because an installed worker has no
repository beside it.

Folding (both sides, the same steps in the same order): NFKC, zero-width
characters removed, whitespace runs collapsed to one space, ``casefold``, then
the Turkish letters folded to ASCII so that a name typed without them
("Odeme yap", "SATIN AL") is the same name. The dotted capital İ is folded
BEFORE casefold, which would otherwise turn it into "i" plus a combining dot.
"""

from __future__ import annotations

import re
from typing import Final

from .injection import normalize_for_markers

HIGH_IMPACT: Final[tuple[str, ...]] = (
    "buy",
    "buy now",
    "purchase",
    "pay",
    "pay now",
    "checkout",
    "check out",
    "place order",
    "place your order",
    "submit order",
    "order now",
    "complete order",
    "complete purchase",
    "confirm order",
    "confirm purchase",
    "confirm payment",
    "confirm and pay",
    "subscribe",
    "start subscription",
    "start free trial",
    "cancel subscription",
    "delete",
    "remove",
    "send",
    "transfer",
    "send money",
    "close account",
    "satın al",
    "satın alın",
    "hemen al",
    "şimdi al",
    "öde",
    "ödeyin",
    "ödeme yap",
    "ödemeyi tamamla",
    "ödemeyi onayla",
    "ödemeye geç",
    "onayla ve öde",
    "sipariş ver",
    "siparişi tamamla",
    "siparişi onayla",
    "alışverişi tamamla",
    "satın almayı onayla",
    "abone ol",
    "aboneliği başlat",
    "aboneliği iptal et",
    "ücretsiz denemeyi başlat",
    "sil",
    "silin",
    "kalıcı olarak sil",
    "kaldır",
    "gönder",
    "gönderin",
    "havale",
    "eft",
    "para gönder",
    "transfer et",
    "hesabı kapat",
    "hesabı sil",
)

EXTERNAL_COMMUNICATION: Final[tuple[str, ...]] = (
    "submit",
    "confirm",
    "post",
    "publish",
    "reply",
    "sign up",
    "register",
    "onayla",
    "onaylıyorum",
    "yayınla",
    "paylaş",
    "yanıtla",
    "yorum yap",
    "kaydol",
    "üye ol",
    "kayıt ol",
)

_TURKISH_FOLD: Final = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "I": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ö": "o",
        "Ö": "o",
        "ü": "u",
        "Ü": "u",
        "ç": "c",
        "Ç": "c",
    }
)


def fold(text: str) -> str:
    """The one folding both the control's name and every marker go through."""
    if not text:
        return ""
    return normalize_for_markers(text).translate(_TURKISH_FOLD).casefold().strip()


def _compile(markers: tuple[str, ...]) -> re.Pattern[str]:
    # Longest first, so "onayla ve öde" is tried before "onayla"; the boundaries are
    # "not a letter or a digit on either side" - "\b" would treat an apostrophe suffix
    # ("Sil'in") the same way, which is the intended reading.
    folded = sorted({fold(m) for m in markers if fold(m)}, key=lambda m: (-len(m), m))
    body = "|".join(re.escape(m).replace(r"\ ", " ") for m in folded)
    return re.compile(rf"(?<![0-9a-z])(?:{body})(?![0-9a-z])")


_HIGH_IMPACT_RE: Final = _compile(HIGH_IMPACT)
_EXTERNAL_RE: Final = _compile(EXTERNAL_COMMUNICATION)


def is_high_impact(name: str) -> bool:
    """True when the control's accessible name carries a HIGH_IMPACT word or phrase."""
    return _HIGH_IMPACT_RE.search(fold(name)) is not None


def is_external_communication(name: str) -> bool:
    """True when the name says the control SENDS something (and is not HIGH_IMPACT)."""
    return _EXTERNAL_RE.search(fold(name)) is not None


def first_marker(name: str) -> str | None:
    """The folded marker that matched, for evidence - never the name itself."""
    folded = fold(name)
    match = _HIGH_IMPACT_RE.search(folded) or _EXTERNAL_RE.search(folded)
    return match.group(0) if match else None


__all__ = [
    "EXTERNAL_COMMUNICATION",
    "HIGH_IMPACT",
    "first_marker",
    "fold",
    "is_external_communication",
    "is_high_impact",
]
