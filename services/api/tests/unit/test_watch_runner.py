"""The watch runner (watch-engine): one reading in flight, edge-triggered, quiet when unchanged.

A fake reader stands for the cloud device (the reader has its own tests); the runner writes the
reading, moves the baseline, asks the model only when it must, and tells the owner through
``app.notifications.service.record`` - one line, ``group_key`` ``watch:<id>``.
"""

from __future__ import annotations

import contextlib
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from app.watch import compare, extract, runner, service
from app.watch.models import Watch, WatchReading
from app.watch.reader import Observation
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.assistant_chat import ChatAnswer
from app.ledger.models import ActivityEventRow
from app.notifications.models import NotificationRow

NOON = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)  # 15:00 in Istanbul - outside quiet hours


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        Watch.__table__,
        WatchReading.__table__,
        NotificationRow.__table__,
        ActivityEventRow.__table__,
    ):
        table.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.research.destination.resolve_hostname", lambda host: ["93.184.216.34"])


class FakeReader:
    """Hands out the next observation; records every read."""

    def __init__(self, *observations: Observation) -> None:
        self.queue = list(observations)
        self.reads: list[str] = []

    def read(self, db: Session, *, url: str, selector: str | None, watch_id: uuid.UUID):
        self.reads.append(url)
        return self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]


class FakeProvider:
    name = "fake"
    configured = True

    def __init__(self, speech: str) -> None:
        self.speech = speech
        self.questions: list[str] = []

    def answer(self, question, *, history, now_tr, about_owner=""):
        self.questions.append(question)
        return ChatAnswer(speech=self.speech, ok=True)


def page(text: str) -> Observation:
    return Observation(ok=True, text_sha256=compare.text_sha256(text), text=text)


def failed(reason: str = "Sayfa giriş istiyor.") -> Observation:
    return Observation(ok=False, reason_tr=reason, error_class="auth_wall")


def _scope(factory):
    @contextlib.contextmanager
    def scope():
        with factory() as session:
            yield session

    return scope


def _watch(factory, condition: str = "changed", **kw) -> uuid.UUID:
    with factory() as db:
        view = service.create_watch(
            db,
            url="https://www.home-assistant.io/blog/",
            condition=condition,
            label=kw.pop("label", "Home Assistant"),
            now=NOON,
            **kw,
        )
        db.commit()
        return uuid.UUID(view.id)


def _apply(factory, watch_id, observation, now, *, provider=None, announcer=None):
    with factory() as db:
        watch = db.get(Watch, watch_id)
        return runner.apply_observation(
            db, watch, observation, now=now, provider=provider, announcer=announcer
        )


def _notifications(factory) -> list[NotificationRow]:
    with factory() as db:
        return list(
            db.execute(select(NotificationRow).order_by(NotificationRow.created_at)).scalars()
        )


def _count(factory, model) -> int:
    with factory() as db:
        return db.execute(select(func.count()).select_from(model)).scalar_one()


def _ledger(factory) -> list[ActivityEventRow]:
    with factory() as db:
        return list(db.execute(select(ActivityEventRow)).scalars())


# ------------------------------------------------------------------ (1) compare through the runner


def test_baseline_then_same_then_changed(factory) -> None:
    wid = _watch(factory)
    provider = FakeProvider("19.000")
    first = _apply(factory, wid, page("sürüm 2026.9"), NOON, provider=provider)
    assert first is None
    assert _notifications(factory) == []
    same = _apply(
        factory, wid, page("sürüm   2026.9 "), NOON + timedelta(hours=6), provider=provider
    )
    assert same is None
    assert _notifications(factory) == []
    assert provider.questions == []  # zero model calls
    change = _apply(factory, wid, page("sürüm 2026.10"), NOON + timedelta(hours=12))
    assert change is not None and change.outcome == "changed"
    rows = _notifications(factory)
    assert len(rows) == 1
    assert rows[0].kind == "watch.changed"
    assert rows[0].group_key == f"watch:{wid}"
    assert len(rows[0].body) <= 120 and "\n" not in rows[0].body
    with factory() as db:
        outcomes = [
            r.outcome
            for r in db.execute(select(WatchReading).order_by(WatchReading.read_at)).scalars()
        ]
    assert outcomes == ["same", "same", "changed"]
    assert [r.event_type for r in _ledger(factory)] == ["watch.changed"]


def test_the_same_reading_processed_twice_produces_nothing_the_second_time(factory) -> None:
    wid = _watch(factory)
    _apply(factory, wid, page("a"), NOON)
    at = NOON + timedelta(hours=6)
    assert _apply(factory, wid, page("b"), at) is not None
    assert _apply(factory, wid, page("b"), at) is None
    assert _count(factory, WatchReading) == 2
    assert len(_notifications(factory)) == 1


# ------------------------------------------------------------- (2) conditions through the runner


def test_met_at_the_baseline_notifies_once_with_zaten(factory) -> None:
    wid = _watch(factory, "number_below:20000", selector=".price")
    change = _apply(factory, wid, page("19.499 TL"), NOON)
    assert change is not None and change.outcome == "condition_met"
    assert "Şu an zaten" in change.line_tr
    assert change.value == pytest.approx(19499)
    again = _apply(factory, wid, page("19.299 TL"), NOON + timedelta(hours=6))
    assert again is None
    rows = _notifications(factory)
    assert [r.kind for r in rows] == ["watch.condition_met"]


def test_contains_turns_true_once(factory) -> None:
    wid = _watch(factory, "contains:kararlı sürüm")
    assert _apply(factory, wid, page("beta sürüm"), NOON) is None
    hit = _apply(factory, wid, page("KARARLI SÜRÜM çıktı"), NOON + timedelta(hours=6))
    assert hit is not None and hit.outcome == "condition_met"
    assert (
        _apply(factory, wid, page("KARARLI SÜRÜM çıktı, notlar"), NOON + timedelta(hours=12))
        is None
    )
    assert len(_notifications(factory)) == 1


# ------------------------------------------------------------------ (3) extraction


def test_the_model_is_asked_only_for_a_changed_numeric_page_without_a_number(factory) -> None:
    wid = _watch(factory, "number_below:20000")
    provider = FakeProvider("19.499 TL")
    text = "Ürün sayfası. Fiyat 19.499 TL, kargo 49,90 TL."
    change = _apply(factory, wid, page(text), NOON, provider=provider)
    assert len(provider.questions) == 1
    question = provider.questions[0]
    begin = question.index(extract.UNTRUSTED_BEGIN)
    end = question.index(extract.UNTRUSTED_END)
    assert begin < question.index("Ürün sayfası") < end
    assert question.count("Ürün sayfası") == 1  # the page is ONLY inside the block
    assert change is not None and change.value == pytest.approx(19499)
    _apply(factory, wid, page(text), NOON + timedelta(hours=6), provider=provider)
    assert len(provider.questions) == 1  # unchanged -> no model call


def test_a_parsed_selector_number_never_asks_the_model(factory) -> None:
    wid = _watch(factory, "number_below:20000", selector=".price")
    provider = FakeProvider("1")
    _apply(factory, wid, page("21.000 TL"), NOON, provider=provider)
    assert provider.questions == []


def test_a_changed_watch_never_asks_the_model(factory) -> None:
    wid = _watch(factory)
    provider = FakeProvider("1")
    _apply(factory, wid, page("a 1 2"), NOON, provider=provider)
    _apply(factory, wid, page("b 3 4"), NOON + timedelta(hours=6), provider=provider)
    assert provider.questions == []


def test_an_invented_number_is_dropped_as_unreadable(factory) -> None:
    wid = _watch(factory, "number_below:20000")
    provider = FakeProvider("15.000 TL")  # not on the page
    _apply(factory, wid, page("Fiyat 19.499 TL, kargo 49,90 TL"), NOON, provider=provider)
    with factory() as db:
        reading = db.execute(select(WatchReading)).scalars().one()
        watch = db.get(Watch, wid)
    assert (reading.outcome, reading.reason) == ("unreadable", "değer bulunamadı")
    assert reading.value is None
    assert watch.baseline_sha256 is None


# ------------------------------------------------------------------ (5) failures


def test_three_failures_notify_once_and_the_fourth_is_silent(factory) -> None:
    wid = _watch(factory)
    _apply(factory, wid, page("a"), NOON)
    for n in range(1, 5):
        _apply(factory, wid, failed("Sayfa açılamadı."), NOON + timedelta(hours=n))
    rows = _notifications(factory)
    assert [r.kind for r in rows] == ["watch.read_failed"]
    with factory() as db:
        watch = db.get(Watch, wid)
    assert watch.consecutive_failures == 4
    assert watch.baseline_sha256 == compare.text_sha256("a")  # never moved by a failure
    assert [r.event_type for r in _ledger(factory)] == ["watch.read_failed"]


def test_a_failing_first_reading_notifies_at_once(factory) -> None:
    wid = _watch(factory)
    _apply(factory, wid, failed(), NOON)
    rows = _notifications(factory)
    assert [r.kind for r in rows] == ["watch.read_failed"]
    assert "giriş" in rows[0].body
    _apply(factory, wid, failed(), NOON + timedelta(hours=1))
    _apply(factory, wid, failed(), NOON + timedelta(hours=2))
    assert len(_notifications(factory)) == 1


def test_a_success_after_failures_resets_the_count_and_compares_to_the_old_baseline(
    factory,
) -> None:
    wid = _watch(factory)
    _apply(factory, wid, page("a"), NOON)
    _apply(factory, wid, failed(), NOON + timedelta(hours=1))
    assert _apply(factory, wid, page("a"), NOON + timedelta(hours=2)) is None
    with factory() as db:
        assert db.get(Watch, wid).consecutive_failures == 0


# ------------------------------------------------------------------ announcer


def test_the_announcer_hears_each_notified_change(factory) -> None:
    heard = []

    class Announcer:
        def announce(self, db, change):
            heard.append(change.outcome)
            return True

    wid = _watch(factory)
    _apply(factory, wid, page("a"), NOON, announcer=Announcer())
    _apply(factory, wid, page("b"), NOON + timedelta(hours=1), announcer=Announcer())
    assert heard == ["changed"]


# ------------------------------------------------------------------ (5) the runner's clock


def test_core_down_ten_hours_gives_one_reading_on_return(factory) -> None:
    wid = _watch(factory, every_hours=1)
    reader = FakeReader(page("a"))
    clock_now = [NOON]
    run = runner.WatchRunner(_scope(factory), reader, clock=lambda: clock_now[0])
    assert run.run_due() == 1
    clock_now[0] = NOON + timedelta(hours=10)
    assert run.run_due() == 1
    assert run.run_due() == 0
    assert len(reader.reads) == 2
    with factory() as db:
        watch = db.get(Watch, wid)
    assert watch.next_due_at.replace(tzinfo=UTC) == compare.next_due(clock_now[0], wid, 1)


def test_two_due_watches_never_have_two_readings_in_flight(factory) -> None:
    _watch(factory, label="bir")
    _watch(factory, label="iki")
    gate = threading.Event()
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    class BlockingReader:
        def read(self, db, *, url, selector, watch_id):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            gate.wait(5)
            with lock:
                state["now"] -= 1
            return page("x")

    run = runner.WatchRunner(_scope(factory), BlockingReader(), clock=lambda: NOON)
    results: list[int] = []
    first = threading.Thread(target=lambda: results.append(run.run_due()))
    first.start()
    deadline = time.monotonic() + 5
    while state["now"] == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert run.run_due() == 0  # a second pass while one is in flight does nothing
    gate.set()
    first.join(5)
    assert results == [2]
    assert state["max"] == 1


def test_a_disabled_runner_starts_no_task(factory) -> None:
    import asyncio

    run = runner.WatchRunner(_scope(factory), FakeReader(page("a")), enabled=False)

    async def go():
        await run.start()
        running = run.running
        await run.stop()
        return running

    assert asyncio.run(go()) is False
    assert run.health_check()["status"] == "skipped"


# ------------------------------------------------------------------ (6) storage: purge


def test_readings_older_than_thirty_days_are_purged_by_the_loop(factory) -> None:
    wid = _watch(factory)
    _apply(factory, wid, page("a"), NOON - timedelta(days=31))
    _apply(factory, wid, page("b"), NOON - timedelta(days=29))
    loop = runner.PurgeLoop(_scope(factory), clock=lambda: NOON)
    assert loop.purge_once() == 1
    assert _count(factory, WatchReading) == 1
    health = loop.health_check()
    assert health["retention_days"] == 30
    assert health["last_removed"] == 1
