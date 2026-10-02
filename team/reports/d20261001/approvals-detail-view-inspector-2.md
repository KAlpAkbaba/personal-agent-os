**Inspector report — approvals-detail-view** (branch `team/d20261001/worker-approvals-detail-view` @ `c8012291`, second pass)

**Pass 1 — run it**
- Approvals area: 4 files, 45/45 green. Full web suite: 121 files, 2078/2078. `tsc --noEmit` 0, oxlint 0. All match the worker's numbers. PROVEN_AUTOMATED.
- Diff: 10 files, all inside the area; no `package.json` change, no new dependency, no `dangerouslySetInnerHTML`. Tree clean before and after my runs.
- PostgreSQL / host snapshot: not applicable (no table, migration, script or container touched).
- **My mutations** (restored from backup copies, sha256 identical, 45/45 after):

| Mutation | Result |
|---|---|
| Buttons lock on `cycle_running` instead of `decisionsOpen` | RED, 2 |
| Notice ignores `decisions_open` | RED, 1 |
| Absent field treated as open | RED, 2 |
| Detay disabled by the lock | RED, 1 |
| Panel rendered while closed | RED, 1 |
| Ne placed before the benefit section | RED, 2 |
| Null text renders an empty panel | RED, 1 |
| `rel` dropped from links | RED, 1 |
| Link prefix check widened to `javascript:` | GREEN (the `URL.protocol` check still refuses it) |
| `URL.protocol` check dropped | GREEN (the prefix check still refuses it) |
| Detay `onClick` made a no-op | GREEN (no test clicks) |

- **Real run, which the worker left NOT_RUN**: `next dev` from this worktree against a mock Cloud Core, driven in headless Chromium. PROVEN_PROXY; zero page errors.
  - Three Detay buttons, all `aria-expanded="false"`, no panel at start.
  - Tab reaches Detay, Enter opens it, Space closes it; several stay open at once.
  - The panel takes its own row (672 px, the card's width) below the button; no horizontal overflow at 390 px.
  - The real `2026-10-01-stt-soniox-olcum.md` shows the "fayda örnekleri olmadan" sentence, its six sections and no `<pre>`.
  - A proposal with a benefit section shows three pairs before Ne; null text shows "sunucuya ulaşmadı".
  - `<script>` did not run and `[kötü](javascript:…)` stayed text; the only `href` was the https one.
  - With `cycle_running: true, decisions_open: true` the new sentence shows and Onayla posted `{task_id, gate: fikir, decision: approve, channel: shell}`.
  - With `decisions_open: false` Onayla is disabled, the old sentence shows, and Detay stays enabled.
  - Screenshots are in `%TEMP%\insp-real\` (`detay-three-open.png`, `detay-narrow.png`).

**Pass 2 — break it**
1. **The F3 claim is not fully true.** `parseProposal` is quadratic on leading blank lines: the title check at `proposalSections.ts:294` rescans every earlier line. Measured: 20 000 newlines 1.15 s, 40 000 4.6 s, 80 000 18.0 s.
   - Reach: the API caps a proposal file at 20 000 characters, so about 1.2 s worst case. That cost repeats on every keystroke in the reason box, because the panel re-parses on each render.
   - The prose branch of `_proposal_text` has no cap I could find in `approvals.py`.
   - The fix is one boolean plus a test. The other eleven long inputs I tried were linear (at most 1.4 s for 450 KB, mostly render time).
2. **The rule file's own shape is misread.** A proposal written as `- **Ne**: …` / `- **Faydası — örneklerle**: …` bullets, which is how `researcher.md` spells the list, renders as one flat list and tells the owner it was "written without benefit examples" although it has them. This follows the ADR's "never cut at a list item"; all six real files use `## `, so it is latent, not live.
3. **Example titles are detached from their pairs.** With a title per example (`1. Sabah özeti` with nested Bugün/Bununla, `**Örnek 1**`, `### Örnek 1`), the pairs are read but the titles appear as an orphan list after Kazanmadığımız. `*Bugün*:` in italics is not read as a label.
4. **The header lines run together.** The real proposals' `Tarih / Roadmap / Bağlı iş` lines become one paragraph ("…awaiting_owner Roadmap:…"). Cosmetic.
5. **No automated guard for the click.** Open/close is proven only by my browser run; the worker's NOT_RUN was honest.
6. **Link safety holds.** Fifteen hostile link forms gave no markup and no non-http `href`. `https://user:pw@host` is kept as a link (credential-style URL; noting only).
7. **`docs/HANDOFF.md` is not updated in these two commits.** It is outside the worker's area, so it is the lead's to do at merge. The ADR is unnumbered, as intended.

**Evidence classes**
- Parser, panel and `decisions_open` logic: PROVEN_AUTOMATED.
- Click, keyboard, layout and approve-while-running in a browser: PROVEN_PROXY (mock API, headless).
- The owner opening Detay and approving during a real cycle: READY_FOR_OWNER; it needs the sibling task to send `decisions_open`.
- The server side of `decisions_open`: NOT_RUN here; it belongs to the sibling task.

Every acceptance criterion on the card passes. Finding 1 is a real defect, but it is bounded by the server cap and unreachable with any proposal a researcher would write. Findings 2–4 are latent or cosmetic. I recommend the lead card findings 1 and 2 as follow-ups before the researcher's first real Faydası section lands.

`APPROVE`
