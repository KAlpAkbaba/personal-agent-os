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

7. The cycle (`cycle.ps1`, `Watch-RunLiveness`) looks every 60 s at each run in flight: its temp
   folder and its worktree (never the shared checkout a lead or researcher runs in), its process
   tree. The fields go into the status (`routes.py` `_Run`, `StatusRequest.run_idle_minutes`, all
   optional). "duty" puts the run into the next duty run (a duty may now hold only stuck runs);
   the answer's `stuck` list is judged whole by `Test-TeamStuckDecisions` and applied: restart
   (`Restart-TeamRun -SlotStore`, the run's first deadline kept - a restart is not more time),
   wait (a line), escalate (a line "Danışman'a iletildi: takılmış olabilir: ..."). The duty run
   itself is measured but never handed to itself. Without `TeamLiveness.ps1` beside the cycle
   nothing is measured (the tests' sandboxes of other steps).
8. The page lists each seat with something to say under the scene (`OfficeView.tsx`,
   `livenessLines`). Whether a stop only waits is `officeMood.moodOf`'s decision ("waiting");
   `officeLiveness.ts` only takes its words.

## Consequences

- A Cloud Core older than this `routes.py` refuses the new fields (422): the cycle then writes the
  legacy status for the rest of that cycle (no model/limits on the Ofis) until the Core is released.
- One look costs one Win32_Process query and a directory walk per run, once a minute.
- The 5% CPU share is a choice, not a measurement on a real hung child.

## Addendum (return 3): a decision is checked against the run as it is NOW

- The PM decides on the card's minutes, but the run may write again while the duty runs. Before a
  `restart` is applied the cycle looks at that run once more (`Test-PoolRunRevived`): under the
  bound - its own minutes and its stuck children's - or, with no stuck child, active after the card
  was written, and the restart becomes a wait ("yeniden canlandı"). A handed run that showed life
  before the duty's card is written is taken off the card and climbs the ladder again.
- A seat's `idle_minutes` is the most of its runs' own minutes and their stuck children's, and the
  seat carries `stuck_children`: the Ofis says "takılmış olabilir - alt süreç python.exe 140 dk iz
  yok" for the 2026-10-04 case (the run wrote, its test python did not).
