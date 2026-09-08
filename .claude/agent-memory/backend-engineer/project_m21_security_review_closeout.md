---
name: project-m21-security-review-closeout
description: Status of closing the M21 Mail & Calendar independent security review's five findings (H1/H2/M1/M2/L1) — branch, worktree, what's done, what's not.
metadata:
  type: project
---

Completed 2026-09-08 on branch `m21-secfix` @ 950b23a in `E:\AI\pagentos-wt-m21-secfix`
(base 868f899), 4 commits, **not merged, not pushed**. Built via the cross-worktree
transplant recipe: real work happened on a scratch branch in this session's own worktree,
then `git -C "E:\AI\pagentos-wt-m21-secfix" reset --hard <scratch>` moved it into place.

**All five findings fixed and tested:**
- H1 (vacuous gate) — new lifecycle `prepared -> read_back -> sending/committing ->
  sent/committed` (or back to `read_back` on failure); `app.actions.confirmation_gate.
  Confirmation`/`check_gate` bind a mutation to the SAME session + a LATER turn than its
  own read-back, AND require the router to have actually resolved `MAIL_SEND`/
  `CALENDAR_COMMIT` on the current turn (`ctx.context["last_utterance"]["intent"]`) —
  never the mere fact that `mail.send`/`calendar.commit` was called. New refusal
  `confirmation_not_owner`.
- H2 (race) — atomic `UPDATE ... WHERE state='read_back'` CAS before any provider call.
- M1 (nested-MIME RecursionError) — per-message try/except in `ImapMailProvider.
  _fetch_uids`; `_iter_parts_bounded` replaces `Message.walk()`.
- M2 (unbounded RRULE) — `MAX_RRULE_RAW_SCAN`/`MAX_OCCURRENCES_PER_EVENT`/
  `MAX_OCCURRENCES_PER_WINDOW` caps in `app.calendar.ics`; `clamp_window` finally enforces
  `MAX_WINDOW_DAYS` (declared, never used, before this fix).
- L1 (header injection / uncaught crash) — header sanitisation at parse time
  (`_sanitize_header`), `is_valid_email_address` refuses a poisoned recipient at draft
  creation, `send()`/`commit()` wrap the provider call and revert to `read_back` with a
  typed `send_failed`/`commit_failed` receipt on failure.

Migration `20260908_0029_confirmation_binding.py` adds `read_back_session_id`,
`read_back_turn`, `confirmed_by`, `last_error` to `mail_drafts`/`calendar_proposals`.
`docs/DECISIONS.md` ADR-0084 addendum 2 and `docs/M21_MAIL_CALENDAR_SPEC.md` §3 both
updated. `apps/web/` and `devices/` were never touched (out of scope; the fix is entirely
`services/api`).

**Test evidence:** mail/calendar unit suites 112 passed; full `tests/unit` 5405 passed, 2
skipped (~5m04s); voice corpus 804 cases, 804 correct, 0 forbidden side effects, HEALTHY
(120 in the `mail_calendar` category, up from 118 before this task's two new negatives).
Two file-backed-SQLite concurrent-confirmation tests prove H2 for both mail and calendar.
`ruff check .` clean.

**Not done / owed:** not merged into `main`, not pushed (brief said not to). Did not run
`alembic upgrade head` against a real Postgres instance — validated the migration
structurally (`test_migration_compatibility.py`, expand-only) and via the ORM model on
SQLite instead, consistent with how every other M21 test proves itself. The
security-reviewer agent's own memory file
(`.claude/agent-memory/security-reviewer/m21-mail-calendar-security-review.md`) was read
but not edited — that agent should confirm/close its own findings independently.
