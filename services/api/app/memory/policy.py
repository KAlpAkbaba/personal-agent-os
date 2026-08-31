"""Memory Write Policy (MEMORY_SPEC §5): ignore -> session -> candidate -> durable.

Decision table (deterministic, documented for the lead):

| # | condition (first match wins)                        | decision                          |
|---|-----------------------------------------------------|-----------------------------------|
| 1 | content matches a secret pattern                    | REFUSE (SECRET_REJECTED + audit)  |
| 2 | explicit flag OR explicit owner phrase (en/tr)      | DURABLE, explicit=True, conf=1.0, |
|   |                                                     | actor=OWNER                       |
| 3 | chatty / no-signal content (greeting, ack, filler)  | IGNORE (no row)                   |
| 4 | inferred with stable key OR strong-signal phrase    | CANDIDATE, conf<=0.4, actor=POLICY|
| 5 | any other inferred observation                      | SESSION, conf<=0.4, retention=    |
|   |                                                     | session, actor=POLICY             |

Inferred confidence is always capped at SINGLE_OBSERVATION_MAX_CONFIDENCE for a
single observation; promotion candidate->durable happens only through evidence
accumulation in lifecycle.py (evidence_count >= PROMOTE_MIN_EVIDENCE AND
confidence >= PROMOTE_MIN_CONFIDENCE). Explicit owner instructions bypass the
ladder entirely (MEMORY_SPEC §4: explicit outranks inference).

SECURITY_MODEL §5: secrets are never stored as memory text/value. The guard
refuses API keys, private keys, passwords and bearer tokens by pattern; the
refusal is audited WITHOUT the content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.memory.types import (
    SINGLE_OBSERVATION_MAX_CONFIDENCE,
    Actor,
    MemoryClass,
    RetentionClass,
    WriteStage,
)

# --------------------------------------------------------------- secrets guard

# Named credential patterns (SECURITY_MODEL §5). Matching is on the RAW text
# (case-sensitive where the token prefix is case-sensitive).
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("github_token", re.compile(r"\bghp_[A-Za-z0-9]{20,}")),
    ("github_fine_grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{8,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password_assignment", re.compile(r"(?i)\bpassword\s*[:=]\s*\S+")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    ("generic_api_key", re.compile(r"(?i)\bapi[_-]?key\s*[:=]\s*\S{8,}")),
)


def find_secret(text: str) -> str | None:
    """Return the NAME of the first matching secret pattern (never the match)."""
    for name, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return name
    return None


# ---------------------------------------------------------- explicit detection

# Owner phrases that mean "make this a durable memory now" (tr-TR first-class).
EXPLICIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bremember\b"),
    re.compile(r"(?i)\bfrom now on\b"),
    re.compile(r"(?i)\bi prefer\b"),
    re.compile(r"(?i)\balways use\b"),
    re.compile(r"(?i)hat[ıi]rla"),          # "hatırla" / "hatırlar mısın"
    re.compile(r"(?i)unutma"),               # "unutma"
    re.compile(r"(?i)bundan sonra"),         # "bundan sonra"
    re.compile(r"(?i)tercih ederim"),        # "tercih ederim"
    re.compile(r"(?i)akl[ıi]nda tut"),       # "aklında tut"
)

# Signal words that make an inferred observation candidate-worthy (rule 4).
STRONG_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bprefers?\b"),
    re.compile(r"(?i)\bdecided\b"),
    re.compile(r"(?i)\balways\b"),
    re.compile(r"(?i)\bnever\b"),
    re.compile(r"(?i)\bworkflow\b"),
    re.compile(r"(?i)tercih"),
    re.compile(r"(?i)karar"),
    re.compile(r"(?i)her zaman"),
    re.compile(r"(?i)asla"),
)

# Chatty / no-signal content: greetings, acks, fillers -> never a memory row.
_CHATTY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*(hi|hello|hey|thanks?|thank you|ok(ay)?|yes|no|sure|great|cool)[.!\s]*$"),
    re.compile(
        r"(?i)^\s*(merhaba|selam|te[şs]ekk[üu]r(ler)?|sa[ğg] ?ol|tamam|evet|hay[ıi]r"
        r"|peki|olur)[.!\s]*$"
    ),
    re.compile(r"(?i)^\s*(how are you|nas[ıi]ls[ıi]n)\??\s*$"),
    re.compile(r"(?i)^\s*(good (morning|night)|g[üu]nayd[ıi]n|iyi geceler)[.!\s]*$"),
)

_MIN_SIGNAL_WORDS = 3  # fewer words with no signal phrase -> chatty


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)


def is_explicit_instruction(text: str) -> bool:
    return _matches_any(text, EXPLICIT_PATTERNS)


def is_chatty(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if _matches_any(stripped, _CHATTY_PATTERNS):
        return True
    words = stripped.split()
    if len(words) < _MIN_SIGNAL_WORDS and not _matches_any(stripped, STRONG_SIGNAL_PATTERNS):
        return True
    return False


# ------------------------------------------------------------------- decisions

ACTION_REFUSE = "refuse"
ACTION_IGNORE = "ignore"


@dataclass(slots=True)
class Observation:
    """An incoming statement/observation submitted to the write policy."""

    text: str
    memory_class: MemoryClass = MemoryClass.SEMANTIC
    key: str | None = None
    value: dict[str, Any] = field(default_factory=dict)
    explicit: bool = False  # caller-asserted explicit owner instruction
    confidence_hint: float | None = None
    source: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WriteDecision:
    """Outcome of the write policy for one observation."""

    action: str  # "refuse" | "ignore" | WriteStage value
    explicit: bool = False
    confidence: float = 0.0
    actor: Actor = Actor.POLICY
    retention_class: RetentionClass = RetentionClass.STANDARD
    reason: str = ""
    secret_pattern: str | None = None  # set only for ACTION_REFUSE


def decide(observation: Observation) -> WriteDecision:
    """Apply the decision table to one observation. Pure and deterministic."""
    text = observation.text

    # Scan every caller-supplied surface — text, value AND source/provenance —
    # so a secret cannot be smuggled in through nested payloads (M5 review #3).
    secret = find_secret(text)
    if secret is None and observation.value:
        secret = find_secret(repr(observation.value))
    if secret is None and observation.source:
        secret = find_secret(repr(observation.source))
    if secret is not None:
        return WriteDecision(
            action=ACTION_REFUSE,
            reason="content matches a credential/secret pattern",
            secret_pattern=secret,
        )

    # OWNER authority requires the caller-asserted `explicit` flag, set only by
    # trusted owner-facing surfaces (/remember, an owner UI). A trigger phrase
    # inside arbitrary text is NEVER sufficient by itself: once ingestion
    # pipelines (browser/research/document, M6+) feed text into /observe, a
    # webpage saying "always use ..." must not mint an explicit owner memory
    # (SECURITY_MODEL §6; M5 review #4). Phrase-matched text without the flag
    # is treated as a strong candidate signal, capped like any inference.
    if observation.explicit:
        return WriteDecision(
            action=WriteStage.DURABLE,
            explicit=True,
            confidence=1.0,
            actor=Actor.OWNER,
            retention_class=RetentionClass.STANDARD,
            reason="explicit owner instruction",
        )

    if is_chatty(text):
        return WriteDecision(action=ACTION_IGNORE, reason="chatty/no-signal content")

    hint = observation.confidence_hint
    confidence = min(
        SINGLE_OBSERVATION_MAX_CONFIDENCE,
        hint if hint is not None else 0.3,
    )
    confidence = max(0.0, confidence)

    if (
        observation.key is not None
        or is_explicit_instruction(text)
        or _matches_any(text, STRONG_SIGNAL_PATTERNS)
    ):
        # Inferred episodic events decay naturally: they get SHORT retention
        # so the sweeper can expire them, unlike keyed preferences/facts.
        retention = (
            RetentionClass.SHORT
            if observation.memory_class == MemoryClass.EPISODIC
            else RetentionClass.STANDARD
        )
        return WriteDecision(
            action=WriteStage.CANDIDATE,
            confidence=confidence,
            actor=Actor.POLICY,
            retention_class=retention,
            reason=(
                "explicit-style phrase without owner assertion; candidate only"
                if is_explicit_instruction(text) and observation.key is None
                else "inferred observation with stable key or strong signal"
            ),
        )

    return WriteDecision(
        action=WriteStage.SESSION,
        confidence=min(confidence, 0.2),
        actor=Actor.POLICY,
        retention_class=RetentionClass.SESSION,
        reason="weak inferred observation",
    )


__all__ = [
    "ACTION_IGNORE",
    "ACTION_REFUSE",
    "EXPLICIT_PATTERNS",
    "SECRET_PATTERNS",
    "Observation",
    "WriteDecision",
    "decide",
    "find_secret",
    "is_chatty",
    "is_explicit_instruction",
]
