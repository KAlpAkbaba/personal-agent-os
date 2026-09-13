"""B17 req 39-45: what this system knows about its owner, in front of the model.

B16 gave the owner a way to teach this system something. Without this half that was a
write-only diary: `hybrid_search` has been complete since M5, B16 gave it its first
product caller (`memory.search`, when the owner ASKS), and nothing had ever put a memory in
front of the model on the path where it answers.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.memory import service as memory_service
from app.memory.embedding import DeterministicEmbedder
from app.memory.injection import (
    MAX_INJECTED,
    MAX_INSTRUCTION_CHARS,
    MIN_INFERRED_CONFIDENCE,
    rank,
    select_for_instruction,
)
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.policy import Observation
from app.memory.types import MemoryClass

NOW = datetime(2026, 9, 13, 9, 0, tzinfo=UTC)
EMBEDDER = DeterministicEmbedder()

_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in _TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


def _taught(db: Session, text: str, *, key: str | None = None) -> Memory:
    """What the owner SAID: explicit, durable, confidence 1.0."""
    result = memory_service.remember_explicit(
        db, EMBEDDER, text=text, memory_class=MemoryClass.PREFERENCE, key=key
    )
    return db.get(Memory, result.memory_id)


def _inferred(db: Session, text: str, *, confidence: float = 0.3) -> Memory:
    """What the system worked out: a candidate, capped."""
    result = memory_service.record_observation(
        db, EMBEDDER, Observation(text=text, memory_class=MemoryClass.PREFERENCE)
    )
    memory = db.get(Memory, result.memory_id)
    memory.confidence = confidence
    db.commit()
    return memory


#: Fifty preferences about fifty different things. The first draft of the budget tests
#: used "Sahip {i} numaralı tercihi ..." and got ONE row back: the sentences differ by a
#: digit, which is well above `DEDUP_SIMILARITY`, so the memory service corroborated all
#: fifty into the first one and the ceiling was never tested. A fixture that quietly
#: collapses is a test that quietly stops asserting - `considered` is checked below so it
#: cannot happen again silently.
_FIFTY_DISTINCT: tuple[str, ...] = (
    "Kahveyi sade içerim ve akşam altıdan sonra hiç içmem.",
    "Toplantıları sabah dokuzdan önce koymayı sevmem.",
    "Raporlar bir sayfayı geçmesin, gerisini sorarım.",
    "Bildirim sesi kapalı kalsın, titreşim yeter.",
    "Ekran parlaklığı akşamları kendiliğinden düşsün.",
    "Sabahları müzik değil haber duymak isterim.",
    "Faturaları ayın beşinde öderim.",
    "Öğle yemeğini bir buçukta yerim.",
    "Eve dönerken otoyolu değil sahil yolunu kullanırım.",
    "Haberleri tek bir kaynaktan takip ederim.",
    "Hafta sonu alarm kurulmasın.",
    "Uzun metinleri bana özetleyerek anlat.",
    "Teknik ayrıntıyı ancak istediğimde ver.",
    "Kod incelemesinde önce testlere bakarım.",
    "Yeni bir araç kurmadan önce bana sor.",
    "Kargo bildirimlerini biriktirip akşam söyle.",
    "Doktor randevularını sabaha al.",
    "Spor salonuna salı ve perşembe giderim.",
    "Kitap önerilerini kurgu dışından yap.",
    "Film önerirken korku türünü atla.",
    "Alışveriş listesini market bazında ayır.",
    "Su faturasını otomatik ödemeye bağladım.",
    "Telefonu şarjda bırakıp uyurum.",
    "Sabah duşunu kahvaltıdan önce alırım.",
    "Yazışmalarda resmi dil kullanmanı isterim.",
    "İngilizce terimleri Türkçeye çevirmeye çalışma.",
    "Sunumlarda koyu tema kullan.",
    "Grafiklerde kırmızıyı uyarı için sakla.",
    "Dosya adlarına tarih ekle.",
    "Yedeklemeyi gece yarısı yap.",
    "Parolaları asla bir yere yazma.",
    "Takvimimi başkasıyla paylaşma.",
    "Konum bilgimi yalnızca hava için kullan.",
    "Sesli yanıtları kısa tut.",
    "Beni uyandırırken önce selam ver.",
    "Uçak biletlerinde koridor koltuğu seçerim.",
    "Otel ararken kahvaltı dahil olanlara bak.",
    "Kirayı ayın birinde öderim.",
    "Arabayı altı ayda bir servise veririm.",
    "Bitkileri pazar günleri sularım.",
    "Çayı demli severim.",
    "Tatlıyı öğleden sonra yerim.",
    "Kışın camı sabah on dakika açarım.",
    "Kediyi akşam yedide beslerim.",
    "Ailemi pazar akşamları ararım.",
    "Bankadan gelen aramaları açmam.",
    "Reklam e-postalarını doğrudan sil.",
    "Yazıcıyı yalnızca gerektiğinde aç.",
    "Klavyeyi Türkçe Q düzeninde kullanırım.",
    "Gece modunu saat onda başlat.",
)


# --------------------------------------------------------------------- what gets in


def test_what_the_owner_taught_reaches_the_instruction(db):
    """req 39/41. The first time a memory has ever been in front of the model."""
    _taught(db, "Kahveyi sade severim.")

    selection = select_for_instruction(db, EMBEDDER, now=NOW)

    assert len(selection.memories) == 1
    assert "Kahveyi sade severim." in selection.as_block()


def test_the_block_says_which_sentences_the_owner_actually_said(db):
    """The distinction has to SURVIVE into the instruction. A system that presents its own
    guesses as things the owner told it is how it starts confidently asserting things they
    never said - and the owner cannot catch it, because it sounds like the memory working.
    """
    _taught(db, "Kahveyi sade severim.")
    _inferred(db, "Sahip her zaman koyu temayı kullanıyor.", confidence=0.6)

    block = select_for_instruction(db, EMBEDDER, now=NOW).as_block()

    assert "Kahveyi sade severim. (sahibin söylediği)" in block
    assert "(çıkarım, güven %60)" in block
    assert "emin değilsen sor" in block


def test_a_block_with_no_inferred_rows_does_not_carry_the_warning(db):
    _taught(db, "Kahveyi sade severim.")

    block = select_for_instruction(db, EMBEDDER, now=NOW).as_block()

    assert "emin değilsen sor" not in block


def test_nothing_remembered_is_no_block_rather_than_an_empty_heading(db):
    """A section that announces knowledge and lists none spends tokens saying nothing, and
    reads to a model as an instruction it has failed to satisfy."""
    selection = select_for_instruction(db, EMBEDDER, now=NOW)

    assert selection.memories == []
    assert selection.as_block() == ""


# ------------------------------------------------------------- req 45: explicit wins


def test_what_the_owner_said_outranks_what_the_system_worked_out(db):
    """req 45, and a PRECEDENCE rather than a weight.

    `hybrid_search` scores explicitness alongside recency in one sum, so a guess made this
    morning can outrank something the owner stated in March. That is the right shape for
    "find me what is relevant" and the wrong one for "what does this system believe about
    its owner". The inferred row below is newer AND retrieved; it still comes second.
    """
    stated = _taught(db, "Kahveyi sade severim.")
    stated.last_confirmed_at = NOW - timedelta(days=200)
    db.commit()
    guessed = _inferred(db, "Sahip çayı tercih ediyor.", confidence=0.9)
    guessed.last_confirmed_at = NOW
    db.commit()

    ordered = rank([guessed, stated])

    assert ordered[0].id == stated.id
    assert select_for_instruction(db, EMBEDDER, now=NOW).as_block().index(
        "Kahveyi sade"
    ) < select_for_instruction(db, EMBEDDER, now=NOW).as_block().index("çayı tercih")


def test_a_guess_the_system_has_not_stood_up_yet_stays_out(db):
    """`policy.decide` caps a single observation at 0.4, so the floor sits just above it:
    an inferred row reaches the owner's persona once evidence has accumulated, not the
    first time a conversation summary mentioned something."""
    _inferred(db, "Sahip belki sabahları sessizlik istiyor.", confidence=0.3)

    selection = select_for_instruction(db, EMBEDDER, now=NOW)

    assert selection.memories == []
    assert selection.dropped_low_confidence == 1
    assert 0.3 < MIN_INFERRED_CONFIDENCE


def test_a_corrected_memory_cannot_come_back_through_the_instruction(db):
    """req 44's other half, and it was already right: `retrieval.apply_filters` refuses
    non-active rows unconditionally, so a superseded memory is not reachable here. Asserted
    rather than re-implemented - this test exists so that changing that filter fails
    something that says why it matters."""
    first = _taught(db, "Kahveyi sade severim.", key="coffee.style")
    memory_service.remember_explicit(
        db,
        EMBEDDER,
        text="Kahveyi az şekerli severim.",
        memory_class=MemoryClass.PREFERENCE,
        key="coffee.style",
    )

    block = select_for_instruction(db, EMBEDDER, now=NOW).as_block()

    assert db.get(Memory, first.id).status == "superseded"
    assert "sade" not in block
    assert "az şekerli" in block


# ------------------------------------------------------- the budget the roadmap named


def test_the_injected_block_stays_under_its_ceiling(db):
    """B17's own RISK line: "persona talimatı büyür, jeton bütçesi ölçülmeli".

    The persona is 10,110 characters before any of this and it is sent on every session
    create AND every attach, so an unbounded block is a token bill that grows with the
    owner's history. Fifty memories go in; the ceiling is what comes out.
    """
    for text in _FIFTY_DISTINCT:
        _taught(db, text)

    selection = select_for_instruction(db, EMBEDDER, now=NOW)

    assert selection.considered > MAX_INJECTED, "the fixture must not have deduplicated"
    assert len(selection.as_block()) <= MAX_INSTRUCTION_CHARS
    assert len(selection.memories) <= MAX_INJECTED
    assert selection.dropped_for_budget > 0, "fifty memories must not all fit"


def test_the_ceiling_is_spent_in_rank_order_and_stops(db):
    """It STOPS at the first memory that does not fit rather than skipping ahead to a
    shorter one. A block that silently preferred terse memories over important ones would
    be a ranking nobody wrote down."""
    _taught(db, "A" * 400 + ".")
    short = _taught(db, "Kısa.")
    for memory in (short,):
        memory.confidence = 1.0
    db.commit()

    selection = select_for_instruction(db, EMBEDDER, now=NOW, max_chars=500)

    assert len(selection.memories) == 1
    assert selection.dropped_for_budget >= 1


def test_the_whole_instruction_grows_by_at_most_the_ceiling(db):
    """The number that actually matters is what the provider is sent."""
    from app.voice.realtime_sessions.persona import build_instructions

    for text in _FIFTY_DISTINCT:
        _taught(db, text)
    block = select_for_instruction(db, EMBEDDER, now=NOW).as_block()

    base = build_instructions()
    grown = build_instructions(memory_block=block)

    assert len(grown) - len(base) <= MAX_INSTRUCTION_CHARS + 1


# -------------------------------------------------------------------------- the wiring


def _session_row(session_id: uuid.UUID, owner_session_id: uuid.UUID) -> Any:
    from app.voice.realtime_sessions.models import REALTIME_STATE_ACTIVE, RealtimeSessionRow

    return RealtimeSessionRow(
        id=session_id,
        provider="openai",
        transport="webrtc",
        client_kind="web",
        device_id=None,
        owner_session_id=owner_session_id,
        state=REALTIME_STATE_ACTIVE,
        expires_at=None,
    )


def test_the_session_the_provider_is_given_carries_the_memory(db):
    """The guard, driven through the REAL `_session_config`.

    An injection module nothing calls is the defect this repository keeps paying for, and
    here it looks exactly like a memory that is written, searchable, explainable — and
    never once consulted.
    """
    from app.voice.realtime_sessions import service as realtime

    class _Runtime:
        embedder = EMBEDDER

    _taught(db, "Kahveyi sade severim.")
    row = _session_row(uuid.uuid4(), uuid.uuid4())

    config = realtime._session_config(
        row,
        registry=_EmptyRegistry(),
        prefs=None,
        memory_block=realtime._memory_block(db, _Runtime(), now=NOW),
    )

    assert "Kahveyi sade severim." in config.instructions


def test_a_process_with_no_memory_runtime_mints_the_session_it_always_did(db):
    from app.voice.realtime_sessions import service as realtime

    assert realtime._memory_block(db, None, now=NOW) == ""


def test_a_retrieval_that_raises_costs_a_block_and_never_the_session(db, monkeypatch):
    """This runs while minting a session credential. An owner whose voice stopped working
    because a preference could not be retrieved would rightly regard the memory feature as
    having made things worse."""
    from app.memory import injection
    from app.voice.realtime_sessions import service as realtime

    class _Runtime:
        embedder = EMBEDDER

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("pgvector went away")

    monkeypatch.setattr(injection, "select_for_instruction", _boom)

    assert realtime._memory_block(db, _Runtime(), now=NOW) == ""


def test_one_setting_turns_the_injection_off_again():
    """B17's own rollback plan: "enjeksiyon bayrakla kapatılır; persona eski haline döner".
    That has to be a setting, not a revert - and it has to be read where the runtime is
    resolved, or the flag and the wiring will one day disagree."""
    import inspect

    from app.config import Settings
    from app.voice.realtime_sessions import routes

    source = inspect.getsource(routes._memory_runtime)

    assert "memory_injection_enabled" in source
    assert 'live_sources().get("memory_runtime")' in source
    assert Settings(_env_file=None).memory_injection_enabled is True
    off = Settings(_env_file=None, memory_injection_enabled=False)
    assert off.memory_injection_enabled is False


def test_both_the_create_and_the_attach_path_carry_it():
    """A session created today and reattached tomorrow must know the same things. The
    attach path mints a FRESH credential with fresh instructions (spec §7), so an
    injection wired only into create would work until the owner switched devices."""
    import inspect

    from app.voice.realtime_sessions import routes

    for fn in (routes.create_session, routes.attach_session):
        assert "memory_runtime=_memory_runtime(runtime)" in inspect.getsource(fn), fn.__name__


class _EmptyRegistry:
    def manifest(self) -> list[dict[str, Any]]:
        return []


def test_nothing_superseded_is_ever_retrieved(db):
    """The unconditional filter, asserted directly so a change to it fails here too."""
    memory = _taught(db, "Kahveyi sade severim.")
    memory.status = "superseded"
    db.commit()

    assert select_for_instruction(db, EMBEDDER, now=NOW).memories == []
    assert db.execute(select(Memory)).scalars().all() != []


# --------------------------------------------- the chain, through the real application


def test_a_preference_taught_by_voice_reaches_a_NEW_session_without_being_repeated():
    """B17's REAL_PROOF_REQUIRED, everything but the production round.

    "bir tercih söylendi, YENİ bir oturumda tekrar söylenmeden uygulandı." This drives the
    real application object: a real session is created over HTTP, the owner teaches
    something through the real `memory.remember` tool, that session is closed, and a
    SECOND session is created — whose instructions the provider is handed must carry what
    the owner said in the first one.

    It is the whole chain B16 and B17 exist to build, and until this passed no part of the
    product had ever demonstrated it end to end: B16 could write and never read, B17 could
    read and had never been driven from a real write.
    """
    from tests.voice_corpus.harness import build_harness

    harness = build_harness()
    first = harness.new_session()
    answer = harness.client.post(
        f"/v1/voice/realtime/sessions/{first}/tool-calls",
        json={
            "call_id": "teach-1",
            "name": "memory.remember",
            "arguments": {"statement": "Kahveyi sade severim."},
        },
    )
    assert answer.status_code in (200, 201), answer.text
    harness.client.post(f"/v1/voice/realtime/sessions/{first}/close", json={})

    second = harness.client.post("/v1/voice/realtime/sessions", json={})

    assert second.status_code == 201, second.text
    instructions = second.json()["instructions"]
    assert "Kahveyi sade severim." in instructions
    assert "sahibin söylediği" in instructions


# ------------------------------------------------ req 56: the last gate before it leaves


def test_a_secret_shaped_memory_never_reaches_the_instruction(db):
    """req 56, and this batch is what makes it matter.

    The write policy refuses a credential at the door, so nothing here should ever fire.
    "Should never" is not where to stop when the consequence is a credential travelling to
    a third-party model provider inside a session instruction: a row can predate a pattern
    the guard learned later, or arrive through a path the guard does not cover. This is the
    last gate before the text leaves the machine, and a last gate that trusts the first one
    is not a gate.
    """
    memory = _taught(db, "Sunucu adresi 10.0.0.4.")
    memory.text = "Sunucu parolası password: hunter2-correct-horse"
    db.commit()

    selection = select_for_instruction(db, EMBEDDER, now=NOW)

    assert selection.memories == []
    assert selection.dropped_secret_shaped == 1
    assert "hunter2" not in selection.as_block()


def test_the_gate_uses_the_write_policy_s_own_patterns(db):
    """Not a second list. `app.memory.policy.find_secret` is the one that decides what a
    credential looks like in this product, and a second copy would be two halves of one
    rule with their own memories of the answer."""
    import inspect

    from app.memory import injection

    assert "find_secret" in inspect.getsource(injection.select_for_instruction)


# --------------------------------------- req 42/43: many sources, distinguishable trust


def test_every_write_path_leaves_a_provenance_that_says_which_one(db):
    """req 42. The matrix recorded "şemada var, tek yazıcı araştırma - 8 satır, hepsi
    araştırma". B16 gave the schema two more writers and this holds them apart: a row the
    owner stated and a row this system worked out must be distinguishable from the row
    itself, or "explicit outranks inferred" has nothing to read."""
    stated = _taught(db, "Kahveyi sade severim.")
    worked_out = _inferred(db, "Sahip sabahları sessizlik istiyor.", confidence=0.6)

    assert stated.provenance_json["origin"] == "owner_statement"
    assert worked_out.provenance_json["origin"] == "observation"
    assert stated.provenance_json["policy_reason"]
    assert worked_out.provenance_json["policy_reason"]


def test_confidence_separates_what_was_said_from_what_was_guessed(db):
    """req 43. "Yazılı, üretimde tek sınıf" - one class in production because only one
    writer existed. Two now, and the number means something: 1.0 is what the owner said,
    and an inference is capped below it by the policy no matter how often it recurs."""
    from app.memory.lifecycle import MAX_INFERRED_CONFIDENCE
    from app.memory.types import SINGLE_OBSERVATION_MAX_CONFIDENCE

    stated = _taught(db, "Kahveyi sade severim.")
    worked_out = _inferred(db, "Sahip sabahları sessizlik istiyor.", confidence=0.6)

    assert stated.confidence == 1.0
    assert worked_out.confidence <= MAX_INFERRED_CONFIDENCE < 1.0
    assert SINGLE_OBSERVATION_MAX_CONFIDENCE < MAX_INFERRED_CONFIDENCE


# ------------------------------------------------------- req 55: the sweep, as measured


def test_the_retention_sweep_is_wired_and_runs():
    """req 55 was recorded MISSING and is not: `lifecycle.sweep_expired` sweeps by
    retention class (session/short TTL, never pinned, never explicit, deletion following
    the forget path), `create_app` registers it in the `RetentionSweeper`, the lifespan
    starts it and `/health` reports it. Measured 2026-09-13, wired since Phase 8
    (2026-09-11).

    What was missing is this: a guard. The matrix could say MISSING for two batches because
    nothing failed when it was.
    """
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    sweeper = app.state.retention_sweeper

    assert "memory" in sweeper._sweeps
    assert sweeper._interval_s > 0
    # It reports itself, so "the sweep has not run since Tuesday" is answerable.
    assert set(sweeper.health_check()) >= {"status"}
