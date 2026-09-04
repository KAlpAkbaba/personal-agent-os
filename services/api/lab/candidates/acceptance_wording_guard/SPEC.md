# Acceptance Wording Guard — candidate spec

Status: **SHADOW_READY**. Not deployed, not wired into the application, CI or the
quality gate. Only the owner can move it past this point.

## The problem, from the record

Two qualification runs have now been lost to the same defect class: an
acceptance check that depended on *generated natural-language wording* instead
of on structure the system can prove.

1. **2026-09-04 — ADR-0051 addendum 2.** `owner-explain.ps1` started the web
   voice shell fire-and-forget, slept eight seconds and then waited for a
   realtime session that could not exist. The harness failed with
   `"no NEW realtime session since this script started"`. The product was
   working; the harness was not. Recorded as a qualification-harness defect.
2. **2026-09-05 — ADR-0051 addendum 4.** The check
   `speech_head -like "Efendim, son ara*"` failed a **working** system: the
   briefing had been deliberately made shorter and better worded the same day.
   Everything the owner cared about — the ledger event ids, the research job,
   the counts, the release, the policy version — was correct.

The decision recorded from (2) is the rule this candidate enforces:

> **Generated wording is never acceptance evidence.** Turkish paraphrasing is
> free; an unsupported claim is not.

The rule exists in `docs/DECISIONS.md` and in the compiled experience lesson.
Nothing checks it.

## What the candidate does

Scan the repository's acceptance surfaces and report assertions whose pass/fail
outcome depends on the exact words a generator chose.

Surfaces (defaults, root-relative):

- `scripts/` — every `*.ps1`/`*.psm1`, which is the owner's commands plus the
  `scripts/tests/*.tests.ps1` regression harness;
- `services/api/tests/` — every `*.py`.

Detection is **subject-driven, not literal-driven**. A finding requires

1. a subject expression naming a generation surface, and
2. a literal classified as prose rather than a machine token.

`STRONG_SURFACES` (`speech`, `speech_head`, `narration`, `briefing`,
`factual_summary`, `spoken`, `utterance`, `explanation`, `tts`, …) produce the
`generated_wording_assertion` rule. `WEAK_SURFACES` (`summary`, `text`,
`answer`, `reply`, `response`, `sentence`, `transcript`, …) produce
`possible_wording_assertion`. Identifier parts count, so `speech_for(...)` and
`briefing_service` match.

One subject-independent heuristic, `turkish_prose_assertion`, fires on a
literal that carries Turkish-specific letters and is at least 4 words / 20
characters. It is deliberately `low` severity and excluded by the default
`min_severity`; see NOTES.md for the measurement that put it there.

### Explicit non-findings

- Exact equality against a short fixed machine token: `"PASS"`,
  `"insufficient_valid_findings"`, `"evolution.shadow_ready"`, `"0.4.0"`,
  `"cloud_core"`. One word of token characters is never prose.
- Structural patterns: any literal containing regex metacharacters
  (`^ $ [ ] ( ) | + { } \`) is a pattern, not a sentence.
- A subject that holds the **contents of a file** (assigned from
  `ReadAllText`, `Get-Content`, `.read_text()`, `open(...)`). Grepping a script
  for its own assertion messages is not a wording assertion.

### Severity depends on where the assertion lives

ADR-0051 addendum 4 is about *acceptance* evidence. A qualification check that
fails on phrasing costs the owner a real session; a unit test of the sentence
renderer that pins its output is closer to that renderer's specification. So a
finding under `scripts/` keeps its rule severity, and a finding under a
`tests/` tree is demoted one step. The `context` field says which applied.

### Allow-list

    # wording-guard: allow <reason>

on the finding's line or the line directly above. An allow with no reason is
honoured but reported with `allow_without_reason: true`, because an allow-list
whose entries need no justification is a mute button.

## Output

A JSON report (or one line per finding) carrying, per finding: `path`, `line`,
`surface`, `context`, `rule`, `severity`, `subject`, `operator`, the literal's
**shape** (`kind`, `words`, `chars`, `non_ascii`, `turkish`, `wildcard`, a
`sha256:` prefix) and a suggested structural alternative. Plus `allowed`,
`suppressed`, `skipped`, `files_scanned`, `bytes_scanned` and `duration_s`.

**The matched literal is never emitted.** The subject is rendered as an
identifier path, never as source text, and is forced to printable ASCII.

## Non-goals

- It does not edit anything and offers no `--fix`.
- It is not wired into `scripts/quality-gate.ps1` or CI. Whether a checker like
  this should gate a build is an owner decision, and the measured
  false-positive behaviour (NOTES.md) should inform it.
- It does not judge whether a *product* sentence is good. It only asks whether
  a *check* depends on one.

## Acceptance criteria

1. Detects the PowerShell shape `$speech_head -like "Efendim, son ara*"`.
2. Detects the Python shape `assert "<prose>" in speech`.
3. Does **not** report `-eq "PASS"`, `== "insufficient_valid_findings"`, or an
   enum/dotted-token equality, on any subject.
4. Honours the allow-list comment on the line and the line above, and marks a
   reasonless allow.
5. Scans a large synthetic tree within a bounded time and never blows up on a
   pathological line.
6. Never crashes on an unreadable, oversized or binary file; skips and counts.
7. No module under `services/api/app` imports it.

All seven are asserted in `services/api/tests/unit/test_lab_acceptance_wording_guard.py`.

## Where this lives, and why not where the task suggested

The obvious home would be `services/api/app/evolution/lab/candidates/…`. That
is wrong here, and the repository already says so:
`app/evolution/sandbox.py` lists `services/api/app` in `PROTECTED_TREES` — a
sandbox root may never overlap the API's own source, because the engine must
never write generated code into the running product. Candidate code inside
`app/` is also importable as `app.*` and ships in the wheel
(`pyproject.toml`: `packages = ["app"]`).

`skills/generated/` is the other declared home (`app/evolution/runtime.py`
`PAGENTOS_EVOLUTION_SKILLS_ROOT`), but that tree is git-ignored and its README
states "nothing here is hand-written" — it is the *generator's* runtime output
root, not a place for a reviewable, committed candidate.

So this candidate lives at `services/api/lab/candidates/<name>/`:
outside `app/`, outside the wheel, outside every protected tree (a
`SandboxPolicy` constructed on it succeeds — asserted in the tests), and
tracked in git so the owner can read it before approving anything.
