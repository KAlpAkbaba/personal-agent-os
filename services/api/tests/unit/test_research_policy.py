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
    REASON_CONTINUE,
    REASON_ENOUGH_EVIDENCE,
    REASON_MAX_SOURCES_REACHED,
    REASON_MAX_WAVES,
    REASON_NO_CANDIDATES,
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
    once that budget can no longer fit another wave, regardless of how little
    evidence exists.

    ADR-0074 makes this check STRICTER, not looser: the loop stops when
    ``elapsed + wave_expected_s`` would reach the budget, so it never starts work
    it cannot finish inside 120 s (it used to start a wave at 119.9 s)."""
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=policy.hard_budget_s,  # past the deadline
        sources_fetched=1,
    )
    assert decision.should_fetch is False
    assert decision.reason == REASON_BUDGET_EXHAUSTED

    # A wave whose worst case would cross the deadline is not started either.
    at_the_edge = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=policy.hard_budget_s - policy.wave_expected_s,
        sources_fetched=1,
    )
    assert at_the_edge.should_fetch is False
    assert at_the_edge.reason == REASON_BUDGET_EXHAUSTED

    just_under = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=policy.hard_budget_s - policy.wave_expected_s - 0.01,
        sources_fetched=1,
    )
    assert just_under.should_fetch is True


def test_max_waves_is_a_floor_of_attempts_not_a_ceiling() -> None:
    """ADR-0074, the defect of 2026-09-06: two QUICK runs stopped at ``max_waves``
    with 40-80 s of their own 120 s budget unspent and too little evidence to
    answer. A wave count is no longer a reason to stop while the budget has room
    and there is something fetchable left."""
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=policy.max_waves,
        elapsed_s=1.0,
        sources_fetched=1,
    )
    assert decision.should_fetch is True
    assert decision.reason == "continue"

    # ... and it is still bounded: a pathological run cannot loop forever even with
    # a frozen clock, because it can never spend more waves than it has pages.
    absurd = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=policy.fetch_ceiling,
        elapsed_s=1.0,
        sources_fetched=1,
    )
    assert absurd.should_fetch is False
    assert absurd.reason == REASON_MAX_WAVES


def test_nothing_fetchable_left_stops_the_loop_on_its_own_reason() -> None:
    """Running out of WEB is a different fact from running out of TIME: every
    remaining candidate is on a cooled domain, over a per-domain quota, already
    fetched or refused by destination policy."""
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=1.0,
        sources_fetched=policy.wave_size,
        fetchable_remaining=0,
    )
    assert decision.should_fetch is False
    assert decision.reason == REASON_NO_CANDIDATES


def test_fetch_ceiling_stops_the_loop() -> None:
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=1.0,
        sources_fetched=policy.fetch_ceiling,
    )
    assert decision.should_fetch is False
    assert decision.reason == REASON_MAX_SOURCES_REACHED


def test_max_sources_alone_no_longer_ends_a_run_with_budget_to_spare() -> None:
    """The exact shape of run afee23c9 (2026-09-06): ``max_sources`` pages fetched,
    two pieces of evidence, ~40 s of the hard budget still unspent. It must keep
    going."""
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=2,
        waves_used=3,
        elapsed_s=81.0,
        sources_fetched=policy.max_sources,
    )
    assert decision.should_fetch is True
    assert decision.fetch_count > 0


def test_next_wave_size_is_capped_by_remaining_source_budget() -> None:
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=1.0,
        sources_fetched=policy.fetch_ceiling - 1,  # only one source left in the budget
    )
    assert decision.should_fetch is True
    assert decision.fetch_count == 1


def test_next_wave_never_asks_for_more_than_is_fetchable() -> None:
    policy = POLICIES[MODE_QUICK]
    decision = decide_next_wave(
        policy=policy,
        evidence_count=0,
        waves_used=1,
        elapsed_s=1.0,
        sources_fetched=policy.wave_size,
        fetchable_remaining=2,
    )
    assert decision.should_fetch is True
    assert decision.fetch_count == 2


def _run_the_loop(policy, *, start_s: float, evidence_count: int) -> tuple[int, float, int, str]:
    """The workflow's own wave loop, driven by a FAKE clock (a float this function
    advances by one wave's worst case per iteration — never a real sleep, never
    wall-clock time), with an unlimited fetchable pool. Returns
    (waves, elapsed, fetched, stop reason)."""
    clock = start_s
    waves = 1
    fetched = policy.wave_size
    reason = REASON_CONTINUE
    while True:
        decision = decide_next_wave(
            policy=policy,
            evidence_count=evidence_count,
            waves_used=waves,
            elapsed_s=clock,
            sources_fetched=fetched,
        )
        reason = decision.reason
        if not decision.should_fetch:
            return waves, clock, fetched, reason
        clock += policy.wave_expected_s
        fetched += decision.fetch_count
        waves += 1


def test_the_loop_spends_the_budget_it_has_instead_of_stopping_at_three_waves() -> None:
    """Run d914e44f's shape: one piece of evidence after wave 1, ~58 s of a 120 s
    budget used. The loop must keep going well past ``max_waves``, and must still
    terminate inside the budget."""
    policy = POLICIES[MODE_QUICK]
    waves, elapsed, fetched, reason = _run_the_loop(policy, start_s=12.0, evidence_count=1)
    assert waves > policy.max_waves
    assert elapsed <= policy.hard_budget_s
    assert fetched > policy.max_sources
    assert fetched <= policy.fetch_ceiling
    assert reason in {REASON_BUDGET_EXHAUSTED, REASON_MAX_SOURCES_REACHED}


def test_the_loop_stops_at_the_hard_budget_and_never_crosses_it() -> None:
    """A run that starts its fetching late (discovery was slow) gets the waves the
    remaining budget can pay for, and not one more."""
    policy = POLICIES[MODE_QUICK]
    for start in (0.0, 30.0, 95.0, 111.0, 130.0):
        _waves, elapsed, _fetched, _reason = _run_the_loop(
            policy, start_s=start, evidence_count=0
        )
        assert elapsed <= max(start, policy.hard_budget_s)
        if start < policy.hard_budget_s:
            assert elapsed <= policy.hard_budget_s


def test_every_mode_terminates_and_respects_its_own_budget() -> None:
    for policy in POLICIES.values():
        waves, elapsed, fetched, reason = _run_the_loop(policy, start_s=0.0, evidence_count=0)
        assert reason != REASON_CONTINUE
        assert elapsed <= policy.hard_budget_s
        assert fetched <= policy.fetch_ceiling
        assert waves >= 1


def test_enough_evidence_still_wins_over_every_other_bound() -> None:
    """The early stop is checked first and is unchanged by ADR-0074: a run that has
    what it needs stops, budget or no budget."""
    policy = POLICIES[MODE_QUICK]
    waves, _elapsed, _fetched, reason = _run_the_loop(
        policy, start_s=0.0, evidence_count=policy.target_findings
    )
    assert waves == 1
    assert reason == REASON_ENOUGH_EVIDENCE


def test_a_publishable_run_is_bounded_by_the_SOFT_budget_a_starved_one_by_the_hard() -> None:
    """ADR-0074: extra time belongs to a run that would otherwise have no answer. A
    run that could already publish a full report keeps looking for one more finding
    only until ``target_budget_s``; a run still short of one gets ``hard_budget_s``."""
    policy = POLICIES[MODE_QUICK]
    late = policy.target_budget_s - policy.wave_expected_s

    publishable = decide_next_wave(
        policy=policy,
        evidence_count=policy.target_findings - 1,
        waves_used=2,
        elapsed_s=late,
        sources_fetched=8,
        publishable=True,
    )
    assert publishable.should_fetch is False
    assert publishable.reason == REASON_BUDGET_EXHAUSTED

    starved = decide_next_wave(
        policy=policy,
        evidence_count=1,
        waves_used=2,
        elapsed_s=late,
        sources_fetched=8,
        publishable=False,
    )
    assert starved.should_fetch is True


def test_wave_expected_cost_never_exceeds_the_budget_it_is_measured_against() -> None:
    """Structural: a mode whose single wave costs more than its whole SOFT budget
    could never fetch anything at all once it had a publishable answer."""
    for policy in POLICIES.values():
        assert 0 < policy.wave_expected_s < policy.target_budget_s
        assert policy.target_budget_s <= policy.hard_budget_s
        assert policy.fetch_ceiling >= policy.max_sources
