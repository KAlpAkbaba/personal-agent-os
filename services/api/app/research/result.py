"""What research SAYS vs. what it DID (M18.2 DEFECT 2, ADR-0067).

Before this module, the only research-shaped thing a level could narrate was the
validated report's raw dict or the ledger event's flattened ``ReportStats`` — so the
owner-facing (executive) level and the operator-facing (technical) level read from the
same undifferentiated bag of numbers, and the pipeline's own diagnostics (candidates
discovered, pages rejected, interstitials, dedup) leaked into the spoken answer instead
of the findings.

Two shapes make the split explicit and mechanical rather than a discipline someone has
to remember to keep:

- :class:`ResearchResult` — the OUTCOME an owner asked for: a conclusion, up to a
  handful of findings each with why it matters, and the sources behind them. Built
  ONLY from the validated, provenance-gated report (``app.research.report.ResearchReport``
  / the persisted ``ResearchReportRow.report_json``) — never from counts, never
  fabricated. When the pipeline could not produce a defensible answer
  (``InsufficientValidFindings`` / ``InsufficientValidEvidence``), it carries that fact
  instead of inventing one.
- :class:`ResearchDiagnostics` — the pipeline's OWN telemetry: how many pages were
  found, fetched, rejected and why, deduplicated, quarantined. This is what a
  TECHNICAL-level question or an explicit "hangi sayfalar elendi?" gets; it is never
  what an executive summary reads unasked.

:func:`spoken_result` is the one function allowed to decide what the owner hears by
default: a concise conclusion, up to three findings ("Birincisi ... Bu önemli çünkü
..."), then an offer to say more. No counts, no crawler vocabulary (elendi / eledi /
interstitial / tekrar / dedup / aday) ever appears in it — that is an invariant the
unit tests assert directly, not a style preference.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

#: Why a research run could not produce a defensible answer (mirrors the pipeline's
#: own error classes — app.research.contracts.ERROR_INSUFFICIENT_VALID_FINDINGS /
#: ERROR_INSUFFICIENT_VALID_EVIDENCE — as plain strings so this module never has to
#: import the Temporal-activity-facing contracts module just to name them).
REASON_INSUFFICIENT_FINDINGS = "insufficient_valid_findings"
REASON_INSUFFICIENT_EVIDENCE = "insufficient_valid_evidence"

#: How many findings a spoken answer names before offering to say more (owner UX:
#: a listening budget, not a document — the same "two to four sentences" discipline
#: app.explain.engine already applies elsewhere).
MAX_SPOKEN_FINDINGS = 3

_ORDINALS_TR: tuple[str, ...] = ("Birincisi", "İkincisi", "Üçüncüsü")


@dataclass(frozen=True, slots=True)
class ResearchFinding:
    """One spoken-ready finding: the claim, why it matters, and its citations."""

    finding: str
    why_it_matters: str
    sources: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding": self.finding,
            "why_it_matters": self.why_it_matters,
            "sources": list(self.sources),
        }


@dataclass(frozen=True, slots=True)
class ResearchSourceRef:
    """One source, reduced to what a spoken answer may name: never the raw URL."""

    title: str
    url_host: str
    ref: str

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "url_host": self.url_host, "ref": self.ref}


@dataclass(frozen=True, slots=True)
class ResearchResult:
    """The OUTCOME of a research run — what :func:`spoken_result` narrates from.

    Built deterministically by :meth:`from_report_json` from the validated,
    provenance-gated report only. ``executive_summary`` and ``why_it_matters`` are
    carried through verbatim from the report for the record (an artifact, a log, a
    future UI) — they are NOT what gets spoken; ``spoken_result`` composes its own
    count-free sentences from ``findings`` instead, because a report's own
    ``executive_summary`` (the deterministic provider's, in particular) can itself
    contain a source count.
    """

    topic: str = ""
    executive_summary: str = ""
    findings: tuple[ResearchFinding, ...] = field(default_factory=tuple)
    why_it_matters: str = ""
    sources: tuple[ResearchSourceRef, ...] = field(default_factory=tuple)
    #: True when the pipeline could not produce a defensible answer at all (a quality
    #: gate failure, not merely "few findings") — see ``insufficient_evidence``.
    insufficient: bool = False
    insufficient_reason: str | None = None
    #: ADR-0074: a real answer, from real evidence, that rests on fewer sources than a
    #: full report needs. Emphatically NOT ``insufficient`` — there are findings and
    #: they are spoken; the narration just says plainly how little it stands on and
    #: offers a broader run. Read from the report's own ``thin`` flag.
    thin: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "executive_summary": self.executive_summary,
            "findings": [f.as_dict() for f in self.findings],
            "why_it_matters": self.why_it_matters,
            "sources": [s.as_dict() for s in self.sources],
            "insufficient": self.insufficient,
            "insufficient_reason": self.insufficient_reason,
            "thin": self.thin,
        }

    @classmethod
    def from_report_json(
        cls, report_json: dict[str, Any] | None, *, topic: str = ""
    ) -> ResearchResult:
        """Deterministic: the same ``report_json`` always yields the same result.

        Never reaches for anything but the report's own fields — no ledger counts, no
        crawler telemetry. A finding with neither a title nor a summary is skipped
        rather than spoken as an empty claim; a source that is not a mapping is
        skipped the same way (defensive against a malformed/legacy row, never a
        reason to raise on an owner-facing read path).
        """
        data = report_json or {}
        resolved_topic = str(data.get("topic") or topic or "").strip()

        findings: list[ResearchFinding] = []
        for raw in data.get("findings") or ():
            if not isinstance(raw, dict):
                continue
            claim = str(raw.get("title") or raw.get("summary") or "").strip()
            if not claim:
                continue
            why = str(raw.get("why_it_matters") or "").strip()
            sources = tuple(
                str(eid).strip() for eid in (raw.get("evidence_ids") or ()) if str(eid).strip()
            )
            findings.append(ResearchFinding(finding=claim, why_it_matters=why, sources=sources))

        sources: list[ResearchSourceRef] = []
        for raw in data.get("sources") or ():
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "")
            host = urlsplit(url).netloc if url else ""
            title = str(raw.get("title") or "").strip() or host or "Kaynak"
            sources.append(
                ResearchSourceRef(title=title, url_host=host, ref=str(raw.get("id") or ""))
            )

        top_why = ""
        why_entries = data.get("why_it_matters") or ()
        if why_entries and isinstance(why_entries[0], dict):
            top_why = str(why_entries[0].get("text") or "").strip()

        return cls(
            topic=resolved_topic,
            executive_summary=str(data.get("executive_summary") or "").strip(),
            findings=tuple(findings),
            why_it_matters=top_why,
            sources=tuple(sources),
            thin=bool(data.get("thin")),
        )

    @classmethod
    def insufficient_evidence(cls, *, topic: str = "", reason: str | None = None) -> ResearchResult:
        """The honest answer when a run failed a quality gate (spec item 1): never
        read from logs, never phrased as a finding — just the fact, plainly."""
        return cls(topic=topic.strip(), insufficient=True, insufficient_reason=reason)


#: What the owner is offered when an answer came back thin (ADR-0074): a broader run,
#: in the same words the mode derivation already understands ("geniş" -> STANDARD), so
#: an owner who says yes gets exactly the mode this sentence promised.
BROADER_RUN_OFFER_TR = "İstersen daha geniş araştırayım."


def _conclusion_tr(result: ResearchResult) -> str:
    if result.thin:
        # Honest about the thinness without a single count or crawler word: the owner
        # hears WHAT was found and HOW MUCH it stands on, never how the crawl went.
        if result.topic:
            return (
                f"Efendim, '{result.topic}' konusunda kısa araştırma bütçesinde yalnızca "
                "sınırlı sayıda kaynak doğrulayabildim; bulgular sınırlı."
            )
        return (
            "Efendim, kısa araştırma bütçesinde yalnızca sınırlı sayıda kaynak "
            "doğrulayabildim; bulgular sınırlı."
        )
    if result.topic:
        return f"Efendim, '{result.topic}' konusunda araştırmayı tamamladım."
    return "Efendim, araştırmayı tamamladım."


def _no_findings_tr(result: ResearchResult) -> str:
    if result.topic:
        return f"Efendim, '{result.topic}' konusunda savunulabilir bir bulgu çıkaramadım."
    return "Efendim, savunulabilir bir bulgu çıkaramadım."


def _insufficient_tr(result: ResearchResult) -> str:
    base = "Bu konuda yeterli doğrulanmış kaynak bulamadım"
    if result.insufficient_reason == REASON_INSUFFICIENT_FINDINGS:
        return base + "; toplanan kanıtlardan savunulabilir bir bulgu çıkaramadım."
    if result.insufficient_reason == REASON_INSUFFICIENT_EVIDENCE:
        return base + "; sayfalardan yeterli sayıda geçerli kanıt elde edemedim."
    return base + "."


def spoken_result(result: ResearchResult) -> str:
    """The owner-facing Turkish narration: a concise conclusion, up to
    :data:`MAX_SPOKEN_FINDINGS` findings each with why it matters, then an optional
    offer to say more. Deterministic — the same ``result`` always renders the same
    text — and by construction free of counts and crawler vocabulary: nothing here
    is built from a number the pipeline counted, only from what the report says.
    """
    if result.insufficient:
        return _insufficient_tr(result)
    if not result.findings:
        return _no_findings_tr(result)

    parts: list[str] = [_conclusion_tr(result)]
    shown = result.findings[:MAX_SPOKEN_FINDINGS]
    for ordinal, finding in zip(_ORDINALS_TR, shown, strict=False):
        claim = finding.finding.strip().rstrip(".")
        sentence = f"{ordinal}, {claim}."
        why = finding.why_it_matters.strip().rstrip(".")
        if why:
            sentence += f" Bu önemli çünkü {why}."
        parts.append(sentence)
    if result.thin:
        # A thin answer's closing offer is a BROADER run, not "more of the same":
        # there is no unspoken remainder to read out, only a shallower search than
        # the question deserved (ADR-0074).
        parts.append(BROADER_RUN_OFFER_TR)
    elif len(result.findings) > len(shown) or result.sources:
        parts.append("İstersen diğer bulguları veya kaynakları da anlatabilirim.")
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class ResearchDiagnostics:
    """The pipeline's OWN telemetry — everything ``spoken_result`` deliberately
    leaves out. Consumed by the TECHNICAL explain level and by explicit questions
    ("hangi sayfalar elendi?", "araştırma sırasında ne sorun oldu?"), never by the
    executive level.

    Built from the run's own rows (the persisted report's ``stats`` block today;
    ``timings``/``provider_errors`` stay empty rather than being guessed at when the
    pipeline has not recorded them — never fabricated, per the same rule
    :class:`ResearchResult` follows).
    """

    discovered_count: int = 0
    fetched_count: int = 0
    fetch_failed_count: int = 0
    rejected_pages: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    refused_pages: int = 0
    quarantined_pages: int = 0
    dedup_stats: dict[str, int] = field(default_factory=dict)
    provider_errors: tuple[str, ...] = field(default_factory=tuple)
    synthesis_provider: str = ""
    timings: dict[str, Any] = field(default_factory=dict)
    #: M18.2 fast-path fields (ADR-0068): which speed mode ran, its hard budget in
    #: seconds, how long the run actually took, how many fetch waves it spent, and
    #: the challenge policy's own counters — TECHNICAL-level/explicit-question only,
    #: never read by the executive narration (same rule as every other field here).
    mode: str = ""
    budget_s: float = 0.0
    elapsed_s: float = 0.0
    waves: int = 0
    challenged_pages: int = 0
    cooled_domains: int = 0
    #: ADR-0074: whether the answer was thin, and the reason codes for it
    #: (app.research.synthesis.THIN_REASON_*). Technical level only — the owner hears
    #: the thinness in plain Turkish from ``spoken_result``, never these codes.
    thin: bool = False
    thin_reasons: tuple[str, ...] = field(default_factory=tuple)
    #: B31 req 207: the synthesis substitution, if one happened ({requested, used,
    #: attempts, reason}), and how many discovery queries answered from a fallback search
    #: provider. Technical level only; spoken as a substitution, never hidden.
    synthesis_fallback: dict[str, Any] | None = None
    search_fallbacks: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "discovered_count": self.discovered_count,
            "fetched_count": self.fetched_count,
            "fetch_failed_count": self.fetch_failed_count,
            "rejected_pages": self.rejected_pages,
            "rejected_by_reason": dict(self.rejected_by_reason),
            "refused_pages": self.refused_pages,
            "quarantined_pages": self.quarantined_pages,
            "dedup_stats": dict(self.dedup_stats),
            "provider_errors": list(self.provider_errors),
            "synthesis_provider": self.synthesis_provider,
            "timings": dict(self.timings),
            "mode": self.mode,
            "budget_s": self.budget_s,
            "elapsed_s": self.elapsed_s,
            "waves": self.waves,
            "challenged_pages": self.challenged_pages,
            "cooled_domains": self.cooled_domains,
            "thin": self.thin,
            "thin_reasons": list(self.thin_reasons),
            "synthesis_fallback": (
                dict(self.synthesis_fallback) if self.synthesis_fallback else None
            ),
            "search_fallbacks": self.search_fallbacks,
        }

    @classmethod
    def from_report_json(cls, report_json: dict[str, Any] | None) -> ResearchDiagnostics:
        data = report_json or {}
        stats = data.get("stats") or {}
        if not isinstance(stats, dict):
            stats = {}

        def _int(key: str) -> int:
            try:
                return int(stats.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        rejected_by_reason = stats.get("rejected_by_reason") or {}
        if not isinstance(rejected_by_reason, dict):
            rejected_by_reason = {}
        fallback = data.get("synthesis_fallback")

        def _float(key: str) -> float:
            try:
                return float(stats.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        return cls(
            discovered_count=_int("discovered"),
            fetched_count=_int("fetched"),
            fetch_failed_count=_int("fetch_failed"),
            rejected_pages=_int("rejected"),
            rejected_by_reason={str(k): int(v) for k, v in rejected_by_reason.items()},
            refused_pages=_int("injection_dropped"),
            quarantined_pages=_int("quarantined"),
            dedup_stats={"deduplicated": _int("deduplicated")},
            synthesis_provider=str(data.get("synthesis_provider") or ""),
            synthesis_fallback=(dict(fallback) if isinstance(fallback, dict) else None),
            search_fallbacks=int(data.get("search_fallbacks") or 0)
            if str(data.get("search_fallbacks") or "0").isdigit()
            else 0,
            mode=str(stats.get("mode") or ""),
            budget_s=_float("budget_s"),
            elapsed_s=_float("elapsed_s"),
            waves=_int("waves"),
            challenged_pages=_int("challenged_pages"),
            cooled_domains=_int("cooled_domains"),
            # The flag is authoritative at the report's top level (that is what the
            # owner-facing layer reads); stats carries it too for a legacy row.
            thin=bool(data.get("thin") or stats.get("thin")),
            thin_reasons=tuple(str(r) for r in (stats.get("thin_reasons") or ())),
        )


def build_tool_terminal_payload(
    report_json: dict[str, Any] | None,
    *,
    topic: str = "",
    diagnostics: ResearchDiagnostics | None = None,
) -> dict[str, Any]:
    """The owner-facing schema a research call's terminal tool result carries
    (M18.2 DEFECT 2 / ADR-0067): ``spoken_result`` is what the persona reads
    verbatim when a research result arrives; everything else is for the record and
    for explicit diagnostic questions, never spoken unasked.
    """
    result = ResearchResult.from_report_json(report_json, topic=topic)
    diag = (
        diagnostics
        if diagnostics is not None
        else ResearchDiagnostics.from_report_json(report_json)
    )
    spoken = spoken_result(result)
    return {
        "spoken_result": spoken,
        # M18.2 follow-up to ADR-0067: the SAME text, also under "speech" - the key
        # every other tool result carries and the ONE key session_activity's
        # speech_head and the persona's generic "read speech verbatim" instruction
        # both already look for. "spoken_result" stays the primary name (the persona's
        # research-specific instruction reads it explicitly, and it says more than
        # "this is speech" - it names what kind of speech); this is a second door onto
        # the same content, never a different sentence.
        "speech": spoken,
        "executive_summary": result.executive_summary,
        "findings": [f.as_dict() for f in result.findings],
        "source_summary": [s.as_dict() for s in result.sources],
        # ADR-0074: the explain engine and the acceptance harness both need to tell a
        # THIN answer from a full one without re-deriving it from counts. The reason
        # codes stay in diagnostics (stats.thin_reasons), where the crawler vocabulary
        # belongs; this is the one bit the owner-facing layer branches on.
        "thin": result.thin,
        "diagnostics": diag.as_dict(),
    }


def build_insufficient_terminal_payload(
    *, topic: str = "", reason: str | None = None
) -> dict[str, Any]:
    """The same schema, for a run that failed its quality gate (spec item 1): honest
    and concise, never a report shaped like the log that explains the failure."""
    result = ResearchResult.insufficient_evidence(topic=topic, reason=reason)
    spoken = spoken_result(result)
    return {
        "spoken_result": spoken,
        "speech": spoken,
        "executive_summary": "",
        "findings": [],
        "source_summary": [],
        "thin": False,
        "diagnostics": ResearchDiagnostics().as_dict(),
    }


# --------------------------------------------------------------- E: a failed run's own words
#
# ADR-0178 (owner incident 2026-09-19, item E): "araştırma başarısız oldu dedi" was ALL
# the owner ever heard from a run that read 11 pages perfectly well and rejected all of
# them for a specific, nameable reason (in the production run behind this incident:
# every rejection was off_topic). The run's own terminal ``error`` string — what
# ``app.research.browser_activities.fail_run_activity`` stores on
# ``ResearchRunRow.error`` when the quality gate or synthesis floor could not be met —
# used to be the raw exception text
# ("insufficient_valid_evidence: ranking: 0 contract-valid item(s), 3 required; 0
# quarantined"): English, crawler vocabulary, and silent about the one fact the owner
# actually needs (why). :func:`insufficient_run_narration` is what
# ``fail_run_activity`` composes that string from instead, for the two error classes
# this applies to (:data:`REASON_INSUFFICIENT_EVIDENCE` / a synthesis-floor miss) —
# see that activity for the wiring; this function itself is pure and owns no I/O.


def insufficient_run_narration(
    *, fetched: int, rejected_by_reason: dict[str, int] | None = None
) -> str:
    """The honest, Turkish, count-aware, crawler-vocabulary-free explanation for a run
    that ended with no defensible answer.

    ``fetched`` is how many pages the run actually READ (``ResearchRunRow.progress_json
    ["fetch_done"]``) — the fact that makes "0 kaynak buldum" (nothing was even read)
    and "11 sayfa okudum ama hiçbiri uygun değildi" (plenty was read, none of it
    qualified) two different, both true, both ownable sentences instead of one bare
    "failed". ``rejected_by_reason`` (``ResearchRunRow.progress_json
    ["rejected_by_reason"]``, written by the ranking stage's quality gate — see
    :mod:`app.research.eligibility`) picks which of those it was: the single MOST
    common rejection reason names the sentence, since a mixed bag of reasons still has
    a most-common one and naming it is more honest than naming none.
    """
    reasons = {k: v for k, v in (rejected_by_reason or {}).items() if v}
    if fetched <= 0:
        return "Efendim, bu konuda okunabilecek bir sayfa bulamadım."
    dominant = max(reasons, key=lambda k: reasons[k]) if reasons else None
    if dominant == "off_topic":
        return (
            f"Efendim, {fetched} sayfa okudum, ancak hiçbiri konuyla yeterince ilgili bulunmadı."
        )
    if dominant in ("date_uncertain", "outside_recency_window"):
        return f"Efendim, {fetched} sayfa okudum, ancak tarihlerini doğrulayamadım."
    if dominant == "interstitial":
        return (
            f"Efendim, {fetched} sayfa okudum, ancak çoğu erişim engeli ya da "
            "doğrulama sayfasıyla karşılaştı."
        )
    if dominant == "insufficient_content":
        return f"Efendim, {fetched} sayfa okudum, ancak içerikleri yetersizdi."
    if dominant == "duplicate_event":
        return f"Efendim, {fetched} sayfa okudum, ancak hepsi aynı haberi anlatıyordu."
    return (
        f"Efendim, {fetched} sayfa okudum, ancak hiçbirinden savunulabilir bir bulgu çıkaramadım."
    )


__all__ = [
    "BROADER_RUN_OFFER_TR",
    "MAX_SPOKEN_FINDINGS",
    "REASON_INSUFFICIENT_EVIDENCE",
    "REASON_INSUFFICIENT_FINDINGS",
    "ResearchDiagnostics",
    "ResearchFinding",
    "ResearchResult",
    "ResearchSourceRef",
    "build_insufficient_terminal_payload",
    "build_tool_terminal_payload",
    "insufficient_run_narration",
    "spoken_result",
]
