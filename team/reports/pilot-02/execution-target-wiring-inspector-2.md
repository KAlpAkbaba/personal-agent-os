**Inspector report, `execution-target-wiring` (return pass), sha `c67a0cfa`**

**Pass 1: ran it**
- `app.__file__` points at the worktree. The 21 tests in `test_execution_wiring.py` pass. `ruff check` is clean on all touched files.
- Broader run: `pytest tests/unit -k "device or ledger or execution"` gives 1006 passed. I did not run the full `quality-gate.ps1`, the full unit corpus or the PS suites (NOT_RUN).
- The diff touches only area files: `wiring.py`, the two `target.py` adapters, the test file and the plan. It changes no shared files and no `DEVICE_PROTOCOL` or API schema.
- Mutation A (mine, not the worker's): I forced the allow-list check to `and False`. Result: RED, 2 failed (`…outside_the_allow_list_is_refused`, `…missing_allow_list_module_counts_as_not_allowed`).
- Mutation B (mine): I disabled the `error_class_of` mapping. Result: RED, 5 failed, including both adapter tests and the revoked-cloud test.
- Restore: `wiring.py` was copied back from a backup after each mutation. Full sha256 before and after: `520074ccd5fd2e1be969574fa4d4b00962f925b08be02202ca39ae96ac217620`. `git status` is clean.
- The worker's mutations, "fallback event dropped" and "scheduled `acting=True`", were reported RED earlier and re-run RED in the last inspection. I did not repeat them.

**Pass 2: breaking it**
- **Revoked filter:** the worker's M2 covers it. Both revoked tests go RED when the filter is removed, and the near miss is in the file.
- **ask_owner path:** `error_class_of` returns None for a captcha blocker, so a blocked cloud run is not reported as a missing device. There is a near-miss test.
- **DeviceView contract:** `platform`, `status`, `presence` and `labels` all exist in `app/devices/types.py`, so the fake registry matches the real view fields. `list_device_views` and `BrokerRuntime` were not exercised for real (NOT_RUN).
- **Allow-list:** the default is "not allowed", which is safe. Once `app.execution.allowlist` exists, the lazy import would swallow only `ImportError`.
- **Ledger and privacy:** events carry `job_kind`, `target` and `reason`, plus the `url` only if the rule puts it in the event body. The summaries are Turkish and have no secrets. I did not audit whether `rule.events` includes the URL. That is a small KVKK question for the lead to look at.
- **Duplicate events:** `choose` writes events under a fresh uuid, so a retried activity writes them twice. The worker already told the lead to call it once per run.
- **Scheduled jobs:** `scheduled=True` forces `acting=False` and the cloud-only kind in `choose` itself, and the routine adapter passes both. A caller cannot get acting=True through.
- **Cost:** the extra registry read per run is negligible on CPX32. Rollback is deleting three new files, because no call sites exist yet.

**Findings that do not block approval**
1. **No writer for the registry convention (the worker's item 1).** Nothing writes `platform="cloud"` or the `owner_chrome` label. If the lead adds call sites before enrollment sets them, every research run and routine fails with `no_capable_device`. The lead must hold the call sites until a writer exists, or make the call sites opt-in.
2. **`ruff format --check` fails.** It flags a test-file signature that was already there, so it is cosmetic.
3. **`named-but-unavailable` placeholder.** It is harmless, and the rule only checks it for None.

**Evidence classes**
- PROVEN_AUTOMATED: the wiring logic against a fake registry and in-memory ledger: selection, fallback event, allow-list refusal, no_capable_device mapping, revoked devices, scheduled read-only.
- NOT_RUN: the real registry and broker, the real allow-list module, owner-Chrome enrollment.
- READY_FOR_OWNER: none.

**Hold until the lead decides**
- For the lead at merge: add the call sites only after the registry writer exists.

APPROVE
