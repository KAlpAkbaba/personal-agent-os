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
   `device` -> `None`, meaning the unchanged `select_device` call over the NON-cloud views. The
   chosen cloud / owner_chrome view still passes through `select_device([view], ...)`, so the
   capability and the owner's policy are checked for it as for any machine.
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

## Not changed

The rule table, `select_device`, the workflow, the gateway, any schema. The cloud worker is not probed.

## Consequences / open

- Research with nothing named now runs on the cloud worker whenever it is online, ahead of the
  session's own machine (ADR-0208 affinity applies only once the rule says `device`).
- With two online `owner_chrome` machines, `device_for` takes the first in registry order; the
  session's own machine is not preferred there.
- `research_browser == "owner"` (keyboard/OCR) on the cloud device is untouched: it would still
  try the owner path and fall back per page. The plan activity's serial-fetch clamp
  (`startswith("owner")`) also still applies to a cloud run.
- The workflow's replay-only re-select (`select_device_activity`) still uses `select_device` over
  all views; it is reached only when the planned device went offline.
