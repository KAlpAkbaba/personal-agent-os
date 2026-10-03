## Inspector report — `execution-call-site-routines` @ `6328a062`

The code does what the worker's report says and every number reproduced, but one of my mutations survived and one acceptance line on the card is unmet.

**Pass 1 — run (clean tree, 5 files, all inside the area)**
- **Unit:** `test_routines_execution_target.py` 27 passed.
- **Integration (dev-stack PostgreSQL, schema `0064`):** 2 passed; it writes all three event types (`selected`, `fallback`, `refused`) and I counted 0 leftover devices afterwards. PROVEN_AUTOMATED.
- **Neighbours:** 874 passed, 0 failed (routines, execution, device selection, alarms, operator wiring, toast, artifact open, browser contracts, voice selection).
- **Lint:** `ruff check` and `ruff format --check` clean.
- **NOT_RUN:** mypy (`python -m mypy` → `No module named mypy` in the venv), the full unit suite and `quality-gate.ps1`. The lead's gate covers these.
- **My mutations** (9, different from the worker's; restored from a backup copy; sha256 after is `b3b46096…` for `dispatch.py` and `13c1e78e…` for `target.py`; tree clean):

| Mutation | Result |
|---|---|
| A: browser check narrowed to `navigate` only | 1 RED |
| B: capability not handed to the rule | 1 RED |
| **C: `_spoken` returns the first word only** | **27 passed — SURVIVED** |
| D: `ledger_required=True` | 1 RED |
| E: targets dropped in `_run` | 3 RED |
| F: url dropped | 1 RED |
| G: probe not scheduled | 8 RED |
| I: probe ignores targets | 2 RED |
| J: a policy refusal still sent to the cloud | 1 RED |

- **Real run:** NOT_RUN. No cloud worker is enrolled against the dev broker, so presence and the command client are fakes in every test. PROVEN_PROXY at best for "the cloud worker executes it".

**Pass 2 — break it**
1. **Untested claim (mutation C).** `target._spoken`'s docstring says naming a machine beside the cloud word is still refused. No test uses a mixed list such as `("bulutta", "ev")`, for the run or the probe. Nothing in production calls `scheduled(targets=…)` today, so this is latent.
2. **Acceptance "deny-listed url → refused `deny_listed_site`" is NOT MET.** I confirmed the worker's reading: ADR-0213's table says reading is not refused, `wiring.choose` forces `acting=False` for scheduled jobs, and `test_execution_wiring.py:200` asserts cloud is selected. The branch pins the opposite of the card: a scheduled `navigate` to `turkiye.gov.tr` IS sent to the cloud. The worker cannot fix this inside the area; the lead must amend the card or open a task on `app/execution/`.
3. **Departure 1 (rule asked only on the `scheduled()` view) is correct.** `app/main.py:289` builds one port shared by the wake sequence, operator and voice path, and the cloud worker's real hello advertises `browser.media_play`. `test_the_shared_port_itself_does_not_ask_the_rule` holds it.
4. **Departure 2: `media_playback` still goes to a home/office machine from a scheduled routine.** It sends `browser.session_open` + `browser.navigate` through the plain port. This contradicts the card's "never let a scheduled browser action fall back" as written; it is defensible (audible, visible window), but it is an owner/lead decision, not the worker's.
5. **Production consequence, no setting to turn it off.** The cloud worker requires a `session_id` on every operation I checked (`worker.py:1131-1149`), and a session opened on a machine does not exist there. A routine `browser_action` that works today through a machine's session will be selected for the cloud and then fail on the worker, while the ledger row says `selected`. So the card's PROVEN_REAL criterion ("first ledger row target=cloud") does not prove the action ran. Before release the lead should count production routines whose action kind is `browser_action`.
6. **Host snapshot is older than the last release** (`collected_at` 2026-10-02T06:54Z; `f91ad1e3` went out 11:30 UTC). It is not binding here because the diff touches no script, infra or migration. It does list `pagentos-prod-cloud-browser` as running.
7. **Clean on the rest.** No url in the ledger detail or logs, no secrets or paths, no schema or contract drift (`selection_for` gains a keyword-only argument), idempotency key unchanged, rollback is a plain revert of one commit.

**Evidence classes:** rule call site and ledger rows — PROVEN_AUTOMATED (unit + PostgreSQL); execution on the cloud worker — NOT_RUN; mypy and full gate — NOT_RUN.

**For the lead (not the worker):** items 2, 4 and 5 need a decision recorded in the numbered ADR before merge.

RETURN (1: add a unit test for a machine word beside the cloud word, e.g. `targets=("bulutta","ev")`, refused `forced_target_not_allowed` on both run and probe — mutation C must go RED; 2: state in the ADR text that the cloud worker requires a `session_id`, so a `selected` ledger row is not proof the action ran)
