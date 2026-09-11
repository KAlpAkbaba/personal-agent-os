# Salvage map — preserved work at the Astra handover (2026-09-11)

Written before anything was merged, pushed or deleted. Every branch and worktree in the
repository was inventoried, every uncommitted file diffed, and each body of work classified
against `main` at `699c165`. **Nothing was deleted and nothing was discarded.**

Classification vocabulary: `USEFUL` · `SUPERSEDED` · `DUPLICATE` · `CONFLICTING` ·
`UNSAFE` · `DOC_ONLY`.

## Branches carrying commits that are not on `main`

| Branch | Commit | Classification | Evidence |
|---|---|---|---|
| `codex/astra-p0-reliability` | `429df69` (+572/−29, 12 files) | **USEFUL** | Two independent pieces. (a) `mobile/announcer.py` receipt fix — its tests were re-run at handover: **11 passed**. (b) continuous-recovery timer + transactional installer — its tests were re-run with the worktree's uncommitted round applied: **32 passed, 2 skipped**. Touches release mechanics, so risk tier 4. Handled in Phase 6. |
| `claude/gifted-banach-779aa9` | `825a095` (+551) | **USEFUL** | `evolution/risk.py` (+20) and `evolution/supervisor.py` (+18) with 306 lines of new tests and an ADR. Directly relevant to the risk classification the self-development engine needs. Committed as `wip(...) unverified`; never gated. |
| `claude/hungry-wu-063eec` | `3717cb9` (+321) | **USEFUL**, with one caution | `AppLauncher` argument handling, device protocol text, five test files. Its `state/BUILD_STATE.json` edit (+15) asserts state that was never proven — that hunk must not be taken at face value. |
| `claude/magical-borg-13226b` | `5a9f465` (+808) | **USEFUL** | `scripts/lib/AgentUpdate.ps1` (+197), `qualify-staged-update.ps1`, `install-device-service.ps1` and 230 lines of PS5.1 tests. This is the staged-update path Phase 3 is about. |

## Uncommitted work in worktrees

| Worktree | Files | Classification | Evidence |
|---|---|---|---|
| `E:\PersonalAgentOS_astra_p0_reliability` | 7 | **USEFUL** | The hardening round answering a second independent review: kernel `flock` instead of a userspace lock, inputs pinned outside release trees, exact `status=ok` + release-SHA matching, and restore-on-failed-upgrade. Coherent, not broken mid-flight — its suite passes. |
| `.claude/worktrees/agent-a504ac9b1e59cbcd5` | 11 (+1514) | **SUPERSEDED** | The branch `worktree-agent-a504ac9b1e59cbcd5` is already an ancestor of `main` (0 commits ahead). The one residual difference in `tools_native.py` is `main` being *newer*: `main` passes `root=Path(root)` to `generate()`, this tree does not. Re-applying would regress. |
| `.claude/worktrees/agent-a45638b4a16672ea8` | 3 | **SUPERSEDED** | A creative-export narrowing written to stop `"Sahneyi dışa aktar."` reaching `creative_export`. Measured on `main`: that utterance already resolves to `none`, by a different and less restrictive route. Every corpus case that expects `creative_export` already names a format, so the change is redundant; applying it would additionally narrow `"Bunu dışa aktar."`, which has no corpus case either way. |
| `.claude/worktrees/agent-a142d6c43c53bc81f` | 4 | **DOC_ONLY** | `.claude/agent-memory/backend-engineer/*` — agent notes, no product code. |
| `.claude/worktrees/agent-a79a112a10146528f` | 1 | **DOC_ONLY** | `.claude/agent-memory/windows-engineer/*` — two added lines. |
| repository root | 2 untracked | **DOC_ONLY** | `AGENTS.md` (an adaptation of `CLAUDE.md` pointing at `.Codex/agents/`) and `.codex/agents/*.toml` (11 agent definitions). No product effect. Kept untracked pending the owner's decision. |

## What was done with each

- `SUPERSEDED` bodies of work were **left exactly where they are**. They are not deleted; the
  branches remain, the worktrees remain, and this file records why they are not being
  re-applied. If any of it is wanted later the diff is still there.
- `DOC_ONLY` bodies of work were left untouched for the same reason.
- `USEFUL` work is carried into the phases that own it: Astra's two pieces into Phase 6, and
  the three `claude/*` branches into their subject phases (`magical-borg` → Phase 3,
  `gifted-banach` → Phase 9, `hungry-wu` → Phase 3/4) where each will be reviewed, gated and
  merged on its own evidence rather than as a batch.

No branch or worktree was deleted as part of this handover.
