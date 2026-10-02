## Şu an üzerinde çalışılan
- Görev: `area-widen-rules` (denetleyiciden dönen ikinci tur), alan: `scripts/lib/TeamArea.ps1`, `scripts/tests/team-area.tests.ps1`, `scripts/quality-gate.ps1`, `team/plans/area-widen-rules-adr.md`
- Makine: sahibin Windows PC'si, worktree `.claude/worktrees/team/d20261002/worker-area-widen-rules`, dal `team/d20261002/worker-area-widen-rules`

## Rapor
**sha:** `7ee1e0b0454e815f4227d8e9acb8e1427cc2dbab` (pushed, origin matches, worktree clean). 3 files changed, all inside the area; `scripts/quality-gate.ps1` is untouched this round (the step is already registered).

**The three return points**
1. **Spellings.** `ConvertTo-TeamAreaPath` now takes a path only as plainly written. An entry goes to `Bad` (kept as written, never in `Files`) and `Resolve-TeamAreaRequest` refuses it when it has an empty or `.` segment, a segment ending in `.` or a space, or one of `: [ ] ;`.
   - Named cases, one each: `docs//HANDOFF.md`, `docs/./HANDOFF.md`, `docs/HANDOFF.md.`, `docs/.`, `.claude//agents`.
   - Held-file cases, one each: `app//voice/intents.py`, `app/./voice/intents.py`, `intents.py:412` give `refuse`, where the plain path gives `wait`.
   - The inspector's other spellings and junk entries (`[a], [b]`, `[[a]]`, `a; b`, a URL) are covered in two grouped cases.
   - A guard case shows ordinary paths still widen (`.github/…`, `my file.ts`, `v1.0/`, `c..d`).
2. **Protected path already inside the area beside a free path → refuse.** New named case, three area shapes. The `$asked`→`$add` mutation that survived last round is now RED.
3. **ADR.** The contract paragraph now carries the Bad rules with the reason for refusing rather than repairing, and "no bullet, quote or numbering before the key, nothing after the bracket".

**Added beyond the three points**
- A home start (`~`) is also Bad, and only one trailing slash is dropped (`docs//` is Bad).
- `* alan_disi: […]` (asterisk bullet) is still read as a request, because asterisks are stripped for bold. It is pinned by an assertion and stated in the ADR; the role-lines card should not rely on it.
- The ADR Consequences now record the inspector's notes for the lead (resolve and add in one step; the paths outside the protected list that still widen).

**Open risk for the wiring card:** `Get-TeamAreaRequest` keeps Bad out of `Files`, so resolving `Files` alone would widen the good paths of a request that also held a Bad one. The wiring must call `Resolve-TeamAreaRequest -Files (Files + Bad)`; a named case and the ADR pin this, but nothing in this card enforces it.

**RED → GREEN (PROVEN_AUTOMATED, Windows PowerShell 5.1, `-NoProfile -File`)**
- RED before the lib change: **88 passed, 11 failed, exit 1** (the eight named spelling cases, the grouped spellings case, the junk case, the Files+Bad case).
- GREEN: **99 passed, 0 failed, exit 0**.
- The protected-inside-area case and the bullet case were green on arrival, since they pin existing behaviour; the first is proven by mutation M4.

**Mutation RED** — backup copy, each restored to sha256 `6821b1ed…35dc2e`, verified by `cmp`; never `git checkout`:

| Mutation | Result |
|---|---|
| M1 protected check removed | 44 FAIL |
| M2 conflict check removed | 13 FAIL |
| M3 cap removed | 2 FAIL |
| M4 protected judged on `$add`, not `$asked` | 1 FAIL (the new case) |
| M5 segment rule removed | 11 FAIL |
| M6 `: [ ] ;` rule removed | 4 FAIL |

**Unchanged suites (PROVEN_AUTOMATED):** team-cycle 145/0, team-feed 61/0, script-syntax 148 scripts / 0 failed.

**NOT_RUN:** the full 38-step `quality-gate.ps1` (the lead runs it on the integration branch).

**Other notes**
- The Edit tool left the three files LF in the working copy (they were checked out CRLF). BOMs are intact and the commit diff is only the intended lines.
- Not done, left as the lead's decision in the ADR: extending the protected list (`CLAUDE.md`, `.claude/hooks`, `scripts/team/cycle.ps1`, …) and making pattern entries refuse a directory that holds a match.
- Nothing is wired into the cycle, so nothing is PROVEN_PROXY, READY_FOR_OWNER or PROVEN_REAL.
