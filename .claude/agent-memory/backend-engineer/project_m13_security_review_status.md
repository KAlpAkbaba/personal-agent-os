---
name: project-m13-security-review-status
description: Status of the M13 Cloud Core independent security + verification review fix pass — branch, commit, what's done, what's still pending owner-side action.
metadata:
  type: project
---

As of 2026-09-03, applied the independent security + verification review of
the M13 (browser-research) Cloud Core pipeline in
`E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1`, on branch
`fix/m13-security-review` (branched from `main` at `ecde897`), single commit
`af7b11e`. Not pushed, not merged — this is a worktree-local branch awaiting
review/merge by whoever dispatched the task.

**What's done** (all 12 findings from that review): the memory-boundary fix
(deterministic provider no longer puts excerpt text in Finding.summary;
`remember_activity` gates summary-to-memory on provider/injection status),
`app/research/destination.py` (SSRF/DNS-rebinding-aware fetch-target
validation), `app/research/forbidden_keys.py` (Cloud-Core-side forbidden-key
scan, parity-tested against the browser worker's token list), the
provenance-gate extension (`strip_dangling_citations`, all 4 labels, never
crashes on a fabricated id), `device_id`/`command_id` now actually populated
on `SourceItem` end-to-end, dedup/syndication tests, LLM-output bounds
(`parse_synthesis_response`), a 2 MiB discovery-response cap, device-alias
conflict handling (409 on PATCH + `ambiguous_alias` selection reason), and a
real restart-mid-fetch integration test (passed against the live compose
stack).

**Verification evidence:** `services/api/tests/unit` full suite = 1927
passed, 2 skipped, 0 failed; ruff clean; the M13 browser-research integration
suite (incl. the new restart test) plus M3 research-artifact, broker
WS/restart and mobile-reference-client integration tests = 29 passed, 0
failed, run against the already-up dev compose stack (Temporal/Postgres/
MinIO on their usual local ports).

**Left untouched, out of scope for this task:** `app/voice/**` (excluded
from allowed paths even though one finding's prose mentioned a voice file —
see [[feedback-allowed-paths-override-finding-text]]); `docs/DECISIONS.md`
(not in the task's allowed-paths list, so no new ADR/decision entry was
recorded there despite CLAUDE.md's general instruction to do so — a narrower
task-specific scope override).

**How to apply:** If asked to continue, review, merge, or build on this M13
security work, start from branch `fix/m13-security-review` /
commit `af7b11e` rather than redoing the analysis. If the owner wants an ADR
recorded in `docs/DECISIONS.md` for these fixes, that still needs doing (it
was out of this task's allowed paths).
