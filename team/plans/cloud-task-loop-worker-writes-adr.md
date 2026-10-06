# ADR (number: the lead's) - A cloud task writes on the owner's allow-listed sites: the worker's half (contract v1.9)

Status: accepted (card cloud-task-loop-worker-writes, cycle d20261006). Extends ADR-0213
addendum (2026-09-30, the owner's option 4) and ADR-0218.

## Context

The owner's rule: the cloud reads everywhere and acts only on the sites the owner listed.
Until now the cloud device's clamp cut every `session_open` to READ+NAVIGATE, and the
worker never looked at `cloud_allowlist.SITES` (only at the deny-list). ADR-0218 had said
acting beyond READ+NAVIGATE would come as a decision from Cloud Core's allow-list, pushed to
the worker with the job.

## Decision

1. `browser.session_open` takes `cloud_task: true` and `owner_allow_list` (Cloud Core's
   effective list at that moment). The cloud clamp lets such a session hold at most READ +
   NAVIGATE + REVERSIBLE_WRITE; without `cloud_task` it is READ+NAVIGATE exactly as before.
   A malformed entry refuses the whole session_open (a silently dropped site is a site the
   owner believes is listed); a deny-listed entry is accepted, because the deny-list is asked
   first at every write and always wins.
2. The decision is made on the worker as well as in Cloud Core's gate. Cloud Core's gate is
   the first keeper; the worker is the second, and its answer holds when the two disagree:
   only the worker knows the page's CURRENT address - after a click, a redirect or a
   script, a step the gate planned on `magaza.com.tr` may land on another site.
3. The session's list is JOINED to the seed (`browser-cloud-allowlist.json`), never put in
   its place: the seed is the floor both sides start from, the session's list what the owner
   added since. An empty union refuses every write.
4. Order on the worker: the v1.7 deny-list refusal (`denied_site`) first, unchanged; then
   the keeper, which itself asks the deny-list before the list (`deny_listed_site`), then
   the list (`not_on_owner_allow_list`, evidence `{reason, site}` with the registrable
   domain, never the URL). A click asks the keeper only when its resolved class is
   REVERSIBLE_WRITE or above; following a link is NAVIGATE and served everywhere. The v1.8
   ceiling stays on every write. A reopen only narrows the list.
5. **Pending the owner's review:** on the cloud, EXTERNAL_COMMUNICATION and HIGH_IMPACT are
   refused ALWAYS, even on a listed site - the most restrictive safe option. Widening it is
   the owner's decision (Onay Merkezi).

## Media evidence without a sound device

The cloud container has no audio device. `browser.media_status` reads the video element's
clock (`currentTime`), not sound, so headless Chromium proves "the video is playing" by a
clock that moves (test: a canvas-stream video, +0.5 s over 1.5 s). It proves playback, not
audibility; what the owner hears is the voice card's and the post-release trial's.

## Consequences

- No change for the owner's Chrome or any device session (no list = not asked).
- The status line of BROWSER_CAPABILITIES.md says v1.9 and keeps the literal
  "Status: contract **v1.8**" in a parenthesis, because `test_browser_contract_v18.py`
  (outside this card's area) asserts it; the lead may relax that assertion and drop the
  parenthesis. (Inspector's return 3: that sentence exists only for the v18 test. Fix,
  asked by ALAN_ISTEGI: v18 asserts its own annex heading `- **v1.8 (` instead of the
  status line, and the sentence leaves the document - the same move at v1.10 otherwise.)
- The worker's registrable-domain rule is a COPY of the editor's tables
  (`app/execution/allowlist_store.py`, `app/webtask/sites.py`); `test_browser_contract_v19`
  reads all of them by ast and requires them equal, table for table. A narrower copy would
  refuse every cloud `session_open` of an owner whose list holds the missing ending.
  Known, deliberate difference: the worker's `valid_site` accepts an all-digit name
  (`127.0.0.1`, the real-Chromium fixture's host) that the editor refuses; it is the
  harmless direction (the api half sends only editor-written sites).
