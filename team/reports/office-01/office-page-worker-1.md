## office-page worker report

**sha:** `82132b344a160f64134fab8f962cee7d7aa47105`, branch `team/office-01/worker-office-page`, pushed, worktree clean.

**Şu an üzerinde çalışılan (for the lead to write into HANDOFF):** office-page, area `apps/web/app/core/office`, `apps/web/tests/office`, `team/plans/office-page-*`. Machine: the dev PC. I did not touch `docs/HANDOFF.md`.

**Files:** 14, all inside the area.
- `apps/web/app/core/office/`: `officeApi.ts`, `officeModel.ts`, `officeSprites.ts`, `OfficeScene.tsx`, `OfficePanel.tsx`, `OfficeView.tsx`, `page.tsx`, `office.css`.
- `apps/web/tests/office/`: `fixtures.ts`, `model.test.ts`, `poller.test.ts`, `view.test.tsx`.
- `team/plans/office-page-adr.md` (new; the lead numbers it).
- `team/plans/office-page-integration.md`: the integrator's plan, which I copied into this branch. It had been written in the main checkout, so the worktree didn't have it.

**Third party:** none. The art is our own SVG pixel maps with CSS keyframes, so `public/office/` is empty and `package.json` is untouched.

**Tests (PROVEN_AUTOMATED):** 28 new tests in 3 files.
- **RED first:** before any implementation, all 3 files failed with "Cannot find module .../officeApi".
- **GREEN:** 28 of 28 pass, and the whole web suite passes (117 files, 2040 tests).
- **Covered:**
  - Two working workers type with their titles and `koşan ajan 2/6`.
  - A returned seat stands with the warning mark.
  - The clicked seat's panel shows card, at most 40 lines, branch, and a 12-character sha with the full value in `title`.
  - Approvals list their gate and link to `/core/approvals`; the owner seat shows the count.
  - A failed fetch keeps the last office and shows `bağlantı yok`.
  - The poll runs every 5 s and pauses while hidden (fake timers).
  - `tahmini $X` is always shown.
  - Under reduced motion there is no `office-typing` class and a static `çalışıyor` badge appears.
- **Gates:** `tsc --noEmit` exit 0; oxlint exit 0.

**Mutation proof** (backup copy of `officeModel.ts`, sha256 `03a88e91eff4fa11…` before and after each restore, no `git checkout`):
1. I removed the `.slice(0, REPORT_LINE_CAP)`. That gave RED in 2 tests: "caps the report at 40 lines" and the panel view test.
2. I mapped `returned` to `waiting`. That gave RED in 3 tests: standing pose and warning, aria-label, and the warning markup.

**Not done:**
- **PROVEN_REAL:** no screenshot during a real cycle. That needs the API task and a live cycle; the lead takes it after release.
- **Visual check:** I never opened the page in a browser, so how it actually looks is NOT_RUN. The pixel maps (`officeSprites.ts`) and CSS were not looked at. There's also no page-level test of the `page.tsx` wiring (hooks, `matchMedia`); only the poller and `OfficeView` are tested.
- **Clock-dependent assertions:** the `Bekliyor HH:MM` and start-time strings use the local time zone. The test only checks the format, not the hour.

**Open risks:**
- The model assumes `agents` has the eight seats in contract order, and the page draws whatever `agents` holds.
- A working seat whose `task_id` is missing from `tasks` shows its `task_title` label but an empty panel card.

**For the lead at merge:**
1. Add the Kokpit navigation link to `/core/office`. I did not find the nav file; look where `/core/approvals` is linked, likely `apps/web/app/core/CoreControls.tsx` (it matched that grep) or `CoreBar.tsx`.
2. Add the "Office page sprites … own drawing, no third party" entry to `docs/THIRD_PARTY_COMPONENTS.md`. The text is in `team/plans/office-page-integration.md`.
3. Number the ADR from `team/plans/office-page-adr.md` into `docs/DECISIONS.md`.
4. Update HANDOFF.
