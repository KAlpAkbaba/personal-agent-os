"""Unit tests: Memory Write Policy decision table (pure, no DB).

Covers the ignore -> session -> candidate -> durable ladder, explicit owner
phrase detection (English + Turkish), the single-observation confidence cap
and the secrets guard patterns.
"""

import pytest

from app.memory.policy import (
    ACTION_IGNORE,
    ACTION_REFUSE,
    Observation,
    decide,
    find_secret,
)
from app.memory.types import (
    SINGLE_OBSERVATION_MAX_CONFIDENCE,
    Actor,
    MemoryClass,
    RetentionClass,
    WriteStage,
)

# ------------------------------------------------------------- explicit teach


@pytest.mark.parametrize(
    "text",
    [
        "Remember this: I want summaries in Turkish.",
        "From now on use dark mode in the editor.",
        "I prefer bullet points over paragraphs.",
        "Bundan sonra raporlari kisa tut.",
        "Kahveyi sade severim, bunu hatırla.",
        "Toplantilari sabah 9'a koymayi tercih ederim.",
        "Bunu aklında tut: fatura günü ayın beşi.",
    ],
)
def test_explicit_phrases_without_owner_flag_stay_candidate(text: str) -> None:
    # M5 security review #4: a trigger phrase inside arbitrary text must NEVER
    # mint OWNER authority by itself — once ingestion pipelines feed web/doc
    # text into /observe, "always use ..." on a webpage must not become an
    # explicit owner memory. Phrase-matched text is a strong candidate signal
    # only; OWNER/durable requires the caller-asserted explicit flag.
    decision = decide(Observation(text=text, memory_class=MemoryClass.PREFERENCE))
    assert decision.action == WriteStage.CANDIDATE
    assert decision.explicit is False
    assert decision.actor == Actor.POLICY
    assert decision.confidence <= 0.4


@pytest.mark.parametrize(
    "text",
    [
        "Remember this: I want summaries in Turkish.",
        "Bundan sonra raporlari kisa tut.",
    ],
)
def test_explicit_phrases_with_owner_flag_go_durable(text: str) -> None:
    decision = decide(
        Observation(text=text, memory_class=MemoryClass.PREFERENCE, explicit=True)
    )
    assert decision.action == WriteStage.DURABLE
    assert decision.explicit is True
    assert decision.actor == Actor.OWNER
    assert decision.confidence == 1.0


def test_explicit_flag_from_caller_goes_durable() -> None:
    decision = decide(
        Observation(text="Owner wants metric units.", explicit=True)
    )
    assert decision.action == WriteStage.DURABLE
    assert decision.explicit is True


# --------------------------------------------------------------- no-signal


@pytest.mark.parametrize(
    "text",
    ["hello", "thanks!", "ok", "Merhaba", "teşekkürler", "tamam", "how are you?", "nasılsın"],
)
def test_chatty_content_is_ignored(text: str) -> None:
    decision = decide(Observation(text=text))
    assert decision.action == ACTION_IGNORE


# ---------------------------------------------------------------- inferred


def test_inferred_single_observation_confidence_is_capped() -> None:
    decision = decide(
        Observation(
            text="Owner prefers meetings in the late afternoon.",
            memory_class=MemoryClass.PREFERENCE,
            key="calendar.meeting_time",
            confidence_hint=0.99,
        )
    )
    assert decision.action == WriteStage.CANDIDATE
    assert decision.explicit is False
    assert decision.confidence <= SINGLE_OBSERVATION_MAX_CONFIDENCE
    assert decision.actor == Actor.POLICY


def test_inferred_without_key_or_signal_goes_session_stage() -> None:
    decision = decide(
        Observation(text="Owner opened the analytics dashboard twice today.")
    )
    assert decision.action == WriteStage.SESSION
    assert decision.retention_class == RetentionClass.SESSION
    assert decision.confidence <= SINGLE_OBSERVATION_MAX_CONFIDENCE


def test_strong_signal_without_key_goes_candidate() -> None:
    decision = decide(
        Observation(text="Owner always reviews the deploy checklist before releasing.")
    )
    assert decision.action == WriteStage.CANDIDATE


# ------------------------------------------------------------ secrets guard


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        ("my token is ghp_" + "a" * 30, "github_token"),
        ("key sk-" + "b" * 24, "openai_style_key"),
        ("aws AKIA" + "C" * 16, "aws_access_key"),
        # Built by concatenation so the repo's own secret-content gate scan
        # never sees a contiguous token-shaped literal in this source file.
        ("slack xoxb-" + "1234567890-abcdef", "slack_token"),
        ("-----BEGIN RSA PRIVATE " + "KEY-----\nMII...", "private_key_block"),
        ("password=hunter2secret", "password_assignment"),
        ("Authorization: Bearer abcdefghijklmnopqrstuvwx", "bearer_token"),
        ("api_key = 0123456789abcdef", "generic_api_key"),
    ],
)
def test_secret_patterns_are_refused(text: str, pattern: str) -> None:
    assert find_secret(text) == pattern
    decision = decide(Observation(text=text, explicit=True))
    assert decision.action == ACTION_REFUSE
    assert decision.secret_pattern == pattern


def test_secret_in_value_json_is_refused() -> None:
    decision = decide(
        Observation(
            text="Remember my deploy credential",
            value={"token": "ghp_" + "z" * 30},
            explicit=True,
        )
    )
    assert decision.action == ACTION_REFUSE


def test_plain_preference_text_is_not_a_secret() -> None:
    assert find_secret("Owner prefers dark mode and Turkish narration.") is None


# --------------------------------------------------- the table is the specification


def test_the_decision_table_matches_the_code():
    """B16. The module docstring is what anyone reads before touching this policy, and
    until this batch its row 2 said "explicit flag OR explicit owner phrase" while
    `decide()` has required the FLAG since M5 review #4. A phrase alone is a candidate.

    That is the same shape as B15's req 279 — a comment that was true once, was never
    revisited, and was believed — except that this one described a SECURITY rule: read
    literally, it says a webpage fed through an ingestion pipeline can mint an explicit
    owner memory by containing the words "always use". The code never allowed it.

    So each documented row is exercised here. The table cannot drift from the behaviour
    again without this test failing.
    """
    import app.memory.policy as policy_module

    table = policy_module.__doc__ or ""

    # Row 1 — a secret is refused before anything else, flag or no flag.
    row1 = decide(Observation(text="my api_key = abcdefgh12345678", explicit=True))
    assert row1.action == ACTION_REFUSE
    assert row1.secret_pattern == "generic_api_key"

    # Row 2 — the FLAG, and only the flag.
    row2 = decide(Observation(text="metrik birim kullan", explicit=True))
    assert (row2.action, row2.actor, row2.explicit) == (WriteStage.DURABLE, Actor.OWNER, True)
    assert "caller-asserted `explicit` flag" in table
    assert "explicit flag OR explicit owner phrase" not in table, (
        "the table claims a phrase alone grants OWNER authority; decide() has never done that"
    )

    # Row 3 — chatty content never becomes a row.
    assert decide(Observation(text="tamam")).action == ACTION_IGNORE

    # Row 4 — an explicit-style PHRASE without the flag is a candidate, not an owner memory.
    row4 = decide(Observation(text="Bundan sonra metrik birim kullan lütfen efendim"))
    assert (row4.action, row4.actor, row4.explicit) == (WriteStage.CANDIDATE, Actor.POLICY, False)
    assert row4.confidence <= SINGLE_OBSERVATION_MAX_CONFIDENCE

    # Row 5 — anything else inferred is session-scoped.
    row5 = decide(Observation(text="Bu sabah kahve içtim ve gazete okudum"))
    assert (row5.action, row5.retention_class) == (WriteStage.SESSION, RetentionClass.SESSION)
