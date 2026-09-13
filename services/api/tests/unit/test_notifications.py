"""B11 req 367/368/377/378/379/380/389: reaching the owner with the browser closed.

The in-app inbox read the FAKE push provider's in-memory ``deque``. A restart lost every
notification the owner had not seen, and in production there was nothing to read at all: a
real transport hands the message to Apple or Google and keeps no log, which is exactly why
the fake's log was standing in for one.

So the notification became a row this system owns, written before anything is attempted. The
fallback ladder is then a question about one row - which rung has not been tried - rather
than a chain of separate queues that can each lose their own copy, and the inbox is the FLOOR
of that ladder rather than a rung on it: recording the notification is putting it there.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.notifications import ladder as ladder_module
from app.notifications import service as notifications
from app.notifications import toast
from app.notifications.models import (
    CHANNEL_INBOX,
    CHANNEL_PUSH,
    CHANNEL_TOAST,
    LADDER,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
    NotificationRow,
)

# Mid-afternoon: comfortably outside quiet hours, so a test that is not about quiet hours
# never accidentally becomes one.
NOON = datetime(2026, 9, 13, 14, 0, tzinfo=UTC)
NIGHT = datetime(2026, 9, 13, 23, 30, tzinfo=UTC)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    NotificationRow.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _record(db, **overrides):
    base = {
        "kind": "artifact_ready",
        "title": "Rapor hazır",
        "body": "Üç klasör karşılaştırması bitti efendim.",
        "now": NOON,
    }
    base.update(overrides)
    return notifications.record(db, **base)


# ------------------------------------------------------- the row outlives everything


def test_a_notification_exists_before_anything_is_attempted(db) -> None:
    """The whole change. Nothing is asked of a transport until after this row is committed,
    so no transport failure can lose a notification - the worst case is the owner finding
    it in the inbox instead of hearing about it."""
    row = _record(db)

    assert row.id is not None
    assert row.delivered_at is None, "recording is not delivering"
    assert notifications.inbox(db) == [row]


def test_the_inbox_survives_a_restart(db) -> None:
    """The defect, as directly as it can be staged: the old inbox was a deque in a provider
    object, so this is what used to be lost."""
    row = _record(db)
    db.expunge_all()  # every object this process was holding is gone

    again = notifications.inbox(db)

    assert [n.id for n in again] == [row.id]
    assert again[0].body == "Üç klasör karşılaştırması bitti efendim."


def test_the_inbox_needs_no_transport_at_all(db) -> None:
    """A real push transport keeps no readable log, which is why the fake's was standing in
    for one. The inbox reads the table and asks nothing of anybody."""
    _record(db)

    import inspect

    source = inspect.getsource(notifications.inbox)
    assert "provider" not in source and "deliveries_for" not in source


# ------------------------------------------------------------------- priority (378)


def test_an_unknown_priority_becomes_normal_rather_than_being_stored(db) -> None:
    """A typo must not create a fourth level nothing knows how to treat."""
    assert _record(db, priority="EMERGENCY!!").priority == PRIORITY_NORMAL


def test_the_three_levels_are_the_whole_vocabulary() -> None:
    from app.notifications.models import PRIORITIES

    assert set(PRIORITIES) == {PRIORITY_URGENT, PRIORITY_NORMAL, PRIORITY_LOW}


# --------------------------------------------------------------- quiet hours (379)


def test_a_normal_notification_at_night_waits_until_morning(db) -> None:
    """Quiet hours are about NOISE. The row is in the inbox from the moment it is recorded;
    what waits is the attempt to interrupt."""
    row = _record(db, now=NIGHT)

    assert row.deferred_until is not None
    assert row in notifications.inbox(db), "deferred is not hidden"
    assert notifications.deliverable(db, now=NIGHT) == []


def test_an_urgent_notification_wakes_the_owner(db) -> None:
    """The only reason the priority levels exist, so it is deliberately the only exception."""
    row = _record(db, priority=PRIORITY_URGENT, now=NIGHT)

    assert row.deferred_until is None
    assert [n.id for n in notifications.deliverable(db, now=NIGHT)] == [row.id]


def test_the_deferred_one_is_delivered_once_the_night_is_over(db) -> None:
    row = _record(db, now=NIGHT)
    morning = NIGHT + timedelta(hours=9)

    assert [n.id for n in notifications.deliverable(db, now=morning)] == [row.id]


def test_quiet_hours_cross_midnight(db) -> None:
    """The normal case for a night, and the one a naive `start <= now < end` gets wrong."""
    assert notifications.in_quiet_hours(datetime(2026, 9, 13, 23, 30, tzinfo=UTC)) is True
    assert notifications.in_quiet_hours(datetime(2026, 9, 14, 3, 0, tzinfo=UTC)) is True
    assert notifications.in_quiet_hours(datetime(2026, 9, 14, 9, 0, tzinfo=UTC)) is False


def test_a_daytime_quiet_window_still_works() -> None:
    """The wrap-around handling must not have broken the ordinary case."""
    assert notifications.in_quiet_hours(
        datetime(2026, 9, 13, 14, 0, tzinfo=UTC),
        start=time(13, 0),
        end=time(15, 0),
    ) is True


# ----------------------------------------------------------------- grouping (380)


def test_a_newer_notification_supersedes_its_unread_sibling(db) -> None:
    """Two notices about one thing are one thing that happened twice."""
    first = _record(db, group_key="build:42", body="Derleme başladı.")
    second = _record(db, group_key="build:42", body="Derleme bitti.")

    db.refresh(first)
    assert first.superseded_at is not None
    assert [n.id for n in notifications.inbox(db)] == [second.id]


def test_a_notification_the_owner_already_read_is_never_superseded(db) -> None:
    """It is part of what they know. Marking it superseded to tidy a list would edit their
    history."""
    first = _record(db, group_key="build:42")
    notifications.mark_read(db, first.id, now=NOON)

    _record(db, group_key="build:42")

    db.refresh(first)
    assert first.superseded_at is None


def test_without_a_group_key_nothing_is_grouped(db) -> None:
    """Grouping is a claim that two things are the same thing; making it by accident hides
    one of them, so the default stands alone."""
    first = _record(db)
    _record(db)

    db.refresh(first)
    assert first.superseded_at is None
    assert len(notifications.inbox(db)) == 2


# -------------------------------------------------------------------- the ladder


class _Rung:
    def __init__(self, *, reaches: bool, up: bool = True) -> None:
        self.reaches = reaches
        self.up = up
        self.calls = 0

    def available(self) -> bool:
        return self.up

    def deliver(self, row) -> bool:  # noqa: ANN001 - test double
        self.calls += 1
        return self.reaches


def test_the_first_rung_that_works_is_the_one_used(db) -> None:
    row = _record(db)
    rungs = {CHANNEL_TOAST: _Rung(reaches=True), CHANNEL_INBOX: _Rung(reaches=True)}

    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert channel == CHANNEL_TOAST
    assert rungs[CHANNEL_INBOX].calls == 0, "the ladder stops when it reaches the owner"
    assert row.delivered_via == CHANNEL_TOAST


def test_a_failing_rung_falls_through_to_the_next(db) -> None:
    row = _record(db)
    toast_rung = _Rung(reaches=False)
    rungs = {CHANNEL_TOAST: toast_rung, CHANNEL_INBOX: _Rung(reaches=True)}

    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert toast_rung.calls == 1
    assert channel == CHANNEL_INBOX
    assert CHANNEL_TOAST in row.attempted_json


def test_a_rung_that_is_not_available_is_skipped_not_waited_for(db) -> None:
    """No device online means no toast. Standing still until one appears is how a notice
    about something urgent arrives tomorrow."""
    row = _record(db)
    offline = _Rung(reaches=True, up=False)
    rungs = {CHANNEL_TOAST: offline, CHANNEL_INBOX: _Rung(reaches=True)}

    channel = ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert offline.calls == 0
    assert channel == CHANNEL_INBOX


def test_a_rung_already_tried_is_never_tried_again(db) -> None:
    """Otherwise the ladder is a loop: toast fails, push fails, toast is first again."""
    row = _record(db)
    toast_rung = _Rung(reaches=False)
    rungs = {CHANNEL_TOAST: toast_rung, CHANNEL_INBOX: _Rung(reaches=True)}

    ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)
    ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    assert toast_rung.calls == 1


def test_a_rung_that_raises_is_a_rung_that_did_not_reach_anyone(db) -> None:
    """One channel's exception must not end the ladder - that would turn a transport bug
    into a lost notification, which is the thing this batch removes."""

    class _Exploding:
        def available(self) -> bool:
            return True

        def deliver(self, row) -> bool:  # noqa: ANN001, ARG002 - test double
            raise RuntimeError("the companion pipe is closed")

    row = _record(db)
    rungs = {CHANNEL_TOAST: _Exploding(), CHANNEL_INBOX: _Rung(reaches=True)}

    assert ladder_module.deliver_one(db, row, rungs=rungs, now=NOON) == CHANNEL_INBOX


# ------------------------------------------- the real rung, against the real port


def _port_double(outcome):  # noqa: ANN001, ANN202 - a spec-enforced test double
    """A device action built FROM ``DeviceActionPort`` itself.

    ``create_autospec`` copies the protocol's signature, so a keyword this rung invents
    raises ``TypeError`` here instead of being swallowed by the ladder's blanket except and
    recorded as "the device did not show it". The hand-rolled rung doubles above cannot see
    that: they accept whatever they are passed.
    """
    from unittest.mock import create_autospec

    from app.routines.dispatch import DeviceActionPort

    port = create_autospec(DeviceActionPort, instance=True)
    port.run.return_value = outcome
    return port


def test_the_toast_rung_calls_the_device_port_the_way_the_port_is_declared(db) -> None:
    """The bug this test exists for: the rung passed ``device_id=`` and ``trace_id=``, which
    ``DeviceActionPort.run`` does not take. Every toast would have raised TypeError inside
    the ladder's catch-all and been recorded as a device that did not show it - a channel
    that never worked, looking exactly like a device that was switched off."""
    from app.routines.dispatch import DeviceRunResult

    row = _record(db, title="Yedek alınamadı", body="Yedekleme başarısız oldu efendim.")
    port = _port_double(DeviceRunResult(True, result={"shown": True}))

    reached = ladder_module.ToastRung(device_action=port).deliver(row)

    assert reached is True
    kwargs = port.run.call_args.kwargs
    assert kwargs["capability"] == "desktop.notify"
    assert kwargs["idempotency_key"] == f"notify:{row.id}"
    assert kwargs["payload"]["notification_id"] == str(row.id)


def test_no_capable_device_is_a_rung_that_did_not_reach_anyone(db) -> None:
    """The port answers this in milliseconds without touching a device, which is why the
    rung attempts rather than asking first."""
    from app.routines.dispatch import DeviceRunResult

    row = _record(db)
    port = _port_double(DeviceRunResult(False, "no_capable_device", "cihaz yok"))

    assert ladder_module.ToastRung(device_action=port).deliver(row) is False


def test_a_device_that_answers_shown_false_did_not_reach_anyone(db) -> None:
    """Notifications off in Windows, or no interactive session. A real answer, not an
    error - and recording it as a delivery would be the lie this batch removes."""
    from app.routines.dispatch import DeviceRunResult

    row = _record(db)
    port = _port_double(DeviceRunResult(True, result={"shown": False, "reason": "no_session"}))

    assert ladder_module.ToastRung(device_action=port).deliver(row) is False


def test_the_capability_is_spelled_once_across_cloud_core(db) -> None:  # noqa: ARG001
    """Three places name this capability: the shared contract, the notifications half and
    the dispatch table the mirror test reads. Two spellings is a `no_capable_device` that
    reads like a device fault."""
    from app.notifications import toast as toast_contract
    from app.routines.dispatch import CAPABILITY_DESKTOP_NOTIFY

    assert toast_contract.contract()["capability"] == toast_contract.CAPABILITY
    assert CAPABILITY_DESKTOP_NOTIFY == toast_contract.CAPABILITY


def test_the_ladder_the_application_builds_has_a_toast_rung_when_it_has_a_device() -> None:
    """`default_rungs` is what `create_app` calls. A rung that only ever exists in a test's
    dictionary is the ladder written and unwired all over again."""
    rungs = ladder_module.default_rungs(device_action=object())

    assert set(rungs) == {CHANNEL_TOAST, CHANNEL_INBOX}
    assert set(ladder_module.default_rungs(device_action=None)) == {CHANNEL_INBOX}


def test_the_inbox_is_the_last_rung_and_the_floor() -> None:
    assert LADDER[-1] == CHANNEL_INBOX
    assert ladder_module.InboxRung().available() is True


def test_the_ladder_order_is_soonest_first() -> None:
    """Each rung reaches the owner sooner than the one after it; push before inbox because
    a phone buzzing beats a list they have to open."""
    assert LADDER == (CHANNEL_TOAST, "sound", CHANNEL_PUSH, CHANNEL_INBOX)


def test_delivering_twice_does_not_happen(db) -> None:
    row = _record(db)
    rungs = {CHANNEL_INBOX: _Rung(reaches=True)}

    ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)
    assert ladder_module.deliver_one(db, row, rungs=rungs, now=NOON) is None


# --------------------------------------------------------------- history (377)


def test_the_history_includes_what_never_reached_anyone(db) -> None:
    """"We never reached you about this" is the part of a delivery history that matters."""
    _record(db, body="ulaşılamadı")
    reached = _record(db, body="ulaşıldı")
    notifications.mark_delivered(db, reached, channel=CHANNEL_TOAST, now=NOON)

    entries = notifications.history(db)

    assert len(entries) == 2
    assert {e["delivered_via"] for e in entries} == {CHANNEL_TOAST, None}


def test_the_history_says_which_rungs_were_tried(db) -> None:
    row = _record(db)
    rungs = {CHANNEL_TOAST: _Rung(reaches=False), CHANNEL_INBOX: _Rung(reaches=True)}
    ladder_module.deliver_one(db, row, rungs=rungs, now=NOON)

    entry = notifications.history(db)[0]

    assert CHANNEL_TOAST in entry["attempted"]
    assert entry["delivered_via"] == CHANNEL_INBOX


# ----------------------------------------------------------- read vs delivered (368)


def test_delivered_is_not_read(db) -> None:
    """A toast that appeared while nobody was at the desk was delivered and not read."""
    row = _record(db)
    notifications.mark_delivered(db, row, channel=CHANNEL_TOAST, now=NOON)

    assert row.read_at is None
    assert [n.id for n in notifications.inbox(db, unread_only=True)] == [row.id]

    notifications.mark_read(db, row.id, now=NOON)
    assert notifications.inbox(db, unread_only=True) == []


def test_marking_read_twice_keeps_the_first_moment(db) -> None:
    row = _record(db)
    notifications.mark_read(db, row.id, now=NOON)
    first = row.read_at

    notifications.mark_read(db, row.id, now=NOON + timedelta(hours=1))

    db.refresh(row)
    # SQLite hands timestamps back naive where PostgreSQL keeps them aware; the instant is
    # what is asserted, not the driver's tzinfo.
    assert row.read_at.replace(tzinfo=UTC) == first


# ------------------------------------------------- the desktop.notify contract (369/370)


def test_both_halves_read_one_contract_file() -> None:
    """The four capabilities that were NOT written as a contract first each shipped with
    both suites green and neither half able to talk to the other."""
    contract = toast.contract()

    assert contract["capability"] == "desktop.notify"
    assert contract["request"]["title"]["max_chars"]
    assert contract["response"]["shown"]


def test_the_limits_are_read_from_the_contract_not_restated() -> None:
    """Restating 64 here would be the whole failure mode: the device enforces the
    contract's number and the Cloud Core enforces its memory of it."""
    import inspect

    source = inspect.getsource(toast)
    assert 'contract()["request"][field]["max_chars"]' in source


def test_a_title_too_long_is_refused_rather_than_truncated() -> None:
    """What the owner reads is the Cloud Core's decision; a silently shortened sentence is
    a different sentence."""
    with pytest.raises(toast.ToastRefused, match="title"):
        toast.build(notification_id=uuid.uuid4(), title="x" * 200, body="b")


def test_more_buttons_than_windows_will_show_are_refused() -> None:
    """Windows drops the extras silently, so asking for four and being told nothing is how
    the fourth action quietly stops existing."""
    actions = [{"id": f"a{i}", "label": "Tamam"} for i in range(4)]

    with pytest.raises(toast.ToastRefused, match="buttons"):
        toast.build(notification_id=uuid.uuid4(), title="t", body="b", actions=actions)


def test_an_action_id_is_a_closed_vocabulary() -> None:
    """Echoed back verbatim when pressed - it is the Cloud Core's own word, never free text
    from a model."""
    with pytest.raises(toast.ToastRefused, match="a-z0-9_"):
        toast.build(
            notification_id=uuid.uuid4(),
            title="t",
            body="b",
            actions=[{"id": "Aç Dosyayı", "label": "Aç"}],
        )


def test_anything_but_an_explicit_shown_true_is_not_a_delivery() -> None:
    """A missing field, a non-mapping, a device that answered something else - none of them
    is evidence the owner saw anything."""
    for result in ({"shown": False}, {}, None, "ok", {"shown": "true"}):
        assert toast.was_shown(result) is False
    assert toast.was_shown({"shown": True}) is True
