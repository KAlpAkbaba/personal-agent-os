## Şu an üzerinde çalışılan
`execution-call-site-research` (roadmap 2b, ADR-0213/0220) — alan: `services/api/app/research/{service,target,browser_activities}.py`, `app/execution/wiring.py`, iki test dosyası, ADR metni — makine: MAIL (dev stack). Durum: bitti, commit + push yapıldı, worktree temiz.

## Report
- **sha:** `3204728423d2c481d8d9ce5cdb502efa4c243227`, pushed to `team/d20261001/worker-execution-call-site-research`.
- **Files:** 7 changed, all inside the area.
- **Built:**
  - `start_browser_research` calls `choose_research_target` before picking a device and maps the decision with the new pure `wiring.device_for`.
  - `browser_activities._attached(mode, device_id)` is now the one place both the search and fetch paths decide "attached"; it is False on the cloud device.
  - The PLANNED event carries `execution_target`, `execution_chain`, `execution_skipped`; a refused run's FAILED event carries `execution_reason`.

**Tests (RED→GREEN)**
- Unit `test_execution_call_site_research.py`: 20 written first, 18 failed / 2 passed before the change (first failure: device was `MAIL`, expected `bulut-1`). Now 22 passed, covering every acceptance case on the card.
- Integration `test_research_execution_target.py` on dev-stack PostgreSQL: 3 passed, read back through a fresh session (cloud run row + `execution.selected`, the fallback rows, the `bulutta` refusal). These were written after the implementation; their RED is the mutation run below.

**Mutations** (restored from backup copies, sha256 identical before/after)
- `device_for` returns the first online view → 4 unit + 1 integration RED.
- `_attached` ignores the platform → 3 unit RED.
- Device target selects over all views → 1 unit RED.
- Rule not asked → 11 unit + 3 integration RED.
- Ledger switch forced strict → 1 unit RED.

**Evidence classes**
- Rule call site, `device_for`, `attached`, ledger rows: PROVEN_AUTOMATED (unit + dev-stack PostgreSQL).
- Regression: 1826 neighbouring unit tests passed (execution, devices, research, voice research, realtime sessions, news, ledger, routines, pilot02); 9 existing research integration tests passed (Temporal workflow + artifact).
- `ruff check`: clean.
- PROVEN_REAL (a production PLANNED event naming `execution_target=cloud`): NOT_RUN — needs a release.
- Full unit suite and full gate: NOT_RUN (only the neighbouring suites above).

**Decisions beyond the card's letter** (all in `team/plans/execution-call-site-research-adr.md`)
- **REST `target_device` is not put to the rule.** Otherwise the cloud would overrule a device the caller named; no execution ledger row is written for that path.
- **Machine refusals keep their old sentence.** Voice speaks "'ofis' cihazı şu anda çevrimiçi değil." and REST returns the 409 text; `bulutta` with the cloud down says "Bulut şu anda çevrimiçi değil.".
- **The ledger write is best-effort for the research start only** (`ledger_required=False`; `choose` stays strict by default). Without this, 16 existing tests in `test_research_routes.py` and `test_research_control.py` failed because their SQLite fixtures have no `activity_events` table; I could not edit those files.
- **`choose` takes `research_job_id`.** Ledger rows carry the task id and `source_ref = execution:<task>:<n>`, which is how the integration test finds its own rows.
- **Event keys are prefixed** (`execution_chain`, `execution_skipped`) rather than bare `chain` / `skipped`.
- **`_attached` reads the device row's `platform`**, not a `DeviceView` (same column, no broker runtime needed).
- **`start_browser_research` gains `needs_signed_in_session`** (default False); no caller passes it yet.

**For the lead at merge**
- `test_pilot02_wiring.py` passes unchanged; it does not pin wiring's `__all__`.
- Unit and integration files must run in separate pytest processes (the unit conftest blocks real-database connections).
- `ruff format --check` flags `wiring.py` at line 83, which is code that was already there; I left it.

**Open risks**
- Unnamed research now goes to the cloud ahead of the session's own machine whenever the cloud is online.
- With two online `owner_chrome` machines, registry order decides, not session affinity.
- `research_browser="owner"` on the cloud device and the plan's serial-fetch clamp are untouched.
- The workflow's replay re-select still ranks all views by health.
- My early integration runs left 5 `task.failed` notification rows in the dev database; I deleted exactly those and the test now cleans up its own.
