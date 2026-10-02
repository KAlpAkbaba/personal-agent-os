## Worker report — `narrative-model-wiring` (return, point 1) — cycle d20261002

**Şu an üzerinde çalışılan** (for the lead's HANDOFF): `narrative-model-wiring`, inspector return point 1 — `_narrator_of` in `tools.py`, its test file, the ADR text — owner's dev PC, worktree `worker-narrative-model-wiring`. Now: **Yok** (done, pushed).

**Commit:** `e71b9e14bc725ba2e5ce701837ca96268a8f7264` on `team/d20261002/worker-narrative-model-wiring`, pushed (remote sha matches), tree clean. It sits on top of `f4da5aeb`.

**Files changed:** 3, all inside the area — `tools.py`, `test_narrative_model_wiring.py`, `team/plans/narrative-model-wiring-adr.md`. `config.py` is untouched in this commit.

**The fix (point 1):**
- `_narrator_of` now records `model` only when the spoken text equals the draft, or is the draft followed by `" Ayrıca başarısız: "` (the auditor's repair marker).
- The marker is a private constant in `tools.py`, because `auditor.py` holds it as an inline literal and is outside the area.

**RED → GREEN:**
- New test `test_on_a_rejected_draft_that_opens_the_rule_text_is_still_the_rule_narrators`, two cases through the real tool-call route:
  - `first-sentence`: draft = `"2 iş başarısız oldu."`, the inspector's reproduction.
  - `failures`: draft = the rule text up to `Tamamlananlar:`.
- RED before the fix, both cases: `AssertionError: assert ('model', None) == ('rule', 'audit_rejected')`.
- GREEN after: the file is 17 passed (was 15).

**Mutation proof** — `tools.py` restored from a backup copy after each, sha256 `bb69c09f…7ae3` before, after every restore and at the end:
| Mutation | Result |
|---|---|
| comparison back to `draft + " "` | RED, 2 failed (the two new cases) |
| marker drifts from the auditor's (`" Ayrıca: "`) | RED, 2 failed (both repaired-draft tests) |
| setting ignored (card mutation) | RED, 4 failed (every OFF test) |
| `narrator_reason` not recorded (card mutation) | RED, 13 failed |

**Checks:**
- `ruff check` and `ruff format --check` are clean on the three Python files.
- Related suites (`test_narrative*`, `test_explain*`, `test_voice_realtime_sessions*`, `test_assistant_chat*`, `test_config*`): 340 passed, 0 failed.

**ADR text corrected:**
- The `model` sentence now states the exact rule and why a bare prefix is not enough.
- It names the one case still open: a rejected draft that the rule text continues with that same marker, which needs a failed row whose own ledger summary contains `" Ayrıca başarısız: "`.
- The evidence count reads 17.

**Evidence classes:**
- Narrator and reason for a prefix-shaped rejected draft, on SQLite through the real app route: PROVEN_AUTOMATED.
- PostgreSQL re-probe of the new case: NOT_RUN. The stored value is the same two-word pair the inspector already saw on `jsonb`.
- Wider voice suites (all 38 `test_voice_*.py`), ledger suite, full unit suite, `quality-gate.ps1`, real Haiku model: NOT_RUN this pass.
- Switching the setting on, plus the compose line: READY_FOR_OWNER (unchanged).

**Not done, and why:**
- Inspector point 2 (the real provider reports a timeout as `chat_unavailable`): needs `app/assistant_chat.py`, outside the area — a follow-up card.
- Inspector point 3 (a settings stand-in holding the string `"false"` reads as ON): informational and not in the return list, so left as is.

**Open risks:**
- The marker literal lives in two files. Drift is caught by the two repaired-draft tests, not by a shared constant; exporting one from `auditor.py` is an out-of-area change for the lead to decide.
- The narrator is still inferred from text, because `tell()` returns text alone. Having `narrate()` hand back its `source` would remove the inference, but that is `app/narrative` and `app/explain`, outside the area.
