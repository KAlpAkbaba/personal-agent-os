"""B11-toast (rows 369/370): a toast button press, on its way back to the Cloud Core.

The device shows a real Windows toast with the buttons ``app.notifications.toast.build``
sent. When the owner presses one, the companion queues ``{notification_id, action_id,
pressed_at}`` and the Device Service carries it in the heartbeat's ``status`` as
``notify_actions`` - each press in a few consecutive reports, because a heartbeat is
fire-and-forget. These tests hold the Cloud Core's half: the list is read strictly, a press is
recorded ONCE on the notification row and only for an action that row offered, and the two
halves agree on the numbers because both read ``packages/protocol/desktop-notify.json``.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.ambient import ingest as ambient_ingest
from app.ambient.holdoff import HoldoffRegistry
from app.devices.status import DeviceStatusRegistry, parse_status
from app.notifications import service as notifications
from app.notifications import toast
from app.notifications.models import NotificationRow
from tests.alarms_support import build_session_factory

REPO = Path(__file__).resolve().parents[4]
AGENT = REPO / "devices" / "windows-agent" / "src"
NOW = datetime(2026, 9, 17, 11, 0, tzinfo=UTC)
DEVICE = uuid.uuid4()


@pytest.fixture()
def session():
    factory = build_session_factory()
    NotificationRow.__table__.create(factory.kw["bind"], checkfirst=True)
    with factory() as s:
        yield s


def _row(session, actions=None, target=DEVICE) -> NotificationRow:
    row = _plain_row(session, actions)
    if target is not None:
        assert notifications.note_toast_target(row, target)
        session.commit()
    return row


def _plain_row(session, actions=None) -> NotificationRow:
    return notifications.record(
        session,
        kind="task.completed",
        title="Rapor hazır",
        body="Üç klasör karşılaştırması bitti efendim.",
        data={
            "actions": actions
            if actions is not None
            else [{"id": "open", "label": "Aç"}, {"id": "snooze", "label": "Ertele"}]
        },
        now=NOW,
    )


def _press(notification_id, action_id="open", pressed_at=NOW) -> dict:
    return {
        "notification_id": str(notification_id),
        "action_id": action_id,
        "pressed_at": pressed_at.isoformat().replace("+00:00", "Z"),
    }


# ------------------------------------------------------------- the two halves agree


def test_the_transport_numbers_are_the_ones_the_device_enforces() -> None:
    """Read the device's source rather than restating it: both halves green and unable to
    talk to each other is this repository's most repeated contract defect."""
    queue = (AGENT / "PagentOS.SessionCompanion" / "Notify" / "NotifyActionQueue.cs").read_text(
        encoding="utf-8"
    )
    heartbeat = (AGENT / "PagentOS.Agent.Core" / "Protocol" / "HeartbeatStatus.cs").read_text(
        encoding="utf-8"
    )
    assert f"public const int MaxEntries = {toast.action_max_entries()};" in queue
    assert f"public const int Transmissions = {toast.action_transmissions()};" in queue
    assert f"public const int MaxNotifyActions = {toast.action_max_entries()};" in heartbeat
    assert f'public const string NotifyActions = "{toast.action_field()}";' in heartbeat


def test_the_schema_declares_the_list_the_contract_names() -> None:
    schema = json.loads(
        (REPO / "packages" / "schemas" / "device-protocol.schema.json").read_text(encoding="utf-8")
    )
    declared = schema["$defs"]["deviceStatus"]["properties"][toast.action_field()]
    assert declared["maxItems"] == toast.action_max_entries()
    assert set(declared["items"]["required"]) == {"notification_id", "action_id", "pressed_at"}
    assert declared["items"]["additionalProperties"] is False
    limit = toast.contract()["request"]["actions"]["item"]["id"]["max_chars"]
    assert declared["items"]["properties"]["action_id"]["pattern"] == f"^[a-z0-9_]{{1,{limit}}}$"


def test_every_answer_key_the_device_writes_is_in_the_contract() -> None:
    source = (AGENT / "PagentOS.SessionCompanion" / "Notify" / "NotifyCapabilities.cs").read_text(
        encoding="utf-8"
    )
    start = source.index("private static JsonObject Answer(ToastOutcome")
    body = source[start : source.index("internal static bool TryParse", start)]
    written = set(re.findall(r'\["([a-z_]+)"\]', body))
    assert written == set(toast.contract()["response"])


def test_the_surface_and_setting_tokens_are_the_devices_own() -> None:
    source = (AGENT / "PagentOS.SessionCompanion" / "Notify" / "ToastXml.cs").read_text(
        encoding="utf-8"
    )
    response = toast.contract()["response"]
    for value in response["surface"]["values"] + response["notifier_setting"]["values"]:
        assert f'= "{value}";' in source, value


# ------------------------------------------------------------ reading the list strictly


def test_a_well_formed_press_is_read() -> None:
    nid = uuid.uuid4()
    (press,) = toast.parse_action_presses([_press(nid, "snooze")])
    assert press.notification_id == nid
    assert press.action_id == "snooze"
    assert press.pressed_at == NOW


@pytest.mark.parametrize(
    "entry",
    [
        {
            "notification_id": "the-latest",
            "action_id": "open",
            "pressed_at": "2026-09-17T11:00:00Z",
        },
        {
            "notification_id": str(uuid.UUID(int=1)),
            "action_id": "Aç",
            "pressed_at": "2026-09-17T11:00:00Z",
        },
        # `$` also matches before a trailing newline; the device refuses this id, so must we.
        {
            "notification_id": str(uuid.UUID(int=1)),
            "action_id": "open\n",
            "pressed_at": "2026-09-17T11:00:00Z",
        },
        {
            "notification_id": str(uuid.UUID(int=1)),
            "action_id": "x" * 33,
            "pressed_at": "2026-09-17T11:00:00Z",
        },
        {"notification_id": str(uuid.UUID(int=1)), "action_id": "open", "pressed_at": "yesterday"},
        {"notification_id": str(uuid.UUID(int=1)), "action_id": "open"},
        {
            "notification_id": str(uuid.UUID(int=1)),
            "action_id": 7,
            "pressed_at": "2026-09-17T11:00:00Z",
        },
        "open",
    ],
)
def test_a_malformed_press_is_dropped_not_repaired(entry) -> None:
    assert toast.parse_action_presses([entry]) == ()


def test_the_list_is_bounded_and_anything_but_a_list_is_nothing() -> None:
    nid = uuid.uuid4()
    many = [_press(nid, "open", NOW + timedelta(seconds=i)) for i in range(40)]
    assert len(toast.parse_action_presses(many)) == toast.action_max_entries()
    assert toast.parse_action_presses({"open": True}) == ()
    assert toast.parse_action_presses(None) == ()


def test_the_heartbeat_status_carries_the_presses_and_older_statuses_carry_none() -> None:
    nid = uuid.uuid4()
    status = parse_status(DEVICE, {"input_idle_s": 3, toast.action_field(): [_press(nid)]}, now=NOW)
    assert status is not None
    assert [p.action_id for p in status.notify_actions] == ["open"]
    older = parse_status(DEVICE, {"input_idle_s": 3}, now=NOW)
    assert older is not None and older.notify_actions == ()


# ------------------------------------------------ the payload the Cloud Core sends (regression)


def test_build_refuses_an_action_id_with_a_trailing_newline() -> None:
    """Regression (B11-toast review): ``re.match`` with ``$`` accepted ``"open\\n"``, which the
    device refuses as invalid_payload - the ladder would have spent its toast rung on a
    payload this side should never have sent."""
    with pytest.raises(toast.ToastRefused):
        toast.build(
            notification_id=uuid.uuid4(),
            title="t",
            body="b",
            actions=[{"id": "open\n", "label": "Aç"}],
        )


def test_build_refuses_an_action_id_longer_than_the_device_accepts() -> None:
    with pytest.raises(toast.ToastRefused):
        toast.build(
            notification_id=uuid.uuid4(),
            title="t",
            body="b",
            actions=[{"id": "x" * 33, "label": "Aç"}],
        )


def test_the_surface_is_read_only_from_a_shown_answer_and_only_as_a_contract_token() -> None:
    assert toast.surface({"shown": True, "surface": "toast"}) == "toast"
    assert toast.surface({"shown": True, "surface": "balloon"}) == "balloon"
    assert toast.surface({"shown": True, "surface": "hologram"}) is None
    assert toast.surface({"shown": False, "surface": "toast"}) is None
    assert toast.surface("toast") is None


# --------------------------------------------------------------- recording the press


def test_a_press_is_recorded_once_on_the_row(session) -> None:
    row = _row(session)
    first = notifications.record_action(session, row.id, "open", NOW, device_id=DEVICE, now=NOW)
    again = notifications.record_action(session, row.id, "open", NOW, device_id=DEVICE, now=NOW)

    assert first == notifications.PRESS_RECORDED
    assert again == notifications.PRESS_DUPLICATE
    stored = session.get(NotificationRow, row.id)
    (press,) = stored.data_json["actions_pressed"]
    assert press["action_id"] == "open"
    assert press["pressed_at"] == "2026-09-17T11:00:00Z"
    assert press["device_id"] == str(DEVICE)
    # Pressing a button is not the inbox's "read".
    assert stored.read_at is None
    assert notifications.history(session)[0]["actions_pressed"] == ["open"]


def test_a_press_for_an_action_the_row_never_offered_is_refused(session) -> None:
    row = _row(session)
    assert (
        notifications.record_action(
            session, row.id, "delete_everything", NOW, device_id=DEVICE
        )
        == notifications.PRESS_NOT_OFFERED
    )
    assert "actions_pressed" not in session.get(NotificationRow, row.id).data_json


def test_a_press_for_an_unknown_notification_is_refused(session) -> None:
    assert (
        notifications.record_action(session, uuid.uuid4(), "open", NOW, device_id=DEVICE)
        == notifications.PRESS_UNKNOWN_NOTIFICATION
    )


def test_presses_on_one_row_are_bounded(session) -> None:
    row = _row(session)
    outcomes = [
        notifications.record_action(
            session, row.id, "open", NOW + timedelta(seconds=i), device_id=DEVICE
        )
        for i in range(notifications.MAX_PRESSES_PER_NOTIFICATION + 2)
    ]
    assert (
        outcomes.count(notifications.PRESS_RECORDED) == notifications.MAX_PRESSES_PER_NOTIFICATION
    )
    assert outcomes[-1] == notifications.PRESS_LIMIT


# ------------------------------------------------- through the real heartbeat intake


def test_the_heartbeat_path_records_a_press_that_arrives_in_three_reports_once(session) -> None:
    """The device repeats each press in consecutive reports; a Cloud Core restart in the
    middle (a fresh registry) must not record it twice either."""
    row = _row(session)
    status = {"input_idle_s": 400.0, toast.action_field(): [_press(row.id, "snooze")]}
    statuses = DeviceStatusRegistry()
    holdoffs = HoldoffRegistry()

    results = [
        ambient_ingest.ingest_status(
            session, DEVICE, status, statuses=statuses, holdoffs=holdoffs, now=NOW
        )
        for _ in range(toast.action_transmissions())
    ]
    restarted = ambient_ingest.ingest_status(
        session, DEVICE, status, statuses=DeviceStatusRegistry(), holdoffs=holdoffs, now=NOW
    )

    assert results[0].notify_actions_recorded == (f"{row.id}:snooze",)
    assert all(r.notify_actions_recorded == () for r in results[1:])
    assert restarted.notify_actions_recorded == ()
    stored = session.get(NotificationRow, row.id)
    assert [p["action_id"] for p in stored.data_json["actions_pressed"]] == ["snooze"]


def test_the_heartbeat_path_ignores_a_press_the_row_did_not_offer(session) -> None:
    row = _row(session, actions=[{"id": "open", "label": "Aç"}])
    status = {toast.action_field(): [_press(row.id, "snooze")]}
    result = ambient_ingest.ingest_status(
        session,
        DEVICE,
        status,
        statuses=DeviceStatusRegistry(),
        holdoffs=HoldoffRegistry(),
        now=NOW,
    )
    assert result.notify_actions_recorded == ()
    assert "actions_pressed" not in session.get(NotificationRow, row.id).data_json


# --------------------------------------- bound to the device that showed it (security review)

OTHER_DEVICE = uuid.uuid4()


def test_a_press_from_another_device_is_refused(session) -> None:
    """Security review (2026-09-17, Medium): action ids are guessable, so a compromised second
    device must not be able to press a button on a notice only the target device showed."""
    row = _row(session)
    outcome = notifications.record_action(session, row.id, "open", NOW, device_id=OTHER_DEVICE)
    assert outcome == notifications.PRESS_WRONG_DEVICE
    assert "actions_pressed" not in session.get(NotificationRow, row.id).data_json


def test_a_press_from_the_target_device_is_accepted(session) -> None:
    row = _row(session)
    outcome = notifications.record_action(session, row.id, "open", NOW, device_id=DEVICE)
    assert outcome == notifications.PRESS_RECORDED


def test_every_device_the_toast_was_sent_to_may_press_and_no_other(session) -> None:
    row = _row(session)
    assert notifications.note_toast_target(row, OTHER_DEVICE)
    session.commit()
    third = uuid.uuid4()
    assert (
        notifications.record_action(session, row.id, "open", NOW, device_id=OTHER_DEVICE)
        == notifications.PRESS_RECORDED
    )
    assert (
        notifications.record_action(session, row.id, "snooze", NOW, device_id=third)
        == notifications.PRESS_WRONG_DEVICE
    )


def test_a_press_with_no_device_or_for_a_row_with_no_target_is_refused(session) -> None:
    bound = _row(session)
    unbound = _row(session, target=None)
    assert (
        notifications.record_action(session, bound.id, "open", NOW, device_id=None)
        == notifications.PRESS_WRONG_DEVICE
    )
    assert (
        notifications.record_action(session, unbound.id, "open", NOW, device_id=DEVICE)
        == notifications.PRESS_WRONG_DEVICE
    )


def test_targets_are_deduplicated_bounded_and_only_real_ids(session) -> None:
    row = _row(session)
    assert notifications.note_toast_target(row, DEVICE)
    assert notifications.note_toast_target(row, None) is False
    assert notifications.note_toast_target(row, "not-a-device") is False
    for _ in range(notifications.MAX_TOAST_TARGETS + 3):
        notifications.note_toast_target(row, uuid.uuid4())
    targets = row.data_json[notifications.TOAST_TARGETS_KEY]
    assert len(targets) == notifications.MAX_TOAST_TARGETS
    assert len(set(targets)) == len(targets)


def test_the_heartbeat_path_refuses_a_press_from_a_device_that_did_not_show_it(session) -> None:
    row = _row(session)
    status = {toast.action_field(): [_press(row.id, "open")]}
    result = ambient_ingest.ingest_status(
        session,
        OTHER_DEVICE,
        status,
        statuses=DeviceStatusRegistry(),
        holdoffs=HoldoffRegistry(),
        now=NOW,
    )
    assert result.notify_actions_recorded == ()
    assert "actions_pressed" not in session.get(NotificationRow, row.id).data_json


class _Port:
    def __init__(self, result, device_id) -> None:
        self._result = result
        self._device_id = device_id

    def run(self, **_kwargs):
        from app.routines.dispatch import DeviceRunResult

        return DeviceRunResult(True, result=self._result, device_id=self._device_id)


def test_the_ladder_records_the_device_that_showed_the_toast(session) -> None:
    from app.notifications import ladder

    row = _plain_row(session)
    rungs = ladder.default_rungs(device_action=_Port({"shown": True}, DEVICE))
    assert ladder.deliver_one(session, row, rungs=rungs, now=NOW) == "toast"

    session.expire_all()
    stored = session.get(NotificationRow, row.id)
    assert stored.data_json[notifications.TOAST_TARGETS_KEY] == [str(DEVICE)]
    assert (
        notifications.record_action(session, row.id, "open", NOW, device_id=DEVICE)
        == notifications.PRESS_RECORDED
    )


def test_a_toast_that_was_not_shown_records_no_target(session) -> None:
    from app.notifications import ladder

    row = _plain_row(session)
    rungs = ladder.default_rungs(
        device_action=_Port({"shown": False, "reason": "notifications_disabled"}, DEVICE)
    )
    ladder.deliver_one(session, row, rungs=rungs, now=NOW)
    session.expire_all()
    assert notifications.TOAST_TARGETS_KEY not in session.get(NotificationRow, row.id).data_json


def test_the_broker_port_says_which_device_it_sent_the_command_to(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.devices.commands import CommandFailed, CommandSucceeded
    from app.routines import dispatch as dispatch_mod

    target = uuid.uuid4()
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: object())
    monkeypatch.setattr(dispatch_mod, "list_device_views", lambda session, runtime: [])
    monkeypatch.setattr(
        dispatch_mod,
        "select_device",
        lambda views, capability: SimpleNamespace(device=SimpleNamespace(id=target)),
    )

    class _Session:
        def close(self) -> None:
            pass

    outcomes = (CommandSucceeded(result={"shown": True}), CommandFailed("timeout", "slow", True))
    for outcome in outcomes:
        client = SimpleNamespace(run=lambda _o=outcome, **_k: _o)
        action = dispatch_mod.BrokerDeviceAction(session_factory=_Session, command_client=client)
        result = action.run(
            capability=toast.CAPABILITY, payload={}, idempotency_key="k", timeout_s=1.0
        )
        assert result.device_id == target
