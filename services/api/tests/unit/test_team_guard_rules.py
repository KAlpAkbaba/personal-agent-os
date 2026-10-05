# ruff: noqa: E501 - the PowerShell driver that runs the rules is kept on its lines
"""What happens to a guard run's result: ``scripts/lib/TeamGuardRules.ps1``.

The runner (``scripts/team/guards.ps1``) runs ``team/guards.json`` on one tree and writes a
RESULT. This layer decides what that RESULT means for a task card: the ``guards`` field, the
lines the inspector sees, the lead's wiring list, whether the gate may start, the Ofis
sentence. The library is pure, so the whole file runs in ONE Windows PowerShell process: a
driver written to a temporary directory dot-sources the library, runs every case below over
the fixtures of a cases file and prints one JSON document; each test reads its own case.

Case 10 is the join of the two halves: the REAL runner is run (from inside that one driver)
on a scratch git repository with one failing fake guard, and the RESULT file it wrote is what
the library reads.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
RULES_LIB = REPO / "scripts" / "lib" / "TeamGuardRules.ps1"
QUEUE_LIB = REPO / "scripts" / "lib" / "TeamQueue.ps1"
GUARDS_SCRIPT = REPO / "scripts" / "team" / "guards.ps1"
GUARD_LIST = REPO / "team" / "guards.json"

NOW = "2026-10-04T02:00:00Z"
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_I = "1" * 40
VERDICT_WORDS = ("APPROVE", "RETURN", "REJECT")


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    candidate = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return str(candidate) if candidate.is_file() else None


pytestmark = pytest.mark.skipif(
    _powershell() is None, reason="Windows PowerShell is not on this machine"
)


# ----------------------------------------------------------------------------- fixtures


def _row(guard_id: str, outcome: str, label: str, detail: str = "") -> dict:
    return {
        "id": guard_id,
        "path": f"services/api/tests/unit/test_{guard_id.replace('-', '_')}.py",
        "outcome": outcome,
        "seconds": 1.5,
        "label": label,
        "detail": detail if outcome != "green" else "",
    }


def _result(sha: str, rows: list[dict], at: str = "2026-10-04T01:00:00Z") -> dict:
    green = all(row["outcome"] == "green" for row in rows)
    return {
        "at": at,
        "sha": sha,
        "seconds": 9.5,
        "status": "green" if green else "red",
        "rows": rows,
    }


def _task(task_id: str, state: str) -> dict:
    return {
        "id": task_id,
        "state": state,
        "returns": 1,
        "reason": "ilk denetimde geri verildi",
        "area": ["scripts/lib/Ornek.ps1", "services/api/tests/unit/test_ornek.py"],
        "depends_on": ["onceki-is"],
    }


ALL_GREEN = _result(
    SHA_A,
    [
        _row("g-red", "green", "kırmızı etiketi"),
        _row("g-hung", "green", "asılı etiketi"),
        _row("g-missing", "green", "eksik etiketi"),
    ],
)
MIXED = _result(
    SHA_A,
    [
        _row("g-ok", "green", "yeşil kalan"),
        _row("g-red", "red", "kırmızı etiketi", "FAILED tests/unit/test_g_red.py::test_kirik_vaka"),
        _row("g-hung", "hung", "asılı etiketi", "son satır: bekliyor"),
        _row("g-missing", "missing", "eksik etiketi", ""),
    ],
)
MIXED_LATER_GREEN = _result(
    SHA_B,
    [
        _row("g-ok", "green", "yeşil kalan"),
        _row("g-red", "green", "kırmızı etiketi"),
        _row("g-hung", "green", "asılı etiketi"),
        _row("g-missing", "green", "eksik etiketi"),
    ],
    at="2026-10-04T01:30:00Z",
)
TWO_RED = _result(
    SHA_A,
    [
        _row("g-one", "red", "birinci etiket", "FAILED test_bir"),
        _row("g-two", "red", "ikinci etiket", "FAILED test_iki"),
    ],
)
# The integration branch's run: g-red is green there, g-hung still red, g-missing not run.
INTEGRATION = _result(
    SHA_I,
    [
        _row("g-ok", "green", "yeşil kalan"),
        _row("g-red", "green", "kırmızı etiketi"),
        _row("g-hung", "red", "asılı etiketi", "FAILED hala kirmizi"),
    ],
    at="2026-10-04T03:00:00Z",
)

# The three real cases of the proposal (team/proposals/2026-10-02-koruyucu-testler-is-dalinda.md).
B35 = _result(
    "b35c6ddb" + "0" * 32,
    [
        _row(
            "owner-error-language",
            "red",
            "Türkçe hata cümlesi eksik",
            "FAILED tests/unit/test_owner_error_language.py::"
            "test_every_error_class_has_a_turkish_line - AssertionError: no Turkish sentence for 'unexpected'",
        )
    ],
)
C2C = _result(
    "2c691585" + "0" * 32,
    [
        _row(
            "ci-covers-every-suite",
            "red",
            "yeni test dosyası kapıya bağlı değil",
            "FAILED tests/unit/test_ci_covers_every_suite.py::test_ci_runs_every_powershell_suite"
            " - team-feed.tests.ps1 is not run",
        )
    ],
)
C2C_INTEGRATION = _result(
    "2c69abcd" + "0" * 32,
    [_row("ci-covers-every-suite", "green", "yeni test dosyası kapıya bağlı değil")],
)
STRICT_RED = _result(
    "38100000" + "0" * 32,
    [
        _row(
            "installer-strictmode",
            "red",
            "katı kipte patlayan PowerShell",
            "FAIL TeamRun.ps1: The property 'Count' cannot be found on this object.",
        )
    ],
)
STRICT_GREEN = _result(
    "38100001" + "0" * 32, [_row("installer-strictmode", "green", "katı kipte patlayan PowerShell")]
)


# ------------------------------------------------------------------------------- driver

DRIVER = r"""
param([string]$CasesPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$C = [System.IO.File]::ReadAllText($CasesPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$out = [ordered]@{}
try { . $C.paths.rules }
catch {
    [Console]::Out.Write((@{ load_error = $_.Exception.Message } | ConvertTo-Json -Compress))
    exit 0
}
. $C.paths.queue
$now = [datetime]::Parse($C.now, [System.Globalization.CultureInfo]::InvariantCulture,
    [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal)

function Copy-Fixture { param($Value) return ($Value | ConvertTo-Json -Depth 30 | ConvertFrom-Json) }
function Get-Snap { param($Value) if ($null -eq $Value) { return $null }; return (ConvertTo-Json -InputObject $Value -Depth 30 -Compress) }
function Get-Guards { param($Task) return (Get-TeamProperty -InputObject $Task -Name "guards" -Default $null) }
function New-Task { param([string]$Id, [string]$State) $t = Copy-Fixture $C.task; $t.id = $Id; $t.state = $State; return $t }
function Get-Fields {
    param($Task)
    $f = [ordered]@{}
    foreach ($n in @("state", "returns", "reason", "area", "depends_on")) { $f[$n] = Get-Snap (Get-TeamProperty -InputObject $Task -Name $n) }
    return $f
}
function Set-Result { param($Task, $Result) [void](Set-TeamGuardResult -Task $Task -Result (Copy-Fixture $Result) -Now $now); return $Task }

$cases = [ordered]@{}

$cases["case1"] = {
    $t = Set-Result (New-Task "t1" "in_progress") $C.results.all_green
    return @{ guards = Get-Guards $t }
}

$cases["case2"] = {
    $t = New-Task "t2" "in_progress"
    $before = Get-Fields $t
    [void](Set-Result $t $C.results.mixed)
    return @{ guards = Get-Guards $t; before = $before; after = (Get-Fields $t) }
}

$cases["case3"] = {
    $t = Set-Result (New-Task "t3" "in_progress") $C.results.mixed
    $first = Get-Snap (Get-Guards $t)
    [void](Set-Result $t $C.results.mixed)
    $second = Get-Snap (Get-Guards $t)
    [void](Set-Result $t $C.results.mixed_later_green)
    return @{ first = $first; second = $second; later = (Get-Guards $t) }
}

$cases["case4"] = {
    $never = Get-TeamGuardInspectorNote -Task (New-Task "t4a" "inspecting")
    $green = Get-TeamGuardInspectorNote -Task (Set-Result (New-Task "t4b" "inspecting") $C.results.all_green)
    $red = Get-TeamGuardInspectorNote -Task (Set-Result (New-Task "t4c" "inspecting") $C.results.mixed)
    $report = "## Denetim`nTestler yeşil.`n`nAPPROVE"
    return @{
        never = [string]$never; green = [string]$green; red = [string]$red
        verdict_with_report = (Get-TeamVerdict -Report ($red + "`n" + $report)).Verdict
        verdict_note_alone = (Get-TeamVerdict -Report $red).Verdict
    }
}

$cases["case5"] = {
    $merged = Set-Result (New-Task "t5" "merged") $C.results.two_red
    $list = @(Get-TeamGuardWiringList -Tasks @($merged))
    $others = [ordered]@{}
    foreach ($state in @("approved", "assigned", "in_progress", "inspecting", "returned", "stopped")) {
        $t = Set-Result (New-Task "t5-$state" $state) $C.results.two_red
        $others[$state] = @(Get-TeamGuardWiringList -Tasks @($t)).Count
    }
    return @{ merged = @($list); merged_count = @($list).Count; others = $others }
}

$cases["case6"] = {
    $a = Set-Result (New-Task "t6a" "merged") $C.results.mixed
    $b = Set-Result (New-Task "t6b" "inspecting") $C.results.mixed
    $bBefore = Get-Snap (Get-Guards $b)
    $tasks = @($a, $b)
    [void](Resolve-TeamGuardRows -Tasks $tasks -Result (Copy-Fixture $C.results.integration) -Now $now)
    $first = Get-Snap (Get-Guards $a)
    [void](Resolve-TeamGuardRows -Tasks $tasks -Result (Copy-Fixture $C.results.integration) -Now $now)
    return @{ a = (Get-Guards $a); first = $first; second = (Get-Snap (Get-Guards $a)); b_before = $bBefore; b_after = (Get-Snap (Get-Guards $b)) }
}

$cases["case7"] = {
    $open = Set-Result (New-Task "t7-open" "in_progress") $C.results.mixed
    $mergedGreen = Set-Result (New-Task "t7-green" "merged") $C.results.all_green
    $noField = New-Task "t7-eski" "merged"
    $mergedRed = Set-Result (New-Task "t7-kirmizi" "merged") $C.results.b35
    $g1 = Test-TeamGuardsBlockGate -Tasks @($open)
    $g2 = Test-TeamGuardsBlockGate -Tasks @($open, $mergedGreen)
    $g3 = Test-TeamGuardsBlockGate -Tasks @($mergedGreen, $noField)
    $g4 = Test-TeamGuardsBlockGate -Tasks @($mergedGreen, $noField, $mergedRed)
    [void](Resolve-TeamGuardRows -Tasks @($mergedGreen, $noField, $mergedRed) -Result (Copy-Fixture $C.results.b35_green) -Now $now)
    $g5 = Test-TeamGuardsBlockGate -Tasks @($mergedGreen, $noField, $mergedRed)
    return @{ none_merged = $g1; merged_green = $g2; no_field = $g3; blocked = $g4; resolved = $g5 }
}

$cases["case8"] = {
    $texts = [ordered]@{
        never   = Get-TeamGuardOfficeText -Task (New-Task "t8a" "in_progress")
        green   = Get-TeamGuardOfficeText -Task (Set-Result (New-Task "t8b" "in_progress") $C.results.all_green)
        red     = Get-TeamGuardOfficeText -Task (Set-Result (New-Task "t8c" "in_progress") $C.results.only_red)
        hung    = Get-TeamGuardOfficeText -Task (Set-Result (New-Task "t8d" "in_progress") $C.results.only_hung)
        missing = Get-TeamGuardOfficeText -Task (Set-Result (New-Task "t8e" "in_progress") $C.results.only_missing)
    }
    $two = Get-TeamGuardOfficeText -Task (Set-Result (New-Task "t8f" "in_progress") $C.results.two_red)
    $nolabel = Get-TeamGuardOfficeText -Task (Set-Result (New-Task "t8g" "in_progress") $C.results.no_label)
    return @{ texts = $texts; two = [string]$two; nolabel = [string]$nolabel }
}

$cases["case9"] = {
    $b35 = Set-Result (New-Task "stt-olcum" "inspecting") $C.results.b35
    $feed = Set-Result (New-Task "team-feed" "approved") $C.results.c2c
    $feed.state = "merged"
    $wiring = @(Get-TeamGuardWiringList -Tasks @($b35, $feed))
    $blocked = Test-TeamGuardsBlockGate -Tasks @($b35, $feed)
    [void](Resolve-TeamGuardRows -Tasks @($b35, $feed) -Result (Copy-Fixture $C.results.c2c_integration) -Now $now)
    $after = Test-TeamGuardsBlockGate -Tasks @($b35, $feed)
    $strict = Set-Result (New-Task "team-run" "in_progress") $C.results.strict_red
    $strictFirst = Get-Snap (Get-Guards $strict)
    [void](Set-Result $strict $C.results.strict_green)
    return @{
        b35_note = [string](Get-TeamGuardInspectorNote -Task $b35)
        wiring = @($wiring); blocked = $blocked; feed = (Get-Guards $feed); after = $after
        strict_first = $strictFirst; strict = (Get-Guards $strict)
    }
}

$cases["case10"] = {
    $shell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    $resultFile = $C.paths.runner_result
    $log = & $shell -NoProfile -ExecutionPolicy Bypass -File $C.paths.guards_script -Worktree $C.paths.scratch -OutFile $resultFile -Python $C.paths.python 2>&1
    $code = $LASTEXITCODE
    $runnerResult = Read-TeamJson -Path $resultFile
    $t = Set-Result (New-Task "t10" "in_progress") $runnerResult
    $real = Read-TeamJson -Path $C.paths.guard_list
    $labels = [ordered]@{}
    foreach ($g in @($real.guards)) {
        $one = [pscustomobject]@{ at = "2026-10-04T01:00:00Z"; sha = ("c" * 40); seconds = 1; status = "red"
            rows = @([pscustomobject]@{ id = $g.id; path = $g.path; outcome = "red"; seconds = 1; label = $g.label; detail = "FAILED x" }) }
        $labels[[string]$g.id] = @{ label = [string]$g.label; text = [string](Get-TeamGuardOfficeText -Task (Set-Result (New-Task "t10-$($g.id)" "in_progress") $one)) }
    }
    return @{ code = $code; log = (@($log) -join "`n"); runner_result = $runnerResult; guards = (Get-Guards $t); labels = $labels }
}

foreach ($name in @($cases.Keys)) {
    try { $out[$name] = @{ ok = $true; value = (& $cases[$name]) } }
    catch { $out[$name] = @{ ok = $false; error = ("{0} @ {1}" -f $_.Exception.Message, $_.InvocationInfo.PositionMessage) } }
}
[Console]::Out.Write((ConvertTo-Json -InputObject $out -Depth 40 -Compress))
"""


def _git(repo: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603, S607 - git, on a repository this test made
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.name=guard-rules-test",
            "-c",
            "user.email=guard-rules-test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(repo),
            *arguments,
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )


def _scratch(root: Path) -> Path:
    """A git repository of its own with one failing fake PowerShell guard."""
    repo = root / "scratch"
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "team").mkdir()
    (repo / "scripts" / "tests" / "kirik.tests.ps1").write_bytes(
        b'Write-Host "  PASS  bir vaka"\nWrite-Host "  FAIL  kirik-koruyucu-vakasi"\nexit 1\n'
    )
    guard_list = {
        "version": 1,
        "guards": [
            {
                "id": "kirik-betik",
                "kind": "powershell",
                "path": "scripts/tests/kirik.tests.ps1",
                "label": "sahte koruyucu kırık: ğüşiöç",
            }
        ],
    }
    (repo / "team" / "guards.json").write_bytes(
        json.dumps(guard_list, ensure_ascii=False).encode("utf-8")
    )
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "scratch")
    return repo


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("guard-rules")
    scratch = _scratch(root)
    cases = {
        "now": NOW,
        "task": _task("sablon", "in_progress"),
        "paths": {
            "rules": str(RULES_LIB),
            "queue": str(QUEUE_LIB),
            "guards_script": str(GUARDS_SCRIPT),
            "guard_list": str(GUARD_LIST),
            "scratch": str(scratch),
            "runner_result": str(root / "runner-result.json"),
            "python": sys.executable,
        },
        "results": {
            "all_green": ALL_GREEN,
            "mixed": MIXED,
            "mixed_later_green": MIXED_LATER_GREEN,
            "two_red": TWO_RED,
            "integration": INTEGRATION,
            "b35": B35,
            "b35_green": _result(
                SHA_I, [_row("owner-error-language", "green", "Türkçe hata cümlesi eksik")]
            ),
            "c2c": C2C,
            "c2c_integration": C2C_INTEGRATION,
            "strict_red": STRICT_RED,
            "strict_green": STRICT_GREEN,
            "only_red": _result(SHA_A, [_row("g-red", "red", "kırmızı etiketi", "FAILED x")]),
            "only_hung": _result(SHA_A, [_row("g-red", "hung", "kırmızı etiketi", "son satır")]),
            "only_missing": _result(SHA_A, [_row("g-red", "missing", "kırmızı etiketi")]),
            "no_label": _result(SHA_A, [_row("etiketsiz-koruyucu", "red", "", "FAILED y")]),
        },
    }
    cases_path = root / "cases.json"
    cases_path.write_bytes(json.dumps(cases, ensure_ascii=False).encode("utf-8"))
    driver = root / "driver.ps1"
    # A byte-order mark: Windows PowerShell 5.1 reads a script without one in the ANSI code page.
    driver.write_bytes(b"\xef\xbb\xbf" + DRIVER.encode("utf-8"))
    shell = _powershell()
    assert shell is not None
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        ran = subprocess.run(  # noqa: S603 - a fixed interpreter and a driver this test wrote
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(driver),
                "-CasesPath",
                str(cases_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            timeout=600,  # a hang guard, never the assertion
            check=False,
        )
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode("utf-8", errors="replace")
        stderr = err.read().decode("utf-8", errors="replace")
    assert ran.returncode == 0, stdout + stderr
    try:
        document = json.loads(stdout.lstrip("\ufeff"))
    except json.JSONDecodeError:  # pragma: no cover - the message is the evidence
        pytest.fail(f"the driver printed no JSON: {stdout[:2000]} {stderr[:2000]}")
    return document


def _case(outcomes: dict, name: str):
    assert "load_error" not in outcomes, outcomes.get("load_error")
    case = outcomes[name]
    assert case["ok"], case.get("error")
    return case["value"]


def _list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# --------------------------------------------------------------------------------- cases


def test_1_a_green_result_on_a_fresh_task(outcomes):
    guards = _case(outcomes, "case1")["guards"]
    assert guards["status"] == "green"
    assert _list(guards["open"]) == []
    assert _list(guards["resolved"]) == []
    assert guards["runs"] == 1
    assert guards["sha"] == SHA_A
    assert guards["at"] == ALL_GREEN["at"]


def test_2_red_hung_and_missing_rows_open_in_order_and_nothing_else_changes(outcomes):
    value = _case(outcomes, "case2")
    guards = value["guards"]
    assert guards["status"] == "red"
    opened = _list(guards["open"])
    assert [row["id"] for row in opened] == ["g-red", "g-hung", "g-missing"]
    expected = [row for row in MIXED["rows"] if row["outcome"] != "green"]
    for got, want in zip(opened, expected, strict=True):
        assert got == {
            "id": want["id"],
            "label": want["label"],
            "outcome": want["outcome"],
            "detail": want["detail"],
            "sha": SHA_A,
        }
    for field in ("state", "returns", "reason", "area", "depends_on"):
        assert value["after"][field] == value["before"][field], field


def test_3_the_same_result_twice_changes_nothing_and_a_later_green_resolves_by_branch(outcomes):
    value = _case(outcomes, "case3")
    assert value["second"] == value["first"]
    assert json.loads(value["first"])["runs"] == 1
    later = value["later"]
    assert _list(later["open"]) == []
    assert later["status"] == "green"
    assert later["runs"] == 2
    assert later["sha"] == SHA_B
    resolved = _list(later["resolved"])
    assert [row["id"] for row in resolved] == ["g-red", "g-hung", "g-missing"]
    assert all(
        row["by"] == "branch" and row["sha"] == SHA_B and row["at"] == NOW for row in resolved
    )


def test_4_the_inspector_note(outcomes):
    value = _case(outcomes, "case4")
    assert value["never"] == ""
    assert "koruyucular: yeşil" in value["green"]
    assert SHA_A[:8] in value["green"]
    red = value["red"]
    for row in MIXED["rows"]:
        if row["outcome"] == "green":
            assert row["label"] not in red
            continue
        assert row["id"] in red and row["label"] in red and row["outcome"] in red
        if row["detail"]:
            assert row["detail"] in red
    # the two-way rule, in one sentence
    assert "alanının içindeyse" in red and "alanının dışındaysa" in red
    assert "bağlama listesi" in red and "hüküm denetleyicinindir" in red
    for line in red.splitlines():
        assert line.strip().strip("`* ") not in VERDICT_WORDS
    assert value["verdict_with_report"] == "APPROVE"
    assert value["verdict_note_alone"] == "NONE"


def test_5_the_wiring_list_takes_only_merged_tasks(outcomes):
    value = _case(outcomes, "case5")
    assert value["merged_count"] == 2
    records = _list(value["merged"])
    assert records == [
        {"task": "t5", "id": "g-one", "label": "birinci etiket", "detail": "FAILED test_bir"},
        {"task": "t5", "id": "g-two", "label": "ikinci etiket", "detail": "FAILED test_iki"},
    ]
    assert value["others"] == {
        "approved": 0,
        "assigned": 0,
        "in_progress": 0,
        "inspecting": 0,
        "returned": 0,
        "stopped": 0,
    }


def test_6_an_integration_run_resolves_only_its_green_guards_on_merged_tasks(outcomes):
    value = _case(outcomes, "case6")
    guards = value["a"]
    assert [row["id"] for row in _list(guards["open"])] == ["g-hung", "g-missing"]
    resolved = _list(guards["resolved"])
    assert resolved == [{"id": "g-red", "by": "integration", "sha": SHA_I, "at": NOW}]
    assert value["second"] == value["first"]
    assert value["b_after"] == value["b_before"]


def test_7_the_gate_is_blocked_only_by_an_open_row_on_a_merged_task(outcomes):
    value = _case(outcomes, "case7")
    for name in ("none_merged", "merged_green", "no_field"):
        assert value[name]["Blocked"] is False, name
    blocked = value["blocked"]
    assert blocked["Blocked"] is True
    assert "t7-kirmizi" in blocked["Why"] and "Türkçe hata cümlesi eksik" in blocked["Why"]
    assert "t7-green" not in blocked["Why"] and "t7-eski" not in blocked["Why"]
    assert value["resolved"]["Blocked"] is False


def test_8_the_ofis_text(outcomes):
    value = _case(outcomes, "case8")
    texts = value["texts"]
    assert texts["never"] == ""
    assert texts["green"] == "koruyucular: yeşil"
    assert texts["red"] == "koruyucu kırmızı: kırmızı etiketi"
    assert len(set(texts.values())) == 5
    assert all("kırmızı etiketi" in texts[name] for name in ("red", "hung", "missing"))
    two = value["two"]
    assert two == "koruyucu kırmızı: birinci etiket; koruyucu kırmızı: ikinci etiket"
    assert "etiketsiz-koruyucu" in value["nolabel"]
    for text in [*texts.values(), two, value["nolabel"]]:
        assert "null" not in text.lower()
        assert not text.rstrip().endswith(":")


def test_9_the_three_real_cases_of_the_proposal(outcomes):
    value = _case(outcomes, "case9")
    # b35c6ddb: the 'unexpected' class without a Turkish sentence
    assert "Türkçe hata cümlesi eksik" in value["b35_note"]
    assert "unexpected" in value["b35_note"]
    # 2c691585: team-feed.tests.ps1 not wired, the task approved and merged
    wiring = _list(value["wiring"])
    assert [(row["task"], row["id"]) for row in wiring] == [("team-feed", "ci-covers-every-suite")]
    assert "team-feed.tests.ps1" in wiring[0]["detail"]
    assert value["blocked"]["Blocked"] is True and "team-feed" in value["blocked"]["Why"]
    assert _list(value["feed"]["open"]) == []
    assert _list(value["feed"]["resolved"])[0]["by"] == "integration"
    assert value["after"]["Blocked"] is False
    # QUALIFICATION 38.10: the bare .Count of TeamRun.ps1, then the worker's second run
    assert [row["id"] for row in _list(json.loads(value["strict_first"])["open"])] == [
        "installer-strictmode"
    ]
    strict = value["strict"]
    assert _list(strict["open"]) == []
    assert [(row["id"], row["by"]) for row in _list(strict["resolved"])] == [
        ("installer-strictmode", "branch")
    ]
    assert strict["runs"] == 2


def test_10_the_real_runner_and_the_rules_read_each_other(outcomes):
    value = _case(outcomes, "case10")
    assert value["code"] == 1, value["log"]
    runner = value["runner_result"]
    assert runner["status"] == "red"
    opened = _list(value["guards"]["open"])
    assert len(opened) == 1
    assert opened[0]["id"] == "kirik-betik"
    assert opened[0]["label"] == "sahte koruyucu kırık: ğüşiöç"
    assert "kirik-koruyucu-vakasi" in opened[0]["detail"]
    assert opened[0]["sha"] == runner["sha"]
    real = json.loads(GUARD_LIST.read_bytes().decode("utf-8"))
    labels = value["labels"]
    assert list(labels) == [guard["id"] for guard in real["guards"]]
    for guard in real["guards"]:
        assert labels[guard["id"]]["label"] == guard["label"]
        assert labels[guard["id"]]["text"] == f"koruyucu kırmızı: {guard['label']}"
