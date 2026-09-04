# Independent critique, and what changed because of it

Written after the implementation existed and after it had been run against the
real repository, deliberately looking for reasons the owner should **not**
approve it. Every item below is a defect that was found, not a design intention
restated. The first three were found by running the tool, not by reading it —
which is itself the argument for the "run it against the real thing before
calling it done" step.

## C1 — It reported success having read nothing (fixed)

`EXCLUDED_DIRS` was matched against each candidate's **absolute** path parts.
This repository is checked out at
`…\.claude\worktrees\agent-…\`, so `.claude` was in every absolute path and the
first real run printed:

    scanned 0 files in 0.00s: 0 high, 0 medium, 0 allowed, 0 skipped

A clean bill of health from a checker that opened no files is the single worst
output a tool like this can produce, and nothing in the design would have caught
it — the unit tests all used `tmp_path`, which has no excluded segment.

**Fix:** excludes are matched against the path relative to the root.
**Test:** `test_an_excluded_directory_is_matched_relative_to_the_root` builds the
tree under `.claude/worktrees/wt` and asserts a file is scanned; the real-repo
test asserts `files_scanned > 100`.

## C2 — It leaked the text it promised never to quote (fixed)

Subjects were rendered with `ast.unparse`, which reproduces the source verbatim
— including string arguments. A subject came out as `normalize('1.234,56 ₺')`,
which is (a) exactly the owner content the report claims to withhold and (b)
enough to crash the CLI on a cp1252 console with `UnicodeEncodeError`. Both from
one line of the real repository.

**Fix:** `_subject_path` renders an identifier path only (`detail[speech]`,
`briefing_service.speech_for()`), PowerShell index keys collapse unless they are
bare identifiers, and every subject is forced to printable ASCII.
**Tests:** `test_a_subject_is_an_identifier_path_never_source_text`,
`test_a_finding_never_carries_the_matched_string`.

## C3 — It flagged the file that exists to enforce this very rule (fixed)

`scripts/tests/owner-explain.tests.ps1` reads `owner-explain.ps1` into `$text`
and greps it for its own assertion messages — the regression test the owner
added *because of* ADR-0051 addendum 4. The guard reported five findings against
it. If a checker's first act is to accuse its own enforcement test, it will not
be read twice.

**Fix:** a subject whose root variable was assigned from `ReadAllText` /
`Get-Content` / `.read_text()` / `open(...)` holds file contents, not speech.
Those are reported in a separate `suppressed` list, so the suppression is
visible rather than silent, and it is counted only for matches that would
otherwise have been *reported* (the first version counted every comparison in
such a file, giving a meaningless "73 suppressed").
**Test:** `test_a_subject_holding_file_contents_is_suppressed_not_reported`.

## C4 — Severity did not distinguish a gate from a specification (fixed)

The first honest run reported **67 `high` findings**, nearly all in
`test_explain_engine.py`, `test_explain_learning_questions.py` and
`test_voice_explain_tools.py` — unit tests of the sentence renderers, where
pinning the output is arguably the renderer's specification. ADR-0051 addendum 4
is about *acceptance* evidence: a check that costs the owner a real
qualification session. Treating the two identically is how a checker trains its
reader to skip it.

**Fix:** `context_for(path)` classifies a finding as `qualification`
(under `scripts/`), `test` or `source`, and a `test`-context finding is demoted
one severity step. The `context` is in every finding, so the judgement is
visible and arguable rather than baked into the severity.
**Test:** the same Python shape asserts `high` in a `scripts/` path and `medium`
in a `tests/` path.

## C5 — The Turkish-prose heuristic is not good enough to be on (fixed by demotion)

The subject-independent rule fired on `normalize('31.08.2026') == "otuz bir
agustos …"` (a deterministic normalizer's exact output — legitimate acceptance
evidence, and arguably the point of that test) and on fixed two-word Turkish
fixtures like a notification title. Measured, it is the noisiest rule by a wide
margin: 55 hits, none of them the defect class.

**Fix:** minimum 4 words and 20 characters, severity `low`, and excluded by the
default `min_severity`. It is available as `--min-severity low` for a deliberate
sweep. **This is a capability reduction, chosen knowingly**: the rule is the only
thing that would catch a wording assertion on a variable named `$out`, and that
false-negative class is now accepted. Stated in THREAT_MODEL.md as residual risk
rather than hidden.

## C6 — Findings could double-count the same line (fixed)

A PowerShell line can match both the forward and reverse comparison patterns,
and a Python `Compare` chain visits pairs more than once. Deduplication is on
`(path, line, subject, literal digest)`.

## C7 — Things I could not fix, and the owner should weigh

- **The surface lists are a judgement, not a derivation.** `STRONG_SURFACES` /
  `WEAK_SURFACES` were written by reading this repository. A new subsystem that
  names its generated text something else is invisible to the guard until the
  list is extended. There is no principled way around this short of type
  information the codebase does not carry.
- **`text` is the weakest entry and earns most of the `medium` noise.** It is
  kept because the task's defect class explicitly includes `text` fields, and
  because C3's suppression removes its worst case. If the owner finds the
  `medium` list unhelpful, dropping `text` is a one-line change that would cut
  it roughly in half.
- **67 pre-existing `medium` findings mean this must not be a gate yet.**
  Wiring it into `scripts/quality-gate.ps1` today would turn the build red on
  work nobody has agreed to change. It should stay advisory until that backlog
  is triaged — an owner decision, recorded here so approving the candidate is
  not accidentally approving a gate.
- **The benchmark bound is generous on purpose.** 1200 synthetic files against
  a 20-second ceiling, when the real 232-file repository scans in ~0.33s. That
  catches an algorithmic regression (a quadratic scan, a backtracking regex) and
  will not catch a 2× constant-factor slowdown. A tighter bound would be a
  flaky test on a loaded machine, which is a worse failure.
- **The lifecycle run uses an ephemeral store.** `run_lifecycle.py` exercises
  the real service, authority kernel, transition table, ledger writer and
  UI-state bus, but against in-memory SQLite. Writing lab bookkeeping into the
  owner's PostgreSQL would need `Grant.WRITE_PRODUCTION_DB`, which is a
  production grant the lab does not hold. So the *law* exercised is real and the
  *store* is not, and no claim is made that a row exists in production.

## C8 — What the run revealed about `app/experience` (reported, not fixed)

The lifecycle run drove the real Experience Compiler over the two backfilled
incidents. It compiled two lessons, and both came out as the **generic**
pattern: *"Recurring voice failure (incident.opened)"*, with the generic
statement that "the specific mechanism is not yet well enough understood to
generalize". The specific, valuable lesson — *generated wording is never
acceptance evidence* — exists only in `docs/DECISIONS.md`; the compiler has no
pattern for it, because its two named patterns are the research-evidence leak
and the deployment-provenance ones.

So the sentence "the lesson is already compiled by app/experience" is, on this
evidence, **not true yet**. The opportunity cites the compiled lessons honestly,
as the generic rows they are. Teaching the compiler this pattern is separate
work and belongs to whoever owns `app/experience`.

## Verdict

Approvable as a **read-only, advisory** tool. Not approvable as a quality gate
in its current state, for the reason in C7. The value is concentrated in the
`qualification` context, where it currently reports **zero** findings — the two
historical defects were genuinely fixed — so its near-term worth is as a
regression guard against the third occurrence rather than as a cleanup list.
