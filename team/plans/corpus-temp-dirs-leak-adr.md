# ADR (draft, the lead numbers it): the corpus harness removes the temp folders it made

Date: 2026-10-03. Card: corpus-temp-dirs-leak.

## Context (measurement)

The lead measured on 2026-10-02 23:00 that `%LOCALAPPDATA%\Temp` held 2 671 896 top-level entries.
2 663 542 of them used one of four prefixes: `genesis-work-` 667 912, `genesis-skills-` 667 409,
`native-corpus-` 665 780 and `creative3d-render-` 662 441. These are the four `tempfile.mkdtemp`
calls in `tests/voice_corpus/harness.py::build_harness`. Each harness made four folders, and nothing
ever removed them. The owner corpus builds one harness per case, so 2754 cases leave 11 016 folders.
On this branch, a whole-corpus run with TEMP pointed at an empty E: directory confirmed the count:
the unchanged harness left 11 016 folders, 2754 for each prefix.

## Decision

The fix is a handle owned by the harness. A caller-supplied base directory is optional.

- `HarnessTempDirs` records every folder it creates. `cleanup()` removes exactly those paths, never
  a glob on a prefix, so two live harnesses cannot touch each other's folders.
- `Harness.close()` (also `with build_harness() as h:`) runs a cleanup pass. A second call retries
  whatever the first pass had to leave.
- `run_case(case)` builds its own harness, so it closes that harness in its `finally`. This also
  covers the error path. When a caller passes `harness=...`, the caller closes it.
- `Harness.__post_init__` registers `weakref.finalize(self, temp_dirs.cleanup)`. About 40 test files
  build a harness and drop it without closing it (listed below). They clean up when the harness is
  garbage-collected, or at interpreter exit at the latest, and none of them needed an edit.
- `build_harness(temp_root=...)` puts the four folders under a caller-supplied directory, for
  example pytest's `tmp_path`. The default is still the system temp folder. If the build itself
  raises, it cleans up what it had already made.
- Removal failures:
  - Removal retries `shutil.rmtree` up to 5 times, 50 ms apart, so the wait is bounded (about 0.2 s).
  - A folder that still will not go (for example, a file open on Windows) is kept for the next pass.
  - Each kept folder is counted in the module counter `TEMP_DIRS_LEFT_BEHIND[prefix]` and logged as
    a warning.
  - A kept folder never fails a test and is never silently swallowed.
- The four prefixes are unchanged, because the owner's one-time cleanup is keyed on them.
- Not done: lazy creation of the folders (point 2 of the card). The folders are passed into
  constructors when the app is wired, so a lazy property would mean editing the wiring of four
  families. Removing the folders is the point of the card, so they are still created eagerly.

Why a handle rather than a required base directory: `build_harness()` has ~250 call sites in
~40 files outside this card's area. A required argument would have meant editing all of them.
The handle plus the finalizer cleans up for every one of them without an edit.

## What a caller must do

Nothing, for correctness. For promptness, a caller that keeps a harness should call `h.close()`
or use `with build_harness() as h:`. A caller that wants the folders in pytest's tree can pass
`temp_root=tmp_path`.

## Callers of `build_harness` (grep, tests/)

- `tests/voice_corpus/harness.py`: `run_case` builds its own harness and closes it itself.
- `stt_harness.py`: builds a harness, runs one case and drops it, so the finalizer cleans up.
  The hygiene test covers this path.
- `test_owner_utterance_corpus.py` and `test_stt_utterance_corpus.py`: go through `run_case`.
- Files that build a harness and drop it:
  - appfactory: test_appfactory_{b40,b41,routes,tools,wiring}
  - artifact: test_artifact_{b42,tools}
  - creative: test_creative3d_b44, test_creative_{b43,routes,tools}
  - calendar, mail and daily intent: test_calendar_b46, test_mail_b45, test_mail_calendar_tools,
    test_daily_intent_tools
  - documents: test_documents_{b32,b34,tools}
  - executive: test_executive_{b38,routes}
  - genesis and selfdev: test_genesis_{b36,routes,tools}, test_selfdev_b35
  - intent and memory: test_intent_intelligence_b51_session, test_memory_{b37,injection}
  - native factory: test_nativefactory_{device_build,routes}
  - news, scene and operator: test_news_routes, test_scene_{routes,tools,wiring}, test_operator_b39
  - voice and workflow: test_voice_local_research, test_voice_native_tools,
    test_workflow_start_orphans

## Product code that makes temp paths (services/api/app, read-only finding list)

- `genesis/service.py:354` `genesis-` under `sandbox.root`: `_cleanup_workspace(run)` in `finally`
  runs `rmtree` on its parent.
- `genesis/service.py:682` `genesis-readback-`: `rmtree` in `finally` (line 712).
- `evolution/pipeline.py:265` `evolution-`: `rmtree` in `finally` (line 506).
- `evolution/improvement.py:272` `evolution-improve-`: `rmtree` in `finally` (line 406).
- `evolution/rollout.py:216` `evolution-rollout-` (system temp): `rmtree` in `finally` (line 256).
- `evolution/review.py:332` `evolution-mutant-` (system temp): `rmtree` in `finally` (line 358).
- `identity/root.py:136` `.root-*.tmp` (mkstemp in the root directory): `os.replace` on success,
  `unlink` on failure.
- **FINDING for the lead:** `selfhealing/pipeline.py:253` `selfhealing-` goes under `work_root`, or
  the system temp folder when `work_root` is None. This file has no `rmtree` or other removal of
  `work_dir`, so every pipeline run leaks it, including the release copy it puts under it. That is
  a separate card; nothing was changed here.

## Evidence

- Hygiene test, whole corpus and mutations: PROVEN_AUTOMATED (see the worker report).
- The gate's time on the home PC after the owner's one-time cleanup: NOT_RUN, because the lead
  measures it at the next gate.
