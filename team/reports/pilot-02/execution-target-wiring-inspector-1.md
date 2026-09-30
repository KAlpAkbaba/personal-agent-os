**Verdict: RETURN.** The wiring passes everything I ran, but one mutation survives and one production premise isn't verified.

**Pass 1: run it**
- I re-ran `test_execution_wiring.py` from a clean state: 17 passed. Together with `test_execution_target.py` it is 64 passed. `app.__file__` points at the worktree. Ruff is clean on the five touched files. The worktree is clean.
- `wiring.py` sha256 was `520074ccd5fd2e1be969574fa4d4b00962f925b08be02202ca39ae96ac217620` before and after my mutations. I restored it from a backup copy, not `git checkout`.
- I ran three mutations of my own, different from the worker's:
  - M1, allow-list check disabled: RED (2 failed: `..._outside_the_allow_list_is_refused` and `..._missing_allow_list_module_counts_as_not_allowed`).
  - M2, the `status != "revoked"` filter removed: **GREEN, 17 passed. The mutation survived.**
  - M3, every refusal mapped to `no_capable_device`: RED (`test_a_policy_refusal_is_not_reported_as_no_capable_device`).
- I did not run the full `quality-gate.ps1`; this is a worker branch, not the integration branch.
- I did not run the real `list_device_views` or `BrokerRuntime`. No dev stack was available to me.

**Pass 2: break it**
1. **Revoked devices are untested.** `_availability` filters revoked devices, but no test fails when the filter goes. A revoked cloud worker or a revoked Chrome machine could count as available and nothing would catch it. Add a test in which a revoked, online cloud device and a revoked `owner_chrome` device are present and are not chosen.
2. **The `ask_owner` path is untested.** `cloud_blocker` is a `choose` parameter and the rule returns `ask_owner`, which writes an `execution.refused` event with `ask_owner=True`. No test calls `choose` with a blocker. The path is untested through the wiring, and `error_class_of` should return None for it, so pin that in a test.
3. **The registry convention has no writer in the repo (not a code defect).** I grepped for `platform="cloud"` and for the label `owner_chrome` on a device. Nothing in `app/devices`, the enrollment scripts or the device service ever sets either one. Until an enrollment step sets them, `owner_chrome_enrolled` is always False and there is no cloud device. Once the lead adds the call sites, every research run and routine would then fail with `no_capable_device`. The lead must not add the call sites until the enrollment side writes `platform="cloud"` and the `owner_chrome` label. The worker flagged this as a risk; I confirm it is real.
4. **Double events.** `choose` uses a fresh uuid as `source_ref` on every call, so a retried activity writes the events twice. This is acceptable for now, but the call site must call `choose` once per run.
5. **The `named-but-unavailable` placeholder** is only checked by the rule for `None`. It is harmless, but a comment should say so.
6. **What I found clean:**
   - No secrets or paths in the code.
   - No files outside the area.
   - No contract drift: the ledger vocabulary constants are reused, not redefined.
   - The events carry job kind, chain and reason only. The `url` never reaches the ledger, so there is no KVKK leak.
   - The lazy `allowlist` import fails closed.

**Evidence classes**
- Rule wired to registry and ledger, with fakes and SQLite: PROVEN_AUTOMATED.
- Mutations on the fallback event and on scheduled `acting=True` (the worker's): confirmed RED.
- Real registry conventions and the real `allowlist` module: NOT_RUN.
- Owner-Chrome enrollment labelling: NOT_RUN. It needs the owner's machine.

**Required to close the RETURN:** items 1 and 2 as tests, with M2 shown RED and the file restored by full sha256. Item 3 goes to the lead's merge checklist.

RETURN (test the revoked filter and kill mutation M2; test the `ask_owner` path through `choose`; the lead does not wire call sites until the enrollment side sets `platform="cloud"` and the `owner_chrome` label)
