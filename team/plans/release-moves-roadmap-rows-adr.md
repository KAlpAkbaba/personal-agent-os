# ADR draft: the JARVIS rows follow the releases (card release-moves-roadmap-rows)

Status: proposed (worker-2, cycle d20261007). The lead numbers it and moves it into docs/DECISIONS.md.

## Context

The owner, 2026-10-07: "her iş yarım yapılmış neden?". The JARVIS table in docs/ROADMAP.md was
edited by hand and the releases did not move it: at 11:45 'Knows his money' and 'Verifies what he
hears' still read MISSING although money-ledger and verify-mode were released the night before.

## Decision

`scripts/team/roadmap-rows.ps1`, run by the lead after every release on the lead branch:

- Each card is placed on a JARVIS row by its `roadmap_row`, with the İlerleme strip's rule
  (services/api/app/team/progress.py: same words, same name before a remark, leading words >= 3,
  the same ROW_ALIASES). A card that names no row is printed as `satır dışı`, never placed.
- Released = `released`, `awaiting_real_evidence`, or `done` with a 40-hex sha. Open = every state
  before that and `stopped`. `done` without a sha (merged into another card) is neither.
- A MISSING row with a released card becomes PARTIAL. A PARTIAL row with a card gets ONE
  script-owned segment at the end of its state cell, rewritten whole every run:
  `[kartlar: yayında <id> (<sha 8>), ...; kalan: <open ids> | yok]`. HAVE rows and MISSING rows
  without a released card are not touched; the rest of the cell (the hand-written text) is kept.
- HAVE is never written by the script: when a PARTIAL row has a released card, no open card and
  the strip says `staging_proven`, it prints `ÖNERİ VAR:`; the lead decides and writes it.
- The cell is a pure function of the queue, so a second run changes nothing. `-Commit` checks the
  branch BEFORE writing (refuses main/master/detached, exit 12) and makes one commit of
  docs/ROADMAP.md alone, naming the release sha; it also commits a change an earlier run wrote
  but did not commit.
- The strip is read from `GET /v1/team/office` (with `-QueueUrl`) or `-StripFile`; when it cannot
  be read, the script says so and proposes no HAVE.

## Consequences

- The row's sha is the card's own `sha` (its merge into main); the release sha is in the commit.
- Lines the script did not change are left byte for byte (LF / CRLF kept).
- Not done in this card: the `.claude/agents/lead.md` duty paragraph - the edit was refused by the
  harness's permission layer in the worker run. The text the lead should add after the
  "Releases (owner, 2026-10-01)" paragraph:

> **After every release the JARVIS rows follow it** (the owner, 2026-10-07: "her iş yarım yapılmış
> neden?"). In your first run with a shell after a release, on the lead branch:
> `powershell -NoProfile -File scripts\team\roadmap-rows.ps1 -QueueUrl <url> -QueueToken <token file> -Sha <the released 40-hex> -Commit`.
> It moves a MISSING row with a released card to PARTIAL, writes each PARTIAL row's
> `[kartlar: yayında <id> (<sha>); kalan: <open ids>]` and makes ONE commit naming the release (the
> owner's card is the approval for this one edit of ROADMAP). It never writes HAVE: an
> `ÖNERİ VAR:` line is your decision - write HAVE yourself or leave it PARTIAL and say why in the
> cycle report. `satır dışı` lists cards whose roadmap_row names no JARVIS row: fix the wording or
> accept it. A duty run without a shell writes "roadmap-rows: NOT_RUN (kabuk yok)".
