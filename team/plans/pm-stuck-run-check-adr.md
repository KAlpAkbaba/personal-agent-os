# ADR (taslak) - pm-stuck-run-check: a run's liveness is measured, and a stuck run goes to the Proje Yöneticisi

Status: proposed (the lead numbers it)
Date: 2026-10-05

## Context

The owner, 2026-10-04, looking at five tired faces on the Ofis: "Proje yöneticisine söyle arada
gerçekten işte çalışıp çalışmadıklarını da kontrol etsin, iş takılmış olmasın." A tired face only
says "long". The real case of the same day: the gate-faster worker's unit-test python sat at
140 MB and about one CPU second per 30 s for over two hours, holding the heavy test slot, while
the worker's own temp folder was still written. On 2026-10-05 the owner also saw stopped tasks that
only wait for another task ("alan çakışması: X; o iş bitince") or for the Danışman shown as angry.

## Decision

1. Liveness is MEASURED from cheap, local signs (`scripts/lib/TeamLiveness.ps1`), never guessed: the
   newest write in the run's temp folder and worktree (node_modules, .venv and .git left out,
   links not followed), its output growing, and the CPU its process tree used since the last look,
   counted only above 5% of one core over the time between looks (a hung python's trickle is no
   life). The clock and the process table are parameters.
2. A run with no sign for `run_idle_minutes` (team/cycle-settings.json, 1..1440, default 30) is
   "takılmış olabilir". So is a CHILD: a tool process under a shell of the run's tree whose own
   subtree showed no CPU for as long; only the topmost such process is named.
3. The ladder (`Get-TeamStuckAction`): at the bound the run is handed to the Proje Yöneticisi's
   duty once (the duty card's "Takılmış olabilecek koşular" section; answer `stuck: [{run, action:
   wait|restart|escalate, reason}]`, checked by `Test-TeamStuckDecisions`); at 90 minutes it is
   restarted once without asking, the second time escalated to the Danışman. A run that showed
   life inside the window is never touched. A stuck child climbs the SAME ladder: the run is as
   idle as its most idle stuck child (`Get-TeamStuckAction -StuckChildren`), because in the
   gate-faster case the run itself kept writing.
4. `Restart-TeamRun` stops exactly that run's process tree and starts the same card again in the
   same worktree; git is not touched, so the commits stay. With `-SlotStore` it first reads which
   running test-slot tickets are held by a process of THAT run's tree (holder_pid), and gives them
   back right after the stop (logged `exit=released`, so the estimates ignore them) - not when the
   next ask happens to notice the holder is gone. A sibling run's ticket is never touched.
5. The status carries per run `last_activity_at`, `idle_minutes`, `stuck_children`, and the
   cycle's `run_idle_minutes`. The Ofis answer (`office.py`) counts `idle_minutes` to its own clock
   and sets `stuck` on the run and the seat; a run without the fields (an older cycle) or with a
   broken one carries none of it - it is never trusted as stuck.
6. The page (`officeLiveness.ts`) labels a stuck working seat "takılmış olabilir - N dk iz yok"
   (distinct from tired), and a stopped task whose reason ENDS in "(alan çakışması: X; o iş
   bitince)" or starts "Danışman'a iletildi: " calm: "sırada: X bitince" / "Danışman'da: <a few
   words>". Angry stays for a stop nobody decided yet.

## Consequences

- Wiring left to files outside this task's area (ALAN_ISTEGI): the status schema (`routes.py`
  `_Run` / `StatusRequest`), the cycle loop (`cycle.ps1`: look each tick, write the fields, run
  the ladder with `-StuckChildren`, pass `-StuckRuns`, apply the decisions, call `Restart-TeamRun -SlotStore`), the seat
  rendering (`officeMood.ts` / `OfficeView.tsx`, `officeApi.ts` types) and the setting itself.
- One look costs one Win32_Process query and a directory walk per run.
