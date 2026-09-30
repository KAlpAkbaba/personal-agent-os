**Inspector report: execution-target-rule (pilot-01), sha 02f72cdb**

**Pass 1 — I ran it**
- **Tests:** `test_execution_target.py` gave 46 passed in 0.28s, run from `services/api` with the main checkout's venv. `app.__file__` resolves to the worktree, so the worktree code was tested.
- **Lint:** ruff check and ruff format --check are clean on `app/execution` and the test file.
- **Scope:** the diff has 5 files, all inside the area. I didn't touch HANDOFF, DECISIONS, BUILD_STATE, `app/ledger/vocabulary.py` or the contracts. `app/execution` has no hard-coded paths or secrets.
- **My mutations** (different from the worker's; each restored from a backup copy):

| # | Mutation | Result |
|---|---|---|
| 1 | Payment guard removed | 1 failed |
| 2 | Deny-list refused reading as well as acting | 1 failed |
| 3 | Wall (`ask_owner`) branch disabled | 3 failed |
| 4 | Browser_task chain swapped to owner_chrome, cloud, device | 6 failed |
| 5 | Cloud added to the desktop chain | 4 failed |
| 6 | Device added to the compute chain | 3 failed |

- **Restore:** the `rule.py` sha256 is identical before and after (`8de7766b…040f7`), and the tree is clean.
- **Not run:**
  - The full `services/api` suite and `quality-gate.ps1`. This is a pure module that is not wired in yet.
  - A real run. There is nothing to run yet, since the broker, ledger and availability probes are PR 2.

**Pass 2 — I tried to break it**
- **Verified OK:**
  - `Bulutta` and `BULUT` force cloud (case-insensitive).
  - `bulutbank` and `bulutlu` are aliases, not cloud.
  - An alias that resolves to no device gives `forced_target_unavailable` with `device_unresolved`.
  - Reading a deny-listed host is allowed and acting on it is refused.
  - Research falls back from cloud to device with a reason for each skipped target.
  - A blocker value other than a wall (`rate_limit`) is ignored.
- **Finding (RETURN, minor):** the wall check runs before the chain check, and it hard-codes `Target.CLOUD`.
  - `DESKTOP + cloud_blocker="captcha"` returns `ask_owner`, target `cloud`, chain `(cloud,)`.
  - Signed-in research with a blocker does the same.
  - So the output names cloud for a job that must never be in the cloud, which contradicts the desktop and signed-in rules.
  - No test covers it. The fix is to take the wall branch only when `Target.CLOUD in chain`, and to add a near-miss test (desktop or signed-in with a blocker is not `ask_owner` on cloud). The mutation for the fix is to drop the chain condition and see that test go RED.
- **Notes, not blocking:**
  - RED proof order: `rule.py` was written before the test file. The worker admitted it, and the after-the-fact ModuleNotFoundError proof is weaker than a real test-first RED.
  - A wall is recorded as `execution.refused` with `ask_owner: true`, and `target` is not carried in that event. That is acceptable, but the lead should know the consumer must read the flag.
  - Whether signed-in research falls back from owner_chrome to device is a design choice. It is in the ADR text and I'm not judging it.
- **Evidence class:** `PROVEN_AUTOMATED` for the rules that were tested. `NOT_RUN` for integration.

**Verdict: `RETURN (guard the ask_owner wall branch with Target.CLOUD in chain; add the near-miss test and its mutation RED)`**
