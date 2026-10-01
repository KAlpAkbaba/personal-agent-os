# ADR (no number yet): the cycle writes a live status and honours a stop flag

Context: the owner's 'Ofis' page must show which agents run NOW; the queue only changes when a run
ends. The owner also asked to stop a running cycle "at a safe point" and there was no way.

Decision:
- cycle.ps1 writes a live status (contract in the task card: cycle_id, machine, pid, started_at,
  runs[], estimated_usd, usage_limit{state,resets_at}, updated_at) to team/status.json (file mode) or
  PUT /v1/team/queue/status (API mode, Save-TeamStatusApi). Written after the lock, after each run
  starts, after each run completes, on usage-limit wait/stop/lift, and at the end (finally: runs empty).
- A heartbeat refreshes it every 120 s while a run or a limit wait lasts (Wait-TeamRun -OnTick), so
  the reader's 10-minute staleness rule does not hide a long run.
- A failed status write is one risk line, never a stop.
- team/stop.flag (checked in both modes, in the team root): no NEW run starts (researcher, split, batch),
  runs in flight finish and are recorded, an approved inspection is merged, the limit wait ends; the
  stop line is written, the flag removed, the lock released, exit 0. The task stays where it was
  (a finished worker is 'inspecting') and the next cycle with the same -CycleId continues.
Consequence: a run killed by a crash leaves a status that goes stale in 10 min; the finally block clears it
on every normal and exceptional exit.
