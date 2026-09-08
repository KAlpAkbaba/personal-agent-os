---
name: m21-mail-calendar-security-review
description: Findings from the M21 Mail and Calendar security review (git diff c3dfd02..0ac8d67 -- services/api/app/mail, app/calendar, app/actions/confirmation_gate.py, voice tools/intents, apps/web cockpit approval pair; merged main 0ac8d67, 2026-09-08). High - the read-back-then-confirm gate is a bare timestamp check with no distinct confirmation event, so any tool call after any prior read-back satisfies it; High - mail.send/calendar.commit state transition is a Python read-check-write with no row lock or atomic UPDATE, so concurrent confirms can double-send/double-commit; Medium - deeply nested MIME email causes a verified-live RecursionError that permanently breaks mail.inbox/search/thread for the whole folder until the message is removed elsewhere; Medium - CalDAV/ICS RRULE expansion has no iteration/window cap, so one hostile calendar event (FREQ=SECONDLY, no COUNT, old DTSTART) can make any calendar query iterate seconds-since-DTSTART many times; Low - CRLF-bearing From/Subject headers survive unsanitized into a reply draft, blocked only by EmailMessage's own hard rejection at send time (an uncaught exception, not header injection).
metadata:
  type: project
---

Reviewed M21 (Mail and Calendar), the Cloud Core half (app/mail, app/calendar,
app/actions/confirmation_gate.py, voice/realtime_sessions/tools_mail.py,
tools_calendar.py, voice/intents.py additions) and the web half (apps/web
lib/cockpit/approvals.ts, useApprovalPair.ts, uistate/mail.ts, calendar.ts,
core/panels/CockpitPanels.tsx). Spec: docs/M21_MAIL_CALENDAR_SPEC.md, ADR-0084
(+ addendum 1) in docs/DECISIONS.md. See [[m20-cloud-web-document-intelligence-review]]
for the prior milestone's method this one continues.

High -- the "read-back then confirm" gate is timestamp ordering only; there is no
distinct confirmation event. app.actions.confirmation_gate.check_gate
(services/api/app/actions/confirmation_gate.py lines 60-82) requires only
confirmed_at > read_back_at. But confirmed_at is never an owner-generated signal --
MailService.send (app/mail/service.py line 679) and CalendarService.commit
(app/calendar/service.py line 585) both compute it as now = _now() at the moment
send()/commit() happens to execute, then feed that same now into the gate. And
read_back_at is set unconditionally and synchronously at PREPARE time (draft_reply/
draft_new/edit_draft/propose all set it to "now" the instant they run) -- so once any
draft/proposal exists, read_back_at is essentially always populated. The net effect:
the ordering check reduces to "the row is still prepared, and some non-zero time has
passed since it was prepared" -- which is true of ANY subsequent call to send()/commit(),
regardless of what triggered it. Confirmed by the test suite itself
(services/api/tests/unit/test_mail_service.py _prepared_draft(db, read_back_at=...)
then a direct service.send(...) call, lines ~171-235) -- there is no code-level concept
of a separate "confirmation" object, token, or session binding anywhere. The deterministic
intent router (voice/intents.py lines ~2646-2664) resolves MAIL_SEND/CALENDAR_COMMIT
"on vocabulary alone" (the code's own comment) and is NEVER consulted by the tool
dispatcher (voice/realtime_sessions/tools.py default_registry()) to gate which tool the
model may actually call -- resolved-intent fields only feed ctx.context["last_utterance"]
for argument resolution (mail_ref/calendar_ref), never an allow/deny decision on the
NEXT tool call. So the sole enforcement that "the owner's own confirmation turn, AFTER
hearing the read-back, in this exchange" really happened is the tool description's prose
instruction to the model ("Sahip taslagi duymadan bunu ASLA cagirma") -- soft, not code.
Concretely: if a model, for any reason (bug, or content-injected behavior from a hostile
mail body it just read via mail.read/mail.thread), calls mail.draft (or even just
mail.read_draft) and then mail.send within the same agentic turn with no intervening
owner utterance, the gate passes. This is also why there is no session-identity check
anywhere on read_back_at/confirmed_at (MailDraftRow/CalendarProposalRow,
app/mail/models.py, app/calendar/models.py, carry no session_id column at all) -- the REST
confirm routes deliberately reuse the same gate cross-channel (ADR-0084 addendum 1 point
5, intentional: the Cockpit shows the draft on screen as its own read-back), which is a
defensible design choice, but it rides on the same underlying gate that voice-to-voice
relies on with no distinct-event requirement either. Fix: give the gate a real
confirmation signal independent of "time has passed" -- e.g. a nonce/token minted at
read-back and consumed at confirm, or requiring the tool dispatcher to check that the
CURRENT turn's resolved intent is actually MAIL_SEND/CALENDAR_COMMIT before invoking the
handler (the router already computes this per-turn; nothing wires it into dispatch).

High -- no atomic state transition; concurrent confirms can double-send/double-commit.
MailService.send (app/mail/service.py lines 668-757) and CalendarService.commit
(app/calendar/service.py lines 566-660+) both do a plain ORM read (db.get(...), no
with_for_update()), evaluate check_gate against the in-memory row.state, call the
real self._sender.send()/self._writer.create()/update(), and ONLY THEN set
row.state = SENT/COMMITTED and db.commit(). The REST routes each open their own
session per request (asyncio.to_thread, app/mail/routes.py confirm_draft, app/calendar/
routes.py confirm_proposal). Two concurrent confirm calls (a double-click in the
Cockpit, a client retry after a slow response, or a REST confirm racing a voice "Gonder.")
can both read state == "prepared" before either writes sent/committed, both pass the
gate, and both call the real provider -- a real duplicate email sent, or a duplicate
calendar write, before either transaction commits the state change. ADR-0084's own claim
("a second confirmation never sends twice") is proven only sequentially
(test_send_succeeds_exactly_once_and_a_second_confirmation_refuses,
test_mail_service.py) -- no test exercises concurrent confirms. Fix: an atomic
compare-and-swap UPDATE (UPDATE mail_drafts SET state='sent' WHERE id=:id AND
state='prepared' checked by rowcount) or SELECT ... FOR UPDATE before the gate check,
so only one of two racing requests ever reaches the real sender/writer.

Medium -- deeply nested MIME email is a verified live DoS against mail reading.
message_from_rfc822 (app/mail/providers.py lines 193-292) calls stdlib
email.message_from_bytes then .walk() with no depth bound; ImapMailProvider.
_fetch_uids fetches the full RFC822 body with no size/nesting cap before parsing.
Verified live: a ~3000-level nested multipart/mixed message (~210 KB) raises
RecursionError in .walk() (reproduced directly against the exact stdlib call used
here, Python 3.14). The tool dispatcher's generic except Exception (voice/
realtime_sessions/service.py line ~730) catches this so it does not crash the process or
session, but list_messages/search/thread have no per-message isolation -- one
poisoned message anywhere in the fetched batch aborts the WHOLE call with no partial
results, so mail.inbox/mail.search/mail.thread fail deterministically and
permanently for that folder until the message is removed via another mail client. Trivial
to trigger: any external sender can email the owner's mailbox. Fix: wrap per-message
parsing in its own try/except inside _fetch_uids so one bad message is skipped (with a
count of skipped messages) rather than aborting the batch; consider a raw-byte size cap
before parsing and an iterative (non-recursive) walk, or reject/flag messages whose MIME
nesting exceeds a small bound (RFC 5322 emails have no legitimate reason to nest hundreds
of parts deep).

Medium -- unbounded RRULE expansion is a DoS vector reachable through a hostile
calendar feed. expand_events (app/calendar/ics.py lines 219-264) walks
dateutil.rrule from the event's own DTSTART and only stops once an occurrence reaches
the query window's end -- it does NOT skip ahead to the window's start, so the
iteration cost is proportional to (number of occurrences between DTSTART and end), not
the window size. A single crafted VEVENT with an old DTSTART (e.g. 2015) and
RRULE:FREQ=SECONDLY with no COUNT/UNTIL turns ANY calendar query (calendar.agenda,
calendar.find_slot, or the conflict check inside calendar.propose) into an
effectively-unbounded loop generating hundreds of millions of occurrences. The
MAX_WINDOW_DAYS = 62 constant declared in app/calendar/providers.py line 25 is dead --
grepped the whole services/api tree, it is never referenced outside its own definition
and __all__; nothing clamps the start/end window callers pass in. Reachable via
IcsUrlCalendarProvider (a third-party ICS subscription URL is explicitly a supported,
less-trusted provider type per spec section 2 -- e.g. a public holiday calendar) or via a
compromised/malicious CalDAV server returning one such VEVENT to a REPORT query. Fix:
cap RRULE expansion by both elapsed wall-clock iterations and a hard occurrence count
(e.g. refuse/truncate past 10,000 occurrences or an event whose DTSTART predates the
window by more than N years when the rule has no COUNT/UNTIL), and enforce
MAX_WINDOW_DAYS on every caller of events/free_slots.

Low -- CRLF-bearing headers from an incoming (attacker-authored) email survive
unsanitized into a reply draft. message_from_rfc822's From-header parsing
(app/mail/providers.py lines 198-205) falls to from_email = from_header.strip()
whenever the header doesn't cleanly end in ">" -- .strip() removes only leading/
trailing whitespace, not embedded CR/LF. Verified: a folded From: header (RFC 5322
legal folding, trivially crafted by any sender) survives _decode_header_value with its
embedded CRLF intact. This from_email becomes to_json=[message.from_email] when the
owner replies (draft_reply, mail/service.py). At actual send time,
SmtpMailSender.send assigns it to msg["To"] on a stdlib EmailMessage -- verified
this raises ValueError: Header values may not contain linefeed or carriage return
characters (Python's email.policy.default hard-rejects embedded CR/LF), so no header
injection reaches the wire. But MailService.send does not catch this -- the exception
propagates uncaught, leaving the draft stuck in prepared state with no ledger row
explaining why, a graceless failure (not a boundary bypass) reachable by replying to any
sender whose original message used header folding unusually. Fix: sanitize/reject
control characters in from_email/subject at parse time (app/mail/providers.py), and
wrap self._sender.send() in MailService.send to produce a clean refusal receipt
instead of an uncaught exception.

Verified sound:
- Secrets: grepped app/mail, app/calendar, tools_mail.py, tools_calendar.py, main.py --
  password/caldav_password/mail_*_password never appear outside their own
  __init__ parameters; no receipt/ledger/_publish dict ever includes them.
  contains_secret_reference (from app.voice.intents, already established pre-M21) gates
  draft_new/draft_reply/edit_draft before any provider or DB write; corpus case
  mc.neg.secret ("Sifremi Ali'ye maille.") resolves to Intent.NONE at the ROUTER
  level (never reaches a tool at all) -- two independent layers.
- TLS: grepped for verify=False/CERT_NONE/check_hostname=False/unverified SSL
  contexts across app/mail, app/calendar -- none found. ImapMailProvider uses
  imaplib.IMAP4_SSL (default verified context); SmtpMailSender.starttls() uses
  stdlib defaults (verified context since Python 3.x); CalDavCalendarProvider/
  IcsUrlCalendarProvider use plain httpx defaults (no transport override).
- Fixture fakes: FakeImapServer/FakeSmtpServer (tests/mail_calendar_support.py) both
  bind ("127.0.0.1", 0) (ephemeral loopback-only port) and are torn down
  (shutdown()/.close()); FakeMailSender/FakeCalendarWriter are pure in-memory
  lists, never a socket. No test can reach the network.
- REST route gating: all six new routes (/v1/mail/drafts/pending|{id}/confirm|discard,
  /v1/calendar/proposals/pending|{id}/confirm|discard) added to
  test_identity_enforcement.py's PROTECTED_ENDPOINTS AND covered by the route-table
  completeness sweep (test_only_the_four_deliberate_endpoints_are_unauthenticated) --
  a future endpoint added without require_owner_session would fail that test. They use
  the full unrestricted require_owner_session (not a narrower require_scope), which
  is the stricter, not weaker, choice given "scopes narrow, never elevate."
- Web: grepped apps/web for dangerouslySetInnerHTML in CockpitPanels.tsx and the mail/
  calendar uistate files -- zero matches; subject/body/summary are rendered via plain JSX
  text interpolation. approvals.ts calls exactly the six routes named in the spec, ids
  always through encodeURIComponent, POSTs carry no body, nothing fetches a mail/
  calendar provider directly from the browser. useApprovalPair enforces one-call-at-a-
  time CLIENT-side (a UI nicety; it does not substitute for the missing server-side
  atomic transition above, since two separate browser tabs/sessions bypass it).
- Corpus: mail_calendar category cases checked directly -- mc.neg.delete_all ("Tum
  mailleri sil.") and mc.neg.secret both resolve to Intent.NONE/no tool, matching
  ADR-0084 decision 2 (no delete/move/mass action exists) and the secret-refusal rule;
  the two confirmation cases (mc.send.confirmed, mc.commit.confirmed) are the only
  ones with SIDE_EFFECTS_MAIL_SEND/SIDE_EFFECTS_CALENDAR_COMMIT and go through the
  fake sender/writer only (never a real network path, per module docstrings).
- Data at rest: mail_index stores snippet (500 chars) not full body; mail_drafts.body
  is bounded by its String(20000) column and is owner/model-authored content (not
  fetched attacker bytes) so this is an accepted scope, not a leak; attachments are
  metadata-only (filename/size/content_type, never bytes) at every layer checked
  (MailMessage.attachments, mail_calendar spec section 1 "never downloaded" is honored --
  no attachment-fetching code exists in providers.py or service.py).

Residual risk: the High "vacuous confirmation ordering" and "non-atomic double-send" gaps
are architecture-level, not incidental bugs -- fixing them requires a real confirmation
token/session-binding and a DB-level compare-and-swap respectively, not a one-line patch.
The Medium DoS items (nested-MIME recursion, unbounded RRULE) both follow the exact shape
of prior milestones' "attacker-shaped input crashes/hangs a parser with no bound" pattern
(see M20's OOXML bomb, [[m20-file-document-intelligence-review]]) -- worth checking this
pattern proactively in any FUTURE milestone that parses a third-party binary/document
format (the fix each time is a resource bound at the parse boundary, before any content
is trusted).
