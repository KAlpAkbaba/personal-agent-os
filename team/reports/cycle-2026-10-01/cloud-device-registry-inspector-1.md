**Inspector report: cloud-device-registry** (branch `team/cycle-2026-10-01/worker-cloud-device-registry`, sha 9cb3f9fa)

**Pass 1: ran it**
- The worker's commit touches 3 files, all inside the area: `cloud_registry.py`, `test_cloud_device_registry.py` and `team/plans/cloud-device-registry-adr.md`. `app/broker` is untouched. `git diff main...` also shows other files, but those come from the branch base, not from this task.
- Pytest ran from `services/api` with the main checkout's venv. `app.__file__` resolves inside the worktree.
- `test_cloud_device_registry.py`, `test_execution_wiring.py` and `test_devices_service.py` together: 46 passed in 0.97s, matching the worker's number. `ruff check` is clean.
- I did not re-do the RED run from before the module existed. I did not run the full `quality-gate.ps1`, and this is not the integration branch.
- I ran three mutations of my own, each RED, with the file restored from a backup copy. The full sha256 before and after was `79f83289299fa721b8f7036d2087314f14484e5f2e68a429ecd457330e5c5167`, and `git status` was clean afterwards.

| Mutation | Result |
|---|---|
| alias idempotence check removed | 3 failed |
| "label already present" guard removed | 1 failed |
| `sync_registry_facts` stops writing the label | 3 failed |

**Pass 2: break it**
- **Metadata handling:** `update_device_metadata` replaces the whole `aliases` and `labels` lists. The code passes `[*current, new]`, so the owner's entries are kept. A test covers it, and the worker's mutation that replaces the owner's labels goes RED.
- **Near misses:** the worker's set is present and sound (Windows device named "bulut", `browser.chrome` alone, a device named `owner_chrome`, a cloud device advertising the capability). I found no test that passes for the wrong reason.
- **Worker's "no agent advertises `browser.profile.owner`" claim:** confirmed. In `services/`, the string appears only in `cloud_registry.py`, where `launch_guard.py` and `worker.py` only have `_persistent_profile_owner` and similar names. `browser_agent/policy.CAPABILITIES` has no such entry. The `owner_chrome` label therefore stays unwritten in production until a device-side task adds the capability. Until then `wiring` sees `owner_chrome_enrolled=False`, so owner_chrome jobs fall back or are refused as before.
- **Cloud alias:** useful only if something resolves "bulut" through `select_device`. `wiring` already picks the cloud device by `platform`.
- **Nothing calls `sync_registry_facts` yet.** I grepped `app` and found no caller outside `cloud_registry.py`, so the module is dead code until the lead wires it.
- **Safety and privacy:** no secrets, paths or logging in the module. Writes go through the broker service's audited update path. The work is light: one row read and at most two updates per hello, and it is idempotent.
- **Duplicated constants:** `CLOUD_PLATFORM` is duplicated from `wiring.py` with no test tying the two together. The worker flagged this, and it is low risk. The label is asserted equal to `wiring.OWNER_CHROME_LABEL`.
- **Add-only labels:** a stale `owner_chrome` label survives a revoked enrollment. The worker flagged this too, and it is acceptable until a device advertises the capability.
- **Wiring the call:** when wiring it, check that the hello path commits the session after the call. `update_device_metadata` only mutates `metadata_json`, and I did not read the hello handler.

**For the lead at merge**
1. Add `cloud_registry.sync_registry_facts(session, device)` right after `apply_hello(...)` in the broker hello handler, and make sure the session commits afterwards.
2. Queue a device-side task: `browser_agent` advertises `browser.profile.owner` when a `cdp_loopback` enrollment record exists, and the C# agent passes it through in the hello.
3. Hold `execution-call-sites` until item 2 lands.

**Evidence:** PROVEN_AUTOMATED (unit tests on SQLite, plus the real `wiring.choose`). NOT_RUN: a real agent hello, Postgres, the full gate.

**APPROVE**
