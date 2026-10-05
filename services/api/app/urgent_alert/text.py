"""What an alarm may say: nothing personal, ever.

Pushover processes messages on US servers and Apple APNs carries them, so the alarm is a
doorbell, not a letter: a fixed title, ONE word from a closed category list, and a tailnet
link that opens nowhere outside the owner's own network. Everything else is refused - a name,
an amount, an e-mail address, a phone number - so personal data never leaves the country by
way of a lock-screen banner (KVKK).
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import urlsplit

TITLE: Final[str] = "JARVIS: önemli"
CATEGORIES: Final[tuple[str, ...]] = ("Aktivra", "ev", "haber", "sistem")

#: Pushover's own limits (pushover.net/api, read 2026-10-05).
MAX_BODY: Final[int] = 1024
MAX_URL: Final[int] = 512
TAILNET_SUFFIX: Final[str] = ".ts.net"

#: Checked before the closed list so a refusal names WHAT it caught, and a test can prove each
#: pattern on its own rather than only that the list said no.
_AMOUNT = re.compile(r"₺|\b(?:TL|TRY|lira)\b|\d[\d.,]*\s*(?:TL|TRY|lira)", re.IGNORECASE)
_EMAIL = re.compile(r"@")
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_DIGIT = re.compile(r"\d")


class TextRefused(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def refusal(category: str) -> str | None:
    """Why ``category`` may not be an alarm body, or ``None`` when it may."""
    if len(category) > MAX_BODY:
        return "too_long"
    if _AMOUNT.search(category):
        return "amount"
    if _EMAIL.search(category):
        return "email"
    if _PHONE.search(category):
        return "phone"
    if _DIGIT.search(category):
        return "digits"
    if len(category.split()) > 1:
        return "more_than_one_word"
    if category not in CATEGORIES:
        return "not_a_category"
    return None


def link_refusal(link: str) -> str | None:
    if len(link) > MAX_URL:
        return "link_too_long"
    parts = urlsplit(link)
    if parts.scheme != "https":
        return "link_not_https"
    if parts.username or parts.password:
        return "link_has_userinfo"
    if not (parts.hostname or "").endswith(TAILNET_SUFFIX):
        return "link_not_tailnet"
    return None


def compose(category: str, link: str) -> tuple[str, str, str]:
    """``(title, body, url)`` for an alarm, or :class:`TextRefused`."""
    reason = refusal(category) or link_refusal(link)
    if reason:
        raise TextRefused(reason)
    return TITLE, category, link


__all__ = ["CATEGORIES", "TITLE", "TextRefused", "compose", "link_refusal", "refusal"]
