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
    QUERY_RESEARCH_DETAIL,
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
    explain,
    render_markdown,
    speech_for_level,
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
    assert speech == (
        "Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı. "
        "Beş sonuç ve beş farklı kaynak ürettim. "
        "Dokuz konu dışı sayfayı, on bir ara doğrulama sayfasını, üç tarihi doğrulanamayan "
        "sonucu, bir tekrar eden olayı ve dört tarih dışı sonucu eledim. "
        "Tarayıcı temiz şekilde kapandı. "
        "Research Engine artık gerçek ortamda doğrulanmış durumda. Bilginize."
    )
    # every sentence but the closing word is a known fact tied to evidence
    for statement in briefing.executive[:-1]:
        assert statement.label == LABEL_FACT and statement.evidence_refs
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
    assert speech.startswith("Efendim, son araştırma görevi tamamlandı. Beş sonuç")
    assert "doğrulanmış" not in speech and "qualification" not in speech
    assert "Tarayıcı" not in speech


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
    assert titles[:2] == [
        "Yapay zekâ ajanları ve ajan temelli yapay zekâ",
        "Kamuda yapay zeka dönemi",
    ]
    assert titles[-1] == "Elenen sayfalar"
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
    assert TASK_ID[:8] in speech and TASK_ID not in speech
    assert "Keşfedilen aday 240, getirilen sayfa 33, kanıt 5, elenen 28." in speech
    assert "Araştırma politikası sürümü 4." in speech
    assert "Kurulu tarayıcı çalışanı sürümü 0.4.0; dağıtım yapılmadı." in speech
    assert "commit 537112a" in speech
    assert "2 kaynağın her biri bir cihaz komutuyla getirildi" in speech
    assert "İz kimliği trace." in speech


def test_markdown_sections_are_the_narration_levels() -> None:
    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    briefing = explain(
        source, "Son yaptıklarını anlat", classify("Son yaptıklarını anlat", now=NOW), now=NOW
    )
    body = render_markdown(briefing)
    assert body.startswith("# Özet\n\nEfendim, son araştırma motoru")
    assert "\n# Ayrıntı\n\n1. Yapay zekâ ajanları" in body
    assert "\n# Teknik\n\n1. Çalışma." in body
    assert "\n# Kanıt\n\n- activity_event: ev-research-1" in body
    assert "file: research-1.json (sha256:abc)" in body


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
