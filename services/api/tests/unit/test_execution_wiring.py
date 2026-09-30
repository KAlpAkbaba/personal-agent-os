"""app.execution.wiring + the research/routine adapters (ADR-0213 PR 1b): a fake device
registry and the ledger's own table in SQLite."""

from __future__ import annotations

import dataclasses
import sys
import types
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.devices.selection import NoCapableDeviceError
from app.devices.types import DeviceView
from app.execution import wiring
from app.execution.rule import JobKind, Target
from app.ledger.models import ActivityEventRow
from app.research.target import choose_research_target
from app.routines.target import choose_routine_target

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
DENIED = "https://www.turkiye.gov.tr/giris"
PUBLIC = "https://example.com/haber"


def _device(name, *, platform="windows", presence="online", labels=(), aliases=()):
    return DeviceView(
        id=uuid.uuid4(),
        name=name,
        platform=platform,
        status="enrolled",
        presence=presence,
        capabilities=("browser.chrome",),
        enrolled_at=NOW,
        last_seen_at=NOW,
        labels=tuple(labels),
        aliases=tuple(aliases),
    )


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    ActivityEventRow.__table__.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s
    engine.dispose()


@pytest.fixture()
def registry(monkeypatch):
    devices: list[DeviceView] = []
    monkeypatch.setattr(wiring, "list_device_views", lambda db, runtime: list(devices))
    return devices


def _events(db):
    query = select(ActivityEventRow).order_by(ActivityEventRow.source_ref)
    rows = db.execute(query).scalars().all()
    return [(r.event_type, r.detail_json) for r in rows]


def _choose(db, kind=JobKind.RESEARCH, **kw):
    args = dict(
        spoken_target=None,
        url=PUBLIC,
        needs_signed_in_session=False,
        acting=False,
        scheduled=False,
        db=db,
        runtime=None,
    )
    args.update(kw)
    return wiring.choose(kind, **args)


def test_research_with_the_cloud_device_online_chooses_cloud_and_writes_selected(db, registry):
    registry.append(_device("bulut-1", platform="cloud"))
    decision = _choose(db)
    assert decision.target is Target.CLOUD
    assert [t for t, _ in _events(db)] == ["execution.selected"]


def test_research_with_the_cloud_offline_falls_back_to_the_enrolled_owner_chrome_once(
    db, registry
):
    registry.append(_device("bulut-1", platform="cloud", presence="offline"))
    registry.append(_device("ofis", labels=("owner_chrome",)))
    decision = _choose(db)
    assert decision.target is Target.OWNER_CHROME
    events = _events(db)
    assert [t for t, _ in events] == ["execution.fallback", "execution.selected"]
    assert events[0][1]["reason"] == "cloud_offline"


def test_a_fallback_is_not_written_when_the_cloud_is_simply_chosen(db, registry):
    registry.append(_device("bulut-1", platform="cloud"))
    registry.append(_device("ofis", labels=("owner_chrome",)))
    _choose(db)
    assert "execution.fallback" not in [t for t, _ in _events(db)]


def test_an_owner_chrome_device_that_is_offline_is_not_chosen(db, registry):
    registry.append(_device("bulut-1", platform="cloud", presence="offline"))
    registry.append(_device("ofis", labels=("owner_chrome",), presence="offline"))
    decision = _choose(db)
    assert decision.outcome == "refused"
    assert wiring.error_class_of(decision) == "no_capable_device"


def test_a_scheduled_job_never_reaches_the_rule_as_acting(db, registry, monkeypatch):
    registry.append(_device("bulut-1", platform="cloud"))
    seen = []
    real = wiring.rule.decide
    monkeypatch.setattr(wiring.rule, "decide", lambda r: (seen.append(r), real(r))[1])
    _choose(db, JobKind.BROWSER_TASK, scheduled=True, acting=True, url=DENIED)
    assert [r.acting for r in seen] == [False]
    assert seen[0].job_kind is JobKind.SCHEDULED


def test_a_scheduled_job_on_a_deny_listed_site_is_read_not_refused(db, registry):
    registry.append(_device("bulut-1", platform="cloud"))
    decision = _choose(db, JobKind.BROWSER_TASK, scheduled=True, acting=True, url=DENIED)
    assert decision.outcome == "selected"


def test_an_unscheduled_acting_job_on_a_deny_listed_site_is_refused(db, registry):
    registry.append(_device("bulut-1", platform="cloud"))
    decision = _choose(db, JobKind.BROWSER_TASK, acting=True, url=DENIED)
    assert (decision.outcome, decision.reason) == ("refused", "deny_listed_site")


def _allowlist(monkeypatch, allowed):
    module = types.ModuleType("app.execution.allowlist")
    module.acting_allowed = lambda url: allowed
    monkeypatch.setitem(sys.modules, "app.execution.allowlist", module)


def test_a_cloud_acting_step_outside_the_allow_list_is_refused(db, registry, monkeypatch):
    registry.append(_device("bulut-1", platform="cloud"))
    _allowlist(monkeypatch, False)
    decision = _choose(db, JobKind.BROWSER_TASK, acting=True)
    assert (decision.outcome, decision.reason) == ("refused", "not_on_owner_allow_list")
    assert [t for t, _ in _events(db)] == ["execution.refused"]


def test_a_cloud_acting_step_on_the_allow_list_is_selected(db, registry, monkeypatch):
    registry.append(_device("bulut-1", platform="cloud"))
    _allowlist(monkeypatch, True)
    assert _choose(db, JobKind.BROWSER_TASK, acting=True).target is Target.CLOUD


def test_a_missing_allow_list_module_counts_as_not_allowed(db, registry, monkeypatch):
    registry.append(_device("bulut-1", platform="cloud"))
    monkeypatch.setitem(sys.modules, "app.execution.allowlist", None)  # import -> ImportError
    decision = _choose(db, JobKind.BROWSER_TASK, acting=True)
    assert decision.reason == "not_on_owner_allow_list"


def test_a_reading_cloud_step_needs_no_allow_list(db, registry, monkeypatch):
    registry.append(_device("bulut-1", platform="cloud"))
    monkeypatch.setitem(sys.modules, "app.execution.allowlist", None)
    assert _choose(db, acting=False).target is Target.CLOUD


def test_no_target_at_all_is_the_no_capable_device_error_class(db, registry):
    decision = _choose(db)
    assert wiring.error_class_of(decision) == "no_capable_device"
    assert [t for t, _ in _events(db)][-1] == "execution.refused"


def test_a_policy_refusal_is_not_reported_as_no_capable_device(db, registry):
    registry.append(_device("bulut-1", platform="cloud"))
    decision = _choose(db, JobKind.BROWSER_TASK, acting=True, url=DENIED)
    assert wiring.error_class_of(decision) is None


def test_the_research_adapter_raises_no_capable_device_when_nothing_is_up(db, registry):
    with pytest.raises(NoCapableDeviceError) as err:
        choose_research_target(db, None)
    assert err.value.reason == "no_target_available"


def test_the_research_adapter_returns_the_decision_when_a_target_is_up(db, registry):
    registry.append(_device("bulut-1", platform="cloud"))
    assert choose_research_target(db, None).target is Target.CLOUD


def test_the_routine_adapter_is_read_only_and_cloud_only(db, registry):
    registry.append(_device("ofis", labels=("owner_chrome",)))
    with pytest.raises(NoCapableDeviceError):
        choose_routine_target(db, None)  # a routine never borrows the owner's Chrome
    registry.append(_device("bulut-1", platform="cloud"))
    assert choose_routine_target(db, None, url=DENIED).target is Target.CLOUD


def test_a_spoken_device_alias_that_names_no_device_is_refused(db, registry):
    registry.append(_device("ofis", aliases=("ofis",)))
    decision = _choose(db, spoken_target="evde")
    assert decision.outcome == "refused"


def _revoked(device):
    return dataclasses.replace(device, status="revoked")


def test_a_revoked_online_cloud_device_is_not_chosen(db, registry):
    registry.append(_revoked(_device("bulut-eski", platform="cloud")))
    decision = _choose(db)
    assert decision.outcome == "refused"
    assert wiring.error_class_of(decision) == "no_capable_device"


def test_a_revoked_owner_chrome_device_is_not_a_fallback(db, registry):
    registry.append(_device("bulut-1", platform="cloud", presence="offline"))
    registry.append(_revoked(_device("ofis-eski", labels=("owner_chrome",))))
    decision = _choose(db)
    assert decision.outcome == "refused"
    assert "execution.selected" not in [t for t, _ in _events(db)]


def test_a_blocked_cloud_run_asks_the_owner_and_is_not_a_missing_device(db, registry):
    registry.append(_device("bulut-1", platform="cloud"))
    decision = _choose(db, cloud_blocker="captcha")
    assert decision.outcome == "ask_owner"
    assert wiring.error_class_of(decision) is None
    events = _events(db)
    assert [t for t, _ in events] == ["execution.refused"]
    assert events[0][1]["ask_owner"] is True


def test_no_blocker_leaves_the_cloud_run_selected(db, registry):
    registry.append(_device("bulut-1", platform="cloud"))
    assert _choose(db, cloud_blocker=None).outcome == "selected"
