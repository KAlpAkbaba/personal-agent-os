"""ADR-0207 PR-B: the row is the truth, and a confirmation is a claim that is judged.

``app.webtask.service`` against a real database session (SQLite): what is WRITTEN after a
round, what the owner's words do to the row, and what a confirmation needs before it
opens anything. Every read-after-write goes through a FRESH session: an in-place change
to a JSON column is not seen as a change, and a test that reads back through the session
that wrote would not notice.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.actions.confirmation_gate import (
    CONFIRM_SOURCE_REST,
    CONFIRM_SOURCE_VOICE,
    GATE_CONFIRMATION_NOT_OWNER,
    GATE_NO_CONFIRMATION,
    GATE_NOT_READ_BACK,
    Confirmation,
)
from app.ledger.models import ActivityEventRow
from app.ledger.vocabulary import (
    EVENT_TYPE_WEB_TASK_ASKED_OWNER,
    EVENT_TYPE_WEB_TASK_FINISHED,
    EVENT_TYPE_WEB_TASK_STARTED,
    EVENT_TYPES,
)
from app.webtask import service
from app.webtask.loop import Ports
from app.webtask.models import WebTaskRow
from app.webtask.planner import ScriptedPlanner
from app.webtask.service import WebTaskError
from app.webtask.types import (
    ASK_CONFIRM,
    ASK_LOGIN,
    ASK_PAYMENT,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAITING_OWNER,
)
from tests.unit.test_webtask_acceptance import (
    QUOTE_GOAL,
    QUOTE_SCRIPT,
    T3_SCRIPT,
    done,
    login_site,
    quote_site,
    shop_site,
)
from tests.webtask_support import Clock, FakeBrowser

SESSION = "voice-session-1"


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    WebTaskRow.__table__.create(engine)
    ActivityEventRow.__table__.create(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def ports(browser: FakeBrowser, script: list[Any]) -> Ports:
    return Ports(browser=browser, planner=ScriptedPlanner(script), clock=Clock())


def start(factory: sessionmaker[Session], goal: str) -> uuid.UUID:
    with factory() as db:
        return service.start_task_db(db, goal=goal, session_id=SESSION).id


def run(factory: sessionmaker[Session], task_id: uuid.UUID, p: Ports, rounds: int = 12) -> dict:
    outcome: dict[str, Any] = {}
    for _ in range(rounds):
        with factory() as db:
            outcome = service.run_round_db(db, task_id, p)
        if outcome["status"] != STATUS_RUNNING:
            break
    return outcome


def row(factory: sessionmaker[Session], task_id: uuid.UUID) -> WebTaskRow:
    with factory() as db:
        return service.get_task(db, task_id)


def events(factory: sessionmaker[Session]) -> list[str]:
    with factory() as db:
        rows = db.execute(select(ActivityEventRow).order_by(ActivityEventRow.occurred_at)).scalars()
        return [r.event_type for r in rows]


def voice(turn: int | None, *, session: str = SESSION, intent: bool = True) -> Confirmation:
    return Confirmation(
        source=CONFIRM_SOURCE_VOICE, session_id=session, turn=turn, owner_intent_ok=intent
    )


def waiting_for_confirmation(
    factory: sessionmaker[Session],
) -> tuple[uuid.UUID, FakeBrowser, Ports]:
    browser = quote_site()
    p = ports(browser, list(QUOTE_SCRIPT) + [done("Teklif iletildi.")])
    task_id = start(factory, QUOTE_GOAL)
    outcome = run(factory, task_id, p)
    assert outcome == {
        "status": STATUS_WAITING_OWNER,
        "waiting_for": ASK_CONFIRM,
        "round_index": 1,
        "failure": "",
    }
    return task_id, browser, p


# ------------------------------------------------------------------ starting


def test_a_task_starts_as_a_row_and_a_ledger_event(factory) -> None:
    task_id = start(factory, "  Bugünkü   haberleri oku  ")
    found = row(factory, task_id)
    assert found.goal == "Bugünkü haberleri oku" and found.status == STATUS_RUNNING
    assert found.attended is True and found.round_index == 0
    assert found.state_json["task_id"] == str(task_id)
    assert events(factory) == [EVENT_TYPE_WEB_TASK_STARTED]
    assert {EVENT_TYPE_WEB_TASK_STARTED, EVENT_TYPE_WEB_TASK_ASKED_OWNER} <= set(EVENT_TYPES)
    assert service.workflow_id_for(task_id) == f"web-task-{task_id}"


@pytest.mark.parametrize(
    ("goal", "reason"), [("", "empty_goal"), ("   ", "empty_goal"), ("x" * 601, "goal_too_long")]
)
def test_a_goal_has_to_be_a_goal(factory, goal: str, reason: str) -> None:
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.start_task_db(db, goal=goal)
    assert refused.value.reason == reason


def test_one_task_at_a_time_and_the_refusal_names_the_one_in_flight(factory) -> None:
    first = start(factory, "Bir görev")
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.start_task_db(db, goal="İkinci görev")
    assert refused.value.reason == "task_in_flight"
    assert refused.value.detail == {"task_id": str(first)}

    with factory() as db:
        service.cancel_db(db, first)
    assert start(factory, "İkinci görev") != first


def test_a_row_with_nothing_driving_it_is_closed_and_does_not_refuse_the_next(factory) -> None:
    stale = start(factory, "Yarıda kalan")
    with factory() as db:
        found = service.get_task(db, stale)
        found.updated_at = datetime.now(UTC) - timedelta(minutes=21)
        db.commit()
    fresh = start(factory, "Yenisi")
    assert fresh != stale
    closed = row(factory, stale)
    assert closed.status == STATUS_FAILED and closed.failure == "abandoned"
    assert closed.completed_at is not None


def test_a_task_parked_for_the_owner_is_not_an_orphan_after_twenty_minutes(factory) -> None:
    task_id, _browser, _p = waiting_for_confirmation(factory)
    with factory() as db:
        found = service.get_task(db, task_id)
        found.updated_at = datetime.now(UTC) - timedelta(minutes=45)
        db.commit()
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.start_task_db(db, goal="Bir başkası")
    assert refused.value.reason == "task_in_flight"
    assert row(factory, task_id).status == STATUS_WAITING_OWNER


# ------------------------------------------------------------------ rounds


def test_every_round_is_written_and_read_back_through_a_fresh_session(factory) -> None:
    browser = shop_site()
    p = ports(browser, list(T3_SCRIPT))
    task_id = start(factory, "Şu kulaklığı sepete ekle, ödemede dur")

    with factory() as db:
        first = service.run_round_db(db, task_id, p)
    assert first["round_index"] == 1 and first["status"] == STATUS_RUNNING
    written = row(factory, task_id)
    assert written.round_index == 1 and len(written.state_json["rounds"]) == 1
    assert written.state_json["rounds"][0]["element"] == "Sepete ekle"

    outcome = run(factory, task_id, p)
    assert outcome["status"] == STATUS_WAITING_OWNER and outcome["waiting_for"] == ASK_PAYMENT
    written = row(factory, task_id)
    assert written.waiting_for == ASK_PAYMENT and "Ödeme sınırındayım" in written.message
    assert [r["outcome"] for r in written.state_json["rounds"]] == ["acted", "acted", "asked_owner"]
    assert browser.done == ["add_to_cart"]
    assert events(factory) == [EVENT_TYPE_WEB_TASK_STARTED, EVENT_TYPE_WEB_TASK_ASKED_OWNER]


def test_a_round_on_a_task_that_is_not_running_changes_nothing(factory) -> None:
    task_id, browser, p = waiting_for_confirmation(factory)
    before = row(factory, task_id).state_json
    commands = len(browser.commands)
    with factory() as db:
        outcome = service.run_round_db(db, task_id, p)
    assert outcome["status"] == STATUS_WAITING_OWNER
    assert row(factory, task_id).state_json == before
    assert len(browser.commands) == commands


def test_what_a_surface_may_show_holds_the_trail_and_not_the_page(factory) -> None:
    task_id, _browser, _p = waiting_for_confirmation(factory)
    with factory() as db:
        shown = service.task_dict(service.get_task(db, task_id))
    assert shown["waiting_for"] == ASK_CONFIRM and len(shown["rounds"]) == 2
    assert "observation" not in shown and "Teklif tutarı" not in str(shown["rounds"])
    # The goal is the owner's own sentence and is shown as he said it; what the loop
    # TYPED is on no line of the trail.
    assert "Onaylıyorum" not in str(shown["rounds"]) and shown["rounds"][0]["value_chars"] == 11


# ------------------------------------------------------------------ a confirmation is judged


def test_a_confirmation_before_the_read_back_was_heard_is_refused(factory) -> None:
    task_id, browser, _p = waiting_for_confirmation(factory)
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.confirm_db(db, task_id, voice(3))
    assert refused.value.reason == GATE_NOT_READ_BACK
    assert row(factory, task_id).status == STATUS_WAITING_OWNER and "send" not in browser.done


@pytest.mark.parametrize(
    ("confirmation", "reason"),
    [
        (None, GATE_NO_CONFIRMATION),
        (voice(7, session="another-session"), GATE_NOT_READ_BACK),
        (voice(4), GATE_NO_CONFIRMATION),  # the SAME turn as the read-back
        (voice(3), GATE_NO_CONFIRMATION),  # an earlier one
        (voice(None), GATE_NO_CONFIRMATION),
        (voice(5, intent=False), GATE_CONFIRMATION_NOT_OWNER),  # a model's own initiative
    ],
)
def test_a_claim_that_is_not_the_owners_word_for_this_read_back_opens_nothing(
    factory, confirmation: Confirmation | None, reason: str
) -> None:
    task_id, browser, p = waiting_for_confirmation(factory)
    with factory() as db:
        service.note_read_back_db(db, task_id, session_id=SESSION, turn=4)
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.confirm_db(db, task_id, confirmation)
    assert refused.value.reason == reason
    assert row(factory, task_id).status == STATUS_WAITING_OWNER
    assert run(factory, task_id, p)["status"] == STATUS_WAITING_OWNER
    assert "send" not in browser.done


def test_the_owners_word_on_a_later_turn_of_the_same_session_runs_the_step(factory) -> None:
    task_id, browser, p = waiting_for_confirmation(factory)
    with factory() as db:
        service.note_read_back_db(db, task_id, session_id=SESSION, turn=4)
    with factory() as db:
        confirmed = service.confirm_db(db, task_id, voice(5))
    assert confirmed.status == STATUS_RUNNING and confirmed.waiting_for == ""
    assert confirmed.read_back_session_id is None  # the read-back is spent with the word

    outcome = run(factory, task_id, p)
    assert outcome["status"] == STATUS_DONE and browser.done.count("send") == 1
    final = row(factory, task_id)
    assert final.completed_at is not None
    acted = [r for r in final.state_json["rounds"] if r["confirmed_by"]]
    assert [(r["element"], r["confirmed_by"], r["verified"]) for r in acted] == [
        ("Teklifi ilet", "voice", True)
    ]
    assert events(factory) == [
        EVENT_TYPE_WEB_TASK_STARTED,
        EVENT_TYPE_WEB_TASK_ASKED_OWNER,
        EVENT_TYPE_WEB_TASK_FINISHED,
    ]


def test_the_web_shells_button_is_a_confirmation_only_once_the_text_was_shown(factory) -> None:
    """Decision 1: the Onayla button is never shown without the text it approves. The
    shell says it showed the read-back; before that, its own confirmation is refused."""
    task_id, browser, p = waiting_for_confirmation(factory)
    rest = Confirmation(
        source=CONFIRM_SOURCE_REST, session_id="owner-session", owner_intent_ok=True
    )
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.confirm_db(db, task_id, rest)
    assert refused.value.reason == GATE_NOT_READ_BACK

    with factory() as db:
        service.note_read_back_db(db, task_id, session_id="owner-session", turn=None)
    with factory() as db:
        service.confirm_db(db, task_id, rest)
    assert run(factory, task_id, p)["status"] == STATUS_DONE
    assert browser.done.count("send") == 1


def test_a_confirmation_is_used_once(factory) -> None:
    task_id, _browser, _p = waiting_for_confirmation(factory)
    with factory() as db:
        service.note_read_back_db(db, task_id, session_id=SESSION, turn=4)
    with factory() as db:
        service.confirm_db(db, task_id, voice(5))
    with factory() as db, pytest.raises(WebTaskError):
        service.confirm_db(db, task_id, voice(6))


def test_a_payment_a_login_and_a_finished_task_are_not_opened_by_a_word(factory) -> None:
    browser = shop_site()
    p = ports(browser, list(T3_SCRIPT))
    task_id = start(factory, "Şu kulaklığı sepete ekle, ödemede dur")
    assert run(factory, task_id, p)["waiting_for"] == ASK_PAYMENT
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.note_read_back_db(db, task_id, session_id=SESSION, turn=4)
    assert refused.value.reason == "nothing_to_confirm"
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.confirm_db(db, task_id, voice(9))
    assert refused.value.reason == "nothing_to_confirm"
    assert not browser.flags.get("paid") and "to_payment" not in browser.done

    with factory() as db:
        service.cancel_db(db, task_id)
    with factory() as db, pytest.raises(WebTaskError):
        service.confirm_db(db, task_id, voice(9))


# ------------------------------------------------------------------ devam, hayır, iptal


def test_devam_takes_up_the_same_round(factory) -> None:
    browser = login_site()
    p = ports(browser, [done("1 siparişiniz var.")])
    task_id = start(factory, "Siparişlerime bak")
    outcome = run(factory, task_id, p)
    assert outcome["waiting_for"] == ASK_LOGIN and outcome["round_index"] == 0

    browser.flags["signed_in"] = True
    with factory() as db:
        resumed = service.continue_db(db, task_id, answer="devam")
    assert resumed.status == STATUS_RUNNING and resumed.round_index == 0
    assert row(factory, task_id).state_json["answers"] == ["devam"]
    assert run(factory, task_id, p)["status"] == STATUS_DONE


def test_devam_is_not_a_confirmation_at_the_row_either(factory) -> None:
    task_id, browser, _p = waiting_for_confirmation(factory)
    with factory() as db, pytest.raises(WebTaskError) as refused:
        service.continue_db(db, task_id, answer="devam")
    assert refused.value.reason == "confirmation_required"
    assert "send" not in browser.done


def test_hayir_drops_the_step_and_the_task_goes_on(factory) -> None:
    task_id, browser, p = waiting_for_confirmation(factory)
    with factory() as db:
        declined = service.decline_db(db, task_id)
    assert declined.status == STATUS_RUNNING and declined.waiting_for == ""
    assert run(factory, task_id, p)["status"] == STATUS_DONE
    assert "send" not in browser.done
    assert "onaylamadı" in row(factory, task_id).state_json["answers"][0]


def test_a_cancel_while_the_task_waits_is_applied_at_once(factory) -> None:
    task_id, browser, p = waiting_for_confirmation(factory)
    with factory() as db:
        cancelled = service.request_cancel_db(db, task_id)
    assert cancelled.status == STATUS_CANCELLED and cancelled.completed_at is not None
    assert run(factory, task_id, p)["status"] == STATUS_CANCELLED
    assert "send" not in browser.done
    assert events(factory)[-1] == EVENT_TYPE_WEB_TASK_FINISHED


def test_a_cancel_that_arrives_during_a_round_is_not_overwritten_by_it(factory) -> None:
    browser = shop_site()
    task_id = start(factory, "Şu kulaklığı sepete ekle")

    class CancelsMidRound(FakeBrowser):
        pass

    original = browser.act

    def act(**kwargs: Any) -> dict[str, Any]:
        with factory() as other:
            service.request_cancel_db(other, task_id)  # the owner, from another surface
        return original(**kwargs)

    browser.act = act  # type: ignore[method-assign]
    with factory() as db:
        outcome = service.run_round_db(db, task_id, ports(browser, list(T3_SCRIPT)))
    assert outcome["status"] == STATUS_CANCELLED
    final = row(factory, task_id)
    assert final.status == STATUS_CANCELLED and final.cancel_requested is True
    # The round that was running is on the trail: what it did is not hidden by the cancel.
    assert final.state_json["rounds"][0]["element"] == "Sepete ekle"


def test_a_round_that_could_not_run_leaves_a_failed_row_and_never_a_running_one(factory) -> None:
    task_id = start(factory, "Bir görev")
    with factory() as db:
        failed = service.fail_db(db, task_id, reason="round_could_not_run", detail="RuntimeError")
    assert failed is not None and failed.status == STATUS_FAILED
    assert "RuntimeError" in failed.message and failed.completed_at is not None
    with factory() as db:
        assert service.fail_db(db, task_id, reason="x", detail="y").failure == "round_could_not_run"
        assert service.fail_db(db, uuid.uuid4(), reason="x", detail="y") is None
    assert start(factory, "Sonraki") != task_id


# ------------------------------------------------------------ the target (cloud-task-loop-core)
#
# ADR-0213 addendum (owner, 2026-09-30): a cloud task the owner started may go on after
# he leaves, because it acts nowhere he did not name. The row says so honestly: a cloud
# task is NOT attended. Every other target still is.


def _started_events(factory: sessionmaker[Session]) -> list[dict[str, Any]]:
    with factory() as db:
        rows = db.execute(
            select(ActivityEventRow).where(
                ActivityEventRow.event_type == EVENT_TYPE_WEB_TASK_STARTED
            )
        ).scalars()
        return [dict(r.detail_json or {}) for r in rows]


def test_a_cloud_task_is_not_attended_and_says_where_it_runs(
    factory: sessionmaker[Session],
) -> None:
    device = uuid.uuid4()
    with factory() as db:
        task_id = service.start_task_db(
            db, goal="Bir haber bul", device_id=device, target="cloud", spoken_target="bulutta"
        ).id
    started = row(factory, task_id)
    assert started.attended is False
    assert started.state_json["target"] == "cloud"
    assert service.load(started).target == "cloud"
    assert service.task_dict(started)["target"] == "cloud"
    (detail,) = _started_events(factory)
    assert detail["target"] == "cloud"
    assert detail["device_id"] == str(device)
    assert detail["attended"] is False
    assert detail["spoken_target"] == "bulutta"


@pytest.mark.parametrize("target", ["owner_chrome", "device", ""])
def test_every_other_target_is_attended(factory: sessionmaker[Session], target: str) -> None:
    with factory() as db:
        task_id = service.start_task_db(db, goal="Bir sayfa aç", target=target).id
    assert row(factory, task_id).attended is True
    (detail,) = _started_events(factory)
    assert detail["attended"] is True and detail["target"] == target


def test_the_target_survives_a_round(factory: sessionmaker[Session]) -> None:
    with factory() as db:
        task_id = service.start_task_db(db, goal="Bir sayfa aç", target="cloud").id
    run(factory, task_id, ports(shop_site(), [done("Bitti.")]))
    assert service.load(row(factory, task_id)).target == "cloud"


class _ModelLike:
    """A planner that answers as the model planner does, by its name."""

    name = "model"

    def plan(self, request: Any) -> Any:
        return done("Bitti.")


def test_the_planner_calls_are_counted_on_the_row(factory: sessionmaker[Session]) -> None:
    task_id = start(factory, "Bir sayfa aç")
    run(factory, task_id, Ports(browser=shop_site(), planner=_ModelLike(), clock=Clock()))
    state = row(factory, task_id).state_json
    assert state["planner_calls"] == 1
    assert state["planner_model_calls"] == 1

    other = start(factory, "Bir sayfa aç")
    run(factory, other, ports(shop_site(), [done("Bitti.")]))
    state = row(factory, other).state_json
    assert state["planner_calls"] == 1 and state["planner_model_calls"] == 0


def test_no_routine_scheduler_or_research_code_starts_a_browser_task() -> None:
    """'Hiçbir rutin görev başlatmaz' (ADR-0213 addendum): what runs on a timer or for
    research never starts a browser task. Only the owner's own surfaces may."""
    from pathlib import Path

    app_root = Path(service.__file__).resolve().parents[1]
    offenders = []
    # `scheduler` is named by the card and has no package today; a future one is read too.
    for package in ("routines", "scheduler", "research", "watch", "briefing"):
        for path in sorted((app_root / package).rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "start_task_db" in text or "BrowserTaskWorkflow" in text:
                offenders.append(str(path.relative_to(app_root)))
    assert (app_root / "routines").is_dir() and (app_root / "research").is_dir()
    assert offenders == []
