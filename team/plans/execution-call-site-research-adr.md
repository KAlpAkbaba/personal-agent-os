# ADR (unnumbered; the lead numbers it): the research start asks the execution_target rule

Task: `execution-call-site-research` (roadmap 2b, ADR-0213 / ADR-0220 lead addition 1). 2026-10-01.

## Context

`app.execution.wiring.choose` was built and had no caller. `start_browser_research` picked its
device with `select_device` alone, which knows no platform, so the cloud worker (`bulut`,
platform `cloud`) was chosen or skipped by health order. In the activities, `attached` was read
from `Settings.research_browser` alone, so a run on the cloud device asked for the owner's
Chrome, was refused, and wrote an `owner_chrome -> device` fallback row on every search.

## Decision

1. `start_browser_research` calls `choose_research_target` before any device is picked, with the
   first named word (or none), and maps the decision with the new pure
   `wiring.device_for(decision, views)`: `cloud` -> the online, non-revoked view whose platform is
   `cloud`; `owner_chrome` -> the online, non-revoked, non-cloud view labelled `owner_chrome`;
   `device` -> `None`, meaning the unchanged `select_device` call over the NON-cloud views.
   (Capability and policy: see "Addendum - a target that cannot serve is not available".)
2. The PLANNED event carries `execution_target`, `execution_chain`, `execution_skipped`; a FAILED
   event after a refusal carries `execution_reason` (the decision's reason). The keys are
   prefixed because the event is a flat dict shared with other writers.
3. The ledger rows carry the research task: `wiring.choose(..., research_job_id=)` writes
   `research_job_id` and `source_ref = execution:<task id>:<n>` (default unchanged: a random id).
4. A refusal about MACHINES keeps the sentence the selection always said ("'ofis' cihazı şu anda
   çevrimiçi değil.", "Şu anda çevrimiçi bir cihaz bulunamadı."): voice speaks that text and the
   REST 409 returns it. "bulutta" with the cloud down says "Bulut şu anda çevrimiçi değil.".
5. A REST caller's own `target_device` (no spoken word) is NOT put to the rule: it names a device
   by id, name or alias and stays that device. No execution ledger row is written for it.
6. The research start passes `ledger_required=False`: a ledger write that fails is rolled back
   and logged (`execution_ledger_write_failed`) and the run starts; the decision is still in the
   PLANNED event. `wiring.choose` itself stays strict by default (routines unchanged). Same stance
   as the `task.failed` notification on this path.
7. `browser_activities._attached(mode, device_id)` is the one place both the search and the fetch
   path decide "attached": false when the device row's `platform` is `cloud`. The row is read
   directly (the view copies the same column; a view needs the broker runtime, the fact does not).
8. `start_browser_research` gains `needs_signed_in_session` (default False); no caller passes it yet.

## Addendum (2026-10-02, after the inspector's return) - a target that cannot serve is not available

Found: the cloud worker's hello is `browser_agent/policy.CAPABILITIES` - 30 operation names and
NO family marker `browser.chrome`. The first version chose the cloud by presence and then put it
through `select_device([view], capability="browser.chrome")`: every unnamed research would have
been a FAILED run with MAIL online, under an `execution.selected target=cloud` ledger row.

9. `wiring.choose` takes `capabilities` (the operations the job sends). With them a target is
   available only when one of its devices is online, advertises every one (by name or through
   the family marker, `has_capability`) and is allowed each by the owner's policy. The answer per
   device is `select_device([view], capability=op)`'s own, so there is one definition of
   "capable" and "policy-allowed". Without `capabilities` the rule decides by presence as before
   (the routine adapter is unchanged).
10. Research passes `RESEARCH_OPERATIONS` = `browser.session_open`, `browser.search`,
    `browser.wait`, `browser.fetch_evidence`, `browser.session_close` - exactly what
    `browser_gateway` sends (a test reads the gateway's source). The cloud worker advertises all
    five, so with its REAL hello it is chosen; the machines advertise `browser.chrome`, which
    implies them. The cloud is NOT asked for the family marker: selection may ask for either
    shape (`app.devices.capabilities`), and the operation names are the precise one.
11. A target that is up and cannot serve is skipped for the next in the chain, and the fallback
    row says why: `cloud_capability_missing`, `cloud_policy_denied`,
    `owner_chrome_capability_missing`, `owner_chrome_policy_denied`, `device_capability_missing`,
    `device_policy_denied`. These are written by `wiring` over the rule's `*_offline` skip; the
    rule table is not changed (it still knows only up / down).
12. The `device` target is available exactly when the caller's own
    `select_device(machines, "browser.chrome", ...)` would succeed, and `choose` takes the
    caller's `views`, so the decision and the pick read ONE registry snapshot with ONE test.
    Consequence: a FAILED run never sits under an `execution.selected` row (tested over five
    refusal shapes); `device_for(decision, views, capabilities=)` applies the same test.
13. "bulutta" with the cloud up but unable: FAILED, `forced_target_unavailable`, skip
    `cloud_capability_missing` / `cloud_policy_denied`, said as "Bulut bu işi şu anda yapamıyor."
    (not "çevrimiçi değil", which would be false).
14. The integration test's cleanup no longer trusts the code under test: every ledger write the
    process makes is noted by `source_ref` at write time and the tasks are found by the test's own
    intent text; one test proves a row with no `research_job_id` is removed.

## Addendum 2 (2026-10-02, the inspector's second return) - the cloud device decides its own window

Found by reading, then reproduced in the image: `browser_gateway` sends ONE `session_open` for
every device - `channel: "chrome"`, `policy.visible: true` - the cloud companion's clamp passed
both through, and the worker takes them from the payload ahead of its own
`--channel chromium --headless`. The cloud image has no Google Chrome and no display.

15. `browser_agent.cloud.policy.clamp_command` forces `channel = "chromium"` and
    `policy.visible = false` on every `session_open` (`CLOUD_CHANNEL`, `CLOUD_VISIBLE`), whatever
    the payload says and also when it says nothing. Decided where the device decides: the
    gateway stays one payload for all devices (the owner's machines keep their visible Chrome)
    and no other caller can ask the cloud for a window either. The gateway is not changed.
16. The `session_open` result already reports what was launched (`channel`, `policy.visible`),
    so the caller is told, not surprised.
17. The test does not retype the payload: it compiles the gateway's own `_open_session` from
    `services/api/app/research/browser_gateway.py`, runs it against a recording client, puts
    what it sent through the real clamp and the real worker's `session_open`, and reads the
    arguments the worker hands `ManagedBackend` (headless, chromium, the dedicated profile).
    A second test keeps the reason visible: without the clamp the same worker launches a
    visible `chrome`.
18. "The `device` target is a machine" is the service's own platform filter (`_machines`), and
    is now held by tests with a cloud view that advertises `browser.chrome` and is the
    healthiest device: a signed-in run, the session's own device being the cloud, a machine
    word only the cloud answers to, and a cloud holding a machine's alias.

Run in the image built from this branch (`pagentos-cloud-browser:call-site-research`, compose's
limits, no broker: the bridge is driven over an in-process socket, everything else is the
image's own code), 2026-10-02 on the dev machine:

- the gateway's payload WITHOUT the clamp: `dependency_unavailable`, "Chromium distribution
  'chrome' is not found at /opt/google/chrome/chrome";
- the same payload through the bridge: `session_open` succeeded, `channel chromium`,
  `visible false`, Chromium 151.0.7922.34; `browser.search` on bing returned 10 results;
- `browser.search` on duckduckgo - the gateway's default engine - ended in the provider's
  captcha page twice out of two (`provider_rate_limited`). See "Consequences / open".

## Not changed

The rule table, `select_device`, the workflow, the gateway, any schema. The production cloud
worker is not probed.

## Consequences / open

- Research with nothing named now runs on the cloud worker whenever it is online, ahead of the
  session's own machine (ADR-0208 affinity applies only once the rule says `device`).
- With two online `owner_chrome` machines, `device_for` takes the first in registry order; the
  session's own machine is not preferred there.
- `research_browser == "owner"` (keyboard/OCR) on the cloud device is untouched: it would still
  try the owner path and fall back per page. The plan activity's serial-fetch clamp
  (`startswith("owner")`) also still applies to a cloud run.
- A REST caller's own `target_device="bulut"` still goes through `select_device(...,
  "browser.chrome")` and is refused for the real cloud worker (`capability_missing`, a FAILED run
  with no execution row). The rule is not asked on that path (decision 5); left as it is.
- Releasing this sends every unnamed research to the cloud worker while it is online. A whole
  research run on the production worker is not proven here (the card forbids probing it).
- OPEN, found by the image run and outside this area: headless Chromium got DuckDuckGo's captcha
  page on both searches tried (from the dev machine's address; whether the Cloud Core's address
  gets the same is not measured). DuckDuckGo is the gateway's default engine and a request that
  names one engine tries only that one, so a cloud run's discovery can end in
  `provider_rate_limited` while the same search on bing works. Nothing falls back to a machine
  mid-run. Decide before the release: another engine (or `auto`) for a run on the cloud device,
  or a mid-run fallback.
- "bulutta" still does not arrive from voice (`devices/aliases.py`, outside this area).
- The workflow's replay-only re-select (`select_device_activity`) still uses `select_device` over
  all views; it is reached only when the planned device went offline.
