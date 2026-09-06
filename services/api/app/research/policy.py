"""Research speed modes (M18.2, owner run 2026-09-06 session a4455670).

The owner's first end-to-end spoken research ("son üç gündeki yapay zekâ ajan
gelişmelerini araştır") worked, but discovered 254 candidates and took several
minutes, much of it spent on CAPTCHA/challenge pages. Nothing in the pipeline
had ever declared how much research a *conversational* request should cost —
so it did all of it: every discovery query, every source class, every
candidate, before deciding anything.

This module is the fix, as data rather than as scattered constants: three
named modes (QUICK — the conversational default, STANDARD — broader
corroboration, DEEP — explicit-only, bounded but larger) and one function
that turns an owner utterance's own words into a mode.  Nothing here touches
the network, the database or the workflow's own object; :mod:`
app.research.browser_workflow` reads a :class:`ResearchPolicy` and enforces
it, and this module's :func:`decide_next_wave` is the pure, unit-testable
early-stop decision the workflow's wave loop calls after every rank.

QUICK is the default for a spoken "araştır" precisely because nothing should
ever silently run DEEP: item 1 of the owner's rules is "never silently choose
DEEP", so :func:`derive_mode_from_utterance` only ever promotes past QUICK on
an explicit word the owner actually said.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MODE_QUICK = "quick"
MODE_STANDARD = "standard"
MODE_DEEP = "deep"
RESEARCH_MODES: tuple[str, ...] = (MODE_QUICK, MODE_STANDARD, MODE_DEEP)

DEFAULT_MODE = MODE_QUICK


@dataclass(frozen=True, slots=True)
class ResearchPolicy:
    """One mode's numeric budget, entirely as data (owner rule 2).

    Every bound here is a CEILING, not a target the pipeline tries to reach —
    the wave loop (:func:`decide_next_wave`) stops the moment it has enough
    evidence, well under these numbers on a good run.
    """

    mode: str
    #: How many discovery queries are actually issued per source class. The real run's
    #: 254 candidates came from every query-expansion template x every source class;
    #: capping this is what keeps discovery itself fast, independent of the fetch budget.
    discovery_queries_max: int
    #: How many candidate URLs the pipeline will even consider ranking for fetch,
    #: before any navigation happens (owner rule 4: "quick ranking before any
    #: expensive navigation"). Discovery may find more; the rest are simply never
    #: looked at again for this run.
    candidate_urls_max: int
    #: The hard ceiling on pages actually fetched (navigated to) this run.
    max_sources: int
    #: How many fetches the workflow starts concurrently per wave.
    concurrent_fetches: int
    #: The per-page timeout the gateway waits for one fetch's device round trip
    #: (BrowserDispatchError("timeout", ...) beyond this — retried once, never more,
    #: per the challenge policy). Independent of the device transport's own up-to-120s
    #: ceiling (BROWSER_CAPABILITIES.md), which stays the safety net either way.
    per_page_timeout_s: float
    #: How many pages from the SAME domain may be fetched in one run before the rest
    #: of that domain's candidates are simply skipped (never a reason to fail the run;
    #: there are other domains).
    per_domain_max_pages: int
    #: The report's own findings ceiling for this mode (the pipeline-wide contract
    #: floor, app.research.contracts.MIN_REPORT_FINDINGS, still applies underneath).
    final_findings_max: int
    #: How many strong findings are "enough" to stop fetching early. Always
    #: <= final_findings_max and >= app.research.contracts.MIN_REPORT_FINDINGS.
    target_findings: int
    #: A soft goal surfaced in diagnostics only — never a hard gate a run can fail
    #: (owner rule 5: "also score ... source diversity"; owner rule 2: "when possible").
    min_distinct_publishers: int
    #: How many candidates one fetch wave requests (owner rule 6: "wave 1 = top 4").
    wave_size: int
    #: How many waves the run may spend at most, independent of the time budget.
    max_waves: int
    #: The soft, documented target this mode aims to finish within.
    target_budget_s: float
    #: The HARD budget (owner rule 1): once elapsed_s reaches this, the wave loop
    #: stops fetching and synthesizes from whatever evidence it already has (or
    #: says truthfully that it has too little) — never silently keeps browsing.
    hard_budget_s: float

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "discovery_queries_max": self.discovery_queries_max,
            "candidate_urls_max": self.candidate_urls_max,
            "max_sources": self.max_sources,
            "concurrent_fetches": self.concurrent_fetches,
            "per_page_timeout_s": self.per_page_timeout_s,
            "per_domain_max_pages": self.per_domain_max_pages,
            "final_findings_max": self.final_findings_max,
            "target_findings": self.target_findings,
            "min_distinct_publishers": self.min_distinct_publishers,
            "wave_size": self.wave_size,
            "max_waves": self.max_waves,
            "target_budget_s": self.target_budget_s,
            "hard_budget_s": self.hard_budget_s,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ResearchPolicy:
        """Rebuild from a stored ``plan_json["policy"]`` dict (a replayed/resumed
        activity reads the SAME policy a run started with, never a re-resolved one
        that could differ if settings changed mid-run)."""
        defaults = POLICIES[DEFAULT_MODE]

        def _i(key: str) -> int:
            return int(data.get(key, getattr(defaults, key)))

        def _f(key: str) -> float:
            return float(data.get(key, getattr(defaults, key)))

        return cls(
            mode=str(data.get("mode", defaults.mode)),
            discovery_queries_max=_i("discovery_queries_max"),
            candidate_urls_max=_i("candidate_urls_max"),
            max_sources=_i("max_sources"),
            concurrent_fetches=_i("concurrent_fetches"),
            per_page_timeout_s=_f("per_page_timeout_s"),
            per_domain_max_pages=_i("per_domain_max_pages"),
            final_findings_max=_i("final_findings_max"),
            target_findings=_i("target_findings"),
            min_distinct_publishers=_i("min_distinct_publishers"),
            wave_size=_i("wave_size"),
            max_waves=_i("max_waves"),
            target_budget_s=_f("target_budget_s"),
            hard_budget_s=_f("hard_budget_s"),
        )


#: QUICK: the conversational default (owner rule 1: "target 60-90 s, HARD budget 120 s").
_QUICK = ResearchPolicy(
    mode=MODE_QUICK,
    discovery_queries_max=2,
    candidate_urls_max=25,
    max_sources=10,
    concurrent_fetches=4,
    per_page_timeout_s=10.0,
    per_domain_max_pages=2,
    final_findings_max=5,
    target_findings=4,
    min_distinct_publishers=3,
    wave_size=4,
    max_waves=3,
    target_budget_s=90.0,
    hard_budget_s=120.0,
)

#: STANDARD: broader corroboration (owner rule 1: "2-3 min").
_STANDARD = ResearchPolicy(
    mode=MODE_STANDARD,
    discovery_queries_max=4,
    candidate_urls_max=40,
    max_sources=16,
    concurrent_fetches=4,
    per_page_timeout_s=12.0,
    per_domain_max_pages=3,
    final_findings_max=7,
    target_findings=5,
    min_distinct_publishers=3,
    wave_size=4,
    max_waves=4,
    target_budget_s=150.0,
    hard_budget_s=210.0,
)

#: DEEP: explicit-only, comprehensive but still bounded (owner rule 1: "longer,
#: bounded" — never unlimited, never chosen silently).
_DEEP = ResearchPolicy(
    mode=MODE_DEEP,
    discovery_queries_max=8,
    candidate_urls_max=60,
    max_sources=24,
    concurrent_fetches=4,
    per_page_timeout_s=15.0,
    per_domain_max_pages=4,
    final_findings_max=7,
    target_findings=6,
    min_distinct_publishers=4,
    wave_size=6,
    max_waves=4,
    target_budget_s=360.0,
    hard_budget_s=600.0,
)

POLICIES: dict[str, ResearchPolicy] = {
    MODE_QUICK: _QUICK,
    MODE_STANDARD: _STANDARD,
    MODE_DEEP: _DEEP,
}


def resolve_policy(mode: str | None) -> ResearchPolicy:
    """The named policy, or QUICK for ``None``/anything unrecognised — never an
    error on this path: an unknown mode string must not fail a research run."""
    if mode is None:
        return POLICIES[DEFAULT_MODE]
    return POLICIES.get(mode.strip().lower(), POLICIES[DEFAULT_MODE])


# --------------------------------------------------------------------------- #
# mode derivation from the owner's own words
# --------------------------------------------------------------------------- #

#: Same Turkish-aware fold as app.research.eligibility._fold, duplicated on purpose
#: (a one-directional dependency from a spoken-language heuristic onto the
#: evidence-quality gate's private helper would couple two things that change for
#: different reasons; see app.research.evidence's own note about the ranking
#: formula being deliberately duplicated rather than imported).
_TURKISH_FOLD = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "I": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ö": "o",
        "Ö": "o",
        "ü": "u",
        "Ü": "u",
        "ç": "c",
        "Ç": "c",
        "â": "a",
        "Â": "a",
        "î": "i",
        "Î": "i",
        "û": "u",
        "Û": "u",
    }
)


def _fold(text: str) -> str:
    folded = text.translate(_TURKISH_FOLD)
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return folded.lower()


#: DEEP: explicit, unambiguous words for "comprehensive" research (owner rule 1).
_DEEP_SOLO_MARKERS = ("kapsamli", "derinlemesine")
#: "detayli ... arastir" (any inflection: arastır/araştırın/araştırır mısın/...),
#: not "detayli" alone — a merely detailed ANSWER is not the same request as a
#: comprehensive research run, and "detayli" alone is common in unrelated speech.
_DETAYLI_ARASTIR_RE = re.compile(
    r"\bdetayli\w*\b.{0,24}\barastir\w*\b|\barastir\w*\b.{0,24}\bdetayli\w*\b"
)

#: STANDARD: broader-than-quick but not an explicit "comprehensive" request.
_STANDARD_MARKERS = ("genis", "karsilastirmali")


def derive_mode_from_utterance(text: str) -> str:
    """QUICK unless the owner's own words ask for more (owner rule 1: "never
    silently choose DEEP"). Order matters: DEEP markers are checked before
    STANDARD ones so an utterance naming both wins on the stronger request."""
    folded = _fold(text or "")
    if any(marker in folded for marker in _DEEP_SOLO_MARKERS) or _DETAYLI_ARASTIR_RE.search(folded):
        return MODE_DEEP
    if any(marker in folded for marker in _STANDARD_MARKERS):
        return MODE_STANDARD
    return MODE_QUICK


# --------------------------------------------------------------------------- #
# the wave loop's early-stop decision (owner rule 6), pure and unit-testable
# --------------------------------------------------------------------------- #

REASON_ENOUGH_EVIDENCE = "enough_evidence"
REASON_MAX_WAVES = "max_waves"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_MAX_SOURCES_REACHED = "max_sources_reached"
REASON_CONTINUE = "continue"


@dataclass(frozen=True, slots=True)
class WaveDecision:
    """Whether the workflow should fetch another wave, and how many candidates
    to ask for if so. ``reason`` is diagnostics-only (never spoken)."""

    should_fetch: bool
    fetch_count: int
    reason: str


def decide_next_wave(
    *,
    policy: ResearchPolicy,
    evidence_count: int,
    waves_used: int,
    elapsed_s: float,
    sources_fetched: int,
) -> WaveDecision:
    """The early-stop rule (owner rule 6): stop the moment there is enough
    evidence for the requested answer; otherwise keep going one wave at a time,
    never fetching everything up front, until a bound is hit. Every bound here
    is checked independently so the reported ``reason`` is always the one that
    actually applies first (evidence is enough > waves spent > time spent > the
    run's own source ceiling)."""
    if evidence_count >= policy.target_findings:
        return WaveDecision(False, 0, REASON_ENOUGH_EVIDENCE)
    if waves_used >= policy.max_waves:
        return WaveDecision(False, 0, REASON_MAX_WAVES)
    if elapsed_s >= policy.hard_budget_s:
        return WaveDecision(False, 0, REASON_BUDGET_EXHAUSTED)
    remaining = policy.max_sources - sources_fetched
    if remaining <= 0:
        return WaveDecision(False, 0, REASON_MAX_SOURCES_REACHED)
    return WaveDecision(True, min(policy.wave_size, remaining), REASON_CONTINUE)


__all__ = [
    "DEFAULT_MODE",
    "MODE_DEEP",
    "MODE_QUICK",
    "MODE_STANDARD",
    "POLICIES",
    "REASON_BUDGET_EXHAUSTED",
    "REASON_CONTINUE",
    "REASON_ENOUGH_EVIDENCE",
    "REASON_MAX_SOURCES_REACHED",
    "REASON_MAX_WAVES",
    "RESEARCH_MODES",
    "ResearchPolicy",
    "WaveDecision",
    "decide_next_wave",
    "derive_mode_from_utterance",
    "resolve_policy",
]
