## Şu an üzerinde çalışılan
`lead-roadmap-feeder` — alan: `scripts/team/feed.ps1`, `scripts/lib/TeamFeed.ps1`, `scripts/tests/team-feed.tests.ps1`, `team/plans/lead-roadmap-feeder-adr.md` — makine MAIL, dal `team/d20261001/worker-lead-roadmap-feeder`. İş bitti, commit edildi, push edildi, worktree temiz.

## Report
- **sha:** `ae5f5bcda491adc9b57aaa8eebc06c3c8b29fd38` (pushed; remote equals local).
- **Files changed:** 4, all new, all inside the area. `git diff main...HEAD` also lists lead files only because the worktree was opened from the lead's branch, not main.
- **Why it came back (priority, "çalışanlar durmasın"):** the feeder is built. New cards get `created_at` one second apart, so the lead's order is the queue's order; they land after what is already queued.

**Tests** — `scripts/tests/team-feed.tests.ps1`, 56 cases (pure decisions + sandbox repo, a fake model, the cycle's fake API).
- RED before: exit 1, `scripts\lib\TeamFeed.ps1 is not recognized` (the scripts did not exist).
- GREEN after: `56 passed, 0 failed`.
- Every acceptance line has a sandbox case: three runnable starts nothing; one runnable queues two approved cards with the roadmap reason; four whole-file refusals; `needs_owner` becomes an idea; unmet dependency does not count; one idea row and none on the second run; stray edits refused and nothing committed; other machine's lock; `stop.flag`; `-DryRun`.
- Beyond the card: usage-limit retry, API mode, and no commit on main.

**Mutation RED** (backup copy restored, sha256 before = after each time)
- Area-overlap check removed → 2 FAIL. The mutation drops the feeder's call to `Test-TeamSplit`, because the overlap check itself lives in `TeamQueue.ps1`, outside the area.
- `roadmap_row` check removed → 2 FAIL.
- Eleven more, each RED: stray-file check, roadmap-edit judge, dependencies, stop flag (both places), main guard, `needs_owner`, lock, threshold, idea already named, dry run, NEVER row.
- One mutation first stayed GREEN: the stop flag was checked twice with the same outcome. I removed the redundancy and added a case for the flag raised during a usage-limit wait.

**Evidence classes**
- PROVEN_AUTOMATED (fakes): everything above.
- Fast checks: `script-syntax` 142/0, `installer-strictmode` 24/0, `provision` 13/0, full `team-cycle` 121/0.
- Real data, read-only: the judge accepts every `roadmap_row` in today's `team/queue.json`.
- NOT_RUN: a real `claude -p` lead run; the real Cloud Core store; the scheduled-task wiring. PROVEN_REAL waits for the first night the queue runs low.

**For the lead at merge**
- Call `feed.ps1` before `cycle.ps1` in `scripts/team/register-nightly.ps1`, with the same `-QueueUrl`/`-QueueToken`.
- Add `team-feed.tests.ps1` to `scripts/quality-gate.ps1` and `.github/workflows/ci.yml`, beside `team-cycle.tests.ps1`.
- The ADR text is in `team/plans/lead-roadmap-feeder-adr.md`.

**Decisions in the ADR**
- A `roadmap_row` may carry one bracketed note after the exact row, as today's cards do.
- A `needs_owner` idea gets a proposal file written by the script; without one, approving it would break the queue for every cycle.
- A stray file is named in the report and left as found; only `docs/ROADMAP.md` is put back.

**Open risks**
1. An idea the cycle splits ends with reason "bölündü: …", so the "sahip onayladı" test never sees it. The fix is in `cycle.ps1` or the Onay Merkezi, outside this area.
2. On today's queue seed three ideas of 2026-09-30 match the idea rule (they became order items 2b/2c); the first real run will ask for their rows.
3. Idea rows are written only when the queue is low, as the card says.
4. No throttle: while the next roadmap item cannot be cut, a lead run starts every 30 minutes.
5. The lead's own edits in the main checkout during a feed run cause a refusal; the next run retries.
6. The feeder writes no live status, so the Ofis page shows nothing while it runs.
7. One mutation run took about 5 minutes instead of seconds while the machine was busy; it finished RED and I did not find the cause.
