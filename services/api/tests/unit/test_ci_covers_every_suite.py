"""B02 req 25/26/27/29: nothing that can gate may sit outside the gate.

This repository keeps discovering the same thing: a test suite exists, passes, and is run by
NOTHING. `harness-symbols.tests.ps1` was written on 2026-09-06 and first run by CI on
2026-09-09 - after the defect it describes reached the owner's install. On 2026-09-12 the
same shape, worse: the CI job's hand-typed list named 24 of the 26 PowerShell suites, and one
of the two it omitted was `cloud-release-bluegreen.tests.ps1` - 59 cases over the release path
production actually uses. The web job ran `build` and nothing else, so 82 files and 1587 tests
plus the package's own linter were behind no gate, while every milestone since M18 quoted a
web suite count in its evidence.

A hand-maintained list is fine. A hand-maintained list nobody checks is a gap with a schedule.
These tests are the check: a suite that exists must be named by the workflow, and the gates a
package defines for itself must be the gates CI runs.

2026-10-06 (ci-ps-suites-from-glob): the list itself became the problem - every new suite
added a line at the same place, and two parallel branches collided there at integration. The
step now FINDS the suites with a glob (`scripts\\tests\\*.tests.ps1`, sorted by name) and keeps
a small exception table (`$notInCi`). This file reads both forms: for the glob form "missing"
is computed over the set the glob covers, and the step's exception table must be the same set
as PS_SUITES_NOT_IN_CI - the two halves read each other.
"""

from __future__ import annotations

import fnmatch
import json
import re
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PS_SUITE_DIR = REPO_ROOT / "scripts" / "tests"
WEB_PACKAGE = REPO_ROOT / "apps" / "web" / "package.json"

#: Scripts a package defines that are GATES - they must appear in CI. `dev`/`start` run a
#: server and `build` was always there; these three are the ones that can say "no".
WEB_GATE_SCRIPTS = ("lint", "typecheck", "test")

#: A suite may be left out of CI only for a reason written down here, next to its name. The
#: dict is empty on purpose: today every suite runs, and an entry added later has to carry
#: its justification in the same commit that stops running it.
PS_SUITES_NOT_IN_CI: dict[str, str] = {}


@lru_cache(maxsize=1)
def _workflow_text() -> str:
    return WORKFLOW.read_text("utf-8")


def _ps_suites() -> set[str]:
    return {p.name for p in PS_SUITE_DIR.glob("*.tests.ps1")}


def test_the_workflow_is_where_this_test_thinks_it_is() -> None:
    """A guard on the guard: a moved workflow would make every assertion below vacuous."""
    assert WORKFLOW.is_file(), WORKFLOW
    assert "name: CI" in _workflow_text()
    assert _ps_suites(), "no PowerShell suites found; the glob is wrong"


PS_STEP_NAME = "PowerShell 5.1 script suites"

#: `scripts\tests\<pattern>.tests.ps1` where the pattern holds a wildcard - the glob form.
_GLOB = re.compile(r"scripts[\\/]tests[\\/]([A-Za-z0-9*?-]*[*?][A-Za-z0-9*?-]*)[.]tests[.]ps1")
#: `-File scripts\tests\<name>.tests.ps1` - the hand-listed form.
_LISTED = re.compile(r"-File\s+scripts[\\/]tests[\\/]([a-z0-9-]+[.]tests[.]ps1)")
#: One row of the step's exception table: `'name.tests.ps1' = 'reason'`.
_EXCEPTION_ROW = re.compile(r"^\s*'([a-z0-9-]+[.]tests[.]ps1)'\s*=\s*'([^']*)'", re.M)


def ps_step(text: str) -> str:
    """The 'PowerShell 5.1 script suites' step, header to the next step. Prose elsewhere in
    the workflow (the comments quote suite names) must not count as running a suite."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(rf"^(\s*)- name: {re.escape(PS_STEP_NAME)}\s*$", line):
            indent = len(line) - len(line.lstrip())
            block = [line]
            for nxt in lines[i + 1 :]:
                stripped = nxt.lstrip()
                if stripped and len(nxt) - len(stripped) <= indent:
                    break
                block.append(nxt)
            return "\n".join(block)
    return ""


def step_globs(step: str) -> list[str]:
    return [f"{m}.tests.ps1" for m in _GLOB.findall(step)]


def step_exceptions(step: str) -> dict[str, str]:
    """The step's own `$notInCi = @{ ... }` table (empty when absent)."""
    m = re.search(r"\$notInCi\s*=\s*@\{(.*?)^\s*\}", step, re.S | re.M)
    return dict(_EXCEPTION_ROW.findall(m.group(1))) if m else {}


def suites_missing_from(text: str, suites: set[str]) -> list[str]:
    """Suites that exist and the workflow's PowerShell step does not run. A pure function so
    the rule can be proved against a workflow that breaks it, not only against the one that
    satisfies it. Reads both forms: a glob (what the step covers is what the glob matches,
    less its exception table) or the older hand-typed list."""
    step = ps_step(text)
    globs = step_globs(step)
    if globs:
        covered = {s for s in suites if any(fnmatch.fnmatchcase(s, g) for g in globs)}
        covered -= set(step_exceptions(step))
    else:
        covered = set(_LISTED.findall(step))
    return sorted(suites - covered - set(PS_SUITES_NOT_IN_CI))


def test_ci_runs_every_powershell_suite() -> None:
    missing = suites_missing_from(_workflow_text(), _ps_suites())
    assert not missing, (
        "these PowerShell suites exist and CI runs none of them - add them to the "
        f"'PowerShell 5.1 script suites' step, or record why not in PS_SUITES_NOT_IN_CI: {missing}"
    )


def test_the_rule_would_have_caught_the_gap_it_was_written_for() -> None:
    """The falsification. A checker only ever run against a passing input has proved nothing -
    and this one exists precisely because a list drifted for days without anybody noticing.

    The fixture is the shape of the real 2026-09-12 workflow: a long cmd chain that names most
    of the suites and quietly omits the blue/green release one.
    """
    suites = {"cloud-release.tests.ps1", "cloud-release-bluegreen.tests.ps1", "provision.tests.ps1"}
    drifted = (
        "      - name: PowerShell 5.1 script suites\n"
        "        run: |\n"
        "          powershell -NoProfile -File scripts\\tests\\cloud-release.tests.ps1 && ^\n"
        "          powershell -NoProfile -File scripts\\tests\\provision.tests.ps1\n"
    )
    assert suites_missing_from(drifted, suites) == ["cloud-release-bluegreen.tests.ps1"]
    # ...and says nothing when the list is complete.
    complete = drifted.replace(
        "provision.tests.ps1\n",
        "provision.tests.ps1 && ^\n"
        "          powershell -NoProfile -File scripts\\tests\\cloud-release-bluegreen.tests.ps1\n",
    )
    assert suites_missing_from(complete, suites) == []


#: The shape of the glob step, for the falsifications below. `{glob}` and `{rows}` are the
#: two things a careless edit changes.
_GLOB_STEP = (
    "      - name: PowerShell 5.1 script suites\n"
    "        shell: powershell\n"
    "        run: |\n"
    "          $notInCi = @{{\n"
    "{rows}"
    "          }}\n"
    "          $suites = Get-ChildItem -Path 'scripts\\tests\\{glob}.tests.ps1' -File |"
    " Sort-Object Name\n"
    "          foreach ($suite in $suites) {{\n"
    "            & {engine} -NoProfile -File $suite.FullName\n"
    "          }}\n"
    "      - name: Next step\n"
    "        run: echo scripts\\tests\\provision.tests.ps1\n"
)


def _glob_step(glob: str = "*", rows: str = "", engine: str = "powershell.exe") -> str:
    return _GLOB_STEP.format(glob=glob, rows=rows, engine=engine)


def test_the_glob_rule_catches_a_narrowed_glob() -> None:
    """The glob form's falsification: a glob narrowed to one family leaves the rest unrun, and
    a suite named by a LATER step's text does not count as run by this one."""
    suites = {"cloud-release.tests.ps1", "cloud-release-bluegreen.tests.ps1", "provision.tests.ps1"}
    assert suites_missing_from(_glob_step("cloud-*"), suites) == ["provision.tests.ps1"]
    assert suites_missing_from(_glob_step("*"), suites) == []
    # An exception row takes the suite out of the covered set; PS_SUITES_NOT_IN_CI (empty
    # today) is the only thing that may excuse it, so here it reads as missing.
    row = "            'provision.tests.ps1' = 'needs a desktop session the runner has none of'\n"
    assert suites_missing_from(_glob_step("*", rows=row), suites) == ["provision.tests.ps1"]


def test_the_workflow_finds_the_suites_with_a_glob() -> None:
    """The step is the glob form today - the hand-typed list was what collided at integration."""
    step = ps_step(_workflow_text())
    assert step, f"the '{PS_STEP_NAME}' step is gone"
    assert step_globs(step) == ["*.tests.ps1"], step_globs(step)
    assert "Sort-Object Name" in step, "the suites must run in name order"


def test_the_step_exception_table_is_ps_suites_not_in_ci() -> None:
    """The two halves read each other: the step's `$notInCi` table and PS_SUITES_NOT_IN_CI are
    one set. Either changing alone is red."""
    assert set(step_exceptions(ps_step(_workflow_text()))) == set(PS_SUITES_NOT_IN_CI)
    row = "            'team-board.tests.ps1' = 'flaky on the runner, carded'\n"
    assert step_exceptions(ps_step(_glob_step(rows=row))) == {
        "team-board.tests.ps1": "flaky on the runner, carded"
    }


def engine_problems(step: str) -> list[str]:
    """What keeps the step from running every suite under Windows PowerShell 5.1."""
    problems = []
    if not re.search(r"&\s*powershell[.]exe\s+-NoProfile\s+-File\s", step):
        problems.append("each suite must run as 'powershell.exe -NoProfile -File'")
    if re.search(r"\bpwsh\b", step):
        problems.append("pwsh is PowerShell 7; these suites are 5.1 suites")
    if not re.search(r"^\s*shell:\s*(powershell|cmd)\s*$", step, re.M):
        problems.append("the step's own shell must be Windows PowerShell or cmd")
    return problems


def test_the_step_runs_each_suite_on_windows_powershell_5_1() -> None:
    assert engine_problems(ps_step(_workflow_text())) == []
    assert engine_problems(_glob_step()) == []
    assert engine_problems(_glob_step(engine="pwsh")) != []
    assert engine_problems(_glob_step().replace("shell: powershell", "shell: pwsh")) != []


def test_every_suite_ci_names_still_exists() -> None:
    """A name left behind after a rename makes the step fail for the wrong reason."""
    named = set(re.findall(r"([a-z0-9-]+[.]tests[.]ps1)", _workflow_text()))
    # Names appearing only in prose (this file's own comments quote some) are excluded by
    # requiring the workflow's cmd form.
    invoked = set(re.findall(r"scripts.tests.([a-z0-9-]+[.]tests[.]ps1)", _workflow_text()))
    stale = sorted(invoked - _ps_suites())
    assert not stale, f"CI invokes suites that do not exist: {stale}"
    assert invoked <= named


def test_every_suite_left_out_carries_a_reason() -> None:
    for name, reason in PS_SUITES_NOT_IN_CI.items():
        assert name in _ps_suites(), f"{name} is excluded from CI but does not exist"
        assert len(reason) > 20, f"{name} is excluded from CI with no real reason: {reason!r}"


def test_ci_runs_every_gate_the_web_package_defines() -> None:
    package = json.loads(WEB_PACKAGE.read_text("utf-8"))
    scripts = package.get("scripts", {})
    text = _workflow_text()
    for script in WEB_GATE_SCRIPTS:
        assert script in scripts, f"apps/web no longer defines a '{script}' script"
        assert f"pnpm --dir apps/web {script}" in text, (
            f"apps/web defines '{script}' and CI never runs it - the gate exists and gates nothing"
        )


def test_the_web_job_still_builds_as_well_as_tests() -> None:
    """Tests and a build catch different things; adding the first must not drop the second."""
    assert "pnpm --dir apps/web build" in _workflow_text()
