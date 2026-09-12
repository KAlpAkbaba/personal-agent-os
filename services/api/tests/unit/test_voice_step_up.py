"""B05 req 245/247/248/664/666: the speaker verdict on the decision path.

It was advisory in the most literal sense. One route computed a verdict, returned it to the
caller, and nothing read it - so "voice identity alone is never root authentication" held
only because nothing had wired voice to anything privileged yet. That is a very different
guarantee from holding it on purpose, and it is the kind that stops being true the first time
somebody adds a tool.

The invariant is the first thing tested here and everything else is subordinate to it: there
is no path through this policy where a score, however high, makes an untrusted device
sufficient.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.security import step_up
from app.voice.models import SpeakerVerdictRow

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
SESSION = uuid.uuid4()


@pytest.fixture(autouse=True)
def _shadow_by_default():
    step_up.set_mode(step_up.MODE_SHADOW)
    yield
    step_up.set_mode(step_up.MODE_SHADOW)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SpeakerVerdictRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _verdict(db, *, decision="OWNER", score=0.95, device_trusted=True, age=timedelta(0)):
    step_up.record_verdict(
        db,
        owner_session_id=SESSION,
        decision=decision,
        score=score,
        device_trusted=device_trusted,
        effective_accept=0.75,
        now=NOW - age,
    )


def _decide(db, tool, *, device_trusted=True, mode=step_up.MODE_ENFORCE):
    return step_up.evaluate(
        db, tool=tool, owner_session_id=SESSION, device_trusted=device_trusted, now=NOW, mode=mode
    )


# ------------------------------------------------------------------ the invariant


def test_no_score_makes_an_untrusted_device_enough(db) -> None:
    """The product principle, as a test. A perfect voice match on a device we do not know
    is still not authority - and this is checked BEFORE the verdict is even loaded, so
    there is no ordering in which a score could get a word in first."""
    _verdict(db, score=1.0)

    for tool in ("eye.disable", "eye.enable", "mail.send"):
        decision = _decide(db, tool, device_trusted=False)
        assert decision.allowed is False
        assert decision.reason == step_up.REASON_UNTRUSTED_DEVICE


def test_a_verdict_reached_on_an_untrusted_device_is_not_reusable_later(db) -> None:
    """It was a measurement taken under conditions that capped it at UNCERTAIN. Carrying it
    over to a trusted device would launder exactly the thing the cap exists to prevent."""
    _verdict(db, device_trusted=False)

    decision = _decide(db, "eye.disable", device_trusted=True)

    assert decision.allowed is False
    assert decision.reason == step_up.REASON_NO_VERDICT


# ---------------------------------------------------------------- the three tiers


def test_a_question_needs_nothing(db) -> None:
    """`state.now` is a question about state, not an act on it. Requiring step-up to ask
    what time it is would make the whole policy something the owner turns off."""
    decision = _decide(db, "state.now", device_trusted=False)

    assert decision.allowed is True
    assert decision.tier == step_up.TIER_OPEN
    assert decision.would_refuse is False


def test_a_sensitive_action_needs_a_fresh_owner_verdict(db) -> None:
    _verdict(db)
    assert _decide(db, "eye.disable").allowed is True


def test_a_sensitive_action_without_any_verdict_is_refused(db) -> None:
    decision = _decide(db, "file.search")

    assert decision.allowed is False
    assert decision.reason == step_up.REASON_NO_VERDICT
    assert decision.speech == "Bunu yapmadan önce sesinizi doğrulamam gerekiyor efendim."


def test_a_stale_verdict_is_no_verdict(db) -> None:
    """A verdict from an hour ago says who was speaking an hour ago. Same discipline the
    world model applies to RUNTIME truth, for the same reason."""
    _verdict(db, age=timedelta(hours=1))

    decision = _decide(db, "file.search")

    assert decision.allowed is False
    assert decision.reason == step_up.REASON_STALE_VERDICT


def test_a_verdict_that_was_not_the_owner_is_refused(db) -> None:
    _verdict(db, decision="UNCERTAIN", score=0.80)

    decision = _decide(db, "file.search")

    assert decision.allowed is False
    assert decision.reason == step_up.REASON_NOT_OWNER


def test_a_critical_action_wants_a_higher_score_than_a_sensitive_one(db) -> None:
    """req 248. The same score, judged against a stricter bar - not a second opinion about
    the classifier."""
    _verdict(db, score=0.80)

    assert _decide(db, "eye.disable").allowed is True, "clears the ordinary bar"
    critical = _decide(db, "eye.enable")
    assert critical.allowed is False
    assert critical.reason == step_up.REASON_SCORE_BELOW_CRITICAL


def test_a_critical_action_wants_a_fresher_verdict_too(db) -> None:
    """The more an action costs to undo, the less old evidence is worth."""
    _verdict(db, score=0.99, age=timedelta(minutes=10))

    assert _decide(db, "eye.disable").allowed is True, "within the sensitive window"
    assert _decide(db, "eye.enable").reason == step_up.REASON_STALE_VERDICT


def test_the_two_windows_are_ordered(db) -> None:
    assert step_up.FRESH_FOR_CRITICAL < step_up.FRESH_FOR_SENSITIVE


# ------------------------------------------------------------------- the tier map


def test_every_registered_tool_has_a_tier() -> None:
    """The guard that keeps this policy from becoming the thing it replaced. A default of
    OPEN is how a tool added later ends up ungoverned with nobody deciding that it should
    be; a tier naming a tool that does not exist is a rule that governs nothing. Both
    directions fail here."""
    from app.security.step_up import _TIERS
    from app.voice.realtime_sessions.tools import default_registry

    registered = set(default_registry().names())
    classified = set(_TIERS)

    assert registered - classified == set(), sorted(registered - classified)
    assert classified - registered == set(), sorted(classified - registered)


def test_an_unknown_tool_is_treated_as_sensitive_not_open() -> None:
    """`handle_tool_call` refuses an unregistered tool before this is consulted, and the
    test above keeps the map complete. This is what happens the day both of those are wrong
    at once, and "assume it is harmless" is the wrong answer to that."""
    assert step_up.tier_of("something.nobody.registered") == step_up.TIER_SENSITIVE


def test_the_authority_boundary_tools_are_critical() -> None:
    """Naming them explicitly: these are the ones where being wrong is expensive."""
    for tool in ("release.promote", "release.rollback", "eye.enable", "mail.send",
                 "operator.shell", "capability.approve", "evolution.control"):
        assert step_up.tier_of(tool) == step_up.TIER_CRITICAL, tool


def test_reading_the_owners_mail_and_documents_is_not_open() -> None:
    """A voice that can read the owner's mail out loud is a voice with access to the
    owner's mail. Reading private content is the other half of "sensitive"."""
    for tool in ("mail.read", "mail.inbox", "document.read", "document.summarize",
                 "file.search"):
        assert step_up.tier_of(tool) == step_up.TIER_SENSITIVE, tool


# ------------------------------------------------------------------------- mode


def test_shadow_mode_records_the_refusal_and_lets_the_action_run(db) -> None:
    """Ships off, and for a second reason beyond caution: nothing in the realtime path
    produces a verdict yet, so enforcing today would refuse every sensitive voice action."""
    decision = _decide(db, "file.search", mode=step_up.MODE_SHADOW)

    assert decision.would_refuse is True
    assert decision.allowed is True
    assert decision.reason == step_up.REASON_NO_VERDICT


def test_the_policy_ships_off() -> None:
    from app.config import Settings

    assert Settings(_env_file=None).voice_step_up_mode == step_up.MODE_SHADOW


def test_an_unknown_mode_falls_back_to_shadow(db) -> None:
    assert step_up.set_mode("enfroce") == step_up.MODE_SHADOW


# -------------------------------------------------------------- storing a verdict


def test_a_session_keeps_one_verdict_not_a_history(db) -> None:
    """The question is always "how sure are we right now". A history of voice measurements
    is the archive the product principles refuse to build."""
    _verdict(db, score=0.80)
    _verdict(db, score=0.95)

    rows = db.query(SpeakerVerdictRow).all()
    assert len(rows) == 1
    assert rows[0].score == 0.95


def test_no_probe_embedding_is_ever_stored() -> None:
    """Biometric material lives encrypted behind speaker_profiles.embedding_ref or nowhere.
    Reading the columns rather than trusting the comment above them."""
    columns = set(SpeakerVerdictRow.__table__.columns.keys())

    assert columns == {
        "id",
        "owner_session_id",
        "decision",
        "score",
        "device_trusted",
        "effective_accept",
        "verified_at",
    }
    assert not any("embed" in c or "probe" in c or "sample" in c for c in columns)


# ------------------------------------------------------- wired, not just written


def test_the_relay_consults_the_policy_before_any_handler_runs() -> None:
    """This whole batch is about a rule that existed and was not wired. The check sits in
    `handle_tool_call`, the one relay every voice tool call passes through, ahead of the
    handler - so the model's choice of tool cannot route around it."""
    import inspect

    from app.voice.realtime_sessions import service

    source = inspect.getsource(service.handle_tool_call)
    assert "step_up_policy.evaluate" in source
    assert source.index("step_up_policy.evaluate") < source.index("research_followup_refusal")
    assert "device_is_trusted(db, owner)" in source, "trust derived, never taken from the call"


def test_the_verify_route_writes_the_verdict_the_policy_reads() -> None:
    """Two halves of one fact: if the route stopped recording, every sensitive action would
    quietly start being refused (or, in shadow, quietly counted as refused) with nothing
    saying why."""
    import inspect

    from app.voice import routes

    source = inspect.getsource(routes.verify_speaker_route)
    assert "step_up.record_verdict" in source
