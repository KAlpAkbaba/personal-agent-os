# ADR (gate-faster): the gate's own database, and its independent suites side by side

Status: accepted by the worker; the lead numbers it (an ADR-0254 addendum for part A).
Date: 2026-10-03/04. Card: gate-faster (gate-own-database + gate-parallel-suites).

## A. The gate's own database (ADR-0254 open decision 5, ruling (b))

- **The variable.** `PAGENTOS_DATABASE_URL`: `Settings.database_url` (pydantic-settings, prefix
  `PAGENTOS_`) is what the application and `tests/integration/conftest.py` read
  (`build_engine(Settings().database_url)`). `services/api/tests/unit/test_gate_database_contract.py`
  reads the name from `quality-gate.ps1` (`$script:GateDatabaseVariable`) and resolves it from the
  Settings class, so the two halves cannot drift. conftest.py was NOT edited: it hard-codes no
  database and has no second connection for the reset. Its `exclusive_database` advisory lock is
  per database in PostgreSQL, so it no longer serialises a gate against an inspector.
- **One server, its connections (returned 2026-10-04).** Two runs on two databases still share
  the server's connections: two side by side on the dev server (`max_connections` 300) both died
  on `too many clients already` (69+52 failed, 46+69 errors); one run alone peaks at 231. So
  conftest.py WAS edited after all: a session fixture `server_run_slot` takes one of
  `server_run_slots()` advisory-lock slots in the server-wide `postgres` database before anything
  connects (`_RUN_CONNECTION_BUDGET = 240`, `_SERVER_HEADROOM_CONNECTIONS = 40`: one slot on the
  dev server today, more on a larger one), waits up to 1800 s and says so in the log. Measured
  after: the same two runs side by side, 178 passed + 11 xfailed each (269 s; the second waited
  and ended at 525 s). Integration runs on one server are therefore still one at a time - but they
  wait for each other instead of corrupting each other, and the schema reset is no longer shared.
- **The name rule** (`scripts/lib/GateDatabase.ps1`): `pagentos_gate_<yyyyMMddHHmmss UTC>_<run>`
  or `pagentos_scratch_*`, only `[a-z0-9_]`, at most 63 characters. New- and Remove- refuse any
  other name before a command is sent (`pagentos`, `postgres`, `template1`, `pagentos_prod`, a
  quote, `;`, a space, an upper-case letter, 64 characters: 18 refusals, case 2).
- **How it is reached.** `docker exec -i pagentos-postgres psql` (the dev stack's container,
  as `dev-up.ps1` does); SQL on stdin, no password on a command line or in a log (case 5).
  `CREATE EXTENSION vector` in the new database (migration 0001 needs it).
- **What the gate no longer shares.** "Alembic upgrade head" and "API integration tests" (the
  only two gate steps that touch PostgreSQL) run against `pagentos_gate_<run>`, made at the first
  step's start and dropped in a `finally` (its own row, "Gate database dropped"). The variable is
  set only around those steps' child processes and put back after (`Invoke-WithGateDatabase`).
  The gate does not connect to `pagentos` at all (case 7a/7b).
- **The 24-hour sweep.** `$script:GateDatabaseMaxAgeHours = 24`: at the first step the gate drops
  `pagentos_gate_*` whose name stamp is older and prints how many; scratch databases and
  `pagentos` are never swept (case 4a/4b, injected clock).
- **Cost.** Measured 2026-10-04 00:46: sweep 0.2 s + create 0.4 s + settings URL 0.4 s + alembic
  from zero 6.6 s + drop 0.3 s = 8.1 s, against 6.4 s for the old in-place upgrade: about +2 s.
- **By hand (inspectors, workers)** - three lines, the lead puts them into the role files:
  ```powershell
  . scripts/lib/GateDatabase.ps1; $db = Get-GateDatabaseName -RunId "<task>" -Now ([datetime]::UtcNow); New-GateDatabase -Name $db
  $url = Get-GateDatabaseUrl -BaseUrl (Get-GateSettingsDatabaseUrl -Uv (Get-Command uv).Source -ApiRoot services/api) -Name $db
  try { Invoke-WithGateDatabase -Variable PAGENTOS_DATABASE_URL -Value $url -Action { Push-Location services/api; uv run pytest tests/integration -q -m integration; Pop-Location } } finally { Remove-GateDatabase -Name $db }
  ```
- **Its own suites in the gate.** `gate-database.tests.ps1` is the gate step "Gate database tool
  (PS5.1 + the dev server)" right after the database is dropped; `gate-steps.tests.ps1` is "Gate
  suite group (PS5.1, fake steps)" below the group (its cases time fake steps). Both are in ci.yml.
- integrate-own-lock's `-BesideCycle` may be scheduled after this lands: the gate and an
  inspection no longer reset one database under each other.

## B. Independent suites side by side

`scripts/lib/GateSteps.ps1` `Invoke-GateStepGroup`: each step its own `powershell.exe -NoProfile
-ExecutionPolicy Bypass -File`, stdout and stderr drained into two files (never merged), at most
`$script:GateStepMaxParallel = 3` at once, a lane runs one step at a time, deadline
`$script:GateStepDeadlineSeconds = 7200` (a hang guard; a step past it is killed with its tree by
`taskkill /T /F` and is `FAILED: <what> TIMEOUT: did not end within 7200 s; killed with its process
tree`). Pass/fail is the exit code, as `Assert-ExitCode`. Every `Invoke-Step` and its
`Assert-ExitCode` stay in `quality-gate.ps1`; between `Start-GateGroup` and `Complete-GateGroup`
they are recorded and run by the group. `-GateSerial` runs all as before; `-GateMaxParallel n`.

**A step that throws while it is recorded** (a missing tool: `throw "Windows PowerShell 5.1 not
found"`) used to end the whole gate without its summary (returned 2026-10-04). Now it is a failed
step in the sequential words (`FAILED: <the message>`), keeps its row in the listed order, and the
rest of the group runs (gate-steps case 10). **Lanes** have their own case (9: two steps of one
lane never overlap while a lane-less one runs beside them; 9b: the gate's wiring of the lane).

**A failure** prints `=== <step> ===`, the step's whole log (failed steps first), and
`FAILED: <what> exited with code <n>` - the same words as sequentially (case 7); the table keeps
the listed order; the last lines are the old literal `QUALITY GATE: FAIL` / `exit 1`.

**Evidence per suite** (read 2026-10-03: no listener on a fixed port, temp paths all carry a guid,
no database, no shared worktree, no machine store): script-syntax, machine-readable,
installer-invocation / -browser / config-swap (the `127.0.0.1:8001` they name is a string in a
config under test, nothing listens), installer-strictmode / -deploy / -acl / -evidence,
owner-enrollment (`19222` is a string), agent-update, agent-audit, owner-explain (`:3000` is a
string), cloud-secret, cloud-release, cloud-release-bluegreen, provision, identity-restore,
native-signing-trust (CurrentUser throwaway stores only), team-tick, maintenance-reboot,
host-snapshot (fake docker/psql), web-tailnet-https (fake tailscale).
**Lane `fake-team-api`**: team-integrate, team-feed - each starts the fake team API on a port
probed free and then released (a small race between suites), so one at a time.
**Stay sequential, below the group**: utf8-json (listens on the FIXED port 18099); team-area (its
suite reads its own step's `& $powershell5` line in the gate); team-cycle (two pool cases that
time seats against each other were red beside the group and green alone, 2026-10-03); recovery
supervisor and browser (uv/Playwright, not PowerShell suites); M1 E2E (the desktop); the web steps.

**Measured** (step seconds, this machine):

| | sequential, gate 7a6531ef (quiet) | group run 1 (27 suites, beside a full gate) | group run 2 (25 suites, beside a gate's api unit suite) |
|---|---|---|---|
| team-integrate | 520 | 1209 (lane) | 1519 (lane, one git push timed out: red) |
| team-cycle | 821 | 1234 (lane, 2 timing cases red) | - (sequential now) |
| team-feed | 123 | 294 | 177 |
| blue/green | 516 | 978 | 1362 |
| the other suites | 226 | 399 | 861 |
| **wall** | **2206** (27 suites) | **2738** | **1697** (vs 1383 sequential for these 25, quiet) |

Neither group run was on a quiet machine: other full gates were running (the queue gave ONAY
because they held only `heavy`). Under that load every suite ran 2-20x slower, and the group
added to it. **No saving was measured.** Projected on a quiet machine: the lane is the longest
path (643 s), so the group would take about 650-700 s instead of 1383 s: about 11-12 minutes
saved per gate - a projection from the quiet sequential numbers, not a measurement. The time
that stays: the api unit suite (2398 s), team-cycle (821 s), the Windows agent build (182 s),
browser (222 s), integration (229 s).

**Measured on the branch, 2026-10-05** (the 25 grouped suites; `-OnlyStep` slices, the api unit
suite left to the lead's gate; the lead's full gate on main ran beside EVERY run below - its api
unit step during the width-3 and serial runs, its later steps during the width-2 run - and an
utterance corpus beside the serial one; so the loads are close, not equal):

| | serial (`-GateSerial`) | group, width 3 (the default) | group, width 2 |
|---|---|---|---|
| team-integrate (lane) | 982 | 2157 | 1281 |
| team-feed (lane) | 257 | 362 | 207 |
| blue/green | 867 | 852 | 976 |
| the other 22 | 403 | 359 | 335 |
| **wall** | **2509** | **2523** | **1487** |

At width 3 there was **no saving**: team-integrate (git-heavy, the lane) ran 2.2 times slower beside
two other suites and the lane became the whole wall. At width 2 the group saved **1022 s (17 min)**
against the serial run. The card fixes the default at 3 (`$script:GateStepMaxParallel`, case 6b);
the measurement says 2 on this machine: the lead decides (`-GateMaxParallel 2` meanwhile). Where
the time still is: the api unit suite (about 40 min), team-cycle (1108-1658 s, sequential), the
Windows agent tests (392-805 s; its Unity test fails under memory load, GetLastError 1455 / past
600 s), web build (455 s), recovery supervisor (446 s), integration (343 s).
The rest of that day's gate on the branch: every other step green, except team-cycle's board case
and the browser engine-probe case, both of which read the worker seat's own environment (the board
address variable; Git Bash's '/tmp' warning in this session) - team-cycle green with them unset
(261/0); the browser step needs the lead's gate. A deliberate red (agent-audit made to exit 4 in a
scratch copy) failed the gate with `FAILED: agent audit tests exited with code 4`, its log in the
output, and `QUALITY GATE: FAIL`.
A gate killed from outside (twice that day another session restarted its gate and this one died in
the same second, exit 1, no summary) is not a defect the gate can fix; kill a gate by its PID.

**Risk.** Suites with timing assumptions flake under load (team-cycle's pool, team-integrate's
300 s native-tool timeout); the group raises load. Two gates at once are worse than either.
