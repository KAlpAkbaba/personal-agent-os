"""M17 phase 9: the owner asks what the system learned, what it is building, and what is
waiting to go live — and hears evidence, or an honest "no record".

The subsystems that produce this evidence (experience lessons, evolution opportunities,
goals) arrive over several releases, so the engine asks an evidence source that may not
have them. These tests pin both halves: a source WITH the evidence, and a source without
it, which must produce an uncertainty rather than an invention.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.explain.classify import (
    LEVEL_DETAILED,
    LEVEL_EXECUTIVE,
    QUERY_EVOLUTION,
    QUERY_GOALS,
    QUERY_LEARNED,
    QUERY_SHADOW_READY,
    QUERY_TESTS,
    QUERY_WHY_BUILT,
    classify,
)
from app.explain.engine import (
    LABEL_FACT,
    LABEL_UNCERTAINTY,
    EventView,
    explain,
    speech_for_level,
)
from tests.unit.test_explain_engine import MemorySource

NOW = datetime(2026, 9, 5, 3, 0, tzinfo=UTC)

LESSON_RUNTIME = {
    "lesson_id": "les-1",
    "title": "Runtime provenance",
    "statement": "Bir bileşenin canlıda çalıştığı, deposundaki koda bakılarak varsayılamaz.",
    "root_cause": "Dağıtım ile çalışan sürüm arasındaki uyumsuzluk fark edilmedi.",
    "resolution": "Checkout, staged, installed ve running sürümleri ayrı ayrı doğrulandı.",
    "status": "promoted",
    "score": 0.82,
    "recurrence": 3,
    "confidence": 0.9,
}
LESSON_INTERSTITIAL = {
    "lesson_id": "les-2",
    "title": "Ara sayfalar kanıt değildir",
    "statement": "Doğrulama ve ara sayfaları araştırma kanıtı olarak kabul edilmemeli.",
    "root_cause": "Sayfa geçerliliği sentezden önce hiç sorulmuyordu.",
    "resolution": "Sentez öncesi kalite kapısı eklendi.",
    "status": "candidate",
    "score": 0.51,
    "recurrence": 1,
    "confidence": 0.6,
}

SHADOW_OPPORTUNITY = {
    "opportunity_id": "opp-1",
    "title": "Diagnostic Observer",
    "statement": "Tekrarlayan dağıtım uyumsuzluklarını erken yakalayan bir gözlemci.",
    "status": "SHADOW_READY",
    "origin": [{"kind": "activity_event", "ref": "ev-1"}, {"kind": "incident", "ref": "inc-1"}],
    "scores": {"composite": 0.74, "operational_risk": 0.2, "engineering_cost": 0.3},
}
BUILDING_OPPORTUNITY = {
    "opportunity_id": "opp-2",
    "title": "Kaynak çeşitliliği ölçer",
    "statement": "Araştırma kaynaklarının çeşitliliğini ölçen bir değerlendirici.",
    "status": "BUILDING",
    "origin": [{"kind": "activity_event", "ref": "ev-2"}],
    "scores": {"composite": 0.55, "operational_risk": 0.1, "engineering_cost": 0.5},
}

GOAL_OPEN = {
    "goal_id": "goal-1",
    "title": "Sesli özet kalitesi",
    "intent": "Sesli yanıtların dinlenebilir uzunlukta kalmasını sağla.",
    "status": "active",
    "blockers": [],
}
GOAL_BLOCKED = {
    "goal_id": "goal-2",
    "title": "Google arama yolu",
    "intent": "Google yolunu isteğe bağlı olarak kullanılabilir tut.",
    "status": "waiting_owner",
    "blockers": ["owner verification"],
}


class LearningSource(MemorySource):
    """A source that DOES have the newer subsystems."""

    def __init__(self, events=None, **collections: Any) -> None:
        super().__init__(events or [])
        self._collections = collections

    def lessons(self, *, limit: int = 20):
        return list(self._collections.get("lessons", []))[:limit]

    def procedural_memories(self, *, limit: int = 20):
        return list(self._collections.get("procedural", []))[:limit]

    def opportunities(self, *, statuses=None, limit: int = 20):
        return list(self._collections.get("opportunities", []))[:limit]

    def goals(self, *, limit: int = 20):
        return list(self._collections.get("goals", []))[:limit]


def _briefing(source, question: str):
    return explain(source, question, classify(question, now=NOW), now=NOW)


# ------------------------------------------------------------------ what did you learn


def test_ne_ogrendin_answers_from_lessons_and_says_which_are_still_candidates() -> None:
    source = LearningSource(lessons=[LESSON_RUNTIME, LESSON_INTERSTITIAL])
    briefing = _briefing(source, "Ne öğrendin?")
    assert briefing.query.kind == QUERY_LEARNED
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert speech.startswith("Efendim, bir dersi kalıcı hale getirdim; bir aday")
    assert "canlıda çalıştığı" in speech  # the promoted lesson itself is spoken
    assert len(speech) <= 420
    assert all(s.label == LABEL_FACT for s in briefing.executive)

    detail = speech_for_level(briefing, LEVEL_DETAILED)
    assert "Kök neden: Dağıtım ile çalışan sürüm" in detail
    assert "Çözüm: Checkout, staged" in detail
    assert "henüz aday" in detail  # a candidate is never presented as settled


def test_son_hatalardan_ne_ogrendin_is_the_same_question() -> None:
    source = LearningSource(lessons=[LESSON_RUNTIME])
    briefing = _briefing(source, "Son hatalardan ne öğrendin?")
    assert briefing.query.kind == QUERY_LEARNED
    assert briefing.evidence_refs[0]["kind"] == "experience_lesson"


def test_with_no_lessons_it_says_so_instead_of_inventing_wisdom() -> None:
    briefing = _briefing(LearningSource(), "Ne öğrendin?")
    assert speech_for_level(briefing, LEVEL_EXECUTIVE) == "Henüz kayda geçmiş bir ders çıkarmadım."
    assert briefing.executive[0].label == LABEL_UNCERTAINTY


def test_an_older_evidence_source_without_the_subsystem_degrades_honestly() -> None:
    """A deployment (or a release) without the experience subsystem answers the same way as
    one with nothing learned: no record. It must never raise, and never guess."""
    briefing = _briefing(MemorySource([]), "Ne öğrendin?")
    assert briefing.executive[0].label == LABEL_UNCERTAINTY


# ------------------------------------------------------------------ what are you building


def test_kendi_uzerinde_ne_gelistiriyorsun_counts_what_is_in_flight() -> None:
    source = LearningSource(opportunities=[SHADOW_OPPORTUNITY, BUILDING_OPPORTUNITY])
    briefing = _briefing(source, "Kendi üzerinde ne geliştiriyorsun?")
    assert briefing.query.kind == QUERY_EVOLUTION
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert "bir geliştirme üzerinde çalışıyorum" in speech
    assert "bir tanesi gölge durumda hazır" in speech
    assert "canlıya alma yetkisi bende değil" in speech


def test_hazir_modullerin_neler_names_the_shadow_ready_ones_and_that_they_are_not_live() -> None:
    source = LearningSource(opportunities=[SHADOW_OPPORTUNITY, BUILDING_OPPORTUNITY])
    briefing = _briefing(source, "Hazır modüllerin neler?")
    assert briefing.query.kind == QUERY_SHADOW_READY
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert "Diagnostic Observer" in speech
    assert "Hiçbiri canlı sistemde değil" in speech
    assert "onayınızı bekliyorum" in speech


def test_canliya_alinmayi_bekleyen_ne_var_with_nothing_ready_says_nothing_is_waiting() -> None:
    source = LearningSource(opportunities=[BUILDING_OPPORTUNITY])
    briefing = _briefing(source, "Canlıya alınmayı bekleyen ne var?")
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert speech.startswith("Şu anda canlıya alınmayı bekleyen hazır bir modül yok.")
    assert briefing.executive[0].label == LABEL_FACT


def test_bu_ozelligi_neden_gelistirdin_answers_from_the_opportunity_origin() -> None:
    source = LearningSource(opportunities=[SHADOW_OPPORTUNITY])
    briefing = _briefing(source, "Bu özelliği neden geliştirdin?")
    assert briefing.query.kind == QUERY_WHY_BUILT
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert speech.startswith("Diagnostic Observer: Tekrarlayan dağıtım")
    assert "iki gerçek kayıt üzerine başlattım" in speech


def test_why_built_without_recorded_origin_admits_it() -> None:
    source = LearningSource(opportunities=[{**SHADOW_OPPORTUNITY, "origin": []}])
    briefing = _briefing(source, "Bu özelliği neden geliştirdin?")
    assert any(s.label == LABEL_UNCERTAINTY for s in briefing.executive)


# ------------------------------------------------------------------ goals and tests


def test_hedeflerin_ne_durumda_reports_open_goals_and_what_needs_the_owner() -> None:
    source = LearningSource(goals=[GOAL_OPEN, GOAL_BLOCKED])
    briefing = _briefing(source, "Hedeflerin ne durumda?")
    assert briefing.query.kind == QUERY_GOALS
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert "iki açık hedefim var" in speech
    assert "Google arama yolu sizin müdahalenizi bekliyor" in speech


def test_test_sonuclarini_anlat_reads_test_events_from_the_ledger() -> None:
    passed = EventView(
        event_id="ev-t1",
        occurred_at=NOW - timedelta(hours=1),
        event_type="evolution.tests_passed",
        subsystem="evolution",
        status="completed",
        severity="info",
        factual_summary="Diagnostic Observer: 64 testin tamamı geçti.",
    )
    failed = EventView(
        event_id="ev-t2",
        occurred_at=NOW - timedelta(hours=2),
        event_type="evolution.tests_failed",
        subsystem="evolution",
        status="failed",
        severity="warning",
        factual_summary="Kaynak çeşitliliği ölçer: 2 test başarısız.",
    )
    source = LearningSource([passed, failed])
    briefing = _briefing(source, "Test sonuçlarını anlat.")
    assert briefing.query.kind == QUERY_TESTS
    speech = speech_for_level(briefing, LEVEL_EXECUTIVE)
    assert "son iki test kaydının bir tanesi geçti" in speech
    assert briefing.evidence_refs  # each one points at its ledger event


def test_no_test_records_says_so() -> None:
    briefing = _briefing(LearningSource(), "Test sonuçlarını anlat.")
    assert speech_for_level(briefing, LEVEL_EXECUTIVE) == "Kayıtlarımda test sonucu bulamadım."
