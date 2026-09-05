# Memory Index

- [Worktree venv bootstrap and test speed](env_worktree_venv_and_test_speed.md) — `uv sync` needed per worktree; FastAPI TestClient tests are ~8-10s/test here
- [Test suite speed and tooling](feedback_test_suite_speed_and_tooling.md) — use absolute uv.exe path; TestClient-heavy tests are slow; test_health_endpoint.py can hang (env issue, not a regression signal)
