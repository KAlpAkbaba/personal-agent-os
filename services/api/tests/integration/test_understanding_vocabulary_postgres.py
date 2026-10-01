"""ADR-0224 corrections on the REAL database: a vocabulary row through ``learn()``.

The inspector, 2026-10-01: on PostgreSQL ``learn()`` answered ``write_failed`` -
``ck_memories_class`` (migration 0005) names six classes and ``vocabulary`` is a seventh.
Every unit test ran on SQLite, which builds the tables from the ORM models and has no such
constraint, so 34 green tests stood beside a feature that could not write one row. These
tests take the same calls to the dev stack's PostgreSQL at migration 0064, and one of them
goes back to 0063 to show the refusal it replaces.

Every run teaches its own word (a random suffix), so reruns and other rows never collide.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

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
from app.voice.realtime_sessions.tools_operator import names_unbound_machine
from app.voice.understanding import corrections, fuzzy
from app.voice.understanding import policy as understanding_policy

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

        command.downgrade(cfg, "0063_team_state")
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
