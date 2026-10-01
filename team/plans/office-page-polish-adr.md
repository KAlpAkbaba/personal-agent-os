# ADR (text; the lead numbers it) - Ofis page: seats wrap, a label is cut inside its own cell

Context: the owner's screenshot of a real cycle (docs/evidence/office-page-real-cycle-2026-10-01.png,
a ~670 px column = globals.css `main` 720 px less its padding) showed the floor as four fixed 10rem
columns with `min-width: 43rem` inside a sideways-scrolling box: one rem too wide for the column, so a
scrollbar appeared and the rightmost seat's task title was cut by the scene's edge.

Decision: `.office-floor` is `repeat(auto-fill, minmax(max(7rem, calc(25% - 0.75rem)), 1fr))` with no
width of its own. The 25% floor caps a row at four seats (eight seats = 4+4, never 5+3); the 7rem
minimum lets two seats share a row on a 320 px phone (17rem of content). `.office-scroll` keeps
`overflow-x: auto`, which now only acts when one cell alone is wider than the box. `.office-seat` gets
`min-width: 0` and `.office-figure` is `width: 100%; max-width: 8rem`, so a cell can shrink below the
drawing's natural size. The label keeps its ellipsis at the cell's width and carries the full title in
its `title` attribute (only when the seat has a task); the right panel shows the full title as before.
No new class names, no model change: states, top bar, panel, approvals and the 5 s poll are untouched.

Why not truncate in the model: the width is the browser's to know; a character count would cut too
early in a wide cell and too late in a narrow one.

Evidence: tests/office/layout.test.tsx asserts the markup and reads office.css itself (no browser is
run in this suite; the picture is PROVEN_REAL only when the owner looks at the page in the next cycle).
The four-a-row cap is tested by doing auto-fill's arithmetic on the declared track minimum, the whole
`max(...)` term included: exactly four at the owner's 42rem box, never more than four from 17rem to
120rem, two at 17rem. Without the 25% term five 7rem cells fit 40.5rem and the test goes RED.

Not in this change: the card title's "tahmini USD koşu sürerken de güncellensin" has no acceptance
criterion and touches the data side, not the layout; it needs its own card.
Rollback: revert the commit; the page returns to four fixed columns.
