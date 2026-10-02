"""A routine's browser action runs where the execution_target rule says (ADR-0213 row 1,
order 2b): in the cloud, or nowhere.

``app.routines.target.choose_routine_target`` had no caller: ``BrokerDeviceAction`` picked a
routine's device with ``select_device`` alone, so a scheduled ``browser.navigate`` went to the
healthiest machine. These tests drive the real ``ActionDispatcher`` over the real
``BrokerDeviceAction`` and real ``devices`` rows (presence and the command client are the only
fakes) and read the ledger back.

The port is ONE object shared with the wake sequence, the operator and the voice path
(``app.main``); the last section holds that only the routine's own browser action asks the
rule - an alarm's ``browser.media_play`` in the cloud would wake nobody.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine

from app.devices.commands import CommandSucceeded
from app.execution.rule import Decision, JobKind, Target
from app.ledger.models import ActivityEventRow
from app.operator.launch_fallback import CAPABILITY_APP_LAUNCH
from app.routines import dispatch as dispatch_mod
from app.routines import target as routine_target
from app.routines.dispatch import (
    CAPABILITY_BROWSER_MEDIA_PLAY,
    CAPABILITY_BROWSER_NAVIGATE,
    CAPABILITY_DESKTOP_ALARM_START,
    ActionDispatcher,
    BriefingDelivery,
    BrokerDeviceAction,
)
from tests.device_command_support import FakeDeviceCommandClient
from tests.unit.test_execution_call_site_research import CLOUD_CAPS, Registry
from tests.unit.test_research_browser_activities import ALL_TABLES

PUBLIC = "https://example.com/haber"
DENIED = "https://www.turkiye.gov.tr/giris"
MACHINE_CAPS = ["browser.chrome", CAPABILITY_APP_LAUNCH, CAPABILITY_DESKTOP_ALARM_START]
FIRING = uuid.uuid4()


class _Briefing:
    def narrate(self, **_kw) -> BriefingDelivery:  # pragma: no cover - never a briefing here
        return BriefingDelivery(False, "unused")


class Stage:
    """The registry, the shared port and the routine dispatcher built on it."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.commands = FakeDeviceCommandClient(default_outcome=CommandSucceeded({"ok": True}))
        self.port = BrokerDeviceAction(
            session_factory=registry.factory, command_client=self.commands
        )
        self.dispatcher = ActionDispatcher(briefing=_Briefing(), device_action=self.port)

    def cloud(self, **kw) -> uuid.UUID:
        return self.registry.cloud(**kw)

    def mail(self, **kw) -> uuid.UUID:
        kw.setdefault("capabilities", MACHINE_CAPS)
        return self.registry.mail(**kw)

    def browser_action(self, action: str = "navigate", **detail):
        detail.setdefault("url", PUBLIC)
        return self.dispatcher.dispatch(
            routine_id=uuid.uuid4(),
            firing_id=FIRING,
            action={"kind": "browser_action", "detail": {"action": action, **detail}},
        )

    def scheduled(self, capability: str, payload: dict | None = None, *, targets=()):
        return self.port.scheduled(targets=targets).run(
            capability=capability,
            payload=dict(payload or {}),
            idempotency_key=f"unit:{uuid.uuid4()}",
            timeout_s=5.0,
        )

    def sent(self) -> list[tuple[uuid.UUID, str]]:
        return [(c.device_id, c.capability) for c in self.commands.calls]

    def ledger(self) -> list[tuple[str, dict]]:
        return self.registry.ledger()


@pytest.fixture()
def stage(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / f'routine_target_{uuid.uuid4().hex}.db'}"
    bootstrap = create_engine(url)
    for table in (*ALL_TABLES, ActivityEventRow.__table__):
        table.create(bootstrap)
    bootstrap.dispose()
    registry = Registry(url)
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: registry.broker)
    try:
        yield Stage(registry)
    finally:
        registry.engine.dispose()


# ------------------------------------------------- the routine's browser action: the cloud


def test_a_routines_browser_action_is_sent_to_the_cloud_device_and_the_ledger_says_so(
    stage,
) -> None:
    stage.mail()  # seen a second ago: health order (the old choice) takes IT
    cloud = stage.cloud()

    outcome = stage.browser_action("navigate")

    assert outcome.ok is True
    assert stage.sent() == [(cloud, CAPABILITY_BROWSER_NAVIGATE)]
    ledger = stage.ledger()
    assert [t for t, _ in ledger] == ["execution.selected"]
    assert ledger[0][1]["target"] == "cloud"
    assert ledger[0][1]["job_kind"] == "scheduled"
    assert ledger[0][1]["chain"] == ["cloud"]


def test_with_the_cloud_offline_nothing_is_sent_and_the_failure_says_why(stage) -> None:
    stage.cloud(online=False)
    stage.mail()  # online and able: never a fallback for a scheduled browser action

    outcome = stage.browser_action("navigate")

    assert stage.sent() == []
    assert outcome.ok is False and outcome.status == "failed"
    assert outcome.detail == {"error_class": "no_capable_device"}
    assert outcome.reason.startswith("no_capable_device:")
    assert "no_target_available" in outcome.reason
    ledger = stage.ledger()
    assert [t for t, _ in ledger] == ["execution.fallback", "execution.refused"]
    assert (ledger[0][1]["skipped_target"], ledger[0][1]["reason"]) == ("cloud", "cloud_offline")
    assert ledger[1][1]["reason"] == "no_target_available"


def test_the_port_reports_the_refusal_as_no_capable_device_with_the_rules_reason(stage) -> None:
    stage.cloud(online=False)
    stage.mail()

    result = stage.scheduled(CAPABILITY_BROWSER_NAVIGATE, {"url": PUBLIC})

    assert (result.ok, result.error_class, result.device_id) == (False, "no_capable_device", None)
    assert result.message == f"{routine_target.NO_TARGET_TR} (no_target_available)"
    assert stage.sent() == []


def test_a_cloud_that_does_not_advertise_the_operation_is_refused_not_selected(stage) -> None:
    stage.cloud(capabilities=[c for c in CLOUD_CAPS if c != CAPABILITY_BROWSER_NAVIGATE])
    stage.mail()

    outcome = stage.browser_action("navigate")

    assert stage.sent() == [] and outcome.ok is False
    ledger = stage.ledger()
    assert [t for t, _ in ledger] == ["execution.fallback", "execution.refused"]
    assert ledger[0][1]["reason"] == "cloud_capability_missing"


def test_a_revoked_cloud_device_still_holding_a_connection_is_not_a_target(stage) -> None:
    stage.cloud(status="revoked", seen_s_ago=0.5)
    stage.mail()

    assert stage.browser_action("navigate").ok is False
    assert stage.sent() == []


def test_a_named_machine_is_refused_for_a_scheduled_browser_action(stage) -> None:
    """ADR-0213: ``device`` is not in the scheduled chain - a word that names a machine is
    ``forced_target_not_allowed``, with the cloud up and the machine up."""
    stage.cloud()
    stage.mail()  # alias "ev"

    result = stage.scheduled(CAPABILITY_BROWSER_NAVIGATE, {"url": PUBLIC}, targets=("ev",))

    assert stage.sent() == []
    assert (result.ok, result.error_class) == (False, "no_capable_device")
    assert "forced_target_not_allowed" in result.message
    ledger = stage.ledger()
    assert [t for t, _ in ledger] == ["execution.refused"]
    assert ledger[0][1]["reason"] == "forced_target_not_allowed"
    assert ledger[0][1]["forced"] is True


def test_the_cloud_word_is_the_cloud(stage) -> None:
    cloud = stage.cloud()
    stage.mail()

    result = stage.scheduled(CAPABILITY_BROWSER_NAVIGATE, {"url": PUBLIC}, targets=("bulutta",))

    assert result.ok is True and result.device_id == cloud
    assert stage.ledger()[-1][1]["forced"] is True


@pytest.mark.parametrize("targets", [("bulutta", "ev"), ("ev", "bulutta")])
def test_a_machine_named_beside_the_cloud_word_is_still_refused(stage, targets) -> None:
    """The rule is asked about ONE word. When any of the words is a machine's it is that
    one, wherever it stands: "bulutta" said first does not carry "ev" into the cloud - on
    the run and on the probe alike."""
    stage.cloud()
    stage.mail()  # alias "ev"

    assert stage.port.scheduled(targets=targets).selection_for(CAPABILITY_BROWSER_NAVIGATE) is None
    assert stage.port.scheduled(targets=targets).can_run(CAPABILITY_BROWSER_NAVIGATE) is False
    assert stage.ledger() == []  # the probe wrote nothing

    result = stage.scheduled(CAPABILITY_BROWSER_NAVIGATE, {"url": PUBLIC}, targets=targets)

    assert stage.sent() == []
    assert (result.ok, result.error_class, result.device_id) == (False, "no_capable_device", None)
    assert "forced_target_not_allowed" in result.message
    ledger = stage.ledger()
    assert [t for t, _ in ledger] == ["execution.refused"]
    assert ledger[0][1]["reason"] == "forced_target_not_allowed"
    assert ledger[0][1]["forced"] is True


def test_the_payloads_url_is_what_the_rule_is_asked_about(stage, monkeypatch) -> None:
    stage.cloud()
    asked: list[str | None] = []
    real = routine_target.wiring.choose

    def recording(job_kind, **kw):
        asked.append(kw["url"])
        return real(job_kind, **kw)

    monkeypatch.setattr(routine_target.wiring, "choose", recording)

    stage.browser_action("navigate", url=PUBLIC)
    stage.scheduled("browser.back", {"session_id": "s"})  # carries none

    assert asked == [PUBLIC, None]


# ---------------------------------------------------------------------- the deny-list


def test_a_refusal_the_rule_returns_for_a_deny_listed_site_is_not_sent_anywhere(
    stage, monkeypatch
) -> None:
    """The mapping: a policy refusal comes back as a Decision (ADR-0220: returned, not
    raised) and is the same failed result, carrying its own reason."""
    cloud_only = (Target.CLOUD,)
    refused = Decision(JobKind.SCHEDULED, "refused", None, cloud_only, (), "deny_listed_site")
    monkeypatch.setattr(routine_target.wiring, "choose", lambda *_a, **_kw: refused)
    stage.cloud()
    stage.mail()

    outcome = stage.browser_action("navigate", url=DENIED)

    assert stage.sent() == []
    assert outcome.ok is False and outcome.detail == {"error_class": "no_capable_device"}
    assert "deny_listed_site" in outcome.reason


def test_reading_a_deny_listed_site_is_not_refused_by_the_rule_itself(stage) -> None:
    """ADR-0213's table: "acting on a deny-listed site -> refused; reading is not refused",
    and a scheduled job is never acting (the addendum; ``wiring.choose`` forces it). So the
    REAL rule sends a scheduled read of such a site to the cloud, where no session of the
    owner's exists and the worker's policy is READ + NAVIGATE."""
    cloud = stage.cloud()

    outcome = stage.browser_action("navigate", url=DENIED)

    assert outcome.ok is True and stage.sent() == [(cloud, CAPABILITY_BROWSER_NAVIGATE)]
    assert [t for t, _ in stage.ledger()] == ["execution.selected"]


# ------------------------------------------------------ desktop capabilities: unchanged


def test_an_app_launch_still_goes_to_the_machine_and_writes_no_execution_row(stage) -> None:
    stage.cloud()
    mail = stage.mail()

    result = stage.scheduled(CAPABILITY_APP_LAUNCH, {"app": "notepad"})

    assert result.ok is True and result.device_id == mail
    assert stage.sent() == [(mail, CAPABILITY_APP_LAUNCH)]
    assert stage.ledger() == []


def test_a_routines_alarm_still_rings_on_the_machine(stage) -> None:
    stage.cloud()
    mail = stage.mail()

    outcome = stage.dispatcher.dispatch(
        routine_id=uuid.uuid4(),
        firing_id=FIRING,
        action={"kind": "alarm", "detail": {"wake_volume": {"start": 0.1, "end": 0.5}}},
    )

    assert outcome.ok is True
    assert stage.sent() == [(mail, CAPABILITY_DESKTOP_ALARM_START)]
    assert stage.ledger() == []


# ------------------------------------------------------------- the probe and the run


def _probe(stage, capability: str, targets=()) -> uuid.UUID | None:
    selection = stage.port.scheduled(targets=targets).selection_for(capability)
    return selection.device.id if selection is not None else None


@pytest.mark.parametrize(
    ("cloud_kw", "capability", "targets"),
    [
        ({}, CAPABILITY_BROWSER_NAVIGATE, ()),
        ({"online": False}, CAPABILITY_BROWSER_NAVIGATE, ()),
        ({"status": "revoked"}, CAPABILITY_BROWSER_NAVIGATE, ()),
        ({"capabilities": ["browser.back"]}, CAPABILITY_BROWSER_NAVIGATE, ()),
        ({"policy": {"deny": ["browser.navigate"]}}, CAPABILITY_BROWSER_NAVIGATE, ()),
        ({}, CAPABILITY_BROWSER_NAVIGATE, ("ev",)),
        ({}, CAPABILITY_BROWSER_NAVIGATE, ("bulutta",)),
        ({"online": False}, CAPABILITY_BROWSER_NAVIGATE, ("bulutta",)),
        ({}, CAPABILITY_BROWSER_NAVIGATE, ("bulutta", "ev")),
        ({}, CAPABILITY_APP_LAUNCH, ()),
        ({}, CAPABILITY_APP_LAUNCH, ("ev",)),
    ],
)
def test_selection_for_reports_the_device_the_run_uses_and_writes_nothing(
    stage, cloud_kw, capability, targets
) -> None:
    stage.cloud(**cloud_kw)
    stage.mail()

    probed = _probe(stage, capability, targets)
    assert stage.ledger() == [] and stage.sent() == []  # a probe: no row, no command

    result = stage.scheduled(capability, {"url": PUBLIC}, targets=targets)

    assert result.device_id == probed
    assert result.ok is (probed is not None)
    assert stage.port.scheduled(targets=targets).can_run(capability) is (probed is not None)


def test_the_probe_of_a_browser_operation_is_the_cloud_or_nothing(stage) -> None:
    cloud = stage.cloud()
    mail = stage.mail()

    assert _probe(stage, CAPABILITY_BROWSER_NAVIGATE) == cloud
    assert _probe(stage, CAPABILITY_BROWSER_NAVIGATE, ("ev",)) is None
    assert _probe(stage, CAPABILITY_APP_LAUNCH) == mail
    stage.registry.broker.online.discard(cloud)
    assert _probe(stage, CAPABILITY_BROWSER_NAVIGATE) is None


# ---------------------------------------- the record is not the action; who asks the rule


def test_a_ledger_that_cannot_be_written_does_not_stop_the_action(tmp_path, monkeypatch) -> None:
    url = f"sqlite:///{tmp_path / 'no_ledger.db'}"
    bootstrap = create_engine(url)
    for table in ALL_TABLES:  # no activity_events
        table.create(bootstrap)
    bootstrap.dispose()
    registry = Registry(url)
    monkeypatch.setattr(dispatch_mod, "get_broker_runtime", lambda: registry.broker)
    try:
        stage = Stage(registry)
        cloud = stage.cloud()
        stage.mail()
        assert stage.browser_action("navigate").ok is True
        assert stage.sent() == [(cloud, CAPABILITY_BROWSER_NAVIGATE)]
    finally:
        registry.engine.dispose()


def test_the_shared_port_itself_does_not_ask_the_rule(stage, monkeypatch) -> None:
    """The wake sequence, the operator and the voice path hold this SAME object and send
    ``browser.*`` through it for an owner who is in the room. Only the routine's scheduled
    view asks the rule: the cloud advertises ``browser.media_play`` and is online here, and
    an alarm's music still plays on the machine."""

    def never(*_a, **_kw):
        raise AssertionError("the rule was asked by a caller that is not a routine")

    monkeypatch.setattr(dispatch_mod, "select_routine_device", never)
    assert CAPABILITY_BROWSER_MEDIA_PLAY in CLOUD_CAPS
    stage.registry.cloud_that_looks_like_a_machine(seen_s_ago=300.0)
    mail = stage.mail()

    for port in (stage.port, stage.port.bound_to([mail]), stage.port.bound_to(targets=("ev",))):
        result = port.run(
            capability=CAPABILITY_BROWSER_MEDIA_PLAY,
            payload={"url": PUBLIC},
            idempotency_key=f"unit:{uuid.uuid4()}",
            timeout_s=5.0,
        )
        assert result.ok is True and result.device_id == mail
    assert stage.port.selection_for(CAPABILITY_BROWSER_NAVIGATE).device.id == mail
    assert stage.ledger() == []


def test_a_routines_media_playback_still_plays_on_the_machine(stage) -> None:
    """``media_playback`` opens a VISIBLE window for an owner who is to hear it; the cloud
    worker has no display and refuses every profile but ``research``. It is not the
    routine's browser action and does not ask the rule (the ADR's open question)."""
    stage.cloud()
    mail = stage.mail()

    outcome = stage.dispatcher.dispatch(
        routine_id=uuid.uuid4(),
        firing_id=FIRING,
        action={"kind": "media_playback", "detail": {"url": PUBLIC}},
    )

    assert outcome.ok is True
    assert stage.sent() == [(mail, "browser.session_open"), (mail, CAPABILITY_BROWSER_NAVIGATE)]
    assert stage.ledger() == []


def test_a_port_that_has_no_scheduled_view_is_used_as_it_is() -> None:
    """Every fake port (and a port of another family) keeps working: the dispatcher asks
    for the scheduled view only of a port that offers one."""

    class Plain:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def run(self, *, capability, payload, idempotency_key, timeout_s):
            self.calls.append(capability)
            return dispatch_mod.DeviceRunResult(True)

    plain = Plain()
    outcome = ActionDispatcher(briefing=_Briefing(), device_action=plain).dispatch(
        routine_id=uuid.uuid4(),
        firing_id=FIRING,
        action={"kind": "browser_action", "detail": {"action": "navigate", "url": PUBLIC}},
    )
    assert outcome.ok is True and plain.calls == [CAPABILITY_BROWSER_NAVIGATE]
