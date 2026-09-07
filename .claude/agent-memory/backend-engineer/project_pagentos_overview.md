---
name: project-pagentos-overview
description: What Personal Agent OS is, its stack, and where the canonical specs/tests live — read this first on any task in this repo.
metadata:
  type: project
---

Personal Agent OS (repo root `PersonalAgentOS_Claude_Autonomous_Build_Package_v1`) is a
single-owner personal agent system: Cloud Core (FastAPI + PostgreSQL + Temporal, `services/api`),
a Windows Device Service + Session Companion + Browser Worker (`services/browser`,
`devices/windows-agent`), and a Next.js web client (`apps/web`). Turkish (`tr-TR`) is
first-class in all owner-facing UI/copy.

**Why:** the project is mid-M13 (real browser research pipeline). Contracts are written down
BEFORE code: `docs/M13_RESEARCH_SPEC.md` (Cloud Core shapes) and
`packages/protocol/BROWSER_CAPABILITIES.md` (device/worker shapes) are the source of truth —
read the relevant `§` sections named in a task before touching research/browser code.
`docs/DECISIONS.md` is an append-only numbered ADR log; `state/BUILD_STATE.json` tracks
milestone/feature status (`BUILT_AND_REVIEWED` vs `PROVEN_REAL` — never claim `PROVEN_REAL`
without an actual real-device/real-Chrome run, per [[feedback-proven-real-discipline]]).

**How to apply:** on any research/browser task, read the spec `§` sections named in the prompt
FIRST, then the existing implementation files, before writing code — the spec is normative,
existing code is the current (possibly incomplete) implementation of it. Work usually happens
in a dedicated git worktree under `.claude/worktrees/agent-*`, on a branch named
`worktree-agent-*`, branched from a specific `main` commit named in the task.
