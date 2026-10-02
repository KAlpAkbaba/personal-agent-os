## Şu an üzerinde çalışılan
- Task `execution-call-site-routines` (roadmap 2b, ADR-0213 row 1 / ADR-0220); area `services/api/app/routines/{dispatch,target}.py` + two test files + ADR text; machine: the owner's dev PC, worktree `worker-execution-call-site-routines`. Done; set to "Yok" at merge.

## Report
A routine's `browser_action` now goes to the cloud device the rule selects or fails `no_capable_device`, but three points depart from the card and need the lead's decision.

- **sha:** `6328a062c4d6c1edf30ec14014b556c23fd6d374`, pushed to `team/d20261002/worker-execution-call-site-routines`; worktree clean.
- **Files:** 5, all inside the area (`dispatch.py`, `target.py`, unit test, integration test, `team/plans/execution-call-site-routines-adr.md`).

**Departures from the card (details in the ADR text):**
1. **The check is not in `_select_for` for every caller.** `BrokerDeviceAction` is one object (`app/main.py:289`) shared with the wake sequence, operator, voice path, toast ladder and artifact open, and the cloud advertises `browser.media_play`. A capability-only check would send the wake alarm's music to the cloud. The rule is asked only on a new `BrokerDeviceAction.scheduled()` view, which only `ActionDispatcher._browser_action` takes.
2. **`media_playback` does not ask the rule.** It opens a visible `isolated` window; the cloud worker refuses every profile but `research`, so every such routine would fail for good. It is a one-line change if the owner wants it covered.
3. **Acceptance "deny-listed url -> refused `deny_listed_site`" is NOT MET.** ADR-0213's table says reading is not refused, `wiring.choose` forces `acting=False` for scheduled jobs, and `test_execution_wiring.py` (outside the area) asserts the opposite. I tested the refusal mapping with a stubbed decision and pinned the real answer (selected cloud); changing it means changing `app/execution/`.

**Tests added:**
- **Unit:** 27 in `test_routines_execution_target.py`, through the real `ActionDispatcher` and `BrokerDeviceAction`. RED before: 24 failed / 3 passed; the cloud-online case failed because the command went to MAIL. GREEN: 27 passed.
- **Integration (dev-stack PostgreSQL):** 2 passed; ledger rows read back through a fresh session, cleanup asserts 0 leftovers.
- **Neighbours:** 483 passed (routines, execution, device selection); 209 passed (alarms, operator, toast, artifact open, browser contracts). `ruff check` and `ruff format --check` clean.

**Mutations** (`dispatch.py` sha256 `b3b46096…604eb2` before and after, restored from a backup copy):

| Mutation | Result |
|---|---|
| m1: browser-capability check removed (rule asked for `app.launch`) | 3 unit RED |
| m2: refusal mapped back to `_select_for` | 12 unit RED, 1 integration RED |
| m3: dispatcher uses the plain port (the state before) | 8 unit RED, 2 integration RED |
| m4: shared port itself asks the rule | 2 unit RED |

My first m2 crashed with `UnboundLocalError` and was discarded as proof; the result above is from the rewritten mutation.

**Evidence class:** PROVEN_AUTOMATED (unit + dev-stack PostgreSQL). PROVEN_REAL is NOT_RUN: no real routine has fired against a real cloud worker. Full unit suite and the gate are NOT_RUN (only the 13 suites above); mypy is not installed in the venv.

**Open risks:**
- **No setting guards this.** From release on, every routine `browser_action` fails `no_capable_device` until the cloud worker is up. A setting like research's (ADR-0248) needs `config.py`, outside the area.
- An acting routine op (`click`, `fill`) is sent to the cloud as a read; only the worker's READ+NAVIGATE policy stops it.
- A `session_id` opened on a machine does not exist on the cloud worker.
- No routine field names a device today; the `forced_target_not_allowed` refusal is tested only through `scheduled(targets=…)`.
- The probe (`probe_routine_device`) is a pure twin of the rule call, because `wiring.choose` commits ledger rows; a 10-case test holds probe == run.
