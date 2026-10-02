# Inspector report — execution-call-site-research (second pass, `15da3087`)

The card's code is correct and proven, but releasing it would very likely fail every unnamed research in production. That conclusion comes from reading the code, not from a run.

**Pass 1 — re-run from a clean tree**
- Unit `test_execution_call_site_research.py`: **37 passed**.
- Integration on dev-stack PostgreSQL (`pagentos-postgres`, schema `0063_team_state`): **5 passed**, read back through a fresh session. `activity_events` rows with `source='execution'` afterwards: **0**.
- Neighbour suites (the worker's set plus `test_routines*`): **1248 passed**.
- `ruff check` and `ruff format --check` on the 6 files: clean.
- Column widths in the real table: new reason strings go only into `detail_json`; `source_ref` is 48 characters in a VARCHAR(256). No width risk.
- No migration, table, `scripts/cloud` or `infra/docker` change in the diff, so the host snapshot check does not apply (the fixture is not on this branch).
- Full unit suite and `quality-gate.ps1`: NOT_RUN (branch is not on integration).

**My mutations** (different from the worker's; each restored from a backup copy, sha256 identical before and after, tree clean)

| Mutation | Result |
|---|---|
| X1 `service._machines` returns all views (cloud can become the `device` target) | **survived, 37 green** |
| X2 fetch path ignores `_attached` (one call site only) | RED (1) |
| X4 `ledger_required=True` in `target.py` | RED (1) |
| X6 `_why_not` ignores `revoked` | RED (1) |

**Pass 2 — findings**

1. **Blocker (by reading, not run): the cloud worker cannot open the session research sends.**
   - `browser_gateway.py:496-501` hard-codes `"policy": {..., "visible": True}` and `"channel": "chrome"` in every `session_open`.
   - The cloud companion's `clamp_command` passes both through untouched.
   - `worker.py:1233` and `:1254` let the payload override the worker's `--channel chromium --headless`, so `ManagedBackend(headless=False, channel="chrome")` is launched (`:1382-1385`).
   - The image (`infra/docker/cloud-browser/Dockerfile`, `playwright/python:v1.62.0-noble`) installs no Google Chrome and has no display or Xvfb.
   - Consequence once released: the rule selects the cloud (it is online in production), the run is PLANNED, and `session_open` fails. That covers voice research, the news summary routes and unnamed REST research. Nothing falls back to MAIL mid-run.
   - The unit fake `_cloud_worker_factory` accepts any payload whose profile is not `owner`, so it cannot see this.
   - The fix is outside this card's area (gateway or `browser_agent/cloud/policy.py`); the lead must widen the area or cut a card.
2. **A test passes for the wrong reason (X1).** "Needs a signed-in session never goes to the cloud" holds only because the cloud fixture lacks `browser.chrome`. Nothing in the suite keeps a cloud device that advertises the family marker out of the `device` target at the service's `_machines` filter.
3. **Carried from the worker's report, confirmed in code:**
   - Unnamed research now bypasses session affinity (ADR-0208) whenever the cloud is up.
   - REST `target_device="bulut"` still fails on `browser.chrome`.
   - "bulutta" does not arrive from voice.
   - Production `bulut.capabilities_json` has not been read by anyone.
4. Checked and clean: no secrets or paths, no schema or contract drift, the rule table and `select_device` are untouched, and `wiring.choose` defaults leave routines unchanged. `_runs_on_cloud` adds one short database read per search and per fetch, which is negligible on CPX32. Rollback is a plain revert with no data to migrate.

**Evidence classes**
- Rule call site, skip reasons, PLANNED and ledger fields, `_attached`: PROVEN_AUTOMATED (unit + dev-stack PostgreSQL).
- Cloud worker opens a research session with the gateway's real payload: NOT_RUN. The card forbids probing; the image was not built here because C: is nearly full.
- Production PLANNED event with `execution_target=cloud`: NOT_RUN.

**Verdict**

RETURN (
1. Do not merge or release until the cloud worker accepts the gateway's real `session_open`: either the cloud companion clamp forces `channel=chromium` and `visible=False`, or the gateway does not send them for a cloud device. Include a test that feeds the gateway's actual payload through `browser_agent.cloud.policy.clamp_command` and the worker's launch arguments. This needs an area extension from the lead.
2. Lead, before the merge: one real `session_open` plus `browser.search` on the cloud image, on the host or a local build, since unit fakes cannot prove it.
3. Add a unit test that goes RED on X1: a cloud view advertising `browser.chrome`, with `needs_signed_in_session=True` or a named machine, is never the run's device.
)
