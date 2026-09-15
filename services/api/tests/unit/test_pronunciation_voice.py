"""B21 req 228/229: a pronunciation the owner can teach by saying it, and that the
assistant then uses in its OWN speech.

Two defects, one subsystem. The dictionary has had a REST surface, a unique key, a
normaliser that applies it before every other rule — and zero rows in production, because
its only writer was a hand-made PUT (the matrix: "tek yazıcı manuel PUT"). And the table it
was filling only ever reached text the assistant READ OUT: the narration plan, and what a
tool handed back. Everything the assistant said in its own words went to the provider
having never seen it, so the owner could teach the system their surname and go on hearing
it mangled for the rest of the conversation. The matrix calls that one "asıl eksik".
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.narration import service as narration_service
from app.narration.models import NarrationSession, PronunciationEntry
from app.voice.errors import VoiceError
from app.voice.realtime_sessions.persona import (
    MAX_PRONUNCIATION_CHARS,
    MAX_PRONUNCIATION_RULES,
    build_instructions,
    pronunciation_block,
)
from app.voice.realtime_sessions.tools import ToolContext, default_registry
from app.voice.realtime_sessions.tools_pronunciation import (
    pronunciation_forget,
    pronunciation_list,
    pronunciation_teach,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (PronunciationEntry.__table__, NarrationSession.__table__):
        table.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def _ctx(db) -> ToolContext:
    return ToolContext(
        session_id=uuid.uuid4(),
        owner_session_id=uuid.uuid4(),
        device_id=None,
        client_kind="web",
        context={},
        db=db,
    )


# ------------------------------------------------------ req 228: teaching by voice


def test_the_owner_teaches_a_pronunciation_by_saying_it(db):
    result = pronunciation_teach(
        _ctx(db), {"written_form": "PagentOS", "spoken_form": "peycent os"}
    )

    assert result["status"] == "succeeded"
    # The confirmation says the rule BACK: the one thing "tamam efendim" cannot tell the
    # owner is whether the word was heard correctly.
    assert "PagentOS" in result["speech"] and "peycent os" in result["speech"]
    assert narration_service.pronunciation_map(db) == {"PagentOS": "peycent os"}


def test_teaching_the_same_word_twice_is_a_correction_not_a_second_rule(db):
    ctx = _ctx(db)
    pronunciation_teach(ctx, {"written_form": "Akbaba", "spoken_form": "ak baba"})
    pronunciation_teach(ctx, {"written_form": "Akbaba", "spoken_form": "ak-ba-ba"})

    rules = narration_service.pronunciation_map(db)
    assert rules == {"Akbaba": "ak-ba-ba"}


def test_an_empty_or_oversized_rule_is_refused_rather_than_stored(db):
    ctx = _ctx(db)
    with pytest.raises(VoiceError):
        pronunciation_teach(ctx, {"written_form": "  ", "spoken_form": "bir şey"})
    with pytest.raises(VoiceError):
        pronunciation_teach(ctx, {"written_form": "x" * 200, "spoken_form": "bir şey"})
    assert narration_service.pronunciation_map(db) == {}


def test_the_list_reads_the_rules_back_and_forget_takes_an_id(db):
    ctx = _ctx(db)
    taught = pronunciation_teach(ctx, {"written_form": "K66", "spoken_form": "ka altmış altı"})

    listed = pronunciation_list(ctx, {})
    assert listed["count"] == 1
    assert "K66" in listed["speech"] and "ka altmış altı" in listed["speech"]
    assert listed["entries"][0]["id"] == taught["entry_id"]

    # A deletion resolved from a half-heard word deletes the wrong row: an id, or nothing.
    with pytest.raises(VoiceError):
        pronunciation_forget(ctx, {"entry_id": "K66"})
    gone = pronunciation_forget(ctx, {"entry_id": taught["entry_id"]})
    assert gone["status"] == "succeeded"
    assert narration_service.pronunciation_map(db) == {}


def test_forgetting_a_rule_that_is_not_there_says_so_rather_than_pretending(db):
    result = pronunciation_forget(_ctx(db), {"entry_id": str(uuid.uuid4())})
    assert result["status"] == "failed"
    assert "bulamadım" in result["speech"]


def test_an_empty_table_says_it_is_empty(db):
    assert pronunciation_list(_ctx(db), {})["speech"] == "Kayıtlı telaffuz kuralın yok efendim."


def test_the_three_tools_are_registered_and_governed():
    from app.security import step_up

    names = set(default_registry().names())
    assert {"pronunciation.teach", "pronunciation.list", "pronunciation.forget"} <= names
    # Writes are governed; reading the table back is not a write.
    assert step_up.tier_of("pronunciation.teach") == step_up.TIER_SENSITIVE
    assert step_up.tier_of("pronunciation.forget") == step_up.TIER_SENSITIVE
    assert step_up.tier_of("pronunciation.list") == step_up.TIER_OPEN


def test_no_argument_name_is_one_the_relay_refuses():
    """The guard that caught this on the way in, kept as a statement of the reason.

    `FORBIDDEN_KEY_PARTS` refuses credential- and transcript-shaped argument keys by
    substring, so `token` (credential-shaped) and `context` (it CONTAINS "text") never
    reach a handler. A tool whose required argument is refused by the relay is a tool that
    cannot be called at all — and it would have looked perfectly correct in review.
    """
    from app.voice.realtime_sessions.service import is_forbidden_key

    specs = {spec["name"]: spec for spec in default_registry().manifest()}
    for name in ("pronunciation.teach", "pronunciation.list", "pronunciation.forget"):
        properties = (specs[name].get("parameters") or {}).get("properties") or {}
        assert [key for key in properties if is_forbidden_key(key)] == [], name


# --------------------------------------- req 229: the assistant's own speech obeys it


def test_a_taught_rule_reaches_the_assistants_own_instruction(db):
    pronunciation_teach(_ctx(db), {"written_form": "Akbaba", "spoken_form": "ak-ba-ba"})

    from app.voice.realtime_sessions.service import _pronunciation_rules

    instructions = build_instructions(pronunciation=_pronunciation_rules(db))

    assert "Telaffuz:" in instructions
    assert "Akbaba → ak-ba-ba" in instructions
    # And it says WHEN it applies, because the model is otherwise free to read it as a
    # spelling instruction and write "ak-ba-ba" in a transcript.
    assert "seslendirirken" in instructions


def test_no_rules_means_no_block_rather_than_an_empty_heading(db):
    from app.voice.realtime_sessions.service import _pronunciation_rules

    assert pronunciation_block({}) == ""
    assert pronunciation_block(None) == ""
    assert "Telaffuz:" not in build_instructions(pronunciation=_pronunciation_rules(db))


def test_the_block_is_bounded_by_rules_and_by_characters():
    many = {f"Kelime{i}": f"okunuş {i}" for i in range(MAX_PRONUNCIATION_RULES * 3)}
    block = pronunciation_block(many)

    assert block.count("→") <= MAX_PRONUNCIATION_RULES
    assert len(block) < MAX_PRONUNCIATION_CHARS + 400  # heading + rules, nothing unbounded

    long_forms = {f"Kelime{i}": "a" * 100 for i in range(MAX_PRONUNCIATION_RULES)}
    assert len(pronunciation_block(long_forms)) < MAX_PRONUNCIATION_CHARS + 400


def test_a_broken_rule_does_not_take_the_block_down():
    block = pronunciation_block({"": "boş", "Ada": "", "Gerçek": "ger-çek"})
    assert "Gerçek → ger-çek" in block
    assert "→ \n" not in block


def test_reading_the_table_never_breaks_a_session(db):
    """`_pronunciation_rules` is called while minting a credential. A voice session that
    failed because a pronunciation row could not be read would be the feature making things
    worse than not having it."""
    from app.voice.realtime_sessions.service import _pronunciation_rules

    class _Broken:
        def query(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
            raise RuntimeError("database is on fire")

        def rollback(self) -> None:
            pass

    assert _pronunciation_rules(_Broken()) == {}
