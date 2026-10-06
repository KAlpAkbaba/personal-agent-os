"""Doğrula modu (card verify-mode, d20261006): "bunu doğrula: ..." -> a claim, a verdict
DOĞRU | YANLIŞ | KISMEN | BELİRSİZ, 2-5 sources, the strongest counter-argument; recalled
later by text and by date.

The two voice tools live in the research family (``research.verify`` starts a crawl the same
way ``research.start`` does; ``research.verify_recall`` reads the owner's own records back).
Every registered tool must carry a step-up tier (``app.security.step_up._TIERS``,
``test_voice_step_up.test_every_registered_tool_has_a_tier``), so the tiers are decided here
first. The verdict core (``app.research.verify``) is pure and tested on fixture evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.research import verify
from app.research.verify import VerificationRecord, VerifySource
from app.security import step_up

TOOL_VERIFY = "research.verify"
TOOL_VERIFY_RECALL = "research.verify_recall"

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def test_the_verify_tools_have_a_decided_tier() -> None:
    """A verify starts a crawl on an owner device - the same tier ``research.start`` has.
    Reading back what was verified changes nothing - OPEN, like ``memory.search``."""
    assert step_up._TIERS.get(TOOL_VERIFY) == step_up.TIER_SENSITIVE
    assert step_up._TIERS.get(TOOL_VERIFY_RECALL) == step_up.TIER_OPEN


def _src(
    stance: str, n: int = 1, *, year: int = 2026, quote: str = "karar veren cümle"
) -> VerifySource:
    return VerifySource(
        url=f"https://ornek{n}.example/haber",
        title=f"Kaynak {n}",
        published_at=datetime(year, 9, 1, tzinfo=UTC),
        quote=quote,
        stance=stance,
    )


# --- claim -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "claim"),
    [
        ("Bunu doğrula: Ay'ın yüzeyinde su buzu var.", "Ay'ın yüzeyinde su buzu var"),
        (
            "şunu kontrol et: asgari ücret 2026'da iki kez arttı",
            "asgari ücret 2026'da iki kez arttı",
        ),
        ("Everest'in 8849 metre olduğu doğru mu?", "Everest'in 8849 metre olduğu"),
        ("doğrula   Türkiye'nin başkenti Ankara", "Türkiye'nin başkenti Ankara"),
    ],
)
def test_the_claim_is_the_sentence_without_the_trigger(said: str, claim: str) -> None:
    assert verify.normalise_claim(said) == claim


def test_the_year_a_claim_names_is_read() -> None:
    assert verify.claim_subject_year("asgari ücret 2026'da iki kez arttı") == 2026
    assert verify.claim_subject_year("Ay'da su buzu var") is None


def test_a_measure_is_not_a_year() -> None:
    assert verify.claim_subject_year("zirve 2000 metre yükseklikte") is None
    assert verify.claim_subject_year("törene 1950 kişi katıldı") is None
    assert verify.claim_subject_year("2000 yılında 1950 kişi") == 2000


# --- the trigger: only the owner's own sentence -------------------------------------------

TRIGGERS = [
    ("Bunu doğrula: Ay'ın yüzeyinde su buzu var.", "Ay'ın yüzeyinde su buzu var"),
    (
        "Şunu kontrol et: asgari ücret 2026'da iki kez arttı",
        "asgari ücret 2026'da iki kez arttı",
    ),
    ("Everest'in 8849 metre olduğu doğru mu?", "Everest'in 8849 metre olduğu"),
    ("Türkiye'nin başkenti Ankara mı, doğrula", "Türkiye'nin başkenti Ankara"),
    ("Teyit et: İstanbul'un nüfusu 16 milyonu geçti", "İstanbul'un nüfusu 16 milyonu geçti"),
]
NEAR_MISSES = [
    "Doğru söylüyorsun.",
    "Bu dosya doğru mu?",
    "Uygulamayı doğrula.",
    "Gelen kutusunu kontrol et.",
    "Bunu kontrol et",
    "Kontrol et bakalım hava nasıl olacak",
]


@pytest.mark.parametrize(("said", "claim"), TRIGGERS)
def test_five_trigger_sentences_are_verify_claims(said: str, claim: str) -> None:
    from app.voice.intents import Intent, research_topic_of, resolve_intent

    assert verify.verify_request_kind(said) == verify.VERIFY_CLAIM
    resolved = resolve_intent(said)
    assert resolved.intent is Intent.VERIFY_CLAIM
    assert resolved.capability == TOOL_VERIFY
    # The local mode's argument: the claim, off the owner's own sentence (ADR decision 5).
    assert research_topic_of(said) == claim
    assert verify.normalise_claim(said) == claim


@pytest.mark.parametrize("said", NEAR_MISSES)
def test_near_misses_are_not_a_verify(said: str) -> None:
    from app.voice.intents import Intent, resolve_intent

    assert verify.verify_request_kind(said) is None
    assert resolve_intent(said).intent not in (Intent.VERIFY_CLAIM, Intent.VERIFY_RECALL)


def test_a_near_miss_keeps_its_own_route() -> None:
    from app.voice.intents import Intent, resolve_intent

    assert resolve_intent("Bu dosya doğru mu?").intent is Intent.ARTIFACT_VALIDATE
    assert resolve_intent("Gelen kutusunu kontrol et.").intent is Intent.MAIL_INBOX
    assert (
        resolve_intent("Uygulamayı doğrula.", native_build_focused=True).intent
        is not Intent.VERIFY_CLAIM
    )


@pytest.mark.parametrize(
    "said",
    ["Geçen hafta neyi doğrulamıştık?", "Everest hakkında ne bulmuştuk?", "Dün ne doğruladık?"],
)
def test_recall_sentences_are_verify_recalls(said: str) -> None:
    from app.voice.intents import Intent, research_topic_of, resolve_intent

    assert verify.verify_request_kind(said) == verify.VERIFY_RECALL
    resolved = resolve_intent(said)
    assert resolved.intent is Intent.VERIFY_RECALL
    assert resolved.klass == "query"
    assert research_topic_of(said) == said


def test_a_claim_that_names_a_recall_word_is_still_a_claim() -> None:
    assert (
        verify.verify_request_kind("Bunu doğrula: NASA bunu 2020'de doğrulamıştı")
        == verify.VERIFY_CLAIM
    )


# --- verdict -----------------------------------------------------------------------------


def test_supporting_sources_only_is_dogru() -> None:
    v = verify.decide_verdict([_src("supports", 1), _src("supports", 2), _src("supports", 3)])
    assert v.code == verify.VERDICT_TRUE
    assert v.label == "DOĞRU"
    assert 0.5 < v.confidence <= 1.0


def test_refuting_sources_only_is_yanlis() -> None:
    v = verify.decide_verdict([_src("refutes", 1), _src("refutes", 2)])
    assert v.code == verify.VERDICT_FALSE
    assert v.label == "YANLIŞ"


def test_mixed_sources_is_kismen_with_lower_confidence() -> None:
    mixed = verify.decide_verdict([_src("supports", 1), _src("supports", 2), _src("refutes", 3)])
    agreed = verify.decide_verdict([_src("supports", 1), _src("supports", 2), _src("supports", 3)])
    assert mixed.code == verify.VERDICT_PARTLY
    assert mixed.label == "KISMEN"
    assert mixed.confidence < agreed.confidence


def test_one_source_never_reads_as_certain() -> None:
    assert verify.decide_verdict([_src("supports")]).confidence <= 0.5


def test_no_source_is_belirsiz_never_a_guess() -> None:
    """ADR-0063: no decisive source -> BELİRSİZ with confidence 0, whatever the rest says."""
    for sources in (
        [],
        [_src("neutral", 1), _src("neutral", 2)],
        [_src("supports", 1, quote="  ")],
    ):
        v = verify.decide_verdict(sources)
        assert v.code == verify.VERDICT_UNKNOWN
        assert v.label == "BELİRSİZ"
        assert v.confidence == 0.0


def test_the_kept_sources_are_at_most_five_decisive_ones() -> None:
    """The neutral source comes FIRST (inspector run 2): with it at the end, ``[:5]`` cut it
    for the wrong reason and a ``decisive`` that counted neutral sources stayed green."""
    neutral = VerifySource(
        url="https://notr.example/x",
        title="Nötr Kaynak",
        published_at=datetime(2026, 9, 1, tzinfo=UTC),
        quote="konuyla ilgili ama karar vermeyen cümle",
        stance="neutral",
    )
    sources = [neutral] + [_src("supports", i) for i in range(1, 8)]
    kept = verify.select_sources(sources)
    assert len(kept) == 5
    assert all(s.stance != "neutral" for s in kept)
    verdict = verify.decide_verdict(sources)
    assert verdict.code == verify.VERDICT_TRUE
    spoken = verify.spoken_sentence("Ay'da su buzu var", verdict, sources)
    assert "Nötr Kaynak" not in spoken
    assert "Kaynak 1" in spoken


def test_a_neutral_source_never_decides_even_with_a_quote() -> None:
    """A neutral source with a quote is still not decisive (kills 'decisive counts neutral')."""
    lone = VerifySource(
        url="https://notr.example/x",
        title="Nötr Kaynak",
        published_at=None,
        quote="bir cümle",
        stance="neutral",
    )
    assert not lone.decisive
    assert verify.decide_verdict([lone]).code == verify.VERDICT_UNKNOWN
    assert "kaynak bulamadım" in verify.spoken_sentence("x", verify.decide_verdict([lone]), [lone])


def test_one_source_is_said_as_one_source() -> None:
    """Lead decision (run 3): the 0.5 ceiling stays and the sentence says 'tek kaynak'."""
    one = [_src("supports", 1)]
    spoken = verify.spoken_sentence("Ay'da su buzu var", verify.decide_verdict(one), one)
    assert "Tek kaynak: Kaynak 1" in spoken
    assert "yüzde 50" in spoken
    two = [_src("supports", 1), _src("supports", 2)]
    assert "Tek kaynak" not in verify.spoken_sentence(
        "Ay'da su buzu var", verify.decide_verdict(two), two
    )


def test_the_counter_argument_is_the_strongest_opposite_source() -> None:
    sources = [
        _src("supports", 1),
        _src("supports", 2),
        _src("refutes", 3, quote="aksi yönde veri"),
    ]
    v = verify.decide_verdict(sources)
    counter = verify.counter_argument(v, sources)
    assert counter is not None
    assert counter.quote == "aksi yönde veri"
    assert (
        verify.counter_argument(verify.decide_verdict([_src("supports")]), [_src("supports")])
        is None
    )


def test_a_source_older_than_the_claims_subject_is_said() -> None:
    claim = "asgari ücret 2026'da iki kez arttı"
    sources = [_src("supports", 1, year=2025), _src("supports", 2, year=2026)]
    v = verify.decide_verdict(sources)
    spoken = verify.spoken_sentence(claim, v, sources)
    assert "eski" in spoken
    fresh = verify.spoken_sentence(claim, v, [_src("supports", 2, year=2026)])
    assert "eski" not in fresh


def test_the_spoken_sentence_names_the_verdict_and_a_source() -> None:
    sources = [_src("supports", 1), _src("supports", 2)]
    spoken = verify.spoken_sentence("Ay'da su buzu var", verify.decide_verdict(sources), sources)
    assert "doğru" in spoken
    assert "Kaynak 1" in spoken
    assert spoken.count(".") <= 3


def test_the_spoken_sentence_without_sources_says_so() -> None:
    spoken = verify.spoken_sentence("Ay'da su buzu var", verify.decide_verdict([]), [])
    assert "belirsiz" in spoken
    assert "kaynak bulamadım" in spoken


# --- recall ------------------------------------------------------------------------------


def _record(claim: str, day: int, month: int = 9) -> VerificationRecord:
    sources = [_src("supports", 1), _src("supports", 2)]
    return VerificationRecord(
        said=f"bunu doğrula: {claim}",
        claim=claim,
        verdict=verify.decide_verdict(sources),
        sources=tuple(sources),
        created_at=datetime(2026, month, day, 12, tzinfo=UTC),
    )


RECORDS = [
    _record("Ay'da su buzu var", 29),
    _record("İstanbul'un nüfusu 16 milyonu geçti", 30),
    _record("asgari ücret iki kez arttı", 5, month=10),
]


def test_recall_by_text_matches_words_turkish_folded() -> None:
    found = verify.recall(RECORDS, text="istanbul nüfus")
    assert [r.claim for r in found] == ["İstanbul'un nüfusu 16 milyonu geçti"]
    assert verify.recall(RECORDS, text="mars") == []


def test_recall_by_date_window() -> None:
    since, until = verify.recall_window("geçen hafta neyi doğrulamıştık", NOW)
    found = verify.recall(RECORDS, since=since, until=until)
    assert {r.claim for r in found} == {"Ay'da su buzu var", "İstanbul'un nüfusu 16 milyonu geçti"}
    since, until = verify.recall_window("bu hafta ne doğruladık", NOW)
    assert [r.claim for r in verify.recall(RECORDS, since=since, until=until)] == [
        "asgari ücret iki kez arttı"
    ]
    assert verify.recall_window("dünya hakkında ne bulmuştuk", NOW) == (None, None)


def test_recall_newest_first() -> None:
    assert [r.created_at.day for r in verify.recall(RECORDS)] == [5, 30, 29]


def test_a_date_word_inside_another_word_is_not_a_date() -> None:
    """Kills 'lookbehind removed': "ödün" holds "dün"; "geçen ayrıntı" holds "geçen ay"."""
    assert verify.recall_window("ödün hakkında ne bulmuştuk", NOW) == (None, None)
    assert verify.recall_window("geçen ayrıntı hakkında ne bulmuştuk", NOW) == (None, None)
    assert verify.recall_window("dün ne doğruladık", NOW) == (
        datetime(2026, 10, 5, tzinfo=UTC),
        datetime(2026, 10, 6, tzinfo=UTC),
    )
    assert verify.recall_window("geçen ayki doğrulamalar", NOW)[0] == datetime(
        2026, 9, 1, tzinfo=UTC
    )


def test_the_week_is_the_owners_local_week() -> None:
    """Monday 00:30 in Istanbul is still Sunday in UTC: the window follows the clock given."""
    from zoneinfo import ZoneInfo

    istanbul = datetime(2026, 10, 5, 0, 30, tzinfo=ZoneInfo("Europe/Istanbul"))
    since, _ = verify.recall_window("bu hafta ne doğruladık", istanbul)
    assert since == datetime(2026, 10, 5, tzinfo=ZoneInfo("Europe/Istanbul"))


@pytest.mark.parametrize(
    ("phrase", "subject"),
    [
        ("Everest hakkında ne bulmuştuk?", "everest"),
        ("geçen hafta neyi doğrulamıştık", None),
        ("asgari ücret konusunda ne bulmuştuk", "asgari ücret"),
        ("geçen hafta Everest'i doğrulamıştık", "everest"),
    ],
)
def test_the_recall_subject(phrase: str, subject: str | None) -> None:
    assert verify.recall_subject(phrase) == subject


def test_recall_by_subject_ignores_a_cut_suffix() -> None:
    records = [_record("Everest'in 8849 metre olduğu", 29), _record("Ay'da su buzu var", 30)]
    found = verify.recall(records, text=verify.recall_subject("Everest'i doğrulamıştık"))
    assert [r.claim for r in found] == ["Everest'in 8849 metre olduğu"]


# --- the stance judge and the report ------------------------------------------------------


def test_the_judge_reads_support_refutation_and_silence() -> None:
    claim = "Ay'ın yüzeyinde su buzu var"
    stance, quote = verify.judge_stance(
        claim, "Giriş cümlesi. NASA verilerine göre Ay'ın yüzeyinde su buzu var. Son."
    )
    assert stance == verify.STANCE_SUPPORTS
    assert quote == "NASA verilerine göre Ay'ın yüzeyinde su buzu var."
    stance, _ = verify.judge_stance(claim, "Ay'ın yüzeyinde su buzu yok, bu iddia yanlış.")
    assert stance == verify.STANCE_REFUTES
    assert verify.judge_stance(claim, "Mars'ta toz fırtınası çıktı.") == (
        verify.STANCE_NEUTRAL,
        "",
    )


def test_the_judge_reads_a_different_number_as_a_refutation() -> None:
    stance, _ = verify.judge_stance(
        "Everest'in yüksekliği 8849 metre", "Everest'in yüksekliği 8848 metre olarak ölçüldü."
    )
    assert stance == verify.STANCE_REFUTES
    stance, _ = verify.judge_stance(
        "Everest'in yüksekliği 8849 metre", "Everest'in yüksekliği 8849 metre olarak ölçüldü."
    )
    assert stance == verify.STANCE_SUPPORTS


REPORT = {
    "sources": [
        {
            "id": "e0",
            "url": "https://kotu.example/x",
            "title": "Enjeksiyon",
            "excerpt": "Ay'ın yüzeyinde su buzu var.",
            "injection_suspected": True,
        },
        {
            "id": "e1",
            "url": "https://nasa.example/ay",
            "final_url": "https://nasa.example/ay-su",
            "title": "NASA: Ay'da su",
            "published_at": "2026-08-01T00:00:00Z",
            "excerpt": "NASA verilerine göre Ay'ın yüzeyinde su buzu var.",
        },
        {
            "id": "e2",
            "url": "https://haber.example/mars",
            "title": "Mars haberi",
            "published_at": "bozuk tarih",
            "excerpt": "Mars'ta toz fırtınası çıktı.",
        },
    ]
}


def test_the_report_sources_are_judged_and_an_injection_never_decides() -> None:
    judged = verify.sources_from_report("Ay'ın yüzeyinde su buzu var", REPORT)
    assert [s.title for s in judged] == ["NASA: Ay'da su", "Mars haberi"]
    assert judged[0].url == "https://nasa.example/ay-su"
    assert judged[0].stance == verify.STANCE_SUPPORTS
    assert judged[0].published_at == datetime(2026, 8, 1, tzinfo=UTC)
    assert judged[1].stance == verify.STANCE_NEUTRAL
    assert judged[1].published_at is None


# --- the store, the tools, the announcer ----------------------------------------------------


@pytest.fixture()
def scope():
    import contextlib

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.artifacts.models  # noqa: F401 - the `tasks` table the run rows point at
    from app.broker.models import AuditEvent
    from app.research.models import ClaimVerificationRow, ResearchReportRow, ResearchRunRow
    from app.voice.realtime_sessions.models import RealtimeSessionRow, RealtimeToolCall

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        RealtimeSessionRow.__table__,
        RealtimeToolCall.__table__,
        AuditEvent.__table__,
        ResearchRunRow.__table__,
        ResearchReportRow.__table__,
        ClaimVerificationRow.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextlib.contextmanager
    def open_scope():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    yield open_scope
    engine.dispose()


def _seed_run(scope, task_id, *, stage: str, report: dict | None) -> None:
    from app.research.models import ResearchReportRow, ResearchRunRow

    with scope() as db:
        db.add(ResearchRunRow(task_id=task_id, stage=stage))
        if report is not None:
            db.add(
                ResearchReportRow(
                    task_id=task_id, report_json=report, synthesis_provider="deterministic"
                )
            )
        db.commit()


def test_a_pending_verification_settles_when_its_run_is_ready(scope) -> None:
    import uuid

    from app.research.models import STAGE_DISCOVERING, STAGE_READY

    task_id = uuid.uuid4()
    with scope() as db:
        verify.start_pending(
            db,
            said="bunu doğrula: Ay'ın yüzeyinde su buzu var",
            claim="Ay'ın yüzeyinde su buzu var",
            task_id=task_id,
            now=NOW,
        )
        db.commit()
    _seed_run(scope, task_id, stage=STAGE_DISCOVERING, report=None)
    with scope() as db:
        assert verify.settle_task(db, task_id, now=NOW) is None
        assert verify.search(db)[0].status == "pending"
    with scope() as db:
        from app.research.models import ResearchReportRow, ResearchRunRow

        db.get(ResearchRunRow, task_id).stage = STAGE_READY
        db.add(
            ResearchReportRow(
                task_id=task_id, report_json=REPORT, synthesis_provider="deterministic"
            )
        )
        db.commit()
    with scope() as db:
        assert verify.settle_pending(db, now=NOW) == 1
        db.commit()
    with scope() as db:
        row = verify.search(db, text="ay su")[0]
        assert row.status == "settled"
        assert row.verdict == verify.VERDICT_TRUE
        assert [s["title"] for s in row.sources_json] == ["NASA: Ay'da su"]
        assert "Tek kaynak: NASA: Ay'da su, 1 Ağustos 2026." in row.spoken
        # Settled once: a second pass changes nothing.
        assert verify.settle_pending(db, now=NOW) == 0


def test_a_run_with_no_source_settles_belirsiz_never_a_guess(scope) -> None:
    import uuid

    from app.research.models import STAGE_FAILED, STAGE_READY

    for stage, report, spoken in (
        (STAGE_READY, {"sources": []}, "Bunu doğrulayacak bir kaynak bulamadım; hüküm belirsiz."),
        (STAGE_FAILED, None, verify.RUN_FAILED_TR),
    ):
        task_id = uuid.uuid4()
        with scope() as db:
            verify.start_pending(db, said="x", claim="Ay'da su var", task_id=task_id, now=NOW)
            db.commit()
        _seed_run(scope, task_id, stage=stage, report=report)
        with scope() as db:
            payload = verify.settle_task(db, task_id, now=NOW)
            db.commit()
        assert payload is not None
        assert payload["verdict"] == verify.VERDICT_UNKNOWN
        assert payload["verdict_label"] == "BELİRSİZ"
        assert payload["confidence"] == 0.0
        assert payload["sources"] == []
        assert payload["speech"] == spoken


def test_recall_from_the_table_by_text_and_by_date(scope) -> None:
    with scope() as db:
        verify.start_pending(
            db,
            said="a",
            claim="Ay'da su buzu var",
            task_id=None,
            now=datetime(2026, 9, 29, 12, tzinfo=UTC),
        )
        verify.start_pending(
            db,
            said="b",
            claim="Everest'in 8849 metre olduğu",
            task_id=None,
            now=datetime(2026, 10, 5, 12, tzinfo=UTC),
        )
        db.commit()
    with scope() as db:
        since, until = verify.recall_window("geçen hafta", NOW)
        assert [r.claim for r in verify.search(db, since=since, until=until)] == [
            "Ay'da su buzu var"
        ]
        assert [r.claim for r in verify.search(db, text="everest")] == [
            "Everest'in 8849 metre olduğu"
        ]
        assert verify.search(db, text="mars") == []


def _tool_ctx(db, utterance: dict | None = None):
    import uuid

    from app.voice.realtime_sessions.tools import ToolContext

    context = {}
    if utterance is not None:
        context["last_utterance"] = {"at": NOW.isoformat().replace("+00:00", "Z"), **utterance}
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="desktop",
        context=context,
        db=db,
        now=NOW,
    )


def test_the_recall_tool_answers_from_the_owners_own_sentence(scope) -> None:
    from app.voice.realtime_sessions.tools_verify import research_verify_recall

    with scope() as db:
        row = verify.start_pending(
            db,
            said="a",
            claim="Everest'in 8849 metre olduğu",
            task_id=None,
            now=datetime(2026, 10, 5, 12, tzinfo=UTC),
        )
        row.status, row.verdict, row.confidence = "settled", verify.VERDICT_FALSE, 0.65
        row.sources_json = [{"title": "Ölçüm Raporu", "url": "https://o.example"}]
        db.commit()
    with scope() as db:
        ctx = _tool_ctx(
            db, {"intent": "verify_recall", "research_topic": "Everest hakkında ne bulmuştuk?"}
        )
        result = research_verify_recall(ctx, {})
    assert result["count"] == 1
    assert result["subject"] == "everest"
    assert "yanlış" in result["speech"]
    assert "Ölçüm Raporu" in result["speech"]
    with scope() as db:
        nothing = research_verify_recall(_tool_ctx(db), {"query": "Mars hakkında ne bulmuştuk"})
    assert nothing["speech"] == verify.NOTHING_RECALLED_TR


def test_the_verify_tool_refuses_without_a_claim(scope) -> None:
    from app.voice.errors import VoiceError
    from app.voice.realtime_sessions.tools_verify import research_verify

    with scope() as db, pytest.raises(VoiceError) as raised:
        research_verify(_tool_ctx(db, {"intent": "none"}), {})
    assert raised.value.details["speech"].startswith("Neyi doğrulamamı")


def test_the_verify_tools_are_registered() -> None:
    from app.voice.realtime_sessions.tools import default_registry

    reg = default_registry()
    verify_spec = reg.get(TOOL_VERIFY)
    assert verify_spec is not None and verify_spec.long_running
    recall_spec = reg.get(TOOL_VERIFY_RECALL)
    assert recall_spec is not None and not recall_spec.long_running


def test_the_announcer_speaks_the_verdict_with_its_source(scope) -> None:
    """Acceptance: the owner hears the verdict - not the research summary - when the run of a
    ``research.verify`` call is ready."""
    import uuid
    from datetime import timedelta

    from app.research.models import STAGE_READY
    from app.voice.realtime_sessions.models import (
        REALTIME_STATE_ACTIVE,
        TOOL_STATUS_RUNNING,
        TOOL_STATUS_SUCCEEDED,
        RealtimeSessionRow,
        RealtimeToolCall,
    )
    from app.voice.realtime_sessions.research_announcer import ResearchToolCallAnnouncer
    from app.voice.realtime_sessions.sideband import RecordingSideband

    task_id = uuid.uuid4()
    with scope() as db:
        session_row = RealtimeSessionRow(
            id=uuid.uuid4(),
            provider="simulator",
            transport="webrtc",
            client_kind="desktop",
            device_id=uuid.uuid4(),
            owner_session_id=uuid.uuid4(),
            language="tr-TR",
            state=REALTIME_STATE_ACTIVE,
            context_json={},
            transcript_summary="",
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
            updated_at=NOW,
        )
        db.add(session_row)
        db.add(
            RealtimeToolCall(
                session_id=session_row.id,
                call_id="v1",
                name=TOOL_VERIFY,
                arguments_json={"claim": "Ay'ın yüzeyinde su buzu var"},
                status=TOOL_STATUS_RUNNING,
                long_running=True,
                result_json={"status": "running", "task_id": str(task_id)},
            )
        )
        verify.start_pending(
            db, said="bunu doğrula", claim="Ay'ın yüzeyinde su buzu var", task_id=task_id, now=NOW
        )
        db.commit()
    _seed_run(scope, task_id, stage=STAGE_READY, report=REPORT)
    sideband = RecordingSideband(deliver=True)

    assert ResearchToolCallAnnouncer(scope, sideband).sweep_once(now=NOW) == 1

    with scope() as db:
        call = db.query(RealtimeToolCall).filter(RealtimeToolCall.call_id == "v1").one()
        assert call.status == TOOL_STATUS_SUCCEEDED
        assert call.result_json["speech"].startswith("Hüküm: doğru")
        assert "NASA: Ay'da su" in call.result_json["speech"]
        assert call.result_json["verdict_label"] == "DOĞRU"
        assert verify.search(db)[0].status == "settled"
    _, frame = sideband.frames[0]
    assert frame["payload"]["result"]["speech"] == call.result_json["speech"]
