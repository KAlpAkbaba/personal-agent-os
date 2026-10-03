"""The home PC's pulse, rule half (host-pulse-rules): ``scripts/lib/TeamHostPulse.ps1``.

The cycle will read four numbers before it opens a seat - free memory, the ``%TEMP%`` item
count, free space on C: and E:, and the processes a finished run left behind - and decide
"open a seat or not" from them. This card brings the readers and the pure rules only; nothing
is wired into ``scripts/team/cycle.ps1`` yet (a later card).

The lib is Windows PowerShell, driven here through PowerShell exactly as
``test_team_trials.py`` drives ``TeamQueue.ps1``. Every subprocess has a hang guard, every
scratch folder is under ``tmp_path`` (never the real ``%TEMP%`` root), and the output comes
back through a UTF-8 file so the Turkish lines survive the console code page.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
LIB = REPO / "scripts" / "lib" / "TeamHostPulse.ps1"
GB = 1024**3

_WINDOWS_POWERSHELL = (
    Path(os.environ.get("SystemRoot", r"C:\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)
POWERSHELL = str(_WINDOWS_POWERSHELL) if _WINDOWS_POWERSHELL.is_file() else shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    POWERSHELL is None, reason="no PowerShell: TeamHostPulse.ps1 cannot be run here"
)

HANG_GUARD_S = 120


def _ps(tmp_path: Path, body: str, inputs: Any = None, timeout: int = HANG_GUARD_S) -> Any:
    """Dot-source the lib under StrictMode, give it ``$in`` (JSON), return ``$out`` (JSON)."""
    in_file = tmp_path / "pulse-in.json"
    out_file = tmp_path / "pulse-out.json"
    in_file.write_bytes(json.dumps(inputs, ensure_ascii=False).encode("utf-8"))
    if out_file.exists():
        out_file.unlink()
    script = (
        "Set-StrictMode -Version Latest; $ErrorActionPreference = 'Stop'; "
        f". '{LIB}'; "
        f"$in = [System.IO.File]::ReadAllText('{in_file}', [System.Text.Encoding]::UTF8)"
        " | ConvertFrom-Json; "
        f"{body}; "
        "$json = ConvertTo-Json -InputObject $out -Depth 8 -Compress; "
        f"[System.IO.File]::WriteAllText('{out_file}', $json, "
        "(New-Object System.Text.UTF8Encoding($false)))"
    )
    done = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(out_file.read_bytes().decode("utf-8"))


ROOT_STARTED = "2026-10-03T05:00:00Z"
ROOT_FINISHED = "2026-10-03T05:30:00Z"
ROOT_SPAN = {"StartedAt": ROOT_STARTED, "FinishedAt": ROOT_FINISHED}


def _proc(
    pid: int, ppid: int, name: str, ws_gb: float = 0.1, created: str = "2026-10-03T05:00:05Z"
):
    return {
        "ProcessId": pid,
        "ParentProcessId": ppid,
        "Name": name,
        "WorkingSetBytes": int(ws_gb * GB),
        "CreationDate": created,
    }


def _pulse_inputs(
    free_percent: float = 40,
    temp: int = 1000,
    drives: dict[str, int | None] | None = None,
    processes: list[dict[str, Any]] | None = None,
    roots: list[dict[str, Any]] | None = None,
    too_large: bool = False,
) -> dict[str, Any]:
    drives = drives if drives is not None else {"C": 100 * GB, "E": 100 * GB}
    return {
        "memory": {
            "FreeBytes": int(free_percent * GB),
            "TotalBytes": 100 * GB,
            "FreePercent": free_percent,
        },
        "temp": {"Count": temp, "TooLarge": too_large, "TopPrefix": "corpus"},
        "drives": [
            {"Drive": d, "FreeBytes": free, "Missing": free is None} for d, free in drives.items()
        ],
        "processes": processes or [],
        "roots": roots or [],
    }


CHECK = (
    "$pulse = Get-TeamHostPulse -Memory $in.memory -Temp $in.temp -Drives @($in.drives) "
    "-Processes @($in.processes) -RunRoots @($in.roots); "
    "$th = Get-TeamPulseThresholds -Settings ([pscustomobject]@{}); "
    "$check = Test-TeamHostPulse -Pulse $pulse -Thresholds $th; "
    "$out = @{ ok = [bool]$check.Ok; reasons = @($check.Reasons); "
    "line = (Format-TeamPulseLine -Pulse $pulse -Check $check); "
    "orphans = @($pulse.Orphans | ForEach-Object { [int]$_.ProcessId }) }"
)


# (1) memory -------------------------------------------------------------------------------


def test_memory_below_threshold_stops_new_seats_and_names_the_largest_process(tmp_path):
    processes = [
        _proc(500, 1, "claude", 0.5, "2026-10-03T04:59:59Z"),
        _proc(501, 500, "pytest", 14.2),
        _proc(502, 500, "uv", 0.1),
    ]
    roots = [{"Pid": 500, "TaskId": "test-slots", "Finished": False, "StartedAt": ROOT_STARTED}]
    got = _ps(tmp_path, CHECK, _pulse_inputs(free_percent=8, processes=processes, roots=roots))
    assert got["ok"] is False
    assert any("Bellek %8" in r for r in got["reasons"]), got
    line = got["line"]
    assert line.startswith("Makine: bellek %8, TEMP 1000, C: 100 GB, E: 100 GB"), line
    assert "yeni iş başlatılmadı" in line
    assert "en büyük süreç: pytest, 14 GB, test-slots" in line, line


def test_memory_exactly_at_threshold_passes(tmp_path):
    got = _ps(tmp_path, CHECK, _pulse_inputs(free_percent=15))
    assert got["ok"] is True, got
    assert got["reasons"] == []


def test_healthy_machine_passes_and_the_line_has_no_reason_part(tmp_path):
    got = _ps(tmp_path, CHECK, _pulse_inputs(free_percent=40, temp=1000))
    assert got["ok"] is True, got
    assert got["line"] == "Makine: bellek %40, TEMP 1000, C: 100 GB, E: 100 GB"
    assert "yeni iş" not in got["line"]


# (2) TEMP ---------------------------------------------------------------------------------


def test_temp_of_two_and_a_half_million_fails_with_a_temp_reason(tmp_path):
    got = _ps(tmp_path, CHECK, _pulse_inputs(temp=2_671_896))
    assert got["ok"] is False
    assert any("TEMP" in r and "2671896" in r for r in got["reasons"]), got
    assert "yeni iş başlatılmadı" in got["line"]


def _temp_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "fake-temp"
    folder.mkdir()
    for i in range(179):
        (folder / f"corpus_{i}").mkdir()
    sub = folder / "corpus_sub"  # 180th corpus entry, holding 50 more that must not count
    sub.mkdir()
    for i in range(50):
        (sub / f"pytest-{i}.txt").write_bytes(b"")
    for i in range(120):
        (folder / f"pytest-of-{i}.tmp").write_bytes(b"x")
    return folder


def test_temp_count_is_top_level_only_and_finds_the_commonest_prefix(tmp_path):
    folder = _temp_folder(tmp_path)
    got = _ps(tmp_path, f"$out = Measure-TeamTempItems -Path '{folder}'")
    assert got["Count"] == 300
    assert got["TooLarge"] is False
    assert got["TopPrefix"] == "corpus"


def test_temp_count_with_an_expired_budget_is_too_large_and_returns(tmp_path):
    folder = _temp_folder(tmp_path)
    started = time.monotonic()
    got = _ps(tmp_path, f"$out = Measure-TeamTempItems -Path '{folder}' -BudgetMs 0")
    assert time.monotonic() - started < HANG_GUARD_S
    assert got["TooLarge"] is True, got
    assert got["Count"] < 300


def test_temp_count_on_a_missing_path_is_missing_not_thrown(tmp_path):
    got = _ps(tmp_path, f"$out = Measure-TeamTempItems -Path '{tmp_path / 'no-such-temp'}'")
    assert got == {"Count": 0, "TooLarge": False, "TopPrefix": "", "Missing": True}

    inputs = _pulse_inputs()
    inputs["temp"] = got
    line = _ps(tmp_path, CHECK, inputs)
    assert line["ok"] is True, line
    assert line["line"] == "Makine: bellek %40, TEMP yok, C: 100 GB, E: 100 GB"


def test_a_count_that_ran_out_of_time_under_the_maximum_does_not_fail(tmp_path):
    # inspector 2026-10-03: a 2 s budget that stopped at 20 121 of 40 000 items failed the check
    # and stopped every seat far below max_temp_items; out of time is shown, the maximum decides
    got = _ps(tmp_path, CHECK, _pulse_inputs(temp=120_000, too_large=True))
    assert got["ok"] is True, got
    assert got["line"] == "Makine: bellek %40, TEMP 120000+, C: 100 GB, E: 100 GB"


@pytest.mark.parametrize(
    ("temp", "ok"), [(499_999, True), (500_000, True), (500_001, False)], ids=lambda v: str(v)
)
def test_temp_at_the_maximum_passes_and_one_above_fails(tmp_path, temp, ok):
    # Danışman 2026-10-03 21:00: a -gt -> -ge mutation survived; equal passes, above fails
    got = _ps(tmp_path, CHECK, _pulse_inputs(temp=temp))
    assert got["ok"] is ok, got
    assert any("TEMP" in r for r in got["reasons"]) is (not ok), got


def test_a_count_that_ran_out_of_time_above_the_maximum_fails(tmp_path):
    got = _ps(tmp_path, CHECK, _pulse_inputs(temp=500_001, too_large=True))
    assert got["ok"] is False
    assert any("TEMP" in r and "500001+" in r for r in got["reasons"]), got


def test_temp_count_counts_past_the_prefix_sample(tmp_path):
    # the prefix is taken from the first -PrefixSample names only (that is what keeps the count
    # fast enough for the 2 s budget to reach max_temp_items); the count itself goes on
    folder = _temp_folder(tmp_path)
    got = _ps(tmp_path, f"$out = Measure-TeamTempItems -Path '{folder}' -PrefixSample 10")
    assert got["Count"] == 300
    assert got["TooLarge"] is False
    assert got["TopPrefix"] in ("corpus", "pytest")


# (3) drives -------------------------------------------------------------------------------


def test_a_drive_below_twenty_gb_fails_naming_that_drive(tmp_path):
    got = _ps(tmp_path, CHECK, _pulse_inputs(drives={"C": 12 * GB, "E": 300 * GB}))
    assert got["ok"] is False
    assert len(got["reasons"]) == 1
    assert got["reasons"][0].startswith("C:"), got
    assert "C: 12 GB" in got["line"]


def test_a_drive_exactly_at_twenty_gb_passes(tmp_path):
    got = _ps(tmp_path, CHECK, _pulse_inputs(drives={"C": 20 * GB, "E": 300 * GB}))
    assert got["ok"] is True, got
    assert got["reasons"] == []


def test_a_missing_drive_is_named_in_the_line_not_thrown(tmp_path):
    got = _ps(tmp_path, CHECK, _pulse_inputs(drives={"C": 100 * GB, "E": None}))
    assert got["ok"] is True, got
    assert "E: yok" in got["line"]

    # the reader itself: a drive letter that does not exist comes back Missing
    reader = _ps(
        tmp_path,
        "$out = @(Get-TeamDriveFree -Drives @('C') -Source { param($d) $null })",
    )
    assert reader == [{"Drive": "C", "FreeBytes": None, "Missing": True}]


# (4) TEMP growth --------------------------------------------------------------------------

GROWTH = (
    "$y = $null; if ($null -ne $in.yesterday) { $y = $in.yesterday }; "
    "$g = Compare-TeamTempGrowth -Today $in.today -Yesterday $y -Threshold 100000; "
    "$out = @{ grew = [bool]$g.Grew; delta = $g.Delta; line = $g.Line }"
)


def test_temp_growth_above_the_alarm_is_reported_with_the_prefix(tmp_path):
    got = _ps(
        tmp_path,
        GROWTH,
        {"today": {"Count": 300_001, "TopPrefix": "corpus"}, "yesterday": {"Count": 200_000}},
    )
    assert got["grew"] is True
    assert got["delta"] == 100_001
    assert got["line"] == "geçici klasör büyüyor: +100001, en sık önek corpus"


def test_temp_growth_exactly_at_the_alarm_is_not_growth(tmp_path):
    got = _ps(
        tmp_path,
        GROWTH,
        {"today": {"Count": 300_000, "TopPrefix": "corpus"}, "yesterday": {"Count": 200_000}},
    )
    assert got["grew"] is False
    assert got["delta"] == 100_000


def test_temp_growth_without_yesterday_is_not_growth(tmp_path):
    got = _ps(
        tmp_path, GROWTH, {"today": {"Count": 2_000_000, "TopPrefix": "corpus"}, "yesterday": None}
    )
    assert got["grew"] is False


# (5) orphan tree --------------------------------------------------------------------------

ORPHANS = (
    "$out = @(Get-TeamOrphanTree -Processes @($in.processes) -RunRoots @($in.roots) "
    "| ForEach-Object { [int]$_.ProcessId })"
)


def test_a_finished_runs_live_descendants_are_its_orphans(tmp_path):
    processes = [
        _proc(101, 100, "tail"),
        _proc(102, 100, "grep"),
        _proc(103, 101, "conhost", created="2026-10-03T05:00:06Z"),
    ]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    got = _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots})
    assert sorted(got) == [101, 102, 103]


def test_a_running_roots_children_are_not_orphans(tmp_path):
    processes = [_proc(200, 1, "claude", created="2026-10-03T04:59:59Z"), _proc(201, 200, "pytest")]
    roots = [{"Pid": 200, "TaskId": "x", "Finished": False, "StartedAt": ROOT_STARTED}]
    assert _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}) == []


def test_a_process_outside_the_recorded_trees_is_never_an_orphan(tmp_path):
    processes = [
        _proc(101, 100, "tail"),
        _proc(900, 4, "pytest", 14),  # the owner's own pytest, same name, other tree
        _proc(901, 900, "python"),
        _proc(902, 3, "chrome"),
    ]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    assert _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}) == [101]


def test_a_child_of_an_earlier_process_with_the_roots_pid_is_not_a_descendant(tmp_path):
    processes = [
        # an older process that held pid 100 before the run, and its child: pid reuse
        _proc(100, 4, "vrserver", created="2026-10-03T03:00:00Z"),
        _proc(150, 100, "vrcompositor", created="2026-10-03T03:00:01Z"),
        _proc(101, 100, "tail"),
    ]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    assert _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}) == [101]


def test_a_dead_roots_pid_reused_later_does_not_make_the_new_owners_children_orphans(tmp_path):
    # inspector 2026-10-03: the root (pid 100) finished and died; the owner's Chrome started an
    # hour later and got pid 100; its renderer was returned as an orphan to be closed
    processes = [
        _proc(101, 100, "tail"),  # the run's own leftover, created while the root lived
        _proc(100, 4, "chrome", created="2026-10-03T06:00:00Z"),
        _proc(300, 100, "chrome-renderer", created="2026-10-03T06:00:01Z"),
        _proc(301, 300, "chrome-gpu", created="2026-10-03T06:00:02Z"),
    ]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    assert _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}) == [101]


def test_a_reused_root_pid_whose_reuser_already_exited_hands_over_nothing_of_the_owner(tmp_path):
    # Danışman 2026-10-03 21:00 (inspector reproduced it): the root (pid 100) finished and died; a
    # short-lived launcher later took pid 100, started the owner's Chrome and exited. Nothing holds
    # pid 100 at the sweep, so only FinishedAt can tell the run's children from the launcher's.
    processes = [
        _proc(101, 100, "tail"),  # the run's own leftover, created while the run lived
        _proc(300, 100, "chrome", created="2026-10-03T06:00:01Z"),
        _proc(301, 300, "chrome-gpu", created="2026-10-03T06:00:02Z"),
    ]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    assert _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}) == [101]


def test_a_finished_root_without_finished_at_closes_nothing(tmp_path):
    # in doubt a process is the owner's: without the run's end there is no bound on a reuser
    processes = [_proc(101, 100, "tail"), _proc(102, 101, "grep")]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, "StartedAt": ROOT_STARTED}]
    assert _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}) == []


def test_a_child_of_an_exited_intermediate_is_not_reached(tmp_path):
    # the run's child 101 exited; its pid was reused by an owner process created after the run
    # ended, whose child must not be reached; the run's own grandchild 103 (parent 101 dead) is a
    # safe miss - the walk only goes through processes alive in the snapshot
    processes = [
        _proc(103, 101, "conhost", created="2026-10-03T05:00:06Z"),
        _proc(101, 4, "vrmonitor", created="2026-10-03T06:00:00Z"),
        _proc(400, 101, "vrdashboard", created="2026-10-03T06:00:01Z"),
        _proc(102, 100, "grep"),
    ]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    assert _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}) == [102]


def test_a_finished_root_still_alive_keeps_its_children(tmp_path):
    # the root's own process (created just before StartedAt was taken) is not a reuse
    processes = [
        _proc(100, 4, "claude", created="2026-10-03T04:59:59Z"),
        _proc(101, 100, "tail"),
    ]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    assert _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}) == [101]


def test_a_parent_cycle_in_the_snapshot_terminates(tmp_path):
    processes = [
        _proc(101, 100, "a"),
        _proc(102, 101, "b"),
        _proc(101, 102, "a-again"),  # a stale snapshot can hold a -> b -> a
    ]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    got = _ps(tmp_path, ORPHANS, {"processes": processes, "roots": roots}, timeout=60)
    assert sorted(set(got)) == [101, 102]


def test_orphans_alone_do_not_fail_the_check_but_are_named(tmp_path):
    processes = [_proc(101, 100, "tail"), _proc(102, 100, "grep")]
    roots = [{"Pid": 100, "TaskId": "x", "Finished": True, **ROOT_SPAN}]
    got = _ps(tmp_path, CHECK, _pulse_inputs(processes=processes, roots=roots))
    assert got["ok"] is True, got
    assert sorted(got["orphans"]) == [101, 102]
    assert "biten koşudan kalan süreç: 2 (tail, grep)" in got["line"], got["line"]
    assert "yeni iş" not in got["line"]


# (6) thresholds ---------------------------------------------------------------------------

DEFAULTS = {
    "min_free_memory_percent": 15,
    "max_temp_items": 500000,
    "min_drive_free_gb": 20,
    "temp_growth_alarm": 100000,
}
THRESHOLDS = (
    "$t = Get-TeamPulseThresholds -Settings $settings; "
    "$out = @{ min_free_memory_percent = $t.min_free_memory_percent; "
    "max_temp_items = $t.max_temp_items; min_drive_free_gb = $t.min_drive_free_gb; "
    "temp_growth_alarm = $t.temp_growth_alarm }"
)


def test_thresholds_default_on_an_empty_settings_object(tmp_path):
    got = _ps(tmp_path, "$settings = [pscustomobject]@{}; " + THRESHOLDS)
    assert got == DEFAULTS


def test_thresholds_default_on_todays_cycle_settings_file_which_is_not_changed(tmp_path):
    settings = REPO / "team" / "cycle-settings.json"
    before = hashlib.sha256(settings.read_bytes()).hexdigest()
    body = (
        f"$settings = [System.IO.File]::ReadAllText('{settings}', [System.Text.Encoding]::UTF8)"
        " | ConvertFrom-Json; " + THRESHOLDS
    )
    assert _ps(tmp_path, body) == DEFAULTS
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == before


def test_thresholds_take_a_present_key(tmp_path):
    got = _ps(
        tmp_path, "$settings = [pscustomobject]@{ min_free_memory_percent = 25 }; " + THRESHOLDS
    )
    assert got == {**DEFAULTS, "min_free_memory_percent": 25}


# (7) the real readers on this machine -----------------------------------------------------


def test_the_real_readers_return_numbers_of_the_right_type(tmp_path):
    folder = _temp_folder(tmp_path)
    body = (
        "$m = Get-TeamHostMemory; "
        "$d = @(Get-TeamDriveFree -Drives @('C')); "
        "$p = @(Get-TeamProcessSnapshot); "
        f"$tc = Measure-TeamTempItems -Path '{folder}'; "
        "$self = @($p | Where-Object { $_.ProcessId -eq $PID }); "
        "$out = @{ free = $m.FreePercent; freeBytes = $m.FreeBytes; total = $m.TotalBytes; "
        "c = $d[0].FreeBytes; cMissing = [bool]$d[0].Missing; procs = $p.Count; "
        "selfFound = $self.Count; "
        "selfWs = $(if ($self.Count) { $self[0].WorkingSetBytes } else { 0 }); "
        "temp = $tc.Count }"
    )
    got = _ps(tmp_path, body)
    print("real readers on this machine:", got)
    assert isinstance(got["free"], (int, float)) and 0 <= got["free"] <= 100
    assert 0 < got["freeBytes"] <= got["total"]
    assert got["cMissing"] is False and isinstance(got["c"], int) and got["c"] > 0
    assert got["procs"] > 10
    assert got["selfFound"] == 1 and got["selfWs"] > 0
    assert got["temp"] == 300
