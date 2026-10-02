"""The guard list and its runner: ``team/guards.json`` run on a branch's own tree, no agent.

Three of the seven integration gates of 1-2 October were red on their first run, each on a
test that reads the WHOLE application (a Turkish sentence for every error class, a gate line
for every suite, StrictMode over every library) and that neither the worker nor the inspector
had run. ``scripts/team/guards.ps1`` runs that family in seconds on the tree it is pointed at.

Every runner case below builds its OWN git repository under ``tmp_path`` with tiny fake
guards and hands the runner this interpreter: no case runs a real guard and no case writes
into the shared tree. The real list is read, never run.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[4]
GUARDS_SCRIPT = REPO / "scripts" / "team" / "guards.ps1"
GUARDS_LIB = REPO / "scripts" / "lib" / "TeamGuards.ps1"
GUARD_LIST = REPO / "team" / "guards.json"
QUALITY_GATE = REPO / "scripts" / "quality-gate.ps1"
LEAD_ROLE = REPO / ".claude" / "agents" / "lead.md"

#: The contract of a guard run's RESULT, key -> type, shared with the card that reads it.
RESULT_KEYS = {"at": str, "sha": str, "seconds": (int, float), "status": str, "rows": list}
ROW_KEYS = {
    "id": str,
    "path": str,
    "outcome": str,
    "seconds": (int, float),
    "label": str,
    "detail": str,
}
DETAIL_MAX = 400

#: The frontmatter of the lead's role file as the base of this change had it. The role's
#: name, description and tools are not this file's to change; a card that changes them on
#: purpose changes this block in the same commit.
LEAD_FRONTMATTER = (
    "---\n"
    "name: lead\n"
    "description: Proje Hakimi — owns the roadmap and the definition of done, splits and "
    "assigns work, sends back what is wrong, merges, reports to the owner. Use to run a team "
    "cycle.\n"
    "tools: Read, Grep, Glob, Bash, Edit, Write, Agent\n"
    "---\n"
)

PS_GREEN = 'Write-Host "  PASS  her sey yolunda"\nexit 0\n'
PS_RED = 'Write-Host "  PASS  bir vaka"\nWrite-Host "  FAIL  kirik-betik-vakasi"\nexit 1\n'
PY_GREEN = "def test_gecer():\n    assert True\n"
PY_RED = "def test_kirik_pytest_vakasi():\n    assert 1 == 2\n"


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


def _shell(*arguments: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    assert shell is not None
    return subprocess.run(  # noqa: S603 - a fixed interpreter and this repository's own script
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,  # a hang guard for the test itself, never the assertion
        check=False,
    )


def _guards(worktree: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _shell(
        "-File",
        str(GUARDS_SCRIPT),
        "-Worktree",
        str(worktree),
        "-Python",
        sys.executable,
        *arguments,
    )


def _git(repo: Path, *arguments: str) -> str:
    ran = subprocess.run(  # noqa: S603, S607 - git, on a repository this test made
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.name=guards-test",
            "-c",
            "user.email=guards-test@example.invalid",
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
    return ran.stdout.decode("utf-8", errors="replace")


def _guard(guard_id: str, kind: str, path: str, label: str | None = None) -> dict:
    return {"id": guard_id, "kind": kind, "path": path, "label": label or f"etiket {guard_id}"}


def _write_list(root: Path, guards: list[dict]) -> Path:
    target = root / "team" / "guards.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {"version": 1, "guards": guards}
    target.write_bytes(json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8"))
    return target


def _scratch(tmp_path: Path, guards: list[dict], files: dict[str, str]) -> Path:
    """A git repository of its own: the fake guards, the list, one commit."""
    repo = tmp_path / "scratch"
    (repo / "services" / "api" / "tests" / "unit").mkdir(parents=True)
    (repo / "services" / "api" / "tests" / "unit" / ".keep").write_bytes(b"")
    for relative, text in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    _write_list(repo, guards)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "scratch")
    return repo


def _result(path: Path) -> dict:
    return json.loads(path.read_bytes().decode("utf-8"))


def _lines(ran: subprocess.CompletedProcess[str]) -> list[str]:
    return [line.strip() for line in ran.stdout.splitlines() if line.strip()]


def _process_is_gone(pid: int, deadline_seconds: float = 30.0) -> bool:
    """True once the process has ended. The deadline is this test's own hang guard."""
    kernel = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    synchronize = 0x00100000
    end = time.monotonic() + deadline_seconds
    while True:
        handle = kernel.OpenProcess(synchronize, False, pid)
        if not handle:
            return True
        try:
            if kernel.WaitForSingleObject(handle, 200) == 0:
                return True
        finally:
            kernel.CloseHandle(handle)
        if time.monotonic() > end:
            return False


def _mixed(tmp_path: Path) -> Path:
    """Two green and two red guards, one of each kind, in a fixed order."""
    return _scratch(
        tmp_path,
        [
            _guard("yesil-betik", "powershell", "scripts/tests/yesil.tests.ps1"),
            _guard("kirmizi-betik", "powershell", "scripts/tests/kirmizi.tests.ps1"),
            _guard("yesil-pytest", "pytest", "services/api/tests/unit/test_yesil.py"),
            _guard("kirmizi-pytest", "pytest", "services/api/tests/unit/test_kirmizi.py"),
        ],
        {
            "scripts/tests/yesil.tests.ps1": PS_GREEN,
            "scripts/tests/kirmizi.tests.ps1": PS_RED,
            "services/api/tests/unit/test_yesil.py": PY_GREEN,
            "services/api/tests/unit/test_kirmizi.py": PY_RED,
        },
    )


def _sleeper(pid_file: Path, child_pid_file: Path) -> str:
    """A guard that never ends by itself - and has a child, which must not outlive it."""
    return (
        f"[System.IO.File]::WriteAllText('{pid_file}', \"$PID\")\n"
        "$exe = Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'\n"
        "$child = Start-Process -FilePath $exe -NoNewWindow -PassThru "
        "-ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 600')\n"
        f"[System.IO.File]::WriteAllText('{child_pid_file}', \"$($child.Id)\")\n"
        "Start-Sleep -Seconds 600\n"
        "exit 0\n"
    )


# ------------------------------------------------------------------------- (1) the real list


def test_the_real_guard_list_names_files_the_gate_runs(tmp_path):
    out = tmp_path / "parsed.json"
    ran = _shell(
        "-Command",
        f". '{GUARDS_LIB}'; $list = Read-TeamGuardList -Path '{GUARD_LIST}'; "
        f"[System.IO.File]::WriteAllText('{out}', ($list | ConvertTo-Json -Depth 6), "
        "(New-Object System.Text.UTF8Encoding($false)))",
    )
    assert ran.returncode == 0, ran.stdout + ran.stderr
    parsed = _result(out)
    assert parsed["refused"] is False, parsed["reason"]
    written = json.loads(GUARD_LIST.read_bytes().decode("utf-8"))
    assert written["version"] == 1
    assert parsed["guards"] == written["guards"]  # the runner reads what the file says

    guards = written["guards"]
    assert guards, "an empty list is a run that says green about nothing"
    ids = [g["id"] for g in guards]
    assert len(set(ids)) == len(ids)
    gate = QUALITY_GATE.read_text(encoding="utf-8-sig")
    for guard in guards:
        assert set(guard) == {"id", "kind", "path", "label"}, guard
        assert re.fullmatch(r"[a-z0-9-]+", guard["id"]), guard
        assert guard["label"].strip(), guard
        path = REPO / guard["path"]
        assert path.is_file(), guard["path"]
        if guard["kind"] == "pytest":
            assert guard["path"].startswith("services/api/tests/unit/"), guard
            assert path.suffix == ".py"
        else:
            assert guard["kind"] == "powershell", guard
            assert guard["path"].startswith("scripts/tests/"), guard
            assert path.name.endswith(".tests.ps1")
            # A guard the gate does not run would be red here and never at the gate.
            assert path.name in gate, f"{path.name} is not run by scripts/quality-gate.ps1"


# --------------------------------------------------------------------------- (2) a bad list

_GOOD = {"id": "iyi", "kind": "powershell", "path": "scripts/tests/yesil.tests.ps1", "label": "x"}
BAD_LISTS = {
    "unknown kind": [{**_GOOD, "kind": "bash"}],
    "duplicate id": [_GOOD, dict(_GOOD)],
    "drive letter": [{**_GOOD, "path": "C:/x"}],
    "absolute path": [{**_GOOD, "path": "/x"}],
    "parent directory": [{**_GOOD, "path": "../x"}],
    "empty label": [{**_GOOD, "label": "  "}],
    "backslash": [{**_GOOD, "path": "scripts\\tests\\yesil.tests.ps1"}],
    "bad id": [{**_GOOD, "id": "Buyuk_Harf"}],
    "no guards": [],
}


def test_a_bad_list_is_refused_and_each_refusal_says_its_own_reason(tmp_path):
    repo = _scratch(tmp_path, [_GOOD], {"scripts/tests/yesil.tests.ps1": PS_GREEN})
    assert _guards(repo).returncode == 0  # the list these cases break is a good one
    reasons = {}
    for name, guards in BAD_LISTS.items():
        bad = _write_list(tmp_path / name.replace(" ", "-"), guards)
        out = tmp_path / f"{bad.parent.parent.name}.json"
        ran = _guards(repo, "-List", str(bad), "-OutFile", str(out))
        assert ran.returncode == 2, f"{name}: {ran.stdout}{ran.stderr}"
        assert not out.exists(), name  # a refused list has no result
        lines = _lines(ran)
        assert len(lines) == 1 and lines[0].startswith("koruyucular koşamadı: "), ran.stdout
        reasons[name] = lines[0].replace(str(bad), "")
    assert len(set(reasons.values())) == len(BAD_LISTS), reasons
    wrong_version = tmp_path / "guards-v2.json"
    wrong_version.write_text(json.dumps({"version": 2, "guards": [_GOOD]}), encoding="utf-8")
    assert _guards(repo, "-List", str(wrong_version)).returncode == 2
    not_json = tmp_path / "guards-broken.json"
    not_json.write_text("{ this is not json", encoding="utf-8")
    assert _guards(repo, "-List", str(not_json)).returncode == 2


# ---------------------------------------------------------------------------- (3) all green


def test_all_green_exits_zero_with_the_contract_result(tmp_path):
    guards = [
        _guard("ilk-pytest", "pytest", "services/api/tests/unit/test_yesil.py"),
        _guard("sonra-betik", "powershell", "scripts/tests/yesil.tests.ps1"),
        _guard("son-pytest", "pytest", "services/api/tests/unit/test_yesil_iki.py"),
    ]
    repo = _scratch(
        tmp_path,
        guards,
        {
            "services/api/tests/unit/test_yesil.py": PY_GREEN,
            "scripts/tests/yesil.tests.ps1": PS_GREEN,
            "services/api/tests/unit/test_yesil_iki.py": PY_GREEN,
        },
    )
    out = tmp_path / "result.json"
    ran = _guards(repo, "-OutFile", str(out))

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert _lines(ran)[0] == "koruyucular: yeşil"
    result = _result(out)
    assert set(result) == set(RESULT_KEYS)
    for key, kind in RESULT_KEYS.items():
        assert isinstance(result[key], kind) and not isinstance(result[key], bool), key
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", result["at"]), result["at"]
    assert result["sha"] == _git(repo, "rev-parse", "HEAD").strip()
    assert re.fullmatch(r"[0-9a-f]{40}", result["sha"])
    assert result["status"] == "green"
    assert [row["id"] for row in result["rows"]] == [g["id"] for g in guards]  # the list's order
    for row, guard in zip(result["rows"], guards, strict=True):
        assert set(row) == set(ROW_KEYS)
        for key, kind in ROW_KEYS.items():
            assert isinstance(row[key], kind) and not isinstance(row[key], bool), key
        assert (row["path"], row["label"]) == (guard["path"], guard["label"])
        assert row["outcome"] == "green" and row["detail"] == ""


def test_a_list_of_one_guard_still_has_a_list_of_rows(tmp_path):
    repo = _scratch(
        tmp_path,
        [_guard("tek", "powershell", "scripts/tests/yesil.tests.ps1")],
        {"scripts/tests/yesil.tests.ps1": PS_GREEN},
    )
    out = tmp_path / "result.json"
    assert _guards(repo, "-OutFile", str(out)).returncode == 0
    rows = _result(out)["rows"]
    assert isinstance(rows, list) and [row["id"] for row in rows] == ["tek"]


# ------------------------------------------------------------------------------ (4) one red


def test_a_red_guard_of_each_kind_exits_one_and_names_what_failed(tmp_path):
    repo = _mixed(tmp_path)
    out = tmp_path / "result.json"
    ran = _guards(repo, "-OutFile", str(out))

    assert ran.returncode == 1, ran.stdout + ran.stderr
    result = _result(out)
    assert result["status"] == "red"
    rows = {row["id"]: row for row in result["rows"]}
    assert [rows[i]["outcome"] for i in ("yesil-betik", "yesil-pytest")] == ["green", "green"]
    assert [rows[i]["outcome"] for i in ("kirmizi-betik", "kirmizi-pytest")] == ["red", "red"]
    assert "kirik-betik-vakasi" in rows["kirmizi-betik"]["detail"]
    assert "PASS" not in rows["kirmizi-betik"]["detail"]  # the failing lines, not the run
    assert "test_kirik_pytest_vakasi" in rows["kirmizi-pytest"]["detail"]
    for row in result["rows"]:
        assert len(row["detail"]) <= DETAIL_MAX
    assert _lines(ran) == [
        "koruyucu kırmızı: etiket kirmizi-betik",
        "koruyucu kırmızı: etiket kirmizi-pytest",
    ]


def test_a_long_failure_is_cut_to_the_contracts_length(tmp_path):
    noisy = "".join(f'Write-Host "  FAIL  vaka-{i:03d}-{"x" * 40}"\n' for i in range(40))
    repo = _scratch(
        tmp_path,
        [_guard("gurultulu", "powershell", "scripts/tests/gurultulu.tests.ps1")],
        {"scripts/tests/gurultulu.tests.ps1": noisy + "exit 3\n"},
    )
    out = tmp_path / "result.json"
    assert _guards(repo, "-OutFile", str(out)).returncode == 1
    detail = _result(out)["rows"][0]["detail"]
    assert len(detail) == DETAIL_MAX and detail.startswith("FAIL  vaka-000")


# ------------------------------------------------------------------------- (5) a missing file


def test_a_guard_the_tree_does_not_have_is_missing_and_the_rest_still_run(tmp_path):
    repo = _scratch(
        tmp_path,
        [
            _guard("yok", "pytest", "services/api/tests/unit/test_yok.py"),
            _guard("var", "powershell", "scripts/tests/yesil.tests.ps1"),
        ],
        {"scripts/tests/yesil.tests.ps1": PS_GREEN},
    )
    out = tmp_path / "result.json"
    ran = _guards(repo, "-OutFile", str(out))

    assert ran.returncode == 1, ran.stdout + ran.stderr
    result = _result(out)
    assert [(row["id"], row["outcome"]) for row in result["rows"]] == [
        ("yok", "missing"),
        ("var", "green"),
    ]
    assert result["status"] == "red"
    assert "etiket yok" in ran.stdout


# ------------------------------------------------------------------------------- (6) a hang


def _run_with_a_sleeper(tmp_path: Path, hang_seconds: int) -> tuple[dict, Path, Path, str]:
    pid_file = tmp_path / f"sleeper-{hang_seconds}.pid"
    child_pid_file = tmp_path / f"sleeper-child-{hang_seconds}.pid"
    repo = _scratch(
        tmp_path / f"hang-{hang_seconds}",
        [
            _guard("uyuyan", "powershell", "scripts/tests/uyuyan.tests.ps1"),
            _guard("sonraki", "powershell", "scripts/tests/yesil.tests.ps1"),
        ],
        {
            "scripts/tests/uyuyan.tests.ps1": _sleeper(pid_file, child_pid_file),
            "scripts/tests/yesil.tests.ps1": PS_GREEN,
        },
    )
    out = tmp_path / f"result-{hang_seconds}.json"
    ran = _guards(repo, "-HangSeconds", str(hang_seconds), "-OutFile", str(out))
    assert ran.returncode == 1, ran.stdout + ran.stderr
    return _result(out), pid_file, child_pid_file, ran.stdout


def test_a_hung_guard_is_stopped_with_its_children_and_the_next_one_runs(tmp_path):
    result, pid_file, child_pid_file, _ = _run_with_a_sleeper(tmp_path, 2)
    if not (pid_file.exists() and child_pid_file.exists()):
        # On a loaded machine two seconds can end before the sleeper has started its child.
        # The claim is about a guard that IS running, so it is given the time to be one.
        result, pid_file, child_pid_file, _ = _run_with_a_sleeper(tmp_path, 20)

    assert [(row["id"], row["outcome"]) for row in result["rows"]] == [
        ("uyuyan", "hung"),
        ("sonraki", "green"),
    ]
    assert result["status"] == "red"
    assert _process_is_gone(int(pid_file.read_text(encoding="utf-8")))
    assert _process_is_gone(int(child_pid_file.read_text(encoding="utf-8")))  # the whole tree


# ------------------------------------------------ (7) three wordings, three exit codes


def test_red_hung_and_missing_are_three_wordings_and_the_exit_codes_are_three(tmp_path):
    label = "aynı etiket"
    pid_file = tmp_path / "sleeper.pid"
    repo = _scratch(
        tmp_path,
        [
            _guard("kirmizi", "powershell", "scripts/tests/kirmizi.tests.ps1", label),
            _guard("uyuyan", "powershell", "scripts/tests/uyuyan.tests.ps1", label),
            _guard("yok", "powershell", "scripts/tests/yok.tests.ps1", label),
        ],
        {
            "scripts/tests/kirmizi.tests.ps1": PS_RED,
            "scripts/tests/uyuyan.tests.ps1": _sleeper(pid_file, tmp_path / "child.pid"),
        },
    )
    out = tmp_path / "result.json"
    red = _guards(repo, "-HangSeconds", "2", "-OutFile", str(out))
    assert [row["outcome"] for row in _result(out)["rows"]] == ["red", "hung", "missing"]
    lines = _lines(red)
    assert len(lines) == 3 and all(line.endswith(label) for line in lines), red.stdout
    # One label, three outcomes: what differs between the lines is the wording alone.
    assert len(set(lines)) == 3, lines
    assert lines[0] == f"koruyucu kırmızı: {label}"

    green_repo = _scratch(
        tmp_path / "green",
        [_guard("yesil", "powershell", "scripts/tests/yesil.tests.ps1")],
        {"scripts/tests/yesil.tests.ps1": PS_GREEN},
    )
    green = _guards(green_repo)
    could_not_run = _guards(green_repo, "-List", str(tmp_path / "no-such-list.json"))
    assert [green.returncode, red.returncode, could_not_run.returncode] == [0, 1, 2]


# ------------------------------------------------------------------ (8) the worktree's copy


def test_the_guard_that_runs_is_the_worktrees_copy(tmp_path):
    guards = [_guard("ayni-ad", "powershell", "scripts/tests/ayni-ad.tests.ps1")]
    repo = _scratch(tmp_path, guards, {"scripts/tests/ayni-ad.tests.ps1": PS_RED})
    # The same list beside a same-named guard that PASSES, in a directory that is not the
    # worktree: whatever the runner resolves a guard against, it must be the worktree.
    elsewhere = tmp_path / "elsewhere"
    beside = _write_list(elsewhere, guards)
    passing = elsewhere / "scripts" / "tests" / "ayni-ad.tests.ps1"
    passing.parent.mkdir(parents=True)
    passing.write_bytes(PS_GREEN.encode("utf-8"))

    out = tmp_path / "result.json"
    ran = _guards(repo, "-List", str(beside), "-OutFile", str(out))

    assert ran.returncode == 1, ran.stdout + ran.stderr
    row = _result(out)["rows"][0]
    assert row["outcome"] == "red" and "kirik-betik-vakasi" in row["detail"]


def test_a_pytest_guard_imports_the_worktrees_code(tmp_path):
    """The interpreter is another checkout's; the code under test must still be this tree's."""
    repo = _scratch(
        tmp_path,
        [_guard("agac", "pytest", "services/api/tests/unit/test_agac.py")],
        {
            "services/api/guard_probe_module.py": "WHERE = __file__\n",
            "services/api/tests/unit/test_agac.py": (
                "import pathlib\n\nimport guard_probe_module\n\n\n"
                "def test_agac():\n"
                "    here = pathlib.Path(__file__).resolve().parents[2]\n"
                "    assert pathlib.Path(guard_probe_module.WHERE).resolve().parent == here\n"
            ),
        },
    )
    out = tmp_path / "result.json"
    ran = _guards(repo, "-OutFile", str(out))
    assert ran.returncode == 0, _result(out)["rows"][0]["detail"] if out.exists() else ran.stdout


# ------------------------------------------------------- (9) the worktree is left as found


def test_a_run_leaves_the_worktree_as_it_found_it(tmp_path):
    repo = _mixed(tmp_path)
    (repo / "yarim-kalan-is.txt").write_bytes(b"a worker's uncommitted file\n")
    before = _git(repo, "status", "--porcelain", "--ignored")
    out = tmp_path / "result.json"

    assert _guards(repo, "-OutFile", str(out)).returncode == 1
    outcomes = {row["id"]: row["outcome"] for row in _result(out)["rows"]}
    assert (outcomes["yesil-pytest"], outcomes["kirmizi-pytest"]) == ("green", "red")

    assert _git(repo, "status", "--porcelain", "--ignored") == before
    assert before == "?? yarim-kalan-is.txt\n"
    assert not list(repo.rglob(".pytest_cache")) and not list(repo.rglob("__pycache__"))


# ------------------------------------------------------------------ (10) it could not run


def test_what_cannot_run_at_all_exits_two_and_says_which(tmp_path):
    repo = _scratch(
        tmp_path,
        [_guard("yesil", "pytest", "services/api/tests/unit/test_yesil.py")],
        {"services/api/tests/unit/test_yesil.py": PY_GREEN},
    )
    not_a_tree = tmp_path / "not-a-work-tree"
    not_a_tree.mkdir()
    _write_list(not_a_tree, [_guard("yesil", "pytest", "services/api/tests/unit/test_yesil.py")])
    no_list = tmp_path / "no-such-list.json"
    no_python = tmp_path / "no-such-python.exe"

    cases = {
        "not a work tree": _guards(not_a_tree),
        "no list": _guards(repo, "-List", str(no_list)),
        "no interpreter": _shell(
            "-File", str(GUARDS_SCRIPT), "-Worktree", str(repo), "-Python", str(no_python)
        ),
        "no such directory": _guards(tmp_path / "nowhere"),
    }
    said = {}
    for name, ran in cases.items():
        assert ran.returncode == 2, f"{name}: {ran.stdout}{ran.stderr}"
        lines = _lines(ran)
        assert len(lines) == 1 and lines[0].startswith("koruyucular koşamadı: "), ran.stdout
        said[name] = lines[0]
    assert "git çalışma ağacı değil" in said["not a work tree"]
    assert "liste yok" in said["no list"] and str(no_list) in said["no list"]
    assert "Python yorumlayıcısı yok" in said["no interpreter"]
    assert str(no_python) in said["no interpreter"]
    assert "dizin yok" in said["no such directory"]


# ----------------------------------------------------------------------------- (11) -OutFile


def test_the_out_file_is_the_result_in_utf8_without_a_byte_order_mark(tmp_path):
    label = "Türkçe hata cümlesi eksik: ğüşiöçı İĞÜŞÖÇ"
    repo = _scratch(
        tmp_path,
        [
            _guard("turkce", "powershell", "scripts/tests/kirmizi.tests.ps1", label),
            _guard("yesil", "powershell", "scripts/tests/yesil.tests.ps1"),
        ],
        {"scripts/tests/kirmizi.tests.ps1": PS_RED, "scripts/tests/yesil.tests.ps1": PS_GREEN},
    )
    out = tmp_path / "result.json"
    ran = _guards(repo, "-OutFile", str(out))

    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    result = json.loads(raw.decode("utf-8"))  # strict: a wrong encoding is an error here
    assert result["rows"][0]["label"] == label
    # The file and the screen are one run: the same status, the same rows, the same labels.
    assert (ran.returncode, result["status"]) == (1, "red")
    not_green = [row["label"] for row in result["rows"] if row["outcome"] != "green"]
    assert _lines(ran) == [f"koruyucu kırmızı: {text}" for text in not_green]
    assert not list(tmp_path.glob("result.json.*"))  # nothing left beside it


# -------------------------------------------------------------- (12) two ways of starting it


def test_started_with_file_and_typed_by_name_are_the_same_run(tmp_path):
    repo = _mixed(tmp_path)
    by_file_out = tmp_path / "by-file.json"
    by_name_out = tmp_path / "by-name.json"

    by_file = _guards(repo, "-OutFile", str(by_file_out))
    by_name = _shell(
        "-Command",
        f"Set-Location -LiteralPath '{REPO}'; "
        f"scripts\\team\\guards.ps1 -Worktree '{repo}' -Python '{sys.executable}' "
        f"-OutFile '{by_name_out}'; exit $LASTEXITCODE",
    )

    assert (by_file.returncode, by_name.returncode) == (1, 1), by_name.stdout + by_name.stderr

    def rows(path: Path) -> list[tuple]:
        return [
            (row["id"], row["path"], row["outcome"], row["label"], row["detail"])
            for row in _result(path)["rows"]
        ]

    assert rows(by_file_out) == rows(by_name_out)
    assert _lines(by_file) == _lines(by_name)


# ------------------------------------------------------------------------ (13) the lead's role


def test_the_leads_role_names_the_runner_and_keeps_its_frontmatter():
    assert GUARDS_SCRIPT.is_file()
    raw = LEAD_ROLE.read_bytes()
    assert raw.startswith(LEAD_FRONTMATTER.encode("utf-8"))
    body = raw[len(LEAD_FRONTMATTER.encode("utf-8")) :].decode("utf-8")
    assert "\n---\n" not in body  # the block above is the whole frontmatter
    assert body.count("scripts/team/guards.ps1") == 1
    paragraph = next(p for p in body.split("\n\n") if "scripts/team/guards.ps1" in p)
    assert "-Worktree" in paragraph and "gate" in paragraph
