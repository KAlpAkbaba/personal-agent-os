# Inspector report: office-page-polish @ `f56f9310`

**Verdict in one line:** the change works in a real layout run and meets the card's acceptance, but the "at most four a row" rule can be deleted without any test failing, so it goes back for one test.

**Pass 1 — run it** (clean tree, worker's commit touches 4 files, all inside the area)
- **Office suite:** 5 files, 37/37. **Full web suite:** 119 files, 2049/2049.
- **Gate:** `tsc --noEmit` exit 0; `oxlint app/core/office tests/office` exit 0.
- **Not run:** the full `quality-gate.ps1`, because this is not the integration branch.
- **Database/infra:** no table, migration, store, broker or container is touched, so there is no PostgreSQL obligation.

**My mutations** (different from the worker's; restored from a backup copy, sha256 afterwards `6392e3b4…5bcd` and `0d32660f…487b`, 37/37 again, `git status` empty)

| Mutation | Result |
|---|---|
| I1 `title=""` on every seat | RED, 1 failed |
| I2 `title={seat.name}` | RED, 2 failed |
| I3 the 25% cap removed (`minmax(7rem, 1fr)`) | **GREEN, 37/37 — survives** |
| I4 figure `width: 8rem` put back | RED, 1 failed |
| I5 `min-width: 43rem` put back beside auto-fill | RED, 1 failed |
| I6 label `max-width: 100%` removed | RED, 1 failed |

**Real layout run** (covers the worker's NOT_RUN)
I rendered the real `OfficeView` (8 seats, 90-character title on worker-1) with `globals.css`, `core.css` and `office.css`, and measured it in Playwright's `chrome-headless-shell`. No window opened and 0 processes were left.

| Content column | Rows | Cell width | Horizontal scrollbar |
|---|---|---|---|
| 672 px (the owner's screenshot width) | 4+4 | 153 px | none |
| 639 px | 4+4 | 145 px | none |
| 552 px | 4+4 | 123 px | none |
| 432 px | 3+3+2 | 128 px | none |
| 272–352 px (phone) | 2+2+2+2 | 118–158 px | none |
| 257 px and below | 1 per row | 153–233 px | none |

- At every width, 0 labels fall outside their cell or the floor; the long label is truncated with an ellipsis and carries the 90-character `title`.
- With worker-1 selected, the panel shows the full title, wrapped.
- The figure shrinks with its cell (108 px in a 118 px cell).
- Screenshots: `%TEMP%\inspector-office\office-702.png` and `office-500.png`.

**Pass 2 — findings**
1. **Untested rule (the reason for RETURN).** The stylesheet comment, the ADR and the report all claim "four a row at most, never 5+3", and I3 shows nothing tests it. At the owner's own width, five 7rem cells fit (608 px ≤ 648 px), so dropping the cap turns the owner's screen into 5+3 with every test green. The phone test's regex reads only the first rem number and skips the `max(...)` term.
2. **Phone margin is 12 px (note).** Two a row holds at a 272 px content column, which is a 320 px phone with overlay scrollbars. A 320 px desktop window with a classic 15 px scrollbar gets one seat per row, still with no horizontal scroll.
3. **USD half of the card title is not done.** "Tahmini USD koşu sürerken de güncellensin" has no acceptance and the worker flagged it. The lead should cut a separate card or drop it from the title; it must not be closed as delivered.
4. **Touch screens.** A `title` tooltip is hover-only, so on touch the panel is the only place to read the full title, as the worker said.
5. **Nothing else found.** No contract drift, no secrets or paths, no logging or privacy surface, no cost on the CPX32; rollback is a one-commit revert.

**Evidence classes**
- Label markup, `title` attribute, full title in the panel: PROVEN_AUTOMATED.
- Wrapping, no scrollbar, 4+4 at 672 px, two a row on a phone: PROVEN_PROXY (headless Chromium on static markup with the real CSS, not the deployed page).
- "Never 5+3": measured by me, but NOT covered by any test.
- The owner's look at `/core/office` in the next cycle: READY_FOR_OWNER.

`RETURN (1. add a test that goes RED when the 25% cap is removed from .office-floor — assert the calc(25% - gap) term or do the arithmetic that five minimum cells fit a 40.5rem floor without it — and prove it with mutation I3 and sha256; 2. lead: the USD part of the card title needs its own card or removal, it is not delivered here)`
