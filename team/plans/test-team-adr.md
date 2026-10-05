# ADR (test-team): a separate test team on staging - Test Proje Yöneticisi + four testers

Status: accepted (worker-2, cycle d20261005). The owner's design of 2026-10-03, binding.

## Context

"Test ekibi ve çalışan ekibi ayrı olsun; 4 test ekibi çalışanı ve 1 proje yöneticisi olsun ...
sonuçlar proje yöneticisine dönsün; bu sonuçları testin proje yöneticisi yazılımın proje
yöneticisine iletsin ... kopma noktası neresi bulunsun; bunu kendi proje yöneticisine
bildirsin, o da Danışman'a bildirsin." Staging (scripts/staging/, 127.0.0.1:28000/28001) exists.

## Decision

1. **Own seats, own cap.** `test-lead` and `tester-1..4` (roles in `scripts/testteam/roles/`,
   installed as `.claude/agents/test-lead.md` / `tester.md`). `team/cycle-settings.json` gets
   `test_parallel` (0-16, default 4; 0 = off) and `test_memory_floor_gb` (1-64, default 8);
   `max_parallel` and the software seats never read them. `Get-TeamTestCap`: under the floor
   the round starts nothing; while the gate holds a heavy test slot (`Test-TeamGateHoldsHeavy`
   over TeamTestSlots entries) one tester at a time; each reason is a Turkish sentence printed
   and posted by test-lead.
2. **Staging only, refused before the first byte.** `Test-TestTeamStagingUrl`: http, host
   127.0.0.1/localhost, port 28000/28001, no user part; everything else (dev :8000/:3000, https,
   the tailnet, 127.0.0.2, `@` tricks) is refused. `run-scenario.ps1` judges every step, the
   ladder and the cleanup before it sends anything (exit 2). The web step refuses the same way.
3. **The round** (`scripts/testteam/test-round.ps1`): a plan (one `claude -p` run of test-lead,
   or `-PlanPath`) -> test cards (`team/testteam/<round>/cards.json`, planned/running/passed/
   failed/broke) dealt to tester-1..4 in turn, one scenario family a job -> each tester is one
   `claude -p` run of tester.md -> results to the round only -> failures deduplicated (one per
   scenario+step+actual across testers; within one result, steps failing with the same actual
   are ONE failure naming the others - found by the first real round, staging a release behind
   answered 404 to five watch steps) -> each a `proposed` card (steps/expected/actual/scenario/
   screenshot/staging sha; stable id `test-fail-<family>-<hash>`, never opened twice). File
   queue or the Cloud Core's queue through the feeder's create-only `Save-TeamFeedCreates`.
   `-Retest` re-runs a failed card whose forwarded task is merged/released/done: pass closes,
   fail reopens (+1). The model policy (`team/models.json`: test-lead on lead's model, testers
   on worker's) and the account pool (the inherited CLAUDE_CONFIG_DIR) apply.
4. **Breaking point.** A scenario's `breaking` ladder sends `load` requests at once, x factor up
   to max (<= 512), stops at the first load with an error or a p95 over `max_p95_ms`. A ladder
   of a scenario whose steps already failed is "ölçüm geçersiz", never the headline.
   `kopma-noktasi.md` + one <= 280-char board note beginning "Danışman'a" - never the owner.
5. **Beside the cycle.** `cycle.ps1 -TestTeam` starts one round in its own hidden process
   (`t-<cycle>`, logs under the cycle's report folder) and does not wait for it; no software seat.
6. **Ofis 'Test odası'** (`officeTestRoom.tsx`): five seats, amber testers / teal test lead (CSS
   variables over the office's figures), the office's mood rules, current job and last breaking
   point - read from the board notes test-round posts ("iş: ...", "sonuç: <state> - <job> - kopma: ...").
7. **Voice/web.** `apps/web/tests/e2e/owner-scenarios/open_shell.py` on services/browser's
   Playwright (no new dependency): staging session injected, Chrome fake media
   (`--use-file-for-fake-audio-capture=<wav>` when a step names a wav), a screenshot.

## Consequences / open

- `.claude/agents/test-lead.md` and `tester.md` are byte copies of `scripts/testteam/roles/*.md`
  (installed by the second worker run; a test asserts the copies are byte-identical, and
  test-round still falls back to the roles folder when the copy is missing).
- The board refuses the seats `test-lead` / `tester-N` (`services/api/app/team/board.py`
  SEAT_PATTERN) and has no Danışman seat: until widened the notes are refused (UYARI, the round
  goes on) and the Test odası shows five waiting seats. `OfficeView.tsx` must mount `<TestRoom>`.
- First real round (2026-10-05, staging 6a21294c): health broke at 256 concurrent (p95 8.6-9.2 s
  > 5 s); misheard held to 128; watch routes 404 - staging was deployed 2026-10-04 09:15, before
  the watch engine release: staging is not redeployed after a release.
- Second run (2026-10-05 16:14 UTC, same staging 6a21294c, run-scenario.ps1 by hand): health held
  to 128 concurrent (p95 4526 ms) and broke at 256 (p95 10051 ms > 5000 ms, 0 errors); the planted
  scenario failed as planted (DELETE /v1/watches/<zero id>: expected 200, actual 404); :8000,
  https :28001 and `x@127.0.0.1:28001` refused with exit 2 before any request. The retest closes
  the planted card only after staging carries the fix: a release to main must also redeploy staging.
