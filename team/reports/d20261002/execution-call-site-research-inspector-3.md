**Inspector report — `execution-call-site-research` (4th pass, branch tip `d9a9a463`)**

The code holds and every claim I re-ran reproduces. I found one release-path gap outside the worker's area, and the real image run shows the search order has only one working engine.

**Pass 1 — re-run from a clean tree**
- Unit, the two task files: 66 passed.
- PostgreSQL on the dev stack (`test_research_execution_target.py`, dialect asserted `postgresql`, read back through a fresh session): 6 passed.
- Browser cloud tests (the worker left these NOT_RUN): 60 passed.
- Neighbours (research, execution, devices, config, pilot02, voice/news callers, ledger-device): 1428 passed.
- Real activities under a real Temporal worker (`test_browser_research_workflow.py`, `test_ledger_explain_over_real_runs.py`): 7 passed.
- `ruff check .` is clean on api and browser. `ruff format` would reformat `services/browser/tests/unit/test_cloud_worker.py`, but the same is true on main and the gate does not check format.
- Full api suite and full `quality-gate.ps1`: NOT_RUN (not the integration branch).
- No migration, table, `scripts/cloud` or `infra/docker` change, so the host snapshot does not apply to this diff.

**My mutations — 10 of 10 RED, each restored from a backup copy with identical sha256, tree clean**

| Mutation | Failed |
|---|---|
| `_machines` returns every view | 4 |
| unknown device counts as cloud in `_runs_on_cloud` | 1 |
| `device_for` owner_chrome drops the non-cloud filter | 1 |
| `_why_not` ignores revoked | 1 |
| REST `target_device` put to the rule | 1 |
| every engine in the walk uses the same idempotency key | 1 |
| `ledger_required=True` for research | 1 |
| PLANNED event without the `execution_*` fields | 9 |
| clamp lets the payload choose the channel | 5 |
| clamp does not force `visible` | 6 |

**Real run in the image** (`pagentos-cloud-browser:call-site-research`, built one minute after the clamp commit, compose limits, no broker, dev machine's address)
- The gateway's own `session_open` payload (`chrome`, `visible: true`) through the image's clamp and worker: ok, `channel chromium`, `visible false`.
- `bing`: 10 results, on both of two queries.
- `auto`: `provider_rate_limited` (google and duckduckgo captcha), both queries.
- `brave`: `provider_rate_limited` ("blocked", in 0.3 s), both queries.

**Findings**
1. **The setting cannot be turned on in production as the ADR describes.** `infra/docker/docker-compose.prod.yml` forwards only the variables it names, and `PAGENTOS_RESEARCH_EXECUTION_RULE_ENABLED` is not one of them. I rendered `docker compose config` with the variable set: it appears 0 times, while a wired one (`PAGENTOS_TEAM_STORE`) comes through. Release-order step 4 would silently do nothing; step 5 would catch it. The compose file is outside the worker's area, so this is the lead's line to add at merge, and that edit is then judged against the host snapshot.
2. **`CLOUD_SEARCH_ORDER` has one working engine in the image.** The two fallbacks after bing both failed from this address, so if bing blocks the Cloud Core's address, every unnamed research fails once the rule is on. Nothing falls back to a machine mid-run. No engine is measured from the Cloud Core's address.
3. **The 75 s walk budget starts after the session opens.** `session_open` has its own 60 s timeout and the activity has 90 s, so a slow open plus a full walk can exceed the activity. The single-search path already had this shape (60 + 60); the open took 0.4 s in the image.
4. **Docs for the lead, as the worker said:** `BROWSER_CAPABILITIES.md` does not mention the cloud clamp, and `docs/HANDOFF.md` needs the release order including the compose line.
5. No secrets or paths in code; ledger rows carry target, reason and chain only, no query text. Rollback is unsetting the variable.

**Evidence classes**
- Rule call site, setting off equals main, fallback rows, refusals, `attached`, machines unchanged: PROVEN_AUTOMATED (unit and dev-stack PostgreSQL).
- Clamp, headless `session_open` and bing in the real image: PROVEN_PROXY.
- The gateway's walk driving the real worker end to end: NOT_RUN (I asked each engine separately; the walk itself is proven against a scripted worker).
- Setting on in production and a run with `execution_target=cloud`: NOT_RUN, blocked by finding 1 until the compose line exists; then READY_FOR_OWNER as release steps 2–5.

Merge is safe because the default is off and that path is proven equal to main on PostgreSQL. Lead conditions at merge: add the compose line, correct ADR step 4, and do not turn the setting on before measuring the engines from the Cloud Core.

APPROVE
