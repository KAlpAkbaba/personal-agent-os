# ADR (number: the lead's) - Guard tests on the work branch: the list and the runner

Date: 2026-10-02 · Task: `branch-guards-runner` · Proposal:
`team/proposals/2026-10-02-koruyucu-testler-is-dalinda.md` · Status: the list and the runner
exist and are usable by hand; nothing in the cycle calls them (a later card does).

## Context

Three of the seven integration gates of 1-2 October were red on their first run, each on a
test that reads the WHOLE application and that neither the worker nor the inspector had run
(QUALIFICATION 38.10, ADR-0237 "The lead's wiring", ADR-0242 last paragraph). The full unit
suite is too long for a task run; this family is not.

## Decision

1. **The list is one file, `team/guards.json`**, six entries, all of which exist today and
   all of which the gate runs: `owner-error-language`, `ci-covers-every-suite`,
   `postgres-coverage-ratchet`, `host-snapshot-schema` (pytest, under
   `services/api/tests/unit/`), `script-syntax`, `installer-strictmode` (Windows PowerShell
   5.1, under `scripts/tests/`). Why these: each reads a whole tree as text or as a map
   (every error class, every suite, every table, every script), so a change anywhere can
   turn it red, and the first three are the exact tests behind the three red gates. A test
   holds the list to the tree: every path exists, and every PowerShell entry is named in
   `scripts/quality-gate.ps1` - a guard the gate does not run is refused.
2. **Contract of the list**: `{version: 1, guards: [{id (a-z, 0-9, '-'), kind: 'pytest' |
   'powershell', path (repository-relative, forward slashes), label (the Turkish sentence
   the owner sees when it is red)}]}`. Refused, with a Turkish reason each: an unknown
   kind, a duplicate or malformed id, an absolute path, a drive letter, a backslash, `..`,
   an empty label, an empty list, a version other than 1, a file that is not JSON.
3. **Contract of a run's RESULT**: `{at (UTC ISO-8601), sha (40-hex HEAD of the tree that
   was run), seconds, status: 'green' | 'red', rows: [{id, path, outcome, seconds, label,
   detail}]}`; one row per entry in the list's order; `status` is green only when every row
   is; `label` is copied from the list; `detail` is at most 400 characters of the run's own
   failing lines (pytest's `FAILED`/`ERROR` lines, a PowerShell suite's `FAIL` lines; the
   last three lines when a run fails without naming anything), empty for a green row.
4. **Four outcomes**: `green`; `red` (non-zero exit); `hung` (still going after
   `-HangSeconds`, default 600 - a hang guard, never a measure of success; the WHOLE process
   tree is stopped with `taskkill /T /F` and the guards after it still run); `missing` (the
   list names a file this tree does not have; the rest still run).
5. **Three exit codes of `scripts/team/guards.ps1`**: 0 = every row green; 1 = at least one
   row red, hung or missing; 2 = it could not run at all (no such directory, not a git work
   tree or no commit, no list, a refused list, no interpreter) - nothing is written to
   `-OutFile` then. It prints `koruyucular: yeşil`, or one Turkish line per row that is not
   green, with its own wording per outcome.
6. **The interpreter**: `-Python` defaults to the MAIN checkout's API environment,
   `<main>\services\api\.venv\Scripts\python.exe` - what `uv run` resolves to there (asked
   with `uv run --no-sync`: CPython 3.12.7) - found as the parent of
   `git rev-parse --git-common-dir`. The runner never runs `uv sync` and never makes a
   virtualenv in a worktree. That environment's editable install points `app` at the main
   checkout, but a guard is started as `python -m pytest <worktree file> -q --rootdir
   <worktree>\services\api -p no:cacheprovider` with the working directory
   `<worktree>\services\api`, and `-m` puts that directory first on `sys.path`: the code
   under test is the worktree's. Shown twice: a unit test (a module only the scratch tree
   has is imported from it), and on the real repository (a sentence removed from the scratch
   worktree's catalogue turned the guard red while main still had it).
7. **The tree is left as found**: `PYTHONDONTWRITEBYTECODE=1`, no cache provider, nothing
   written inside the worktree; `git status --porcelain --ignored` is identical before and
   after (unit test, on a tree with no `.gitignore`).
8. **A red guard stops nothing by itself.** On a TASK branch a guard can be red by nature:
   a new suite is wired into the gate only at integration, so `ci-covers-every-suite` is
   red on the branch that adds the suite, correctly. The runner reports; what a red row
   means is decided by who reads it. For the lead, before the gate on the INTEGRATION
   branch, it means: wire it or send it back, and do not start the 80-minute gate
   (`.claude/agents/lead.md`, one paragraph; by hand until the cycle step exists).
9. **pytest-testmon was looked at and not taken** (MIT, 2.2.0): it selects tests by the
   Python code a test executed; these guards read source files as TEXT, which it cannot
   track - it would skip exactly the guard that should run, and say so with confidence.

## Measured (2026-10-02, the owner's PC, a team cycle running beside it)

| guard | scratch worktree of base (C:) | main checkout "at rest" (CPU 42 %) | main beside a second pytest run |
|---|---|---|---|
| owner-error-language | 10.2 s | 8.6 s | 3.9 s |
| ci-covers-every-suite | 3.0 s | 2.8 s | 2.8 s |
| postgres-coverage-ratchet | 7.2 s | 6.3 s | 6.8 s |
| host-snapshot-schema | 4.0 s | 3.2 s | 3.3 s |
| script-syntax | 8.3 s | 5.2 s | 5.2 s |
| installer-strictmode | 2.9 s | 2.6 s | 2.5 s |
| **total** | **35.6 s** | **28.8 s** | **24.4 s** |

Under the 180 s the card allows, so no guard is dropped. "At rest" was never truly at rest
(the cycle's other seats were running); the first guard's time is mostly the first import
of `app` and falls once the files are in the disk cache.

## Consequences

- Not caught: a change that breaks ANOTHER family's behaviour test - the gate still finds it.
- The list can go stale (a new whole-tree test that nobody adds); the gate runs it anyway,
  so the loss is no worse than today.
- One guard is one Python start (3-10 s each); a single pytest process for the four would be
  faster, and is not done: one row per guard, one hang guard per guard, is the contract.
- For the lead to write: the TEAM_PROTOCOL clause, and - with the wiring card - the count of
  first-run-red gates over the next five integrations (PROVEN_REAL is that card's).
