"""B16 req 31-38, 61-62: the owner's voice over their own memory.

`app.memory` has been complete since M5 — a frozen write-policy decision table with a
secrets guard, evidence and version chains, an audit log, hybrid retrieval, a promotion
ladder, a full REST surface — and nothing under `app/voice/` imported one line of it. The
only non-REST caller in the product is `app.experience`, which reads the Activity Ledger
and never touches conversation. So the owner could teach this system nothing by speaking.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import EVENT_TYPE_MEMORY_REMEMBERED, EVENT_TYPE_MEMORY_USED
from app.memory.embedding import DeterministicEmbedder
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.types import Actor, MemoryClass
from app.operator.models import ObjectFocusRow
from app.voice.errors import VoiceError
from app.voice.realtime_sessions.models import RealtimeSessionRow
from app.voice.realtime_sessions.tools_memory import (
    MEMORY_TOOL_NAMES,
    memory_correct,
    memory_forget,
    memory_pin,
    memory_remember,
    memory_search,
    memory_why,
)

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
    ActivityEventRow.__table__,
    ObjectFocusRow.__table__,
    RealtimeSessionRow.__table__,
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


class _Runtime:
    embedder = EMBEDDER


class _Ctx:
    """A ToolContext's shape, with nothing a memory tool does not read."""

    def __init__(
        self,
        db: Session,
        *,
        runtime: Any = None,
        call_id: str = "c1",
        session_id: uuid.UUID | None = None,
    ) -> None:
        self.db = db
        # A FIXED id by default, so most tests can build several contexts that all belong to
        # one conversation; overridden where a test needs a genuinely different one.
        self.session_id = session_id or uuid.UUID("11111111-1111-1111-1111-111111111111")
        self.owner_session_id = uuid.uuid4()
        self.device_id = None
        self.client_kind = "desktop"
        self.context: dict[str, Any] = {}
        self.now = NOW
        self.call_id = call_id
        self.live: dict[str, Any] = {"memory_runtime": runtime or _Runtime()}


def _ledger(db: Session, event_type: str) -> list[ActivityEventRow]:
    return list(
        db.execute(select(ActivityEventRow).where(ActivityEventRow.event_type == event_type))
        .scalars()
        .all()
    )


def _remember(ctx: _Ctx, statement: str, **extra: Any) -> dict[str, Any]:
    return memory_remember(ctx, {"statement": statement, **extra})


# ------------------------------------------------------------------------- remember


def test_the_owner_can_teach_a_preference_by_speaking(db):
    """req 31/35. The first sentence in this product's history that reaches `app.memory`
    without a browser."""
    ctx = _Ctx(db)

    answer = _remember(ctx, "Kahveyi sade severim.")

    memory = db.get(Memory, uuid.UUID(answer["memory_id"]))
    assert memory is not None
    assert memory.text == "Kahveyi sade severim."
    assert answer["speech"] == "Aklımda efendim."


def test_what_the_owner_says_is_durable_and_carries_its_provenance(db):
    """The roadmap's own test plan for B16: "yazılan kaydın provenans ve güven taşıdığı".

    Durable and `Actor.OWNER`, because `policy.decide()` grants that on the caller-asserted
    flag and a step-up-gated realtime session is one of the surfaces the policy names as
    allowed to assert it. A CANDIDATE row would make "sesle kalıcı bellek yazılır" false.
    """
    ctx = _Ctx(db)

    answer = _remember(ctx, "Toplantıları sabah dokuza koymayı tercih ederim.")

    memory = db.get(Memory, uuid.UUID(answer["memory_id"]))
    assert memory.explicit is True
    assert memory.confidence == 1.0
    assert memory.stage == "durable"
    assert memory.provenance_json.get("origin") == "owner_statement"
    assert memory.provenance_json["source"]["channel"] == "voice"


def test_a_credential_said_out_loud_is_refused_out_loud(db):
    """The roadmap's third test-plan clause: "hassas verinin dışarıda kaldığı".

    Refused AND heard. A password spoken and quietly dropped is worse than one spoken and
    refused: the owner would believe it was stored and never learn otherwise. The sentence
    the owner hears is the memory subsystem's own.
    """
    ctx = _Ctx(db)

    with pytest.raises(VoiceError) as caught:
        _remember(ctx, "Sunucu parolası: password: hunter2-correct-horse")

    assert caught.value.message == "refused to store secret-like content as memory"
    assert caught.value.details["memory_error_class"] == "secret_rejected"
    assert db.execute(select(Memory)).scalars().all() == []


def test_teaching_writes_the_ledger_event_that_had_never_been_written(db):
    """`EVENT_TYPE_MEMORY_REMEMBERED` has been enumerated in the ledger's vocabulary since
    the ledger was written and NOTHING ever emitted it. This is its first writer."""
    ctx = _Ctx(db)

    answer = _remember(ctx, "Raporları kısa tut.")

    rows = _ledger(db, EVENT_TYPE_MEMORY_REMEMBERED)
    assert len(rows) == 1
    assert rows[0].detail_json["memory_id"] == answer["memory_id"]
    assert "Raporları kısa tut." in rows[0].factual_summary


def test_a_paragraph_is_refused_as_a_memory(db):
    ctx = _Ctx(db)

    with pytest.raises(VoiceError) as caught:
        _remember(ctx, "x" * 501)

    assert "a fact, not a document" in caught.value.message


# --------------------------------------------------------------------------- search


def test_search_reads_matches_back_and_numbers_them(db):
    """req 32, and the step every destructive tool depends on: it is what turns "bunu"
    into an id the owner has actually heard."""
    ctx = _Ctx(db)
    _remember(ctx, "Kahveyi sade severim.")
    _remember(_Ctx(db, call_id="c2"), "Çayı demli severim.")

    answer = memory_search(_Ctx(db, call_id="c3"), {"query": "kahve"})

    assert answer["count"] >= 1
    assert "1." in answer["speech"]
    assert all(uuid.UUID(m["memory_id"]) for m in answer["memories"])


def test_a_search_that_finds_nothing_says_so(db):
    answer = memory_search(_Ctx(db), {"query": "denizaltı"})

    assert answer["count"] == 0
    assert answer["speech"] == "Bu konuda kayıtlı bir şey bulamadım efendim."


def test_every_memory_read_back_to_the_owner_gets_a_receipt(db):
    """req 62. Nothing anywhere recorded that a memory had been USED, so "hangi kaydı
    kullandın?" was a question the system could not answer about itself."""
    ctx = _Ctx(db)
    _remember(ctx, "Kahveyi sade severim.")

    memory_search(_Ctx(db, call_id="search-1"), {"query": "kahve"})

    used = _ledger(db, EVENT_TYPE_MEMORY_USED)
    assert len(used) == 1
    assert used[0].detail_json["reason"] == "search"


def test_a_search_nobody_heard_is_not_a_use(db):
    """The receipt is stamped where the memory LEAVES for somebody who will act on it, so
    a search that matched nothing writes none - the rule `app.notifications` paid for as
    "a queue is not a delivery"."""
    memory_search(_Ctx(db), {"query": "denizaltı"})

    assert _ledger(db, EVENT_TYPE_MEMORY_USED) == []


# --------------------------------------------------------------------------- forget


def test_forgetting_needs_an_id_and_refuses_a_description(db):
    """The sharpest tool in this product's voice surface. `app.memory` HARD-deletes the
    row, its versions, its evidence and its embeddings and says so on purpose, so a
    fuzzy match is not offered one: the owner hears `memory.search` read the matches back
    and names the one they meant."""
    ctx = _Ctx(db)

    with pytest.raises(VoiceError) as caught:
        memory_forget(ctx, {"memory_id": "kahve tercihi"})

    assert "memory.search first" in caught.value.message


def test_forgetting_by_id_removes_the_row_and_says_what_went(db):
    ctx = _Ctx(db)
    answer = _remember(ctx, "Kahveyi sade severim.")

    gone = memory_forget(_Ctx(db, call_id="f1"), {"memory_id": answer["memory_id"]})

    assert db.get(Memory, uuid.UUID(answer["memory_id"])) is None
    assert "Kahveyi sade severim." in gone["speech"]


def test_forgetting_an_unknown_id_is_a_sentence_not_a_crash(db):
    with pytest.raises(VoiceError) as caught:
        memory_forget(_Ctx(db), {"memory_id": str(uuid.uuid4())})

    assert caught.value.details["memory_error_class"] == "not_found"


# ------------------------------------------------------------------ correct and pin


def test_a_correction_versions_the_row_rather_than_replacing_it(db):
    """req 37. The owner is correcting what the system got WRONG, not replacing a fact
    that used to be true - so the previous text survives as a version."""
    ctx = _Ctx(db)
    answer = _remember(ctx, "Kahveyi sade severim.")

    fixed = memory_correct(
        _Ctx(db, call_id="c-fix"),
        {"memory_id": answer["memory_id"], "statement": "Kahveyi az şekerli severim."},
    )

    assert fixed["version"] == 2
    versions = (
        db.execute(
            select(MemoryVersion).where(MemoryVersion.memory_id == uuid.UUID(answer["memory_id"]))
        )
        .scalars()
        .all()
    )
    assert any(v.edited_by == Actor.OWNER.value for v in versions)


def test_pinning_marks_the_row_the_system_may_never_rewrite(db):
    ctx = _Ctx(db)
    answer = _remember(ctx, "Faturaları ayın beşinde öderim.")

    pinned = memory_pin(_Ctx(db, call_id="p1"), {"memory_id": answer["memory_id"]})

    assert pinned["pinned"] is True
    assert db.get(Memory, uuid.UUID(answer["memory_id"])).pinned is True


# ------------------------------------------------------------------------------ why


def test_the_system_can_say_why_it_remembers_something(db):
    """req 61. `inspect_memory` has returned provenance, evidence, versions, audit and
    conflicts since M5, and nothing ever turned any of it into a sentence."""
    ctx = _Ctx(db)
    answer = _remember(ctx, "Kahveyi sade severim.")

    explained = memory_why(_Ctx(db, call_id="w1"), {"memory_id": answer["memory_id"]})

    assert "siz söylediniz" in explained["speech"]
    assert "Kahveyi sade severim." in explained["speech"]
    assert explained["speech"].endswith("efendim.")


def test_explaining_a_memory_is_also_a_use(db):
    ctx = _Ctx(db)
    answer = _remember(ctx, "Kahveyi sade severim.")

    memory_why(_Ctx(db, call_id="w2"), {"memory_id": answer["memory_id"]})

    used = _ledger(db, EVENT_TYPE_MEMORY_USED)
    assert [row.detail_json["reason"] for row in used] == ["explain"]


def test_an_inferred_memory_says_it_was_inferred_and_how_sure_it_is(db):
    """The other half of req 61, and the one that matters: an owner asking "why do you
    remember this" about something they never said deserves to hear that nobody told it."""
    from app.memory import service as memory_service
    from app.memory.policy import Observation

    result = memory_service.record_observation(
        db,
        EMBEDDER,
        Observation(
            text="Sahip her zaman koyu temayı kullanıyor.",
            memory_class=MemoryClass.PREFERENCE,
            source={"kind": "inference"},
        ),
    )

    explained = memory_why(_Ctx(db), {"memory_id": str(result.memory_id)})

    assert "konuşmalardan çıkardım" in explained["speech"]
    assert "güvenim yüzde" in explained["speech"]


# -------------------------------------------------------------------------- wiring


def test_a_tool_never_invents_its_own_embedder(db):
    """ADR-0078's own lesson, applied before it can be paid for again. A tool that built a
    `DeterministicEmbedder()` when the runtime was missing would write vectors from a
    different model into the one embeddings table, and every semantic search would quietly
    get worse. It refuses instead."""
    ctx = _Ctx(db)
    ctx.live = {}

    with pytest.raises(VoiceError) as caught:
        _remember(ctx, "Kahveyi sade severim.")

    assert "needs the memory runtime" in caught.value.message


def test_the_application_gives_the_voice_session_the_memory_runtime():
    """The guard. Registering a live source is what the unit tests above CANNOT prove: they
    inject one into `ToolContext.live` themselves, which is exactly how ADR-0078's defect
    survived - every alarm tool worked in tests and answered "no device runtime" in
    production."""
    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))

    live = app.state.voice_realtime.live_sources()
    assert live.get("memory_runtime") is not None
    assert live["memory_runtime"] is app.state.memory


def test_every_memory_tool_is_registered_and_tiered():
    """B05's step-up gate covers the tiers; this covers the other half - that the six names
    this module exports are the six the running registry actually offers."""
    from app.security.step_up import TIER_OPEN, tier_of
    from app.voice.realtime_sessions.tools import default_registry

    registry = default_registry()

    for name in MEMORY_TOOL_NAMES:
        assert registry.get(name) is not None, name
    # The four that change what the system believes about its owner are NOT open.
    for name in ("memory.remember", "memory.forget", "memory.correct", "memory.pin"):
        assert tier_of(name) != TIER_OPEN, name
    assert len(MEMORY_TOOL_NAMES) == 6


# ------------------------------------------- the vocabulary is the memory service's own


def test_the_provenance_vocabulary_is_the_memory_service_s_own():
    """Read out of the WRITERS, not restated from memory.

    The first draft of `_ORIGIN_TR` keyed on a `provenance["kind"]` that nothing in this
    product sets, so `memory.why` answered "nereden geldiğini kaydetmemişim" for every
    single memory while the row said exactly where it came from - an explanation that was
    wrong in the one direction that matters, because the owner cannot check it. It was
    caught by one test about an inferred memory; this is the guard that catches the next
    origin somebody adds.

    Same discipline as `test_desktop_capability_mirror` and B15's
    `test_the_briefing_clip_bound_is_the_device_s_own`: two halves of one vocabulary, and
    this fails when they drift.
    """
    import re
    from pathlib import Path

    from app.memory.receipts import _ORIGIN_TR

    package = Path(__file__).resolve().parents[2] / "app" / "memory"
    written: set[str] = set()
    for source in package.glob("*.py"):
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(r'"origin":\s*(.+)', text):
            # Everything quoted after the key, stopping at the next KEY - one writer is
            # `{"origin": "eval_corpus", "slug": ...}` and "slug" is not an origin.
            for value, is_key in re.findall(r'"([a-z_]+)"(\s*:)?', match.group(1)):
                if is_key:
                    break
                written.add(value)

    assert written, "no provenance origin is written anywhere; this test is reading nothing"
    missing = written - set(_ORIGIN_TR)
    assert not missing, (
        f"app.memory writes provenance origins {sorted(missing)} that `memory.why` has no "
        "sentence for; it would answer 'nereden geldiğini kaydetmemişim' about a row whose "
        "origin is recorded"
    )


def test_a_use_receipt_is_readable_where_the_owner_asks_what_you_did(db):
    """req 62's other half: a receipt nobody can read is not a receipt.

    The claim being checked is narrow and worth stating exactly. `app.explain.engine`
    answers "bugün ne yaptın?" with `source.events(since=..., subsystems=None, ...)` - an
    unfiltered ledger read - so a `memory.used` row is in what that question reads, the
    same way every other subsystem's rows are. It is NOT a bespoke "which memory did you
    use" answer, and this test does not pretend it is: what it holds is that the receipt
    lands where the owner's existing question already looks, rather than in a table only
    this batch knows about.
    """
    from app.ledger import service as ledger_service

    ctx = _Ctx(db)
    _remember(ctx, "Kahveyi sade severim.")
    memory_search(_Ctx(db, call_id="search-2"), {"query": "kahve"})
    db.commit()

    events = ledger_service.query(db, subsystems=None, limit=100)

    used = [e for e in events if e.event_type == EVENT_TYPE_MEMORY_USED]
    assert len(used) == 1
    assert "sahibe okundu" in used[0].factual_summary


# ------------------------------------------------------- B18 req 49: "bunu" and "şunu"


def test_forgetting_resolves_the_memory_the_owner_was_just_read(db):
    """req 49. `memory.search` sets the durable object focus on what it speaks, so a bare
    "bunu unut" acts on the memory the owner actually heard - the same current/previous
    mechanism the window, document, message, draft, event, artifact, project, scene and
    creative families have had since M19, which requirement 49 was recorded as MISSING.

    This is not the fuzzy matcher B16 refused. Nothing is resolved from the sentence: the
    id comes from a record of what was said out loud.
    """
    ctx = _Ctx(db)
    answer = _remember(ctx, "Kahveyi sade severim.")
    memory_search(_Ctx(db, call_id="s1"), {"query": "kahve"})

    gone = memory_forget(_Ctx(db, call_id="f1"), {})

    assert gone["memory_id"] == answer["memory_id"]
    assert db.get(Memory, uuid.UUID(answer["memory_id"])) is None


def test_a_session_that_has_read_nothing_back_still_refuses(db):
    """The half that keeps the convenience from becoming the defect. With nothing spoken,
    there is nothing to point at, and a hard delete does not guess."""
    _remember(_Ctx(db), "Kahveyi sade severim.")

    with pytest.raises(VoiceError) as caught:
        memory_forget(_Ctx(db, call_id="f2"), {})

    assert "nothing has been read back" in caught.value.message
    assert db.execute(select(Memory)).scalars().all() != []


def test_a_named_id_always_wins_over_the_focus(db):
    """The owner naming the second of three is not pointing at the first."""
    first = _remember(_Ctx(db), "Kahveyi sade severim.")
    second = _remember(_Ctx(db, call_id="r2"), "Çayı demli severim.")
    memory_search(_Ctx(db, call_id="s2"), {"query": "kahve"})

    pinned = memory_pin(_Ctx(db, call_id="p1"), {"memory_id": second["memory_id"]})

    assert pinned["memory_id"] == second["memory_id"]
    assert db.get(Memory, uuid.UUID(first["memory_id"])).pinned is False


def test_the_focus_names_the_memory_that_was_spoken_first(db):
    """A bare deictic means the one at the top of what was just read out."""
    from app.operator import focus as focus_module
    from app.operator.models import FOCUS_KIND_MEMORY

    _remember(_Ctx(db), "Kahveyi sade severim.")
    answer = memory_search(_Ctx(db, call_id="s3"), {"query": "kahve"})

    entry = focus_module.current(db, FOCUS_KIND_MEMORY)
    assert entry is not None
    assert entry.object_id == answer["memories"][0]["memory_id"]
    assert entry.source == "memory_search"


def test_a_search_that_found_nothing_points_at_nothing(db):
    from app.operator import focus as focus_module
    from app.operator.models import FOCUS_KIND_MEMORY

    memory_search(_Ctx(db), {"query": "denizaltı"})

    assert focus_module.current(db, FOCUS_KIND_MEMORY) is None


# --------------------------------------------- B18 req 46/50: where and when it was taught


def test_a_taught_memory_records_the_conversation_that_taught_it(db):
    """req 50. `Memory.conversation_id` has been in the schema since M5 and nothing ever
    set it, so "you told me this while we were talking about X" was unanswerable."""
    ctx = _Ctx(db)

    answer = _remember(ctx, "Kahveyi sade severim.")

    memory = db.get(Memory, uuid.UUID(answer["memory_id"]))
    assert memory.conversation_id == ctx.session_id


def _session_started(db, ctx, *, at: datetime) -> None:
    """The realtime session row `_links` reads to decide whether a project focus belongs to
    THIS conversation."""
    from app.voice.realtime_sessions.models import REALTIME_STATE_ACTIVE, RealtimeSessionRow

    db.add(
        RealtimeSessionRow(
            id=ctx.session_id,
            provider="openai",
            transport="webrtc",
            client_kind="web",
            device_id=None,
            owner_session_id=ctx.owner_session_id,
            state=REALTIME_STATE_ACTIVE,
            expires_at=None,
            created_at=at,
        )
    )
    db.commit()


def test_a_memory_taught_while_a_project_is_open_carries_it(db):
    """req 46. `Memory.project_id` has been in the schema since M5, `RetrievalFilters` can
    scope by it and `hybrid_search` already scores a project match - and nothing filled it,
    so the join existed and nobody could make it."""
    from app.operator import focus as focus_module
    from app.operator.models import FOCUS_KIND_PROJECT

    ctx = _Ctx(db)
    _session_started(db, ctx, at=NOW - timedelta(minutes=10))
    project_id = uuid.uuid4()
    focus_module.set_focus(db, FOCUS_KIND_PROJECT, str(project_id), label="Görev takibi", now=NOW)

    answer = _remember(ctx, "Bu projede testleri önce yazıyoruz.")

    assert db.get(Memory, uuid.UUID(answer["memory_id"])).project_id == project_id


def test_a_project_opened_before_this_conversation_is_not_stamped_on_it(db):
    """The bound, and the reason it exists. `focus.current` has no staleness limit - a
    deictic word means the most recent thing however long ago - but a durable link stamped
    on every memory taught afterwards is a different question. A project opened in March
    must not be attached to a preference taught in June: a join that is always true is not
    a join."""
    from app.operator import focus as focus_module
    from app.operator.models import FOCUS_KIND_PROJECT

    focus_module.set_focus(db, FOCUS_KIND_PROJECT, str(uuid.uuid4()), now=NOW - timedelta(days=90))
    ctx = _Ctx(db)
    _session_started(db, ctx, at=NOW)

    answer = _remember(ctx, "Kahveyi sade severim.")

    assert db.get(Memory, uuid.UUID(answer["memory_id"])).project_id is None


def test_the_project_link_is_findable_again_in_a_later_session(db):
    """What "oturumlar arası sürer" actually means: a different session, and the link is
    still the way to find what was learned while that project was open."""
    from app.memory.retrieval import RetrievalFilters, structured_candidates
    from app.operator import focus as focus_module
    from app.operator.models import FOCUS_KIND_PROJECT

    ctx = _Ctx(db)
    _session_started(db, ctx, at=NOW - timedelta(minutes=10))
    project_id = uuid.uuid4()
    focus_module.set_focus(db, FOCUS_KIND_PROJECT, str(project_id), now=NOW)
    _remember(ctx, "Bu projede testleri önce yazıyoruz.")
    # A DIFFERENT conversation, with no session row and no project open in it.
    _remember(_Ctx(db, call_id="r2", session_id=uuid.uuid4()), "Kahveyi sade severim.")

    scoped = structured_candidates(db, RetrievalFilters(project_id=project_id))

    assert [m.text for m in scoped] == ["Bu projede testleri önce yazıyoruz."]


def test_no_project_open_is_no_link_rather_than_a_guess(db):
    answer = _remember(_Ctx(db), "Kahveyi sade severim.")

    assert db.get(Memory, uuid.UUID(answer["memory_id"])).project_id is None


# ------------------------------------------- ADR-0192: the local mode has no model either


def test_the_local_mode_remembers_what_the_owner_said_with_no_statement_argument(db):
    """Owner, 2026-09-21: "memory kısmını tüm modlara entegre et". In the local mode
    (ADR-0173) the router names `memory.remember` and the browser posts it with EMPTY
    arguments - no model writes a `statement` - and the tool refused on that, so "bunu
    hatırla: kahveyi şekersiz içiyorum" saved nothing at all. The owner's own sentence is on
    the turn record; the fact is read from there."""
    ctx = _Ctx(db)
    ctx.context["last_utterance"] = {
        "intent": "memory_remember",
        "memory_statement": "Kahveyi şekersiz içiyorum.",
    }

    answer = memory_remember(ctx, {})

    memory = db.get(Memory, uuid.UUID(answer["memory_id"]))
    assert memory is not None and memory.text == "Kahveyi şekersiz içiyorum."


def test_the_local_mode_searches_the_subject_the_owner_named(db):
    ctx = _Ctx(db)
    _remember(ctx, "Kahveyi sade severim.")
    _remember(ctx, "Pazartesi sabahları toplantım var.")
    ctx.context["last_utterance"] = {"intent": "memory_search", "memory_query": "kahve"}

    answer = memory_search(ctx, {})

    assert "Kahve" in answer["speech"] or "kahve" in answer["speech"], answer["speech"]


def test_a_remember_the_turn_did_not_ask_for_is_still_refused(db):
    """The fallback reads the TURN: an empty call with no remember sentence behind it has
    no owner's words to write, and writing nothing is the honest answer."""
    ctx = _Ctx(db)
    ctx.context["last_utterance"] = {"intent": "clock_query"}

    with pytest.raises(VoiceError):
        memory_remember(ctx, {})


@pytest.mark.parametrize(
    ("said", "fact"),
    [
        ("Bunu hatırla: kahveyi şekersiz içiyorum", "kahveyi şekersiz içiyorum"),
        ("Şunu hatırla, pazartesi toplantım var", "pazartesi toplantım var"),
        ("Kahveyi şekersiz içtiğimi hatırla", "Kahveyi şekersiz içtiğimi"),
        ("Aklında tut: annemin doğum günü 3 Mart", "annemin doğum günü 3 Mart"),
    ],
)
def test_the_fact_is_the_sentence_without_the_command(said: str, fact: str) -> None:
    from app.voice.intents import memory_statement_of

    assert memory_statement_of(said) == fact


@pytest.mark.parametrize(
    ("said", "query"),
    [
        ("Kahve hakkında ne biliyorsun", "Kahve"),
        ("Benim hakkımda ne biliyorsun", ""),
        ("Neyi hatırlıyorsun", ""),
    ],
)
def test_the_search_subject_is_what_is_left_of_the_question(said: str, query: str) -> None:
    """ "Benim hakkımda" is the owner, not a subject - an empty query is the structured
    "everything you know about me" answer, which is exactly what was asked."""
    from app.voice.intents import memory_query_of

    assert memory_query_of(said) == query
