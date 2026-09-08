---
name: project-m26-cloud-core-status
description: M26 Executive Autonomy backend track status — completed 2026-09-08 on worktree branch, not merged/pushed
metadata:
  type: project
---

Completed 2026-09-08 on branch `worktree-agent-a2b3a8ca8ece923c9` (commits `9b6dec0`,
`b406bc8`), in worktree
`E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\.claude\worktrees\agent-a2b3a8ca8ece923c9`.
Implements the backend-engineer half of M26 (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md, ADR-0089):
`app/executive/{spec,graph,planner,activities,workflow,service,routes}.py`, migration 0033,
EXEC_* voice intents + `executive.*` tools, UI contract v11 (`executive.run`), voice corpus's
`preceding_turns` (was `pre_turn`) generalization + 120 new executive cases.

**Why:** owner directive M26, kickoff 2026-09-08. A parallel web track owns `apps/web` and
was NOT merged into this worktree as of these commits — `test_uistate_contract_halves.py`'s
`executive_run` FAMILIES entry fails as designed until it lands.

**Full-repo `pytest tests/unit -q` result (confirmed, 1210s / ~20min real time):** 6751
passed, 2 skipped, 3 failed at `9b6dec0`. Two failures were the expected, by-design
`executive_run` contract-halves gap (above). The third — `test_voice_realtime_sessions.py::
test_create_selects_by_capability_and_returns_the_contract`, an exhaustive tool-name-set
assertion this track's own targeted `test_executive_*.py` runs never touched — was a real
miss, fixed at `b406bc8`. Re-run confirmed 36/36 in that file; the fix is small and isolated
enough that the full suite was not re-run a second time (~20 min cost) — that full green
re-confirmation is what to do FIRST next session before trusting this branch further.

**How to apply:** before touching `app/executive/*` again, re-read
[[feedback_temporal_worker_restart_test_hang]] and [[feedback_executive_workflow_scheduling_gap]]
first — both cost significant time to find. When adding a NEW voice tool family anywhere in
this codebase, grep for exhaustive tool-name-set tests (`grep -rl '"scene.inspect"' tests/`
style — hardcoded `== {...}` sets are the tell) rather than assuming
`tests/unit/test_<family>_*.py` is the complete blast radius; `test_voice_realtime_sessions.py`
and `test_uistate.py` both carry one such list per contract and are easy to miss from a
family-scoped grep alone.
