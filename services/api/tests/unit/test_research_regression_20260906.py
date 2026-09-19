"""Regression fixtures taken from the owner's three real QUICK research runs of
2026-09-06 — the runs ADR-0074 exists because of.

All three asked the same kind of short, conversational question on the same device,
within six minutes of each other. Two of them FAILED for lack of evidence while a
third to two thirds of their own 120 s budget was still unspent:

* ``afee23c9`` (19:49:51Z) — 103 candidates discovered, 25 shortlisted, openai.com
  challenged twice (page_validity access_denied, then interstitial) and COOLED, then
  "19 candidate(s) skipped: domain cooled" and "11 candidate(s) skipped: per-domain
  quota"; 10 pages fetched over 3 waves; 2 pieces of evidence survived the gate
  (rejections: date_uncertain, off_topic); synthesis: "openai produced 0 defensible
  finding(s); 3 required" -> FAILED insufficient_valid_findings at 81 s of 120 s.
* ``d914e44f`` (19:54:09Z) — 109 discovered, the same shape, 10 fetched, 1 piece of
  evidence (interstitial 2, date_uncertain, off_topic 2, outside_recency_window 2,
  insufficient_content) -> FAILED at 58 s.
* ``deabbd44`` (19:51:55Z) — 46 discovered, 3 pieces of evidence after 8 fetches,
  "synthesized via deterministic", 3 findings, READY at 43 s. The owner heard this one.

Three defects, reproduced below as the shapes they actually had:

1. the 25-candidate shortlist was the top-N of ONE preference sort, so the skipped
   slots were never refilled from the ~100 candidates already discovered;
2. the wave loop stopped at ``max_waves`` with 40-80 s of budget left;
3. the synthesis gate was binary at ``MIN_REPORT_FINDINGS``, so 2 real findings and
   0 findings were the same outcome to the owner.

Written against the real counts, not simplified ones: a future change that "passes the
unit tests" while re-creating any of these three shapes fails here.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.research.browser_activities import build_fetch_shortlist
from app.research.contracts import MIN_REPORT_FINDINGS, InsufficientValidFindings
from app.research.discovery import DiscoveredCandidate
from app.research.evidence import EvidenceRecord, dedup_and_rank
from app.research.policy import (
    MODE_QUICK,
    POLICIES,
    REASON_BUDGET_EXHAUSTED,
    REASON_MAX_SOURCES_REACHED,
    decide_next_wave,
)
from app.research.report import assign_evidence_ids
from app.research.result import BROADER_RUN_OFFER_TR, ResearchResult, spoken_result
from app.research.synthesis import THIN_REASON_EVIDENCE, synthesize_thin

NOW = datetime(2026, 9, 6, 19, 49, 51, tzinfo=UTC)
TOPIC = "son üç gündeki yapay zekâ ajan gelişmeleri"
RECENCY_LABEL = "son 3 gün"
QUICK = POLICIES[MODE_QUICK]

#: Run afee23c9's own numbers.
AFEE_DISCOVERED = 103
AFEE_COOLED_SKIPPED = 19
AFEE_QUOTA_SKIPPED = 11
AFEE_FETCHED = 10
AFEE_EVIDENCE = 2
AFEE_ELAPSED_S = 81.0

#: Run d914e44f's own numbers.
D914_DISCOVERED = 109
D914_EVIDENCE = 1
D914_ELAPSED_S = 58.0


def _candidate(url: str, *, hint: str | None = None) -> DiscoveredCandidate:
    return DiscoveredCandidate(
        url=url,
        title="yapay zeka ajanlari gelismeleri",
        publisher="p",
        discovered_by="browser_search",
        query_id="news:0",
        published_hint=hint,
    )


def _afee_candidate_pool() -> list[DiscoveredCandidate]:
    """103 candidates in the shape the run actually had: openai.com the single most
    promising-looking domain (it answered the query best and carried recent date
    hints) with 30 URLs, the rest spread across other publishers."""
    openai = [
        _candidate(f"https://openai.com/index/story-{i}", hint="2 saat once") for i in range(30)
    ]
    others = [
        _candidate(f"https://publisher{i % 24}.example.com/story-{i}")
        for i in range(AFEE_DISCOVERED - 30)
    ]
    assert len(openai) + len(others) == AFEE_DISCOVERED
    return openai + others


# --------------------------------------------------------------- defect 1: refill


def test_a_cooled_dominant_domain_no_longer_empties_the_shortlist() -> None:
    """afee23c9: openai.com was cooled after two challenges and 19 of its candidates
    were skipped. Those slots must be refilled from the ~73 candidates from other
    domains that discovery had ALREADY found — the cap is on what can be fetched,
    not on what was discovered."""
    pool = _afee_candidate_pool()
    # A cooled domain is not fetchable at all; the caller filters it out before the
    # shortlist is built (app.research.browser_activities.fetch_targets_activity).
    fetchable = [c for c in pool if "openai.com" not in c.url]

    shortlist = build_fetch_shortlist(
        fetchable,
        limit=QUICK.candidate_urls_max,
        per_domain_max=QUICK.per_domain_max_pages,
        topic=TOPIC,
    )

    assert len(shortlist) == QUICK.candidate_urls_max
    assert all("openai.com" not in c.url for c in shortlist)
    # And the run gets breadth, not one publisher's front page twice: 25 slots over
    # at least 13 distinct domains at 2 pages each.
    hosts = [c.url.split("/")[2] for c in shortlist]
    assert len(set(hosts)) >= QUICK.candidate_urls_max // QUICK.per_domain_max_pages


def test_a_quota_spent_dominant_domain_no_longer_empties_the_shortlist() -> None:
    """The other 11 skipped slots: openai.com was not only cooled, it had also spent
    its 2-page per-domain allowance. Candidates it can no longer contribute must
    occupy no shortlist slot at all."""
    shortlist = build_fetch_shortlist(
        _afee_candidate_pool(),
        limit=QUICK.candidate_urls_max,
        per_domain_max=QUICK.per_domain_max_pages,
        domain_counts={"openai.com": QUICK.per_domain_max_pages},
        topic=TOPIC,
    )

    assert len(shortlist) == QUICK.candidate_urls_max
    assert all("openai.com" not in c.url for c in shortlist)


def test_the_shortlist_is_never_one_domain_even_before_anything_is_skipped() -> None:
    """The root cause, upstream of both skips: the shortlist itself must not be one
    domain's list. Before this, the single preference sort put openai.com — the domain
    with the recent date hints — in every slot it could reach."""
    shortlist = build_fetch_shortlist(
        _afee_candidate_pool(),
        limit=QUICK.candidate_urls_max,
        per_domain_max=QUICK.per_domain_max_pages,
        topic=TOPIC,
    )
    hosts = [c.url.split("/")[2] for c in shortlist]
    assert hosts.count("openai.com") <= QUICK.per_domain_max_pages
    assert len(set(hosts)) > 1


# ----------------------------------------------------------- defect 2: the waves


@pytest.mark.parametrize(
    ("elapsed_s", "evidence", "fetched"),
    [
        (AFEE_ELAPSED_S, AFEE_EVIDENCE, AFEE_FETCHED),
        (D914_ELAPSED_S, D914_EVIDENCE, AFEE_FETCHED),
    ],
)
def test_neither_failed_run_would_stop_with_that_much_budget_left(
    elapsed_s: float, evidence: int, fetched: int
) -> None:
    """Both runs stopped after 3 waves / 10 fetches with too little evidence and
    39 s (afee23c9) and 62 s (d914e44f) of their hard budget unspent. Neither wave
    count nor ``max_sources`` may end a run in that state any more."""
    decision = decide_next_wave(
        policy=QUICK,
        evidence_count=evidence,
        waves_used=3,
        elapsed_s=elapsed_s,
        sources_fetched=fetched,
    )
    assert decision.should_fetch is True
    assert decision.fetch_count > 0
    assert elapsed_s + QUICK.wave_expected_s < QUICK.hard_budget_s


def test_the_extra_waves_still_end_inside_the_hard_budget() -> None:
    """The budget is a ceiling, not an aspiration: replaying afee23c9 forward on a
    fake clock, the run stops on its own and never crosses 120 s."""
    clock, waves, fetched = AFEE_ELAPSED_S, 3, AFEE_FETCHED
    reason = ""
    while True:
        decision = decide_next_wave(
            policy=QUICK,
            evidence_count=AFEE_EVIDENCE,
            waves_used=waves,
            elapsed_s=clock,
            sources_fetched=fetched,
        )
        reason = decision.reason
        if not decision.should_fetch:
            break
        clock += QUICK.wave_expected_s
        fetched += decision.fetch_count
        waves += 1

    assert clock <= QUICK.hard_budget_s
    assert waves > QUICK.max_waves  # more attempts than the old ceiling allowed
    assert fetched > AFEE_FETCHED  # and more pages actually read
    assert reason in {REASON_BUDGET_EXHAUSTED, REASON_MAX_SOURCES_REACHED}


def test_the_run_that_succeeded_spends_only_the_soft_budget_looking_for_more() -> None:
    """deabbd44 reached 3 pieces of evidence after 8 fetches at 43 s and was READY —
    the run the owner actually heard. 3 is under QUICK's early-stop threshold of 4, so
    it may keep looking; but it already has a publishable report, so the SOFT budget
    (90 s) bounds that search, not the hard 120 s. Extra time belongs to a run that
    would otherwise have no answer."""
    keeps_going = decide_next_wave(
        policy=QUICK,
        evidence_count=MIN_REPORT_FINDINGS,
        waves_used=2,
        elapsed_s=43.0,
        sources_fetched=8,
        publishable=True,
    )
    assert keeps_going.should_fetch is True

    soft_budget_spent = decide_next_wave(
        policy=QUICK,
        evidence_count=MIN_REPORT_FINDINGS,
        waves_used=2,
        elapsed_s=QUICK.target_budget_s - QUICK.wave_expected_s,
        sources_fetched=8,
        publishable=True,
    )
    assert soft_budget_spent.should_fetch is False
    assert soft_budget_spent.reason == REASON_BUDGET_EXHAUSTED

    # The SAME clock, for a run that still has nothing publishable, keeps going: the
    # hard budget is what a starved run gets.
    starved = decide_next_wave(
        policy=QUICK,
        evidence_count=AFEE_EVIDENCE,
        waves_used=2,
        elapsed_s=QUICK.target_budget_s - QUICK.wave_expected_s,
        sources_fetched=8,
        publishable=False,
    )
    assert starved.should_fetch is True


def test_enough_evidence_still_ends_a_run_immediately() -> None:
    decision = decide_next_wave(
        policy=QUICK,
        evidence_count=QUICK.target_findings,
        waves_used=2,
        elapsed_s=43.0,
        sources_fetched=8,
    )
    assert decision.should_fetch is False
    assert decision.reason == "enough_evidence"


# ------------------------------------------------------- defect 3: the thin answer


def _evidence(count: int) -> list[EvidenceRecord]:
    stories = (
        (
            "https://openai.com/index/agent-platform",
            "OpenAI yeni yapay zeka ajani platformunu duyurdu",
            "official",
            "OpenAI, gelistiricilerin kendi yapay zeka ajanlarini kurmasina olanak taniyan "
            "bir platform duyurdu. Ajanlar arac kullanimi, kalici hafiza ve cok adimli "
            "gorev planlamasi yapabiliyor.",
        ),
        (
            "https://framework.example.com/agent-2-0",
            "Acik kaynak yapay zeka ajani cercevesi 2.0 yayinlandi",
            "community",
            "Surum, arac cagirma protokolu destegi, daha iyi hafiza yonetimi ve cok ajanli "
            "is akislari icin bir planlayici iceriyor.",
        ),
        (
            "https://enterprise.example.com/agent-adoption",
            "Kurumsal yapay zeka ajani kullanimi hizlaniyor",
            "news",
            "Rapor, ajan is akislarinin otonom gorev tamamlama oranlarini ve insan onayi "
            "gereken adimlari olcuyor.",
        ),
    )
    raw = [
        EvidenceRecord(
            url=url,
            title=title,
            excerpt=excerpt,
            fetched_at=NOW,
            extraction_method="dom_text",
            source_class=source_class,
        )
        for url, title, source_class, excerpt in stories[:count]
    ]
    return assign_evidence_ids(dedup_and_rank(raw, topic=TOPIC))


def _thin_report_json(count: int) -> dict:
    result, reasons, _provider_name = synthesize_thin(
        TOPIC,
        _evidence(count),
        recency_label=RECENCY_LABEL,
        mode=MODE_QUICK,
        cooled_domains=1,  # openai.com, in both failed runs
        rejected_by_reason={"date_uncertain": 1, "off_topic": 1},
    )
    return {
        "topic": TOPIC,
        "executive_summary": result.executive_summary,
        "findings": [f.as_dict() for f in result.findings],
        "sources": [],
        "why_it_matters": [s.as_dict() for s in result.why_it_matters],
        "thin": True,
        "stats": {"thin": True, "thin_reasons": list(reasons)},
    }


@pytest.mark.parametrize("evidence_count", [AFEE_EVIDENCE, D914_EVIDENCE])
def test_both_failed_runs_now_end_ready_and_thin_instead_of_failing(evidence_count: int) -> None:
    """afee23c9 had 2 verified sources and d914e44f had 1. Both were reported to the
    owner as "yeterli doğrulanmış kaynak bulamadım" although real, defensible findings
    existed. Both now produce a report, and the report says how thin it is."""
    assert evidence_count < MIN_REPORT_FINDINGS  # still short of a FULL report
    result, reasons, _provider_name = synthesize_thin(
        TOPIC, _evidence(evidence_count), recency_label=RECENCY_LABEL, mode=MODE_QUICK
    )
    assert len(result.findings) == evidence_count
    assert reasons[0] == THIN_REASON_EVIDENCE
    assert f"yalnızca {evidence_count} kaynak doğrulanabildi" in result.executive_summary
    # Provenance is not weakened by thinness: every finding still cites its evidence.
    for finding in result.findings:
        assert finding.evidence_ids


@pytest.mark.parametrize("evidence_count", [AFEE_EVIDENCE, D914_EVIDENCE])
def test_what_the_owner_would_have_heard_instead_of_the_failure(evidence_count: int) -> None:
    spoken = spoken_result(ResearchResult.from_report_json(_thin_report_json(evidence_count)))
    # The findings that existed all along are actually spoken.
    assert "Birincisi" in spoken
    # The thinness is stated, not hidden ...
    assert "sınırlı" in spoken
    # ... and a broader run is offered rather than left to the owner to think of.
    assert spoken.endswith(BROADER_RUN_OFFER_TR)
    # Still no crawler vocabulary and no counts in the sentences this pipeline composes.
    for word in ("elendi", "eledi", "interstitial", "dedup", "aday"):
        assert word not in spoken.lower()
    assert not any(ch.isdigit() for ch in spoken.split("Birincisi")[0])


def test_zero_evidence_is_still_a_truthful_failure() -> None:
    """The line ADR-0074 does not cross: thinness is a shape of ANSWER. A run with
    nothing verified still fails, and still says so."""
    with pytest.raises(InsufficientValidFindings):
        synthesize_thin(TOPIC, [], recency_label=RECENCY_LABEL, mode=MODE_QUICK)

    failed = ResearchResult.insufficient_evidence(
        topic=TOPIC, reason="insufficient_valid_findings"
    )
    spoken = spoken_result(failed)
    assert "bulamadım" in spoken
    assert BROADER_RUN_OFFER_TR not in spoken


def test_the_run_that_already_worked_is_unchanged() -> None:
    """deabbd44: 3 pieces of evidence, 3 findings, READY, synthesized deterministically.
    It must not be re-labelled thin — MIN_REPORT_FINDINGS is untouched as the threshold
    for a full report."""
    from app.research.synthesis import DeterministicSynthesisProvider

    result = DeterministicSynthesisProvider().synthesize(
        TOPIC, _evidence(3), recency_label=RECENCY_LABEL
    )
    assert len(result.findings) == MIN_REPORT_FINDINGS
    assert "yalnızca" not in result.executive_summary
