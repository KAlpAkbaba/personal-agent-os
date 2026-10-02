"""ADR-0224 corrections: the owner's correction becomes vocabulary, and the next match uses it.

Nothing here builds a memory row by hand: the pair goes through ``app.memory.service`` (the
real write policy, the real tables on SQLite), the vocabulary is read back from those rows,
and the decision is the real ``policy.read_turn`` behind ``corrections.read_turn``. The two
relay tests at the bottom say the sentences to the real relay (``record_client_events``).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.memory import policy as memory_policy
from app.memory import service as memory_service
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
from app.memory.policy import ACTION_IGNORE, Observation, decide
from app.memory.types import Actor, MemoryClass, WriteStage
from app.voice.intents import Intent, owned_by_a_table, resolve_intent
from app.voice.understanding import corrections, normalize
from app.voice.understanding import policy as understanding_policy
from app.voice.understanding.semantic import SemanticEngine

MEMORY_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]
EMBEDDER = DeterministicEmbedder()
ALIASES = ("ev", "ofis", "iş")
NOW = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)

#: A form the confusion list does not hold: "ofisü" is already in ``stt-confusions.json``.
MISHEARD = "Ofüs bilgisayarında hesap makinesini aç."
QUESTION = "Hangi bilgisayarda: ev mi, ofis mi, iş mi?"


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    for table in MEMORY_TABLES:
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _no_vocabulary_leaks():
    corrections.reset()
    yield
    corrections.reset()


def _read(db: Session, text: str, *, engine: SemanticEngine | None = None, **kwargs: Any):
    """One sentence the way the relay reads it: the rule tables, then the layers with the
    vocabulary the memory rows hold NOW."""
    vocabulary = corrections.vocabulary(db)
    intent = resolve_intent(text)
    from app.devices.aliases import extract_aliases
    from app.voice.realtime_sessions.tools_operator import names_unbound_machine

    bound = extract_aliases(text)
    decision = corrections.read_turn(
        text,
        rule=understanding_policy.rule_reading(
            intent.intent.value, application=intent.application, route_repair=intent.route_repair
        ),
        vocabulary=vocabulary,
        bound_devices=bound,
        names_machine=names_unbound_machine(text, bound),
        aliases=ALIASES,
        engine=engine,
        **kwargs,
    )
    return intent, decision


def _kept(text: str, intent: Any, decision: Any, *, now: datetime = NOW) -> dict[str, Any] | None:
    return corrections.correctable(
        text,
        intent=intent.intent.value,
        application=intent.application,
        decision=decision,
        now=now,
    )


def _ask_and_answer(db: Session, tmp_path: Path, answer: str = "ofis bilgisayarında"):
    intent, first = _read(db, MISHEARD)
    assert first.band == "low" and first.question == QUESTION and not first.acts
    kept = _kept(MISHEARD, intent, first)
    correction = corrections.correction_turn(kept, answer, now=NOW + timedelta(seconds=5))
    assert correction is not None, "the answer to the question is a correction turn"
    learned = corrections.learn(
        db, EMBEDDER, correction, session_id="sess-1", proposals_dir=tmp_path
    )
    return correction, learned


def _vocabulary_rows(db: Session) -> list[Memory]:
    return list(
        db.execute(select(Memory).where(Memory.memory_class == "vocabulary")).scalars().all()
    )


# --- the acceptance: LOW question -> answer -> a vocabulary row -> HIGH next time -------------


def test_the_answer_to_a_low_question_is_written_and_the_same_sentence_is_high_next_time(
    db, tmp_path
) -> None:
    """Red before: no vocabulary class, no corrections module; the second call was LOW."""
    correction, learned = _ask_and_answer(db, tmp_path)
    assert (correction.kind, correction.heard, correction.meant) == ("device", "ofüs", "ofis")
    assert learned.written, learned

    rows = _vocabulary_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row.text == "ofüs = ofis (cihaz)"
    assert row.value_json == {"kind": "device", "heard": "ofüs", "meant": "ofis"}
    assert row.provenance_json["source"] == {"kind": "voice_correction", "session_id": "sess-1"}

    intent, second = _read(db, MISHEARD)
    assert intent.intent is Intent.APP_OPEN and intent.application == "calc"
    assert second.band == "high" and second.confidence == 1.0 and second.acts
    assert second.question is None
    assert second.device is not None and second.device.alias == "ofis"
    assert second.layer == corrections.LAYER_VOCABULARY
    assert any("ofüs = ofis" in line for line in second.candidate.evidence), second.candidate


@pytest.mark.parametrize("answer", ["Ofis.", "hayır, ofis bilgisayarında", "Hayır, ofis."])
def test_every_spoken_form_of_the_answer_is_the_same_correction(db, tmp_path, answer) -> None:
    correction, learned = _ask_and_answer(db, tmp_path, answer)
    assert (correction.heard, correction.meant) == ("ofüs", "ofis")
    assert learned.written
    # ... and the turn is re-issued on the machine the owner named, never on the session's.
    assert (correction.intent, correction.application, correction.device) == (
        "app_open",
        "calc",
        "ofis",
    )


# --- a correction is explicit, owner-sourced, never a candidate ----------------------------------


def test_a_correction_is_explicit_and_durable_never_a_candidate(db, tmp_path) -> None:
    _ask_and_answer(db, tmp_path)
    row = _vocabulary_rows(db)[0]
    assert (row.stage, row.explicit, row.confidence) == (WriteStage.DURABLE.value, True, 1.0)
    assert row.provenance_json["origin"] == "owner_statement"
    audit = db.execute(
        select(MemoryAuditEvent).where(MemoryAuditEvent.action == "created")
    ).scalar_one()
    assert audit.actor == Actor.OWNER.value


def test_the_write_policy_refuses_to_infer_a_vocabulary_row(db) -> None:
    """The class has no ladder: without the owner's flag there is no row at all - not a
    candidate that evidence could later promote."""
    inferred = Observation(
        text="her zaman ofüs = ofis (cihaz)",  # a strong-signal phrase AND a stable key
        memory_class=memory_policy.VOCABULARY,
        key="device:ofus",
        value={"kind": "device", "heard": "ofüs", "meant": "ofis"},
    )
    assert decide(inferred).action == ACTION_IGNORE
    result = memory_service.record_observation(db, EMBEDDER, inferred)
    assert result.action == "ignored" and _vocabulary_rows(db) == []
    assert corrections.vocabulary(db) == ()

    explicit = decide(
        Observation(
            text="ofüs = ofis (cihaz)", memory_class=memory_policy.VOCABULARY, explicit=True
        )
    )
    assert (explicit.action, explicit.actor, explicit.explicit, explicit.confidence) == (
        WriteStage.DURABLE,
        Actor.OWNER,
        True,
        1.0,
    )
    assert memory_policy.VOCABULARY.value == "vocabulary"
    assert "vocabulary" in (memory_policy.__doc__ or "")
    # The other classes keep their ladder.
    assert decide(Observation(text="her zaman metrik birim kullan", key="units")).action == (
        WriteStage.CANDIDATE
    )
    assert decide(
        Observation(text="metrik", memory_class=MemoryClass.PREFERENCE, explicit=True)
    ).action == (WriteStage.DURABLE)


def test_a_secret_shaped_word_is_refused_and_learning_never_raises(db, tmp_path) -> None:
    correction = corrections.Correction(
        kind="device", heard="password=hunter2hunter2", meant="ofis", intent="app_open"
    )
    learned = corrections.learn(db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path)
    assert not learned.written and learned.reason == "secret_rejected"
    assert _vocabulary_rows(db) == [] and list(tmp_path.iterdir()) == []


# --- deletion removes the synonym -----------------------------------------------------------


def test_forgetting_the_vocabulary_memory_removes_the_synonym(db, tmp_path) -> None:
    _, learned = _ask_and_answer(db, tmp_path)
    assert _read(db, MISHEARD)[1].band == "high"
    engine = SemanticEngine(EMBEDDER, [("app_open", "hesap makinesini aç")])
    rows = corrections.entity_rows(corrections.vocabulary(db))
    assert rows == (("device", "ofis", "ofüs"),)
    assert engine.entity_index({}, rows).match("ofüs bilgisayarında")["device"].value == "ofis"

    # "ofisü'yü unut" is the memory family's FORGET (the rule tables, unchanged) ...
    assert resolve_intent("ofüs'ü unut").intent is Intent.MEMORY_FORGET
    # ... and the vocabulary row a forget sentence names is found by its heard word.
    named = corrections.named_synonym(corrections.vocabulary(db), "ofüs'ü unut")
    assert named is not None and named.memory_id == learned.memory_id
    memory_service.forget_memory(db, named.memory_id, actor=Actor.OWNER)

    assert corrections.vocabulary(db) == ()
    assert corrections.entity_rows(corrections.vocabulary(db)) == ()
    assert "device" not in engine.entity_index({}, ()).match("ofüs bilgisayarında")
    _, after = _read(db, MISHEARD)
    assert after.band == "low" and after.question == QUESTION and after.device is None


def test_the_owner_changing_their_mind_replaces_the_synonym(db, tmp_path) -> None:
    _ask_and_answer(db, tmp_path)
    _ask_and_answer_again = corrections.learn(
        db,
        EMBEDDER,
        corrections.Correction(kind="device", heard="ofüs", meant="ev", intent="app_open"),
        session_id="sess-2",
        proposals_dir=tmp_path,
    )
    assert _ask_and_answer_again.written
    active = corrections.vocabulary(db)
    assert [(s.heard, s.meant) for s in active] == [("ofüs", "ev")]
    assert _read(db, MISHEARD)[1].device.alias == "ev"


# --- the reload is a version check, not a rebuild per sentence ------------------------------


def test_the_vocabulary_is_reloaded_only_when_its_rows_changed(db, tmp_path) -> None:
    engine = SemanticEngine(EMBEDDER, [("app_open", "hesap makinesini aç")])
    _ask_and_answer(db, tmp_path)
    before = corrections.loads()
    for _ in range(5):
        assert _read(db, MISHEARD, engine=engine)[1].band == "high"
    assert corrections.loads() == before + 1  # one load for the new row, then the check only
    builds = engine.entity_builds
    for _ in range(3):
        _read(db, "Ofüsü bilgisayarında hesap makinesini aç.", engine=engine)
    assert engine.entity_builds <= builds + 1  # the index is built once for this vocabulary
    assert corrections.loads() == before + 1


def test_a_near_form_of_a_synonym_reaches_the_entity_index_and_is_read_back(db, tmp_path) -> None:
    """The exact word the owner corrected is the owner's own (HIGH); a NEAR form of it is
    layer 2's business - bound through the index's vocabulary rows and read back."""
    engine = SemanticEngine(EMBEDDER, [("app_open", "hesap makinesini aç")])
    near = "Ofüss bilgisayarında hesap makinesini aç."  # not "ofüs" plus a case ending
    assert _read(db, near, engine=engine)[1].band == "low"
    _ask_and_answer(db, tmp_path)
    _, decision = _read(db, near, engine=engine)
    assert decision.band == "medium" and decision.device.alias == "ofis"
    assert decision.layer == understanding_policy.LAYER_SEMANTIC


# --- the proposal: once per synonym, and never the protocol file ----------------------------


def test_the_proposal_is_written_once_per_synonym_and_the_confusion_list_is_untouched(
    db, tmp_path
) -> None:
    confusions = Path(normalize.__file__).with_name(normalize.CONFUSIONS_FILE)
    before = hashlib.sha256(confusions.read_bytes()).hexdigest()
    correction, first = _ask_and_answer(db, tmp_path)
    assert first.proposal is not None and first.proposal.parent == tmp_path
    body = first.proposal.read_text(encoding="utf-8")
    assert "ofüs" in body and "ofis" in body and "stt-confusions.json" in body
    stamp = first.proposal.stat().st_mtime_ns

    again = corrections.learn(db, EMBEDDER, correction, session_id="sess-9", proposals_dir=tmp_path)
    assert again.written and again.action == "corroborated"
    assert again.proposal == first.proposal and again.proposal_written is False
    assert [p.name for p in tmp_path.iterdir()] == [first.proposal.name]
    assert first.proposal.stat().st_mtime_ns == stamp
    assert len(_vocabulary_rows(db)) == 1

    other = corrections.Correction(kind="device", heard="evü", meant="ev", intent="app_open")
    second = corrections.learn(db, EMBEDDER, other, session_id="sess-9", proposals_dir=tmp_path)
    assert second.proposal_written and len(list(tmp_path.iterdir())) == 2
    assert hashlib.sha256(confusions.read_bytes()).hexdigest() == before


def test_no_proposals_directory_is_said_not_hidden(db) -> None:
    correction = corrections.Correction(
        kind="device", heard="ofüs", meant="ofis", intent="app_open"
    )
    learned = corrections.learn(db, EMBEDDER, correction, session_id="s", proposals_dir=None)
    assert learned.written and learned.proposal is None
    assert learned.proposal_reason == "no_proposals_dir"


# --- what a correction turn is, and what it is not ------------------------------------------


def test_a_no_after_a_read_back_reissues_the_turn(db, tmp_path) -> None:
    heard = "Ofisü bilgisayarında hesap makinesini açın."  # the confusion list: MEDIUM, ofis
    intent, decision = _read(db, heard)
    assert decision.band == "medium" and decision.device.alias == "ofis"
    kept = _kept(heard, intent, decision)
    assert corrections.correction_turn(kept, "ev bilgisayarında", now=NOW) is None, (
        "after a read-back a bare device phrase is a new sentence; a correction says no"
    )
    correction = corrections.correction_turn(kept, "Hayır, ev bilgisayarında.", now=NOW)
    assert (correction.kind, correction.heard, correction.meant) == ("device", "ofisü", "ev")
    assert (correction.intent, correction.application, correction.device) == (
        "app_open",
        "calc",
        "ev",
    )
    # "ofisü" is nothing like "ev": the owner changed the machine, the STT did not mishear it.
    assert correction.reason == "not_similar" and not correction.learnable
    assert not corrections.learn(
        db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
    ).written

    # A word that does resemble the alias the owner named is the mishearing, and is learned.
    kept = {**kept, "heard_device": "evü"}
    correction = corrections.correction_turn(kept, "Hayır, ev bilgisayarında.", now=NOW)
    assert correction.learnable and (correction.heard, correction.meant) == ("evü", "ev")
    assert corrections.learn(
        db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
    ).written
    _, after = _read(db, "Evü bilgisayarında hesap makinesini aç.")
    assert after.band == "high" and after.device.alias == "ev"


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        # an adverb is the name of nothing, before its likeness is even asked
        ("Hemen bilgisayarımda hesap makinesini aç.", "not_a_name"),
        ("Şimdi bilgisayarımda hesap makinesini aç.", "not_a_name"),
        # a word no list holds: only its unlikeness to "ev" refuses it
        ("Lütfen şirket bilgisayarında hesap makinesini aç.", "not_similar"),
        ("Kırmızı bilgisayarımda hesap makinesini aç.", "not_similar"),
        ("Küçük bilgisayarımda hesap makinesini aç.", "not_similar"),
    ],
)
def test_the_word_before_the_computer_word_is_only_a_guess(db, tmp_path, sentence, reason) -> None:
    """The heard word is taken by POSITION. "Hemen bilgisayarımda ... aç" answered with "ev"
    must not teach "hemen = ev": the next "hemen hesap makinesini aç" would launch at home.
    A guessed word is learned only when it resembles the alias the owner answered with."""
    intent, decision = _read(db, sentence)
    assert decision.band == "low" and decision.question == QUESTION
    correction = corrections.correction_turn(
        _kept(sentence, intent, decision), "ev bilgisayarında", now=NOW
    )
    assert correction is not None and correction.device == "ev"  # the answer still answers
    assert correction.reason == reason and not correction.learnable
    assert not corrections.learn(
        db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
    ).written
    assert _vocabulary_rows(db) == []


def test_the_pair_form_states_both_sides_and_outranks_the_confusion_list(db, tmp_path) -> None:
    """Nothing is guessed in "ona X deme, Y de", so an unlike word may be taught there - and
    the owner's own word outranks the release artefact (the confusion list says "ofis")."""
    for said, word, alias in (
        ("Ona şirket deme, iş de.", "şirket", "iş"),
        ("Ona ofisü deme, ev de.", "ofisü", "ev"),
    ):
        correction = corrections.correction_turn(None, said, now=NOW)
        assert (correction.kind, correction.heard, correction.meant) == ("device", word, alias)
        assert correction.intent is None  # a word is corrected, no turn is re-issued
        assert corrections.learn(
            db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
        ).written
    _, office = _read(db, "Ofisü bilgisayarında hesap makinesini açın.")
    assert office.band == "high" and office.device.alias == "ev"
    _, work = _read(db, "Şirket bilgisayarında hesap makinesini aç.")
    assert work.band == "high" and work.device.alias == "iş"


def test_a_closed_form_the_owner_said_is_never_remapped(db, tmp_path) -> None:
    """ "Ofis bilgisayarında ... aç" then "hayır, ev bilgisayarında" is a change of mind, not
    a mishearing: "ofis" must not come to mean the home PC."""
    kept = {
        "intent": "app_open",
        "application": "calc",
        "band": "medium",
        "device": "ofis",
        "heard_device": "ofis",
        "at": NOW.isoformat(),
    }
    correction = corrections.correction_turn(kept, "hayır, ev bilgisayarında", now=NOW)
    assert correction is not None and correction.device == "ev"  # re-issued at home ...
    assert not correction.learnable and correction.reason == "known_word"  # ... and not learned
    learned = corrections.learn(db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path)
    assert not learned.written and _vocabulary_rows(db) == []


@pytest.mark.parametrize(
    "heard",
    [
        "dizüstü",  # an alias phrase the device grammar reads
        "ofisi",  # a suffixed alias (layer 1 drops the suffix)
        "evim",
        "salon",  # an alias the owner configured on a device
        "chrome penceresi",  # words the allow-list reads as an application
        "hesap makinesi",
    ],
)
def test_no_word_the_system_already_reads_becomes_a_synonym(db, tmp_path, heard) -> None:
    kept = {
        "intent": "app_open",
        "application": "calc",
        "band": "low",
        "device": None,
        "heard_device": heard,
        "at": NOW.isoformat(),
    }
    correction = corrections.correction_turn(
        kept, "ofis bilgisayarında", now=NOW, aliases=("ev", "ofis", "salon")
    )
    assert correction is not None and correction.device == "ofis"
    assert correction.reason == "known_word" and not correction.learnable
    assert not corrections.learn(
        db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
    ).written
    assert _vocabulary_rows(db) == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Diğer bilgisayarda hesap makinesini aç.",
        "Öbür bilgisayarda hesap makinesini aç.",
        "Onun bilgisayarında hesap makinesini aç.",
        "Yandaki bilgisayarda hesap makinesini aç.",
    ],
)
def test_a_pointing_word_is_not_a_name(db, sentence) -> None:
    intent, decision = _read(db, sentence)
    kept = _kept(sentence, intent, decision)
    if kept is None:
        return  # nothing was asked: nothing to correct
    correction = corrections.correction_turn(kept, "ofis", now=NOW)
    assert correction is not None and correction.device == "ofis"
    assert not correction.learnable


def test_nothing_is_a_correction_after_a_high_turn_or_a_stale_one(db) -> None:
    intent, decision = _read(db, "Ofis bilgisayarında hesap makinesini aç.")
    assert decision.band == "high"
    assert _kept("Ofis bilgisayarında hesap makinesini aç.", intent, decision) is None
    assert corrections.correction_turn(None, "hayır, ev bilgisayarında", now=NOW) is None

    intent, low = _read(db, MISHEARD)
    kept = _kept(MISHEARD, intent, low)
    late = NOW + timedelta(seconds=understanding_policy.ANSWER_WINDOW_S + 1)
    assert corrections.correction_turn(kept, "ofis bilgisayarında", now=late) is None
    assert corrections.correction_turn(kept, "hava nasıl", now=NOW) is None


def test_the_pair_form_teaches_the_unknown_side(db, tmp_path) -> None:
    """ "Ona X deme, Y de": whichever side the system knows is the entity, the other is the
    new word for it."""
    assert resolve_intent("hesaplayıcıyı aç").intent is Intent.NONE
    correction = corrections.correction_turn(
        None, "Ona hesap makinesi deme, hesaplayıcı de.", now=NOW
    )
    assert (correction.kind, correction.heard, correction.meant) == ("app", "hesaplayıcı", "calc")
    assert corrections.learn(
        db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
    ).written
    assert _vocabulary_rows(db)[0].text == "hesaplayıcı = Hesap Makinesi (uygulama)"

    # Rule first: with the vocabulary of this turn loaded, the rule table reads the word.
    intent, decision = _read(db, "Hesaplayıcıyı aç.")
    assert intent.intent is Intent.APP_OPEN and intent.application == "calc"
    assert decision.band == "high" and decision.acts
    assert any("hesaplayıcı = " in line for line in decision.candidate.evidence)
    # A word the allow-list already reads is not relearned ...
    known = corrections.correction_turn(None, "ona hesap makinesi deme, calculator de", now=NOW)
    assert known is not None and not known.learnable and known.reason == "already_known"
    # ... a pair naming two different known things is refused, and so is one naming none.
    clash = corrections.correction_turn(None, "ona not defteri deme, hesap makinesi de", now=NOW)
    assert clash is not None and not clash.learnable and clash.reason == "known_word"
    assert corrections.correction_turn(None, "ona zımbırtı deme, dalga de", now=NOW) is None


@pytest.mark.parametrize(
    ("said", "sentence"),
    [
        # a token of an application alias phrase, bare and with its case ending
        ("Ona ofis deme, hesap de.", "Hesap makinesini aç."),
        ("Ona ev deme, makinesini de.", "Hesap makinesini aç."),
        ("Ona ev deme, defteri de.", "Not defterini aç."),
        # the computer word itself, in any form
        ("Ona ev deme, bilgisayar de.", "Bilgisayarda hesap makinesini aç."),
        ("Ona ofis deme, bilgisayarım de.", "Bilgisayarımda hesap makinesini aç."),
        # a verb of the rule tables
        ("Ona ofis deme, açsana de.", "Hesap makinesini açsana."),
        ("Ona ofis deme, açın de.", "Hesap makinesini açın."),
        ("Ona ev deme, kapat de.", "Hesap makinesini aç."),
    ],
)
def test_the_pair_form_never_teaches_the_routers_own_words_as_a_device(
    db, tmp_path, said, sentence
) -> None:
    """Red before: "ona ofis deme, hesap de" was learned as "hesap = ofis (cihaz)", and the
    next "Hesap makinesini aç" opened on ofis at HIGH with no machine named. A word the
    router itself reads - a token of an application's name, the computer word, a verb -
    names no machine, whatever the owner's sentence looked like."""
    _, before = _read(db, sentence)
    correction = corrections.correction_turn(None, said, now=NOW)
    assert correction is not None and correction.kind == "device"
    assert correction.reason == "known_word" and not correction.learnable
    learned = corrections.learn(db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path)
    assert not learned.written
    assert _vocabulary_rows(db) == [] and list(tmp_path.iterdir()) == []
    _, after = _read(db, sentence)
    assert (after.band, after.device, after.layer) == (before.band, before.device, before.layer)


def test_a_verb_is_no_name_for_an_application_and_a_heard_word_has_a_length(db, tmp_path) -> None:
    """ "Ona hesap makinesi deme, açsana de" would make every "... açsana" the allow-list cannot
    read ("Kapıyı açsana") open the calculator. And a heard "word" is a word: it becomes a memory
    key and a file name."""
    verb = corrections.correction_turn(None, "Ona hesap makinesi deme, açsana de.", now=NOW)
    assert verb is not None and verb.reason == "known_word" and not verb.learnable
    long = corrections.correction_turn(None, f"Ona {'ofüs' * 53} deme, ofis de.", now=NOW)
    assert long is not None and long.reason == "not_a_name" and not long.learnable
    for correction in (verb, long):
        assert not corrections.learn(
            db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
        ).written
    assert _vocabulary_rows(db) == [] and list(tmp_path.iterdir()) == []
    corrections.vocabulary(db)
    assert resolve_intent("Kapıyı açsana.").intent is Intent.NONE


@pytest.mark.parametrize(
    "sentence",
    [
        "Bunu bana deme, evde de.",  # "don't tell ME that, say it at home"
        "Öyle deme, evde de.",
        "Kimseye deme, evde de.",
        "Onu şirket deme, iş de.",  # "onu" is the thing SAID, not the thing named
        "Şirket deme, iş de.",
    ],
)
def test_the_pair_form_needs_its_address_word(db, sentence) -> None:
    """Red before: the address word was optional, so "Bunu bana deme, evde de" was read as
    "bana = ev (cihaz)". "Ona/buna/şuna X deme, Y de" is the form; without the word that says
    WHAT is being named, "... deme, ... de" is ordinary speech."""
    assert corrections.correction_turn(None, sentence, now=NOW) is None
    kept = _kept(MISHEARD, *_read(db, MISHEARD))
    assert corrections.correction_turn(kept, sentence, now=NOW) is None


@pytest.mark.parametrize(
    "sentence",
    [
        "Ona ev deme, hemen de.",  # the module's own example of what is never taught
        "Ona ev deme, şimdi de.",
        "Ona ev deme, bana de.",
        "Ona ev deme, kimseye de.",
        "Ona ofis deme, lütfen de.",
        "Ona ofis deme, öyle de.",
        "Buna hesap makinesi deme, hemen de.",
        "Şuna not defteri deme, lütfen de.",
    ],
)
def test_a_pronoun_an_adverb_or_a_politeness_word_names_nothing(db, tmp_path, sentence) -> None:
    """Red before: "ona ev deme, hemen de" stored "hemen = ev (cihaz)". A word every other
    sentence carries is the name of no machine and no application."""
    correction = corrections.correction_turn(None, sentence, now=NOW)
    assert correction is not None
    assert correction.reason == "not_a_name" and not correction.learnable
    assert not corrections.learn(
        db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
    ).written
    assert _vocabulary_rows(db) == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Ona aptal deme, evde de.",  # "say it AT HOME": a place, not a name
        "Ona aptal deme, ofis bilgisayarında de.",
        "Ona aptal deme, ofisteki de.",
        "Ona aptal deme, Chrome'da de.",
        "Ona evde deme, aptal de.",
    ],
)
def test_the_pair_form_states_a_name_bare(db, sentence) -> None:
    """Red before: "ona aptal deme, evde de" stored "aptal = ev (cihaz)". The side the system
    knows is a NAME - "ev", "ofis bilgisayarı", "Not Defteri" - never a word in a case."""
    assert corrections.correction_turn(None, sentence, now=NOW) is None


def test_the_pair_form_still_reads_a_bare_machine_name(db) -> None:
    for sentence in ("Ona şirket deme, iş bilgisayarı de.", "Buna şirket deme, iş de."):
        correction = corrections.correction_turn(None, sentence, now=NOW)
        assert correction is not None and correction.learnable, sentence
        assert (correction.kind, correction.heard, correction.meant) == ("device", "şirket", "iş")


def test_a_taught_machine_word_binds_only_where_the_sentence_names_a_machine(db, tmp_path) -> None:
    """Red before: a device synonym bound wherever its word stood, so a row "bana = ev" sent
    "Bana hesap makinesini aç" home at HIGH. The grammar binds its own aliases by case
    ("ofis bilgisayarında", "ofiste") and leaves a lone "ev" alone; a taught word is read the
    same way - whatever the vocabulary rows hold."""
    plain = "Bana hesap makinesini aç."
    _, before = _read(db, plain)
    for heard, meant in (("bana", "ev"), ("ofüs", "ofis")):
        assert corrections.learn(
            db,
            EMBEDDER,
            corrections.Correction(kind="device", heard=heard, meant=meant),
            session_id="s",
            proposals_dir=tmp_path,
        ).written
    assert len(corrections.vocabulary(db)) == 2
    for sentence in (plain, "Ofüs hesap makinesini aç.", "Ofüsü hesap makinesini aç."):
        _, after = _read(db, sentence)
        assert after.device is None and after.layer != corrections.LAYER_VOCABULARY, sentence
        assert (after.band, after.confidence) == (before.band, before.confidence), sentence
    for sentence in (
        MISHEARD,
        "Ofüs bilgisayarımda hesap makinesini aç.",
        "Ofüste hesap makinesini aç.",
        "Ofüsteki bilgisayarda hesap makinesini aç.",
    ):
        _, bound = _read(db, sentence)
        assert bound.band == "high" and bound.device.alias == "ofis", sentence
        assert bound.layer == corrections.LAYER_VOCABULARY, sentence


def test_not_that_one_names_the_application_and_reissues_the_turn(db) -> None:
    heard = "Ofisü bilgisayarında hesap makinesini açın."
    intent, decision = _read(db, heard)
    kept = _kept(heard, intent, decision)
    correction = corrections.correction_turn(kept, "Onu değil, Not Defteri.", now=NOW)
    assert correction is not None and correction.kind == "app"
    assert (correction.intent, correction.application, correction.device) == (
        "app_open",
        "notepad",
        "ofis",
    )
    # The application was read from words the allow-list knows: there is no new word to learn.
    assert not correction.learnable


def test_without_a_vocabulary_the_router_reads_exactly_as_before() -> None:
    assert corrections.active() == ()
    assert resolve_intent("hesaplayıcıyı aç").intent is Intent.NONE
    assert resolve_intent("Hesap makinesini aç").application == "calc"
    # A learned word is matched whole, with a closed case ending - never as a prefix.
    token = corrections.activate((corrections.Synonym("app", "not", "notepad", uuid.uuid4()),))
    try:
        assert resolve_intent("notu aç").application == "notepad"
        assert resolve_intent("notaları aç").intent is Intent.NONE
        assert resolve_intent("notebook aç").intent is Intent.NONE
    finally:
        corrections.deactivate(token)
    assert resolve_intent("notu aç").intent is Intent.NONE


def test_a_malformed_vocabulary_row_is_no_synonym(db) -> None:
    """``memory.remember`` could be handed the class by a model: a row with no pair in its
    value names nothing, and a device row naming no alias binds nothing."""
    values = ({}, {"kind": "device", "heard": "ofüs", "meant": "bulut"}, {"kind": "x"})
    for index, value in enumerate(values):
        memory_service.remember_explicit(
            db,
            EMBEDDER,
            text="serbest metin",
            memory_class=memory_policy.VOCABULARY,
            key=f"serbest:{index}",
            value=value,
        )
    assert len(_vocabulary_rows(db)) == 3
    assert corrections.vocabulary(db) == ()


# --- through the real relay ----------------------------------------------------------------


def test_relay_the_answer_teaches_the_word_and_the_second_time_is_right(
    monkeypatch, tmp_path
) -> None:
    from app.memory.runtime import MemoryRuntime
    from app.voice.understanding import combine
    from tests.unit.test_operator_open_application_fallback import (
        _both_online,
        _bound_session,
        _say,
        _tool,
    )

    monkeypatch.setattr(combine, "_default_engine", None)
    monkeypatch.setenv(corrections.PROPOSALS_DIR_ENV, str(tmp_path / "proposals"))
    (tmp_path / "proposals").mkdir()
    world = _both_online(monkeypatch, tmp_path)
    engine = world.factory.kw["bind"]
    for table in MEMORY_TABLES:
        table.create(engine, checkfirst=True)
    runtime = world.client.app.state.voice_realtime
    runtime.register_live(
        memory_runtime=MemoryRuntime(runtime.settings, engine=engine, embedder=EMBEDDER)
    )
    sid = _bound_session(world, "MAIL")

    first = _say(world.client, sid, MISHEARD)["resolved_intents"][0]
    assert first["band"] == "low"
    call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["result"]["speech"] == QUESTION and world.commands.calls == []

    answered = _say(world.client, sid, "Ofis bilgisayarında.")["resolved_intents"][0]
    assert answered["intent"] == "app_open" and answered["band"] == "high"
    with world.factory() as session:
        rows = _vocabulary_rows(session)
        assert [row.text for row in rows] == ["ofüs = ofis (cihaz)"]
        assert rows[0].stage == "durable" and rows[0].explicit is True
    assert len(list((tmp_path / "proposals").iterdir())) == 1

    # Another session: the word is the owner's, not the session's.
    sid2 = _bound_session(world, "MAIL")
    second = _say(world.client, sid2, MISHEARD)["resolved_intents"][0]
    assert second["band"] == "high" and second["confidence"] == 1.0
    call = _tool(world.client, sid2, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["status"] == "succeeded", call
    assert {c["device_id"] for c in world.commands.calls} == {world.ids["GMKADIRAKBABA"]}
    assert len(list((tmp_path / "proposals").iterdir())) == 1


def test_relay_a_no_after_the_read_back_reissues_the_turn_and_guesses_no_word(
    monkeypatch, tmp_path
) -> None:
    from app.memory.runtime import MemoryRuntime
    from app.voice.realtime_sessions.models import RealtimeSessionRow
    from app.voice.understanding import combine
    from tests.unit.test_operator_open_application_fallback import (
        _both_online,
        _bound_session,
        _say,
        _tool,
    )

    monkeypatch.setattr(combine, "_default_engine", None)
    monkeypatch.setenv(corrections.PROPOSALS_DIR_ENV, str(tmp_path))
    world = _both_online(monkeypatch, tmp_path)
    engine = world.factory.kw["bind"]
    for table in MEMORY_TABLES:
        table.create(engine, checkfirst=True)
    runtime = world.client.app.state.voice_realtime
    runtime.register_live(
        memory_runtime=MemoryRuntime(runtime.settings, engine=engine, embedder=EMBEDDER)
    )
    heard = "Ofisü bilgisayarında hesap makinesini açın."
    sid = _bound_session(world, "GMKADIRAKBABA")
    assert _say(world.client, sid, heard)["resolved_intents"][0]["band"] == "medium"

    said = _say(world.client, sid, "Hayır, ev bilgisayarında.")["resolved_intents"][0]
    assert (said["intent"], said["band"], said["tool"]) == ("app_open", "high", "operator.app_open")
    call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["status"] == "succeeded", call
    assert {c["device_id"] for c in world.commands.calls} == {world.ids["MAIL"]}
    with world.factory() as session:
        assert _vocabulary_rows(session) == []  # "ofisü" is nothing like "ev": no mishearing
        kept = session.get(RealtimeSessionRow, uuid.UUID(sid)).context_json
        assert corrections.CORRECTABLE_KEY not in kept  # a HIGH turn leaves nothing to correct

    # A third "hayır" corrects nothing: the turn before it was the owner's own closed form.
    assert _say(world.client, sid, "Hayır, ofis.")["resolved_intents"][0]["intent"] == "none"
    # The pair form teaches the word outright, and the next reading uses it.
    taught = _say(world.client, sid, "Ona ofisü deme, ev de.")["resolved_intents"][0]
    assert taught["intent"] == "none"
    with world.factory() as session:
        assert [row.text for row in _vocabulary_rows(session)] == ["ofisü = ev (cihaz)"]
    again = _say(world.client, sid, heard)["resolved_intents"][0]
    assert again["band"] == "high" and again["confidence"] == 1.0
    with world.factory() as session:
        record = session.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["last_utterance"]
    assert record["device_targets"] == ["ev"] and record["understanding"]["layer"] == "vocabulary"

    # The router's own words are taught to nobody: "hesap" stays a word of the calculator's
    # name, and the next "hesap makinesini aç" names no machine.
    assert _say(world.client, sid, "Ona ofis deme, hesap de.")["resolved_intents"][0]["intent"] == (
        "none"
    )
    with world.factory() as session:
        assert [row.text for row in _vocabulary_rows(session)] == ["ofisü = ev (cihaz)"]
    plain = _say(world.client, sid, "Hesap makinesini aç.")["resolved_intents"][0]
    assert plain["intent"] == "app_open"
    with world.factory() as session:
        record = session.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["last_utterance"]
    assert record["device_targets"] == [] and record["understanding"]["layer"] != "vocabulary"


def _relay_world(monkeypatch, tmp_path):
    from app.memory.runtime import MemoryRuntime
    from app.voice.understanding import combine
    from tests.unit.test_operator_open_application_fallback import _both_online

    monkeypatch.setattr(combine, "_default_engine", None)
    (tmp_path / "proposals").mkdir()
    monkeypatch.setenv(corrections.PROPOSALS_DIR_ENV, str(tmp_path / "proposals"))
    world = _both_online(monkeypatch, tmp_path)
    engine = world.factory.kw["bind"]
    for table in MEMORY_TABLES:
        table.create(engine, checkfirst=True)
    runtime = world.client.app.state.voice_realtime
    runtime.register_live(
        memory_runtime=MemoryRuntime(runtime.settings, engine=engine, embedder=EMBEDDER)
    )
    return world


def test_relay_ordinary_speech_teaches_nothing_and_the_next_command_stays_put(
    monkeypatch, tmp_path
) -> None:
    """Red before: in an office session "Bunu bana deme, evde de." was stored as "bana = ev
    (cihaz)" - durable, every session, nothing spoken - and the next "Bana hesap makinesini
    aç." was HIGH 1.0 on the home PC."""
    from app.voice.realtime_sessions.models import RealtimeSessionRow
    from tests.unit.test_operator_open_application_fallback import _bound_session, _say, _tool

    world = _relay_world(monkeypatch, tmp_path)
    sid = _bound_session(world, "GMKADIRAKBABA")  # the office PC
    for sentence in (
        "Bunu bana deme, evde de.",
        "Öyle deme, evde de.",
        "Kimseye deme, evde de.",
        "Şirket deme, iş de.",  # no address word: held by the form alone, no list knows it
        "Ona ev deme, hemen de.",
        "Ona aptal deme, evde de.",
    ):
        said = _say(world.client, sid, sentence)["resolved_intents"][0]
        assert said["intent"] == "none", sentence
        with world.factory() as session:
            assert [row.text for row in _vocabulary_rows(session)] == [], sentence
    assert list((tmp_path / "proposals").iterdir()) == []

    for sentence in ("Bana hesap makinesini aç.", "Hemen hesap makinesini aç."):
        world.commands.calls.clear()
        plain = _say(world.client, sid, sentence)["resolved_intents"][0]
        assert plain["intent"] == "app_open", sentence
        with world.factory() as session:
            record = session.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["last_utterance"]
        assert record["device_targets"] == [], sentence
        assert record["understanding"]["layer"] != "vocabulary", sentence
        call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
        assert call["status"] == "succeeded", call
        assert {c["device_id"] for c in world.commands.calls} == {world.ids["GMKADIRAKBABA"]}


def test_relay_a_sentence_the_rule_tables_route_is_never_read_as_a_correction(
    monkeypatch, tmp_path
) -> None:
    """Rule first, at the relay: "Ona dur deme, ev de." is the stop family's sentence and
    "Ona devam et deme, ev de." the resume family's. The corrections module would read each
    as a pair - its own lists know the operator's verbs, not every word of every rule table -
    so the relay's guard is what keeps "dur = ev (cihaz)" out of the memory. Mutation: the
    guard removed -> a row is written here."""
    from tests.unit.test_operator_open_application_fallback import _bound_session, _say

    world = _relay_world(monkeypatch, tmp_path)
    sid = _bound_session(world, "GMKADIRAKBABA")
    for sentence, routed in (
        ("Ona dur deme, ev de.", "stop"),
        ("Ona devam et deme, ev de.", "resume"),
    ):
        # The module alone WOULD learn it: without that, this test proves nothing.
        alone = corrections.correction_turn(None, sentence, now=NOW)
        assert alone is not None and alone.learnable, sentence
        said = _say(world.client, sid, sentence)["resolved_intents"][0]
        assert said["intent"] == routed, sentence
        with world.factory() as session:
            assert [row.text for row in _vocabulary_rows(session)] == [], sentence
    assert list((tmp_path / "proposals").iterdir()) == []
    # ... and the same form the tables leave unrouted IS a lesson, through the same relay.
    assert _say(world.client, sid, "Ona şirket deme, iş de.")["resolved_intents"][0]["intent"] == (
        "none"
    )
    with world.factory() as session:
        assert [row.text for row in _vocabulary_rows(session)] == ["şirket = iş (cihaz)"]


# --- a taught application word never takes a sentence another table owns ---------------------

#: The inspector's four (2026-10-02): each is another rule table's sentence, HIGH, and each
#: was ``app_open`` HIGH 1.0 after "ona not defteri deme, dosya de" (and müzik, şarkı).
OWNED_SENTENCES = (
    ("Dosyayı aç.", "artifact_open"),
    ("Dosyayı aç ve oku.", "document_read"),
    ("Müzik aç.", "media_play"),
    ("Şarkıyı aç.", "media_play"),
)
OWNED_WORDS = (("dosya", "notepad"), ("müzik", "calc"), ("şarkı", "mspaint"))


def test_a_taught_application_word_is_asked_only_when_the_whole_router_answers_none() -> None:
    """Red before: ``app_for`` was asked inside the application table, which stands BEFORE
    the document, artifact and media tables - so a row "dosya = Not Defteri" turned "Dosyayı
    aç" into ``app_open``. Whatever the rows hold, a taught word is the LAST reading."""
    before = [resolve_intent(sentence) for sentence, _ in OWNED_SENTENCES]
    assert [r.intent.value for r in before] == [owner for _, owner in OWNED_SENTENCES]
    token = corrections.activate(
        (
            *(corrections.Synonym("app", heard, meant) for heard, meant in OWNED_WORDS),
            corrections.Synonym("app", "hesaplayıcı", "calc"),
            corrections.Synonym("app", "kapı", "notepad"),
        )
    )
    try:
        after = [resolve_intent(sentence) for sentence, _ in OWNED_SENTENCES]
        assert [(r.intent, r.matched, r.application) for r in after] == [
            (r.intent, r.matched, r.application) for r in before
        ]
        # ... and a sentence the whole router leaves unrouted is still read by the lesson:
        # the words as heard, the polite request and the all-caps transcript.
        for sentence in ("Hesaplayıcıyı aç.", "Kapıyı aç.", "HESAPLAYICIYI AÇ"):
            taught = resolve_intent(sentence)
            assert taught.intent is Intent.APP_OPEN, sentence
            assert taught.matched.startswith("vocabulary:"), sentence
        assert resolve_intent("Kapıyı aç.").application == "notepad"
        asked = resolve_intent("Hesaplayıcıyı açar mısın?")
        assert (asked.intent, asked.application) == (Intent.APP_OPEN, "calc")
        polite = resolve_intent("Hesaplayıcıyı açabilir misin?")
        assert (polite.intent, polite.application) == (Intent.APP_OPEN, "calc")
        assert polite.route_repair == "polite"
        # No open verb, no application: the word alone opens nothing.
        assert resolve_intent("Hesaplayıcı nerede?").intent is not Intent.APP_OPEN
    finally:
        corrections.deactivate(token)


def test_a_taught_word_outranks_only_the_guess_at_a_title_nothing_else_wanted() -> None:
    """The media table's last resort reads ANY two unknown words and "aç" as a title, so a
    strict "only when the router answers nothing" would make "Ofis bilgisayarında
    hesaplayıcıyı aç" a YouTube search - the very sentence ADR-0224 is about. That guess
    claims only "a name nothing else wanted"; the owner's lesson wants it. Nothing else is
    outranked: a title with no taught word in it stays the media table's."""
    named = "Ofis bilgisayarında hesaplayıcıyı aç."
    guessed = resolve_intent(named)
    assert guessed.intent is Intent.MEDIA_PLAY and not owned_by_a_table(guessed)
    assert owned_by_a_table(resolve_intent("Müzik aç.")), "a media WORD is the table's own"
    assert not owned_by_a_table(resolve_intent("Kapıyı aç."))
    token = corrections.activate((corrections.Synonym("app", "hesaplayıcı", "calc"),))
    try:
        taught = resolve_intent(named)
        assert (taught.intent, taught.application) == (Intent.APP_OPEN, "calc")
        assert taught.media_query is None and taught.confidence == 1.0
        assert taught.capability == resolve_intent("Hesap makinesini aç.").capability
        title = resolve_intent("Güldür Güldür aç.")
        assert title.intent is Intent.MEDIA_PLAY and title.media_query
        played = resolve_intent("Hesaplayıcı şarkısını çal.")  # a media word and a play verb
        assert played.intent is Intent.MEDIA_PLAY
    finally:
        corrections.deactivate(token)


@pytest.mark.parametrize("word", ["dosya", "müzik", "şarkı", "dosyayı", "müziği"])
def test_a_word_another_rule_table_routes_is_no_name_for_an_application(db, tmp_path, word) -> None:
    """Red before: "Ona not defteri deme, dosya de." was written, silently and durably. A
    word the router already reads in an open sentence is a known word - asked of the router
    itself (the sentence "<word> aç" and its accusative), not of a list."""
    correction = corrections.correction_turn(None, f"Ona not defteri deme, {word} de.", now=NOW)
    assert correction is not None and (correction.kind, correction.meant) == ("app", "notepad")
    assert correction.reason == "known_word" and not correction.learnable
    learned = corrections.learn(db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path)
    assert not learned.written
    assert _vocabulary_rows(db) == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("word", ["hesaplayıcı", "kapı", "karalama"])
def test_a_word_no_rule_table_routes_is_still_taught(db, tmp_path, word) -> None:
    """The refusal above asks the router, so a word it leaves unrouted stays teachable - and
    it asks the rule TABLES: a word the owner already taught is not what answers."""
    correction = corrections.correction_turn(None, f"Ona not defteri deme, {word} de.", now=NOW)
    assert correction is not None and correction.learnable, (word, correction)
    assert corrections.learn(
        db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
    ).written
    assert len(corrections.vocabulary(db)) == 1
    longer = corrections.correction_turn(
        None, f"Ona hesap makinesi deme, {word} zımbırtısı de.", now=NOW
    )
    assert longer is not None and longer.learnable, longer
    assert (longer.heard, longer.meant) == (f"{word} zımbırtısı", "calc")


# --- a turn an application synonym decided is the vocabulary's, not the rule's --------------


def test_a_turn_decided_by_an_application_synonym_records_the_vocabulary_layer(
    db, tmp_path
) -> None:
    """Red before: layer ``rule``, confidence 1.0 - the calibration data could not tell that
    a word the owner taught, not a rule table, decided the turn."""
    correction = corrections.correction_turn(
        None, "Ona hesap makinesi deme, hesaplayıcı de.", now=NOW
    )
    assert corrections.learn(
        db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path
    ).written
    intent, decision = _read(db, "Hesaplayıcıyı aç.")
    assert intent.intent is Intent.APP_OPEN and decision.band == "high" and decision.acts
    assert decision.layer == corrections.LAYER_VOCABULARY
    assert decision.audit_block()["layer"] == "vocabulary"
    # The allow-list's own name is the rule's, with or without the row ...
    _, plain = _read(db, "Hesap makinesini aç.")
    assert plain.layer == understanding_policy.LAYER_RULE
    assert not any("vocabulary" in line for line in plain.candidate.evidence)
    # ... and a device the sentence did not name is still asked for, by its own layer.
    _, asked = _read(db, "Ofüs bilgisayarında hesaplayıcıyı aç.")
    assert asked.band == "low" and asked.layer != corrections.LAYER_VOCABULARY


# --- the secret guard reads the sentence as it was SPOKEN -----------------------------------

SPOKEN_SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"


@pytest.mark.parametrize(
    "sentence",
    [
        f"Ona chrome deme, {SPOKEN_SECRET} de.",
        f"Ona {SPOKEN_SECRET} deme, chrome de.",
        f"Ona ofis deme, {SPOKEN_SECRET} de.",
    ],
)
def test_a_secret_in_the_spoken_sentence_is_refused_before_it_is_a_word(
    db, tmp_path, sentence
) -> None:
    """Red before: the guard saw the NORMALISED word ("sk proj abc..."), which no secret
    pattern matches, so the key became a memory row and a proposal file name."""
    correction = corrections.correction_turn(None, sentence, now=NOW)
    assert correction is not None
    assert correction.reason == "secret_rejected" and not correction.learnable
    assert correction.heard is None  # nothing of it is carried any further
    learned = corrections.learn(db, EMBEDDER, correction, session_id="s", proposals_dir=tmp_path)
    assert (learned.written, learned.reason) == (False, "secret_rejected")
    assert _vocabulary_rows(db) == [] and list(tmp_path.iterdir()) == []
    assert corrections.vocabulary(db) == ()


def test_the_word_kept_for_a_correction_is_never_part_of_a_secret(db) -> None:
    """The other way a heard word reaches ``learn``: kept from the sentence before, by
    position. A sentence carrying a secret keeps no word at all."""
    sentence = f"{SPOKEN_SECRET} bilgisayarında hesap makinesini aç."
    assert corrections.heard_device_word(sentence) == "abcdefghijklmnopqrstuvwxyz123456"
    intent, decision = _read(db, sentence)
    kept = _kept(sentence, intent, decision)
    assert kept is not None and kept["heard_device"] is None
    correction = corrections.correction_turn(kept, "ofis bilgisayarında", now=NOW)
    assert correction is not None and correction.device == "ofis" and not correction.learnable
    plain = _kept(MISHEARD, *_read(db, MISHEARD))
    assert plain["heard_device"] == "ofüs"


def test_relay_a_taught_application_word_never_takes_over_an_owned_sentence(
    monkeypatch, tmp_path
) -> None:
    """Red before, through the real relay: after "Ona not defteri deme, dosya de." (and
    müzik, şarkı) the four sentences were ``app_open`` HIGH 1.0 and the tool call ran
    ``desktop.open_application``."""
    from app.voice.realtime_sessions.models import RealtimeSessionRow
    from tests.unit.test_operator_open_application_fallback import _bound_session, _say, _tool

    world = _relay_world(monkeypatch, tmp_path)
    sid = _bound_session(world, "GMKADIRAKBABA")

    def heard(sentence: str) -> tuple[Any, ...]:
        said = _say(world.client, sid, sentence)["resolved_intents"][0]
        with world.factory() as session:
            record = session.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["last_utterance"]
        return said["intent"], said["band"], said["tool"], record["understanding"]["layer"]

    before = {sentence: heard(sentence) for sentence, _ in OWNED_SENTENCES}
    assert [reading[0] for reading in before.values()] == [owner for _, owner in OWNED_SENTENCES]

    # 1. The lesson itself is refused: the router already reads the word.
    for word in ("dosya", "müzik", "şarkı"):
        said = _say(world.client, sid, f"Ona not defteri deme, {word} de.")["resolved_intents"][0]
        assert said["intent"] == "none", word
        with world.factory() as session:
            assert _vocabulary_rows(session) == [], word
    assert list((tmp_path / "proposals").iterdir()) == []
    assert {sentence: heard(sentence) for sentence, _ in OWNED_SENTENCES} == before

    # 2. ... and whatever the rows hold (an older release wrote them, an owner edit): a
    #    taught word is read only where the whole router answered nothing.
    with world.factory() as session:
        for word, app_id in OWNED_WORDS:
            assert corrections.learn(
                session,
                EMBEDDER,
                corrections.Correction(kind="app", heard=word, meant=app_id),
                session_id=sid,
            ).written
        assert len(_vocabulary_rows(session)) == 3
    assert {sentence: heard(sentence) for sentence, _ in OWNED_SENTENCES} == before
    assert world.commands.calls == []

    # 3. A word no table owns is taught, opens its application - and says which layer did.
    taught = _say(world.client, sid, "Ona hesap makinesi deme, hesaplayıcı de.")
    assert taught["resolved_intents"][0]["intent"] == "none"
    assert heard("Hesaplayıcıyı aç.") == ("app_open", "high", "operator.app_open", "vocabulary")
    call = _tool(world.client, sid, "operator.app_open", {"application": "Hesap Makinesi"})
    assert call["status"] == "succeeded", call
    assert [c["payload"].get("application") for c in world.commands.calls] == ["calc"]
    assert heard("Hesap makinesini aç.")[3] == "rule"
    # ... on the machine the sentence names too (the media table's bare-title guess would
    # have made this a search for "hesaplayıcıyı").
    named = heard("Ev bilgisayarında hesaplayıcıyı aç.")
    assert named == ("app_open", "high", "operator.app_open", "vocabulary")
    with world.factory() as session:
        record = session.get(RealtimeSessionRow, uuid.UUID(sid)).context_json["last_utterance"]
    assert record["device_targets"] == ["ev"]


def test_relay_an_all_caps_owned_sentence_keeps_its_intent_whatever_the_rows_hold(
    monkeypatch, tmp_path
) -> None:
    """An ALL-CAPS transcript whose "I" cannot say which i it is takes its own path through
    the router (``caps_fold``), with its own "a table owns it" guard. Mutation: that guard
    removed -> the three with an "I" are ``app_open`` here ("MÜZİK AÇ." says its İ and is
    read as heard, by the other guard)."""
    from tests.unit.test_operator_open_application_fallback import _bound_session, _say

    world = _relay_world(monkeypatch, tmp_path)
    sid = _bound_session(world, "GMKADIRAKBABA")
    capitals = ("DOSYAYI AÇ.", "DOSYAYI AÇ VE OKU.", "MÜZİK AÇ.", "ŞARKIYI AÇ.")
    owners = [owner for _, owner in OWNED_SENTENCES]

    def heard() -> list[str]:
        return [_say(world.client, sid, s)["resolved_intents"][0]["intent"] for s in capitals]

    assert heard() == owners
    with world.factory() as session:
        for word, app_id in OWNED_WORDS:
            planted = corrections.Correction(kind="app", heard=word, meant=app_id)
            assert corrections.learn(session, EMBEDDER, planted, session_id=sid).written
    assert heard() == owners
    assert world.commands.calls == []


def test_relay_a_spoken_secret_is_never_written_and_never_a_file_name(
    monkeypatch, tmp_path
) -> None:
    from tests.unit.test_operator_open_application_fallback import _bound_session, _say

    world = _relay_world(monkeypatch, tmp_path)
    sid = _bound_session(world, "GMKADIRAKBABA")
    said = _say(world.client, sid, f"Ona chrome deme, {SPOKEN_SECRET} de.")["resolved_intents"][0]
    assert said["intent"] == "none"
    with world.factory() as session:
        assert _vocabulary_rows(session) == []
        texts = session.execute(select(Memory.text)).scalars().all()
        assert not any("abcdefghijklmnopqrstuvwxyz" in text for text in texts)
    assert list((tmp_path / "proposals").iterdir()) == []


def test_relay_the_audit_row_of_a_spoken_secret_says_secret_rejected_and_none_of_its_words(
    monkeypatch, tmp_path
) -> None:
    """A lesson refused for a secret is said, not hidden: the turn's audit row names the
    outcome and the kind - never the sentence. Mutation: the relay's branch off -> the row
    carries no ``understanding_correction`` at all."""
    from app.broker.models import AuditEvent
    from app.voice.realtime_sessions.service import ACTION_INTENT_RESOLVED
    from tests.unit.test_operator_open_application_fallback import _bound_session, _say

    world = _relay_world(monkeypatch, tmp_path)
    sid = _bound_session(world, "GMKADIRAKBABA")
    _say(world.client, sid, f"Ona chrome deme, {SPOKEN_SECRET} de.")
    with world.factory() as session:
        turns = select(AuditEvent).where(AuditEvent.action == ACTION_INTENT_RESOLVED)
        (row,) = session.execute(turns.where(AuditEvent.subject_ref == sid)).scalars().all()
    assert row.metadata_json.get("understanding_correction") == {
        "kind": "app",
        "written": False,
        "reason": "secret_rejected",
        "proposed": False,
    }
    stored = f"{row.metadata_json} {row.subject_ref} {row.trace_id}".casefold()
    assert "abcdefghijklmnopqrstuvwxyz" not in stored and "sk-proj" not in stored
