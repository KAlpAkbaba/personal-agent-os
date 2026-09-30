**Worker report: onay-merkezi (pilot-01), return fix**

**Şu an üzerinde çalışılan** (for the lead's HANDOFF): task `onay-merkezi`, area `app/team`, `test_team_approvals.py`, the web `approvals` page and the ADR text. It ran on the home PC in this worktree. The lead writes `docs/HANDOFF.md`.

**sha:** `a2faeaa10e7d539eeb3760c155b97cc0e85de3ff` on branch `team/pilot-01/worker-onay-merkezi`. The push printed no error. The worktree is clean.

**Files changed (4, all inside the area):**
- `services/api/app/team/approvals.py`
- `services/api/tests/unit/test_team_approvals.py`
- `apps/web/app/core/approvals/page.tsx`
- `team/plans/onay-merkezi-adr.md`

**What changed**
- Onayla at `awaiting_release` no longer writes `approved`. It keeps the state at `awaiting_release` and writes three fields:
  - `release_approved` = true;
  - `release_approved_at` in the UTC `YYYY-MM-DDTHH:MM:SSZ` form;
  - `release_approved_by`, set from the channel (`shell` or `voice`).
- Onayla at `awaiting_owner` still writes `approved` and no release fields.
- Reddet at either gate still writes `stopped` plus the owner's `reason`, and no flag.
- The shell button reads "Yayını onayla" at the release gate and "Onayla" at the idea gate. It starts no release.
- The ADR text now records this as decided and drops the old "open decision" paragraph.

**Tests added (4 new, 1 fixed):**
- `test_a_release_approval_leaves_the_state_at_awaiting_release_and_raises_the_flag` checks the state, the flag, the timestamp format and the `shell` channel.
- `test_a_voice_release_approval_records_voice_as_the_channel`.
- `test_an_idea_approval_writes_approved_and_no_release_fields` is the near miss for the flag test.
- `test_rejecting_a_release_stops_the_task_and_raises_no_flag`.
- I changed the existing voice test `test_a_voice_approval_accepts_the_gate_spelled_with_or_without_the_dotless_i`, which had asserted the old `approved` state.
- RED: 3 failed and 35 passed before the implementation. GREEN: `test_team_approvals.py` and `test_team_queue_schema.py` together give 76 passed.
- ruff check is clean and ruff format reports the 4 files already formatted.

**Mutation (RED, then restored):** the mutant writes `approved` at the release gate and sets the flag to false. Result: 2 failed and 36 passed. The two failures are the flag test and the voice dotless-i test. I restored from a backup copy, not `git checkout --`.

| `app/team/approvals.py` | sha256 |
|---|---|
| before the mutation | `70198218c277d3e217565fe933f2c321cc241072b2bd4a85e3364cb3d24630a5` |
| after the restore | `70198218c277d3e217565fe933f2c321cc241072b2bd4a85e3364cb3d24630a5` |

After the restore the file passed 38 tests.

**Evidence classes**
- PROVEN_AUTOMATED: the API rules and the router, through the real `create_app` with the real owner-session dependency.
- NOT_RUN: `tsc` and oxlint on the web page. The one-line label change was not type-checked.
- READY_FOR_OWNER: the shell page and the voice path. Nothing was run live.

**Blocking for the lead (outside my area):**
1. `team/queue.schema.json` has `additionalProperties: false` and does not know `release_approved`, `release_approved_at` or `release_approved_by`. Add them, or the queue will fail validation after the first release approval. The card said the fields are in the schema, but they are not there in this tree (grep found nothing). `test_team_queue_schema.py` only checks the current queue, so it stays green.
2. `cycle.ps1` has no reader for the flag yet. Until it has one, a merged task that is release-approved just stays at `awaiting_release`. This is safe, but it does nothing.
3. Still to wire at merge, as in the inspector's report: `team` subsystem and the two event types in `app/ledger/vocabulary.py`, `include_router` in `app/main.py`, the `/core/approvals` navigation link, and the ADR number and DECISIONS entry.

**Open risks**
- The ledger event still carries `to_state`, so a release approval logs `awaiting_release -> awaiting_release`. The flag is in `detail_json` only through `decision`, not as its own field. That is accurate but easy to misread.
- `owner_sentence` is never written by this API. The lead writes it for its own sentence approvals.
- The inspector's earlier findings on the partial write, the lock race, the missing VM checkout and the JSON formatting still stand.
