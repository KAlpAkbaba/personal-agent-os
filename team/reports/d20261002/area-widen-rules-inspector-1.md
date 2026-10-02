# Denetim: area-widen-rules @ `c0145539` (inspector, d20261002)

**Pass 1 — run (all PROVEN_AUTOMATED, Windows PowerShell 5.1, re-run by me)**
- `team-area.tests.ps1`: **85 passed, 0 failed, exit 0**, matching the worker's numbers.
- Red-first: with `TeamArea.ps1` removed from a scratch copy the suite exits 1 ("…TeamArea.ps1 is not recognized"), the same failure the worker reported.
- Unchanged suites: team-cycle **145/0**, team-feed **61/0**, script-syntax **148 scripts / 0 failed**.
- The commit touches exactly the 4 files of the area. The gate step sits at `scripts/quality-gate.ps1:507`, right after the roadmap feeder, in the same shape. The library's AST shows no process, file or store command.
- Mutations: 14 of my own, different from the worker's, on a scratch copy of `scripts/lib` and the suite. **13 RED, 1 survived.** The worktree was never modified: sha256 `fde5aa02…195549` before and after, `git status` clean.
  - RED: protected exact-match only (15 FAIL); conflict one direction only (1); `returned` holders ignored (1); cap `-ge`→`-gt` (2); size `-gt`→`-ge` (1); widen early return removed (1); own id not excluded (1); first line wins (1); key case-insensitive (1); deadlock check removed (1); wait counts as a return (6); wait still widens (9); `..` accepted (2).
  - Survived: protected judged on `$add` instead of `$asked`. No case asks for a protected path that is already inside the area.
- Full 38-step gate: **NOT_RUN** (worker branch; the lead runs it on the integration branch). No table, migration, store, broker or container is touched, so there is no PostgreSQL debt and the host snapshot does not apply.

**Pass 2 — break it**
1. **A spelling defeats both the protected check and the conflict check.** `ConvertTo-TeamAreaPath` strips only a leading `./`; `Get-TeamAreaKey` does not collapse segments. Measured, each returning `widen` with `Test-TeamAreaReturnCounts` = false:
   - Protected: `docs//HANDOFF.md`, `docs/./HANDOFF.md`, `docs/HANDOFF.md.`, `docs/.`, `team/./queue.json`, `state/./BUILD_STATE.json`, `.claude//agents`, `.claude/./agents/inspector.md`, `services//recovery-supervisor/x.py`, `scripts/cloud/./backup-cloud-core.sh`.
   - Conflict: with `services/api/app/voice/intents.py` held by an `in_progress` card, the plain path gives `wait`, but `app//voice/intents.py`, `app/./voice/intents.py` and `intents.py:412` give `widen`. The last is the file:line form models really write.
   - Effect: it fails closed today, because `cycle.ps1:962` does not see `docs/HANDOFF.md` inside `[docs//HANDOFF.md]`. But the rule layer still answers "genişletildi" for a protected or held file, spends one of the two widenings on an unusable entry, and gives a return that does not count. The card says "never widened into".
2. **Junk entries become `Files`.** `[a], [b]` gives `a]` and `[b`; `[[a]]` gives `[a]`; `a; b` gives one path; `https://…` and `~/x` are accepted. This is the same normalisation gap as finding 1.
3. **The surviving mutation** (see above): "ANY asked path protected" is not pinned by a case.
4. Notes for the lead, not return items:
   - Bulleted, quoted or numbered request lines (`- alan_disi: […]`) and a trailing `.` are read as no request. The role-lines card must say "no bullet"; `Get-TeamVerdict` behaves the same.
   - `Add-TeamAreaWidening` trusts the resolution it is given: a hand-made or stale `widen` adds `docs/HANDOFF.md` and takes the count to 3. The wiring card must resolve and add in one step.
   - These all widen, though none is on the card's protected list: `CLAUDE.md`, `PROJECT_CONSTITUTION.md`, `docs/DEVELOPMENT_POLICY.md`, `.claude/hooks`, `.claude/settings.json`, `scripts/team/cycle.ps1`, `scripts/lib/TeamArea.ps1`, `scripts/quality-gate.ps1`, `services/api/alembic`, `.github`. Your decision.
   - Pattern entries do not refuse a directory that holds a match (`apps`, `devices` widen). Harmless today: the only tracked match is `.env.example`.
   - No single path for update-signature verification exists; the worker's claim holds.
- Checked and clean: Turkish `Why` text is intact under 5.1 (bytes `c4 b0`, BOM present); JSON round trip of single-element arrays; all 21 protected path entries exist in the tree; the three deviations are towards refusal and are in the ADR; no secrets or machine paths; no KVKK data.

**Evidence classes:** suite, red-first, mutations, gate step registration: PROVEN_AUTOMATED. Full gate: NOT_RUN (lead). Nothing is PROVEN_PROXY or READY_FOR_OWNER.

All fixes are inside the card's area, so no `alan_disi` line.

RETURN (1: ConvertTo-TeamAreaPath must send to Bad — never Files, and refuse in Resolve — any path with an empty or `.` segment, a segment ending in `.` or a space, or one of `: [ ] ;`, with a named case for each of docs//HANDOFF.md, docs/./HANDOFF.md, docs/HANDOFF.md., docs/., .claude//agents, and held-file spellings app//voice/intents.py and intents.py:412 not giving widen; 2: a named case for a protected path already inside the area beside a free path -> refuse, so the $asked-vs-$add mutation goes RED; 3: ADR contract paragraph updated with the new Bad rules and "no bullet, quote or numbering before the key")
