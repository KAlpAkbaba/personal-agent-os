"""The spoken Ofis summary: one Turkish paragraph from the same view the page shows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from app.team import office
from app.team import store as team_store
from app.team.speech import office_paragraph
from app.voice.realtime_sessions import tools_team

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
STAMP = team_store.stamp(NOW)


def _task(task_id, title, state, *, role="worker", at=STAMP, **extra):
    return {
        "id": task_id,
        "title": title,
        "state": state,
        "updated_at": at,
        "reports": [{"role": role, "at": at, "outcome": "ok", "summary": ["x"]}],
        **extra,
    }


def _status(*runs, usage=None, usd=0):
    doc = {
        "cycle_id": "c1",
        "machine": "m",
        "started_at": STAMP,
        "updated_at": STAMP,
        "estimated_usd": usd,
        "runs": list(runs),
    }
    if usage:
        doc["usage_limit"] = usage
    return doc


def _run(task, n):
    return {"role": "worker", "task": task, "started_at": f"2026-10-01T11:0{n}:00Z"}


LOCK = {"held": True, "cycle_id": "c1", "machine": "m", "acquired_at": STAMP}


def _view(queue, status, approvals=()):
    lock = dict(LOCK)
    return office.office_view(queue, lock, status, list(approvals), NOW)


def _busy_view():
    queue = {
        "tasks": [
            _task("t-secret-1", "Ofis sayfası: iki sütun, kartlar", "in_progress"),
            _task("t-secret-2", "Sesli özet - tek paragraf", "in_progress"),
            _task("t-secret-3", "Eski görev döndü", "stopped"),
        ]
    }
    status = _status(_run("t-secret-1", 1), _run("t-secret-2", 2))
    approvals = [{"task_id": "t-secret-4", "title": "Yayın", "gate": "yayin"}]
    return _view(queue, status, approvals)


def test_busy_office_reads_as_one_paragraph_with_three_facts_and_no_task_id():
    view = _busy_view()
    assert view["cycle"]["running"] is True  # the fixture really is live
    text = office_paragraph(view)
    assert "\n" not in text
    assert text.startswith("Altı kişiden iki çalışan çalışıyor: ")
    assert "Ofis sayfası" in text and "iki sütun" not in text  # first clause only
    assert "Bir görev geri döndü." in text
    assert "Bir onayınız bekliyor." in text
    assert "t-secret" not in text
    assert len(text.split()) <= 60


def test_nothing_running_says_so():
    view = _view({"tasks": []}, None)
    assert office_paragraph(view) == "Ekip şu an çalışmıyor efendim."


def test_idle_team_still_reports_waiting_approvals():
    view = _view({"tasks": []}, None, [{"task_id": "x", "title": "Y", "gate": "fikir"}])
    assert office_paragraph(view) == "Ekip şu an çalışmıyor efendim. Bir onayınız bekliyor."


def test_usage_limit_and_estimated_cost_are_named_only_when_they_matter():
    queue = {"tasks": [_task("a", "Bir iş", "in_progress")]}
    ok = office_paragraph(_view(queue, _status(_run("a", 1))))
    assert "sınır" not in ok and "dolar" not in ok
    waiting = _status(_run("a", 1), usage={"state": "waiting", "resets_at": None}, usd=1.5)
    text = office_paragraph(_view(queue, waiting))
    assert "kullanım sınırı" in text.lower()
    assert "Tahmini maliyet 1,50 dolar" in text


def test_long_titles_are_cut_and_the_paragraph_stays_short():
    long = "Çok uzun bir başlık " + "kelime " * 30
    queue = {"tasks": [_task(f"t{n}", long, "in_progress") for n in range(3)]}
    status = _status(*[_run(f"t{n}", n) for n in range(3)], usd=3)
    text = office_paragraph(_view(queue, status))
    assert len(text.split()) <= 60
    assert "t0" not in text


def test_tool_answers_from_a_file_store_through_the_real_handler(tmp_path: Path):
    root = tmp_path / "team"
    root.mkdir()
    (root / "queue.json").write_text(
        json.dumps({"version": 1, "tasks": [_task("t1", "Gerçek iş", "stopped")]}),
        encoding="utf-8",
    )
    ctx = SimpleNamespace(live={"team_store": team_store.FileStore(root), "team_root": root})
    result = tools_team.team_status(ctx, {})
    assert result["status"] == "ok"
    assert result["speech"] == "Ekip şu an çalışmıyor efendim. Bir görev geri döndü."
    assert "t1" not in result["speech"]


def test_tool_is_spelled_for_the_registry_and_takes_no_arguments():
    from app.voice.realtime_sessions.tools import ToolRegistry

    reg = tools_team.register_team_tools(ToolRegistry())
    spec = reg.get("team.status") if hasattr(reg, "get") else None
    assert tools_team.TOOL_TEAM_STATUS == "team.status"
    assert spec is not None
    assert spec.parameters["properties"] == {}
    assert spec.parameters["additionalProperties"] is False


# ------------------------------------------- the voice counts what the page counts (2026-10-02)


def _role_run(role, task, n):
    return {"role": role, "task": task, "started_at": f"2026-10-01T11:0{n}:00Z"}


def test_three_inspections_are_three_at_work_for_the_ear_as_they_are_on_the_page():
    """The page counts RUNS since the fourth-seat work; the voice still counted working seats:
    three inspections on one seat were "3/6" on the page and "bir çalışan" aloud. The module's
    promise is that the two never disagree."""
    queue = {
        "tasks": [
            _task("t-1", "Birinci iş: liste", "inspecting", role="inspector"),
            _task("t-2", "İkinci iş", "inspecting", role="inspector"),
            _task("t-3", "Üçüncü iş", "inspecting", role="inspector"),
        ]
    }
    status = _status(
        _role_run("inspector", "t-1", 1),
        _role_run("inspector", "t-2", 2),
        _role_run("inspector", "t-3", 3),
    )
    view = _view(queue, status)
    assert view["cycle"]["running_agents"] == 3
    said = office_paragraph(view)
    assert said.startswith("Altı kişiden üç çalışan çalışıyor: "), said
    assert "Birinci iş" in said and "İkinci iş" in said and "Üçüncü iş" in said, said
    assert "t-1" not in said


def test_seven_runs_are_seven_of_seven_as_on_the_page():
    tasks = [_task(f"w-{n}", f"İş {n}", "in_progress") for n in range(1, 6)]
    tasks += [_task(f"i-{n}", f"Denetim {n}", "inspecting", role="inspector") for n in (1, 2)]
    runs = [_role_run("worker", f"w-{n}", n) for n in range(1, 6)]
    runs += [_role_run("inspector", f"i-{n}", 5 + n) for n in (1, 2)]
    view = _view({"tasks": tasks}, _status(*runs))
    assert (view["cycle"]["running_agents"], view["cycle"]["capacity"]) == (7, 7)
    said = office_paragraph(view)
    assert said.startswith("Yedi kişiden yedi çalışan çalışıyor: "), said
    assert said.count(",") <= 3 and "ve diğerleri" in said, said


def test_a_view_without_the_run_count_is_still_spoken_from_its_seats():
    """An older view (no ``running_agents``, no ``runs``): the seats are what there is."""
    view = _busy_view()
    view["cycle"].pop("running_agents", None)
    for agent in view["agents"]:
        agent.pop("runs", None)
    assert office_paragraph(view).startswith("Altı kişiden iki çalışan çalışıyor: ")
