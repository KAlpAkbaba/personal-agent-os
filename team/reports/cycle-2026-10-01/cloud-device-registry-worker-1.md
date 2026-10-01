**Şu an üzerinde çalışılan:** cloud-device-registry, `services/api/app/devices/`, machine MAIL (worker). The lead writes HANDOFF from this report.

**Sha:** 9cb3f9fa1b237b7a538b9885f5eaada9a14639cc (pushed, worktree clean). 3 files changed, all in the area.

**What was built** (`app/devices/cloud_registry.py`):
- `ensure_cloud_alias(db, device)` adds `bulut` to `metadata_json.aliases` for `platform == "cloud"` only. It is idempotent and case-insensitive, and keeps the owner's aliases.
- `owner_chrome_label(view)` returns `owner_chrome` when the device advertises the exact capability `browser.profile.owner`. It never returns the label for a cloud device.
- `sync_registry_facts(db, device)` is the hello-path function. It calls both writers, only ever adds, and returns whether it wrote anything.
- All writes go through `broker_service.update_device_metadata`. `app/broker` is untouched.

**Tests** (`test_cloud_device_registry.py`, 11, real broker tables in SQLite plus the real `wiring.choose`):
- RED: before the module existed, collection failed with `ImportError: cannot import name 'cloud_registry'`.
- GREEN: 11 passed. With `test_execution_wiring.py` and `test_devices_service.py`, 46 passed. `ruff check` is clean.
- Hits: cloud gains `bulut` once; a device advertising the capability is labelled; `wiring.choose` then selects CLOUD (spoken "bulutta") and OWNER_CHROME (signed-in need).
- Near misses: a Windows device named "bulut" gets no alias; `browser.chrome` alone gets no label; a device named `owner_chrome` gets no label; a cloud device advertising the capability gets no label; before sync, `choose` does not pick OWNER_CHROME or CLOUD.
- Mutations, each RED, file restored byte-for-byte (full sha256 `79f83289299fa721b8f7036d2087314f14484e5f2e68a429ecd457330e5c5167` before and after):

| Mutation | Result |
|---|---|
| platform check removed | 2 failed |
| label from `view.name` | 5 failed |
| family marker `browser.chrome` implies the label | 3 failed |
| cloud exclusion removed | 1 failed |
| label replaces the owner's labels | 1 failed |
| alias check made case-sensitive | 1 failed |

- Evidence class: PROVEN_AUTOMATED (unit, SQLite). Nothing was run against a real agent or Postgres (NOT_RUN).
- I ran pytest from `services/api` with the main checkout's `.venv`; `app.__file__` resolves inside the worktree.

**ADR text:** `team/plans/cloud-device-registry-adr.md`.

**For the lead at merge**
1. **Call site:** in `app/broker`, right after `apply_hello(...)` for the device (hello handler), add `cloud_registry.sync_registry_facts(session, device)`. One call; it is idempotent.
2. **Blocking fact:** no agent advertises `browser.profile.owner` today. I searched the Windows agent and `browser_agent` worker. The owner-Chrome enrollment is a file on the device (`owner-enrollment.json`, ADR-0113) and the hello carries nothing about it. The `owner_chrome` label therefore stays unwritten until the agent advertises that capability. I chose the name and did not invent a second source. A device-side task is needed: the worker adds the capability to `policy.CAPABILITIES` when its enrollment registry holds a `cdp_loopback` record, and the C# agent passes it through the hello.
3. **Cloud alias:** it works on its own, because the cloud device is already selected by `platform`. The alias only matters if something resolves "bulut" through `select_device`.
4. **`execution-call-sites`:** it waits on this task. Until item 2 is done, owner_chrome jobs fall back or are refused as before.

**Open risks**
- Labels are add-only. A revoked enrollment leaves a stale `owner_chrome` label. Removing it would also wipe a label the owner set by hand while no agent advertises the capability. Flip to sync-remove once item 2 lands.
- `OWNER_CHROME_LABEL` and `CLOUD_PLATFORM` are duplicated from `wiring.py`, because importing `wiring` from `devices` would create a cycle. The test asserts the label equals `wiring.OWNER_CHROME_LABEL`; there is no such assertion for the platform string.
