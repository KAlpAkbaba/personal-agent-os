# Inspector report — execution-call-site-research @ `32047284` (cycle d20261001)

**Pass 1 — run it (tree clean before and after; no code changed)**
- Unit `test_execution_call_site_research.py`: **22 passed**.
- Integration `test_research_execution_target.py` on dev-stack PostgreSQL (`pagentos-postgres`, schema `0063_team_state`): **3 passed**, read back through a fresh session.
- Research integration together (Temporal workflow, artifact, the new file): **12 passed**.
- Neighbouring unit suites (`test_execution_*`, `test_devices_selection`, `test_pilot02_wiring`, `test_research_routes`, `test_research_control`, `test_research_browser_activities`, `test_research_browser_selection`, `test_news_routes`): **279 passed**.
- `ruff check`: clean. `ruff format --check`: one file flagged, `wiring.py` line 83, which predates this commit.
- NOT_RUN by me: the worker's wider 1826-test set (voice, ledger, routines), the full unit suite and `quality-gate.ps1` — the lead's gate is running on this machine and the 22-test file took up to 6 minutes under that load.
- Host snapshot: not applicable, the diff touches no `scripts/cloud`, `infra/docker` or migration. `activity_events.source_ref` is 256 wide on the dev stack and in the snapshot; the new value is 48 characters.

**My mutations (different from the worker's; each restored from a backup copy, sha256 identical)**

| Mutation | Result |
|---|---|
| `device_for` drops the revoked filter | 1 unit RED |
| `_execution_fields` returns `{}` | 6 unit + 1 integration RED |
| fetch path reads the setting alone again | 1 unit RED |
| `research_job_id` not passed | 1 unit + 3 integration RED |
| `_refusal` always returns the rule's sentence | 2 unit RED |

The fourth mutation left 6 execution ledger rows in the dev database, because the test cleans up by `research_job_id`. I deleted exactly those; 0 remain.

**Pass 2 — break it**
1. **BLOCKER: the cloud worker cannot pass the capability check this code puts it through.**
   - The cloud companion's hello advertises `browser_agent/policy.CAPABILITIES`: 30 names, and `browser.chrome` is not one of them. `has_capability(those, "browser.chrome")` is False.
   - Probe with that list on the cloud row and MAIL online: the rule selects cloud, `_select_for` calls `select_device([cloud], capability="browser.chrome")`, and the run is FAILED with "'browser.chrome' yeteneğine sahip çevrimiçi bir cihaz bulunamadı.". The ledger still says `execution.selected target=cloud`.
   - On main the same start goes to MAIL. If production's `bulut` row holds what the code sends, releasing this fails every unnamed voice, REST and news research while the cloud is online.
   - Both test files hide it: the cloud fixture is given `["browser.chrome"]` by hand.
   - I could not read the production row. The lead should read `devices.capabilities_json` for `bulut` before any merge.
2. **Same shape for `owner_chrome`.** A labelled machine without the capability (or policy-denied) gives a FAILED run with MAIL online. A chosen target that cannot serve never falls to the next one in the chain, and no fallback row is written.
3. **"bulutta" never arrives from voice.** `strip_device_phrases("bulutta yapay zeka araştır")` names nothing, and `targets_of_turn` keeps only ev/iş/ofis/laptop. The forced-cloud branch is reachable only by a direct call, so that acceptance case is proven at function level only. The cause is `devices/aliases.py`, outside this area, and the card specified this input: a new card for the lead, not the worker's defect.
4. REST `target_device="bulut"` reaches the cloud; `"bulutta"` gives "not found". Two named machines are refused before the rule, with no ledger row. Both acceptable.
5. The rollback when the ledger write fails is safe: the task and each ledger row commit on their own. No secrets, paths, schema or contract drift; the rule table, `select_device`, the workflow and the gateway are untouched.
6. Session affinity is now overruled by the cloud (the worker's stated open risk, confirmed by probe). This is what the card asks for.

**Evidence classes**
- Rule call site, `device_for`, PLANNED and ledger fields, `_attached`: PROVEN_AUTOMATED (unit + dev-stack PostgreSQL), for a cloud device that advertises `browser.chrome` only.
- Cloud run with the real worker's capabilities: fails (item 1).
- "bulutta" by voice: NOT_RUN, unreachable.
- Production PLANNED event naming `execution_target=cloud`: NOT_RUN.

`RETURN (1. a cloud device with the worker's real hello capabilities must not fail the run while a capable machine is online — either count capability and policy in availability or fall to the next chain target with a fallback row, and never leave a "selected cloud" ledger row on a FAILED run; test it with the capability list read from services/browser/browser_agent/policy.py, RED first; 2. the same for an owner_chrome machine that lacks the capability or is policy-denied; 3. the integration test's cleanup must not depend on research_job_id alone)`
