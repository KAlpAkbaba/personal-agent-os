**Inspector report — execution-target-rule (pilot-01), sha `4a07170e`**

**Pass 1 — run it**
- Ran `pytest tests/unit/test_execution_target.py` from `services/api`: **47 passed in 0.33s**.
- `app.__file__` points into this worktree, so the tests ran against the worktree code and not the main checkout.
- `ruff check` is clean, and `ruff format --check` reports 4 files already formatted.
- Scope: 5 files, all inside the area (`app/execution/{__init__,rule,vocabulary}.py`, the test file, the ADR). Nothing shared was touched: not `HANDOFF`, `DECISIONS`, `BUILD_STATE` or `ledger/vocabulary.py`.
- The deny-list comes from `app.webtask.sites.denied`, which reads the protocol file. It is not copied.
- I ran three mutations of my own, each different from the worker's. `rule.py` was restored from a backup copy each time. Its sha256 began `b5a8d9ad90e4cb2b` before and after.

| Mutation | Result |
|---|---|
| Research chain reordered so owner_chrome comes before cloud | RED, 6 failed and 41 passed |
| Payment guard changed to `if False` | RED, 1 failed and 46 passed |
| Deny-list check made to ignore `acting`, so reading is refused too | RED, 1 failed and 46 passed |

**Pass 2 — break it (direct probes of `decide`)**
- Scheduled with cloud offline is refused with `no_target_available` and skips only cloud. It never reaches owner_chrome.
- Signed-in research with the spoken word `bulutta` is refused with `forced_target_not_allowed`. Cloud is never chosen.
- `BULUT` in capitals forces cloud. `bulutlu` does not match the cloud word; it counts as a device alias and, with no resolved device, gives `forced_target_unavailable`.
- A device alias that resolves to no device gives `forced_target_unavailable`. A named device on a compute job gives `forced_target_not_allowed`.
- A deny-listed bank site is refused when acting and allowed when only reading, and the same holds on the `turkiye.gov.tr` entry.
- A CAPTCHA on a scheduled job gives `ask_owner`. The previous RETURN was that a desktop job or a signed-in job with a wall returned `ask_owner` on cloud. It is fixed and tested (RED, then GREEN).
- My probe on `e-devlet.gov.tr` was allowed, but that host is not in the deny-list (the list has `turkiye.gov.tr`). This is my mistake, not a defect.
- Privacy: events carry only job kind, chain, target and reason codes. The URL and spoken word are not put in events, so nothing leaks to the ledger or logs.
- Contract: the three event strings are constants in `vocabulary.py` and are still unregistered in `ledger/vocabulary.py`. That is the lead's merge step.

**Findings, none blocking**
1. A wall is recorded as `execution.refused` with `ask_owner: true`. The consumer in PR 2 must read that flag and not treat it as a plain refusal.
2. The ADR table lacks the line "a wall on a job that cannot run in the cloud is ignored". Add it at merge.
3. `NOT_RUN`: the full `services/api` suite and `quality-gate.ps1`. The module is pure and not imported anywhere yet, so there is no wiring to break.
4. `NOT_RUN`: any real run. There is no broker, ledger or availability probe until PR 2.

**Evidence class:** PROVEN_AUTOMATED for the rule table, the chains, the events and the fix. Nothing here is higher.

APPROVE
