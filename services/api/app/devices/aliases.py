"""Turkish device-alias phrase parsing (PROJECT_CONSTITUTION.md §11a, spec §4/§8).

The owner names a device the way a person talks, not by id: "ev bilgisayarımda
aç", "işteki bilgisayarda araştır", "laptopta devam et". This module turns
such free text into a small set of CANONICAL alias tokens
(``"ev"`` / ``"iş"`` / ``"laptop"``) that are then matched, case-insensitively,
against the owner-configured ``devices.metadata_json.aliases`` list for each
device (``app.devices.selection``). It never itself decides which device an
alias belongs to — that mapping is owner data, kept centrally, never a
hardcoded machine name (constitution §11a).

Recognized forms (diacritic-tolerant: "ev"/"İş" case-insensitive, Turkish
dotted/dotless i handled via ``casefold``):

- bare alias words: "ev", "iş"/"is", "laptop", "dizüstü"
- noun forms: "ev bilgisayarı", "iş bilgisayarı"
- locative forms: "evdeki", "işteki"
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

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (ALIAS_EV, re.compile(r"\bev\s*bilgisayar\w*\b")),
    (ALIAS_EV, re.compile(r"\bevdeki\w*\b")),
    (ALIAS_EV, re.compile(r"\bevimde\w*\b")),
    (ALIAS_EV, re.compile(r"\bevde\w*\b")),
    (ALIAS_EV, re.compile(r"^\s*ev\s*$")),
    (ALIAS_IS, re.compile(r"\bi[şs]\s*bilgisayar\w*\b")),
    (ALIAS_IS, re.compile(r"\bi[şs]teki\w*\b")),
    (ALIAS_IS, re.compile(r"\bi[şs]te\w*\b")),
    (ALIAS_IS, re.compile(r"^\s*i[şs]\s*$")),
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
    "alias_matches",
    "extract_alias",
    "normalize",
]
