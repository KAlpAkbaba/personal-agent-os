## Şu an üzerinde çalışılan
`execution-call-site-research` (roadmap 2b, ADR-0213), fourth pass: the lead's two release-safety decisions. Area: `services/api` config, research service, gateway, activities and their tests, plus the ADR draft. Machine: the owner's dev PC, worktree `worker-execution-call-site-research`.

## Result
- **sha:** `d9a9a46340a1fa8b2c816fc1077c61e5d4e8b73c`, pushed; worktree clean.
- **Files:** 8 changed, all inside the area (`config.py`, `service.py`, `browser_gateway.py`, `browser_activities.py`, three test files, the ADR draft).
- **Not run in the image:** the engine walk is proven only against a scripted worker. brave in headless Chromium, and every engine from the Cloud Core's address, are unmeasured.

**1. Setting, default off.** `research_execution_rule_enabled` (`PAGENTOS_RESEARCH_EXECUTION_RULE_ENABLED`, default false).
- Off: the start makes the one `select_device` call main makes, over all views. The rule is not asked, no ledger row is written, and events carry no `execution_*` key.
- On: everything the branch built.
- The release order is in the ADR draft as addendum 3, decision 20: release the api, rebuild and restart `cloud-browser`, verify one `session_open`, then set the variable, then verify one run.

**2. Cloud discovery.** The worker's `engine` is one name or `auto` (google, then duckduckgo); it has no order parameter and its code is outside the area. So the gateway walks the order itself: `CLOUD_SEARCH_ORDER = ("bing", "auto", "brave")`, one `browser.search` per entry until one returns results.
- A machine's search is the single request, payload and idempotency key it always was.
- The walk has a 75 s budget, because the discover activity has 90 s and the workflow is not changed.
- I forced `interstitial="fallback"` on every cloud request; the card did not ask for this. The cloud window is headless, so a handoff would only be waited out.
- The run's `search` event names the engines that did not answer.

## Tests (RED → GREEN)
- **RED before the change:** first an ImportError on `CLOUD_SEARCH_ORDER`; then, with inert scaffolding only, 13 failed / 53 passed. Two of those 13 were mistakes in my own tests, which I fixed; the other 11 were behavioural.
- **GREEN, unit:** the two task files, 66 passed.
- **GREEN, PostgreSQL (dev stack):** 6 passed, including the new setting-off test read back from PostgreSQL. This file must run in its own process; the unit conftest blocks real connections when both share one.
- **Neighbours:** research unit suites 124 + 711 passed; execution, devices, config, browser and pilot02 385 passed; callers (voice, news, routes) 76 passed.
- **Lint:** `ruff check .` clean on api; the 7 touched Python files are formatted.

## Mutations
All 12 went RED and each file was restored from a backup copy with identical sha256.

| Mutation | Failed |
|---|---|
| setting ignored | 3 unit, 1 on PostgreSQL |
| setting default on | 4 |
| walk asks only the first engine | 5 |
| walk keeps the caller's interstitial | 1 |
| walk ignores its budget | 1 |
| order never given to the cloud | 2 |
| order given to every device | 2 |
| cloud order is a single engine | 4 |
| evidence hides the engines that did not answer | 2 |
| card: `_attached` ignores the platform | 5 |
| card: `device_for` takes any online view | 4 |

## Evidence classes
- Setting off equals main's choice; setting on; the gateway order; machines unchanged: PROVEN_AUTOMATED (unit and dev-stack PostgreSQL).
- The order in the cloud image with the setting on: NOT_RUN.
- Browser unit suite: NOT_RUN this pass; no browser file changed.
- Full api suite: NOT_RUN; only the files above.
- A production run with `execution_target=cloud`: NOT_RUN.

## Open risks
- If every engine is blocked from the Cloud Core's address, the query fails with `provider_rate_limited`; nothing falls back to a machine mid-run.
- An activity retry walks the order again with the same idempotency keys. By the test double's description of the client this is a replay, so retries would not re-search; I did not check the real client.
- On the cloud device the workflow's own `search_provider` no longer decides the engine.
- The exact host command for the `cloud-browser` rebuild is not verified; the ADR names the compose service and profile only.
- For the lead at merge: `docs/HANDOFF.md` needs the release order, and `BROWSER_CAPABILITIES.md` still does not mention the cloud clamp. Both are outside my area.
