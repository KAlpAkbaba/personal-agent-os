---
name: feedback-rfc822-rrule-dos-bounds
description: Empirical findings from fixing pathological-input DoS bugs in email/RRULE parsing (M21 security review M1/M2) — where RecursionError actually trips, and why dateutil.rrule cannot be bounded by API choice alone.
metadata:
  type: feedback
---

**A ~3000-level nested multipart MIME message raises `RecursionError` inside
`email.message_from_bytes` itself on this interpreter (default recursion limit 1000),
not inside `Message.walk()`** as an incident report/security review may describe it (the
finding that triggered [[project_m21_security_review_closeout]] said "raises in
`email.walk()`" — empirically, parsing already fails around depth ~900-1000, before
`walk()` is ever reached). Verified by a depth sweep: parse+walk both succeed up to depth
800, both fail by depth 1000. **How to apply:** never fix this class of bug by hardening
only `.walk()` — wrap the WHOLE `message_from_bytes`/parse call in the per-message
try/except; where exactly the stdlib's own recursion trips is an implementation detail
that can shift between Python versions and must not be relied on.

**`dateutil.rrule` has no way to jump ahead — `rrule.between()` and `rrule.xafter()` both
iterate from the rule's own DTSTART internally, same as a plain `for` loop over the
rule.** So a `FREQ=SECONDLY` event anchored decades before a requested window (the
DoS shape) cannot be bounded by switching API calls; the only fix is capping the RAW
number of occurrences the rule is allowed to generate while searching (independent of how
many land in the window), on top of separate caps on what is KEPT per-event and
per-window. Confirmed empirically: a raw-scan cap of 200,000 iterations resolves a
SECONDLY-since-2000 query against a same-day window in well under 0.5s.

**A window-clamp helper needs to run in the PUBLIC entry point, never a private
raw-fetch helper an internal `get_event`-style method also calls with its own wide
lookup window** (e.g. ±365/730 days to find one specific uid) — clamping the shared
helper would silently break that internal lookup. Split into a `_report`/`_expand`
(unclamped, used by both) and clamp only in the methods a caller's own window reaches
(`events`/`free_slots`).
