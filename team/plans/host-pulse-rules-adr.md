# ADR (draft, host-pulse-rules): the home PC's pulse - readers, pure rules, and where the cycle calls them

Status: accepted for the rule half (card `host-pulse-rules`, cycle d20261003). The lead numbers it.
Proposal: `team/proposals/2026-10-03-ev-pc-nabzi.md`.

## Context

In 36 hours the home PC ran out of a resource three times and the lead found each one hours later
by hand: 2 671 896 items in `%TEMP%`, 48 GB of memory exhausted by one 14 GB `pytest`, and a
finished run's `tail -f` + `grep` holding the scheduler's tick open. The cycle opened seats without
looking at the machine.

## Decision

`scripts/lib/TeamHostPulse.ps1` (Windows PowerShell 5.1, StrictMode, dot-sourceable, no top-level
side effects) in two halves:

- **Readers** - the only functions that touch the machine, each with its source as a parameter so
  tests inject it: `Get-TeamHostMemory` (Win32_OperatingSystem -> FreeBytes, TotalBytes,
  FreePercent), `Measure-TeamTempItems -Path -BudgetMs 2000` (Count, TooLarge, TopPrefix),
  `Get-TeamDriveFree -Drives C,E` (per drive FreeBytes or Missing), `Get-TeamProcessSnapshot`
  (Win32_Process -> ProcessId, ParentProcessId, Name, WorkingSetBytes, CreationDate).
- **Pure rules**, no I/O: `Get-TeamHostPulse`, `Get-TeamOrphanTree`, `Test-TeamHostPulse`,
  `Get-TeamPulseThresholds`, `Compare-TeamTempGrowth`, `Format-TeamPulseLine`.

Thresholds (top-level keys of `team/cycle-settings.json`, defaults when a key is missing or not a
non-negative number): `min_free_memory_percent` 15, `max_temp_items` 500000, `min_drive_free_gb` 20
(each present drive), `temp_growth_alarm` 100000. One direction everywhere: below a minimum or above a
maximum fails, equal passes. 15 % of 48 GB is ~7 GB - the room the owner's web shell and an
interactive session need while one more seat would start; 500 000 TEMP items is a fifth of the
level that made the unit step take 1 h 48 min; 20 GB is what one gate's build outputs and a
corpus run write; a growth of 100 000 a day reaches the 2.6 million level in under a month.

- **Time-bounded count.** The TEMP count is top level only, never recursive, never opens a file,
  and stops when `BudgetMs` runs out: `TooLarge` is then true, the count so far is kept, and
  `TooLarge` itself fails the check (a count that cannot finish in 2 s is the finding). The line
  shows it as `TEMP <n>+`.
- **Orphans are a pid tree from recorded roots, never a name list.** `RunRoots` are the records the
  wiring card takes from `Start-TeamRun` (`Pid`, `TaskId`, `Finished`, `StartedAt`). An orphan is a
  live descendant (ParentProcessId walk, visited set so a stale a->b->a snapshot terminates) of a
  root whose `Finished` is true. The root pid itself is never returned. Selecting by name would make
  the owner's VR, Chrome, his own `pytest` and `next dev` candidates; a pid tree from roots the
  cycle itself started cannot reach them.
- **Pid-reuse guard.** Windows keeps a dead parent's pid in `ParentProcessId`, and pids are reused.
  A child is accepted only when created at or after its parent; the root's creation is taken as its
  `StartedAt`, so nothing created before the run started is ever the run's. A process without a
  CreationDate is skipped (never killed on a guess); a root without `StartedAt` yields nothing.
- **Orphans never fail the check** - they are closed, not waited on - but the line names them:
  ` - biten koşudan kalan süreç: 2 (tail, grep)`.
- **Line**: `Makine: bellek %<n>, TEMP <n>, C: <n> GB, E: <n> GB` (percent and drive GB rounded
  down, so a failing value never reads as the threshold; a missing drive reads `E: yok` and does not
  fail - the office PC has no E:), then the orphan part, then when not Ok
  ` - yeni iş başlatılmadı: ` + the reasons joined by ` / `. The memory reason names the largest
  process inside the cycle's own run trees: `Bellek %8 kaldı; en büyük süreç: pytest, 14 GB, test-slots`.
- **Growth**: `Compare-TeamTempGrowth -Today -Yesterday -Threshold` -> Grew only when the delta is
  above the alarm; no yesterday -> not Grew; line `geçici klasör büyüyor: +<Delta>, en sık önek <prefix>`.

## What the wiring card must call, where (`scripts/team/cycle.ps1`, `TeamQueue.ps1`, `TeamRun.ps1`)

1. `Start-TeamRun` records each run's root: `Pid`, `TaskId`, `StartedAt` (UTC, taken AFTER the
   process started), and `Finished` set true when the run ends.
2. At tick start: snapshot (`Get-TeamProcessSnapshot`), `Get-TeamOrphanTree` on the recorded roots,
   close exactly those pids (deepest first), never anything else.
3. Before `Select-TeamSeatFill`, every tick: the four readers -> `Get-TeamHostPulse` ->
   `Get-TeamPulseThresholds` (from `team/cycle-settings.json`) -> `Test-TeamHostPulse`. Not Ok ->
   zero new seats this tick; running runs continue untouched.
4. `Format-TeamPulseLine` into the cycle status document and the cycle report each tick; once a day
   `Compare-TeamTempGrowth` against yesterday's stored count, its line into the report when Grew.
5. Add the four threshold keys to `team/cycle-settings.json` only when the owner wants other values;
   the defaults hold without them.

## What the Office card shows

The latest `Makine: ...` line as one plain line on the Office page and in `office-cycle-status`; when
it carries ` - yeni iş başlatılmadı: ...` that is the reason the seats read 0/N.

## Not done here

Nothing is wired; no process is closed; `team/cycle-settings.json` is read in a test, never changed.
