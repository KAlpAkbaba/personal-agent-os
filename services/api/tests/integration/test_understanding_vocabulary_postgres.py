"""ADR-0224 corrections on the REAL database: a vocabulary row through ``learn()``.

The inspector, 2026-10-01: on PostgreSQL ``learn()`` answered ``write_failed`` -
``ck_memories_class`` (migration 0005) names six classes and ``vocabulary`` is a seventh.
Every unit test ran on SQLite, which builds the tables from the ORM models and has no such
constraint, so 34 green tests stood beside a feature that could not write one row. These
tests take the same calls to the dev stack's PostgreSQL at migration 0064, and one of them
goes back to 0063 to show the refusal it replaces.

Every run teaches its own word (a random suffix), so reruns and other rows never collide.
The relay tests at the bottom say the sentences to the real application on the same
database; their words are fixed (a mishearing must resemble its alias) and removed around
each test.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.config import Settings
from app.devices.aliases import extract_aliases
from app.memory import service
from app.memory.embedding import DeterministicEmbedder
from app.memory.models import Memory
from app.memory.runtime import MemoryRuntime
from app.memory.types import Actor, MemoryClass, MemoryStatus, WriteStage
from app.voice.intents import resolve_intent
from app.voice.realtime_sessions.models import RealtimeSessionRow
from app.voice.realtime_sessions.tools_operator import names_unbound_machine
from app.voice.understanding import corrections, fuzzy
from app.voice.understanding import policy as understanding_policy
from tests.integration.migration_ids import parent_of, revision_named

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]
EMBEDDER = DeterministicEmbedder()
ALIASES = ("ev", "ofis", "iş")


def _key(heard: str) -> str:
    """The memory key of a taught device word: one row per heard word."""
    return f"device:{fuzzy.fold(heard)}"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings()


@pytest.fixture()
def heard(settings: Settings) -> Iterator[str]:
    """A word nobody taught before, and nothing of it left behind (history rows included)."""
    word = "ofüs" + "".join(chr(ord("a") + int(c, 16)) for c in uuid.uuid4().hex[:8])
    corrections.reset()
    try:
        yield word
    finally:
        corrections.reset()
        with MemoryRuntime(settings).session() as session:
            leftovers = session.execute(select(Memory.id).where(Memory.key == _key(word))).scalars()
            for memory_id in list(leftovers):
                service.forget_memory(session, memory_id, actor=Actor.OWNER)


def _alembic() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


def _correction(heard: str, meant: str) -> corrections.Correction:
    return corrections.Correction(kind="device", heard=heard, meant=meant, intent="app_open")


def _mine(session: Session, heard: str) -> list[corrections.Synonym]:
    """This run's synonym as the rows hold it NOW (the vocabulary may hold others' too)."""
    return [s for s in corrections.vocabulary(session) if s.heard == heard]


def _read(session: Session, sentence: str) -> understanding_policy.Decision:
    vocabulary = corrections.vocabulary(session)
    intent = resolve_intent(sentence)
    bound = extract_aliases(sentence)
    return corrections.read_turn(
        sentence,
        rule=understanding_policy.rule_reading(
            intent.intent.value, application=intent.application, route_repair=intent.route_repair
        ),
        vocabulary=vocabulary,
        bound_devices=bound,
        names_machine=names_unbound_machine(sentence, bound),
        aliases=ALIASES,
    )


def _rows(session: Session, heard: str) -> list[Memory]:
    return list(
        session.execute(
            select(Memory).where(Memory.key == _key(heard)).order_by(Memory.created_at)
        ).scalars()
    )


def test_a_vocabulary_row_is_written_reloaded_superseded_and_forgotten(
    settings: Settings, heard: str, tmp_path: Path
) -> None:
    sentence = f"{heard.capitalize()} bilgisayarında hesap makinesini aç."

    # Written: explicit, durable, class vocabulary - and the constraint let it in.
    with MemoryRuntime(settings).session() as session:
        assert _read(session, sentence).band == "low"
        learned = corrections.learn(
            session, EMBEDDER, _correction(heard, "ofis"), session_id="pg-1", proposals_dir=tmp_path
        )
        assert learned.written and learned.action == "created", learned
        assert learned.proposal_written and learned.proposal is not None
        row = session.get(Memory, learned.memory_id)
        assert row is not None and row.memory_class == MemoryClass.VOCABULARY.value
        assert (row.stage, row.explicit, row.status) == (
            WriteStage.DURABLE.value,
            True,
            MemoryStatus.ACTIVE.value,
        )
        assert row.text == f"{heard} = ofis (cihaz)"
        assert row.value_json == {"kind": "device", "heard": heard, "meant": "ofis"}
    first_id = learned.memory_id

    # Reloaded: a fresh runtime, a fresh session and a process that has read nothing.
    corrections.reset()
    with MemoryRuntime(settings).session() as session:
        assert [(s.meant, s.memory_id) for s in _mine(session, heard)] == [("ofis", first_id)]
        decision = _read(session, sentence)
        assert decision.band == "high" and decision.device.alias == "ofis"
        assert decision.layer == corrections.LAYER_VOCABULARY
        loads = corrections.loads()
        assert _read(session, sentence).band == "high"
        assert corrections.loads() == loads  # the second sentence was a version check only

        # Taught again: the same row, corroborated; the proposal is not written twice.
        again = corrections.learn(
            session, EMBEDDER, _correction(heard, "ofis"), session_id="pg-2", proposals_dir=tmp_path
        )
        assert again.written and again.action == "corroborated" and again.memory_id == first_id
        assert again.proposal_written is False and len(list(tmp_path.iterdir())) == 1

        # Superseded: the owner changed their mind; the old row stays as history only.
        changed = corrections.learn(
            session, EMBEDDER, _correction(heard, "ev"), session_id="pg-3", proposals_dir=tmp_path
        )
        assert changed.written and changed.memory_id != first_id, changed
        assert [(s.meant, s.memory_id) for s in _mine(session, heard)] == [
            ("ev", changed.memory_id)
        ]
        assert _read(session, sentence).device.alias == "ev"

    with MemoryRuntime(settings).session() as session:
        statuses = {row.id: row.status for row in _rows(session, heard)}
        assert statuses == {
            first_id: MemoryStatus.SUPERSEDED.value,
            changed.memory_id: MemoryStatus.ACTIVE.value,
        }
        # ... and the memory service's own supersede (an owner edit) reads the class back.
        edited = service.supersede_memory(
            session,
            EMBEDDER,
            changed.memory_id,
            actor=Actor.OWNER,
            text=f"{heard} = iş (cihaz)",
            value={"kind": "device", "heard": heard, "meant": "iş"},
        )
        assert edited.memory_class == MemoryClass.VOCABULARY.value
        assert [(s.meant, s.memory_id) for s in _mine(session, heard)] == [("iş", edited.id)]
        assert _read(session, sentence).device.alias == "iş"

        # Forgotten: the row the sentence names by its heard word, its vector with it.
        named = corrections.named_synonym(corrections.vocabulary(session), f"{heard}'ü unut")
        assert named is not None and named.memory_id == edited.id
        service.forget_memory(session, named.memory_id, actor=Actor.OWNER)

    corrections.reset()
    with MemoryRuntime(settings).session() as session:
        assert _mine(session, heard) == []
        after = _read(session, sentence)
        assert after.band == "low" and after.device is None
        vectors = session.execute(
            sql_text("SELECT count(*) FROM memory_embeddings WHERE memory_id = :m"),
            {"m": str(edited.id)},
        ).scalar_one()
        assert vectors == 0


def test_migration_0064_is_what_lets_the_row_in_and_its_downgrade_takes_it_out(
    settings: Settings, heard: str, tmp_path: Path
) -> None:
    """At 0063 the database refuses the class and ``learn`` says so without raising and
    without breaking the session - what production would have done with the relay wired
    and no migration. 0064 is the difference; its downgrade removes the rows it allowed."""
    cfg = _alembic()
    command.upgrade(cfg, "head")
    try:
        with MemoryRuntime(settings).session() as session:
            assert corrections.learn(
                session, EMBEDDER, _correction(heard, "ofis"), session_id="pg", proposals_dir=None
            ).written

        command.downgrade(cfg, parent_of(revision_named("memory_vocabulary_class")))
        with MemoryRuntime(settings).session() as session:
            assert _rows(session, heard) == []
            refused = corrections.learn(
                session,
                EMBEDDER,
                _correction(heard, "ofis"),
                session_id="pg",
                proposals_dir=tmp_path,
            )
            assert (refused.written, refused.reason) == (False, "write_failed")
            assert list(tmp_path.iterdir()) == []  # nothing is proposed that was not written
            assert session.execute(sql_text("SELECT 1")).scalar_one() == 1  # still usable
            assert _rows(session, heard) == []
    finally:
        command.upgrade(cfg, "head")

    corrections.reset()
    with MemoryRuntime(settings).session() as session:
        learned = corrections.learn(
            session, EMBEDDER, _correction(heard, "ofis"), session_id="pg", proposals_dir=tmp_path
        )
        assert learned.written and learned.action == "created"
        assert [s.meant for s in _mine(session, heard)] == ["ofis"]


# --- through the real relay, on PostgreSQL ---------------------------------------------------
#
# The inspector, 2026-10-02: the relay tests ran on PostgreSQL only from a scratch harness.
# What the real database adds to them: the session's ``context_json`` is JSONB (what the
# relay keeps of a turn - ``understanding_correctable`` - must survive the round trip between
# two requests), the vocabulary row is written on the relay's own session inside the request,
# and the application the owner builds (``create_app``) is what registered the memory runtime.

#: The words these tests teach through the relay. A heard word must RESEMBLE the alias it is
#: corrected to, so it cannot be random: the rows are removed before and after instead.
RELAY_KEYS = ("device:ofus", "app:hesaplayici", "app:dosya")
RELAY_MISHEARD = "Ofüs bilgisayarında hesap makinesini aç."
RELAY_SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"


def _forget_relay_words(settings: Settings) -> None:
    with MemoryRuntime(settings).session() as session:
        leftovers = session.execute(select(Memory.id).where(Memory.key.in_(RELAY_KEYS))).scalars()
        for memory_id in list(leftovers):
            service.forget_memory(session, memory_id, actor=Actor.OWNER)


@pytest.fixture()
def relay(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from tests.integration.conftest import owner_client

    (tmp_path / "proposals").mkdir()
    monkeypatch.setenv(corrections.PROPOSALS_DIR_ENV, str(tmp_path / "proposals"))
    corrections.reset()
    _forget_relay_words(settings)
    try:
        yield owner_client(settings)
    finally:
        corrections.reset()
        _forget_relay_words(settings)


def _session(client: Any) -> str:
    created = client.post("/v1/voice/realtime/sessions", json={"client_kind": "cli"})
    assert created.status_code == 201, created.text
    return created.json()["session_id"]


def _say(client: Any, sid: str, sentence: str) -> dict[str, Any]:
    response = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 1000, "turn": 1, "text": sentence}]},
    )
    assert response.status_code == 200, response.text
    return response.json()["resolved_intents"][0]


def _context(settings: Settings, sid: str) -> dict[str, Any]:
    """The session's JSONB context, read back through a FRESH session."""
    with MemoryRuntime(settings).session() as session:
        row = session.get(RealtimeSessionRow, uuid.UUID(sid))
        assert row is not None
        return dict(row.context_json or {})


def _relay_rows(settings: Settings) -> dict[str, str]:
    with MemoryRuntime(settings).session() as session:
        rows = session.execute(
            select(Memory).where(
                Memory.key.in_(RELAY_KEYS), Memory.status == MemoryStatus.ACTIVE.value
            )
        ).scalars()
        return {row.key: row.text for row in rows}


def test_relay_on_postgres_the_answer_teaches_the_word_and_the_second_time_is_right(
    settings: Settings, relay: Any, tmp_path: Path
) -> None:
    sid = _session(relay)
    first = _say(relay, sid, RELAY_MISHEARD)
    assert (first["intent"], first["band"]) == ("app_open", "low")
    kept = _context(settings, sid)[corrections.CORRECTABLE_KEY]  # JSONB kept it ...
    assert kept["heard_device"] == "ofüs" and kept["band"] == "low"
    call = relay.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={
            "call_id": f"pg-{uuid.uuid4()}",
            "name": "operator.app_open",
            "arguments": {"application": "Hesap Makinesi"},
        },
    )
    assert call.status_code == 200, call.text
    assert call.json()["result"]["speech"].startswith("Hangi bilgisayarda"), call.json()

    answered = _say(relay, sid, "Ofis bilgisayarında.")  # ... for the next request to read
    assert (answered["intent"], answered["band"]) == ("app_open", "high")
    assert _relay_rows(settings) == {"device:ofus": "ofüs = ofis (cihaz)"}
    with MemoryRuntime(settings).session() as session:
        row = session.execute(
            select(Memory).where(
                Memory.key == "device:ofus", Memory.status == MemoryStatus.ACTIVE.value
            )
        ).scalar_one()
        assert row.memory_class == MemoryClass.VOCABULARY.value
        assert (row.stage, row.explicit) == (WriteStage.DURABLE.value, True)
        assert row.provenance_json["source"] == {"kind": "voice_correction", "session_id": sid}
    assert len(list((tmp_path / "proposals").iterdir())) == 1
    assert corrections.CORRECTABLE_KEY not in _context(settings, sid)  # corrected once

    # Another session, and a process that has read nothing: the word is the owner's.
    corrections.reset()
    sid2 = _session(relay)
    second = _say(relay, sid2, RELAY_MISHEARD)
    assert (second["intent"], second["band"], second["confidence"]) == ("app_open", "high", 1.0)
    record = _context(settings, sid2)["last_utterance"]
    assert record["device_targets"] == ["ofis"]
    assert record["understanding"]["layer"] == "vocabulary"
    assert len(list((tmp_path / "proposals").iterdir())) == 1  # once per synonym

    # Forgotten: the same sentence asks again.
    with MemoryRuntime(settings).session() as session:
        named = corrections.named_synonym(corrections.vocabulary(session), "ofüs'ü unut")
        assert named is not None and named.heard == "ofüs"
        service.forget_memory(session, named.memory_id, actor=Actor.OWNER)
    assert _say(relay, sid2, RELAY_MISHEARD)["band"] == "low"


def test_relay_on_postgres_an_application_word_fills_a_gap_and_takes_no_owned_sentence(
    settings: Settings, relay: Any, tmp_path: Path
) -> None:
    sid = _session(relay)
    owned = ("Dosyayı aç.", "Dosyayı aç ve oku.", "Müzik aç.", "Şarkıyı aç.")
    before = {sentence: _say(relay, sid, sentence)["intent"] for sentence in owned}
    assert "app_open" not in before.values() and "none" not in before.values(), before

    # The lesson for a word a table owns is refused; a secret in the sentence is refused as
    # it was spoken; a word no table owns is learned.
    assert _say(relay, sid, "Ona not defteri deme, dosya de.")["intent"] == "none"
    assert _say(relay, sid, f"Ona chrome deme, {RELAY_SECRET} de.")["intent"] == "none"
    assert _relay_rows(settings) == {}
    with MemoryRuntime(settings).session() as session:
        leaked = session.execute(
            sql_text("SELECT count(*) FROM memories WHERE text LIKE '%abcdefghijklmnopqrstuvwxyz%'")
        ).scalar_one()
        assert leaked == 0
    assert list((tmp_path / "proposals").iterdir()) == []
    assert _say(relay, sid, "Ona hesap makinesi deme, hesaplayıcı de.")["intent"] == "none"
    assert _relay_rows(settings) == {"app:hesaplayici": "hesaplayıcı = Hesap Makinesi (uygulama)"}

    # Whatever the rows hold (this one is planted, as an older release would have left it),
    # the four sentences keep their tables ...
    with MemoryRuntime(settings).session() as session:
        assert corrections.learn(
            session,
            EMBEDDER,
            corrections.Correction(kind="app", heard="dosya", meant="notepad"),
            session_id=sid,
        ).written
    assert {sentence: _say(relay, sid, sentence)["intent"] for sentence in owned} == before

    # ... and the taught word opens its application, recorded as the vocabulary's turn.
    for sentence in ("Hesaplayıcıyı aç.", "Ofis bilgisayarında hesaplayıcıyı aç."):
        said = _say(relay, sid, sentence)
        assert (said["intent"], said["band"], said["confidence"]) == ("app_open", "high", 1.0)
        record = _context(settings, sid)["last_utterance"]
        assert record["understanding"]["layer"] == "vocabulary", sentence
    assert record["device_targets"] == ["ofis"]
    _say(relay, sid, "Hesap makinesini aç.")
    assert _context(settings, sid)["last_utterance"]["understanding"]["layer"] == "rule"
