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
def test_explicit_owner_phrases_go_durable(text: str) -> None:
    decision = decide(Observation(text=text, memory_class=MemoryClass.PREFERENCE))
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
        ("slack xoxb-1234567890-abcdef", "slack_token"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMII...", "private_key_block"),
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
