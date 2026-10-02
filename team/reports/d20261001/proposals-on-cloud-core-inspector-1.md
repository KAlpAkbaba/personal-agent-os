# Inspector report — `proposals-on-cloud-core` @ `db480b22`

**Verdict in short:** the build does what the card asks and holds on real PostgreSQL, but one input the contract says is a 422 is a 500 on PostgreSQL only, and the branch cannot go green inside its area.

## Pass 1 — run
- **Unit, team files:** 2 failed, 208 passed, reproducing the worker's numbers. The two failures are exactly the ones reported.
- **Related unit files** (`test_office01_wiring`, `test_pilot01_wiring`, `test_team_office`): 35 passed.
- **Real PostgreSQL, dev stack** (`pagentos-postgres`, dialect confirmed `postgresql`): 10 passed (5 proposals, 2 approvals, 3 existing `team_state`).
- **ruff check + format:** clean on the touched files.
- **Area:** 9 files changed against `team/nightly/lead`, all inside the area; no migration, no new table.
- **Merge:** `git merge-tree` against the current `team/nightly/lead` (`69730f8e`) is conflict-free.
- **My mutations** (restored from a backup copy, sha256 verified, tree clean afterwards):

| Mutation | Unit | PostgreSQL |
|---|---|---|
| Database branch of `decisions_open` removed | 7 RED | 2 RED |
| Key-width check dropped | 4 RED | 2 RED (Postgres itself: `value too long for type character varying(80)`) |
| File fallback allowed on the database store | 1 RED | not run |
| Database replace keeps the old text | 2 RED | 2 RED |
| Listing says `decisions_open` true always | 2 RED | not run |
| Route's own text limit dropped | survives | not run |

  The surviving mutation is equivalent: the store enforces the same limit.
- **Concurrent first puts on PostgreSQL:** 15 rounds of 8 threads on one name gave 1 row each round and 0 errors.
- **Cycle-side premise:** `Save-TeamQueueApi` writes only changed tasks, each with the `updated_at` it read. The owner-decides-while-running rule rests on this and it holds.
- **NOT_RUN:** full unit suite (~5400), full `quality-gate.ps1`, PowerShell suites (no ps1 touched), mypy (not installed in the venv).

## Pass 2 — break
1. **Defect, PostgreSQL only.** A `text` containing U+0000 posted to `/v1/team/queue/proposals` returns **500** on PostgreSQL (`UntranslatableCharacter: \u0000 cannot be converted to text`, JSONB). The store's check lets it through, so SQLite keeps it; the card says anything else is 422 and nothing written. This is the addendum-4 shape, and a UTF-16 file read as UTF-8 by the sibling `cycle.ps1` would hit it. `put_report` has the same hole (it predates this task; I measured 500 there too).
2. **Minor, FileStore on Windows.** `put_proposal("nul.md", …)` passes the name pattern and raises an uncaught `FileExistsError` from `os.replace`, which the route would turn into a 500. Reserved device names should be refused as `Invalid`.
3. **The two red tests cannot be fixed inside the area.** The worker's account is accurate, and refusing to hide the route from the contract test was right. This needs the lead: cherry-pick `1df514f0` or widen the area.
4. **For the lead at merge.** `voice/realtime_sessions/tools_team.py:36` on `team/nightly/lead` calls `approvals.list_pending(queue, root)` without the store. On the Cloud Core that falls back to the file store, so that path gets no proposal text.
5. **For the lead at merge.** `page.tsx:123,133` still locks its buttons on `cycle_running`. Until `approvals-detail-view` lands, the owner sees no change.
6. **Stale-write trail.** A decision refused as `stale_write` has already left its ledger event; the worker says so. I did not measure how often this happens.
7. **Listing cost.** The Ofis view also calls `list_pending`, so every poll now reads each pending idea's full text (up to 200 000 characters) and then cuts it to 20 000. Fine at today's sizes (8 real proposals, names of 38 characters or fewer, all matching the pattern).
8. No secrets, no machine paths in product code, no owner text in logs beyond the existing request log. Rollback is a code revert; `kind='proposal'` rows are inert to older code.

## Evidence classes
- Proposals put/read/replace, 80/81 boundary, listing through the store: **PROVEN_AUTOMATED** (unit on both stores, plus real PostgreSQL).
- Decision while the lock is held, and `stale_write`: **PROVEN_AUTOMATED** (unit, plus real PostgreSQL).
- Branch green as a whole: **NOT_RUN / RED** (2 failing by construction).
- Owner reads an idea's text with the home PC off: **READY_FOR_OWNER**.

`RETURN (1. refuse a text containing U+0000 in _check_proposal as Invalid -> 422, nothing written, with a unit test on both stores and a PostgreSQL integration test through the route, RED first; 2. refuse Windows reserved device names (nul/con/aux/prn/com1-9/lpt1-9 stems) in proposal_name_problems, or turn the FileStore's OSError into Invalid, with a test; 3. LEAD: add tests/unit/test_team_state.py and tests/unit/test_team_approvals.py to the area or cherry-pick 1df514f0 at integration - the branch is 2 RED until then; 4. LEAD at merge: pass the store in voice/realtime_sessions/tools_team.py's list_pending call)`
