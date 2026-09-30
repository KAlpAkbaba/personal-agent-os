**Worker report, `execution-target-wiring`, RETURN closed**

**Şu an üzerinde çalışılan:** `execution-target-wiring`, area `services/api/app/execution/wiring.py` and its adapters and tests. It is now finished and the worktree is clean. I did not touch `docs/HANDOFF.md`.

**sha:** `c67a0cfaf74b2c9e302640f10059278932be150b`, branch `team/pilot-02/worker-execution-target-wiring`, pushed. I first tried to push at the commit step and did not read the push output beyond a trailing `tail`, so treat the push as unconfirmed until you see the branch on the remote.
**Files changed:** 1, `services/api/tests/unit/test_execution_wiring.py`, inside the area. `wiring.py` is untouched.

**Tests added (4):**
- `test_a_revoked_online_cloud_device_is_not_chosen`. A revoked, online cloud device is refused, and `error_class_of` gives `no_capable_device`.
- `test_a_revoked_owner_chrome_device_is_not_a_fallback`. With the cloud offline, a revoked `owner_chrome` device is not chosen and no `execution.selected` event is written.
- `test_a_blocked_cloud_run_asks_the_owner_and_is_not_a_missing_device`. With `cloud_blocker="captcha"`, `choose` returns `ask_owner`. `error_class_of` returns None. Exactly one `execution.refused` event is written, with `ask_owner=True`.
- `test_no_blocker_leaves_the_cloud_run_selected`. This is the near miss for the blocker test.

**Proof (PROVEN_AUTOMATED):**
- **GREEN:** 21 passed, up from 17. `app.__file__` points at the worktree.
- **M2:** I removed the `status != "revoked"` filter with `live = list(views)`. The result was RED: 2 failed (both revoked tests), 19 passed.
- **Restore:** I restored `wiring.py` from a backup copy. Its sha256 was `520074ccd5fd2e1be969574fa4d4b00962f925b08be02202ca39ae96ac217620` before the mutation and after the restore.
- **Ruff check:** clean on `wiring.py` and the test file.
- **Ruff format:** `ruff format --check` flags line 90 of the test file, a function signature that was already there before my changes. I did not reformat it, to keep the diff small. Say if you want it folded in.
- **Earlier mutations:** the fallback event dropped and scheduled `acting=True` were shown RED in the first pass, and the inspector re-ran M1 and M3 RED.

**For the lead at merge:**
1. **Do not add the call sites yet.** Nothing in the repo writes `platform="cloud"` or the `owner_chrome` label on a device. Until an enrollment step sets both, `choose` always returns `no_capable_device`, and every research run and routine would fail.
2. **Call sites, when you add them:**
   - Research service: `app.research.target.choose_research_target(db, runtime, ...)`.
   - Routine service: `app.routines.target.choose_routine_target(db, runtime, url=...)`.
   - Call each **once per run**. `choose` writes events under a fresh uuid, so a retried activity would write them twice.
3. **Allow-list:** `app.execution.allowlist` is imported lazily and a missing module counts as "not allowed". Once that module lands, cloud acting steps go through it.

**Not run (NOT_RUN):**
- The real `list_device_views` and `BrokerRuntime`; the tests use a fake registry.
- The real `allowlist` module.
- Owner-Chrome enrollment on the owner's machine.

**Open risks:**
- The registry convention has no writer (item 1 above).
- The `named-but-unavailable` placeholder is only checked by the rule for `None`, so it is harmless. I did not add the comment the inspector asked for, because it is a wiring.py edit outside this return's scope. It is a one-line comment for the lead to add if wanted.

The card's optional ADR text stays as before in `team/plans/execution-target-wiring-adr.md` (unchanged this pass).
