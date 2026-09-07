---
name: worktree-python-env
description: Worktrees have no .venv; run the main repo's venv python with cwd set to the worktree's services/api
metadata:
  type: project
---

Git worktrees under `.claude/worktrees/` do **not** get their own `services/api/.venv`. The only virtualenv is in the main checkout at `E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\services\api\.venv`.

Run it with the worktree as the working directory and `import app` resolves to the **worktree's** code, not the main repo's (verified: `app.__file__` points inside the worktree):

```
cd <worktree>/services/api
E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\services\api\.venv\Scripts\python.exe -m pytest tests/unit -q
```

**Why:** the machine's C: drive has very little free space, so creating a venv per worktree is not viable, and the system `python` on PATH is 3.14 while the project pins `>=3.12,<3.13`. Building a fresh venv per agent would also cost minutes per task.

**How to apply:** in any worktree-isolated task, use that absolute interpreter path for `pytest`, `ruff check`, `ruff format` and ad-hoc scripts rather than `python`, `uv run`, or creating a venv. Related: [[machine-tool-paths]]
