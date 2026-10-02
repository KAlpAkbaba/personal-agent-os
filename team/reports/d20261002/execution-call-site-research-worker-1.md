## Şu an üzerinde çalışılan
`execution-call-site-research` (roadmap 2b, ADR-0213) — third round, the inspector's second return. Area: `services/browser/browser_agent/cloud/policy.py`, two browser unit test files, the api unit test file, the ADR draft. Machine: the dev PC, in worktree `worker-execution-call-site-research`.

## Result
- **sha:** `c01da760ca150e82a1f48c0b536dc8333e1231ea`, pushed, tree clean. Two commits on top of `15da3087`; 5 files changed, all inside the area.
- **One open risk blocks a safe release:** on the cloud image, `browser.search` on DuckDuckGo (the gateway's default engine) ended in a captcha on both tries. Details under point 3.

**1. The cloud worker now opens the session research sends.**
- `clamp_command` forces `channel="chromium"` and `policy.visible=false` on every `session_open`, whether the payload asks for something else or says nothing. The gateway is unchanged.
- New `test_cloud_session_open_payload.py` does not retype the payload. It compiles the gateway's own `_open_session` from `browser_gateway.py`, runs it against a recording client, passes the result through the real clamp into the real worker's `session_open`, and reads what the worker hands `ManagedBackend`.
- RED before the fix: 6 failed, 54 passed (`assert 'chrome' == 'chromium'`). GREEN after: 60 passed.
- A control test shows the same worker launches a visible `chrome` when the clamp is skipped.

**2. X1 now goes RED.**
- Four new api unit tests use a cloud view that advertises `browser.chrome` and is the healthiest device:
  - a signed-in run stays on MAIL;
  - the session's own device being the cloud does not pull the run there;
  - a machine word only the cloud answers to names no machine (FAILED, no device);
  - a cloud holding MAIL's alias does not block "ev".
- These were not RED-first: the platform filter already existed, so the mutation is the proof.

**3. Real run in the image** (meant for the inspector; I ran it to check the fix).
- Built `pagentos-cloud-browser:call-site-research` from this branch and ran it with the compose limits. There was no broker: the bridge was driven over an in-process socket.
- Gateway payload without the clamp: `dependency_unavailable … Chromium distribution 'chrome' is not found at /opt/google/chrome/chrome`.
- Same payload through the bridge: `open ack: succeeded, channel chromium, visible false, browser_version 151.0.7922.34`.
- `browser.search` on bing: `succeeded, result_count 10` (first hit mgm.gov.tr).
- `browser.search` on duckduckgo: `provider_rate_limited: every provider (duckduckgo) ended in captcha`, 2 of 2 tries.

## Mutations
Each was restored from a backup copy with sha256 identical before and after.

| Mutation | Result |
|---|---|
| X1: `_machines` returns all views | RED, 4 failed (`service.py` sha `ae17f28d…7533` before and after) |
| clamp does not force the channel | RED, 6 failed |
| clamp does not force visible | RED, 6 failed |
| `device_for` takes the first online view regardless of platform | RED, 4 failed |
| `_attached` ignores the platform | RED, 3 failed |

## Checks run
- Browser unit suite: 1022 passed.
- Api `test_execution_call_site_research.py`: 41 passed.
- Integration on dev-stack PostgreSQL (`127.0.0.1:15432`): 5 passed.
- `ruff check` clean on both packages. `ruff format --check` passes on the two files I wrote or changed in the browser package; `test_cloud_worker.py` was already unformatted at HEAD and I left it.

## Evidence classes
- Clamp, gateway-to-worker launch arguments, X1 guard: PROVEN_AUTOMATED.
- Cloud image opens the gateway's session and searches on bing: PROVEN_REAL on a local build, not on the Cloud Core host.
- Full research run on the production worker, and a production PLANNED event with `execution_target=cloud`: NOT_RUN.

## Open risks
- **DuckDuckGo captcha (outside my area).** This was measured from the dev machine's address only; the Cloud Core's address is NOT_RUN. A request that names one engine tries only that one, and nothing falls back to a machine mid-run, so a cloud run's discovery can fail. Decide before release: another engine (or `auto`) for cloud runs, or a mid-run fallback. It is written into the ADR draft.
- Carried over unchanged: unnamed research bypasses session affinity when the cloud is up; REST `target_device="bulut"` is refused; "bulutta" does not arrive from voice; production `bulut.capabilities_json` is unread.

## For the lead and inspector
- ADR text is addendum 2 in `team/plans/execution-call-site-research-adr.md`.
- Add `device_for` to `test_pilot02_wiring.py` if it holds wiring's `__all__`.
- The image is left on the machine for the inspector (1.05 GB). The probe driver is outside the repo at `C:\Users\alpak\AppData\Local\Temp\claude\cloud_probe.py`.
