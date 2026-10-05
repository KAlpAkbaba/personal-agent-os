# ruff: noqa: E501 - the PowerShell that drives the cycle's own functions is kept on its lines
"""The live status' bounds (task team-status-bounds, an ADR-0214 addendum).

``PUT /v1/team/queue/status`` accepted what the store cannot hold or what means nothing: a
timestamp longer than ``team_state.updated_at`` (VARCHAR(32): a 500 on PostgreSQL, a 200 on
SQLite and the file store), ``used_pct`` of 1e999 (a 500: the refusal could not be written as
JSON), -5 or 250000, and an infinite ``estimated_usd``. Each bound is held here through the
real application object, with its own code and a Turkish message, and nothing is written.

What the cycle writes is taken from the cycle itself: ``New-CycleStatus`` and
``Get-LimitsDocument`` are lifted out of ``scripts/team/cycle.ps1`` and run under Windows
PowerShell in seven situations, and every document they make must be a 200.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import typing
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.team import routes
from app.team import store as team_store
from app.team.models import TeamStateRow
from tests.identity_support import authenticate, install_identity

REPO = Path(__file__).resolve().parents[4]
STATUS = "/v1/team/queue/status"
OPUS, SONNET = "claude-opus-5-5", "claude-sonnet-5-5"
STAMP = "2026-10-03T03:00:00Z"
#: The four refusals this task adds. The route documents them in ``STATUS_REFUSALS``.
CODES = {
    "status_stamp_too_long",
    "status_model_id_too_long",
    "status_used_pct_invalid",
    "status_estimated_usd_invalid",
}
TURKISH = set("çğıöşüÇĞİÖŞÜ")


def _full(**extra: Any) -> dict[str, Any]:
    """A status with every part the request model has: a run, both windows, one lowered."""
    doc: dict[str, Any] = {
        "cycle_id": "d20261003",
        "machine": "MAIL",
        "pid": 7,
        "started_at": STAMP,
        "runs": [
            {
                "task": "a-task",
                "role": "worker",
                "started_at": STAMP,
                "model": OPUS,
                "last_activity_at": STAMP,
                "idle_minutes": 34,
                "stuck_children": [{"pid": 4242, "name": "python.exe", "idle_minutes": 34}],
            }
        ],
        "estimated_usd": 1.5,
        "usage_limit": {"state": "waiting", "resets_at": STAMP},
        "limits": {
            "fable": {"state": "limited", "resets_at": STAMP, "used_pct": 100},
            "all": {"state": "ok", "resets_at": STAMP, "used_pct": 47.5},
            "fallback": True,
            "lowered": [
                {"task": "a-task", "role": "worker", "from": OPUS, "to": SONNET, "at": STAMP}
            ],
        },
        "updated_at": STAMP,
    }
    doc.update(extra)
    return doc


def _previous() -> dict[str, Any]:
    return _full(cycle_id="the-previous-one", updated_at="2026-10-03T02:59:00Z")


# ------------------------------------------------------------------ the application


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    TeamStateRow.__table__.create(eng)
    yield eng
    eng.dispose()


def _make_store(kind: str, engine, tmp_path: Path) -> team_store.TeamStore:
    if kind == "db":
        return team_store.DbStore(sessionmaker(bind=engine, expire_on_commit=False))
    root = tmp_path / "team"
    (root / "reports").mkdir(parents=True)
    (root / "queue.json").write_text('{"version": 1, "tasks": []}\n', encoding="utf-8")
    (root / "lock.json").write_text('{"held": false}\n', encoding="utf-8")
    return team_store.FileStore(root)


@pytest.fixture(params=["file", "db"])
def owner(request, engine, tmp_path):
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    app.state.team_store = _make_store(request.param, engine, tmp_path)
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    # the previous document every refusal must leave in place
    put = client.put(STATUS, json=_previous())
    assert put.status_code == 200, put.text
    return client


def _put_raw(client: TestClient, text: str):
    return client.put(STATUS, content=text, headers={"Content-Type": "application/json"})


def _assert_refused(response, code: str, client: TestClient) -> None:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, dict), f"not the route's refusal shape: {response.text}"
    assert detail["code"] == code, response.text
    assert detail["code"] in CODES
    assert detail["code"] in getattr(routes, "STATUS_REFUSALS", {})
    assert TURKISH & set(detail["message"]), f"the message is not Turkish: {detail['message']}"
    # nothing was written: the status is the previous document
    assert client.get(STATUS).json() == _previous()


# ------------------------------------------------------------------ (1) the model id


def test_a_model_id_of_64_characters_is_accepted_and_65_is_refused(owner):
    for place in ("run", "from", "to"):
        doc = _full()
        target = doc["runs"][0] if place == "run" else doc["limits"]["lowered"][0]
        key = "model" if place == "run" else place
        target[key] = "m" * 64
        assert owner.put(STATUS, json=doc).status_code == 200, place
        assert owner.get(STATUS).json() == doc
        owner.put(STATUS, json=_previous())
        target[key] = "m" * 65
        _assert_refused(owner.put(STATUS, json=doc), "status_model_id_too_long", owner)


# ------------------------------------------------------------------ (2) every timestamp


def _nested_models(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    found: list[type[BaseModel]] = []
    for arg in typing.get_args(annotation):
        found.extend(_nested_models(arg))
    return found


def _is_stamp(name: str) -> bool:
    return name == "at" or name.endswith("_at")


def _stamp_fields(model: type[BaseModel]) -> set[tuple[str, str]]:
    """Every timestamp field of the request model, by (model, field): read from the model, so a
    field added later is counted here."""
    found: set[tuple[str, str]] = set()
    for name, field in model.model_fields.items():
        key = field.alias or name
        if _is_stamp(key):
            found.add((model.__name__, key))
        for inner in _nested_models(field.annotation):
            found |= _stamp_fields(inner)
    return found


def _stamp_paths(model: type[BaseModel], doc: Any, prefix: tuple = ()) -> list[tuple]:
    """Where in ``doc`` each timestamp field of ``model`` sits, with the model that owns it."""
    paths: list[tuple] = []
    for name, field in model.model_fields.items():
        key = field.alias or name
        if not isinstance(doc, dict) or key not in doc:
            continue
        if _is_stamp(key):
            paths.append(((model.__name__, key), (*prefix, key)))
        for inner in _nested_models(field.annotation):
            value = doc[key]
            if isinstance(value, list):
                for index, item in enumerate(value):
                    paths.extend(_stamp_paths(inner, item, (*prefix, key, index)))
            else:
                paths.extend(_stamp_paths(inner, value, (*prefix, key)))
    return paths


def _set(doc: dict, path: tuple, value: Any) -> None:
    for step in path[:-1]:
        doc = doc[step]
    doc[path[-1]] = value


STAMP_PATHS = _stamp_paths(routes.StatusRequest, _full())


def test_every_timestamp_field_of_the_request_model_is_in_the_sample():
    named = _stamp_fields(routes.StatusRequest)
    assert {owner for owner, _ in STAMP_PATHS} == named
    # started_at twice (the cycle's, a run's), updated_at, three resets_at, a lowered's at, a
    # run's last_activity_at (pm-stuck-run-check)
    assert len(named) >= 5 and len(STAMP_PATHS) == 8, STAMP_PATHS


def test_the_bound_is_the_width_of_the_column_that_keeps_updated_at():
    width = TeamStateRow.__table__.c.updated_at.type.length
    assert width == 32
    assert getattr(routes, "STAMP_MAX", None) == width


@pytest.mark.parametrize("path", [path for _, path in STAMP_PATHS], ids=str)
def test_a_timestamp_of_32_characters_is_accepted_and_33_is_refused(owner, path):
    doc = _full()
    _set(doc, path, "2" * 32)
    assert owner.put(STATUS, json=doc).status_code == 200
    assert owner.get(STATUS).json() == doc
    owner.put(STATUS, json=_previous())
    _set(doc, path, "2" * 33)
    _assert_refused(owner.put(STATUS, json=doc), "status_stamp_too_long", owner)


# ------------------------------------------------------------------ (3)(4) the two numbers


def _with_number(field: str, literal: str) -> str:
    """The document as JSON text with ``literal`` (1e999, NaN, a quoted string) where the
    number is: a client may send what ``json=`` could not."""
    doc = _full()
    if field == "used_pct":
        doc["limits"]["fable"]["used_pct"] = "@@"
    else:
        doc["estimated_usd"] = "@@"
    return json.dumps(doc).replace('"@@"', literal)


@pytest.mark.parametrize("value", ["0", "100", "37.5"])
def test_used_pct_from_0_to_100_is_accepted(owner, value):
    text = _with_number("used_pct", value)
    assert _put_raw(owner, text).status_code == 200
    assert owner.get(STATUS).json() == json.loads(text)


@pytest.mark.parametrize(
    "value", ["-5", "100.01", "250000", "1e999", "-1e999", "NaN", "Infinity", '"Infinity"']
)
def test_used_pct_outside_0_to_100_or_not_finite_is_refused(owner, value):
    response = _put_raw(owner, _with_number("used_pct", value))
    _assert_refused(response, "status_used_pct_invalid", owner)


def test_used_pct_in_the_all_window_is_held_the_same_way(owner):
    doc = _full()
    doc["limits"]["all"]["used_pct"] = 250000
    _assert_refused(owner.put(STATUS, json=doc), "status_used_pct_invalid", owner)


@pytest.mark.parametrize("value", ["0", "3.61"])
def test_estimated_usd_zero_or_more_is_accepted(owner, value):
    text = _with_number("estimated_usd", value)
    assert _put_raw(owner, text).status_code == 200
    assert owner.get(STATUS).json() == json.loads(text)


@pytest.mark.parametrize("value", ["-1", "1e999", "NaN", "-Infinity", '"NaN"'])
def test_estimated_usd_negative_or_not_finite_is_refused(owner, value):
    response = _put_raw(owner, _with_number("estimated_usd", value))
    _assert_refused(response, "status_estimated_usd_invalid", owner)


def test_every_documented_refusal_has_a_turkish_message():
    documented = getattr(routes, "STATUS_REFUSALS", {})
    assert set(documented) == CODES
    for code, message in documented.items():
        assert TURKISH & set(message), code


def test_another_broken_field_is_still_a_422_and_never_a_500(owner):
    """The other refusals keep the framework's shape; a non-finite number in one of them must
    not make the refusal itself unwritable."""
    doc = _full()
    text = json.dumps(doc).replace('"pid": 7', '"pid": 1e999')
    response = _put_raw(owner, text)
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)
    assert owner.get(STATUS).json() == _previous()


# ------------------------------------------------------------------ what the cycle writes


_GENERATOR = r"""
param([string]$Repo)
$ErrorActionPreference = "Stop"
. (Join-Path $Repo "scripts\lib\TeamQueue.ps1")
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $Repo "scripts\team\cycle.ps1"), [ref]$tokens, [ref]$errors)
foreach ($name in @("Get-LimitsDocument", "New-CycleStatus")) {
    $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    if ($null -eq $fn) { throw "$name is not in cycle.ps1" }
    . ([scriptblock]::Create($fn.Extent.Text))
}
$CycleId = "d20261003"
$Machine = "GMKADIRAKBABA-OFFICE-PC"
$now = [datetime]::UtcNow
$chain = @(Get-TeamModelChain)

function Reset-Scenario {
    $script:cycle = [pscustomobject]@{ started_at = (Get-TeamTimestamp -Now $now.AddHours(-1)); spent_usd = 0.0 }
    $script:liveRuns = New-Object System.Collections.ArrayList
    $script:usageLimit = [pscustomobject]@{ state = "ok"; resets_at = $null }
    $script:limitedModels = @{}
    $script:limitWindows = @{}
    $script:loweredRuns = New-Object System.Collections.ArrayList
    $script:modelSetting = [pscustomobject]@{ fallback = $true }
    # pm-stuck-run-check: the cycle's liveness look is on and its bound is the setting's.
    $script:livenessOn = $true
    $script:runIdleMinutes = 30
}
function Add-Run([string]$Task, [string]$Role, [string]$Model) {
    [void]$script:liveRuns.Add([pscustomobject]@{ task = $Task; role = $Role; started_at = (Get-TeamTimestamp); model = $Model })
}
function Add-Lowered([string]$Task) {
    [void]$script:loweredRuns.Add([ordered]@{ task = $Task; role = "worker"; from = $chain[1]; to = $chain[2]; at = (Get-TeamTimestamp) })
}
function Limit-Model([string]$Model, [string]$Until) {
    $script:limitedModels[$Model] = [pscustomobject]@{ until = $Until; type = "weekly"; seen_at = (Get-TeamTimestamp) }
}
function Write-Shape([string]$Name, [bool]$Legacy = $false) {
    $doc = New-CycleStatus -Legacy $Legacy
    [Console]::Out.WriteLine($Name + "`t" + (ConvertTo-Json -InputObject $doc -Depth 12 -Compress))
}
$later = Get-TeamTimestamp -Now $now.AddHours(3)

Reset-Scenario
Write-Shape "no runs"

Reset-Scenario
Add-Run "a-task" "worker" $chain[2]; Add-Run "b-task" "inspector" $chain[0]; Add-Run "c-task" "worker" $chain[1]
# A run with no sign of life, as Watch-RunLiveness leaves it: its stuck child named.
$script:liveRuns[2] | Add-Member -NotePropertyName last_activity_at -NotePropertyValue (Get-TeamTimestamp -Now $now.AddMinutes(-34)) -Force
$script:liveRuns[2] | Add-Member -NotePropertyName idle_minutes -NotePropertyValue 34 -Force
$script:liveRuns[2] | Add-Member -NotePropertyName stuck_children -NotePropertyValue @([pscustomobject]@{ pid = 4242; name = "python.exe"; idle_minutes = 34 }) -Force
foreach ($i in 1..20) { Add-Lowered ("task-{0:00}" -f $i) }
$script:cycle.spent_usd = 3.61237
Write-Shape "three runs, twenty lowered"

Reset-Scenario
foreach ($model in $chain) { Limit-Model $model $later }
$script:limitWindows["fable"] = [pscustomobject]@{ used_pct = 100; resets_at = $later; observed_at = (Get-TeamTimestamp) }
$script:limitWindows["all"] = [pscustomobject]@{ used_pct = 100; resets_at = $later; observed_at = (Get-TeamTimestamp) }
$script:usageLimit = [pscustomobject]@{ state = "stopped"; resets_at = $later }
Write-Shape "every model limited"

Reset-Scenario
Add-Run "a-task" "worker" $chain[2]; Add-Lowered "a-task"
Write-Shape "one lowered"

Reset-Scenario
Add-Run "a-task" "worker" $chain[1]
Write-Shape "the old shape" $true

Reset-Scenario
Add-Run "a-task" "worker" $chain[1]
$script:limitWindows["fable"] = [pscustomobject]@{ used_pct = 37.5; resets_at = $later; observed_at = (Get-TeamTimestamp) }
$script:limitWindows["all"] = [pscustomobject]@{ used_pct = 0; resets_at = $null; observed_at = (Get-TeamTimestamp) }
$script:usageLimit = [pscustomobject]@{ state = "waiting"; resets_at = $later }
Write-Shape "waiting, the tool's percentages"

Reset-Scenario
Limit-Model $chain[0] ""
$script:modelSetting = [pscustomobject]@{ fallback = $false }
$script:usageLimit = [pscustomobject]@{ state = "stopped"; resets_at = $null }
Write-Shape "fable limited undated, no fallback, stopped"
"""


@pytest.fixture(scope="module")
def cycle_shapes(tmp_path_factory) -> list[tuple[str, dict[str, Any]]]:
    system = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    windows = system / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    shell = shutil.which("powershell") or (str(windows) if windows.is_file() else None)
    if shell is None:
        pytest.skip("the cycle's functions need Windows PowerShell")
    script = tmp_path_factory.mktemp("cycle-status") / "shapes.ps1"
    script.write_text(_GENERATOR, encoding="utf-8-sig")
    done = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Repo",
            str(REPO),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    shapes = []
    for line in done.stdout.splitlines():
        if "\t" in line:
            name, text = line.split("\t", 1)
            shapes.append((name, json.loads(text)))
    return shapes


def test_the_seven_shapes_the_cycle_writes_are_all_accepted(owner, cycle_shapes):
    assert len(cycle_shapes) == 7, [name for name, _ in cycle_shapes]
    accepted = 0
    for name, doc in cycle_shapes:
        response = owner.put(STATUS, json=doc)
        assert response.status_code == 200, (name, response.text)
        assert owner.get(STATUS).json() == doc, name
        accepted += 1
    assert accepted == 7
    shapes = dict(cycle_shapes)
    assert "limits" not in shapes["the old shape"]
    assert len(shapes["three runs, twenty lowered"]["limits"]["lowered"]) == 20
    stuck = shapes["three runs, twenty lowered"]["runs"][2]
    assert stuck["idle_minutes"] == 34
    assert stuck["stuck_children"] == [{"pid": 4242, "name": "python.exe", "idle_minutes": 34}]
    assert shapes["three runs, twenty lowered"]["run_idle_minutes"] == 30
    assert "run_idle_minutes" not in shapes["the old shape"]
    assert shapes["every model limited"]["limits"]["all"]["state"] == "limited"
    assert shapes["waiting, the tool's percentages"]["limits"]["fable"]["used_pct"] == 37.5
