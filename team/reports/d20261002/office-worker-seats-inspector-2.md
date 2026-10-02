**Inspector report — `office-worker-seats` @ `db3d971a` (cycle d20261002)**

**Pass 1 — run it (tree clean before and after, HEAD unchanged)**
- **API office tests:** `tests/unit/test_team_office.py` is 43 passed (the report says 42). The module was imported from this worktree, not the main checkout.
- **Other readers:** `test_team_*`, `test_office01_wiring` and `test_voice_intents_team_status` are 355 passed (the report says 353). ruff check and format are clean.
- **Web:** `tests/office` is 5 files / 55 tests passed; tsc exit 0; oxlint exit 0 on the office folders.
- **Full web suite:** 119 files / 2067 tests, green in 14 of 17 runs. Each of the 3 failing runs failed exactly one test: 2 of 9 runs under deliberate 3× parallel load, and 1 solo run whose test name I did not capture.
  - The two identified failures are `tests/voice/latency.test.ts` (5 s timeout) and `tests/voice/store.test.tsx` ('closed' vs 'listening').
  - Both are outside this diff and no office test ever failed. They are flakes for the lead to card.
- **Mutations (mine, 12, different from the worker's):** all RED, each restored from a backup copy with sha256 before = after. The four file hashes match the ones the worker reported.
  - API (`office.py`, 6):
    - minimum seats 4→3: 10 failed
    - seat fields from the last run: 1 failed
    - inspector before the workers: 16 failed
    - returned tasks filled by absolute seat position: 3 failed
    - `running_agents` counting seats: 7 failed
    - capacity from seats: 3 failed
  - Web (6):
    - `OfficeScene.tsx`, badge only with reduced motion: 1 failed
    - `officeModel.ts`, one run treated as several: 6 failed
    - `officeModel.ts`, unknown seat not plain: 3 failed
    - `officeModel.ts`, label from the id: 7 failed
    - `officeModel.ts`, worker name without its number: 8 failed
    - `OfficePanel.tsx`, first run dropped from the list: 1 failed
- **Real PostgreSQL (dev stack):** the diff touches no table, migration or store, but I ran it anyway with a scratch integration test, deleted afterwards.
  - Real app, DbStore, 12 runs PUT in reverse order (6 worker, 3 inspector, lead, researcher, integrator), 23-character machine name.
  - `GET /v1/team/office` returned 11 seats in contract order, `running` true, 12/12, inspector runs in start order, six worker seats each with one run.
  - `test_team_state_postgres.py` is 3 passed alongside it.

**Pass 2 — break it**
- **Voice and page now disagree (confirmed by a direct call).** `app/team/speech.py` still counts working seats:
  - three inspector runs: page `3/6`, voice "Altı kişiden bir çalışan çalışıyor";
  - five workers and two inspectors: page `7/7`, voice "Yedi kişiden altı çalışan".
  - On main both said 1. The `speech.py` docstring promises "the voice and the page can never disagree". The file is outside the area and the worker reported it; it needs its own card in the same release.
- **Acceptance wording:** "4 workers + 1 inspector → 5 (RED before: 4)" does not catch `running_agents` counting seats; the three-inspector, per-role and seven-run tests do. The worker said so, and my mutation confirms it.
- **Top bar `5/6` page test** was green before the change (the bug was in the API). The worker disclosed this; it is now locked by a test.
- **No upper bound on seats:** `StatusRequest.runs` has no length cap, so 12 worker runs give 17 seats. The PUT is owner-authenticated; low risk.
- **Layout:** nine seats lay out 4+4+1, with the owner alone on the third row. Read from the CSS only, never drawn.
- **Page fixture:** `busyCycle` is hand-built in the web tests; no test reads the API's real output, so the two halves agree only by matching key names.
- **Compatibility and rollback:** an old page against the new API shows "worker" for `worker-4` and does not crash; the new page against an old API without `runs` is tested. The role is a `Literal` in the route, so no run can be left unseated. Pure function, no schema change: a revert is safe.
- **Hygiene:** 11 files, all inside the area; no secrets or paths; the `runs` entries carry only task id, title and start time.

**Evidence classes**
- API seat rules, counts, `runs`, order: PROVEN_AUTOMATED.
- The route on real PostgreSQL: PROVEN_AUTOMATED (dev stack).
- Page model, scene, panel: PROVEN_AUTOMATED (static markup only).
- Real browser drawing (wrapping, badge visible with motion on): NOT_RUN — no Playwright in `apps/web`; READY_FOR_OWNER.
- Owner sees four desks and the right count in a live cycle: READY_FOR_OWNER (needs the release).
- Full API unit suite (~5400): NOT_RUN here; the lead's full gate on the integration branch covers it.

**For the lead at merge**
1. Card `speech.py` to read `cycle.running_agents`, in the same release.
2. Card the two voice flakes.
3. `SeatId` is now `string` and `SEAT_NAME_TR` is no longer exported (`seatName()` replaces it); `model-policy-office-ui` builds on that.

APPROVE
