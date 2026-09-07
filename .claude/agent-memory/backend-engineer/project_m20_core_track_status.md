---
name: project-m20-core-track-status
description: M20 File & Document Intelligence, Cloud Core + voice track — completed 2026-09-08 on branch m20-core (commit 50b65aa) in worktree E:\AI\pagentos-wt-m20-core, 8 commits ahead of main; not merged, not pushed
metadata:
  type: project
---

**Completed 2026-09-08.** Picked up an interrupted prior agent's uncommitted WIP for the
Cloud Core + voice half of M20 (`app/documents/*`, `tools_documents.py`,
`documents_support.py`, the `document_index` migration), repaired it, merged current
`main` (which had moved ahead to include BOTH the device half and the web half of M20
while this track was in flight — check `git log --oneline main` before assuming which
tracks have landed), and completed every remaining deliverable and test the brief named.

**Where the result lives:** branch `m20-core`, worktree `E:\AI\pagentos-wt-m20-core`,
commit `50b65aa` (8 commits ahead of `main`). Not merged into `main`, not pushed — per the
task's own instruction, that is someone else's (the integrator/lead's) call once the
device/web/core tracks are all reconciled.

**Real bugs found and fixed in the recovered WIP (all with regression tests):**
- `app/voice/intents.py`: a module-level `frozenset` construction referenced
  `_DEICTIC_WORDS` before its definition in file order → `NameError` on import, i.e.
  `create_app()` could not even construct. Moved the frozenset next to the name it needs.
- `app/documents/retrieval.py`: `score_block()` weighted an exact word match the same as
  a shared-prefix match, so a CSV oracle question ("Kerem hangi şehirde?") tied the
  correct data row against an unrelated header row and a stable sort let the header win.
- `app/documents/answers.py`: `compare_blocks()`'s "the changed clause" picked the FIRST
  changed ref in document order, which was a version-stamp paragraph right under the
  title rather than the actual "Madde 3" payment clause the oracle names — fixed to pick
  the changed ref nested under the most specific heading section (by depth), never
  hardcoding any fixture's own wording.
- `app.voice.intents._previous_match`'s "az önceki = the one that just finished (i.e.
  current)" exception is RESEARCH-only (a research can "just finish"); reused verbatim
  for documents it broke the brief's own example utterance ("Az önceki sunuma geri
  dön.") — added a document-specific word match without that exception.
- `DocumentService.inspect()` always went through a full `document.extract` for a file
  not yet indexed, contradicting both the brief's `inspect -> file.inspect` mapping and
  the tool's own advertised description ("İçeriği OKUMAZ, yapıyı söyler") — rewrote to
  use the lightweight `file.inspect` capability and never touch the index when nothing is
  indexed yet.
- The fake device's `file.search`/`file.compare` shapes didn't match
  `DEVICE_PROTOCOL.md §6j` (search records carried `sha256` they shouldn't; a cross-kind
  compare fabricated a ref-by-ref block diff instead of the documented
  empty-lists/`kind: "<a>/<b>"` shape) — both fixed for fidelity even though no
  production code depended on the difference (fakes must serve the real shapes).
- Two pre-existing "exact vocabulary" tripwire tests needed one-line updates for the new
  `UiState.DOCUMENT_ANALYSIS` (contract v4->v5) and the 8 new tool names — see
  [[feedback_test_suite_speed_and_tooling]].

**Final evidence:** 63 new documents-specific unit tests (index/retrieval/answers/tools/
wiring/focus/fixtures), all 10 `truth.json` oracle questions individually parametrized and
passing, the `documents` corpus category at 148 cases (all passing, 0 forbidden side
effects), the full corpus at 686 cases (538 pre-existing + 148 new, all green), `ruff
check .` clean, and the full `tests/unit` suite at 5124 passed / 2 skipped / 0 failed.

**Not done / deferred (by design, per the task's own scope):** `docs/ACCEPTANCE_TESTS.md`
and `state/BUILD_STATE.json` were not updated for the overall M20 milestone (that is the
integrator's call once device/web/core are reconciled); the cognitive-backend prose
rewrite was not exercised (correctly out of scope — no test may depend on a real model);
`search()`'s "≥2 hits sharing one NAME -> lists paths, asks which" branch and the
folder-focus-on-ambiguous-search branch are implemented but not covered by a dedicated
test (truth.json's fixtures never produce two same-named files, only two same-TITLED
ones, which IS tested).
