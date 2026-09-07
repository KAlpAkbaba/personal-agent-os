# Memory Index

- [Worktree venv bootstrap and test speed](env_worktree_venv_and_test_speed.md) — `uv sync` per worktree (~2min, once); warm gates are fast (~20s / ~160s); needs PYTHONIOENCODING=utf-8 for Turkish
- [Test suite speed and tooling](feedback_test_suite_speed_and_tooling.md) — use absolute uv.exe path, never bare `python`; older slowness/hang notes superseded by the timings above
- [Pre-existing unit failure](project_preexisting_unit_failure.md) — test_identity_enforcement's allowlist fails on main (alarms audio route); verify before chasing it
- [Allowed-paths overrides finding text](feedback_allowed_paths_override_finding_text.md) — a finding naming an out-of-scope file is context, not a mandate to edit it
- [git stash / worktree gotchas](reference_git_stash_untracked_gotcha.md) — use `git stash -u` for a true baseline; never `cd`/`-C` outside the assigned worktree
- [M13 security review status](project_m13_security_review_status.md) — branch fix/m13-security-review @ af7b11e, all 12 findings applied, tests green, not merged/pushed
- [Personal Agent OS overview](project_pagentos_overview.md) — repo layout, spec-first workflow, where contracts/decisions/state live
- [PROVEN_REAL discipline](feedback_proven_real_discipline.md) — never claim PROVEN_REAL in BUILD_STATE.json without a real device/Chrome/OS run
- [Temporal activity idempotency-key reuse bug pattern](feedback_temporal_activity_idempotency.md) — a retry/reopen must use a NEW idempotency key or it just replays the old failure
- [Test commands and gates](reference_test_commands_and_gates.md) — exact uv/pnpm/docker paths, DB safety (never touch pagentos_e2e_m13), live-API-on-8001 gotcha
- [Worktree venv bootstrap and test speed](env_worktree_venv_and_test_speed.md) — `uv sync` needed per worktree; FastAPI TestClient tests are ~8-10s/test here
- [Test suite speed and tooling](feedback_test_suite_speed_and_tooling.md) — use absolute uv.exe path; TestClient-heavy tests are slow; test_health_endpoint.py can hang (env issue, not a regression signal)
- [Worktree can lag behind main](project_worktree_lag_behind_main.md) — sibling agent worktrees merge into main continuously; ff-merge before trusting "ADR doesn't exist yet"
- [Research module patterns](feedback_research_module_patterns.md) — app/research/* conventions: pure decision fns, policy-stored-once-in-plan_json, workflow.now(), field-name collision checks, min(caller,policy) ceilings
- [Pre-existing clock-resolution flakiness](project_preexisting_clock_flakiness.md) — presence/research_focus/voice_eye_tools tests race on Windows wall-clock ties; not a regression
