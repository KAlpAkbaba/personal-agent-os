"""app.research.policy: research speed modes, mode derivation, and the wave
loop's early-stop decision (M18.2, ADR-0068).

Pure and deterministic throughout - no network, no browser, no database, no
Temporal - so the owner's rules ("target 60-90s, HARD budget 120s for QUICK",
"never silently choose DEEP", "wave 1 = top 4 ... stop early") are each
asserted directly against the function that implements them, independent of
whether a real workflow run ever reaches that code path.
"""

from __future__ import annotations

from app.research.policy import (
    DEFAULT_MODE,
    MODE_DEEP,
    MODE_QUICK,
    MODE_STANDARD,
    POLICIES,
    REASON_BUDGET_EXHAUSTED,
    REASON_ENOUGH_EVIDENCE,
    REASON_MAX_SOURCES_REACHED,
    REASON_MAX_WAVES,
    ResearchPolicy,
    decide_next_wave,
    derive_mode_from_utterance,
    resolve_policy,
)

# --------------------------------------------------------------------------- #
# policy table
# --------------------------------------------------------------------------- #


def test_default_mode_is_quick() -> None:
    assert DEFAULT_MODE == MODE_QUICK


def test_quick_policy_matches_the_owner_budget() -> None:
    quick = POLICIES[MODE_QUICK]
    assert quick.discovery_queries_max <= 2
    assert 20 <= quick.candidate_urls_max <= 30
    assert 8 <= quick.max_sources <= 12
    assert 3 <= quick.concurrent_fetches <= 4
    assert 8.0 <= quick.per_page_timeout_s <= 12.0
    assert quick.per_domain_max_pages <= 2
    assert quick.final_findings_max <= 5
    assert quick.min_distinct_publishers >= 3
    assert 60.0 <= quick.target_budget_s <= 90.0
    assert quick.hard_budget_s == 120.0


def test_standard_and_deep_budgets_differ_from_quick_and_from_each_other() -> None:
    quick, standard, deep = POLICIES[MODE_QUICK], POLICIES[MODE_STANDARD], POLICIES[MODE_DEEP]
    # STANDARD: broader corroboration, 2-3 minutes.
    assert 120.0 <= standard.target_budget_s <= 180.0
    assert standard.hard_budget_s > quick.hard_budget_s
    assert standard.max_sources > quick.max_sources
    assert standard.discovery_queries_max > quick.discovery_queries_max
    # DEEP: explicit-only, longer, but still bounded (never unlimited).
    assert deep.hard_budget_s > standard.hard_budget_s
    assert deep.max_sources > standard.max_sources
    assert deep.max_waves > 0  # bounded, not "keep going forever"
    # Every mode's target ceiling respects the pipeline-wide contract floor.
    from app.research.contracts import MIN_REPORT_FINDINGS

    for policy in (quick, standard, deep):
        assert policy.final_findings_max >= MIN_REPORT_FINDINGS
        assert MIN_REPORT_FINDINGS <= policy.target_findings <= policy.final_findings_max


def test_resolve_policy_defaults_to_quick_for_none_and_unknown() -> None:
    assert resolve_policy(None).mode == MODE_QUICK
    assert resolve_policy("not-a-real-mode").mode == MODE_QUICK
    assert resolve_policy("STANDARD").mode == MODE_STANDARD  # case-insensitive


def test_policy_round_trips_through_its_own_dict() -> None:
    original = POLICIES[MODE_DEEP]
    restored = ResearchPolicy.from_dict(original.as_dict())
    assert restored == original


def test_policy_from_dict_falls_back_to_quick_defaults_for_missing_fields() -> None:
    restored = ResearchPolicy.from_dict({"mode": "standard"})
    assert restored.mode == "standard"
    # QUICK's own number, not STANDARD's resolved one (25 vs 40): a partial dict
    # is filled from QUICK defaults field-by-field, never re-resolved by name.
    assert restored.candidate_urls_max == POLICIES[MODE_QUICK].candidate_urls_max


# --------------------------------------------------------------------------- #
# mode derivation from the owner's own words
# --------------------------------------------------------------------------- #


def test_quick_is_the_default_for_ordinary_conversational_research() -> None:
    assert derive_mode_from_utterance("son üç gündeki yapay zekâ ajan gelişmelerini araştır") == (
        MODE_QUICK
    )
    assert derive_mode_from_utterance("") == MODE_QUICK


def test_explicit_comprehensive_words_choose_deep() -> None:
    assert derive_mode_from_utterance("bu konuyu kapsamlı araştır") == MODE_DEEP
    assert derive_mode_from_utterance("derinlemesine bir araştırma yap") == MODE_DEEP
    assert derive_mode_from_utterance("detaylı bir şekilde araştır") == MODE_DEEP
    # Diacritic/case tolerant, matching every other Turkish-facing matcher in this codebase.
    assert derive_mode_from_utterance("KAPSAMLI ARASTIR") == MODE_DEEP


def test_detayli_alone_without_arastir_does_not_choose_deep() -> None:
    """ "detaylı" alone describes the ANSWER the owner wants, not a request for a
    comprehensive research run - only "detaylı ... araştır" together does."""
    assert derive_mode_from_utterance("bunu detaylı anlatır mısın") == MODE_QUICK


def test_broader_words_choose_standard() -> None:
    assert derive_mode_from_utterance("geniş bir araştırma yap") == MODE_STANDARD
    assert derive_mode_from_utterance("karşılaştırmalı bir bakış istiyorum") == MODE_STANDARD


def test_deep_wins_over_standard_when_both_present() -> None:
    assert derive_mode_from_utterance("geniş ve kapsamlı bir araştırma yap") == MODE_DEEP


# --------------------------------------------------------------------------- #
# the wave loop's early-stop decision
# --------------------------------------------------------------------------- #


def test_enough_evidence_after_wave_one_stops_before_wave_two() -> None:
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=policy.target_findings,  # wave 1 already produced enough
        waves_used=1,
        elapsed_s=20.0,
        sources_fetched=policy.wave_size,
    )
    assert decision.should_fetch is False
    assert decision.reason == REASON_ENOUGH_EVIDENCE


def test_not_enough_evidence_continues_to_wave_two() -> None:
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=20.0,
        sources_fetched=policy.wave_size,
    )
    assert decision.should_fetch is True
    assert decision.fetch_count == policy.wave_size
    assert decision.reason == "continue"


def test_a_quick_run_respects_its_hard_deadline() -> None:
    """Owner rule 1: a QUICK run's HARD budget is 120s. A fake clock (elapsed_s
    passed in directly, never a real sleep) proves the wave loop stops fetching
    the instant that budget is spent, regardless of how little evidence exists."""
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=policy.hard_budget_s,  # exactly at the deadline
        sources_fetched=1,
    )
    assert decision.should_fetch is False
    assert decision.reason == REASON_BUDGET_EXHAUSTED

    just_under = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=policy.hard_budget_s - 0.01,
        sources_fetched=1,
    )
    assert just_under.should_fetch is True


def test_max_waves_stops_even_with_time_and_evidence_left_to_try() -> None:
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=policy.max_waves,
        elapsed_s=1.0,
        sources_fetched=1,
    )
    assert decision.should_fetch is False
    assert decision.reason == REASON_MAX_WAVES


def test_max_sources_ceiling_stops_the_loop() -> None:
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=1.0,
        sources_fetched=policy.max_sources,
    )
    assert decision.should_fetch is False
    assert decision.reason == REASON_MAX_SOURCES_REACHED


def test_next_wave_size_is_capped_by_remaining_source_budget() -> None:
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=1.0,
        sources_fetched=policy.max_sources - 1,  # only one source left in the budget
    )
    assert decision.should_fetch is True
    assert decision.fetch_count == 1
