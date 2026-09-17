"""B26 req 749/750: the router reports what it decided, and a wrong decision gets noticed.

The seven misroutes were found by a person sitting down with a table of sentences. Nothing
in the running system would ever have reported them: the router resolved, the tool ran, the
screen automation went off, and no record anywhere said "that was wrong". A class of defect
that only a human with a table can find will be back the week after the table is put down.

Two claims, tested apart because they fail apart:

* **749** — every resolution is recorded, and the owner's WORDS are not. The transcript is
  the most private thing this system handles, and the detector does not need it.
* **750** — a misroute announces itself in the SEQUENCE. A person whose screens just went
  dark because they asked about software updates says "dur" within a few seconds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.voice.intents import Intent
from app.voice.route_telemetry import (
    ACTING_INTENTS,
    MISROUTE_WINDOW_S,
    REACTION_TOKENS,
    RING_SIZE,
    RouteTelemetry,
    candidates,
    reaction_words,
    reset_telemetry,
    telemetry,
)

T0 = datetime(2026, 9, 14, 9, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


# --------------------------------------------------------------- 749: the record


def test_a_resolution_is_recorded_without_the_owners_words() -> None:
    ring = RouteTelemetry()
    event = ring.record(
        Intent.AMBIENT_POLICY_SET,
        matched="otomatik",
        tokens=("otomatik", "güncellemeleri", "kapat"),
        session_id="s1",
        now=T0,
    )

    assert event.intent is Intent.AMBIENT_POLICY_SET
    assert event.matched == "otomatik"
    assert event.routed is True
    assert event.session_id == "s1"
    # The transcript is not here, and neither is any word of it except the closed-set
    # reaction flags — which this sentence has none of.
    assert event.reaction_words == ()
    payload = event.as_dict()
    assert "güncellemeleri" not in str(payload)
    assert set(payload) == {"at", "intent", "matched", "routed", "reaction_words", "session_id"}


def test_only_the_closed_reaction_set_survives_into_the_record() -> None:
    """The one exception to "no words": a flag drawn from a fixed list of eight."""
    assert reaction_words(("bu", "yanlış", "oldu", "iptal")) == ("yanlış", "iptal")
    assert reaction_words(("ali", "ile", "toplantı")) == ()
    for word in reaction_words(("hayır", "gizli", "şifre")):
        assert word in REACTION_TOKENS


def test_not_routing_is_recorded_as_not_routing() -> None:
    ring = RouteTelemetry()
    event = ring.record(Intent.NONE, matched=None, tokens=("saat", "kaç"), now=T0)
    assert event.routed is False


def test_the_ring_is_bounded() -> None:
    """The ledger is the durable record; this is the last few seconds, in memory."""
    ring = RouteTelemetry(size=4)
    for index in range(10):
        ring.record(Intent.NONE, now=at(index))
    assert len(ring.events) == 4
    assert ring.events[0].at == at(6)
    assert RING_SIZE >= 64, "a ring too small to hold a conversation reports nothing"


# ------------------------------------------------------------ 750: the detection


def test_an_action_the_owner_objects_to_is_a_candidate() -> None:
    ring = RouteTelemetry()
    ring.record(Intent.AMBIENT_POLICY_SET, matched="otomatik", now=T0)
    ring.record(Intent.STOP, matched="dur", now=at(3))

    found = ring.candidates()
    assert len(found) == 1
    assert found[0].acted.intent is Intent.AMBIENT_POLICY_SET
    assert found[0].seconds == 3
    assert "ambient_policy_set" in found[0].why
    assert "3.0 sn" in found[0].why


def test_a_bare_negation_counts_even_without_an_intent_of_its_own() -> None:
    ring = RouteTelemetry()
    ring.record(Intent.MAIL_SEND, matched="gönder", now=T0)
    ring.record(Intent.NONE, tokens=("hayır", "yanlış"), now=at(2))

    found = ring.candidates()
    assert len(found) == 1
    assert found[0].acted.intent is Intent.MAIL_SEND


def test_a_late_objection_is_not_an_objection_to_this() -> None:
    """Outside the window, "dur" is the owner stopping whatever is happening NOW."""
    ring = RouteTelemetry()
    ring.record(Intent.MAIL_SEND, matched="gönder", now=T0)
    ring.record(Intent.STOP, now=at(MISROUTE_WINDOW_S + 1))
    assert ring.candidates() == []


def test_an_objection_before_the_action_is_not_about_it() -> None:
    """Order matters: a reaction can only be to something that already happened."""
    events = [
        RouteTelemetry().record(Intent.STOP, now=T0),
        RouteTelemetry().record(Intent.MAIL_SEND, matched="gönder", now=at(2)),
    ]
    assert candidates(events) == []


def test_one_objection_names_one_action() -> None:
    """An owner saying "dur" once is objecting to ONE thing.

    Reporting all three preceding actions would drown the real one in the other two — the
    failure mode of every detector that reports a set instead of a suspect.
    """
    ring = RouteTelemetry()
    ring.record(Intent.DISPLAY_OFF, matched="ekran", now=T0)
    ring.record(Intent.AMBIENT_POLICY_SET, matched="uyurken", now=at(1))
    ring.record(Intent.MAIL_SEND, matched="gönder", now=at(2))
    ring.record(Intent.STOP, now=at(3))

    found = ring.candidates()
    assert len(found) == 1
    assert found[0].acted.intent is Intent.MAIL_SEND, "the NEAREST preceding action"


def test_a_conversation_with_no_objection_reports_nothing() -> None:
    ring = RouteTelemetry()
    ring.record(Intent.MAIL_SEND, matched="gönder", now=T0)
    ring.record(Intent.SUMMARIZE, now=at(2))
    ring.record(Intent.NEXT_ITEM, now=at(4))
    assert ring.candidates() == []


def test_only_ACTING_intents_are_watched() -> None:
    """A narration that read the wrong paragraph is corrected by saying so; a mail that was
    sent is sent. The detector is about the second kind."""
    ring = RouteTelemetry()
    ring.record(Intent.SUMMARIZE, now=T0)
    ring.record(Intent.STOP, now=at(1))
    assert ring.candidates() == []

    assert Intent.MAIL_SEND in ACTING_INTENTS
    assert Intent.AMBIENT_POLICY_SET in ACTING_INTENTS
    assert Intent.SUMMARIZE not in ACTING_INTENTS
    assert Intent.CLOCK_QUERY not in ACTING_INTENTS


def test_an_unrouted_action_intent_is_not_an_action() -> None:
    """`routed` is False only for NONE, but the guard is explicit so a future intent that
    resolves without dispatching cannot become a phantom suspect."""
    ring = RouteTelemetry()
    ring.record(Intent.NONE, now=T0)
    ring.record(Intent.STOP, now=at(1))
    assert ring.candidates() == []


# ------------------------------------------------------- the app's own telemetry


def test_the_module_level_ring_is_reset_between_tests() -> None:
    """The autouse fixture in conftest. Without it, one test's resolutions would pair with
    the next test's "dur" and report a misroute nobody caused."""
    assert telemetry().events == ()
    telemetry().record(Intent.MAIL_SEND, matched="gönder", now=T0)
    assert len(telemetry().events) == 1
    reset_telemetry()
    assert telemetry().events == ()


def test_observe_notes_only_the_turn_that_completes_a_suspicion() -> None:
    """One misroute, one ledger row — not one per following turn."""
    from app.voice import route_telemetry

    written: list[str] = []

    def fake_note(db, candidate, *, now):  # noqa: ANN001, ARG001
        written.append(candidate.why)
        return True

    original = route_telemetry.note_candidate
    route_telemetry.note_candidate = fake_note  # type: ignore[assignment]
    try:
        assert route_telemetry.observe(
            None, Intent.MAIL_SEND, matched="gönder", tokens=(), session_id="s", now=T0
        ) is None
        assert written == []
        candidate = route_telemetry.observe(
            None, Intent.STOP, matched="dur", tokens=(), session_id="s", now=at(2)
        )
        assert candidate is not None
        assert len(written) == 1
        # A further turn does not re-report the same suspicion.
        route_telemetry.observe(
            None, Intent.SUMMARIZE, matched=None, tokens=(), session_id="s", now=at(4)
        )
        assert len(written) == 1
    finally:
        route_telemetry.note_candidate = original  # type: ignore[assignment]


def test_the_ledger_is_evidence_not_a_dependency() -> None:
    """A ledger that cannot be written must never take the conversation down with it."""
    from app.voice.route_telemetry import MisrouteCandidate, RouteEvent, note_candidate

    acted = RouteEvent(at=T0, intent=Intent.MAIL_SEND, matched="gönder", routed=True)
    reaction = RouteEvent(at=at(1), intent=Intent.STOP, matched="dur", routed=True)
    candidate = MisrouteCandidate(acted=acted, reaction=reaction, seconds=1.0)

    assert note_candidate(None, candidate, now=at(1)) is False

    class Exploding:
        def add(self, *_args, **_kwargs):
            raise RuntimeError("no database today")

    assert note_candidate(Exploding(), candidate, now=at(1)) is False


def test_the_service_actually_calls_it() -> None:
    """The defect shape this repository keeps finding: complete code with no caller.

    Telemetry nobody records is a module, not a measurement — so this reads the one
    production call site rather than trusting that it exists.
    """
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "app/voice/realtime_sessions/service.py"
    text = source.read_text("utf-8")
    assert "route_telemetry.observe(" in text
    # Right after the resolution, with the tokens the resolver produced — not a second
    # tokenisation that could disagree with the one that made the decision.
    assert "tokens=intent.tokens" in text
    assert "matched=intent.matched or None" in text


def test_a_suspicion_really_lands_in_the_ledger() -> None:
    """2026-09-17, production: every note was refused by the ledger's vocabulary
    (``voice.misroute_suspected`` was never registered) and only a warning was logged. The
    tests above replaced the writer or handed it no database, so none of them could see it.
    This one writes through the real ledger."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.ledger.models import ActivityEventRow
    from app.voice.route_telemetry import MisrouteCandidate, RouteEvent, note_candidate

    engine = create_engine("sqlite://")
    ActivityEventRow.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    acted = RouteEvent(at=T0, intent=Intent.MAIL_SEND, matched="gönder", routed=True)
    reaction = RouteEvent(at=at(1), intent=Intent.STOP, matched="dur", routed=True)
    candidate = MisrouteCandidate(acted=acted, reaction=reaction, seconds=1.0)

    assert note_candidate(db, candidate, now=at(1)) is True
    rows = db.execute(select(ActivityEventRow)).scalars().all()
    assert [row.event_type for row in rows] == ["voice.misroute_suspected"]
    assert rows[0].action == str(Intent.MAIL_SEND)
