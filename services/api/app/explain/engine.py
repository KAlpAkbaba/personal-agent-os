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
    QUERY_EVIDENCE,
    QUERY_FAILURES,
    QUERY_MODULE_PROBLEM,
    QUERY_PROBLEMS_NOW,
    QUERY_RESEARCH_DETAIL,
    QUERY_SUBSYSTEM_STATUS,
    QUERY_TECHNICAL,
    QUERY_TODAY,
    QUERY_WHY_FAILED,
    ExplainQuery,
)
from app.narration.numbers import cardinal

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

    @property
    def title(self) -> str:
        return f"Etkinlik özeti — {self.generated_at.strftime('%Y-%m-%d %H:%M')} UTC"

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
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _qualified_for(events: list[EventView], job_id: str | None) -> EventView | None:
    for ev in events:
        if ev.event_type == "research.qualified" and (
            job_id is None or ev.research_job_id == job_id
        ):
            return ev
    return None


# ------------------------------------------------------------------ research


def _research_executive(ev: EventView, qualified: EventView | None) -> list[Statement]:
    """The owner's target briefing, each sentence tied to the evidence that supports it."""
    d = ev.detail or {}
    refs = (ev.ref, *ev.evidence_refs)
    out: list[Statement] = []
    if qualified is not None:
        out.append(
            Statement(
                "Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı.",
                LABEL_FACT,
                (qualified.ref, *qualified.evidence_refs),
            )
        )
    else:
        out.append(Statement("Efendim, son araştırma görevi tamamlandı.", LABEL_FACT, refs))
    findings = _n(d.get("findings"))
    sources = _n(d.get("sources"))
    publishers = _n(d.get("distinct_publishers"))
    if findings or sources:
        if publishers and publishers == sources:
            out.append(
                Statement(
                    f"{cardinal(findings).capitalize()} sonuç ve {cardinal(sources)} farklı kaynak "
                    "ürettim.",
                    LABEL_FACT,
                    refs,
                )
            )
        else:
            out.append(
                Statement(
                    f"{cardinal(findings).capitalize()} sonuç ve {cardinal(sources)} kaynak "
                    "ürettim.",
                    LABEL_FACT,
                    refs,
                )
            )
    by_reason = d.get("rejected_by_reason") or {}
    if isinstance(by_reason, dict) and by_reason:
        parts = [
            f"{cardinal(_n(count))} {_REJECTION_TR_ACC.get(reason, reason)}"
            for reason, count in by_reason.items()
            if _n(count) > 0
        ]
        if parts:
            out.append(Statement(f"{_tr_list(parts).capitalize()} eledim.", LABEL_FACT, refs))
    if qualified is not None:
        q = qualified.detail or {}
        if q.get("pagentos_chrome_after") == 0:
            out.append(Statement("Tarayıcı temiz şekilde kapandı.", LABEL_FACT, (qualified.ref,)))
        out.append(
            Statement(
                "Research Engine artık gerçek ortamda doğrulanmış durumda.",
                LABEL_FACT,
                (qualified.ref,),
            )
        )
    out.append(Statement("Bilginize.", LABEL_FACT, ()))
    return out


def _research_detailed(ev: EventView, report: dict[str, Any] | None) -> list[BriefingItem]:
    items: list[BriefingItem] = []
    refs = (ev.ref, *ev.evidence_refs)
    if report:
        sources = {str(s.get("id")): s for s in report.get("sources", []) if isinstance(s, dict)}
        for f in report.get("findings", []):
            if not isinstance(f, dict):
                continue
            statements = [Statement(str(f.get("summary") or "").strip(), LABEL_FACT, refs)]
            why = str(f.get("why_it_matters") or "").strip()
            if why:
                statements.append(Statement(f"Neden önemli: {why}", LABEL_INFERENCE, refs))
            cited = [sources.get(str(eid)) for eid in (f.get("evidence_ids") or [])]
            names = [str(s.get("publisher") or s.get("title") or "") for s in cited if s]
            if names:
                statements.append(Statement(f"Kaynak: {_tr_list(names)}.", LABEL_FACT, refs))
            items.append(BriefingItem(str(f.get("title") or "Bulgu"), tuple(statements)))
    if not items:
        items.append(
            BriefingItem(
                "Bulgular",
                (Statement("Bulguların ayrıntısı bu kayıtta yok.", LABEL_UNCERTAINTY, refs),),
            )
        )
    d = ev.detail or {}
    by_reason = d.get("rejected_by_reason") or {}
    if isinstance(by_reason, dict) and by_reason:
        parts = [f"{_REJECTION_TR.get(r, r)}: {_n(c)}" for r, c in by_reason.items()]
        items.append(
            BriefingItem(
                "Elenen sayfalar",
                (
                    Statement(
                        f"Toplam {_n(d.get('rejected'))} sayfa elendi; {_tr_list(parts)}.",
                        LABEL_FACT,
                        refs,
                    ),
                ),
            )
        )
    return items


def _research_technical(
    ev: EventView, qualified: EventView | None, report: dict[str, Any] | None
) -> list[BriefingItem]:
    d = ev.detail or {}
    refs = (ev.ref, *ev.evidence_refs)
    items: list[BriefingItem] = []
    run_facts = [
        f"Görev kimliği {_short(ev.research_job_id)}." if ev.research_job_id else "",
        f"Keşfedilen aday {_n(d.get('discovered'))}, getirilen sayfa {_n(d.get('fetched'))}, "
        f"kanıt {_n(d.get('evidence'))}, elenen {_n(d.get('rejected'))}.",
        f"Sentez sağlayıcısı {d.get('synthesis_provider')}." if d.get("synthesis_provider") else "",
        f"Araştırma politikası sürümü {ev.version}." if ev.version else "",
    ]
    items.append(
        BriefingItem("Çalışma", tuple(Statement(t, LABEL_FACT, refs) for t in run_facts if t))
    )
    if qualified is not None:
        q = qualified.detail or {}
        facts = [
            f"Kurulu tarayıcı çalışanı sürümü {q.get('installed_release')}; dağıtım "
            f"{'yapılmadı' if not q.get('deployed') else 'yapıldı'}."
            if q.get("installed_release")
            else "",
            f"Cloud Core araştırma politikası {q.get('cloud_policy_version')}."
            if q.get("cloud_policy_version")
            else "",
            f"Kaynak deposu commit {q.get('git_commit')}." if q.get("git_commit") else "",
            f"Kanıt dosyası {q.get('evidence_file')} ({q.get('digest')})."
            if q.get("evidence_file")
            else "",
            "PagentOS Chrome süreçleri: öncesi "
            f"{_n(q.get('pagentos_chrome_before'))}, sonrası "
            f"{_n(q.get('pagentos_chrome_after'))}."
            if "pagentos_chrome_after" in q
            else "",
        ]
        items.append(
            BriefingItem(
                "Qualification",
                tuple(
                    Statement(t, LABEL_FACT, (qualified.ref, *qualified.evidence_refs))
                    for t in facts
                    if t
                ),
            )
        )
    if report:
        cmds = sorted(
            {
                str(s.get("command_id"))
                for s in report.get("sources", [])
                if isinstance(s, dict) and s.get("command_id")
            }
        )
        if cmds:
            items.append(
                BriefingItem(
                    "Cihaz komutları",
                    (
                        Statement(
                            f"{len(cmds)} kaynağın her biri bir cihaz komutuyla getirildi; "
                            f"ilk komut {_short(cmds[0])}.",
                            LABEL_FACT,
                            refs,
                        ),
                    ),
                )
            )
    if ev.trace_id:
        items.append(
            BriefingItem(
                "İzleme", (Statement(f"İz kimliği {_short(ev.trace_id)}.", LABEL_FACT, refs),)
            )
        )
    return items


# ------------------------------------------------------------------ generic


def _generic_executive(ev: EventView) -> list[Statement]:
    return [
        Statement(
            f"Efendim, en son {_subsystem_tr(ev.subsystem)} tarafında: {ev.factual_summary}",
            LABEL_FACT,
            (ev.ref, *ev.evidence_refs),
        ),
        Statement("Bilginize.", LABEL_FACT, ()),
    ]


def _event_item(ev: EventView) -> BriefingItem:
    statements = [Statement(ev.factual_summary, LABEL_FACT, (ev.ref, *ev.evidence_refs))]
    when = _iso(ev.occurred_at)
    statements.append(Statement(f"Zaman: {when}; durum: {ev.status}.", LABEL_FACT, (ev.ref,)))
    if ev.version:
        statements.append(Statement(f"Sürüm: {ev.version}.", LABEL_FACT, (ev.ref,)))
    return BriefingItem(f"{_subsystem_tr(ev.subsystem)} — {ev.event_type}", tuple(statements))


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
    source: EvidenceSource, question: str, query: ExplainQuery, *, now: datetime | None = None
) -> Briefing:
    """Retrieve, then compose. The order is the whole point."""
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

    else:
        # last_activity / today / subsystem_status / research_detail / technical / evidence
        activities = [e for e in recent if e.event_type not in _ANNOTATION_EVENT_TYPES]
        finished = [e for e in activities if e.status in ("completed", "failed")]
        latest = finished[0] if finished else (activities[0] if activities else None)
        if latest is None:
            executive.append(Statement(NO_EVIDENCE_TR, LABEL_UNCERTAINTY, ()))
        elif latest.event_type == "research.completed":
            qualified = _qualified_for(all_recent, latest.research_job_id)
            report = (
                source.research_report(latest.research_job_id) if latest.research_job_id else None
            )
            executive.extend(_research_executive(latest, qualified))
            detailed.extend(_research_detailed(latest, report))
            technical.extend(_research_technical(latest, qualified, report))
            add_refs((latest.ref,), latest.evidence_refs)
            if qualified is not None:
                add_refs((qualified.ref,), qualified.evidence_refs)
            if report and report.get("artifact_id"):
                add_refs(({"kind": "artifact", "ref": str(report["artifact_id"])},))
        else:
            executive.extend(_generic_executive(latest))
            detailed.append(_event_item(latest))
            technical.append(_event_item(latest))
            add_refs((latest.ref,), latest.evidence_refs)
        if query.kind in (QUERY_TODAY, QUERY_SUBSYSTEM_STATUS) or (
            query.kind not in (QUERY_RESEARCH_DETAIL, QUERY_TECHNICAL, QUERY_EVIDENCE)
            and len(finished) > 1
        ):
            window = _window_statement(recent, query)
            if window is not None:
                executive.insert(
                    len(executive) - 1
                    if executive and executive[-1].text == "Bilginize."
                    else len(executive),
                    window,
                )
        others = [e for e in finished[1:11]]
        for e in others:
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
    )


__all__ = [
    "LABEL_FACT",
    "LABEL_INFERENCE",
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
    "render_markdown",
    "speech_for_level",
]
