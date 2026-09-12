"""B08: one exit code, one meaning.

Merging the recovery-supervisor work onto main put two different failures on ``exit 82`` in
``release-cloud-core-bluegreen.sh``: "another operation holds the lock" (retry later) and
"the migration failed" (stop and look at the database). Opposite instructions on one number.

Neither side's tests could see it. The lock is taken at the top of the script and the
migration runs much later, so no single run reaches both - each suite asserted its own number
and passed. It was only visible by reading the whole script at once, which is what this test
does, mechanically, from now on.

The scripts are bash and these tests are Python: this file reads the other side's source
rather than restating what it believes the codes to be, the same discipline the device
contract tests follow.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

CLOUD = Path(__file__).resolve().parents[4] / "scripts" / "cloud"
#: Every operator script with an exit vocabulary of its own. `restore-cloud-core.sh` earned
#: its place immediately: adding `--from-offhost` in B09 reached for 96 and 97, both of which
#: already meant something else in that file. The guard caught its own author.
RELEASE_SCRIPTS = (
    "release-cloud-core-bluegreen.sh",
    "release-cloud-core.sh",
    "restore-cloud-core.sh",
    "backup-cloud-core.sh",
)

#: Codes deliberately raised from more than one place because they ARE one failure, said
#: about different subjects. Whether two messages describe the same failure is not something
#: a regular expression can judge, so it is a named decision instead: adding to this list is
#: somebody deciding, and a NEW collision still fails.
SHARED_BY_DESIGN: dict[str, dict[int, str]] = {
    "release-cloud-core-bluegreen.sh": {
        75: (
            "a colour never became healthy - the idle one during a release, the previous "
            "one during a rollback. One failure, two subjects, and the operator's next "
            "move is the same either way."
        ),
        77: (
            "the edge reload failed. Release, rollback and reconcile each say so in their "
            "own words; the failure and the remedy are identical."
        ),
    },
    "restore-cloud-core.sh": {
        90: (
            "something else holds a lock - the backup's or the blue/green operation's. One "
            "failure ('wait and try again'), two locks."
        ),
        91: (
            "--from-offhost with no off-host repository: no configuration file, or a file "
            "that names no repository. One gap, said about two ways of having it."
        ),
    },
}


def _exits(script: Path) -> dict[int, list[str]]:
    """Every ``exit <n>`` in the script, with the line it is on, for n >= 64.

    Below 64 are the conventional codes (0 success, 1 generic, 2 misuse) and the shell's own;
    this is about the script's OWN vocabulary, which is where a collision costs something.
    """
    found: dict[int, list[str]] = defaultdict(list)
    for raw in script.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        for match in re.finditer(r"\bexit (\d+)", line):
            code = int(match.group(1))
            if code >= 64:
                found[code].append(line)
    return found


@pytest.mark.parametrize("name", RELEASE_SCRIPTS)
def test_the_script_has_its_own_exit_vocabulary_at_all(name: str) -> None:
    """A guard that finds nothing proves nothing: if the parse breaks, this fails first."""
    script = CLOUD / name
    assert script.exists(), script
    assert _exits(script), f"no exit codes were parsed out of {name}"


@pytest.mark.parametrize("name", RELEASE_SCRIPTS)
def test_no_exit_code_carries_two_different_meanings(name: str) -> None:
    """The same number twice is fine when it is the same failure said in two places - a
    couple of paths legitimately share "the colour never became healthy". It is not fine when
    the two lines are different failures, because the operator, the harness and the recovery
    timer all read the number and nothing else.
    """
    collisions: list[str] = []
    for code, lines in sorted(_exits(CLOUD / name).items()):
        if code in SHARED_BY_DESIGN.get(name, {}):
            continue
        distinct = {re.sub(r"\s+", " ", line) for line in lines}
        # An `exit N` on its own line carries no message; it is the sites WITH a message
        # that assert a meaning, so a bare repeat is not evidence of two meanings.
        speaking = {line for line in distinct if "echo" in line or ">&2" in line}
        if len(speaking) > 1:
            collisions.append(f"exit {code}: {sorted(speaking)}")

    assert not collisions, (
        "these exit codes mean two different things in one script, so the number cannot "
        "tell an operator what to do. If the two really ARE one failure said twice, add "
        "the code to SHARED_BY_DESIGN with the reason:\n" + "\n".join(collisions)
    )


def test_the_shared_codes_are_each_explained() -> None:
    """An allowlist without reasons is a way to make a guard quiet, not a way to record a
    decision."""
    assert SHARED_BY_DESIGN
    for script, codes in SHARED_BY_DESIGN.items():
        for code, reason in codes.items():
            assert len(reason) > 40, f"{script} exit {code} repeats without saying why"


def test_the_shared_codes_really_are_shared() -> None:
    """The other direction: an entry that no longer describes anything is stale, and a stale
    exemption hides the next real collision on that number."""
    for script, codes in SHARED_BY_DESIGN.items():
        used = _exits(CLOUD / script)
        for code in codes:
            assert len(used.get(code, [])) > 1, (
                f"{script} exit {code} no longer repeats; drop the entry"
            )


def test_the_lock_and_the_migration_do_not_share_a_number() -> None:
    """The specific collision this file was written for, pinned by name: retry-later and
    stop-and-look must never be the same number again."""
    script = (CLOUD / "release-cloud-core-bluegreen.sh").read_text(encoding="utf-8")

    lock = re.search(r"holds the lock.*?\n.*?exit (\d+)", script, re.S)
    migration = re.search(r"migration FAILED.*?exit (\d+)", script, re.S)

    assert lock and migration, "one of the two failure paths was not found"
    assert lock.group(1) != migration.group(1)


def test_the_documented_reconcile_family_matches_the_code() -> None:
    """The header enumerates the --reconcile exits. A comment that drifts from the script is
    worse than no comment: it is read by whoever is deciding what to do at 3am."""
    script = (CLOUD / "release-cloud-core-bluegreen.sh").read_text(encoding="utf-8")
    header = script.split("# -----", 1)[0]

    documented = {int(n) for n in re.findall(r"\b(8[0-9]) ", header)}

    assert documented, "the --reconcile exit documentation was not found in the header"
    used = set(_exits(CLOUD / "release-cloud-core-bluegreen.sh"))
    undocumented = sorted(code for code in used if 80 <= code <= 89 and code not in documented)
    assert not undocumented, f"these 8x exits are used and not documented: {undocumented}"
