# ADR (unnumbered; the lead numbers it): the test queue - ONAY / BEKLE before a heavy test run

Date: 2026-10-03. Card: `test-slots` (cycle d20261003). Roadmap row: Repairs and improves itself
(how it is built from here: the team cycle).

## The owner's sentence (2026-10-02)

"bir ajan test yapacakken diğerlerine bilgi versin ki başka bir ajan orada güncel olarak test
yapıyorsa birbirlerinin testlerini engellememiş olurlar; diğerleri onay alma ya da bekle komutuna
göre devam etsin; buna göre çalışan sayımızı da arttıralım."

Why: that day a full gate beside three inspectors and a worker took 3.5 h instead of 1.5 (api unit
suite 2 h 02 instead of 56 min; a worker's corpus run 62 min instead of 11); an inspection lost a
probe because the gate reset the shared dev database under it; a desktop test failed while
something else held the GPU. The machine (20 cores / 28 threads / 48 GB) is not the limit; the
heavy runs piling onto the same minutes and the same shared things is.

## Decision

A machine-local queue, `scripts/lib/TeamTestSlots.ps1` + `scripts/team/test-slot.ps1`, store in
`%LOCALAPPDATA%\PagentOS\test-slots` (outside every worktree, so all of them share it): one JSON
file per request, a lock file held FileShare.None for every decision (the OS drops it when its
process dies), every write a temp file + move/replace, every read tolerant of a half-written or
vanished file. `runs.log` gets one line per finished run (time, role, task, kinds, waited_s,
ran_s, exit) so the numbers below can be decided from data.

| kind | at once | what it covers | why this number |
|---|---|---|---|
| `database` | 1 | anything that migrates, resets or writes the dev stack's shared PostgreSQL: the api integration suite, a hand-run alembic, the gate's dev-up + alembic | one shared database: a second writer pulls the floor from under the first |
| `desktop` | 1 | the foreground window or the GPU: the operator lab, the Unity scene tests, a headed browser, the M1 E2E | one foreground, one GPU: two steal focus from each other and both fail |
| `heavy` | 3 | many cores for minutes: the whole api unit suite, the owner utterance corpus, the web build + suite, the dotnet build + test, the integration suite, a suite over two minutes in the gate of 2026-10-03 (team-cycle 820 s, cloud-release-bluegreen 481 s, the browser agent 213 s) | a ceiling, not a fit: four at once is where the 3.5-hour gate came from, but three do NOT run free - one whole api unit run grew to 14 GB on 2026-10-03 (3 x 14 = 42 of 48 GB; several at once crashed the PC), and two whole unit runs side by side measured +27 % and +30 % over their times alone. The roles no longer run the whole unit suite (the lead's gate does); whether 3 stays is the lead's call from `runs.log` |

A request may need several kinds (the integration suite: `database,heavy`) and gets all or none.
Kinds are counted in the table's order. A waiter that could be granted now has its kinds kept for
it (it asks again within the poll); one that cannot be granted keeps nothing - so
`database,heavy` waiting on the database never holds a heavy slot, and nothing deadlocks.

The conversation is the owner's two words. `ask` never blocks: `ONAY <ticket>` (exit 0) or
`BEKLE <position> | tutan: <role task "what" since> | önde: <who is ahead>` (exit 3); 2 is a bad
request, 4 a void/unknown ticket, 5 the queue itself failed; `run` returns the command's exit code.
First come, first served, by the FIRST ask. **The gate goes first:** a request with `-Role gate`
is placed ahead of every waiting request, never ahead of a run already going (granted or running).

Lifetimes: a ticket unused for 5 minutes is void; a place nobody asked about for 10 minutes is
dropped; a running slot belongs to the wrapper's pid AND its process start time - when that
process is gone (killed by an agent's 10-minute tool limit, a crash) or the pid now belongs to
another process, the slot is free at the next ask. `run` starts the command with no redirection
(both streams pass straight through, nothing can fill a pipe) and releases in a finally.

The gate (`scripts/quality-gate.ps1`) asks for each heavy step's kinds before the step and holds
them for that step only (unit: heavy; dotnet: heavy; dev-up, alembic: database; integration:
database,heavy; web build and web suite: heavy; M1 E2E: desktop). It WAITS - asks every 20 s,
prints the BEKLE line once a minute - and its wait is the `WaitSeconds` column of the summary.
`-NoTestSlots` runs it as before. A broken queue never fails the gate (the step runs, it says so).
`-StepList <file>` replaces the built-in steps with a file of `Invoke-Step` calls - the tests' way in.

The roles (`worker.md`, `inspector.md`, `lead.md`) carry one identical rule: before a command of
the three kinds ask; on BEKLE do something else or ask again, never run it anyway; run it through
`test-slot.ps1 run`; a run that got no slot in the time the agent had is NOT_RUN with the BEKLE
line quoted. A small targeted test (one file, seconds) needs no slot. The suite reads the three
files and runs their example commands through the real script with `-DryRun`.

## What this does NOT do

It is a rule the agents follow, not a cage. A run started without asking is not stopped, and
nothing is ever killed or refused for being long. The log and the inspector's report are how a
miss is seen.

A killed wrapper's command keeps running after its slot is freed (proven by the inspector's probe
on 84639f8a: the wrapper was killed, the next ask was ONAY, and the 25 s child wrote its marker
afterwards). The slot follows the wrapper by design and nothing is killed; so if a tool kills only
the wrapper and not its process tree, the queue can admit a new heavy run while the orphaned one
still loads the machine. The log shows it as `exit=holder-gone`.

The gate's wait has no upper bound. It asks every 20 s for as long as the holder is alive; a run
that is alive but hung holds its kinds, and the gate waits on it. In practice an agent's tool
timeout kills that wrapper and frees the slot; a hung holder outside any tool limit would stall the
gate until someone ends it (its BEKLE line, printed once a minute, names who holds it).

It separates the MACHINE's shared things, not a worktree's files: two runs of the same suite in
ONE worktree still collide (measured 2026-10-03: the worker's demonstration run beside the gate
in the same tree made `test_contract_falsification.py` fail in the gate - the test hides
`packages/protocol/realtime-session-contract.json` for a moment and the other run read it then).
`heavy` 3 lets two such runs start together; a second run in the same tree is the caller's mistake.

Not touched: `scripts/team/cycle.ps1` and the status document (showing "test sırası bekliyor" on
the Ofis page is the next card, after run-liveness-visible), services/api, the dev stack.

## Raising the worker seats afterwards

Measured, not guessed: with the queue merged, raise the worker seats by two, then read a full day
of `runs.log` (`waited_s` per role and kind) and the gate's `WaitSeconds` and total time. If the
gate's total stays within ~20 % of its time alone and runs rarely wait more than a few minutes,
keep the seats (or raise again); if `heavy` waits dominate, the number to change is the seats, not
the capacity - the capacity is the machine's.
