"""M12: the terminology / phonetics / hesitation / intent eval sets exist,
cover the acceptance list, and agree with the normaliser and intent resolver."""

from __future__ import annotations

from app.narration.normalizer import normalize
from app.voice.datasets import (
    HESITATION_CASES,
    M12_INTENT_UTTERANCES,
    M12_TERMINOLOGY,
    M12_TERMINOLOGY_CASES,
    TURKISH_PHONETICS,
    TURKISH_PHONETICS_CASES,
)
from app.voice.intents import Intent, normalize_transcript, resolve_intent

ACCEPTANCE_TERMS = {
    "PagentOS", "Tailscale", "Hetzner", "PostgreSQL", "PowerShell", "FortiGate",
    "OpenAI", "Claude", "Windows", "Kubernetes", "Redis", "Temporal",
}


def test_terminology_list_matches_the_acceptance_standard() -> None:
    assert set(M12_TERMINOLOGY) == ACCEPTANCE_TERMS
    covered = " ".join(c.text for c in M12_TERMINOLOGY_CASES)
    for term in ACCEPTANCE_TERMS:
        assert term in covered, term


def test_phonetics_set_covers_every_special_character() -> None:
    assert set(TURKISH_PHONETICS) == {"ı", "İ", "ğ", "ş", "ç", "ö", "ü"}
    covered = " ".join(c.text for c in TURKISH_PHONETICS_CASES)
    for ch in TURKISH_PHONETICS:
        assert ch in covered, ch
    # the normaliser must not damage the characters it is asked to speak
    for case in TURKISH_PHONETICS_CASES:
        spoken = normalize(case.text)
        for ch in ("ı", "ğ", "ş", "ç", "ö", "ü"):
            if ch in case.text:
                assert ch in spoken, (case.case_id, spoken)


def test_hesitation_cases_start_with_fillers_and_keep_their_content() -> None:
    for case in HESITATION_CASES:
        _, tokens, dropped = normalize_transcript(case.reference)
        assert dropped >= 1, case.case_id  # a filler was present and removed
        assert len(tokens) >= 3, case.case_id  # ...and real content followed it
        # a hesitation is never a stop word
        assert resolve_intent(case.reference).intent != Intent.STOP


def test_intent_utterances_resolve_to_the_nine_m12_intents() -> None:
    expected = {
        "m12-int-stop": Intent.STOP, "m12-int-resume": Intent.RESUME,
        "m12-int-repeat": Intent.REPEAT, "m12-int-item": Intent.REPEAT_ITEM,
        "m12-int-slower": Intent.SLOWER, "m12-int-faster": Intent.FASTER,
        "m12-int-summary": Intent.SUMMARIZE, "m12-int-detail": Intent.DETAIL,
        "m12-int-skip": Intent.SKIP,
    }
    assert {c.case_id for c in M12_INTENT_UTTERANCES} == set(expected)
    for case in M12_INTENT_UTTERANCES:
        assert resolve_intent(case.reference).intent == expected[case.case_id], case.case_id
