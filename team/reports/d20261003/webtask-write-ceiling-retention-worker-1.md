**Şu an üzerinde çalışılan:** webtask-write-ceiling-retention (cycle d20261003). Area: the 14 files on the card. Machine: owner's dev PC, worktree `worker-webtask-write-ceiling-retention`.

Both parts are done and pushed. All four mutations went red; everything passes except one mail test that failed once in the full run and passed when rerun (below).

- **sha:** `4cf0d77636073f5d6b4273926560c1b9de7797ec`, pushed. Worktree clean.
- **Files:** 14, all inside the area (9 modified, 5 new). Nothing else touched.
- **Contract:** v1.8. `fill`, `select_option` and `set_checked` now take `risk_ceiling`. A write above the ceiling is refused before anything is written. No new operation name and no new `contracts` key.
- **Retention:** a task that ends (done, failed, cancelled or abandoned) stores `observation: null`. `scrub_observations` clears older ended rows and runs from `start_task_db`. A task waiting for you keeps the address, title and elements but not the page text; I did this optional part because no reader needs the text after a park.

**Red first, against the unchanged code (PROVEN_AUTOMATED):**
- API, 3 new files: 27 failed, 22 passed. The passing ones were the click/navigate/scroll and "running task keeps its observation" checks, which should pass on old code too.
- Browser unit: import error, `classify_write` did not exist.
- Browser e2e: 11 failed, 1 passed. Two of those 11 failed because of a mistake in my test: it compared the whole result, but every result also carries a `lifecycle` block. I fixed the comparison; the "no ceiling = v1.7" case now passes on old and new code alike, as it should.
- Then green: API webtask files 432 passed; browser unit 25 passed; e2e 12 passed (headless, run twice).

**Window check:** I sampled visible Chrome windows every second during the e2e runs. The most I saw was 1, the same as before the run started, so the tests opened no window. I used a small sampler script I wrote, not the original window-monitor tool.

**Mutations (PROVEN_AUTOMATED), each restored from a backup copy with matching sha256, never `git checkout`:**
1. Ceiling sent for `fill` only: device-port tests red for select and check (8 failed); `3fac842a…` same after restore.
2. Worker checks after writing: e2e "page unchanged" red (7 failed); `cf25bdb5…` same.
3. Terminal clear removed from `_write`: retention tests red (4 failed), and the PostgreSQL test red too; `3d410a8b…` same.
4. Scrub also clears running rows: scrub test red; `3d410a8b…` same.

**Full suites (PROVEN_AUTOMATED):**
- Browser unit: 1053 passed.
- API unit, in 7 chunks: 14 775 passed, 5 skipped, 1 xfailed, 1 failed.
- The failure is `test_mail_service.py::test_two_concurrent_confirmations_race…`, a TimeoutError while other workers' pytest runs were loading the machine. It passed when rerun alone, I did not touch the file, and it has nothing to do with web tasks. It is still a flaky test that someone should look at.
- `test_postgres_coverage_ratchet.py` passes, unedited.
- API integration on PostgreSQL: 158 passed, 11 xfailed; the new file passes 2/2 and reads the row back with a JSON expression through a fresh session.

**PostgreSQL detail:** the shared dev database is already at migration `0065_misheard_utterances`, which this branch does not have, so `alembic upgrade head` failed there. I ran the integration suite on a scratch database in the same dev PostgreSQL container, migrated to this branch's head (`0064`), then dropped it.

**Edits to existing tests:**
- `test_browser_contract_v17.py`, one line: the version assertion now checks that the v1.7 entry is in the change log, because the document says v1.8.
- `test_webtask_device_port.py`: the fill/select/check rows now expect `risk_ceiling`, and the fake device answers writes with `risk_class`. New tests were added below the existing ones.
- `test_webtask_gate.py` and `test_webtask_acceptance.py` pass without edits.

**Decision outside the card (in the ADR):** an agent from before v1.8 ignores the field and performs the write. The Cloud Core then raises `capability_missing`, but `loop.py` (not in my area) counts that as a failed step and may plan another write. So `device_port` remembers the device and sends it no further writes until the Cloud Core restarts; clicks still go, since their ceiling is enforced from v1.7. A restart costs at most one more write that only the Cloud Core's own gate checked.

**PROVEN_PROXY:** "the device refuses" is shown on a test page in headless Chromium, not on the owner's Chrome.

**NOT_RUN:**
- A real Windows agent serving v1.8: installed agents keep their old worker until an agent update, which is not part of this card.
- Any real web task: nothing calls `start_task_db` before PR-D.

**Open:**
- A timed scrub from the app's lifespan (needs `app/main.py`) is the named follow-up; until then old rows are cleared only when the next task starts.
- An element the page-element collector does not list is treated as unnamed and inside a form, so the stricter rule applies.

**ADR text:** `team/plans/webtask-write-ceiling-retention-adr.md`. It lists every reader of the stored observation; none needs it after a task ends. Nothing is READY_FOR_OWNER: no migration, no setting, no compose change.
