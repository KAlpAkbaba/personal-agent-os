## ADR (number by the lead): The Onay Merkezi's "Detay" reads a proposal with its own small parser

**Context.** Owner, 2026-10-01: each waiting idea needs a "Detay" that tells him, with examples,
what the idea would bring. The page dumped `proposal_text` in one `<pre>`. The text is Markdown
written by the researcher; it reaches the browser from the Cloud Core and is not trusted markup.

**Decision.**
- `apps/web/app/core/approvals/proposalSections.ts` is a pure parser, not a Markdown renderer and
  not a dependency. It yields sections, paragraphs, lists and links; `ProposalDetail.tsx` renders
  them as React text nodes. No `dangerouslySetInnerHTML`, no HTML from the text, ever.
- A link exists only for `http://` / `https://` (prefix check AND `new URL().protocol`; the
  normalised `href` is what is rendered, `rel="noopener noreferrer"`, new tab). Any other scheme
  stays in the sentence exactly as written. Bare URLs are not auto-linked.
- Sections are cut at `## ` headings with any title (unknown ones kept under their own title).
  The older `**Heading**:` / `**Heading:**` line cuts a section ONLY for the seven names of the
  researcher's rule (Ne, Faydası…, Neden şimdi, Nasıl, Maliyet…, Kanıt planı, Karar): the same
  proposals write ordinary sentences as `**Efor**: orta`, and those must stay where they are.
- The benefit section is matched by whole name (`fayda`, `faydası`, `faydalar…` followed by a
  space, dash or the end), not by stem: "Faydalanılan kaynaklar" is not it. Its "Bugün:" /
  "Bununla:" lines (plain, bold, numbered or bulleted, with wrapped continuation lines) become
  pairs; "Kazanç:" and "Kazanmadığımız:" are read as the two closing lines; anything else in the
  section is kept and shown after them. A "Bugün" with no "Bununla" is shown as a half pair.
- Order in the panel: benefit, Ne, then every other section in the file's order (the untitled
  lines under the title - date, roadmap row - come first among those, without a heading).
- `**bold**` markers are removed and the words kept as plain text; emphasis, code spans, tables
  and nested lists are not rendered (a `###` line is a paragraph, a nested item a flat item).
- "Detay" is offered on every idea, and on a release only when it carries a proposal. It is never
  disabled by the decision lock: reading is not deciding. `aria-expanded`, and `aria-controls`
  while open; the state is per card, so several may be open.
- `decisions_open` (sibling task approvals-while-cycle-runs) is optional in the view type;
  `decisionsOpen(view) = decisions_open ?? !cycle_running` is the only reader. The buttons lock on
  `!decisionsOpen`. Three sentences: running + open -> "kararınız bir sonraki döngüde uygulanır";
  running + closed -> the old "bitene kadar karar verilemez"; not running + closed (a state the
  API may send) -> "Şu anda karar verilemez; biraz sonra yeniden deneyin." - a disabled button is
  never left unexplained.
- `page.tsx` keeps the fetch; the rendering moved to `ApprovalsList.tsx` (a Next page may export
  only its page, and the tests render the list from a view).

**Consequences.** A proposal written with constructs outside this list is still fully readable,
as text. The three ideas of 2026-10-01 have no benefit section and show the "fayda örnekleri
olmadan yazılmış" sentence until the researcher rewrites them. No stylesheet was touched (outside
the area): the panel uses plain `h3` / `p` / `ul` / `ol` inside the existing `detail-row`.
