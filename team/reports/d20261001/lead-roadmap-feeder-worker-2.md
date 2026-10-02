## Şu an üzerinde çalışılan
`lead-roadmap-feeder` (cycle d20261001, second pass after the inspector's RETURN) — area `scripts/team/feed.ps1`, `scripts/lib/TeamFeed.ps1`, `scripts/tests/team-feed.tests.ps1`, `team/plans/lead-roadmap-feeder-adr.md` — machine: the owner's PC, worktree `worker-lead-roadmap-feeder`.

## Result
All four return points are addressed, committed and pushed.
- **sha:** `34f95be98b5523c0f29110e1c12aa91d3f147bca` on `team/d20261001/worker-lead-roadmap-feeder`; the remote ref matches and the worktree is clean.
- **Files:** 3 changed, all inside the area (`feed.ps1`, `team-feed.tests.ps1`, the ADR text); `TeamFeed.ps1` is untouched.

## The four points
1. **No run, no report.** With the stop flag or a held lock, `feed.ps1` now prints one line and neither writes nor POSTs `feed-<date>.md`. The lock exit code is still 3.
   - RED before the fix: 4 cases failed (file lock, file stop flag, API lock, new API stop flag); the API cases showed `POST /v1/team/queue/reports 200`.
   - GREEN after the fix.
   - The same change is applied to the API acquire race, which has no test because the fake API cannot lose that race.
2. **A run that fails after writing a valid feed file queues nothing.** Two new cases: the fake writes two valid cards and then exits 1, or hangs and is killed at `-RunMinutes 0.3`. Both assert the file on disk is valid and the queue is unchanged.
3. **Uncommitted `docs/ROADMAP.md`.** New case: no idea row is asked for, Edit is not granted, HEAD is unchanged, the file is byte-equal to the edit as found and still ` M`, and the card is still queued.
4. **Detached HEAD.** New case: no idea row, no Edit, HEAD unchanged, roadmap unchanged, and the card is still queued.

## Mutation proof
`feed.ps1` sha256 was `1eee7c06…63df92` before the mutations and after each restore; restores were from a backup copy.

| Mutation | Result |
|---|---|
| `$done.Ok` removed from `$trusted` | both point-2 cases RED |
| `$dirty` line removed | point-3 case RED |
| `$branch -eq "HEAD"` removed | point-4 case RED |
| `Save-FeedReport` put back on the lock and flag paths | the 4 point-1 cases RED |

## Runs on the restored tree
- `team-feed.tests.ps1`: 61 passed, 0 failed
- `script-syntax`: 142 checked, 0 failed
- `installer-strictmode`: 24 passed, 0 failed
- `provision`: 13 passed, 0 failed

## Evidence classes
- **PROVEN_AUTOMATED:** points 1–4 and the four mutations above.
- **NOT_RUN this round:**
  - the card's two mutations (area overlap, `roadmap_row`) — proven at `ae5f5bcd`, and `TeamFeed.ps1` is unchanged since;
  - `team-cycle.tests.ps1`;
  - the full `quality-gate.ps1`;
  - a real PostgreSQL / Cloud Core re-run of point 1 — only the fake API was used;
  - a real `claude -p` lead run.

## ADR
`team/plans/lead-roadmap-feeder-adr.md` has a new item 9 ("No run, no report"), and item 8's wording is corrected.

## Open risks for the lead
- A feed run that did start still posts `feed-<date>.md`, so it becomes the newest report in the Onay Merkezi until the next cycle report. The fix covers only the nothing-started paths the return named; the rest needs the approvals route to pick by name, which is outside this area.
- The deadline case costs about 18 s of suite time and needs the fake to start within that; a slower start fails the case rather than passing it empty.
- The inspector's non-blocking items are unchanged:
  - API writes are not atomic;
  - the `needs_owner` proposal file is local only;
  - no throttle on empty answers;
  - writes to ignored paths are not seen;
  - the wiring must not treat exit 3 as a reason to skip the cycle.
- Wiring is still the lead's at merge: the scheduled-task call, plus the suite in `quality-gate.ps1` and `ci.yml`.
