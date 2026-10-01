**Report: office-page, rework (out-of-area test files)**

- **sha:** `7f98647738701b92230e0007bef7954009b9155a`, pushed to `team/office-01/worker-office-page`. The worktree is clean.
- **Fix:** I moved the four test files into the task area, so `apps/web/tests/office/` no longer exists. The new location is `apps/web/app/core/office/tests/` (`fixtures.ts`, `model.test.ts`, `poller.test.ts`, `view.test.tsx`).
- **Import changes:** I changed the relative imports (`../officeApi` etc.) and the `vi.mock` path for `lib/session` to `../../../lib/session`. No test logic changed.
- **Files changed vs the previous commit:** 4, all renames inside `apps/web/app/core/office/`.

**Evidence**
- **Tests (PROVEN_AUTOMATED):** 28 of 28 pass when run with `node node_modules/vitest/vitest.mjs run --dir app/core/office`.
- **Failure during the move:** the first run after the move failed 1 of 28, in the fetch test. It fell through to the real network because the `lib/session` mock path was stale. Fixing the path made it green.
- **Gates:** `tsc --noEmit` exit 0, oxlint exit 0.
- **Mutations:** I did not re-run them. They were proven on the earlier commit and the tests are logic-identical, so I carry them over as the earlier evidence.
  - Removing the 40-line cap on the report went RED in 2 tests.
  - Mapping `returned` to `waiting` went RED in 3 tests.

**NOT_RUN**
- **Plain `vitest run`:** `apps/web/vitest.config.ts` has `include: ["tests/**/*"]`, so a plain run no longer picks these tests up. The config is outside my area. I ran them with `--dir app/core/office` instead.
- **Full web suite:** I did not re-run it after the move.
- **Browser check and PROVEN_REAL:** I never opened the page in a browser, and there is no screenshot from a real cycle (the lead takes that after release).

**For the lead at merge**
1. Add `"app/core/office/tests/**/*.test.{ts,tsx}"` to `include` in `apps/web/vitest.config.ts`, or the office tests won't run in the normal suite.
2. Add the Kokpit nav link to `/core/office`. I did not find the nav file; look where `/core/approvals` is linked, probably `apps/web/app/core/CoreControls.tsx` or `CoreBar.tsx`.
3. Add the "own drawing, no third party" entry to `docs/THIRD_PARTY_COMPONENTS.md`; the text is in `team/plans/office-page-integration.md`.
4. Number the ADR from `team/plans/office-page-adr.md` into `docs/DECISIONS.md`.
5. Update `docs/HANDOFF.md`.

**Open risks** are unchanged from the previous report.
- The model assumes `agents` holds the eight seats in contract order.
- A working seat whose `task_id` is missing from `tasks` shows its title label but an empty panel card.
