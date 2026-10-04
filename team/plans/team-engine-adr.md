# ADR (team-engine): the team engine - one continuous loop, the return goes to its worker, the integration branch follows its base

Status: accepted (worker, 2026-10-04); the lead numbers it (ADR-0214 addendum).
Owner rules of 2026-10-03: "Çalışan işini bitirip denetleyiciye verdiğinde ... aynı çalışana işi
geri gönderecek"; "Döngüyü kaldırabiliriz. Direkt bir sirkülasyon şeklinde getirebiliriz."

## Decisions

1. **Seat affinity lives in the loop's own file, not in the task record.** `team/queue.schema.json`
   is closed (`additionalProperties: false`) and the Cloud Core refuses an unknown task field, so
   `owner_seat` / `session_id` / `seat_gone_at` are kept in `team/logs/seats.json` on the loop's
   machine (like `team/limits.json`). `Get-TeamTaskSeat` reads the task's own `owner_seat` /
   `session_id` first, so once the schema and the store gain them nothing else changes. Records
   without a seat behave as before (any free seat, fresh run).
2. **Named worker seats** `worker-1..N` (`Select-TeamSeatFill` sets `Seat`). A returned task whose
   claim lives (`Get-TeamSeatClaim`: present, or gone < 60 min) is offered ONLY its owner seat,
   ahead of the queue; while it waits, that seat is reserved and takes no new task. The owner
   seat may take a new task while its work is inspected; the return then is its next run.
3. **Resume.** Worker runs keep their session (no `--no-session-persistence`); the session id is
   read from the result document and stored. The fixing run gets `--resume <id>` (id checked
   against `^[A-Za-z0-9-]{1,80}$`) plus the usual card (reason + the inspector's report), and keeps
   every guard of a fresh run (model, budget, no background tasks, area). Only a LOST session
   (`Test-TeamResumeLost`: the tool says the session is not found / expired / cannot be resumed)
   is not a failure of the task and spends no try: the same seat starts a fresh run once, told
   so in its card, and the report says "<seat> önceki oturumunu sürdüremedi". A resumed run that
   times out, hits its budget, is limited or crashes is an ordinary failure: counted, its
   report kept, no fresh run in its place (the inspector, 2026-10-04).
4. **The loop.** `-MaxHours` defaults to 0; `-Continuous` keeps the process alive when idle. The
   four jobs of the cycle boundary are kept without it: (a) handover - `team/handover.flag` or a
   change of the scripts' SHA-256 -> the loop writes `team/logs/loop-handover.json`, exits without
   killing its runs and starts its successor (same arguments), which waits for it, takes the lock
   and adopts the runs by pid + files; for that every run is started detached
   (`Start-TeamDetachedRun`: cmd.exe, prompt from a file, output/err/exit code into files under
   the day's `running/` folder); (b) local midnight switches day id, report folder, report and
   integration branch with `-DailyId`; (c) the 30-minute tick is a watchdog (`tick.ps1 -Watchdog`):
   live loop -> feeder detached, exit; dead loop (no file / pid gone / heartbeat > 10 min) ->
   feeder, then the loop started detached; (d) the researcher's hours are a timer in the loop.
   The loop renews its own lock every 30 minutes (a lock older than 6 h is anybody's).
5. **Status.** The live status carries `runs[].seat`, `loop_id`, `loop_started_at` and `returns`
   (`task`, `owner_seat`). The Cloud Core's status model is strict, so a 422 drops these fields
   first (silently, nothing the Ofis shows today is lost), then the model fields (as before).
6. **The integration branch follows its base.** `Merge-TeamBranch -Follow`: if the base has
   commits `integrate/<id>` lacks, they are merged first (`merge: <base> <sha> into
   integrate/<id>`, --no-ff). A conflict there aborts (the integrate worktree is left clean, no
   merge in progress), the task stays `inspecting` with the
   inspector's APPROVE as its last report (`Test-TeamAwaitingMerge`; the protocol has no state of
   its own for "approved, waiting to merge"), the lead gets one line "entegrasyon dalı <base>
   dalını alamadı: <files>", and every refill tries the merge again - never a second inspection.
   A conflict that remains with the base in is the task's: "entegrasyon dalında çakışma", as before.
7. **The loop's integrate worktree is the loop's.** Proposed text for `.claude/agents/lead.md`
   (lead-protected, not edited here): "integrate/<id>'nin çalışma kopyası döngünündür; değişikliği
   başka bir çalışma kopyasında hazırla, sonra dalı ileri sar (fast-forward)."

## Not done here (outside the area)

- The Ofis: `services/api/app/team/routes.py` (`_Run.seat`, status `loop_id`/`loop_started_at`/
  `returns`), `services/api/app/team/office.py` (the '!' on `owner_seat`; "geri dönen iş sırada:
  <title>" when that seat is busy) and the web top bar "Akış: kesintisiz, <n> saattir".
- `owner_seat` / `session_id` as fields of the store's task record (the api's task model): until
  that card lands they live in `team/logs/seats.json` on the loop's machine (decision 1).
- A daily standup does not exist in the code yet; when it comes it is a timer like the researcher's.

## Consequences

- A broken successor start leaves the handover file: the watchdog's next loop adopts the runs.
- Detached runs survive a crashed loop too; a loop that dies without a handover leaves them
  running and they are not adopted (their tasks are taken up again as before) - risk noted.
