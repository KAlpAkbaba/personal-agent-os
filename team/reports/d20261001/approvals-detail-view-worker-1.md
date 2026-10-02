## Şu an üzerinde çalışılan
- Görev `approvals-detail-view` (cycle d20261001): Onay Merkezi "Detay" paneli ve `decisions_open`.
- Alan: `apps/web/app/core/approvals`, `apps/web/tests/approvals`, `team/plans/approvals-detail-view-adr.md`.
- Makine: sahibin Windows PC'si, worktree `.claude/worktrees/team/d20261001/worker-approvals-detail-view`.
- Durum: bitti, commit edildi, push edildi, ağaç temiz. HANDOFF'a dokunmadım (lead yazar).

## Report
- **sha:** `270249b7cf3a40bd6308acd830a32770ee758a37` on `team/d20261001/worker-approvals-detail-view`, pushed.
- **Files:** 9, all inside the area.
  - New: `proposalSections.ts`, `ProposalDetail.tsx`, `ApprovalsList.tsx`, three files under `tests/approvals/`, the ADR text.
  - Changed: `approvalsApi.ts` (`decisions_open?`, `decisionsOpen()`), `page.tsx` (keeps the fetch; rendering moved to `ApprovalsList.tsx` so tests can render a view).
  - `tests/cockpit/approvals-client.test.ts` is untouched: it tests the mail/calendar approval client, not this page.

**Tests added (25):** `proposal-sections.test.ts` (10) and `detail-view.test.tsx` (15), covering every acceptance line.
- **RED before:** both files failed with `Cannot find module …/ApprovalsList` and `…/proposalSections`. This is a missing-module RED, not a behavioural one.
- **GREEN after:** 25/25. Full web suite 120 files / 2066 tests passed, so the existing approvals tests stay green. `tsc --noEmit` exit 0. `oxlint app tests` exit 0, no errors, and no line mentions the approvals files.

**Mutation RED** (restored from backup copies, sha256 before/after identical, no `git checkout --`):

| Mutation | Result |
|---|---|
| Link-scheme check removed (`safeHref` returns its input) | 4 failed / 21 passed |
| `decisions_open` ignored (`return !view.cycle_running`) | 3 failed / 22 passed |
| Extra: Detay open by default | 1 failed |
| Extra: benefit rendered after the other sections | 3 failed |

**Evidence class:**
- Parser, panel markup, the three sentences, `decisions_open` locking, script/`javascript:` text rendered as plain text: PROVEN_AUTOMATED.
- All six real `team/proposals/*.md` parsed and rendered in a scratch run (deleted afterwards; all show the "fayda örnekleri olmadan" sentence, `http(s)` links only, no leftover `**`): one-off check, not a committed test.
- The click that toggles Detay, keyboard reachability in a browser, and the owner approving while a cycle runs: NOT_RUN. There is no DOM library; the card is rendered closed (the default) and open (`defaultOpen`) and each markup is asserted. PROVEN_REAL waits for the owner.

**Decisions (ADR text in `team/plans/approvals-detail-view-adr.md`):**
- A `**Heading**:` line cuts a section only for the rule's seven section names; `## ` headings cut for any title. Otherwise `**Efor**: orta` would become a section.
- `**bold**` markers are dropped and the words kept as plain text; code spans and tables are not rendered.
- Detay is shown on every idea, and on a release only when it carries a proposal. It is never disabled by the decision lock.
- I added a third sentence for "no cycle running, `decisions_open` false": "Şu anda karar verilemez; biraz sonra yeniden deneyin."

**Open risks:**
- The sibling API task (`approvals-while-cycle-runs`) is not on this branch. The contract is exercised only from the web side, with the field name taken from the card; no test reads the server's source.
- No stylesheet was touched (outside the area), so the panel is plain `h3`/`p`/`ul`/`ol` inside `detail-row`. It may want CSS after the owner sees it.
- The untitled lines under a proposal's title (Tarih / Roadmap / Bağlı iş) are joined into one paragraph.
- Bare URLs are not auto-linked, and a link whose URL contains parentheses stays as text.
