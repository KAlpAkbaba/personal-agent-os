"""The model policy on the Cloud Core (ADR-0214 addendum 7, task model-policy-api).

One setting document in the team store (a model per role, the fallback switch), read and
written through ``/v1/team/queue/models``; the live status carries each run's model and the
limits; ``/v1/team/office`` shows both. The same contract is held by ``scripts/lib/TeamQueue.ps1``
(the cycle's half): a test here reads its source, so the two lists cannot drift apart.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.team import models_setting, office
from app.team import store as team_store
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]
MODELS = "/v1/team/queue/models"
STATUS = "/v1/team/queue/status"
OFFICE = "/v1/team/office"
FABLE, OPUS, SONNET = "claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5"
ROLES = ("lead", "researcher", "integrator", "worker", "inspector")
DEFAULT_ROLES = {
    "lead": FABLE,
    "researcher": OPUS,
    "integrator": OPUS,
    "worker": OPUS,
    "inspector": FABLE,
}
NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
STAMP = "2026-10-02T12:00:00Z"
Z_FORM = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
LOCK = {
    "held": True,
    "machine": "MAIL",
    "cycle_id": "c1",
    "pid": 7,
    "acquired_at": "2026-10-02T11:00:00Z",
}


def _setting(fallback: bool = True, **roles: str) -> dict[str, Any]:
    return {"roles": {**DEFAULT_ROLES, **roles}, "fallback": fallback}


def _stored(fallback: bool = True, **roles: str) -> dict[str, Any]:
    return {**_setting(fallback, **roles), "updated_at": STAMP}


def _window(state: str = "ok", resets_at: str | None = None, used_pct: Any = None) -> dict:
    return {"state": state, "resets_at": resets_at, "used_pct": used_pct}


def _limits(**extra: Any) -> dict[str, Any]:
    limits = {"fable": _window(), "all": _window(), "fallback": True, "lowered": []}
    limits.update(extra)
    return limits


def _status(
    runs: list[tuple[str, str, str | None]], *, at: datetime = NOW, **extra: Any
) -> dict[str, Any]:
    stamp = team_store.stamp
    listed = []
    for i, (task, role, model) in enumerate(runs):
        run = {"task": task, "role": role, "started_at": stamp(at - timedelta(minutes=30 - i))}
        if model is not None:
            run["model"] = model
        listed.append(run)
    doc: dict[str, Any] = {
        "cycle_id": "c1",
        "machine": "MAIL",
        "pid": 7,
        "started_at": stamp(at - timedelta(hours=1)),
        "runs": listed,
        "estimated_usd": 1.5,
        "usage_limit": {"state": "ok", "resets_at": None},
        "updated_at": stamp(at),
    }
    doc.update(extra)
    return doc


def _view(status, models=None, lock=LOCK, now=NOW):
    return office.office_view({"version": 1, "tasks": []}, lock, status, [], now, models=models)


def _seat(view, name):
    return next(s for s in view["agents"] if s["seat"] == name)


# ------------------------------------------------------------------ the pure rules


def test_the_chain_is_strongest_first_and_is_weaker_holds_it():
    assert models_setting.CHAIN == (FABLE, OPUS, SONNET)
    assert models_setting.is_weaker(OPUS, FABLE)
    assert models_setting.is_weaker(SONNET, OPUS)
    assert models_setting.is_weaker(SONNET, FABLE)
    assert not models_setting.is_weaker(FABLE, OPUS)
    assert not models_setting.is_weaker(OPUS, SONNET)
    for model in models_setting.CHAIN:
        assert not models_setting.is_weaker(model, model)


def test_the_defaults_are_fable_for_lead_and_inspector_and_opus_for_the_rest():
    assert models_setting.defaults(STAMP) == {
        "roles": DEFAULT_ROLES,
        "fallback": True,
        "updated_at": STAMP,
    }


def test_a_whole_setting_has_no_problems():
    assert models_setting.problems(_setting(), strict=True) == []
    assert models_setting.problems(_stored(False, worker=SONNET, inspector=OPUS), strict=True) == []


BROKEN = [
    ("unknown_model", _setting(worker="claude-opus-4")),
    ("unknown_model", _setting(worker="")),
    ("unknown_model", _setting(worker=7)),  # type: ignore[arg-type]
    ("unknown_role", _setting(king=OPUS)),
    (
        "missing_role",
        {"roles": {r: m for r, m in DEFAULT_ROLES.items() if r != "lead"}, "fallback": True},
    ),
    ("unknown_key", {**_setting(), "surprise": 1}),
    ("invalid", {"roles": DEFAULT_ROLES}),  # no fallback
    ("invalid", {"roles": DEFAULT_ROLES, "fallback": "yes"}),
    ("invalid", {"roles": [FABLE], "fallback": True}),
    ("invalid", {"fallback": True}),
    ("invalid", {**_setting(), "updated_at": 7}),
    ("inspector_weaker_than_worker", _setting(worker=FABLE, inspector=OPUS)),
    ("inspector_weaker_than_worker", _setting(worker=OPUS, inspector=SONNET)),
]


@pytest.mark.parametrize(("code", "document"), BROKEN)
def test_every_way_a_setting_breaks_the_contract_has_its_code(code, document):
    found = models_setting.problems(document, strict=True)
    assert found and found[0][0] == code, found


def test_an_inspector_as_strong_as_the_worker_is_allowed():
    for model in models_setting.CHAIN:
        assert models_setting.problems(_setting(worker=model, inspector=model), strict=True) == []
    assert models_setting.problems(_setting(worker=SONNET, inspector=FABLE), strict=True) == []


def test_what_is_stored_is_filled_and_a_broken_one_is_the_defaults():
    # the file a person wrote (team/models.json before this task): roles only
    filled = models_setting.effective({"roles": {"worker": SONNET, "inspector": OPUS}}, STAMP)
    assert filled == {
        "roles": {**DEFAULT_ROLES, "worker": SONNET, "inspector": OPUS},
        "fallback": True,
        "updated_at": STAMP,
    }
    kept = _stored(False, lead=OPUS)
    kept["updated_at"] = "2026-09-30T08:00:00Z"
    assert models_setting.effective(kept, STAMP) == kept
    assert models_setting.effective(None, STAMP) == models_setting.defaults(STAMP)
    # never a model that is not one of the three, never an inspector below the worker
    for broken in (
        {"roles": {"worker": "gpt"}},
        {"roles": {"worker": FABLE, "inspector": SONNET}},
        {"roles": {"inspector": SONNET}},  # filled worker is opus: sonnet is weaker
        ["not", "an", "object"],
    ):
        assert models_setting.effective(broken, STAMP) == models_setting.defaults(STAMP)


def test_the_cycles_half_names_the_same_chain_roles_and_defaults():
    """The two halves read each other: TeamQueue.ps1's lists against models_setting's."""
    source = (REPO / "scripts" / "lib" / "TeamQueue.ps1").read_text(encoding="utf-8")

    def listed(name: str) -> tuple[str, ...]:
        found = re.search(rf"\$script:{name} = @\(([^)]*)\)", source)
        assert found, f"{name} was not found in TeamQueue.ps1"
        return tuple(re.findall(r'"([^"]+)"', found.group(1)))

    assert listed("TeamModelChain") == models_setting.CHAIN
    assert set(listed("TeamModelRoles")) == set(models_setting.ROLES) == set(ROLES)
    body = re.search(r"function Get-TeamModelDefaults \{(.*?)\n\}", source, re.DOTALL)
    assert body, "Get-TeamModelDefaults was not found in TeamQueue.ps1"
    theirs = dict(re.findall(r'(\w+) = "(claude-[a-z0-9-]+)"', body.group(1)))
    assert theirs == models_setting.DEFAULT_ROLES == DEFAULT_ROLES
    assert re.search(r"fallback\s*=\s*\$true", body.group(1))
    for code in {code for code, _ in BROKEN}:
        assert f'"{code}"' in source, f"the cycle's validator has no code {code}"


# ------------------------------------------------------------------ the two stores


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    TeamStateRow.__table__.create(eng)
    yield eng
    eng.dispose()


def _file_store(tmp_path: Path) -> team_store.FileStore:
    root = tmp_path / "team"
    (root / "reports").mkdir(parents=True)
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    return team_store.FileStore(root)


def _make_store(kind: str, engine, tmp_path: Path) -> team_store.TeamStore:
    if kind == "db":
        return team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    return _file_store(tmp_path)


@pytest.fixture(params=["file", "db"])
def store(request, engine, tmp_path) -> team_store.TeamStore:
    return _make_store(request.param, engine, tmp_path)


def test_nothing_stored_reads_as_none_and_a_put_round_trips_and_is_replaced(store):
    assert store.read_models() is None
    first = _stored(worker=SONNET, inspector=OPUS)
    store.put_models(first)
    assert store.read_models() == first
    second = {**_stored(False), "updated_at": "2026-10-02T12:05:00Z"}
    store.put_models(second)
    assert store.read_models() == second
    read = store.read_models()
    read["roles"]["worker"] = "changed-by-the-reader"
    assert store.read_models() == second


@pytest.mark.parametrize(
    "broken",
    [
        _stored(worker="gpt"),
        _stored(worker=FABLE, inspector=OPUS),
        {**_stored(), "surprise": 1},
        _setting(),  # no updated_at: the store keeps a stamped document
        {**_setting(), "updated_at": "yesterday"},
    ],
)
def test_the_store_itself_refuses_a_setting_that_breaks_the_contract(store, broken):
    good = _stored(lead=OPUS)
    store.put_models(good)
    with pytest.raises(team_store.Invalid):
        store.put_models(broken)
    assert store.read_models() == good


def test_the_file_store_keeps_the_setting_in_models_json_beside_the_queue(tmp_path):
    store = _file_store(tmp_path)
    store.put_models(_stored(worker=SONNET, inspector=OPUS))
    on_disk = json.loads((store.root / "models.json").read_text(encoding="utf-8"))
    assert on_disk == _stored(worker=SONNET, inspector=OPUS)
    (store.root / "models.json").write_text("{ not json", encoding="utf-8")
    assert store.read_models() is None


def test_the_database_keeps_it_as_one_row_of_the_existing_table_within_its_columns(engine):
    db = team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    db.put_models(_stored())
    db.put_models({**_stored(False), "updated_at": "2026-10-02T12:05:00Z"})
    db.put_status(
        _status(
            [("a-task", "worker", OPUS)],
            limits=_limits(lowered=[_lowered(i) for i in range(20)]),
        )
    )
    widths = {
        column.name: column.type.length
        for column in TeamStateRow.__table__.columns
        if getattr(column.type, "length", None)
    }
    with sessionmaker(bind=engine)() as session:
        rows = session.query(TeamStateRow).all()
    assert sorted((row.kind, row.key) for row in rows) == [
        ("models", "models"),
        ("status", "status"),
    ]
    for row in rows:
        for name, width in widths.items():
            value = getattr(row, name)
            assert len(value) <= width, f"{row.kind}/{row.key}: {name} is {len(value)} > {width}"


def _lowered(index: int = 0) -> dict[str, str]:
    return {
        "task": f"task-{index}",
        "role": "worker",
        "from": OPUS,
        "to": SONNET,
        "at": f"2026-10-02T11:{index:02d}:00Z",
    }


# ------------------------------------------------------------------ the routes


def _app(kind: str, engine, tmp_path: Path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_store = _make_store(kind, engine, tmp_path)
    return app, settings


@pytest.fixture(params=["file", "db"])
def owner(request, engine, tmp_path):
    app, settings = _app(request.param, engine, tmp_path)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    client.team_store = app.state.team_store
    return client


def test_get_with_nothing_stored_is_the_defaults(owner):
    response = owner.get(MODELS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"roles", "fallback", "updated_at"}
    assert body["roles"] == DEFAULT_ROLES
    assert body["fallback"] is True
    assert Z_FORM.match(body["updated_at"])
    assert owner.team_store.read_models() is None  # a read writes nothing


def test_a_put_is_stamped_by_the_server_and_the_get_returns_it(owner):
    before = team_store.stamp(team_store.utcnow())
    wanted = {
        **_setting(False, worker=SONNET, inspector=OPUS),
        "updated_at": "1999-01-01T00:00:00Z",
    }
    put = owner.put(MODELS, json=wanted)
    assert put.status_code == 200, put.text
    stored = put.json()
    assert stored["roles"] == wanted["roles"] and stored["fallback"] is False
    assert Z_FORM.match(stored["updated_at"]) and stored["updated_at"] >= before
    assert owner.get(MODELS).json() == stored
    assert owner.team_store.read_models() == stored
    # without updated_at too (the Ofis page sends the two things the owner chose)
    again = owner.put(MODELS, json=_setting())
    assert again.status_code == 200, again.text
    assert owner.get(MODELS).json()["roles"] == DEFAULT_ROLES


@pytest.mark.parametrize(("code", "document"), BROKEN)
def test_a_put_that_breaks_the_contract_is_422_with_its_code_and_nothing_is_written(
    owner, code, document
):
    refused = owner.put(MODELS, json=document)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == code
    assert refused.json()["detail"]["problems"]
    assert owner.team_store.read_models() is None
    # and over a setting that is there: it stays
    assert owner.put(MODELS, json=_setting(lead=OPUS)).status_code == 200
    kept = owner.get(MODELS).json()
    assert owner.put(MODELS, json=document).status_code == 422
    assert owner.get(MODELS).json() == kept


def test_a_body_that_is_not_an_object_is_422(owner):
    assert owner.put(MODELS, json=[FABLE]).status_code == 422
    assert owner.put(MODELS, json="fable").status_code == 422
    assert owner.team_store.read_models() is None


def test_the_models_routes_are_closed_without_an_owner_session(engine, tmp_path):
    app, _ = _app("db", engine, tmp_path)
    client = TestClient(app)
    assert client.get(MODELS).status_code == 401
    assert client.put(MODELS, json=_setting(worker=SONNET, inspector=SONNET)).status_code == 401
    assert app.state.team_store.read_models() is None


def test_the_setting_file_in_the_repository_is_served_whole(engine, tmp_path):
    """``team/models.json`` as the tree holds it (roles only) is a valid stored setting."""
    app, settings = _app("file", engine, tmp_path)
    in_tree = (REPO / "team" / "models.json").read_bytes()
    (app.state.team_store.root / "models.json").write_bytes(in_tree)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    body = client.get(MODELS).json()
    assert body["roles"] == {**DEFAULT_ROLES, **json.loads(in_tree)["roles"]}
    assert body["fallback"] is json.loads(in_tree).get("fallback", True)


# ------------------------------------------------------------------ the live status


def _live(runs, **extra):
    return _status(runs, at=team_store.utcnow(), **extra)


def test_a_status_with_a_model_per_run_and_the_limits_is_accepted_and_read_back(owner):
    limits = _limits(
        fable=_window("limited", "2026-10-03T07:00:00Z", 100),
        all=_window("ok", None, 47.5),
        lowered=[_lowered(1), _lowered(2)],
    )
    doc = _live([("a-task", "worker", SONNET), ("b-task", "inspector", FABLE)], limits=limits)
    put = owner.put(STATUS, json=doc)
    assert put.status_code == 200, put.text
    assert owner.get(STATUS).json() == doc
    assert owner.team_store.read_status()["limits"]["lowered"][0]["from"] == OPUS


def test_an_old_shape_status_is_still_accepted_and_kept_as_it_was_sent(owner):
    doc = _live([("a-task", "worker", None)])
    assert "limits" not in doc and "model" not in doc["runs"][0]
    assert owner.put(STATUS, json=doc).status_code == 200
    assert owner.get(STATUS).json() == doc


@pytest.mark.parametrize(
    "limits",
    [
        _limits(fable=_window("ok", None, "ninety")),
        _limits(all=_window("ok", None, True)),
        _limits(fable=_window("bored")),
        _limits(fable={**_window(), "guess": 50}),
        _limits(surprise=1),
        _limits(fallback="yes"),
        _limits(lowered=[{**_lowered(), "why": "x"}]),
        _limits(lowered=[{k: v for k, v in _lowered().items() if k != "to"}]),
        _limits(lowered=[_lowered(i) for i in range(21)]),
        {k: v for k, v in _limits().items() if k != "all"},
    ],
)
def test_a_status_whose_limits_break_the_contract_is_422_and_nothing_is_written(owner, limits):
    doc = _live([("a-task", "worker", OPUS)], limits=limits)
    assert owner.put(STATUS, json=doc).status_code == 422
    assert not owner.get(STATUS).json().get("cycle_id")


def test_a_run_whose_model_is_not_a_string_or_an_unknown_run_key_is_422(owner):
    doc = _live([("a-task", "worker", OPUS)])
    doc["runs"][0]["model"] = 7
    assert owner.put(STATUS, json=doc).status_code == 422
    doc["runs"][0] = {**doc["runs"][0], "model": OPUS, "effort": "high"}
    assert owner.put(STATUS, json=doc).status_code == 422


# ------------------------------------------------------------------ the Ofis


def test_every_seat_shows_the_model_its_role_is_set_to():
    setting = _stored(worker=SONNET, inspector=OPUS, lead=OPUS)
    view = _view(None, setting, lock=None)
    assert {s["seat"]: s["model"] for s in view["agents"]} == {
        "lead": OPUS,
        "researcher": OPUS,
        "integrator": OPUS,
        "worker-1": SONNET,
        "worker-2": SONNET,
        "worker-3": SONNET,
        "worker-4": SONNET,
        "inspector": OPUS,
        "owner": None,
    }
    assert all("running_model" not in s for s in view["agents"])
    assert view["models"] == setting


def test_with_no_setting_given_the_seats_show_the_defaults():
    view = _view(None, None, lock=None)
    assert _seat(view, "lead")["model"] == FABLE
    assert _seat(view, "worker-3")["model"] == OPUS
    assert _seat(view, "inspector")["model"] == FABLE
    assert view["models"]["roles"] == DEFAULT_ROLES and view["models"]["fallback"] is True


def test_running_model_is_there_only_when_a_live_run_is_on_another_model():
    status = _status(
        [
            ("a-task", "worker", OPUS),  # as configured
            ("b-task", "worker", SONNET),  # lowered
            ("c-task", "inspector", FABLE),
            ("d-task", "inspector", OPUS),  # one of the seat's runs is on another model
            ("e-task", "lead", None),  # an old cycle: the run names no model
        ]
    )
    view = _view(status, _stored())
    w1, w2, inspector, lead = (
        _seat(view, n) for n in ("worker-1", "worker-2", "inspector", "lead")
    )
    assert (w1["model"], w1.get("running_model")) == (OPUS, None) and "running_model" not in w1
    assert (w2["model"], w2["running_model"]) == (OPUS, SONNET)
    assert (inspector["model"], inspector["running_model"]) == (FABLE, OPUS)
    assert lead["model"] == FABLE and "running_model" not in lead
    assert [r["model"] for r in inspector["runs"]] == [FABLE, OPUS]
    assert w2["runs"] == [
        {"task_id": "b-task", "task_title": None, "since": w2["since"], "model": SONNET}
    ]
    assert "model" not in lead["runs"][0]  # nobody said which: not "null", not a guess
    assert "running_model" not in _seat(view, "worker-3")


def test_three_live_inspector_runs_are_three_running_and_each_run_names_its_model():
    status = _status([(n, "inspector", m) for n, m in (("a", FABLE), ("b", FABLE), ("c", OPUS))])
    view = _view(status, _stored())
    seat = _seat(view, "inspector")
    assert view["cycle"]["running_agents"] == 3
    assert [(r["task_id"], r["model"]) for r in seat["runs"]] == [
        ("a", FABLE),
        ("b", FABLE),
        ("c", OPUS),
    ]
    assert (seat["model"], seat["running_model"]) == (FABLE, OPUS)


def test_a_stale_status_shows_the_configured_models_and_no_running_model():
    old = _status([("a-task", "worker", SONNET)], at=NOW - timedelta(minutes=11))
    view = _view(old, _stored())
    assert _seat(view, "worker-1")["model"] == OPUS
    assert all("running_model" not in s for s in view["agents"])


def test_the_limits_of_the_status_are_passed_through():
    limits = _limits(
        fable=_window("limited", "2026-10-03T07:00:00Z", 100),
        all=_window("ok", None, 47.5),
        fallback=False,
        lowered=[_lowered(1), _lowered(2)],
    )
    view = _view(_status([], limits=limits), _stored())
    assert view["cycle"]["limits"] == limits


def test_with_no_status_or_an_old_one_the_limits_are_ok_with_null_percentages():
    empty = {
        "fable": {"state": "ok", "resets_at": None, "used_pct": None},
        "all": {"state": "ok", "resets_at": None, "used_pct": None},
        "fallback": True,
        "lowered": [],
    }
    assert _view(None, None, lock=None)["cycle"]["limits"] == empty
    assert _view(_status([]), _stored())["cycle"]["limits"] == empty
    # the switch shown is the setting's when no status says otherwise
    assert _view(None, _stored(False), lock=None)["cycle"]["limits"] == {**empty, "fallback": False}


def test_a_limit_whose_reset_has_passed_is_not_shown_as_a_limit():
    """The cycle that wrote it may be gone: yesterday's "limited" must not stand for ever."""
    limits = _limits(
        fable=_window("limited", "2026-10-02T11:59:00Z", 100),
        all=_window("limited", "2026-10-02T12:30:00Z", 100),
    )
    shown = _view(_status([], limits=limits), _stored())["cycle"]["limits"]
    assert shown["fable"] == {"state": "ok", "resets_at": None, "used_pct": None}
    assert shown["all"] == {
        "state": "limited",
        "resets_at": "2026-10-02T12:30:00Z",
        "used_pct": 100,
    }


def test_limits_a_file_holds_in_another_shape_never_reach_the_page_as_they_are():
    status = _status([])
    status["limits"] = {
        "fable": {"state": "bored", "resets_at": 7, "used_pct": "ninety"},
        "all": "limited",
        "fallback": "yes",
        "lowered": [1, *[_lowered(i) for i in range(25)]],
    }
    shown = _view(status, _stored(False))["cycle"]["limits"]
    assert shown["fable"] == {"state": "ok", "resets_at": None, "used_pct": None}
    assert shown["all"] == {"state": "ok", "resets_at": None, "used_pct": None}
    assert shown["fallback"] is False
    assert shown["lowered"] == [_lowered(i) for i in range(5, 25)]  # the newest twenty


def test_the_office_route_shows_the_setting_the_owner_put_and_the_live_models(owner):
    put = owner.put(MODELS, json=_setting(worker=SONNET, inspector=OPUS))
    assert put.status_code == 200, put.text
    owner.team_store.acquire_lock(machine="MAIL", cycle_id="c1", pid=7)
    limits = _limits(all=_window("ok", None, 47))
    doc = _live([("a-task", "worker", SONNET), ("b-task", "inspector", FABLE)], limits=limits)
    assert owner.put(STATUS, json=doc).status_code == 200
    body = owner.get(OFFICE).json()
    assert body["models"] == put.json() == owner.get(MODELS).json()
    assert _seat(body, "worker-1")["model"] == SONNET
    assert "running_model" not in _seat(body, "worker-1")
    assert (_seat(body, "inspector")["model"], _seat(body, "inspector")["running_model"]) == (
        OPUS,
        FABLE,
    )
    assert _seat(body, "lead")["model"] == FABLE
    assert body["cycle"]["limits"] == limits
    assert body["cycle"]["running_agents"] == 2


def test_the_office_route_with_nothing_stored_shows_the_defaults_and_null_percentages(owner):
    body = owner.get(OFFICE).json()
    assert body["models"]["roles"] == DEFAULT_ROLES
    assert _seat(body, "inspector")["model"] == FABLE
    assert body["cycle"]["limits"]["fable"]["used_pct"] is None
    assert body["cycle"]["limits"]["all"] == {"state": "ok", "resets_at": None, "used_pct": None}
