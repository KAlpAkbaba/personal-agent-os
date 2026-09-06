"""Evidence-first composition of an activity briefing (spec §2 steps 2-3).

The engine never sees the database directly: it asks an :class:`EvidenceSource` for
ledger events and the records they point at, so the same composition runs over the
real ledger in production and over hand-built evidence in tests. What it is given is
what it may say; a question with no evidence is answered with an uncertainty, never
with an invented state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.explain.classify import (
    LEVEL_DETAILED,
    LEVEL_EXECUTIVE,
    LEVEL_TECHNICAL,
    QUERY_CAN_DEPLOY,
    QUERY_EVIDENCE,
    QUERY_EVOLUTION,
    QUERY_EYE_STATE,
    QUERY_FAILURES,
    QUERY_GOALS,
    QUERY_LEARNED,
    QUERY_MODULE_PROBLEM,
    QUERY_PROBLEMS_NOW,
    QUERY_REJECTED_PAGES,
    QUERY_RESEARCH_PROBLEMS,
    QUERY_SELF_CODE,
    QUERY_SHADOW_READY,
    QUERY_SINCE_YOU_LEFT,
    QUERY_SUBSYSTEM_STATUS,
    QUERY_TESTS,
    QUERY_TODAY,
    QUERY_WHY_BUILT,
    QUERY_WHY_FAILED,
    QUERY_WORLD_STATE,
    ExplainQuery,
)
from app.ledger.screening import MAX_EVIDENCE_CHARS, safe_evidence_text
from app.narration.numbers import cardinal
from app.research.result import (
    REASON_INSUFFICIENT_EVIDENCE,
    REASON_INSUFFICIENT_FINDINGS,
    ResearchDiagnostics,
    ResearchResult,
    spoken_result,
)

LABEL_FACT = "known_fact"
LABEL_INFERENCE = "inference"
LABEL_UNCERTAINTY = "uncertainty"
STATEMENT_LABELS = (LABEL_FACT, LABEL_INFERENCE, LABEL_UNCERTAINTY)

SECTION_EXECUTIVE = "Özet"
SECTION_DETAILED = "Ayrıntı"
SECTION_TECHNICAL = "Teknik"
SECTION_EVIDENCE = "Kanıt"
LEVEL_SECTIONS = {
    LEVEL_EXECUTIVE: SECTION_EXECUTIVE,
    LEVEL_DETAILED: SECTION_DETAILED,
    LEVEL_TECHNICAL: SECTION_TECHNICAL,
}

NO_EVIDENCE_TR = "Bu konuda kayıt bulamadım."
#: How many findings the detailed level narrates before pointing at the report.
MAX_DETAILED_ITEMS = 5

# ------------------------------------------------------------------ owner relevance

RELEVANCE_TASK = "task_completion"
RELEVANCE_CHANGE = "change"
RELEVANCE_FAILURE = "failure"
RELEVANCE_SECURITY = "security"
RELEVANCE_EVOLUTION = "evolution"
RELEVANCE_TELEMETRY = "telemetry"
RELEVANCE_META = "meta"
RELEVANCE_CLASSES = (
    RELEVANCE_TASK,
    RELEVANCE_CHANGE,
    RELEVANCE_FAILURE,
    RELEVANCE_SECURITY,
    RELEVANCE_EVOLUTION,
    RELEVANCE_TELEMETRY,
    RELEVANCE_META,
)
#: Classes an executive briefing is made of. Narration and voice-session bookkeeping
#: (``meta``) and routine telemetry are excluded unless the owner asks about that
#: subsystem: otherwise "Son yaptıklarını anlat" would answer "az önce sana son
#: yaptıklarımı anlattım" - the explanation eating its own tail (owner UX result,
#: 2026-09-04).
MEANINGFUL_CLASSES = frozenset(
    {RELEVANCE_TASK, RELEVANCE_CHANGE, RELEVANCE_FAILURE, RELEVANCE_SECURITY, RELEVANCE_EVOLUTION}
)


def owner_relevance(ev: EventView) -> str:
    """Which kind of thing this event is to the owner, from its type and subsystem."""
    t = ev.event_type
    if ev.subsystem in ("voice", "ledger") or t.startswith(("briefing.", "voice.", "ledger.")):
        return RELEVANCE_META
    if ev.severity == "critical" or ev.subsystem == "security" or t.startswith("security."):
        return RELEVANCE_SECURITY
    if ev.status == "failed" or t.endswith(".failed") or t.startswith("incident."):
        return RELEVANCE_FAILURE
    if t.startswith("evolution.") or ev.subsystem == "evolution":
        return RELEVANCE_EVOLUTION
    if t.startswith("deployment.") or ev.subsystem == "deployment":
        return RELEVANCE_CHANGE
    if t in ("research.completed", "research.qualified") or t.startswith("memory."):
        return RELEVANCE_TASK
    if t in ("research.quality_gate", "research.planned") or t.startswith("browser."):
        return RELEVANCE_TELEMETRY
    return RELEVANCE_TELEMETRY


#: Events that annotate another event rather than being an activity of their own. The
#: owner's qualification verdict is ABOUT the research run; "son ne yaptın" is answered
#: with the run, and the verdict is folded into that answer.
_ANNOTATION_EVENT_TYPES = frozenset(
    {"research.qualified", "ledger.backfill", "briefing.queued", "briefing.delivered"}
)

#: How the quality gate's reasons are spoken (accusative, so they slot into "... eledim").
_REJECTION_TR_ACC = {
    "off_topic": "konu dışı sayfayı",
    "interstitial": "ara doğrulama sayfasını",
    "date_uncertain": "tarihi doğrulanamayan sonucu",
    "outside_recency_window": "tarih dışı sonucu",
    "duplicate_event": "tekrar eden olayı",
    "insufficient_content": "yetersiz içerikli sayfayı",
}
_REJECTION_TR = {
    "off_topic": "konu dışı",
    "interstitial": "ara doğrulama sayfası",
    "date_uncertain": "tarihi doğrulanamadı",
    "outside_recency_window": "tarih aralığı dışında",
    "duplicate_event": "tekrar eden olay",
    "insufficient_content": "yetersiz içerik",
}

_SUBSYSTEM_TR = {
    "research": "araştırma motoru",
    "browser": "tarayıcı",
    "cloud_core": "Cloud Core",
    "device_service": "cihaz servisi",
    "session_companion": "oturum yardımcısı",
    "deployment": "dağıtım",
    "voice": "ses",
    "memory": "hafıza",
    "goal": "hedef çekirdeği",
    "self_model": "öz model",
    "evolution": "evrim motoru",
    "ledger": "etkinlik defteri",
}


#: Which subsystem answers each question kind. This is the ROUTING RECORD: the durable
#: answer to "which cognitive path served this question", written by the engine that
#: dispatched it rather than guessed later by reading the generated Turkish. A harness that
#: has to infer the subsystem from prose is not checking routing, it is checking wording -
#: which is the defect class this whole milestone was built around (owner M17 run,
#: 2026-09-05).
SUBSYSTEM_FOR_QUERY: dict[str, str] = {
    QUERY_LEARNED: "memory+experience",
    QUERY_GOALS: "goals",
    QUERY_WORLD_STATE: "worldmodel",
    QUERY_EYE_STATE: "worldmodel",
    QUERY_SELF_CODE: "selfmodel",
    QUERY_EVOLUTION: "evolution",
    QUERY_SHADOW_READY: "evolution",
    QUERY_WHY_BUILT: "evolution",
    QUERY_CAN_DEPLOY: "authority",
    QUERY_TESTS: "ledger",
    QUERY_SINCE_YOU_LEFT: "ledger",
    QUERY_RESEARCH_PROBLEMS: "research",
    QUERY_REJECTED_PAGES: "research",
}

#: The six the M17 combined qualification asks about, in the order it asks them.
M17_QUERY_KINDS: tuple[str, ...] = (
    QUERY_LEARNED,
    QUERY_GOALS,
    QUERY_WORLD_STATE,
    QUERY_SELF_CODE,
    QUERY_EVOLUTION,
    QUERY_CAN_DEPLOY,
)


# ------------------------------------------------------------------ evidence views


@dataclass(frozen=True, slots=True)
class EventView:
    """A ledger event as the engine sees it (a neutral copy of the row)."""

    event_id: str
    occurred_at: datetime
    event_type: str
    subsystem: str
    status: str
    severity: str
    factual_summary: str
    module: str | None = None
    version: str | None = None
    action: str = ""
    result: str | None = None
    production_state: str = "n/a"
    command_id: str | None = None
    trace_id: str | None = None
    research_job_id: str | None = None
    browser_session_id: str | None = None
    related_module_id: str | None = None
    evidence_refs: tuple[dict[str, Any], ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)
    source: str = "live"

    @property
    def ref(self) -> dict[str, Any]:
        return {"kind": "activity_event", "ref": self.event_id}


class EvidenceSource(Protocol):
    """What the engine may look at. Read-only by construction."""

    def events(
        self,
        *,
        since: datetime | None,
        subsystems: tuple[str, ...] | None,
        statuses: tuple[str, ...] | None,
        limit: int,
    ) -> list[EventView]: ...

    def research_report(self, task_id: str) -> dict[str, Any] | None: ...

    def open_incidents(self) -> list[dict[str, Any]]: ...

    # --- M17 phase 9. Optional: a source that predates these subsystems (or a deployment
    # where they are absent) simply returns nothing, and the engine says it has no record
    # rather than inventing one.

    def lessons(self, *, limit: int = 20) -> list[dict[str, Any]]:  # pragma: no cover
        return []

    def procedural_memories(self, *, limit: int = 20) -> list[dict[str, Any]]:  # pragma: no cover
        return []

    def opportunities(
        self, *, statuses: tuple[str, ...] | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:  # pragma: no cover
        return []

    def goals(self, *, limit: int = 20) -> list[dict[str, Any]]:  # pragma: no cover
        return []


# ------------------------------------------------------------------ briefing model


@dataclass(frozen=True, slots=True)
class Statement:
    text: str
    label: str
    evidence_refs: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "label": self.label, "evidence_refs": list(self.evidence_refs)}


@dataclass(frozen=True, slots=True)
class BriefingItem:
    title: str
    statements: tuple[Statement, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "statements": [s.as_dict() for s in self.statements]}


@dataclass(frozen=True, slots=True)
class Briefing:
    question: str
    query: ExplainQuery
    generated_at: datetime
    executive: tuple[Statement, ...]
    detailed: tuple[BriefingItem, ...]
    technical: tuple[BriefingItem, ...]
    evidence_refs: tuple[dict[str, Any], ...]
    #: The research job the briefing is about, when it is about one.
    research_job_id: str | None = None
    #: That job's report artifact (docs/DECISIONS.md ADR-0075). The pair
    #: (research_job_id, research_artifact_id) is what a harness compares against the
    #: fixture to prove a follow-up REUSED the finished run instead of starting one.
    research_artifact_id: str | None = None
    #: The structured numbers the sentences were built from (counts, versions, verdicts):
    #: what a checker compares against the source record, instead of matching wording.
    facts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse a malformed briefing where it is BUILT, not where it is serialized.

        The world-model branch shadowed this accumulator with a local list of world facts,
        so a list reached ``facts``. Nothing complained until ``provenance()`` called
        ``dict(self.facts)`` during persistence, deep inside the voice tool, and the owner
        heard "activity.explain failed" with an error class of ``internal_bug`` - a
        ValueError about a "dictionary update sequence" naming neither the field nor the
        branch. A composed briefing is cheap to check and expensive to debug (2026-09-05).
        """
        if not isinstance(self.facts, dict):
            raise TypeError(
                "Briefing.facts must be a mapping of structured values, not "
                f"{type(self.facts).__name__}; a branch has shadowed the accumulator"
            )

    @property
    def title(self) -> str:
        return f"Etkinlik özeti — {self.generated_at.strftime('%Y-%m-%d %H:%M')} UTC"

    def provenance(self) -> dict[str, Any]:
        """What this briefing rests on, structurally (M16, owner UX result 2026-09-04).

        Acceptance must not depend on generated Turkish wording: a paraphrase is allowed,
        an unsupported claim is not. So the record names the ledger events, the research
        job, the evidence references and the structured facts that produced the sentences -
        and whether any of it was backfilled from canonical rows or written live.
        """
        events = [r for r in self.evidence_refs if r.get("kind") == "activity_event"]
        sources = sorted({str(r.get("kind")) for r in self.evidence_refs if r.get("kind")})
        return {
            "event_ids": [str(r.get("ref")) for r in events],
            "evidence_kinds": sources,
            "research_job_id": self.research_job_id,
            "research_artifact_id": self.research_artifact_id,
            "facts": dict(self.facts),
            "seeded": False,
            "statement_labels": sorted({s.label for s in self.executive}),
        }

    def cognition(self) -> dict[str, Any]:
        """The routing record: which cognitive path served this question, and on what.

        Deliberately NOT derived from the spoken sentences. A checker asking "did the world
        model answer?" reads ``subsystem`` here; it does not search Turkish prose for the
        word for "truth". ``entity_ids`` are the actual records this answer used - lesson
        ids, goal ids, module ids, opportunity ids, policy ids - so an answer can be traced
        back to the rows behind it whichever subsystem produced it.

        ``research_job_id`` appears only when the answer really came from a research run.
        Requiring every cognitive answer to cite one would be requiring provenance from the
        wrong subsystem (owner direction, 2026-09-05).
        """
        by_kind: dict[str, list[str]] = {}
        for ref in self.evidence_refs:
            kind = str(ref.get("kind") or "")
            ident = str(ref.get("ref") or "")
            if kind and ident:
                by_kind.setdefault(kind, []).append(ident)
        counts = self.counts()
        return {
            "query_kind": self.query.kind,
            "subsystem": SUBSYSTEM_FOR_QUERY.get(self.query.kind, "ledger"),
            "facts": counts["facts"],
            "inferences": counts["inferences"],
            "uncertainties": counts["uncertainties"],
            "evidence_count": counts["evidence"],
            "evidence_kinds": sorted(by_kind),
            "entity_ids": {kind: ids[:8] for kind, ids in sorted(by_kind.items())},
            "research_job_id": self.research_job_id,
            "research_artifact_id": self.research_artifact_id,
        }

    def counts(self) -> dict[str, int]:
        statements = list(self.executive)
        for item in (*self.detailed, *self.technical):
            statements.extend(item.statements)
        return {
            "facts": sum(1 for s in statements if s.label == LABEL_FACT),
            "inferences": sum(1 for s in statements if s.label == LABEL_INFERENCE),
            "uncertainties": sum(1 for s in statements if s.label == LABEL_UNCERTAINTY),
            "evidence": len(self.evidence_refs),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "query": self.query.as_dict(),
            "generated_at": self.generated_at.isoformat(),
            "executive": [s.as_dict() for s in self.executive],
            "detailed": [i.as_dict() for i in self.detailed],
            "technical": [i.as_dict() for i in self.technical],
            "evidence_refs": list(self.evidence_refs),
            "provenance": self.provenance(),
            "cognition": self.cognition(),
            **self.counts(),
        }


# ------------------------------------------------------------------ rendering


def render_markdown(briefing: Briefing) -> str:
    """The briefing as the canonical artifact body the narration engine reads.

    Sections ARE the narration levels (spec §2): ``# Özet`` executive, ``# Ayrıntı``
    detailed, ``# Teknik`` technical, ``# Kanıt`` the references. Items are numbered so
    "ikinci madde" means the second item of the section being read.
    """
    lines: list[str] = [f"# {SECTION_EXECUTIVE}", ""]
    lines.append(" ".join(s.text for s in briefing.executive) or NO_EVIDENCE_TR)
    lines.append("")
    for heading, items in (
        (SECTION_DETAILED, briefing.detailed),
        (SECTION_TECHNICAL, briefing.technical),
    ):
        lines.append(f"# {heading}")
        lines.append("")
        if not items:
            lines.append(NO_EVIDENCE_TR)
        for n, item in enumerate(items, start=1):
            body = " ".join(s.text for s in item.statements)
            lines.append(f"{n}. {item.title}. {body}".strip())
        lines.append("")
    lines.append(f"# {SECTION_EVIDENCE}")
    lines.append("")
    if not briefing.evidence_refs:
        lines.append("- (kanıt yok)")
    for ref in briefing.evidence_refs:
        extra = f" ({ref['digest']})" if ref.get("digest") else ""
        lines.append(f"- {ref.get('kind', '?')}: {ref.get('ref', '?')}{extra}")
    lines.append("")
    return "\n".join(lines)


def speech_for_level(briefing: Briefing, level: str) -> str:
    """What is spoken for one level, verbatim from the same statements the artifact
    carries — the voice never says something the document does not."""
    if level == LEVEL_EXECUTIVE:
        return " ".join(s.text for s in briefing.executive) or NO_EVIDENCE_TR
    items = briefing.detailed if level == LEVEL_DETAILED else briefing.technical
    if not items:
        return NO_EVIDENCE_TR
    parts = []
    for n, item in enumerate(items, start=1):
        body = " ".join(s.text for s in item.statements)
        parts.append(f"{cardinal(n).capitalize()}: {item.title}. {body}".strip())
    return " ".join(parts)


# ------------------------------------------------------------------ helpers


def _n(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _tr_list(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " ve " + parts[-1]


def _subsystem_tr(name: str | None) -> str:
    return _SUBSYSTEM_TR.get(name or "", name or "sistem")


#: The four truth kinds, in the order a person would want to hear them: what the code says,
#: what is installed, what is actually running, what the record proves.
TRUTH_ORDER: tuple[str, ...] = (
    "source_truth",
    "installed_truth",
    "runtime_truth",
    "evidence_truth",
)

_TRUTH_KIND_TR: dict[str, str] = {
    "source_truth": "Kaynak kodda",
    "installed_truth": "Kurulu olan",
    "runtime_truth": "Şu an çalışan",
    "evidence_truth": "Kayıtla kanıtlı",
}


def _opp_status(opportunity: dict[str, Any]) -> str:
    """An opportunity's status, case-folded.

    ``OpportunityStatus`` values are lowercase (``shadow_ready``), and the engine compared
    them against uppercase literals. Every comparison was therefore false: "gece kendi
    uzerinde ne gelistirdin" answered "sifir gelistirme" while a real SHADOW_READY
    candidate sat one query away - and the sibling branch that passed a lowercase filter to
    the database found it in the same run (M17 rehearsal, 2026-09-05).
    """
    return str(opportunity.get("status") or "").strip().lower()


#: In-flight lab statuses, lowercase, as the database stores them.
_LAB_IN_FLIGHT = ("researching", "design_ready", "building", "testing", "evaluating")
STATUS_SHADOW_READY = "shadow_ready"
STATUS_LIVE = "live"


def _production_action_names(policy: dict[str, Any]) -> list[str]:
    """The first few production action names, as strings, for one spoken sentence."""
    return [str(a) for a in (policy.get("production_actions") or [])][:4]


def _truth_kind_phrase(by_kind: dict[str, int]) -> str:
    """ "iki kaynak, bir çalışan" - only the kinds that actually have observations."""
    parts = [
        f"{cardinal(by_kind[kind])} {_TRUTH_KIND_TR.get(kind, kind).lower()}"
        for kind in TRUTH_ORDER
        if by_kind.get(kind)
    ]
    return _tr_list(parts) if parts else "hiçbiri"


def _said(value: Any, *, max_len: int = MAX_EVIDENCE_CHARS) -> str:
    """Free text from a stored row, made safe to read aloud.

    Lessons, opportunities, goals and procedural memories carry text that did not
    originate in this engine: an incident's evidence blob, an exception message, a
    captured page. The ledger's HTTP route screens what it receives, but that is one
    ingestion path among several, and none of the others passed through it - so this
    is screened again at the point it becomes speech, which is the only place every
    path meets (independent security review, 2026-09-05).

    It replaces rather than refuses: a poisoned row costs one sentence of the answer,
    never the whole answer, and the evidence reference still points at the record.
    """
    return safe_evidence_text(value, max_len=max_len)


def _plain(value: Any, *, max_len: int = 64) -> str:
    """A version, commit, file name or count as spoken text: one line, bounded, no
    control characters. Ledger detail values come from owner scripts and evidence files;
    the route screens them for instructions, and this keeps them short and flat."""
    text = " ".join(str(value if value is not None else "").split())
    return text[:max_len]


def _short(identifier: str | None) -> str:
    """Identifiers are spoken by their first block; the full value stays in the evidence
    references, where it can be read rather than listened to."""
    if not identifier:
        return ""
    return str(identifier).split("-", 1)[0][:12]


def _module_key(name: str) -> str:
    """ "Diagnostic Observer" and "diagnostic_observer" are the same module."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _iso(dt: datetime) -> str:
    """Spoken-friendly UTC time: a date, a space, hours and minutes. Never an ISO 'T'
    stamp - that is read aloud as a word, and the narration normaliser does not
    expect one inside prose (found on the real dev database)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _qualified_for(events: list[EventView], job_id: str | None) -> EventView | None:
    for ev in events:
        if ev.event_type == "research.qualified" and (
            job_id is None or ev.research_job_id == job_id
        ):
            return ev
    return None


# ------------------------------------------------------------------ research


def _needs_owner_action(recent: list[EventView]) -> tuple[bool, EventView | None]:
    """Whether anything in the window still needs the owner: an open failure or a
    security event with nothing completed after it on the same subsystem."""
    for ev in recent:
        if owner_relevance(ev) in (RELEVANCE_FAILURE, RELEVANCE_SECURITY):
            later_ok = any(
                other.subsystem == ev.subsystem
                and other.status == "completed"
                and other.occurred_at > ev.occurred_at
                for other in recent
            )
            if not later_ok:
                return True, ev
    return False, None


def _research_executive(
    ev: EventView,
    qualified: EventView | None,
    recent: list[EventView] | None = None,
    report: dict[str, Any] | None = None,
) -> list[Statement]:
    """The owner briefing: two to four sentences - the RESULT of the research, why it
    matters, and whether anything needs the owner - each tied to the evidence that
    supports it.

    M18.2 DEFECT 2 (ADR-0067): this used to build its outcome sentence from
    ``ev.detail`` — the pipeline's own counts ("N farklı kaynaktan M sonuç üretti ve K
    uygun olmayan sayfayı eledi") — so the owner heard diagnostics instead of what was
    found. It now consumes :class:`app.research.result.ResearchResult`, built ONLY
    from the validated report's findings; counts and eliminated-page tallies moved to
    ``_research_technical`` and to the explicit diagnostic query kinds
    (``QUERY_RESEARCH_PROBLEMS`` / ``QUERY_REJECTED_PAGES``).
    """
    refs = (ev.ref, *ev.evidence_refs)
    out: list[Statement] = []
    if qualified is not None:
        out.append(
            Statement(
                "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti.",
                LABEL_FACT,
                (qualified.ref, *qualified.evidence_refs),
            )
        )
    d = ev.detail or {}
    topic = str((report or {}).get("topic") or d.get("topic") or "")
    result = ResearchResult.from_report_json(report, topic=topic)
    text = spoken_result(result)
    if qualified is not None and text.startswith("Efendim, "):
        # The qualification sentence above already addressed the owner; avoid saying
        # "Efendim" twice in the same two-sentence breath.
        text = text[len("Efendim, ") :]
        text = text[:1].upper() + text[1:]
    out.append(Statement(text, LABEL_FACT if not result.insufficient else LABEL_UNCERTAINTY, refs))
    needs_action, culprit = _needs_owner_action(recent or [])
    if needs_action and culprit is not None:
        out.append(
            Statement(
                f"Müdahalenizi gerektiren bir konu var: {culprit.factual_summary}",
                LABEL_FACT,
                (culprit.ref,),
            )
        )
    else:
        closing = "Şu anda müdahalenizi gerektiren bir sorun yok."
        if qualified is not None and (qualified.detail or {}).get("pagentos_chrome_after") == 0:
            closing = "Tarayıcı temiz kapandı; şu anda müdahalenizi gerektiren bir sorun yok."
        out.append(Statement(closing, LABEL_INFERENCE, tuple(e.ref for e in (recent or [])[:5])))
    return out


def _research_detailed(ev: EventView, report: dict[str, Any] | None) -> list[BriefingItem]:
    """The findings, one item each. M18.2 DEFECT 2 (ADR-0067): the pipeline's own
    "Elenen sayfalar" (eliminated-pages) tally used to be appended here too — a
    detailed answer about what was FOUND is not the place for how many pages were
    rejected finding it. That tally now lives only in ``_research_technical`` and in
    the explicit ``QUERY_REJECTED_PAGES`` question."""
    items: list[BriefingItem] = []
    refs = (ev.ref, *ev.evidence_refs)
    if report:
        sources = {str(s.get("id")): s for s in report.get("sources", []) if isinstance(s, dict)}
        for f in report.get("findings", []):
            if not isinstance(f, dict):
                continue
            statements = [Statement(_said(f.get("summary")), LABEL_FACT, refs)]
            why = _said(f.get("why_it_matters"))
            if why:
                statements.append(Statement(f"Neden önemli: {why}", LABEL_INFERENCE, refs))
            cited = [sources.get(str(eid)) for eid in (f.get("evidence_ids") or [])]
            names = [_said(s.get("publisher") or s.get("title"), max_len=80) for s in cited if s]
            if names:
                statements.append(Statement(f"Kaynak: {_tr_list(names)}.", LABEL_FACT, refs))
            items.append(BriefingItem(_said(f.get("title")) or "Bulgu", tuple(statements)))
            if len(items) >= MAX_DETAILED_ITEMS:
                break  # the rest stays in the report artifact; "hepsini oku" reads it
    if not items:
        items.append(
            BriefingItem(
                "Bulgular",
                (Statement("Bulguların ayrıntısı bu kayıtta yok.", LABEL_UNCERTAINTY, refs),),
            )
        )
    return items


def _research_technical(
    ev: EventView, qualified: EventView | None, report: dict[str, Any] | None
) -> list[BriefingItem]:
    """A concise technical briefing: versions, evidence, failures, architecture - not a
    recital of every identifier and count. Identifiers stay in the report artifact and the
    evidence references; a failure is the one thing that earns its details here."""
    d = ev.detail or {}
    refs = (ev.ref, *ev.evidence_refs)
    q = (qualified.detail or {}) if qualified is not None else {}
    qrefs = (qualified.ref, *qualified.evidence_refs) if qualified is not None else refs
    items: list[BriefingItem] = []
    # The ledger event's own detail IS the pipeline's stats, flattened onto it
    # (app.ledger.service.build_research_completed_event); re-wrapping it under
    # "stats" lets this reuse the one ResearchDiagnostics constructor rather than
    # re-deriving the same numbers by hand a second time.
    diag = ResearchDiagnostics.from_report_json({"stats": d, **d})

    versions: list[str] = []
    policy = ev.version or q.get("cloud_policy_version")
    if policy:
        versions.append(f"Research policy v{_plain(policy)} çalıştı")
    if q.get("installed_release"):
        versions.append(
            f"browser worker {_plain(q.get('installed_release'))} "
            + ("değişmedi, deployment gerekmedi" if not q.get("deployed") else "yeniden dağıtıldı")
        )
    if d.get("synthesis_provider"):
        versions.append(f"sentez sağlayıcısı {_plain(d.get('synthesis_provider'))}")
    if versions:
        items.append(
            BriefingItem("Sürümler", (Statement("; ".join(versions) + ".", LABEL_FACT, qrefs),))
        )

    evidence_bits = [
        f"{diag.discovered_count} aday keşfedildi, {diag.fetched_count} sayfa getirildi, "
        f"{_n(d.get('evidence'))} kanıt kabul edildi, {diag.rejected_pages} sayfa elendi."
    ]
    if q:
        checks = ["kanıt kontrolleri geçti"]
        if "pagentos_chrome_after" in q:
            checks.append(
                "tarayıcı temizliği geçti"
                if _n(q.get("pagentos_chrome_after")) == 0
                else (
                    "tarayıcı temizliği başarısız "
                    f"({_n(q.get('pagentos_chrome_after'))} süreç kaldı)"
                )
            )
        evidence_bits.append(_tr_list(checks).capitalize() + ".")
    items.append(
        BriefingItem("Kanıt", tuple(Statement(t, LABEL_FACT, refs) for t in evidence_bits))
    )

    # The pipeline's own diagnostics (app.research.result.ResearchDiagnostics): which
    # pages were eliminated and why, and how much of the run's own output was
    # self-rejected. Moved here from the DETAILED level (M18.2 DEFECT 2, ADR-0067) -
    # this is exactly what "hangi sayfalar elendi?" / "araştırma sırasında ne sorun
    # oldu?" ask for, and never what the executive summary volunteers.
    if diag.rejected_by_reason:
        parts = [f"{_REJECTION_TR.get(r, r)}: {c}" for r, c in diag.rejected_by_reason.items()]
        items.append(
            BriefingItem(
                "Elenen sayfalar",
                (
                    Statement(
                        f"Toplam {diag.rejected_pages} sayfa elendi; {_tr_list(parts)}.",
                        LABEL_FACT,
                        refs,
                    ),
                ),
            )
        )
    if diag.quarantined_pages or diag.refused_pages:
        gate_bits: list[str] = []
        if diag.quarantined_pages:
            gate_bits.append(
                f"{cardinal(diag.quarantined_pages)} öğe kalite kapısında karantinaya alındı"
            )
        if diag.refused_pages:
            gate_bits.append(
                f"{cardinal(diag.refused_pages)} ifade şüpheli içerik nedeniyle reddedildi"
            )
        items.append(
            BriefingItem(
                "Kalite kapısı",
                (Statement(_tr_list(gate_bits).capitalize() + ".", LABEL_FACT, refs),),
            )
        )

    error_class = d.get("error_class") or (ev.result if ev.status == "failed" else None)
    if error_class:
        items.append(
            BriefingItem(
                "Hata",
                (Statement(f"Hata sınıfı {_plain(error_class)}.", LABEL_FACT, refs),),
            )
        )

    items.append(
        BriefingItem(
            "Mimari",
            (
                Statement(
                    "Keşif DuckDuckGo ile, sayfalar cihazdaki Chrome ile getirildi; kalite "
                    "kapısı sentezden önce çalıştı ve rapor bir artefakt olarak saklandı.",
                    LABEL_INFERENCE,
                    refs,
                ),
            ),
        )
    )
    return items


# ------------------------------------------------------------------ generic


def _generic_executive(ev: EventView, recent: list[EventView] | None = None) -> list[Statement]:
    """Two or three sentences for any other latest activity: what it was, and whether the
    owner is needed - the same closing rule as the research briefing."""
    out = [
        Statement(
            f"Efendim, en son {_subsystem_tr(ev.subsystem)} tarafında: {ev.factual_summary}",
            LABEL_FACT,
            (ev.ref, *ev.evidence_refs),
        )
    ]
    needs_action, culprit = _needs_owner_action(recent or [])
    if needs_action and culprit is not None:
        out.append(
            Statement(
                f"Müdahalenizi gerektiren bir konu var: {culprit.factual_summary}",
                LABEL_FACT,
                (culprit.ref,),
            )
        )
    else:
        out.append(
            Statement(
                "Şu anda müdahalenizi gerektiren bir sorun yok.",
                LABEL_INFERENCE,
                tuple(e.ref for e in (recent or [])[:5]),
            )
        )
    return out


def _event_item(ev: EventView) -> BriefingItem:
    statements = [Statement(ev.factual_summary, LABEL_FACT, (ev.ref, *ev.evidence_refs))]
    when = _iso(ev.occurred_at)
    statements.append(Statement(f"Zaman: {when}; durum: {ev.status}.", LABEL_FACT, (ev.ref,)))
    if ev.version:
        statements.append(Statement(f"Sürüm: {ev.version}.", LABEL_FACT, (ev.ref,)))
    return BriefingItem(f"{_subsystem_tr(ev.subsystem)} — {ev.event_type}", tuple(statements))


def _call(source: Any, name: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Ask an evidence source for something it may not have.

    The learning, goal and evolution subsystems arrive over several releases; an older
    source (or a deployment without them) simply has no such method, and the engine then
    says it has no record instead of pretending.
    """
    getter = getattr(source, name, None)
    if getter is None:
        return []
    try:
        return list(getter(**kwargs) or [])
    except Exception:  # noqa: BLE001 - a missing subsystem is an absence, not a failure
        return []


def _call_obj(source: Any, name: str, **kwargs: Any) -> dict[str, Any] | None:
    """The dict-shaped sibling of :func:`_call`.

    The world model and the authority policy answer with ONE object, not a list, and
    `list(a_dict)` would quietly yield its keys - an answer made of field names. Absence
    is None here, and the caller turns None into "I cannot read that", never into an
    empty-but-confident answer.
    """
    getter = getattr(source, name, None)
    if getter is None:
        return None
    try:
        result = getter(**kwargs)
    except Exception:  # noqa: BLE001 - a missing subsystem is an absence, not a failure
        return None
    return result if isinstance(result, dict) else None


def _lesson_ref(lesson: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "experience_lesson", "ref": str(lesson.get("lesson_id", ""))}


def _opportunity_ref(opportunity: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "evolution_opportunity", "ref": str(opportunity.get("opportunity_id", ""))}


def _goal_ref(goal: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "goal", "ref": str(goal.get("goal_id", ""))}


def _lesson_item(lesson: dict[str, Any]) -> BriefingItem:
    ref = _lesson_ref(lesson)
    statements = [Statement(_said(lesson.get("statement")), LABEL_FACT, (ref,))]
    if lesson.get("root_cause"):
        statements.append(
            Statement(f"Kök neden: {_said(lesson['root_cause'])}", LABEL_FACT, (ref,))
        )
    if lesson.get("resolution"):
        statements.append(Statement(f"Çözüm: {_said(lesson['resolution'])}", LABEL_FACT, (ref,)))
    if str(lesson.get("status")) == "candidate":
        statements.append(
            Statement("Bu ders henüz aday; yeterince tekrarlanmadı.", LABEL_UNCERTAINTY, (ref,))
        )
    return BriefingItem(_said(lesson.get("title")) or "Ders", tuple(statements))


def _lesson_technical(lessons: list[dict[str, Any]]) -> list[BriefingItem]:
    items: list[BriefingItem] = []
    for lesson in lessons[:MAX_DETAILED_ITEMS]:
        ref = _lesson_ref(lesson)
        items.append(
            BriefingItem(
                _said(lesson.get("title")) or "Ders",
                (
                    Statement(
                        f"Skor {_plain(lesson.get('score'))}; tekrar "
                        f"{_n(lesson.get('recurrence'))}; güven "
                        f"{_plain(lesson.get('confidence'))}; durum {lesson.get('status')}.",
                        LABEL_FACT,
                        (ref,),
                    ),
                ),
            )
        )
    return items


def _opportunity_item(opportunity: dict[str, Any]) -> BriefingItem:
    ref = _opportunity_ref(opportunity)
    statements = [Statement(_said(opportunity.get("statement")), LABEL_FACT, (ref,))]
    statements.append(
        Statement(
            f"Durum: {opportunity.get('status')}."
            + (" Canlıda değil." if _opp_status(opportunity) != STATUS_LIVE else ""),
            LABEL_FACT,
            (ref,),
        )
    )
    return BriefingItem(_said(opportunity.get("title")) or "Fırsat", tuple(statements))


def _opportunity_technical(opportunities: list[dict[str, Any]]) -> list[BriefingItem]:
    items: list[BriefingItem] = []
    for opportunity in opportunities[:MAX_DETAILED_ITEMS]:
        ref = _opportunity_ref(opportunity)
        scores = opportunity.get("scores") or {}
        items.append(
            BriefingItem(
                _said(opportunity.get("title")) or "Fırsat",
                (
                    Statement(
                        f"Bileşik skor {_plain(scores.get('composite'))}; risk "
                        f"{_plain(scores.get('operational_risk'))}; maliyet "
                        f"{_plain(scores.get('engineering_cost'))}; durum "
                        f"{opportunity.get('status')}.",
                        LABEL_FACT,
                        (ref,),
                    ),
                ),
            )
        )
    return items


def _goal_item(goal: dict[str, Any]) -> BriefingItem:
    ref = _goal_ref(goal)
    statements = [
        Statement(_said(goal.get("intent") or goal.get("title")), LABEL_FACT, (ref,)),
        Statement(f"Durum: {goal.get('status')}.", LABEL_FACT, (ref,)),
    ]
    blockers = goal.get("blockers") or []
    if blockers:
        statements.append(
            Statement(f"Engel: {_tr_list([_said(b) for b in blockers[:3]])}.", LABEL_FACT, (ref,))
        )
    return BriefingItem(_said(goal.get("title")) or "Hedef", tuple(statements))


def _window_statement(events: list[EventView], query: ExplainQuery) -> Statement | None:
    if not events:
        return None
    failed = sum(1 for e in events if e.status == "failed")
    span = "bugün" if query.kind == QUERY_TODAY else "son yedi günde"
    text = f"{span.capitalize()} toplam {cardinal(len(events))} etkinlik kaydettim"
    text += (
        f"; {cardinal(failed)} tanesi başarısız oldu." if failed else "; hiçbiri başarısız olmadı."
    )
    return Statement(text, LABEL_FACT, tuple(e.ref for e in events[:20]))


# ------------------------------------------------------------------ compose


def explain(
    source: EvidenceSource,
    question: str,
    query: ExplainQuery,
    *,
    now: datetime | None = None,
    research_job_id: str | None = None,
) -> Briefing:
    """Retrieve, then compose. The order is the whole point.

    ``research_job_id`` (docs/DECISIONS.md ADR-0075) BINDS the answer to one completed
    research: a follow-up ("teknik anlat", "kaynakları söyle") is about the run the
    caller resolved, not about whatever happens to be latest in the ledger by the time
    the owner asks. It only ever narrows what is READ - it can never start, re-run or
    change a research, and when no event for that job is in the window the engine
    falls back to its ordinary "latest activity" behaviour rather than inventing one.
    """
    now = now or datetime.now(UTC)
    subsystems = (query.subsystem,) if query.subsystem else None
    recent = source.events(since=query.since, subsystems=subsystems, statuses=None, limit=100)
    all_recent = (
        recent
        if subsystems is None
        else source.events(since=query.since, subsystems=None, statuses=None, limit=100)
    )

    executive: list[Statement] = []
    detailed: list[BriefingItem] = []
    technical: list[BriefingItem] = []
    refs: list[dict[str, Any]] = []

    def add_refs(*groups: tuple[dict[str, Any], ...]) -> None:
        for group in groups:
            for ref in group:
                if ref not in refs:
                    refs.append(ref)

    answered_research_job_id: str | None = None
    answered_research_artifact_id: str | None = None
    facts: dict[str, Any] = {}
    #: Real activity, without the annotations that describe other events.
    activities = [e for e in recent if e.event_type not in _ANNOTATION_EVENT_TYPES]

    if query.kind in (QUERY_FAILURES, QUERY_WHY_FAILED):
        failed = [e for e in recent if e.status == "failed"]
        if not failed:
            executive.append(
                Statement(
                    "Bu dönemde başarısız bir etkinlik kaydım yok.",
                    LABEL_FACT,
                    tuple(e.ref for e in recent[:5]),
                )
            )
        else:
            last = failed[0]
            executive.append(
                Statement(
                    f"Efendim, en son başarısız olan {_subsystem_tr(last.subsystem)} işi: "
                    f"{last.factual_summary}",
                    LABEL_FACT,
                    (last.ref, *last.evidence_refs),
                )
            )
            if query.kind == QUERY_WHY_FAILED:
                reason = (last.detail or {}).get("error_class") or last.result
                if reason:
                    executive.append(
                        Statement(f"Kayıtlı hata sınıfı: {reason}.", LABEL_FACT, (last.ref,))
                    )
                else:
                    executive.append(
                        Statement(
                            "Hatanın nedeni kayıtta yer almıyor.", LABEL_UNCERTAINTY, (last.ref,)
                        )
                    )
            for e in failed[:10]:
                detailed.append(_event_item(e))
                technical.append(_event_item(e))
                add_refs((e.ref,), e.evidence_refs)
        executive.append(Statement("Bilginize.", LABEL_FACT, ()))

    elif query.kind in (QUERY_PROBLEMS_NOW, QUERY_MODULE_PROBLEM):
        incidents = source.open_incidents()
        if query.module:
            module_key = _module_key(query.module)
            incidents = [
                i for i in incidents if module_key in _module_key(str(i.get("component") or ""))
            ]
            module_events = [
                e
                for e in all_recent
                if (e.module and module_key in _module_key(e.module))
                or (e.related_module_id and module_key in _module_key(e.related_module_id))
            ]
        else:
            module_events = []
        critical = [e for e in recent if e.severity == "critical"]
        if not incidents and not critical and not module_events:
            target = f"{query.module} için" if query.module else "Şu anda"
            executive.append(
                Statement(
                    f"{target} açık bir sorun kaydım yok.",
                    LABEL_FACT,
                    tuple(e.ref for e in recent[:5]),
                )
            )
            if query.module and not module_events:
                executive.append(
                    Statement(
                        f"{query.module} adında bir modül için hiç kayıt bulamadım.",
                        LABEL_UNCERTAINTY,
                        (),
                    )
                )
        else:
            for inc in incidents:
                text = (
                    f"Açık olay: {inc.get('component')} — {inc.get('severity')}; "
                    f"{_n(inc.get('occurrence_count'))} kez görüldü."
                )
                ref = {"kind": "incident", "ref": str(inc.get("id"))}
                executive.append(Statement(text, LABEL_FACT, (ref,)))
                detailed.append(
                    BriefingItem(
                        str(inc.get("component") or "olay"), (Statement(text, LABEL_FACT, (ref,)),)
                    )
                )
                add_refs((ref,))
            for e in critical:
                executive.append(Statement(f"Kritik: {e.factual_summary}", LABEL_FACT, (e.ref,)))
                add_refs((e.ref,), e.evidence_refs)
            for e in module_events[:10]:
                detailed.append(_event_item(e))
                technical.append(_event_item(e))
                add_refs((e.ref,), e.evidence_refs)
            if module_events and not incidents and not critical:
                executive.append(
                    Statement(
                        f"{query.module} için kayıtlı bir hata yok; son durumu: "
                        f"{module_events[0].factual_summary}",
                        LABEL_FACT,
                        (module_events[0].ref,),
                    )
                )
        executive.append(Statement("Bilginize.", LABEL_FACT, ()))

    elif query.kind == QUERY_SINCE_YOU_LEFT:
        # The returning owner gets ONE briefing in the order they asked for: what was
        # completed, what was learned, what is shadow-ready, what failed, and what needs
        # them. Each part is a count with its evidence; the detail carries the items.
        completed = [
            e
            for e in activities
            if e.status == "completed" and owner_relevance(e) in MEANINGFUL_CLASSES
        ]
        failures = [e for e in activities if e.status == "failed"]
        lessons = _call(source, "lessons", limit=20)
        promoted = [lesson for lesson in lessons if lesson.get("status") == "promoted"]
        shadow = [
            o
            for o in _call(source, "opportunities", limit=20)
            if _opp_status(o) == STATUS_SHADOW_READY
        ]
        needs_action, culprit = _needs_owner_action(activities)
        if not activities and not lessons and not shadow:
            executive.append(
                Statement("Siz yokken kayda geçen bir iş olmadı.", LABEL_UNCERTAINTY, ())
            )
        else:
            executive.append(
                Statement(
                    f"Efendim, siz yokken {cardinal(len(completed))} işi tamamladım.",
                    LABEL_FACT,
                    tuple(e.ref for e in completed[:8]),
                )
            )
            if promoted:
                executive.append(
                    Statement(
                        f"{cardinal(len(promoted)).capitalize()} ders çıkardım; ilki: "
                        f"{_said(promoted[0].get('statement'))}",
                        LABEL_FACT,
                        (_lesson_ref(promoted[0]),),
                    )
                )
            if shadow:
                executive.append(
                    Statement(
                        f"{cardinal(len(shadow)).capitalize()} yetenek gölge durumda hazır; "
                        "hiçbiri canlıda değil.",
                        LABEL_FACT,
                        tuple(_opportunity_ref(o) for o in shadow[:5]),
                    )
                )
            if failures:
                executive.append(
                    Statement(
                        f"{cardinal(len(failures)).capitalize()} iş başarısız oldu.",
                        LABEL_FACT,
                        tuple(e.ref for e in failures[:5]),
                    )
                )
            if needs_action and culprit is not None:
                executive.append(
                    Statement(
                        f"Müdahalenizi gerektiren bir konu var: {culprit.factual_summary}",
                        LABEL_FACT,
                        (culprit.ref,),
                    )
                )
            else:
                executive.append(
                    Statement(
                        "Müdahalenizi gerektiren bir konu yok.",
                        LABEL_INFERENCE,
                        tuple(e.ref for e in activities[:5]),
                    )
                )
        for event in completed[:MAX_DETAILED_ITEMS]:
            detailed.append(_event_item(event))
            add_refs((event.ref,))
        for lesson in promoted[:3]:
            detailed.append(_lesson_item(lesson))
            add_refs((_lesson_ref(lesson),))
        for opportunity in shadow[:3]:
            detailed.append(_opportunity_item(opportunity))
            add_refs((_opportunity_ref(opportunity),))
        for event in failures[:3]:
            technical.append(_event_item(event))
            add_refs((event.ref,))

    elif query.kind == QUERY_LEARNED:
        lessons = _call(source, "lessons", limit=20)
        procedural = _call(source, "procedural_memories", limit=20)
        if not lessons and not procedural:
            executive.append(
                Statement("Henüz kayda geçmiş bir ders çıkarmadım.", LABEL_UNCERTAINTY, ())
            )
        else:
            promoted = [lesson for lesson in lessons if lesson.get("status") == "promoted"]
            candidates = [lesson for lesson in lessons if lesson.get("status") == "candidate"]
            # "sıfır dersi kalıcı hale getirdim" is technically true and reads as a
            # non-answer. Say the shape that actually holds.
            kept = len(promoted) + len(procedural)
            if kept:
                headline = (
                    f"Efendim, {cardinal(kept)} dersi kalıcı hale getirdim; "
                    f"{cardinal(len(candidates))} aday hâlâ değerlendirmede."
                )
            else:
                headline = (
                    f"Efendim, {cardinal(len(candidates))} ders adayım var; "
                    "henüz hiçbirini kalıcı hale getirmedim."
                )
            executive.append(
                Statement(
                    headline,
                    LABEL_FACT,
                    tuple(_lesson_ref(lesson) for lesson in (promoted + candidates)[:8]),
                )
            )
            # One lesson per DISTINCT statement: two incidents of the same defect class
            # compile to two rows carrying identical text, and reading it twice in a row
            # sounds like a stutter rather than like knowing two things. The rest stay in
            # the detailed level, where the owner can ask for them.
            top: list[dict[str, Any]] = []
            seen_statements: set[str] = set()
            for lesson in promoted or candidates:
                text = _said(lesson.get("statement") or lesson.get("title"))
                if not text or text in seen_statements:
                    continue
                seen_statements.add(text)
                top.append(lesson)
                if len(top) >= 2:
                    break
            for lesson in top:
                executive.append(
                    Statement(
                        _said(lesson.get("statement") or lesson.get("title")),
                        LABEL_FACT,
                        (_lesson_ref(lesson),),
                    )
                )
            for lesson in (promoted + candidates)[:MAX_DETAILED_ITEMS]:
                detailed.append(_lesson_item(lesson))
                add_refs((_lesson_ref(lesson),))
            for memory in procedural[:MAX_DETAILED_ITEMS]:
                detailed.append(
                    BriefingItem(
                        _said(memory.get("key")) or "Yordam",
                        (
                            Statement(
                                _said(memory.get("text")),
                                LABEL_FACT,
                                ({"kind": "memory", "ref": str(memory.get("memory_id"))},),
                            ),
                        ),
                    )
                )
            technical.extend(_lesson_technical(lessons))

    elif query.kind in (QUERY_EVOLUTION, QUERY_SHADOW_READY, QUERY_WHY_BUILT):
        opportunities = _call(source, "opportunities", limit=20)
        shadow = [o for o in opportunities if _opp_status(o) == STATUS_SHADOW_READY]
        building = [o for o in opportunities if _opp_status(o) in _LAB_IN_FLIGHT]
        if not opportunities:
            executive.append(
                Statement(
                    "Şu anda kendi üzerimde yürüttüğüm bir geliştirme kaydı yok.",
                    LABEL_UNCERTAINTY,
                    (),
                )
            )
        elif query.kind == QUERY_SHADOW_READY:
            if shadow:
                names = _tr_list([_said(o.get("title"), max_len=80) for o in shadow[:3]])
                executive.append(
                    Statement(
                        f"Efendim, {cardinal(len(shadow))} yetenek hazır ve gölge durumda: "
                        f"{names}. Hiçbiri canlı sistemde değil; onayınızı bekliyorum.",
                        LABEL_FACT,
                        tuple(_opportunity_ref(o) for o in shadow[:8]),
                    )
                )
            else:
                executive.append(
                    Statement(
                        "Şu anda canlıya alınmayı bekleyen hazır bir modül yok.",
                        LABEL_FACT,
                        tuple(_opportunity_ref(o) for o in opportunities[:5]),
                    )
                )
        elif query.kind == QUERY_WHY_BUILT:
            target = (shadow or building or opportunities)[0]
            executive.append(
                Statement(
                    f"{_said(target.get('title'))}: {_said(target.get('statement'))}",
                    LABEL_FACT,
                    (_opportunity_ref(target),),
                )
            )
            origin = target.get("origin") or []
            if origin:
                executive.append(
                    Statement(
                        f"Bu işi {cardinal(len(origin))} gerçek kayıt üzerine başlattım.",
                        LABEL_FACT,
                        tuple(dict(r) for r in origin[:5] if isinstance(r, dict)),
                    )
                )
            else:
                executive.append(
                    Statement("Bu fikrin dayandığı kayıt elimde yok.", LABEL_UNCERTAINTY, ())
                )
        else:
            executive.append(
                Statement(
                    (
                        f"Efendim, {cardinal(len(building))} geliştirme üzerinde çalışıyorum "
                        f"ve {cardinal(len(shadow))} tanesi gölge durumda hazır."
                        if building
                        else f"Efendim, {cardinal(len(shadow))} yeteneği bitirdim ve gölge "
                        "durumda hazır bekliyor; şu an elimde süren bir geliştirme yok."
                    ),
                    LABEL_FACT,
                    tuple(_opportunity_ref(o) for o in opportunities[:8]),
                )
            )
        for opportunity in (shadow + building)[:MAX_DETAILED_ITEMS]:
            detailed.append(_opportunity_item(opportunity))
            add_refs((_opportunity_ref(opportunity),))
        technical.extend(_opportunity_technical(shadow + building))
        if shadow:
            executive.append(
                Statement(
                    "Hiçbiri canlıya alınmadı; canlıya alma yetkisi bende değil.",
                    LABEL_FACT,
                    tuple(_opportunity_ref(o) for o in shadow[:3]),
                )
            )

    elif query.kind == QUERY_GOALS:
        goals = _call(source, "goals", limit=20)
        active = [
            g for g in goals if str(g.get("status")) in ("active", "blocked", "waiting_owner")
        ]
        achieved = [g for g in goals if str(g.get("status")) == "achieved"]
        if not goals:
            executive.append(Statement("Kayıtlı bir hedefim yok.", LABEL_UNCERTAINTY, ()))
        else:
            executive.append(
                Statement(
                    f"Efendim, {cardinal(len(active))} açık hedefim var; "
                    f"{cardinal(len(achieved))} tanesini tamamladım.",
                    LABEL_FACT,
                    tuple(_goal_ref(g) for g in goals[:8]),
                )
            )
            blocked = [g for g in goals if str(g.get("status")) in ("blocked", "waiting_owner")]
            if blocked:
                executive.append(
                    Statement(
                        f"{_said(blocked[0].get('title'))} sizin müdahalenizi bekliyor.",
                        LABEL_FACT,
                        (_goal_ref(blocked[0]),),
                    )
                )
        for goal in active[:MAX_DETAILED_ITEMS]:
            detailed.append(_goal_item(goal))
            add_refs((_goal_ref(goal),))

    elif query.kind in (QUERY_WORLD_STATE, QUERY_EYE_STATE):
        # "Kendi sisteminde şu anda ne görüyorsun?" - the four truth kinds, kept apart.
        # The whole point of the world model is that source truth is not runtime truth, so
        # the answer says WHICH KIND each fact is, and says out loud what it does not know.
        #
        # This is the BRIEFING form (REST /v1/explain, an artifact with three levels). The
        # spoken answer to the same question is not this: the realtime tools route
        # world_state / eye_state to app.state.now.compose_live_state, which speaks the
        # current state itself (docs/M18_ACTION_CONTRACT.md §3) - the owner heard counts of
        # truth kinds on 2026-09-06 and that is not "what do you see".
        snapshot = _call_obj(source, "world_state")
        if not snapshot:
            executive.append(Statement("Dünya modelim şu anda okunamıyor.", LABEL_UNCERTAINTY, ()))
        else:
            world_facts = [f for f in (snapshot.get("facts") or []) if isinstance(f, dict)]
            # Register what this answer rests on. Attaching a ref to a Statement is not the
            # same as citing it: only add_refs puts it in the briefing's evidence, which is
            # what provenance and the routing record read. Three branches attached refs and
            # cited nothing, so the M17 production check found empty evidence behind true
            # sentences (2026-09-05).
            add_refs(
                ({"kind": "world_snapshot", "ref": str(snapshot.get("observed_at") or "now")},)
            )
            add_refs(
                tuple({"kind": "world_fact", "ref": str(f.get("key"))} for f in world_facts[:12])
            )
            uncertainties = [
                u for u in (snapshot.get("uncertainties") or []) if isinstance(u, dict)
            ]
            by_kind: dict[str, int] = {}
            for fact in world_facts:
                by_kind[str(fact.get("truth_kind"))] = (
                    by_kind.get(str(fact.get("truth_kind")), 0) + 1
                )
            stale = [f for f in world_facts if bool(f.get("stale"))]
            executive.append(
                Statement(
                    f"Efendim, şu an {cardinal(len(world_facts))} doğrulanmış gözlemim var: "
                    f"{_truth_kind_phrase(by_kind)}.",
                    LABEL_FACT,
                    ({"kind": "world_snapshot", "ref": str(snapshot.get("observed_at") or "now")},),
                )
            )
            if uncertainties:
                executive.append(
                    Statement(
                        f"{cardinal(len(uncertainties))} konuda emin değilim; tahmin etmiyorum.",
                        LABEL_UNCERTAINTY,
                        (),
                    )
                )
            if stale:
                executive.append(
                    Statement(
                        f"{cardinal(len(stale))} gözlem bayatlamış olabilir; "
                        "onları güncel diye sunmuyorum.",
                        LABEL_UNCERTAINTY,
                        (),
                    )
                )
            for kind_name in TRUTH_ORDER:
                rows = [f for f in world_facts if str(f.get("truth_kind")) == kind_name]
                if not rows:
                    continue
                statements = []
                for fact in rows[:4]:
                    label = LABEL_FACT if not fact.get("stale") else LABEL_UNCERTAINTY
                    statements.append(
                        Statement(
                            f"{_said(fact.get('key'), max_len=60)}: {_plain(fact.get('value'))}"
                            + (" (bayat)" if fact.get("stale") else ""),
                            label,
                            ({"kind": "world_fact", "ref": str(fact.get("key"))},),
                        )
                    )
                detailed.append(
                    BriefingItem(_TRUTH_KIND_TR.get(kind_name, kind_name), tuple(statements))
                )
            for unc in uncertainties[:MAX_DETAILED_ITEMS]:
                technical.append(
                    BriefingItem(
                        _said(unc.get("subject"), max_len=60) or "Bilinmeyen",
                        (
                            Statement(
                                f"Neden bilinmiyor: {_said(unc.get('reason'), max_len=120)}.",
                                LABEL_UNCERTAINTY,
                                ({"kind": "world_uncertainty", "ref": str(unc.get("subject"))},),
                            ),
                        ),
                    )
                )

    elif query.kind == QUERY_SELF_CODE:
        # "Kendi kodun hakkında ne biliyorsun?" - from the index, never from memory of
        # having written it. An empty index is answered as an empty index.
        overview = _call_obj(source, "code_overview", limit=8)
        modules = (overview or {}).get("modules") or []
        total = int((overview or {}).get("module_count") or 0)
        if not overview or total == 0:
            executive.append(
                Statement(
                    "Kendi kodumun dizinini henüz çıkarmadım; bu yüzden modüllerim hakkında "
                    "bir şey iddia etmiyorum.",
                    LABEL_UNCERTAINTY,
                    (),
                )
            )
        else:
            areas = sorted({str(m.get("owner_area") or "") for m in modules if m.get("owner_area")})
            add_refs(({"kind": "code_index", "ref": "code_modules"},))
            add_refs(
                tuple({"kind": "code_module", "ref": str(m.get("module_id"))} for m in modules)
            )
            executive.append(
                Statement(
                    f"Efendim, kendi kodumdan {cardinal(total)} modül tanıyorum"
                    + (f"; başlıca alanlar {_tr_list(areas[:3])}." if areas else "."),
                    LABEL_FACT,
                    ({"kind": "code_index", "ref": "code_modules"},),
                )
            )
            for module in modules[:MAX_DETAILED_ITEMS]:
                ref = {"kind": "code_module", "ref": str(module.get("module_id"))}
                statements = [
                    Statement(
                        f"{_said(module.get('module_id'), max_len=80)}: "
                        f"{_said(module.get('purpose'), max_len=160) or 'amacı kayıtlı değil'}.",
                        LABEL_FACT,
                        (ref,),
                    ),
                    Statement(
                        f"Üretim durumu {_plain(module.get('production_state'))}.",
                        LABEL_FACT,
                        (ref,),
                    ),
                ]
                adrs = [str(a) for a in (module.get("adr_refs") or [])][:3]
                if adrs:
                    statements.append(Statement(f"Kararlar: {_tr_list(adrs)}.", LABEL_FACT, (ref,)))
                detailed.append(
                    BriefingItem(
                        _said(module.get("module_id"), max_len=80) or "Modül", tuple(statements)
                    )
                )

    elif query.kind == QUERY_CAN_DEPLOY:
        # "Bunu canlıya alabilir misin?" - the answer is the boundary itself, read from the
        # authority module rather than described from memory, so the spoken rule and the
        # enforced rule cannot drift apart.
        policy = _call_obj(source, "authority_policy")
        shadow = _call(source, "opportunities", statuses=("shadow_ready",), limit=5)
        if not policy:
            executive.append(
                Statement(
                    "Yetki sınırını şu anda okuyamıyorum; okuyamadığım bir sınıra "
                    "dayanarak canlıya alma sözü vermem.",
                    LABEL_UNCERTAINTY,
                    (),
                )
            )
        else:
            ref = {"kind": "authority_policy", "ref": "app.evolution.authority"}
            add_refs((ref,))
            add_refs(
                tuple(
                    {"kind": "root_policy", "ref": str(item.get("policy_id"))}
                    for item in (policy.get("root_policies") or [])[:8]
                )
            )
            add_refs(tuple(_opportunity_ref(o) for o in shadow[:3]))
            # Both halves of the rule, in the order that answers the question asked. "No,
            # I can never deploy" was the old answer and it was WRONG: the policy forbids
            # autonomous promotion, not owner-authorised release, and telling the owner
            # they cannot ask for something they can ask for is its own kind of untruth
            # (owner authority policy, 2026-09-05).
            if policy.get("owner_authorised_release_permitted"):
                executive.append(
                    Statement(
                        "Evet efendim, sizin açık onayınızla alabilirim; kendi başıma alamam.",
                        LABEL_FACT,
                        (ref,),
                    )
                )
                executive.append(
                    Statement(
                        "Geliştirme motorum laboratuvar yetkisiyle çalışır ve hiçbir üretim "
                        "yetkisi taşımaz; kendiliğinden canlıya çıkaramam.",
                        LABEL_FACT,
                        (ref,),
                    )
                )
                executive.append(
                    Statement(
                        "Siz açıkça 'canlıya al' derseniz, kimliği doğrulanmış onayınıza "
                        "bağlı olarak gerekli kontrolleri ve yeterlilik sürecini uygular, "
                        "sonra sürümü yayına alırım.",
                        LABEL_FACT,
                        (ref,),
                    )
                )
                if policy.get("asking_is_not_authorising"):
                    executive.append(
                        Statement(
                            "Bu soru tek başına bir dağıtım başlatmaz.",
                            LABEL_FACT,
                            (ref,),
                        )
                    )
            else:
                executive.append(
                    Statement("Hayır efendim, canlıya kendim alamam.", LABEL_FACT, (ref,))
                )
                executive.append(
                    Statement(
                        "Canlıya alma sizin onayınızı ve yeterlilik testini gerektirir.",
                        LABEL_FACT,
                        (ref,),
                    )
                )
            if policy.get("lab_holds_any_production_grant"):
                executive.append(
                    Statement(
                        "Dikkat: laboratuvar yetkisi bir üretim izni taşıyor görünüyor; "
                        "bu bir hata olurdu.",
                        LABEL_UNCERTAINTY,
                        (ref,),
                    )
                )
            if shadow:
                names = _tr_list([_said(o.get("title"), max_len=60) for o in shadow[:2]])
                executive.append(
                    Statement(
                        f"Bekleyen aday: {names}; gölgeye hazır, canlıda değil.",
                        LABEL_FACT,
                        tuple(_opportunity_ref(o) for o in shadow[:2]),
                    )
                )
            detailed.append(
                BriefingItem(
                    "Yetki sınırı",
                    (
                        Statement(
                            "Üretim eylemleri: "
                            f"{_tr_list(_production_action_names(policy))}. "
                            "Hiçbiri laboratuvar yetkisiyle yapılamaz.",
                            LABEL_FACT,
                            (ref,),
                        ),
                        Statement(
                            f"Değiştirilemez kök politikalar: "
                            f"{cardinal(len(policy.get('root_policies') or []))} adet.",
                            LABEL_FACT,
                            (ref,),
                        ),
                    ),
                )
            )
            for item in (policy.get("root_policies") or [])[:MAX_DETAILED_ITEMS]:
                technical.append(
                    BriefingItem(
                        _said(item.get("title"), max_len=70) or "Politika",
                        (
                            Statement(
                                _said(item.get("statement"), max_len=200),
                                LABEL_FACT,
                                ({"kind": "root_policy", "ref": str(item.get("policy_id"))},),
                            ),
                        ),
                    )
                )

    elif query.kind == QUERY_TESTS:
        test_events = [
            e
            for e in recent
            if e.event_type.endswith((".tests_passed", ".tests_failed"))
            or "test" in (e.action or "")
        ]
        if not test_events:
            executive.append(
                Statement("Kayıtlarımda test sonucu bulamadım.", LABEL_UNCERTAINTY, ())
            )
        else:
            passed = [e for e in test_events if e.status == "completed"]
            executive.append(
                Statement(
                    f"Efendim, son {cardinal(len(test_events))} test kaydının "
                    f"{cardinal(len(passed))} tanesi geçti.",
                    LABEL_FACT,
                    tuple(e.ref for e in test_events[:8]),
                )
            )
            for event in test_events[:MAX_DETAILED_ITEMS]:
                detailed.append(_event_item(event))
                add_refs((event.ref,))

    else:
        # last_activity / today / subsystem_status / research_detail / technical / evidence
        # Owner relevance, not chronology: narration and voice bookkeeping never lead an
        # executive briefing unless the owner asked about that subsystem.
        asked_meta = query.subsystem in ("voice", "ledger")
        meaningful = [e for e in activities if owner_relevance(e) in MEANINGFUL_CLASSES]
        pool = activities if asked_meta or not meaningful else meaningful
        finished = [e for e in pool if e.status in ("completed", "failed")]
        latest = finished[0] if finished else (pool[0] if pool else None)
        if research_job_id:
            # ADR-0075: the caller bound this question to a COMPLETED research, so the
            # answer is about that run - not about whatever finished most recently.
            # Identity, by the job's own id; never by re-matching the topic text.
            bound_event = next(
                (
                    e
                    for e in (*recent, *all_recent)
                    if e.event_type == "research.completed"
                    and e.research_job_id == research_job_id
                ),
                None,
            )
            if bound_event is not None:
                latest = bound_event
        if latest is None:
            executive.append(Statement(NO_EVIDENCE_TR, LABEL_UNCERTAINTY, ()))
        elif latest.event_type == "research.completed":
            qualified = _qualified_for(all_recent, latest.research_job_id)
            report = (
                source.research_report(latest.research_job_id) if latest.research_job_id else None
            )
            executive.extend(_research_executive(latest, qualified, activities, report))
            answered_research_job_id = latest.research_job_id
            answered_research_artifact_id = (
                str(report["artifact_id"]) if report and report.get("artifact_id") else None
            )
            d = latest.detail or {}
            facts = {
                "findings": _n(d.get("findings")),
                "sources": _n(d.get("sources")),
                "rejected": _n(d.get("rejected")),
                "discovered": _n(d.get("discovered")),
                "fetched": _n(d.get("fetched")),
                "evidence": _n(d.get("evidence")),
                "rejected_by_reason": dict(d.get("rejected_by_reason") or {}),
                "event_type": latest.event_type,
                "source": latest.source,
                "qualified": qualified is not None,
                "verdict": (qualified.detail or {}).get("verdict") if qualified else None,
                "installed_release": (qualified.detail or {}).get("installed_release")
                if qualified
                else None,
                "deployed": (qualified.detail or {}).get("deployed") if qualified else None,
                "policy_version": latest.version,
            }
            detailed.extend(_research_detailed(latest, report))
            technical.extend(_research_technical(latest, qualified, report))
            add_refs((latest.ref,), latest.evidence_refs)
            if qualified is not None:
                add_refs((qualified.ref,), qualified.evidence_refs)
            if report and report.get("artifact_id"):
                add_refs(({"kind": "artifact", "ref": str(report["artifact_id"])},))
        elif latest.event_type == "research.failed":
            # M18.2 DEFECT 2 / spec item 1: a failed quality gate is said so, concisely
            # and truthfully, never dressed up as a finding and never read from the
            # failure log itself.
            d = latest.detail or {}
            error_class = str(d.get("error_class") or "").upper()
            reason = (
                REASON_INSUFFICIENT_FINDINGS
                if "FINDING" in error_class
                else REASON_INSUFFICIENT_EVIDENCE
                if "EVIDENCE" in error_class
                else None
            )
            failure_result = ResearchResult.insufficient_evidence(reason=reason)
            executive.append(
                Statement(
                    spoken_result(failure_result),
                    LABEL_UNCERTAINTY,
                    (latest.ref, *latest.evidence_refs),
                )
            )
            # Same closing rule every other executive branch here follows (an open
            # failure with nothing resolved since IS still something the owner needs
            # to hear about) — this branch only changes what the OUTCOME sentence says,
            # never whether an unresolved failure is surfaced.
            needs_action, culprit = _needs_owner_action(activities)
            if needs_action and culprit is not None:
                executive.append(
                    Statement(
                        f"Müdahalenizi gerektiren bir konu var: {culprit.factual_summary}",
                        LABEL_FACT,
                        (culprit.ref,),
                    )
                )
            else:
                executive.append(
                    Statement(
                        "Şu anda müdahalenizi gerektiren bir sorun yok.",
                        LABEL_INFERENCE,
                        tuple(e.ref for e in activities[:5]),
                    )
                )
            detailed.append(_event_item(latest))
            technical.append(_event_item(latest))
            add_refs((latest.ref,), latest.evidence_refs)
        else:
            executive.extend(_generic_executive(latest, activities))
            detailed.append(_event_item(latest))
            technical.append(_event_item(latest))
            add_refs((latest.ref,), latest.evidence_refs)
        if query.kind in (QUERY_TODAY, QUERY_SUBSYSTEM_STATUS):
            window = _window_statement(pool, query)
            if window is not None:
                executive.insert(
                    len(executive) - 1
                    if executive and executive[-1].text == "Bilginize."
                    else len(executive),
                    window,
                )
        for e in finished[1:6]:
            detailed.append(_event_item(e))
            add_refs((e.ref,))
        if query.kind == QUERY_EVIDENCE and latest is not None:
            listed = [f"{r.get('kind')}: {r.get('ref')}" for r in refs[:8]]
            executive.insert(
                0,
                Statement(
                    f"Kanıt olarak {cardinal(len(refs))} kayıt tutuyorum: {_tr_list(listed)}.",
                    LABEL_FACT,
                    tuple(refs[:8]),
                ),
            )

    return Briefing(
        question=question,
        query=query,
        generated_at=now,
        executive=tuple(executive),
        detailed=tuple(detailed),
        technical=tuple(technical),
        evidence_refs=tuple(refs),
        research_job_id=answered_research_job_id,
        research_artifact_id=answered_research_artifact_id,
        facts=facts,
    )


__all__ = [
    "LABEL_FACT",
    "LABEL_INFERENCE",
    "MEANINGFUL_CLASSES",
    "RELEVANCE_CLASSES",
    "LABEL_UNCERTAINTY",
    "LEVEL_SECTIONS",
    "NO_EVIDENCE_TR",
    "SECTION_DETAILED",
    "SECTION_EVIDENCE",
    "SECTION_EXECUTIVE",
    "SECTION_TECHNICAL",
    "STATEMENT_LABELS",
    "Briefing",
    "BriefingItem",
    "EventView",
    "EvidenceSource",
    "Statement",
    "explain",
    "owner_relevance",
    "render_markdown",
    "speech_for_level",
]
