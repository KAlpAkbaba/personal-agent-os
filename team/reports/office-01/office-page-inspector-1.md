**Inspector report: office-page, rework at `7f986477`, branch `team/office-01/worker-office-page`**

**Pass 1: run**
- **Office tests:** 28 of 28 pass (3 test files plus a `fixtures.ts`), run with `--dir app/core/office`.
- **Full web suite:** 114 files and 2012 tests pass, but none of the office tests are in that count. `vitest.config.ts` has `include: ["tests/**/..."]`, so a plain `vitest run` never reaches `app/core/office/tests/`.
- **Gates:** `tsc --noEmit` exit 0, `oxlint` exit 0.
- **Scope:** every office file sits in `apps/web/app/core/office/`, plus the two plan files. The diff against `26225411` touches 14 files, all in the area. The `apps/web/tests/office/` leftovers are gone.
- **Mutations:** these are different from the worker's, except the `returned` one, which I re-ran because the worker did not. Each restore was confirmed by sha256.

| Mutation | Result |
|---|---|
| Report cap 40 → 41 | RED, 2 tests |
| `tahmini $` replaced by `~$` | RED, 2 tests |
| sha length 12 → 8 | RED, 2 tests |
| Poller ignores `hidden` on visibility change | RED, 1 test |
| Reduced motion ignored in the scene | RED, 1 test |
| Owner count badge removed | RED, 2 tests |
| `returned` mapped to `seated` | RED, 1 test |

- **Real run:** not possible here. There is no live `/v1/team/office` API and I opened no browser.

**Pass 2: break it**
- **Contract drift:** none. The types in `officeApi.ts` match the card field for field, and the path is `/v1/team/office`.
- **Seat mapping:** `buildOffice` forces the owner seat to `waiting` and shows the approval count. It trusts the API to send the eight seats in order.
- **Privacy and secrets:** no console or log calls, no storage, no secrets or paths in the code. No new dependency and no assets, which matches the plan's "own drawing" choice. The THIRD_PARTY entry text is in the plan.
- **Safety:** the page only reads and renders. CPU is low: one fetch every 5 s, with the timer cleared while the tab is hidden.
- **Poller race (minor):** two fetches in flight can resolve out of order, and an old response can overwrite a newer one. A fetch that was in flight when the tab went hidden still delivers its data. Neither is wrong enough to block.
- **Test gap (minor):** `page.tsx` is not tested. Its `onError` leaving the last view in place is the "never a blank office" claim, and no test covers it. The `offline` prop and the poller are each tested alone. Removing `setOffline` or clearing `view` in the page would stay green. The card asked for "the page with a mocked fetch", and the view test only renders the view with `offline` passed in. The wiring is thin and I read it as correct, but the claim is PROVEN_PROXY, not automated.
- **Empty panel:** a working seat whose `task_id` is missing from `tasks` opens a panel with no card. It shows "Bu koltuğun şu an bir işi yok", which is wrong while the seat is working. The worker already listed this as a risk.
- **Rollback:** delete `apps/web/app/core/office/` and the nav link.

**Evidence classes**
- **PROVEN_AUTOMATED:**
  - the model: two working workers type with their titles and `2/6`, `returned` stands with the warning mark, and `tahmini` always accompanies the USD;
  - the panel: the 40-line cap and the 12-character sha with the full value in the title;
  - approvals with their gate, and the owner's count;
  - the 5 s poll, which pauses while hidden and resumes with a fetch;
  - reduced motion, where no animation class renders and the static badge shows.
- **PROVEN_PROXY:** a failed fetch keeping the last office (view and poller tested separately, page wiring not).
- **READY_FOR_OWNER / NOT_RUN:** the browser view, phone-width layout, `prefers-reduced-motion` in a real browser, and the PROVEN_REAL screenshot from a real cycle. The lead takes that after release.

**For the lead at merge (confirmed)**
- Add `app/core/office/tests/**/*.test.{ts,tsx}` to `include` in `apps/web/vitest.config.ts`, or the 28 tests never run in the suite. This is the one that matters.
- Add the Kokpit nav link, the THIRD_PARTY entry and the ADR number.
- Update `docs/HANDOFF.md`.

**APPROVE**
