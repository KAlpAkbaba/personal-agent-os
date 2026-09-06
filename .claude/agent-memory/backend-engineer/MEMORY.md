# Memory Index

- [Worktree venv bootstrap and test speed](env_worktree_venv_and_test_speed.md) — `uv sync` per worktree (~2min, once); warm gates are fast (~20s / ~160s); needs PYTHONIOENCODING=utf-8 for Turkish
- [Test suite speed and tooling](feedback_test_suite_speed_and_tooling.md) — use absolute uv.exe path, never bare `python`; older slowness/hang notes superseded by the timings above
- [Pre-existing unit failure](project_preexisting_unit_failure.md) — test_identity_enforcement's allowlist fails on main (alarms audio route); verify before chasing it
