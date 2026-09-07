---
name: preexisting-clock-resolution-flakiness
description: test_presence_eye_invalidation.py / test_research_focus.py / test_voice_eye_tools.py flake on Windows independently of any one task's changes — a clock-resolution race, not a logic bug
metadata:
  type: project
---

Running `tests/unit/test_presence_eye_invalidation.py`, `tests/unit/test_research_focus.py`
and `tests/unit/test_voice_eye_tools.py` together (or in the full suite) intermittently
fails a DIFFERENT subset of their tests on each run — reproduces with just these three
files, no other tests involved, and with nothing from any specific task's work loaded.

**Why:** these tests assert "the most recently written row wins" (idempotent read-backs,
research focus ordering, eye enable/disable receipts) from timestamps produced by
consecutive `datetime.now(UTC)` calls in the same process. Windows' default wall-clock
resolution is coarser than the gap between two such calls, so two writes can land on the
IDENTICAL timestamp; whatever secondary ordering the code falls back to then is not
chronological (e.g. a random UUID). This reproduces on a stock checkout with none of a
task's own changes loaded — confirmed by running the three files alone, repeatedly, in
this environment.

`app/operator/focus.py` (M19) hit the exact same failure mode in its own new tests and
was fixed at the SOURCE: `_next_default_selected_at()` nudges a would-be tie forward by a
microsecond using a process-wide monotonic guard, so two same-process writes are always
strictly ordered. The three pre-existing files above were left alone — fixing them was
out of scope for the task that found this — but the same fix (a monotonic nudge on the
default `now=None` path, never touching an explicitly-passed timestamp) is the right
shape if a future task owns one of them.

**How to apply:** if a `uv run pytest tests/unit -q` run on Windows shows 1-4 failures
among `test_presence_eye_invalidation.py` / `test_research_focus.py` /
`test_voice_eye_tools.py` that are NOT reproducible on a second run (different test names
fail each time), treat it as this pre-existing flakiness rather than a regression — rerun
2-3 times to confirm the instability, and check whether your own change touches any of
those three files' subsystems (presence/eye, research focus) before assuming it caused it.
See also [[preexisting-identity-enforcement-failure]] for the other known pre-existing
`tests/unit` failure.
