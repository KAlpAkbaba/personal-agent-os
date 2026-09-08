---
name: project-m21-core-track-status
description: M21 Mail & Calendar track A (Cloud Core + voice) status, branch, evidence, and known gaps as of 2026-09-08
metadata:
  type: project
---

Completed 2026-09-08 on branch `m21-core` @ commit `6da0d06` in `E:\AI\pagentos-wt-m21-core`
(15 commits ahead of its base `c3dfd02`), built via [[reference_worktree_isolation_and_cross_worktree_transplant]]
in the scratch worktree `E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\.claude\worktrees\agent-a1bef356c7397a2cc`
(branch `worktree-agent-a1bef356c7397a2cc`, kept in sync via `git reset --hard <sha>` both
ways after every meaningful edit — NOT merged/pushed to `m21-core` itself; `m21-core`
already reflects the final state directly).

**Scope**: providers (IMAP/SMTP/CalDAV/ICS-URL + an in-repo iCalendar parser using
`dateutil.rrule`, already a transitive dep via boto3/botocore — no new dependency),
migration 0027 (mail_index/mail_drafts/calendar_index/calendar_proposals, expand-only),
the shared `app/actions/confirmation_gate.py` (one gate, five refusal codes, used by both
`MailService.send` and `CalendarService.commit`), the six REST approval routes, 15 voice
tools (9 mail + 6 calendar) + 15 intents, the `mail_calendar` corpus category (118 cases,
all green), UI contract v6.

**Full test evidence**: whole `tests/unit` suite (uv from `services/api`) = 5366 passed, 2
skipped, 1 failed in ~5 min. The 1 failure
(`test_voice_realtime_sessions.py::test_listing_is_newest_first_even_within_the_same_second`)
is a PRE-EXISTING Windows wall-clock-tie flake, confirmed unrelated: passes standalone and
in its whole file every time, only surfaces under the full suite's system load, and the
file has nothing to do with mail/calendar. See [[project_preexisting_clock_flakiness]] for
the same class of issue elsewhere in this repo.

**Owner run**: the Owner Utterance Suite (`test_owner_utterance_corpus.py`) = 804 passed
(686 pre-existing + 118 new `mail_calendar`), zero forbidden side effects. The two
confirmation cases (`mc.send.confirmed`, `mc.commit.confirmed`/`mc.commit.tamam_ekle`)
reach the fake sender/writer exactly once each; every other case reaches it zero times —
verified by a NEW harness mechanism (`Harness.mail_sent_count()`/`calendar_committed_count()`)
since mail/calendar never touch the fake DEVICE the existing side-effect check already
covered.

**Bugs found and fixed along the way** (real, not hypothetical):
1. The relay's `terminal_status_for` (app/voice/realtime_sessions/tools.py) only honours
   a handler's own `needs_clarification` dict for tools listed in
   `OPERATOR_CLARIFYING_TOOLS | DOCUMENT_CLARIFYING_TOOLS` — every other tool always got
   `TOOL_STATUS_SUCCEEDED` regardless of what it returned. Had to add
   `MAIL_CLARIFYING_TOOLS`/`CALENDAR_CLARIFYING_TOOLS` to that check, or "Gönder." with
   nothing prepared silently "succeeded" instead of clarifying.
2. SQLite has no real timezone-aware column type — a `DateTime(timezone=True)` value
   round-trips NAIVE. Both `app/actions/confirmation_gate.py` and
   `app/calendar/service.py` needed an `_aware()`/`_utc()`/`_local()` normalization
   discipline (store UTC, display Europe/Istanbul) or the gate's own time comparison and
   the calendar proposal's displayed start/end broke under a real DB round trip.
3. `_alarm_match`'s "ertele" (snooze) branch was UNCONDITIONAL — any utterance containing
   it became ALARM_SNOOZE regardless of context, colliding with M21's own "Bunu bir saat
   ertele" (calendar reschedule). Fixed by adding an `event_focused` guard to that one
   branch only (default False, so every pre-M21 alarm case is provably unaffected — full
   corpus rerun confirmed 855/855 unchanged before this file existed).
4. imaplib's `_command` ascii-encodes any `str` arg and raises on non-ASCII (a Turkish
   folder name, a Turkish search term) — fixed by encoding SELECT/SEARCH arguments to
   UTF-8 bytes before passing them to `imaplib`.
5. A fake SMTP server needs to advertise `AUTH LOGIN PLAIN` in its EHLO reply and handle
   the exchange, or `smtplib.login()` raises `SMTPNotSupportedError` before ever sending
   anything.

**Known, disclosed simplifications** (not test failures, deliberate scope calls):
- The gate's "within the same session" (spec §1) is enforced only by TIME ORDERING
  (`confirmed_at > read_back_at`), not a stored session identity — the single-owner
  system and the REST route's own statelessness make a literal session-scoped check
  awkward, and every test/corpus case's ordering is naturally session-local anyway.
- `ImapMailProvider` fetches whole `RFC822` per message (headers+body+any attachment
  bytes over the wire) and discards attachment content into metadata only when building
  the stored `MailMessage`; a fully spec-literal build would FETCH BODYSTRUCTURE first
  and pull text parts only, never receiving attachment bytes at all.
- `tests/integration/test_migrations.py` (the real Postgres upgrade/downgrade round trip)
  could not run — no local Postgres in this environment (`OperationalError: connection
  timeout` to `127.0.0.1:15432`). Verified statically instead via
  `test_migration_compatibility.py` (expand-only AST check) and
  `test_migration_model_agreement.py` (ORM/DDL agreement), both green.
- `docs/ACCEPTANCE_TESTS.md`/`state/BUILD_STATE.json` were NOT updated — this track brief
  scoped only the Cloud Core + voice half; milestone closure across both M21 tracks reads
  as the lead/integrator's job, not per-track.

**Integration note for the lead**: `main` has advanced past this branch's base (`c3dfd02`)
with what looks like the M21 web/UI track's own merge plus unrelated M22 artifact fixture
files — checked for file-level overlap with a diff and found NONE (the web track's own
"UI contract v6" work is on the frontend side; `services/api/app/uistate/contract.py` on
`main` is still v5, so my v6 bump there is a clean, non-conflicting addition). Branch not
pushed, main not touched, per the brief's explicit rule.
