# ADR (watch-engine): the watch - a public page read in the cloud, told only on a change

Status: accepted (cycle d20261003). Roadmap row: Proactive - warns, briefs, watches over him
(PARTIAL), on the cloud reader of order 2b (ADR-0213/0257).

## Decision

1. **Its own aggregate, not a routine action.** `app.watch` owns a watch's own clock
   (`every_hours`, `next_due_at`): one clock for one decision. `app/routines/` is untouched.
   Two tables (migration `0066_watches`): `watches` and `watch_readings`. No column holds page
   text: a reading stores a hash, at most one number, its outcome and a short reason.
2. **Where it runs: the cloud, or nowhere.** The reader asks `rule.decide` directly with
   `JobKind.SCHEDULED`, `acting=False` (chain = cloud only), and finds the device with
   `wiring.device_for`. It does **not** call `wiring.choose`: that writes `execution.*` ledger
   rows on every call, and 240 readings a day would bury the narrative. The reading's row in
   `watch_readings` is the record. A home machine that is online is never a fallback: cloud
   down -> `unreadable` "Bulut şu anda çevrimiçi değil." and no command is sent.
   The ADR-0257 setting `routines_execution_rule_enabled` is not read (the lead's ruling).
3. **The read** goes through `DeviceBrowserGateway.fetch_page_digest(url, selector)` (new;
   `fetch_url` unchanged): one `browser.fetch_evidence` on the research profile, a session per
   reading (new idempotency keys each time), the fetch_evidence/selector CONTRACT
   (`selector` <= 200 chars, `text_sha256`, `selector_matched`). The destination is checked at
   creation, at every reading and in the gateway; a final URL on another host is checked
   again on return.
4. **Compare by hash first.** Unchanged hash -> `same`, no parse, no model, no notification.
   Numeric conditions: our own tr-TR parser (no new dependency; `fold` from
   `app/macros/naming.py` for `contains:`), then - only when the hash changed and no single
   number was found - one Haiku extraction through the existing `ChatProvider` (no tools),
   page text only inside an untrusted block, and the answer must occur in the page text
   before our parser reads it (an invented number is dropped -> `değer bulunamadı`).
   Ambiguous `19.99` is None, never a guess; nothing is stripped before parsing; NBSP is a space.
5. **Edge-triggered.** A condition notifies when it turns true, not while it stays true; the
   first reading is the baseline (`changed` never notifies there; a condition already true
   notifies once, "Şu an zaten ..."). Failures never move the baseline; a failing first
   reading is told at once, otherwise the third failure in a row, never the fourth.
   A value that cannot be found counts as a failure for these rules.
6. **Telling.** `app.notifications.service.record` (its ladder owns quiet hours), one line of
   at most 120 characters, `group_key` `watch:<id>`; a ledger row (subsystem `watch`) only for
   `watch.changed` / `watch.condition_met` / `watch.read_failed`.
7. **Loops.** `WatchRunner` (one reading in flight - a lock; the next due time counted from
   now, so a Core down ten hours reads once; a fixed per-watch offset from its id spreads the
   checks) and `PurgeLoop` (30 days, the misheard notebook's precedent). Both on
   `/v1/system/health` (`watch_runner`, `watch_purge`).
8. **Off by default.** `watch_runner_enabled=False` until browser-redirect-guard is released
   (the worker does not yet refuse a redirect hop, and DNS rebinding also applies to our
   resolve-once check). Off: watches are kept and listed, nothing is read.

## Consequences

- The hash covers the matched text (or the whole primary text); `contains:` and the model
  see only the excerpt (8000 characters) - a phrase far down a long page needs a selector.
- A page whose value cannot be found asks the model at every reading until it is found
  (the hash never becomes a baseline); bounded by `every_hours` and 20 watches.
- Rollback: the flag; or revert + `0066_watches.downgrade()` (drops both tables; readings are
  30-day data by design).
- After release: real cloud reading (Home Assistant release page; tailnet targets refused;
  10 watches for one hour on CPX32 with `infra/docker/cloud-browser/measure-memory.sh`) only
  once watch-engine and browser-redirect-guard are both released.
