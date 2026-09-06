"""Self Explanation Engine (M16 spec §2): evidence first, then words.

The evidence here is the owner's real research run of 2026-09-04 17:28-17:33 UTC
(`research-1.json`): the numbers, the rejection reasons and the report shape are the real
ones, so the executive briefing these tests pin is the sentence the owner asked to hear.
Nothing is seeded that did not happen; the "no evidence" cases prove the engine says so.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.explain import engine
from app.explain.classify import (
    LEVEL_DETAILED,
    LEVEL_EXECUTIVE,
    LEVEL_TECHNICAL,
    QUERY_EVIDENCE,
    QUERY_FAILURES,
    QUERY_LAST_ACTIVITY,
    QUERY_MODULE_PROBLEM,
    QUERY_PROBLEMS_NOW,
    QUERY_REJECTED_PAGES,
    QUERY_RESEARCH_DETAIL,
    QUERY_RESEARCH_PROBLEMS,
    QUERY_TODAY,
    QUERY_WHY_FAILED,
    classify,
)
from app.explain.engine import (
    LABEL_FACT,
    LABEL_INFERENCE,
    LABEL_UNCERTAINTY,
    NO_EVIDENCE_TR,
    EventView,
    _research_executive,
    explain,
    render_markdown,
    speech_for_level,
)
from app.research.result import ResearchResult

#: M18.2 DEFECT 2 (ADR-0067): the executive briefing now speaks the report's actual
#: findings (ResearchResult / spoken_result), never the pipeline's own counts — this
#: used to read "Beş farklı kaynaktan beş sonuç üretti ve yirmi sekiz uygun olmayan
#: sayfayı eledi", which is exactly the defect this milestone item fixed.
OWNER_SENTENCE = (
    "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. "
    "Araştırmayı tamamladım. Birincisi, Yapay zekâ ajanları ve ajan temelli yapay zekâ. "
    "Bu önemli çünkü Ajan kavramının Türkçe kamuoyunda tanımlanması. İkincisi, Kamuda "
    "yapay zeka dönemi. Bu önemli çünkü Kamu kullanımı ajan taleplerini büyütür. "
    "İstersen diğer bulguları veya kaynakları da anlatabilirim. "
    "Tarayıcı temiz kapandı; şu anda müdahalenizi gerektiren bir sorun yok."
)

NOW = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
TASK_ID = "2c1c0d2e-0d1a-4d51-9c6e-1e6a4a1f0b11"

#: research-1.json → report.stats, verbatim.
REAL_STATS = {
    "fetched": 33,
    "queries": 0,
    "evidence": 5,
    "rejected": 28,
    "discovered": 240,
    "deduplicated": 28,
    "fetch_failed": 0,
    "rejected_by_reason": {
        "off_topic": 9,
        "interstitial": 11,
        "date_uncertain": 3,
        "duplicate_event": 1,
        "outside_recency_window": 4,
    },
}

REAL_REPORT = {
    "artifact_id": "9d7f1c2e-5b0a-4a3e-8c4d-2f1e3a4b5c6d",
    "executive_summary": "Son üç günde yapay zekâ ajanları alanında beş gelişme öne çıktı.",
    "findings": [
        {
            "id": "f1",
            "title": "Yapay zekâ ajanları ve ajan temelli yapay zekâ",
            "summary": "Bilim ve Gelecek, ajan temelli yapay zekânın ne olduğunu açıkladı.",
            "why_it_matters": "Ajan kavramının Türkçe kamuoyunda tanımlanması.",
            "importance": 3,
            "evidence_ids": ["e1"],
        },
        {
            "id": "f2",
            "title": "Kamuda yapay zeka dönemi",
            "summary": "On bakanlıkta otuz pilot uygulama başlıyor.",
            "why_it_matters": "Kamu kullanımı ajan taleplerini büyütür.",
            "importance": 2,
            "evidence_ids": ["e4"],
        },
    ],
    "sources": [
        {
            "id": "e1",
            "publisher": "Bilim ve Gelecek",
            "url": "https://bilimvegelecek.com.tr/x",
            "command_id": "0b1d8c9e-1111-4c1a-9c00-000000000001",
        },
        {
            "id": "e4",
            "publisher": "takvim.com.tr",
            "url": "https://www.takvim.com.tr/y",
            "command_id": "0b1d8c9e-1111-4c1a-9c00-000000000004",
        },
    ],
}


def _research_completed(**overrides: Any) -> EventView:
    base = dict(
        event_id="ev-research-1",
        occurred_at=NOW - timedelta(minutes=30),
        event_type="research.completed",
        subsystem="research",
        status="completed",
        severity="info",
        factual_summary="Araştırma tamamlandı: 5 bulgu, 5 kaynak; 28 sayfa elendi.",
        version="4",
        research_job_id=TASK_ID,
        trace_id="trace-research-1",
        evidence_refs=({"kind": "research_report", "ref": TASK_ID},),
        detail={
            **REAL_STATS,
            "findings": 5,
            "sources": 5,
            "distinct_publishers": 5,
            "synthesis_provider": "openai",
        },
        source="backfill:research_runs",
    )
    base.update(overrides)
    return EventView(**base)


def _qualified() -> EventView:
    return EventView(
        event_id="ev-qualified-1",
        occurred_at=NOW - timedelta(minutes=25),
        event_type="research.qualified",
        subsystem="research",
        status="completed",
        severity="notice",
        factual_summary="Sahip qualification çalışması PASS ile bitti.",
        research_job_id=TASK_ID,
        evidence_refs=({"kind": "file", "ref": "research-1.json", "digest": "sha256:abc"},),
        detail={
            "verdict": "PASS",
            "pagentos_chrome_before": 0,
            "pagentos_chrome_after": 0,
            "installed_release": "0.4.0",
            "deployed": False,
            "cloud_policy_version": 4,
            "git_commit": "537112a",
            "evidence_file": "research-1.json",
            "digest": "sha256:abc",
        },
        source="live",
    )


class MemorySource:
    def __init__(
        self,
        events: list[EventView],
        report: dict[str, Any] | None = None,
        incidents: list[dict[str, Any]] | None = None,
    ) -> None:
        self._events = sorted(events, key=lambda e: e.occurred_at, reverse=True)
        self._report = report
        self._incidents = incidents or []

    def events(self, *, since, subsystems, statuses, limit):
        out = []
        for e in self._events:
            if since is not None and e.occurred_at < since:
                continue
            if subsystems and e.subsystem not in subsystems:
                continue
            if statuses and e.status not in statuses:
                continue
            out.append(e)
        return out[:limit]

    def research_report(self, task_id: str):
        return self._report if task_id == TASK_ID else None

    def open_incidents(self):
        return list(self._incidents)


# ------------------------------------------------------------------ the owner's sentence


def test_last_activity_is_the_owner_briefing_from_the_real_numbers() -> None:
    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    query = classify("Son yaptıklarını anlat", now=NOW)
    assert query.kind == QUERY_LAST_ACTIVITY and query.level == LEVEL_EXECUTIVE

    briefing = explain(source, "Son yaptıklarını anlat", query, now=NOW)
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert speech == OWNER_SENTENCE
    # a listening budget, not a document: two to four sentences, well under thirty
    # seconds (M18.2 DEFECT 2 widened this slightly: the outcome sentence now names
    # up to three findings instead of one count-laden sentence)
    assert 2 <= len(briefing.executive) <= 4
    assert len(speech) <= 480
    assert "2c1c0d2e" not in speech and "sha256" not in speech
    # the outcome sentences are known facts tied to evidence; the "nothing needs you"
    # closing is an inference over the window and says so
    for statement in briefing.executive[:-1]:
        assert statement.label == LABEL_FACT and statement.evidence_refs
    assert briefing.executive[-1].label == LABEL_INFERENCE
    assert {r["kind"] for r in briefing.evidence_refs} >= {
        "activity_event",
        "research_report",
        "file",
        "artifact",
    }


def test_without_the_owner_verdict_the_engine_does_not_claim_qualification() -> None:
    """The DB knows the run completed; only the owner's evidence file says PASS. Without
    that record the briefing must not say "doğrulanmış" - that would be fabrication."""
    source = MemorySource([_research_completed()], REAL_REPORT)
    briefing = explain(source, "Son ne yaptın", classify("Son ne yaptın", now=NOW), now=NOW)
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert speech.startswith("Efendim, araştırmayı tamamladım. Birincisi, Yapay zekâ ajanları")
    assert "doğrulama" not in speech and "Research Engine" not in speech
    assert "Tarayıcı" not in speech
    # the actual defect this fixes: no counts, no crawler vocabulary, ever spoken
    for word in ("elendi", "eledi", "interstitial", "farklı kaynaktan", "sonuç üretti"):
        assert word not in speech
    assert speech.endswith("Şu anda müdahalenizi gerektiren bir sorun yok.")


def test_no_evidence_is_said_not_invented() -> None:
    source = MemorySource([])
    briefing = explain(
        source, "Son yaptıklarını anlat", classify("Son yaptıklarını anlat", now=NOW), now=NOW
    )
    assert speech_for_level(briefing, LEVEL_EXECUTIVE) == NO_EVIDENCE_TR
    assert briefing.executive[0].label == LABEL_UNCERTAINTY
    assert briefing.evidence_refs == ()
    assert briefing.counts()["facts"] == 0


# ------------------------------------------------------------------ levels


def test_detailed_level_reads_the_actual_findings_with_labels() -> None:
    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    query = classify("Araştırmayı detaylandır", now=NOW)
    assert query.kind == QUERY_RESEARCH_DETAIL and query.level == LEVEL_DETAILED
    briefing = explain(source, "Araştırmayı detaylandır", query, now=NOW)
    titles = [item.title for item in briefing.detailed]
    # M18.2 DEFECT 2 (ADR-0067): "Elenen sayfalar" moved to the TECHNICAL level (see
    # test_technical_level_carries_versions_ids_and_counts) — a detailed answer about
    # what was FOUND no longer also carries how many pages were rejected finding it.
    assert titles == [
        "Yapay zekâ ajanları ve ajan temelli yapay zekâ",
        "Kamuda yapay zeka dönemi",
    ]
    first = briefing.detailed[0].statements
    assert first[0].label == LABEL_FACT and "Bilim ve Gelecek" in first[0].text
    assert first[1].label == LABEL_INFERENCE and first[1].text.startswith("Neden önemli")
    assert first[2].text == "Kaynak: Bilim ve Gelecek."
    speech = speech_for_level(briefing, LEVEL_DETAILED)
    assert speech.startswith("Bir: Yapay zekâ ajanları")
    assert "İki: Kamuda" in speech or "Iki: Kamuda" in speech


def test_technical_level_carries_versions_ids_and_counts() -> None:
    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    query = classify("Teknik olarak ne değişti", now=NOW)
    assert query.level == LEVEL_TECHNICAL
    briefing = explain(source, "Teknik olarak ne değişti", query, now=NOW)
    speech = speech_for_level(briefing, LEVEL_TECHNICAL)
    # versions, evidence, failures, architecture - and no recital of identifiers
    assert "Research policy v4 çalıştı" in speech
    assert "browser worker 0.4.0 değişmedi, deployment gerekmedi" in speech
    assert (
        "240 aday keşfedildi, 33 sayfa getirildi, 5 kanıt kabul edildi, 28 sayfa elendi." in speech
    )
    assert "Kanıt kontrolleri geçti ve tarayıcı temizliği geçti." in speech
    # M18.2 DEFECT 2 (ADR-0067): the eliminated-pages tally moved here from the
    # DETAILED level — exactly what "hangi sayfalar elendi?" asks for.
    assert "Toplam 28 sayfa elendi" in speech
    assert "ara doğrulama sayfası: 11" in speech
    assert "Mimari" in speech
    assert TASK_ID[:8] not in speech and "537112a" not in speech and "trace" not in speech
    assert "Hata" not in speech
    assert len(speech) <= 900


def test_markdown_sections_are_the_narration_levels() -> None:
    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    briefing = explain(
        source, "Son yaptıklarını anlat", classify("Son yaptıklarını anlat", now=NOW), now=NOW
    )
    body = render_markdown(briefing)
    assert body.startswith("# Özet\n\nEfendim, Research Engine gerçek ortam")
    assert "\n# Ayrıntı\n\n1. Yapay zekâ ajanları" in body
    assert "\n# Teknik\n\n1. Sürümler." in body
    assert "\n# Kanıt\n\n- activity_event: ev-research-1" in body
    assert "file: research-1.json (sha256:abc)" in body


# --------------------------------------------------------- M18.2 DEFECT 2 (ADR-0067)


def test_research_executive_speaks_findings_never_ev_detail_counts() -> None:
    """_research_executive consumes the validated report (ResearchResult), not
    ev.detail's counts — even when ev.detail carries huge numbers, none of them may
    leak into the executive statements."""
    ev = _research_completed()
    statements = _research_executive(ev, qualified=None, recent=[], report=REAL_REPORT)
    text = " ".join(s.text for s in statements)
    assert "Yapay zekâ ajanları ve ajan temelli yapay zekâ" in text
    assert "Kamuda yapay zeka dönemi" in text
    for word in ("240", "33", "28", "farklı kaynaktan", "eledi", "elendi"):
        assert word not in text


def test_research_executive_with_no_report_says_so_without_inventing_counts() -> None:
    """A research.completed event whose report row is missing (a source that predates
    the report table, or a lookup failure) must not fall back to ev.detail's counts —
    it says there is nothing to speak, honestly."""
    ev = _research_completed()
    statements = _research_executive(ev, qualified=None, recent=[], report=None)
    text = " ".join(s.text for s in statements)
    assert "bulgu çıkaramadım" in text
    for word in ("240", "33", "28", "eledi", "elendi"):
        assert word not in text


def test_research_executive_matches_the_standalone_result_helper() -> None:
    """The engine's rendering and app.research.result.spoken_result must agree — the
    engine is a thin wrapper (qualification sentence + address-repetition trim), not a
    second implementation of the same narration."""
    result = ResearchResult.from_report_json(REAL_REPORT)
    from app.research.result import spoken_result

    ev = _research_completed()
    statements = _research_executive(ev, qualified=None, recent=[], report=REAL_REPORT)
    # unqualified: the engine's second-to-last-or-only result statement IS spoken_result
    assert any(s.text == spoken_result(result) for s in statements)


def test_hangi_sayfalar_elendi_routes_to_technical_diagnostics() -> None:
    query = classify("Hangi sayfalar elendi?", now=NOW)
    assert query.kind == QUERY_REJECTED_PAGES
    assert query.level == LEVEL_TECHNICAL
    assert query.subsystem == "research"

    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    briefing = explain(source, "Hangi sayfalar elendi?", query, now=NOW)
    speech = speech_for_level(briefing, LEVEL_TECHNICAL)
    assert "Toplam 28 sayfa elendi" in speech
    assert "ara doğrulama sayfası: 11" in speech
    # the executive answer (findings) is NOT what this question gets
    assert "Yapay zekâ ajanları" not in speech


def test_arastirma_sirasinda_ne_sorun_oldu_routes_to_technical_diagnostics() -> None:
    query = classify("Araştırma sırasında ne sorun oldu?", now=NOW)
    assert query.kind == QUERY_RESEARCH_PROBLEMS
    assert query.level == LEVEL_TECHNICAL
    assert query.subsystem == "research"

    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    briefing = explain(source, "Araştırma sırasında ne sorun oldu?", query, now=NOW)
    speech = speech_for_level(briefing, LEVEL_TECHNICAL)
    assert "Toplam 28 sayfa elendi" in speech


def test_a_failed_research_run_answers_honestly_without_reading_the_log() -> None:
    """spec item 1: a run that failed its quality gate is said so, concisely and
    truthfully — never dressed up as a finding. The OUTCOME sentence itself never
    reads the failure log (unlike the separate, pre-existing "needs owner action"
    closing, which surfaces an unresolved failure's own factual_summary by design —
    see test_an_open_failure_turns_the_closing_into_a_call_for_action)."""
    failed = _research_completed(
        event_id="ev-failed-only",
        event_type="research.failed",
        status="failed",
        factual_summary="Araştırma başarısız oldu: insufficient_valid_findings.",
        detail={"error_class": "insufficient_valid_findings"},
    )
    source = MemorySource([failed])
    briefing = explain(
        source, "Son yaptıklarını anlat", classify("Son yaptıklarını anlat", now=NOW), now=NOW
    )
    outcome = briefing.executive[0]
    assert outcome.text.startswith("Bu konuda yeterli doğrulanmış kaynak bulamadım")
    assert "bulgu çıkaramadım" in outcome.text
    assert "insufficient_valid_findings" not in outcome.text


# ------------------------------------------------------------------ other questions


def test_failures_and_why_failed_read_the_failed_event() -> None:
    failed = _research_completed(
        event_id="ev-failed",
        event_type="research.failed",
        status="failed",
        occurred_at=NOW - timedelta(hours=2),
        factual_summary="Araştırma başarısız oldu: insufficient_valid_findings.",
        detail={"error_class": "insufficient_valid_findings"},
    )
    source = MemorySource([failed, _research_completed()], REAL_REPORT)
    briefing = explain(source, "Ne başarısız oldu", classify("Ne başarısız oldu", now=NOW), now=NOW)
    assert briefing.query.kind == QUERY_FAILURES
    assert "en son başarısız olan araştırma motoru işi" in briefing.executive[0].text
    why = explain(
        source, "Neden başarısız olmuştu", classify("Neden başarısız olmuştu", now=NOW), now=NOW
    )
    assert why.query.kind == QUERY_WHY_FAILED
    assert any(
        "insufficient_valid_findings" in s.text and s.label == LABEL_FACT for s in why.executive
    )


def test_no_failures_says_so_as_a_fact() -> None:
    source = MemorySource([_research_completed()], REAL_REPORT)
    briefing = explain(source, "Ne başarısız oldu", classify("Ne başarısız oldu", now=NOW), now=NOW)
    assert briefing.executive[0].text == "Bu dönemde başarısız bir etkinlik kaydım yok."
    assert briefing.executive[0].label == LABEL_FACT


def test_problems_now_uses_open_incidents_and_critical_events() -> None:
    incident = {
        "id": "inc-1",
        "component": "browser_worker",
        "severity": "warning",
        "occurrence_count": 3,
    }
    source = MemorySource([_research_completed()], REAL_REPORT, incidents=[incident])
    briefing = explain(
        source, "Şu anda sorun var mı", classify("Şu anda sorun var mı", now=NOW), now=NOW
    )
    assert briefing.query.kind == QUERY_PROBLEMS_NOW
    assert briefing.executive[0].text.startswith("Açık olay: browser_worker")
    assert {"kind": "incident", "ref": "inc-1"} in briefing.evidence_refs
    clean = explain(
        MemorySource([_research_completed()]),
        "Şu anda sorun var mı",
        classify("Şu anda sorun var mı", now=NOW),
        now=NOW,
    )
    assert clean.executive[0].text == "Şu anda açık bir sorun kaydım yok."


def test_module_problem_for_an_unknown_module_is_an_uncertainty() -> None:
    source = MemorySource([_research_completed()], REAL_REPORT)
    query = classify("Diagnostic Observer'da sorun ne", now=NOW)
    assert query.kind == QUERY_MODULE_PROBLEM and query.module == "diagnostic observer"
    briefing = explain(source, "Diagnostic Observer'da sorun ne", query, now=NOW)
    labels = [s.label for s in briefing.executive]
    assert LABEL_UNCERTAINTY in labels
    assert any("hiç kayıt bulamadım" in s.text for s in briefing.executive)


def test_module_problem_reads_the_module_ledger_when_it_exists() -> None:
    shadow = EventView(
        event_id="ev-do-1",
        occurred_at=NOW - timedelta(hours=1),
        event_type="evolution.shadow_ready",
        subsystem="evolution",
        status="completed",
        severity="notice",
        module="diagnostic_observer",
        version="0.4.0",
        related_module_id="diagnostic_observer",
        production_state="shadow_ready",
        factual_summary="Diagnostic Observer 0.4.0 shadow ready; 64 test geçti.",
        detail={"tests_passed": 64},
    )
    source = MemorySource([shadow, _research_completed()], REAL_REPORT)
    query = classify("Diagnostic Observer'da sorun ne", now=NOW)
    briefing = explain(source, "Diagnostic Observer'da sorun ne", query, now=NOW)
    assert any(
        "kayıtlı bir hata yok" in s.text and s.label == LABEL_FACT for s in briefing.executive
    )
    assert briefing.detailed[0].title.startswith("evrim motoru")


def test_today_counts_only_today() -> None:
    old = _research_completed(event_id="ev-old", occurred_at=NOW - timedelta(days=3))
    today = _research_completed()
    source = MemorySource([old, today], REAL_REPORT)
    query = classify("Bugün neler yaptın", now=NOW)
    assert query.kind == QUERY_TODAY
    briefing = explain(source, "Bugün neler yaptın", query, now=NOW)
    assert any("Bugün toplam bir etkinlik kaydettim" in s.text for s in briefing.executive)


def test_evidence_question_lists_the_references() -> None:
    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    briefing = explain(source, "Kanıtı ne", classify("Kanıtı ne", now=NOW), now=NOW)
    assert briefing.query.kind == QUERY_EVIDENCE
    assert briefing.executive[0].text.startswith("Kanıt olarak")
    assert "research_report" in briefing.executive[0].text


@pytest.mark.parametrize("label", ["known_fact", "inference", "uncertainty"])
def test_statement_labels_are_the_three_the_spec_names(label: str) -> None:
    assert label in engine.STATEMENT_LABELS


# ------------------------------------------------------------------ owner relevance


def _voice_explained(minutes_ago: int) -> EventView:
    return EventView(
        event_id=f"ev-voice-{minutes_ago}",
        occurred_at=NOW - timedelta(minutes=minutes_ago),
        event_type="voice.explained",
        subsystem="voice",
        status="completed",
        severity="info",
        factual_summary="Sahibe executive düzeyinde etkinlik özeti anlatıldı: 5 olgu.",
        source="live",
    )


def test_the_explanation_itself_never_leads_the_next_explanation() -> None:
    """Owner UX result 2026-09-04: "Son yaptıklarını anlat" answered with the previous
    narration. Voice and ledger bookkeeping are meta activity: ranked out of an executive
    briefing unless the owner asks about Voice."""
    source = MemorySource(
        [_voice_explained(1), _voice_explained(3), _research_completed(), _qualified()],
        REAL_REPORT,
    )
    briefing = explain(
        source, "Son yaptıklarını anlat", classify("Son yaptıklarını anlat", now=NOW), now=NOW
    )
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert speech == OWNER_SENTENCE
    assert "anlatıldı" not in speech and "anlattım" not in speech

    asked_about_voice = explain(
        source, "Ses tarafında ne durumda", classify("Ses tarafında ne durumda", now=NOW), now=NOW
    )
    assert asked_about_voice.query.subsystem == "voice"
    assert "etkinlik özeti anlatıldı" in speech_for_level(asked_about_voice, LEVEL_EXECUTIVE)


def test_relevance_classes_cover_every_event_type_the_ledger_knows() -> None:
    from app.explain.engine import RELEVANCE_CLASSES, owner_relevance

    cases = {
        "research.completed": "task_completion",
        "research.qualified": "task_completion",
        "research.failed": "failure",
        "research.quality_gate": "telemetry",
        "deployment.cloud_core.released": "change",
        "incident.opened": "failure",
        "evolution.shadow_ready": "evolution",
        "voice.session.closed": "meta",
        "voice.explained": "meta",
        "ledger.backfill": "meta",
        "briefing.delivered": "meta",
    }
    for event_type, expected in cases.items():
        ev = EventView(
            event_id="x",
            occurred_at=NOW,
            event_type=event_type,
            subsystem=event_type.split(".")[0],
            status="failed" if "fail" in event_type else "completed",
            severity="info",
            factual_summary="",
        )
        assert owner_relevance(ev) == expected, event_type
        assert expected in RELEVANCE_CLASSES
    critical = EventView(
        event_id="c",
        occurred_at=NOW,
        event_type="deployment.cloud_core.released",
        subsystem="deployment",
        status="completed",
        severity="critical",
        factual_summary="",
    )
    assert owner_relevance(critical) == "security"


def test_an_open_failure_turns_the_closing_into_a_call_for_action() -> None:
    failed = _research_completed(
        event_id="ev-failed",
        event_type="research.failed",
        status="failed",
        occurred_at=NOW - timedelta(minutes=10),
        factual_summary="Araştırma başarısız oldu: insufficient_valid_findings.",
        detail={"error_class": "insufficient_valid_findings"},
    )
    source = MemorySource([failed, _research_completed(), _qualified()], REAL_REPORT)
    briefing = explain(
        source, "Son yaptıklarını anlat", classify("Son yaptıklarını anlat", now=NOW), now=NOW
    )
    closing = briefing.executive[-1]
    assert closing.text.startswith("Müdahalenizi gerektiren bir konu var:")
    assert closing.label == LABEL_FACT and closing.evidence_refs


def test_detailed_level_stops_at_five_items_and_keeps_the_rest_in_the_report() -> None:
    many = dict(REAL_REPORT)
    many["findings"] = [
        {**REAL_REPORT["findings"][0], "id": f"f{i}", "title": f"Bulgu {i}"} for i in range(1, 9)
    ]
    source = MemorySource([_research_completed(), _qualified()], many)
    briefing = explain(
        source, "Araştırmayı detaylandır", classify("Araştırmayı detaylandır", now=NOW), now=NOW
    )
    titles = [i.title for i in briefing.detailed]
    # M18.2 DEFECT 2 (ADR-0067): "Elenen sayfalar" no longer rides along here.
    assert titles == ["Bulgu 1", "Bulgu 2", "Bulgu 3", "Bulgu 4", "Bulgu 5"]
    assert "Bulgu 6" not in titles
