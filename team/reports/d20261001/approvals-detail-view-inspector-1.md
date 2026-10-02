**Inspector report: approvals-detail-view @ `270249b7`** (branch `team/d20261001/worker-approvals-detail-view`, tree clean before and after)

**Pass 1 — run**
- Approvals tests: 25/25. Full web suite: 120 files / 2066 tests passed. `tsc --noEmit` exit 0. `oxlint app tests` exit 0, with no warning in the approvals files.
- `next build`: exit 0, `/core/approvals` prerendered. This is the first proof that the `page.tsx` / `ApprovalsList.tsx` split is valid for Next.
- The worker's commit touches 9 files, all inside the area. No secrets, no paths, no `dangerouslySetInnerHTML`, no new dependency.
- No table, migration, store or broker is touched, so the PostgreSQL rule does not apply.
- My mutations (restored from backup copies, sha256 identical after, 25/25 again):

| Mutation | Result |
|---|---|
| Refused link falls back to its raw href at the call site | RED, 3 failed |
| List locks on `cycle_running` instead of `decisionsOpen` | RED, 2 failed |
| Notice ignores `open` | RED, 1 failed |
| "Sunucuya ulaşmadı" sentence dropped | RED, 1 failed |
| "Fayda örnekleri olmadan" sentence dropped | RED, 1 failed |
| Detay disabled by the decision lock | RED, 1 failed |
| Reddet allowed without a reason | RED, 1 failed |
| Only the `^https?://` prefix test removed | GREEN — equivalent mutant, the `URL().protocol` check still refuses |

**Pass 2 — break (scratch probe, deleted afterwards)**
- All six real `team/proposals/*.md` parse to the expected seven sections, with http(s) hrefs only and no leftover `**`. None has a Faydası section, so **the benefit-pair path has never met real researcher output** — only the worker's own fixture.
- **F1 (return).** The one-line form `1. Bugün: … / Bununla: …` (also `… → Bununla: …`) puts everything into "Bugün" and renders an empty "→ Bununla:" label. This is the form the task card itself quotes. A true half pair also shows the empty label.
- **F2 (return).** Inside a `## ` proposal, a bullet such as `- **Maliyet**: 5 USD/ay` or `- **Karar**: …` cuts a new section. My probe gave `Maliyet/risk, Maliyet, Karar, Karar`. `BOLD_HEADING` accepts a list marker and fires even when the document uses `## ` headings; `**Efor**` is safe only because it is not a known name.
- **F3 (low).** Two regexes are quadratic on one long line: `HASH_HEADING` took 10.1 s on a 100 KB `## # # #…` line, and `LINK` took 7.9 s on 500 KB of `[`. Realistic proposals (3–6 KB) take under 1 ms and the text is our own researcher's, but it freezes the page thread with no bound.
- **F4 (low).** Smaller rendering gaps:
  - A Faydası section with no pairs shows a bare heading and no "fayda örnekleri olmadan" sentence.
  - Numbered lists render as `<ul>`, so "Nasıl" steps lose their numbers.
  - A `## ` line inside a code fence cuts a section.
  - A second benefit section is flattened to "Bugün: c Bununla: d".
  - All-caps `KAZANMADIĞIMIZ:` is not recognised.
  - Backticks stay literal (20–48 per real proposal).
- **Contract.** `decisions_open` exists on no branch outside `apps/web`. The server still answers 409 `cycle_running` (`approvals.py:212`) and does not send the field, so the fallback holds today. No test reads the server side; the sibling task must add one.
- **Layout.** `.detail-row` is `display:flex; flex-wrap:wrap`, so the panel is a flex item with no width rule. Not verified in a browser.
- HTML/attribute injection attempts (`"onmouseover=`, `<script>` in link text and URL) all came out escaped. Rollback is a revert of one commit.
- The report's "missing-module RED" is honestly labelled; my mutations supply the behavioural RED.

**Evidence classes**
- PROVEN_AUTOMATED: parser, panel markup, the three sentences, `decisions_open` locking, script/`javascript:` rendered as text, Next build.
- PROVEN_PROXY: real proposals render (scratch run, not a committed test).
- NOT_RUN: the Detay click and keyboard toggle, and the panel's layout — this machine has no DOM library or browser tooling in `apps/web`.
- READY_FOR_OWNER: open Detay on an idea and approve while a cycle runs (needs the sibling API task).

**RETURN (**
1. F1: split a one-line `Bugün: … / Bununla: …` (and `→ Bununla:`) into a pair, and do not render an empty "→ Bununla:" label; add tests.
2. F2: a `**Name**:` line must not cut a section when it is a list item, or when the document has `## ` headings; add a test with `- **Maliyet**:` and `- **Karar**:` under `## Maliyet/risk`.
3. F3: bound the two quadratic regexes (or cap line length), with a test that asserts the behaviour, not a stopwatch.
4. Commit a test that parses the real `team/proposals/*.md` shape, so the proxy check is not a one-off.

F4 is optional. **)**
