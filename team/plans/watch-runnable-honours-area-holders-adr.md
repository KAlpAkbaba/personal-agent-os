# ADR draft: the night watch counts runnable cards by the cycle's own rule (watch-runnable-honours-area-holders)

Status: proposed (cycle d20261007, worker-3). The lead numbers it.

## Context

2026-10-07 01:02 the Danisman's watch reported "bos calisan koltugu var (2/4) ama baslayabilir
3 kart bekliyor (30+ dk)" every 15 minutes and started a Danisman run for it each time. The
queue status at 2026-10-06T22:02:34Z showed all three cards sharing an area with a card in
work (in_progress / inspecting); the cycle held them correctly (Section 4,
`Get-TeamAreaHolders` / `Select-TeamSeatFill`). The watch's "runnable" filter
(`scripts/lib/TeamWatch.ps1`) looked only at `state = approved` and unmet dependencies.

## Decision

`Invoke-TeamWatchCheck` calls `Get-TeamAreaHolders` (TeamQueue.ps1, the function the cycle
uses) for every approved card with no unmet dependency. A card with holders is not runnable:
it is said on its own line `alan bekliyor: <card> <- <holder>, ...` and is left out of the
idle-seat count and the idle streak. A card with a free area still raises `idle-seats`.

## Why call, not re-implement

- One rule, one place: the watch is meant to catch the cycle failing to fill a seat it
  COULD fill. If the watch carried its own copy of "who may start", the two would drift
  (states that hold an area, the overlap test on globs and trailing slashes) and every drift
  is either a false alarm (this incident) or a missed one.
- A change to Section 4 (e.g. a new in-work state) reaches the watch without anyone
  remembering it does.
- TeamWatch.ps1 already depends on TeamQueue.ps1 (Get-TeamTasks, Get-TeamUnmetDependencies,
  the escalated prefix), so no new coupling.

## Evidence

`scripts/tests/team-watch.tests.ps1`, case "an approved card whose area a card in work holds
is waiting, not runnable": a spy around `Get-TeamAreaHolders` proves the call by behaviour;
two looks with held cards give no `idle-seats`, `idle_streak` 0 and the `alan bekliyor` lines;
a free card beside them still gives `idle-seats`. Removing the filter turns it RED.

## Not done

The temporary copy `%USERPROFILE%\.pagentos-team\danisman-watch.ps1` is outside the
repository; it is retired when the scheduled task runs `scripts/team/watch.ps1`
(danisman-watch-in-repo).
