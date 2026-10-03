# ADR (gate-faster): the gate's own database, and its independent suites side by side

Status: accepted by the worker; the lead numbers it (an ADR-0254 addendum for part A).
Date: 2026-10-03/04. Card: gate-faster (gate-own-database + gate-parallel-suites).

## A. The gate's own database (ADR-0254 open decision 5, ruling (b))

- **The variable.** `PAGENTOS_DATABASE_URL`: `Settings.database_url` (pydantic-settings, prefix
  `PAGENTOS_`) is what the application and `tests/integration/conftest.py` read
  (`build_engine(Settings().database_url)`). `services/api/tests/unit/test_gate_database_contract.py`
  reads the name from `quality-gate.ps1` (`$script:GateDatabaseVariable`) and resolves it from the
  Settings class, so the two halves cannot drift. conftest.py was NOT edited: it hard-codes no
  database and has no second connection. Its `exclusive_database` advisory lock is per database
  in PostgreSQL, so it no longer serialises a gate against an inspector.
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

**Risk.** Suites with timing assumptions flake under load (team-cycle's pool, team-integrate's
300 s native-tool timeout); the group raises load. Two gates at once are worse than either.
