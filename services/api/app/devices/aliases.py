"""Turkish device-alias phrase parsing (PROJECT_CONSTITUTION.md §11a, spec §4/§8).

The owner names a device the way a person talks, not by id: "ev bilgisayarımda
aç", "işteki bilgisayarda araştır", "laptopta devam et". This module turns
such free text into a small set of CANONICAL alias tokens
(``"ev"`` / ``"iş"`` / ``"ofis"`` / ``"laptop"``) that are then matched, case-insensitively,
against the owner-configured ``devices.metadata_json.aliases`` list for each
device (``app.devices.selection``). It never itself decides which device an
alias belongs to — that mapping is owner data, kept centrally, never a
hardcoded machine name (constitution §11a).

Recognized forms (diacritic-tolerant: "ev"/"İş" case-insensitive, Turkish
dotted/dotless i handled via ``casefold``):

- bare alias words: "ev", "iş"/"is", "ofis", "laptop", "dizüstü"
- noun forms: "ev bilgisayarı", "iş bilgisayarı", "ofis bilgisayarı"
- locative forms, closed: "evde", "evdeki", "işte", "işteki", "ofiste", "ofisteki"
- verb-phrase forms with a locative suffix + a verb the owner would say to
  delegate work to a device: "<alias>-de/-da aç/çalıştır/araştır/devam et",
  e.g. "ev bilgisayarında aç", "laptopta devam et", "işteki bilgisayarda
  araştır" (the noun/locative patterns already absorb the trailing suffix
  because they end in ``\\w*``, which also swallows the verb attached without
  a space is never produced by Turkish grammar, so verbs are not matched
  explicitly — only used as documentation of the phrase shapes this parser
  must accept, and are exercised as full phrases in tests).

Anything not recognized returns ``None`` (never guessed).
"""

from __future__ import annotations

import re

ALIAS_EV = "ev"
ALIAS_IS = "iş"
ALIAS_LAPTOP = "laptop"
ALIAS_OFIS = "ofis"

# Every locative is a CLOSED form, never a stem followed by an open word tail (ADR-0205).
# The open forms read every conjugation of "istemek" as "at work" - "istediğim videoyu aç"
# selected the device aliased "iş" - and "evden çıkınca" as "at home". Turkish hangs its
# meaning on the suffix, so a stem match is a guess about the suffix; with one device the
# guess could not pick a wrong machine, with two it can.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (ALIAS_EV, re.compile(r"\bev\s*bilgisayar\w*\b")),
    (ALIAS_EV, re.compile(r"\bevdeki\w*\b")),
    (ALIAS_EV, re.compile(r"\bevimde(?:ki\w*)?\b")),
    (ALIAS_EV, re.compile(r"\bevde(?:yken)?\b")),
    (ALIAS_EV, re.compile(r"^\s*ev\s*$")),
    (ALIAS_IS, re.compile(r"\bi[şs]\s*bilgisayar\w*\b")),
    (ALIAS_IS, re.compile(r"\bi[şs]teki\w*\b")),
    (ALIAS_IS, re.compile(r"\bi[şs]te(?:yken)?\b")),
    (ALIAS_IS, re.compile(r"^\s*i[şs]\s*$")),
    (ALIAS_OFIS, re.compile(r"\bofis\s*bilgisayar\w*\b")),
    (ALIAS_OFIS, re.compile(r"\bofisteki\w*\b")),
    (ALIAS_OFIS, re.compile(r"\bofisimde(?:ki\w*)?\b")),
    (ALIAS_OFIS, re.compile(r"\bofiste(?:yken)?\b")),
    (ALIAS_OFIS, re.compile(r"^\s*ofis\s*$")),
    (ALIAS_LAPTOP, re.compile(r"\blaptop\w*\b")),
    (ALIAS_LAPTOP, re.compile(r"\bdiz[üu]st[üu]\w*\b")),
)


def normalize(text: str) -> str:
    return text.strip().casefold()


def extract_alias(text: str) -> str | None:
    """Extract a canonical alias token from free text, or ``None``."""
    lowered = normalize(text)
    if not lowered:
        return None
    for canonical, pattern in _PATTERNS:
        if pattern.search(lowered):
            return canonical
    return None


def alias_matches(device_aliases: list[str], target_text: str) -> bool:
    """True when any of a device's configured aliases matches ``target_text``.

    Two ways to match: the target text (after alias-phrase extraction) equals
    one of the device's own canonical aliases, OR — for a device whose owner
    configured a free-form alias like "eski masaüstü" — a direct
    case-insensitive substring/equality match against the raw target text.
    """
    if not device_aliases:
        return False
    extracted = extract_alias(target_text)
    normalized_target = normalize(target_text)
    for alias in device_aliases:
        normalized_alias = normalize(alias)
        if not normalized_alias:
            continue
        if extracted is not None and normalized_alias == extracted:
            return True
        if normalized_alias == normalized_target:
            return True
    return False


__all__ = [
    "ALIAS_EV",
    "ALIAS_IS",
    "ALIAS_LAPTOP",
    "ALIAS_OFIS",
    "alias_matches",
    "extract_alias",
    "normalize",
]
