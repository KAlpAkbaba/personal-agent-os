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
"""

from __future__ import annotations

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


def suites_missing_from(text: str, suites: set[str]) -> list[str]:
    """Suites that exist and the workflow never names. A pure function so the rule can be
    proved against a workflow that breaks it, not only against the one that satisfies it."""
    # The workflow names them with Windows separators inside a cmd block; match on the file
    # name alone so a path style change here does not read as a missing suite.
    named = set(re.findall(r"([a-z0-9-]+[.]tests[.]ps1)", text))
    return sorted(suites - named - set(PS_SUITES_NOT_IN_CI))


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
            f"apps/web defines '{script}' and CI never runs it - the gate exists and gates "
            "nothing"
        )


def test_the_web_job_still_builds_as_well_as_tests() -> None:
    """Tests and a build catch different things; adding the first must not drop the second."""
    assert "pnpm --dir apps/web build" in _workflow_text()
