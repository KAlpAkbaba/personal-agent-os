# ADR draft: a red gate reruns only its failed steps when the fix stays in their files

Card: gate-rerun-failed-steps (cycle d20261006). The owner, 2026-10-06: "bu kapının daha hızlı
kontrolünü sağlamanın bir yolu var mı, çok zaman kaybediyoruz". The Danışman twice reran only the
failed steps by hand (`quality-gate.ps1 -OnlyStep`, 10-15 min instead of 95) and released on
"gate green on A except steps X, steps X green on B". This makes that a rule the integrate step
applies by itself.

## Decision

After a red gate on sha A (`scripts/team/integrate.ps1`, step 5), the next attempt on the
branch at sha B runs `scripts/quality-gate.ps1 -OnlyStep <steps>` instead of the full gate when
`Get-TeamGateRerunDecision` (scripts/lib/TeamGateRerun.ps1) says "partial":

- the last attempt is the GATE's red (not the guards'), finished (`QUALITY GATE: FAIL`), named
  its steps, and was NOT itself a partial rerun (never two in a row; "cleared" also means full);
- B descends from A, and every file A..B changes is in a family of the step map whose steps
  include a red one (the family is rerun whole);
- or A..B changes nothing (B is A) and the red was the environment's (too many clients, Docker
  down, a junction node_modules, no disk, /tmp missing): the same steps on the same sha, once.
  The integrate step's "red on this very commit, wait" lets that one case through.

Everything else is the full gate: a file in no family, a family with no red step (code shared
with green steps), the gate's own files (`scripts/quality-gate.ps1`, `scripts/lib/Gate*.ps1`),
lock and project files, migrations, `infra/**`, `conftest.py`, and `services/api/app/**`
(application code is shared with steps beyond its two test steps - the card's rule).

The step map (`$script:TeamGateRerunFamilies` + one family per suite the gate runs, read from
the gate script's own `Invoke-Step ... scripts\tests\<x>.tests.ps1` lines):

| paths | steps |
|---|---|
| services/api/tests/integration/** | API integration tests |
| services/api/tests/unit/** | API unit tests |
| services/api/tests/*.py (shared test support) | API unit tests + API integration tests |
| apps/web/** (package.json excepted) | Web shell build + Web shell lint, unit tests and types |
| scripts/tests/<suite>.tests.ps1 | the step that runs that suite |

Needs: API integration tests -> Dev stack up, Alembic upgrade head, Gate database dropped (the
gate's own database is made and dropped by steps of their own). Always in a slice: Required
files, Secret hygiene, the two ruff steps, Script syntax (seconds each).

Documents tests read (docs/HANDOFF.md, docs/DECISIONS.md, state/BUILD_STATE.json, team/plans,
team/reports) are not "nobody's": the steps whose tests name them are ADDED to the slice
(`Get-TeamGateReaderSteps`: services/api tests or app code -> both API steps; a gate suite ->
its step; apps/web -> the web steps). A suite the gate does not run naming it -> full gate. A
script library naming it (a protected-file list) is not counted as a reader: the suites that
exercise the libraries run them on sandbox copies. This is a judged risk, named here.

The same reader search runs for EVERY changed file the full gate does not already take (first
inspection, 2026-10-07: `scripts/tests/team-feed.tests.ps1` reads `apps/web/tests/approvals/
fixtures.ts`; `services/api/tests/unit/test_gate_database_contract.py` reads
`scripts/tests/gate-database.tests.ps1`): a test file, fixture or suite another step's test names
adds that step to the slice; a name found in a suite the gate does not run is the full gate. The
search is by the file's leaf name over services/api tests and app, apps/web src and tests, and
the gate-run suites, read once per plan; a file is not its own reader. A generic leaf name
(`index.ts`) over-matches - that only adds steps or sends it to the full gate (fail-closed). On
the real tree: fixtures.ts -> the web steps + "Agent team roadmap feeder"; gate-database.tests.ps1
-> both API steps. A suite naming a real file only to build a sandbox must spell it indirectly
(team-gate-rerun.tests.ps1 uses `$dot`), or it is counted as a reader.

Ancestry is checked BEFORE "nothing changed": a B that does not descend from A has no change list
(the branch was rebuilt), and an empty list there is not "the same content" - the full gate.

## The record

A partial green record: `result: green`, `sha: B`, `log:` A's (red) full-gate log,
`rerun_log:` B's slice log, `rerun_of: <n of A>`, `gate_sha: A`, `gate_log`, `rerun_steps`.
`log` deliberately names A's FAIL log: a release step that does not know the chain finds FAIL and
refuses (fail-closed). `Test-TeamGateRerunChain` is the check a release step must make: the red
record exists, red on A; every red step was rerun; B descends from A; A's log finished and failed
only in those steps; B's log is green for every rerun step (each ran, none SKIPPED, no unmatched
pattern). A partial red record carries the same `rerun_of` fields, so the next red is full.

## Open (for the lead at merge)

- `scripts/lib/TeamRelease.ps1` (outside this card's area): `Find-TeamReleaseGate` must, for a
  green record with `rerun_of`, call `Test-TeamGateRerunChain` and read `rerun_log` instead of
  `log`; until then a rerun green on main is refused by the release step (no auto-release; the
  Danışman releases by hand as on 2026-10-06). The suite's case "release (scripts/lib/TeamRelease.ps1
  - ALAN_ISTEGI)" is RED until that is wired. `Test-TeamGateRerunChain` needs git, so
  `Find-TeamReleaseGate` takes a `-RepoRoot` (the case passes it) and release.ps1 must pass its own.
- `scripts/quality-gate.ps1`: add `scripts\tests\team-gate-rerun.tests.ps1` as a step (PS5.1,
  git, fakes, ~2-3 min).
- `scripts/tests/team-integrate.tests.ps1`'s sandbox copies five libraries by name; without
  TeamGateRerun.ps1 there the integrate step runs the full gate (TeamIntegrate.ps1 loads it only
  when present) - unchanged behaviour, so that suite stays green.

## Alternatives rejected

- Rerun the red steps plus any step a change touches (instead of a full gate for code shared
  with green steps): faster, but the card asks for the full gate there, and the map is young.
- A separate "green_rerun" result: every reader of `green` (strikes, the idempotent finish,
  Complete-GreenGate) would need it; the extra fields on `green` keep them working.
