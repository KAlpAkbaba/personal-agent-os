"""Unit tests: the self-model refresher (ADR-0111).

The defect these were written for is not a wrong answer, it is a stale one.
``build_index`` worked; nothing called it. Production's map was written on
2026-09-05 and never again, so by 2026-09-10 it described 222 of 423 modules and
was missing both files of the defect the owner had reported the day before.

So the assertions here are about the WIRE, not the walk: that a pass happens
without anyone asking, that it picks up a file that appeared after the last one,
that a failure is absorbed rather than raised, and that the real application
object -- not a hand-built one -- carries the component and starts it.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.selfmodel.models import CodeModule
from app.selfmodel.refresh import SelfModelRefresher
from tests.selfmodel_support import make_engine, write_fixture_tree


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return write_fixture_tree(tmp_path / "repo")


@pytest.fixture()
def factory():
    engine = make_engine()
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def _pointed_at(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """The root is never an argument, so a test moves the root itself.

    Patching ``default_repo_root`` where ``refresh`` imported it keeps the
    production rule intact: no caller can hand this component a directory.
    """
    monkeypatch.setattr("app.selfmodel.refresh.default_repo_root", lambda: repo)


def test_a_pass_indexes_the_checkout_without_anyone_asking(
    monkeypatch: pytest.MonkeyPatch, factory, repo: Path
) -> None:
    _pointed_at(monkeypatch, repo)
    refresher = SelfModelRefresher(factory)

    report = refresher.refresh_once()

    assert report is not None and report.modules_reparsed > 0
    with factory() as session:
        assert session.get(CodeModule, "app.observer.diagnostic_observer") is not None


def test_a_module_added_after_the_last_pass_is_picked_up(
    monkeypatch: pytest.MonkeyPatch, factory, repo: Path
) -> None:
    """The owner's requirement, reduced to its smallest form: a file appears, and
    the map contains it without a human running anything."""
    _pointed_at(monkeypatch, repo)
    refresher = SelfModelRefresher(factory)
    refresher.refresh_once()
    with factory() as session:
        assert session.get(CodeModule, "app.observer.latecomer") is None

    (repo / "services/api/app/observer/latecomer.py").write_text(
        '"""Arrived after the index was built."""\n', encoding="utf-8"
    )

    second = refresher.refresh_once()

    assert second is not None and second.modules_reparsed == 1
    with factory() as session:
        row = session.get(CodeModule, "app.observer.latecomer")
        assert row is not None
        assert row.purpose == "Arrived after the index was built."


def test_a_removed_module_leaves_the_map(
    monkeypatch: pytest.MonkeyPatch, factory, repo: Path
) -> None:
    _pointed_at(monkeypatch, repo)
    refresher = SelfModelRefresher(factory)
    refresher.refresh_once()
    (repo / "services/api/app/observer/helpers.py").unlink()

    report = refresher.refresh_once()

    assert report is not None and report.modules_removed == 1
    with factory() as session:
        assert session.get(CodeModule, "app.observer.helpers") is None


def test_a_failing_pass_is_absorbed_and_reported_as_none(
    monkeypatch: pytest.MonkeyPatch, factory, repo: Path
) -> None:
    """An aid to diagnosis must never be able to take Cloud Core down."""
    _pointed_at(monkeypatch, repo)

    def explode(*_args, **_kwargs):
        raise RuntimeError("disk went away mid-walk")

    monkeypatch.setattr("app.selfmodel.refresh.build_index", explode)
    refresher = SelfModelRefresher(factory)

    assert refresher.refresh_once() is None
    assert refresher.last_report is None


def test_the_root_cannot_be_supplied_by_a_caller() -> None:
    """A repository walker whose root is an argument is a file reader with extra
    steps (the rule ``app/selfmodel/routes.py`` states for the HTTP surface)."""
    import inspect

    signature = inspect.signature(SelfModelRefresher.refresh_once)
    assert list(signature.parameters) == ["self"]


def test_the_real_application_object_carries_it_and_the_lifespan_runs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through ``create_app`` and its real lifespan, not a hand-built app.

    "A component built, tested and never wired" is the defect class this
    repository has now named three times, and it is the exact shape of the
    defect underneath this one: ``build_index`` was built, tested against the
    real checkout, and called by nothing. A test that only constructed a
    ``SelfModelRefresher`` would have passed just as happily.

    ``refresh_once`` is replaced on the instance before the lifespan starts, so
    the loop's first pass records itself instead of walking a repository and
    dialling a database this suite forbids.
    """
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    app = create_app(Settings(_env_file=None))
    refresher = app.state.selfmodel_refresher
    assert isinstance(refresher, SelfModelRefresher)
    # It reads the same database the rest of the service does, not one of its own.
    assert refresher._session_factory == app.state.artifacts.session

    passes = 0
    stopped = False

    def counted():
        nonlocal passes
        passes += 1
        return None

    real_stop = refresher.stop

    async def watched_stop():
        nonlocal stopped
        stopped = True
        await real_stop()

    monkeypatch.setattr(refresher, "refresh_once", counted)
    monkeypatch.setattr(refresher, "stop", watched_stop)
    monkeypatch.setattr(refresher, "_initial_delay_s", 0.0)
    monkeypatch.setattr(refresher, "_interval_s", 0.05)

    with TestClient(app) as client:
        assert refresher.running, "the lifespan did not start it"
        client.get("/v1/system/health")
        deadline = time.perf_counter() + 5.0
        while not passes and time.perf_counter() < deadline:
            time.sleep(0.05)

    assert passes >= 1, "the lifespan started it but it never indexed anything"
    # ``running`` alone cannot carry this claim: the TestClient closes its event
    # loop on the way out, so the task reads as not-running whether the lifespan
    # cancelled it or simply abandoned it. Removing the ``stop()`` call left this
    # test green until the call itself was watched.
    assert stopped, "the lifespan did not stop it; a shutdown would leak the task"
    assert not refresher.running


async def test_the_background_loop_runs_a_pass_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch, factory, repo: Path
) -> None:
    _pointed_at(monkeypatch, repo)
    passes = 0
    refresher = SelfModelRefresher(factory, interval_s=0.01, initial_delay_s=0.0)
    real = refresher.refresh_once

    def counted():
        nonlocal passes
        passes += 1
        return real()

    monkeypatch.setattr(refresher, "refresh_once", counted)

    await refresher.start()
    assert refresher.running
    for _ in range(200):
        if passes:
            break
        await asyncio.sleep(0.01)
    await refresher.stop()

    assert passes >= 1, "the loop never ran a pass on its own"
    assert not refresher.running
