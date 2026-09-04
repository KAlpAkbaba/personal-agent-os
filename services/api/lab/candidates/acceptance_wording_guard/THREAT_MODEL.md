# Threat model — Acceptance Wording Guard

What could this checker itself do wrong? It reads the owner's acceptance
scripts and produces a report a person will act on, so its failure modes are
(a) lying about the code, (b) leaking what it read, (c) reading what it should
not, and (d) not terminating.

## T1 — False positives erode trust (the dominant risk)

**Why it matters most.** A checker that flags honest code is worse than no
checker: the owner learns to skip its output, and the one real finding is lost
in the noise. This has to be measured, not asserted.

Measured against this repository (231 files, defaults):

| Iteration | high | medium | low | note |
|---|---|---|---|---|
| naive literal-only rule | — | — | — | would flag every `-match "must be replaced"` in the harness |
| subject-driven, flat severity | 67 | 41 | — | 67 "high" almost all in *renderer unit tests* |
| context-aware + suppressions | **0** | 67 | 55 | 0 in the qualification surfaces |

Mitigations, each with a test:

- **Subject-driven detection.** A prose literal alone is never enough; the
  subject must name a generation surface. This is what keeps the hundreds of
  honest `$result.StdOut -match "…"` / `$_.Exception.Message -match "…"`
  assertions out of the report.
- **Machine tokens are never prose.** `classify_literal` is a pure, total
  function; `"PASS"`, `"insufficient_valid_findings"`, `"0.4.0"`,
  `"evolution.shadow_ready"` classify as `machine_token`, and anything with
  regex metacharacters as `pattern`.
- **Context-aware severity.** A wording assertion in `scripts/` (an owner
  command or its harness — where being wrong costs a real session) keeps its
  severity; the same shape in a `tests/` tree is demoted one step, because
  pinning a renderer's output in that renderer's own unit test is a different
  claim.
- **File-content suppression.** A subject assigned from `ReadAllText` /
  `Get-Content` / `.read_text()` / `open(...)` is a *source grep*, not speech.
  Without this, `scripts/tests/owner-explain.tests.ps1` — the file that exists
  to enforce this very rule — reported five findings against itself.
- **The noisy heuristic is opt-in.** `turkish_prose_assertion` is `low` and
  filtered out by the default `min_severity`, because measurement showed it
  firing on deterministic normalizer output and fixed Turkish fixtures.
- **The allow-list is honest.** `# wording-guard: allow <reason>` keeps a
  deliberate case; an allow with no reason is still honoured but reported as
  `allow_without_reason`, so the escape hatch cannot quietly become a mute
  button.

**Residual risk (accepted, stated):** false negatives. The subject gate means a
wording assertion on a variable named nothing like a generation surface
(`$out`, `$val`) is missed unless it is long Turkish prose. That is the
deliberate trade: this candidate optimises for a report the owner will read.

## T2 — Leaking what it read

The tool reads the owner's acceptance scripts, and its report may be pasted
into an issue, a commit body or a chat.

- Findings carry the literal's **shape only** — word count, character count,
  `non_ascii`/`turkish`/`wildcard` flags and a 12-hex-character SHA-256 prefix
  for correlation. Never the literal.
- Subjects are rendered from the AST as an **identifier path** (`detail[speech]`,
  `briefing_service.speech_for()`), never with `ast.unparse`. The first
  implementation did use `unparse`, and it reproduced string arguments verbatim
  — a subject came out as `normalize('1.234,56 ₺')`, putting content into a
  report that promises not to quote any, and crashing a cp1252 console on the
  way. Subjects are additionally forced to printable ASCII.
- PowerShell index expressions collapse to `[...]` unless the key is a bare
  identifier.
- Nothing is written anywhere; the tool has no output path argument.

## T3 — Reading files it should not

- Every scanned path must be under the resolved `root`; a surface that escapes
  it (built from `..`, or a symlink out of the tree) is dropped, and the check
  is in `_iter_files`, not in the caller.
- Symlinks are never followed.
- Only `.ps1`, `.psm1` and `.py` are opened at all.
- `EXCLUDED_DIRS` is matched against the path **relative to the root**. Matching
  the absolute path was a real bug: this repository is checked out inside a
  `.claude/worktrees/…` directory, so the first run silently scanned zero files
  and reported success. A checker that reports "no findings" because it read
  nothing is the most dangerous output it can produce; the file-count assertion
  in the tests exists for that reason.

## T4 — Not terminating (a regex that hangs)

- Every regex is bounded (`{0,N}`) with no nested quantifiers, so matching is
  linear in the line length; there is no catastrophic-backtracking construct.
- Lines are truncated to `MAX_LINE_CHARS` (2000) *before* matching.
- String literals are captured with a negated character class bounded to
  `MAX_LITERAL_CHARS` (400).
- Files above `MAX_FILE_BYTES` (1 MiB) are skipped and counted, never read.
- The walk stops at `MAX_FILES` and sets `truncated`.
- `_subject_path` recursion is depth-capped.
- Python parsing catches `SyntaxError`, `ValueError` and `RecursionError`.

Both bounds are asserted: a 200 000-character pathological line completes well
inside a second, and a 1 200-file synthetic tree scans inside the benchmark
bound.

## T5 — Crashing on hostile input

`_read_text` returns a reason instead of raising for a failed `stat`, an
unreadable file or a binary file (NUL byte in the first 8 KiB), and decoding
uses `errors="replace"`. A file the tool cannot read appears in `skipped`, and
the scan continues.

## T6 — Becoming an authority it was never granted

This is lab code. It is not imported by anything under `services/api/app` (a
test asserts the whole package, recursively), it is not in the wheel, it is not
in CI, and it is not in `scripts/quality-gate.ps1`. It executes no subprocess,
opens no socket and writes no file. Its only effect is a report.

Making it a gate is a separate, owner-approved decision — and on the evidence
above it should stay advisory until the `medium` backlog in the renderer unit
tests has been triaged, or the whole build would go red on 67 pre-existing
findings the moment it was wired in.
