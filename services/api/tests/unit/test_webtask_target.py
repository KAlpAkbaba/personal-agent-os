"""Where a browser task runs (card cloud-task-loop-core): the execution_target rule, asked
for the ``browser_task`` kind, over a fake device registry and the ledger's own table.

The rule itself is ``tests/unit/test_execution_wiring.py``'s. What is held here is the
web task's adapter: the operations it asks a target to serve, that a task starts without
claiming to act (each write is judged at the gate, S4), the device the decision IS, and
the owner's words when there is nowhere to run.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.devices.types import DeviceView
from app.execution import allowlist_store, wiring
from app.execution import vocabulary as vocab
from app.ledger.models import ActivityEventRow
from app.webtask import target as task_target
from app.webtask.service import WebTaskError
from app.webtask.target import TASK_OPERATIONS, choose_task_target
from app.webtask.types import CAPABILITY_OF

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _device(name, *, platform="windows", presence="online", labels=(), capabilities=None):
    return DeviceView(
        id=uuid.uuid4(),
        name=name,
        platform=platform,
        status="enrolled",
        presence=presence,
        capabilities=tuple(capabilities or ("browser.chrome",)),
        enrolled_at=NOW,
        last_seen_at=NOW,
        labels=tuple(labels),
        aliases=(),
    )


def _cloud(presence="online"):
    # The cloud worker's hello names its operations; it carries no family marker.
    return _device(
        "cloud-worker", platform="cloud", presence=presence, capabilities=TASK_OPERATIONS
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
    # The owner's allow-list is EMPTY: a task that claimed to act at its start would be
    # refused in the cloud for it. It does not claim to (each write is judged at the gate).
    monkeypatch.setattr(allowlist_store, "effective_sites", lambda: ())
    return devices


def _events(db):
    rows = db.execute(select(ActivityEventRow).order_by(ActivityEventRow.source_ref)).scalars()
    return [(r.event_type, r.source, r.research_job_id, r.detail_json) for r in rows]


def test_the_operations_are_the_loops_own() -> None:
    assert TASK_OPERATIONS[:3] == ("browser.session_open", "browser.tab_new", "browser.observe")
    assert set(CAPABILITY_OF.values()) <= set(TASK_OPERATIONS)
    assert len(set(TASK_OPERATIONS)) == len(TASK_OPERATIONS)


def test_bulutta_with_the_cloud_up_runs_in_the_cloud(db, registry) -> None:
    cloud = _cloud()
    registry.extend([cloud, _device("pc", labels=("owner_chrome",))])

    chosen = choose_task_target(db, None, spoken_target="bulutta", allowed_hosts=())

    assert chosen.target == "cloud"
    assert chosen.device_id == cloud.id
    events = _events(db)
    assert [e[0] for e in events] == [vocab.EXECUTION_SELECTED]
    _type, source, research_job_id, detail = events[0]
    assert source == "execution" and research_job_id is None
    assert detail["target"] == "cloud" and detail["job_kind"] == "browser_task"


def test_bulutta_with_the_cloud_down_says_so_in_turkish(db, registry) -> None:
    registry.extend([_cloud(presence="offline"), _device("pc", labels=("owner_chrome",))])

    with pytest.raises(WebTaskError) as caught:
        choose_task_target(db, None, spoken_target="bulutta", allowed_hosts=())

    assert caught.value.reason == "no_capable_device"
    assert str(caught.value) == "Bulut şu anda çevrimiçi değil."


def test_bulutta_with_a_cloud_that_cannot_serve_says_that(db, registry) -> None:
    registry.append(_cloud_without_writes())
    with pytest.raises(WebTaskError) as caught:
        choose_task_target(db, None, spoken_target="bulutta", allowed_hosts=())
    assert caught.value.reason == "no_capable_device"
    assert str(caught.value) == "Bulut bu işi şu anda yapamıyor."


def _cloud_without_writes():
    return _device(
        "cloud-worker",
        platform="cloud",
        capabilities=("browser.session_open", "browser.tab_new", "browser.observe"),
    )


def test_no_word_and_the_cloud_down_falls_back_to_the_owners_chrome(db, registry) -> None:
    pc = _device("pc", labels=("owner_chrome",))
    registry.extend([_cloud(presence="offline"), pc])

    chosen = choose_task_target(db, None, spoken_target=None, allowed_hosts=())

    assert chosen.target == "owner_chrome" and chosen.device_id == pc.id
    kinds = [e[0] for e in _events(db)]
    assert kinds == [vocab.EXECUTION_FALLBACK, vocab.EXECUTION_SELECTED]
    assert all(e[1] == "execution" and e[2] is None for e in _events(db))


def test_the_device_target_is_the_machine_selection_picks(db, registry) -> None:
    pc = _device("pc")
    registry.extend([_cloud(presence="offline"), pc])
    chosen = choose_task_target(db, None, spoken_target=None, allowed_hosts=())
    assert chosen.target == "device" and chosen.device_id == pc.id


def test_nowhere_to_run_is_no_capable_device(db, registry) -> None:
    with pytest.raises(WebTaskError) as caught:
        choose_task_target(db, None, spoken_target=None, allowed_hosts=())
    assert caught.value.reason == "no_capable_device"
    assert str(caught.value) == "Görevi çalıştıracak uygun bir hedef yok."


def test_the_task_is_chosen_without_claiming_to_act(db, registry, monkeypatch) -> None:
    """S4: acting is decided step by step at the gate. A decision taken with acting=True
    would ask the empty allow-list now and refuse the cloud for a task that may only read."""
    seen: dict[str, object] = {}
    real = wiring.choose

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(task_target.wiring, "choose", spy)
    registry.append(_cloud())
    choose_task_target(db, None, spoken_target="bulutta", allowed_hosts=("example.com",))
    assert seen["acting"] is False and seen["scheduled"] is False and seen["url"] is None
    assert seen["needs_signed_in_session"] is False
    assert tuple(seen["capabilities"]) == TASK_OPERATIONS
