# Inspector report: execution-call-site-research @ `c01da760` (cycle d20261002)

The in-area code does what the card asks and is proven, but releasing it as it stands would break unnamed research in production. Two release blockers, both outside the worker's area; the tree is clean after my runs.

## Pass 1: re-run
- **Api unit file:** 41 passed.
- **Dev-stack PostgreSQL** (`127.0.0.1:15432`, dialect asserted): `test_research_execution_target.py` 5 passed; 0 leftover rows afterwards (psql count).
- **Other integration files that call the research start** (workflow, artifact, voice sessions, mobile push): 32 passed together with the 5 above.
- **Wider api unit** (research, execution, cloud, devices, routines, ledger, pilot wiring): 1551 passed. The voice-research and news chunk exited 0; I did not capture its count.
- **Browser unit:** 1015 passed in `tests/unit` (the report says 1022; I did not run the whole `tests` tree).
- **Lint:** `ruff check .` is clean on api and browser. `test_cloud_worker.py` is unformatted on main too.
- **My mutations** (8, different from the worker's, each restored from a backup with identical sha256), all RED:

| Mutation | Result |
|---|---|
| `device_for` without the online/capability test | 2 failed |
| fetch path decides attached from the setting alone | 1 failed |
| PLANNED event without execution fields | 9 unit, 1 on PostgreSQL |
| `needs_signed_in_session` dropped | 3 failed |
| ledger rows without `research_job_id` | 1 unit, 5 on PostgreSQL |
| cloud available by presence alone | 5 unit, 1 on PostgreSQL |
| FAILED event without `execution_reason` | 3 unit, 1 on PostgreSQL |
| `CLOUD_VISIBLE = True` | 7 failed |

- **Real run in the image** (`pagentos-cloud-browser:call-site-research`, built after `623f9e54`, compose limits, no broker):
  - Gateway payload without the clamp: `dependency_unavailable … 'chrome' is not found`.
  - Through the clamp: `session_open` succeeded, chromium, `visible false`.
  - bing: 10 results.
  - duckduckgo: captcha, 2 of 2.
  - `auto` (google, duckduckgo): captcha, 1 of 1.

## Pass 2: findings
1. **Release order (blocker).** `docker-compose.prod.yml` says `cloud-browser` is "NOT part of the release transaction", and no script rebuilds it. The host snapshot shows `pagentos-prod-cloud-browser` running. A normal blue/green release ships the api half only, so unnamed research goes to the old worker with no clamp, and every `session_open` fails as in my unclamped run. Neither the ADR draft nor HANDOFF names the rebuild step.
2. **Discovery on the cloud (blocker).** The default engine is captcha'd in headless Chromium, and so is `auto`, so the worker's suggested remedy does not work. bing works but is not a selectable `research_search_provider` (`duckduckgo|google|auto`). Today the same run searches Google in the owner's Chrome. This was measured from the dev machine's address; the Cloud Core's address is NOT_RUN.
3. **"bulutta" reaches the service from no caller.** Voice aliases do not deliver it and REST sends `target_device`, so the forced-cloud branch is proven at function level only. REST `target_device="bulut"` is still refused.
4. **Production `bulut.capabilities_json` is unread.** If it lacks one of the five operations the rule skips the cloud, which is the safe direction.
5. **Host snapshot is older than the last release** (`collected_at` 2026-10-01T19:18Z; main `5f250e5b` is serving). The diff touches no migration, `infra/docker` or `scripts/cloud`, so this is informational.
6. **Column widths are safe.** New reasons go into `detail_json`; `source_ref` is at most 48 characters in a VARCHAR(256), matching the snapshot. There is no schema change.
7. **Contract doc.** `BROWSER_CAPABILITIES.md` does not say the cloud device forces chromium and headless (lead, outside the area).
8. **`test_pilot02_wiring.py` does not hold wiring's `__all__`**, so there is nothing to add at merge.

## Evidence classes
- Call site, `device_for`, attached helper, ledger and run rows on PostgreSQL: PROVEN_AUTOMATED.
- Cloud image opening the gateway's session and searching on bing: PROVEN_PROXY (local build, no broker).
- Production worker failing without the clamp: PROVEN_PROXY (same worker code with the clamp bypassed; the production image itself is unread).
- A production research run with `execution_target=cloud`: NOT_RUN.

RETURN (1. write the release order into the ADR and HANDOFF: rebuild and restart `cloud-browser` from this commit before the api half serves, or keep the call site behind a default-off setting until then; 2. give a cloud run a discovery that works, by a card that widens the area to the provider list or gateway, before any release sends unnamed research there; both are for the lead, since no defect was found in the worker's in-area code)
