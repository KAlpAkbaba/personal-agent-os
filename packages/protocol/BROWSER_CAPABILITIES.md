# Browser capabilities over the device protocol (M13, ADR-0050)

Status: contract **v1.2** — binding for `services/api` (Cloud Core), `devices/windows-agent`
(Session Companion) and `services/browser` (Browser Worker). Change it here first.

- v1 (M13, ADR-0050): the family, sessions, risk classes, the error taxonomy, §3's payloads.
- v1.1 (2026-09-04, ADR-0050 addendum): the persistent research session, Google through the
  real UI, the owner verification handoff (§3a), search schema 3.
- **v1.2 (2026-09-07, M18.3 Track B, ADR-0073): the alarm media family** — `browser.media_play`,
  `browser.media_volume`, `browser.media_status`, `browser.media_stop` (§1, §3b), the dedicated
  `alarm` profile and the `media` session kind (§2). Additive: every v1.1 name, payload and
  result is unchanged. The worker advertises `contracts["browser.media"] = 1`; a consumer
  checks it BEFORE planning a media wake, so an agent installed before M18.3 produces a named
  contract mismatch and the local tone fallback rather than a failure inside a firing alarm.

The Windows Browser Agent is reached over the SAME proven device/broker command path as
`desktop.open_application` (DEVICE_PROTOCOL.md §5): one command envelope, one
`accepted → running → succeeded|failed` lifecycle, one idempotency store, one audit log.
Cloud Core is the brain: it plans, decides which device executes, and drives the browser
with fine-grained commands so every step is durable (Temporal), idempotent and resumable.
The device is a safe, dumb executor: it never chooses sites beyond what a command names,
never elevates a web page's text into an instruction, and never ships session material.

```
Cloud Core (planner/workflow)  --command envelope-->  Device Service (Session 0)
                                                        └─ pipe exec_request ──> Session Companion (owner session)
                                                                                    └─ stdio JSON lines ──> Browser Worker (python -m browser_agent.worker)
                                                                                                              └─ Playwright ──> real Chrome (channel=chrome, dedicated profile)
```

## 1. Capability names (advertised in `hello.capabilities`)

Family marker: `browser.chrome` — present when the device has a configured Browser Worker.
Per-operation names (each is a device command `capability`):

| Capability | Risk class | Purpose |
|---|---|---|
| `browser.session_open` | NAVIGATE | Open (or reuse) a browser session for a job. Idempotent. |
| `browser.session_close` | NAVIGATE | Close a session. Idempotent (unknown session → ok). |
| `browser.worker_status` | READ | Worker/browser health, versions, open sessions. |
| `browser.navigate` | NAVIGATE | Go to an http(s) URL. |
| `browser.back` / `browser.forward` | NAVIGATE | History navigation. |
| `browser.tab_list` / `browser.tab_new` / `browser.tab_close` / `browser.tab_select` | NAVIGATE | Tab surface. |
| `browser.inspect` | READ | Current URL/title/page kind. |
| `browser.find` | READ | Resolve a semantic target; count + describe matches. |
| `browser.click` | see §4 | Click a semantic target. |
| `browser.fill` | REVERSIBLE_WRITE | Type into a field (replaces content). |
| `browser.select_option` | REVERSIBLE_WRITE | Choose a `<select>` value. |
| `browser.set_checked` | REVERSIBLE_WRITE | Checkbox/radio. |
| `browser.scroll` | NAVIGATE | Scroll the document (DOM `scrollBy`, never coordinates). |
| `browser.wait` | READ | Wait for navigation / text / target. |
| `browser.extract` | READ | Page text, links, metadata, JSON-LD. |
| `browser.snapshot` | READ | Accessibility (ARIA) snapshot. |
| `browser.screenshot` | READ | Viewport JPEG, diagnostics only. |
| `browser.download` | HIGH_IMPACT | Save a file the owner authorised. Gated (§4). |
| `browser.search` | NAVIGATE | Run a web search on a search engine page and read the result links semantically. |
| `browser.fetch_evidence` | NAVIGATE | navigate + wait + extract + classify in one command (the research primitive). |
| `browser.media_play` | NAVIGATE | v1.2. Play a named media URL in a `media` session and PROVE it is playing. |
| `browser.media_volume` | NAVIGATE | v1.2. Ramp the media element's own volume inside the page. |
| `browser.media_status` | READ | v1.2. Read the media element. Changes nothing. |
| `browser.media_stop` | NAVIGATE | v1.2. Pause, then close the session. Idempotent. |

Names match `^[a-z][a-z0-9_.]{1,63}$`. Anything else in the `browser.` namespace fails with
`capability_missing`.

## 2. Sessions

Every operation except `session_open`, `session_close` and `worker_status` carries
`session_id` (string, 1–128 chars, `^[A-Za-z0-9_.:-]+$`; Cloud Core uses the research task
id). A session is one Playwright context in one Chrome instance with its own tabs.

`browser.session_open` payload:

```json
{"session_id":"…","profile":"research","policy":{"allowed_risk_classes":["READ","NAVIGATE"],"visible":true},"channel":"chrome"}
```

- `profile`: `research` (the dedicated PagentOS agent profile, persistent, never the owner's
  `User Data`), `alarm` (v1.2 — a SECOND dedicated persistent profile, for alarm media only)
  or `isolated` (fresh non-persistent context). The owner's real Chrome session
  is NOT reachable through this contract in v1; it stays behind `BrowserEnrollment` +
  `owner_authorized_for_research` (ADR-0035 §4) and a later contract version.
- `session_kind` (v1.2, optional, default `research`): `research` — everything v1.1 did,
  unchanged — or `media`, the alarm surface (§3b).
- `policy.allowed_risk_classes`: the classes this session may execute (§4). A research session
  is `["READ","NAVIGATE"]`. The worker enforces this per operation and refuses the rest with
  `security_scope_error` — Cloud Core enforces the same rule before sending (defence in depth).
- `policy.visible`: headful (default true — the owner sees the real Chrome window) or headless.
- `channel`: `chrome` (installed Google Chrome; the qualification target) or `chromium`
  (Playwright's bundled build, CI only). Missing channel → `dependency_unavailable`.

Result: `{"session_id":"…","created":true|false,"profile":"research","session_kind":"research","channel":"chrome","browser_version":"…","idle_timeout_s":600,"policy":{…},"lifecycle":{…}}`
(`profile`/`session_kind` are v1.2 additions; every other key is unchanged).
Reopening an existing session returns `created:false` and the existing policy (policy is not
widened by a second open; a narrower reopen is applied). A reopen may NOT change `profile` or
`session_kind` — that answers `validation_error`, because silently handing the caller a
different browser than the one it named is worse than refusing.

**The alarm profile and the media session kind (v1.2, M18.3, ADR-0073).** They are one thing,
checked from both directions: `profile: "alarm"` requires `session_kind: "media"`, and a
`media` session may never use the `research` profile (both refusals are `validation_error`).
The alarm profile directory is DERIVED, not configured by default — the research profile's
sibling, `<profile-dir>-alarm` — and the worker refuses at startup an
`--alarm-profile-dir` that is, or contains, the research profile; `ManagedBackend`'s existing
real-profile guard refuses either of them anywhere inside a real Chrome/Edge/Brave profile
tree. So alarm audio can never appear in the browser the owner's research is using, and never
in the owner's own Chrome. One session id owns each persistent profile at a time (the M13
lifecycle guard, generalised): a second session id on the `alarm` profile is
`browser_lifecycle_violation`, exactly as for `research`, and the two coexist happily.

Under `session_kind: "media"` the worker launches Chrome with
**`--autoplay-policy=no-user-gesture-required`** and, by default, a visible window (an
explicit `policy.visible` still wins). That switch is a *preference for our own dedicated
window* — it changes how our profile treats our page's `play()` call. **It is not an anti-bot
measure**: it defeats no site protection, no bot detection, no DRM, no advertising and no
consent handling, and it is applied to no other session kind. A media session's default risk
classes are `["READ","NAVIGATE"]`, the same as research: the media surface never fills a
field, never submits, never downloads and never clicks anything.

The four media operations REFUSE to run on a session that is not `session_kind: "media"`
(`validation_error` naming the fix). That refusal, not a convention, is what keeps alarm
audio out of the research browser.

**Lifecycle invariant and identity (ADR-0050 items 14/15).** One research job = one worker
process + one Chrome process on the dedicated profile + one window; tabs are bounded
(`max_tabs`, default 6, ceiling 12, settable in `session_open`). Every session-scoped result
carries `lifecycle`:

```json
{"session_uid":"uuid per actual browser launch","browser_pid":12345,"worker_pid":678,"profile_dir":"…","tab_count":1,"max_tabs":6,"max_windows":1,"reused":true,"launch_kind":"clean|recovery","launch_lock":"Local\\PagentOS.BrowserProfile.<hash>","job_object_assigned":true}
```

`session_uid` and `browser_pid` are identical across every command of a job; a consumer that
sees them change is looking at a relaunch. Guards, all answered with
`browser_lifecycle_violation` (non-retryable, `evidence.guard` names the guard): a second
session id while one owns the research profile; a tab that would cross `max_tabs` (refused
before anything opens); another worker *process* holding the OS-level launch lock for the
profile (`launch_lock`); the launch-rate circuit breaker (`launch_breaker`: three *recovery*
launches — after an orphan reap or a failed launch — within ten minutes write a durable
`browser-lifecycle-fault.json` under the worker data dir and every research-profile launch is
refused until it ages out, across worker restarts; the hello reports it as `lifecycle_fault`).
Before a launch the worker reaps only Chrome processes whose command line names the PagentOS
profile directory (never the owner's Chrome), and the launched Chrome root is placed in a
Windows Job Object with kill-on-close held by the worker alone: when the worker process ends,
however it ends, the kernel terminates the whole Chrome tree. `session_close` waits for the
root to exit and answers `browser_pid_exited`. Ownership is durable in
`browser-ownership.json` (research job id, browser session id, worker pid, Chrome root pid and
start time, transport `playwright-pipe`, profile path, lock name, tab ids, `closed_at`,
`browser_pid_exited`). Browser detection for the hello never starts a browser (Windows reads
the executable's version resource; `chrome.exe --version` would open a window).

Sessions close after `idle_timeout_s` without a command, on `session_close`, or when the
worker restarts. An operation on an unknown session fails with `validation_error`
(`message` starts with `unknown session`) — Cloud Core re-opens and retries; nothing is
lost because every research step is idempotent and READ/NAVIGATE only.

## 3. Payloads and results

Common rules: every payload is a JSON object ≤ 64 KiB (broker rule); every result is a JSON
object ≤ 48 KiB (`MAX_BROWSER_RESULT_BYTES`), the worker truncates text fields and sets
`"truncated": true` rather than exceeding it. Timeouts: the pipe request carries
`timeout_ms`; the worker must return (success, typed error, or `timeout`) before it elapses.
Browser-family commands may run up to 120 s (`InteractiveCapabilityExecutor` raises its cap
for `browser.*`); Cloud Core sends `timeout_s` 60–120.

`target` is a semantic `TargetSpec` object exactly as `browser_agent.targets.TargetSpec`
accepts it: one of `role`(+`name`), `text`, `label`, `placeholder`, `test_id`; optional
`exact`; optional `frame` (iframe `name`). Coordinates/CSS/XPath are rejected with
`validation_error`.

Navigation result (`navigate`, `back`, `forward`, `tab_select`, `wait` with navigation):

```json
{"url":"…","title":"…","http_status":200,"page_kind":"ok","site_error":null,"elapsed_ms":812}
```

`page_kind` ∈ `ok | auth_wall | captcha | error_page | blocked | empty`. This is the
**browser error vs website error** split: a website problem is a *successful* command whose
`page_kind ≠ ok` (with `site_error: {"kind":"http_error|auth_wall|captcha|blocked|dns|reset",
"http_status": 503|null, "detail":"…"}`), whereas a browser/transport problem is a typed
command error (`timeout`, `dependency_unavailable`, `ui_state_changed`, …). Auth walls are
detected from password fields on the landing page, HTTP 401, and a small marker list
(login/sign in/oturum aç/giriş yap in the title or main heading); a bare HTTP 403 without
login markers is `blocked` (bot filtering, observed for real on a public newsroom against
headless Chrome); CAPTCHA pages from known markers (recaptcha/hcaptcha/turnstile/"verify you
are human"/"bots use duckduckgo"/"checking your browser"/"just a moment"/"are you a robot").
The worker never attempts to solve a CAPTCHA. Headful real Chrome with the dedicated
persistent profile (`visible: true`, the default) is the qualified posture: on 2026-09-03 all
three engines and the primary newsrooms served headful Chrome normally while headless Chrome
was refused or challenged.

`browser.extract` payload `{"session_id":"…","mode":"text|links|metadata|structured|all","max_chars":24000,"frame":null}` → 

```json
{"url":"…","title":"…","text":"…","truncated":false,
 "metadata":{"canonical_url":"…","published_at":"2026-09-01T10:00:00Z","modified_at":null,"publisher":"OpenAI","author":null,"language":"en","description":"…"},
 "links":[{"text":"…","href":"https://…"}],
 "structured":{"json_ld":[…]},
 "page_kind":"ok"}
```

Metadata comes from `<meta property="article:published_time">`, `<meta name="date">`,
`<time datetime>`, JSON-LD `datePublished/dateModified`, `og:site_name`, `<html lang>`,
`<link rel=canonical>`; absent → `null`, never guessed. `links` ≤ 200, http(s) only.

`browser.search` payload `{"session_id":"…","query":"…","engine":"auto|google|duckduckgo|bing|brave","max_results":10,"recency_days":3,"locale":"tr-TR"}` →

```json
{"schema_version":2,"engine":"google","requested_provider":"google","provider":"google","fallback":false,"fallback_reason":null,
 "query":"…","result_count":8,"locale":"tr-TR",
 "attempts":[{"provider":"google","outcome":"ok"}],
 "results":[{"rank":1,"url":"…","title":"…","snippet":"…","published_hint":"2 gün önce"}],"page_kind":"ok"}
```

Provider abstraction: **Google is the primary provider; DuckDuckGo is the fallback.** `auto`
(the default) means `google → duckduckgo`; a named engine is used alone. A provider is
abandoned, with the reason recorded, on `captcha` (Google's `/sorry/` "unusual traffic"
interstitial is detected by URL and text and is never solved), `consent` (a consent
interstitial), `blocked`, `empty` (no organic result parsed), a transport error, or
malformed markup. When every provider fails the command fails with `provider_rate_limited`
(retryable) and the evidence still lists the attempts. Evidence fields are mandatory:
`requested_provider`, `provider` (the one that produced the results), `fallback`,
`fallback_reason`, `query`, `result_count`, `attempts`. `locale` (payload, else the worker's
`--locale`, else the machine's user locale) sets Google's `hl`/`gl`; nothing assumes one
locale. Google organic results are read from the results region only: ads (`#tads`,
`data-text-ad`), "People also ask", knowledge-panel and other right-hand links, carousels and
Google's own domains are excluded; each result carries `rank`, `title`, `url`
(`/url?q=` redirects unwrapped) and the visible snippet when present. `max_results` ≤ 20.
`bing`/`brave` remain selectable by name only. **Schema versioning**: the result carries
`schema_version` (2 = provider evidence present), and the hello / `browser.worker_status`
carry `contracts: {"browser.search": 2}`; a consumer checks the contract BEFORE searching and
reports a *contract/version mismatch* naming the installed worker version when it is lower,
never a missing-property failure (an installed worker predating this schema answered a real
owner run without evidence on 2026-09-03).

`browser.fetch_evidence` payload `{"session_id":"…","url":"…","query":"…","source_class":"news","excerpt_chars":1200,"timeout_ms":30000}` →

```json
{"url":"…","final_url":"…","title":"…","excerpt":"…","text_chars":18234,"fetched_at":"2026-09-03T09:00:00Z",
 "extraction_method":"dom_text","page_kind":"ok","http_status":200,
 "metadata":{…as extract…},"source_class":"news","query":"…","injection_markers":0,"links_count":142}
```

The excerpt is verbatim page text (main/article region first, body fallback), never a
paraphrase; `injection_markers` counts instruction-like patterns found in the page text
(§6) so Cloud Core can flag the evidence; the worker keeps the text as data regardless.

`browser.click` payload `{"session_id":"…","target":{…},"frame":null}` → `{"clicked":true,"navigated":false,"url":"…","resolved":{"tag":"a","role":"link","name":"…"}}`.

`browser.fill` `{"session_id","target","value","frame"}`; `browser.select_option` `{"session_id","target","value"}`; `browser.set_checked` `{"session_id","target","checked"}` → `{"ok":true}`.

`browser.scroll` `{"session_id","direction":"down|up|to_end|to_top","amount_px":800}` → `{"scroll_y":…,"scroll_height":…,"at_end":false}`.

`browser.wait` `{"session_id","for":"navigation|text|target|load","text":"…","target":{…},"timeout_ms":10000}` → `{"satisfied":true,"elapsed_ms":…}` (`satisfied:false` on timeout, not an error).

`browser.find` `{"session_id","target","frame"}` → `{"match_count":2,"elements":[{"tag":"button","role":"button","name":"…","text":"…","visible":true}]}` (≤ 20).

`browser.snapshot` `{"session_id","max_chars":24000}` → `{"aria_snapshot":"…","truncated":false}`.

`browser.screenshot` `{"session_id"}` → `{"format":"jpeg","base64":"…","bytes":…}` (viewport only, ≤ 300 KiB).

`browser.download` `{"session_id","target","authorization_ref":"…"}` → `{"path":"…","bytes":…,"sha256":"…"}`; saved under the companion data dir `downloads/`; refused (`security_scope_error`) unless the session policy allows `HIGH_IMPACT` and `authorization_ref` is present.

`browser.tab_list` → `{"tabs":[{"index":0,"url":"…","title":"…","active":true}]}`; `browser.tab_new {"url":null}` → `{"index":1}`; `browser.tab_close {"index":1}` → `{"closed":true}` (closing the last tab → `validation_error`); `browser.tab_select {"index":0}` → navigation result.

`browser.inspect` → `{"url","title","tab_index","tab_count","page_kind"}`.

`browser.worker_status` → `{"worker_version":"…","browser":{"channel":"chrome","version":"…","alive":true},"sessions":[{"session_id","tabs","idle_s"}],"uptime_s":…}`.

## 3a. Persistent research session, Google through the real UI, owner handoff (contract v1.1)

One browser session per research job: Cloud Core opens the session once (`session_id` =
task id), reuses it for every search, navigation, inspection and extraction of that job, and
closes it when the job finishes; a consumer that sees `validation_error` "unknown session"
re-opens once and retries. The worker keeps the loaded Google results tab and the profile
(cookies, locale, consent) between commands and between normal runs; nothing is spoofed or
masked (no stealth plugins, no fingerprint or webdriver masking, no CAPTCHA solving).

`browser.search` for `google` drives the real page: the worker navigates to Google's home
page the first time (or reuses the already loaded results tab afterwards), types the query
into the search box (`role=combobox`), submits it, waits for the results region
(`#search`/`#rso`) or an interstitial — readiness is a DOM/navigation condition, never a
fixed sleep — and parses the organic results (§3). `recency_days` is applied to the loaded
results page as Google's own time filter. Every other engine keeps its results URL.

Payload additions: `"mode": "interactive" | "unattended"` (owner-facing; `interactive` =
Google → owner handoff if needed → fallback only afterwards, `unattended` = Google →
deterministic fallback if blocked) and/or `"interstitial": "fallback" | "handoff"` (default
`fallback`; an explicit `interstitial` wins). Result additions: `"mode"` (the effective
mode), `"verification_handoffs"` (interstitials handed to the owner in this session so far),
`"state": "ok" | "waiting_for_owner_verification"`, `"path"`:
`google_ui | google_url | fallback | handoff_pending | handoff_cleared | handoff_timeout_fallback | handoff_repeat_fallback`,
`"verification_url"` (when waiting), and `"verification"` (schema 3, below).

**Verification evidence, schema 3 (2026-09-04).** The interstitial handed to the owner is
evidence in exactly ONE place, the result's `verification` block:

```json
{"handoffs":1,"outcome":"pending|cleared|timeout|repeat|null","interstitial":"captcha|consent|null","verification_url":"…|null"}
```

`outcome` is `null` when no interstitial was handed to the owner (an unattended fallback keeps
its provider-level `fallback_reason=google:captcha`). While a handoff is pending the top-level
`page_kind` is `waiting`, never the interstitial kind, and the Google attempt's `outcome` is a
verification code, not the kind: `verification_pending`, `verification_timeout` (a
fallback-mode search on a still-pending query) or `interstitial_after_verification` (a further
interstitial once the owner has cleared one). The canonical fallback reasons are therefore
`google:verification_timeout` and `google:interstitial_after_verification` for the handoff
paths, and `google:captcha` / `google:consent` for an unattended block. Consumers must not
copy the interstitial kind to another key: a duplicate is a contract violation, not a
convenience (real owner incident: two layers each adding `interstitial` produced a
duplicate-key failure that swallowed the fallback attempt).

**Retry once, never loop (2026-09-04).** A session hands an interstitial to the owner at most
ONCE. After a cleared verification the pending search is retried exactly once on the same
session (`handoff_cleared`). If Google shows an interstitial again afterwards, the worker does
not hand off a second time: the attempt is recorded (`detail` says so) and the provider
fallback applies (`path=handoff_repeat_fallback`). A `fallback`-mode search on a query whose
verification is still pending records the interstitial and moves to the next provider WITHOUT
navigating to Google again (`path=handoff_timeout_fallback`). A `handoff`-mode search on the
same pending query while the page is still blocked answers the same waiting state again
without a new navigation. On a handoff the worker brings the Chrome TAB to the front
(Playwright) and the Chrome WINDOW to the foreground (Win32, best effort, the research
Chrome's own root pid only). The dedicated profile keeps Google's verification and consent
cookies between runs, as normal Chrome behaviour permits; nothing is spoofed or solved.

- `fallback` (unattended): an interstitial (`captcha` = Google's unusual-traffic page,
  `consent`, `blocked`) is recorded as the attempt's outcome and the next provider is tried
  (§3), `path=fallback`.
- `handoff` (owner present): on an interstitial the worker brings the Chrome window to the
  foreground (`bring_to_front`), leaves the page exactly as it is, and returns a SUCCESSFUL
  command with `state=waiting_for_owner_verification`, `page_kind=captcha|consent`,
  `provider=null`, `results=[]`, `path=handoff_pending`. Nothing is retried in a loop and
  nothing is solved: the owner completes the page by hand.

`browser.wait` gains `"for": "verification_cleared"` (READ): polls the current page of the
session every ~500 ms until it is no longer an interstitial (URL off `/sorry/` and
`consent.google.*`, results region or a normal page present) or `timeout_ms` elapses →
`{"satisfied": true|false, "url": …, "elapsed_ms": …}`. After `satisfied: true` the consumer
re-issues the same `browser.search`; the worker parses the already loaded results page when
its query matches (`path=handoff_cleared`), otherwise types the query again.

`browser.fetch_evidence` gains `"tab": "same" | "new"` (default `same`): `new` opens the URL in
a new tab, extracts there, closes that tab and reselects the previous one, so the Google
results tab stays loaded for the next search; the result carries `"tab_used"`.

## 3b. Alarm media operations (contract v1.2, M18.3 spec §4, ADR-0073)

Four operations on a `session_kind: "media"` session (§2). They exist so the wake alarm can
play the music the owner named and PROVE it is playing — or say, truthfully, why it is not,
so Cloud Core records that and rings the local tone instead.

| op | risk | payload |
|---|---|---|
| `browser.media_play` | NAVIGATE | `{"session_id":"…","url":"https://…","volume":0.15,"verify_seconds":3}` — `volume` 0..1, `verify_seconds` an integer 1..10 (default 3) |
| `browser.media_volume` | NAVIGATE | `{"session_id":"…","level":0.6,"ramp_seconds":20}` — `level` 0..1, `ramp_seconds` 0..120 (default 0 = set at once) |
| `browser.media_status` | READ | `{"session_id":"…"}` |
| `browser.media_stop` | NAVIGATE | `{"session_id":"…"}` |

```json
media_play   {"playing":true,"verified":true,"url":"…","final_url":"…","title":"…","current_time_s":1.5,"duration_s":250.0,"volume":0.15,"muted":false,"reason":null}
media_volume {"applied":true,"level_from":0.15,"level_to":0.6,"ramp_seconds":20}
media_status {"present":true,"playing":true,"paused":false,"ended":false,"current_time_s":13.0,"duration_s":250.0,"volume":0.6,"muted":false,"title":"…","url":"…"}
media_stop   {"stopped":true,"was_playing":true,"session_id":"…","browser_pid_exited":true}
```

`media_play`: validates the URL against the same destination policy as `navigate` (a refusal
is `security_scope_error`, never a playback `reason`), navigates, classifies the landing page
(§3), waits at most **10 s** for a `<video>` element, sets `volume` **FIRST** (so the owner is
never hit by the page's own level for the instant before the ramp), calls `play()`, then
verifies that the element's own `currentTime` advanced by **≥ 0.5 s** over `verify_seconds`
while `paused === false`. `verified: true` means the page really moved; `playing` is what the
element reports at the end of the check.

`reason` — `null` when verified, otherwise exactly one of:

| reason | when |
|---|---|
| `no_media_element` | no `<video>` attached within 10 s (and no consent wording on the page) |
| `autoplay_blocked` | `play()` rejected with `NotAllowedError` |
| `challenge` | CAPTCHA / "confirm you're not a bot" / sign-in wall (`page_kind` `captcha`/`auth_wall`/`blocked`, or Google's `/sorry/` interstitial) |
| `consent_wall` | a consent gate the page cannot leave without a click we will not make: a `consent.youtube.*` / `consent.google.*` landing, or consent wording on a page that produced no media element |
| `navigation_failed` | HTTP ≥ 400, or a transport failure (`net::ERR_*`, DNS, navigation timeout) |
| `error` | `play()` rejected for any other reason, or it resolved and the element still did not move |

**NO retry, NO bypass, no click on anything** — not a consent button, not an ad, not a
challenge. No DRM or ad circumvention, no download, nothing that reads cookies or storage
(the forbidden-key scan and the 48 KiB cap apply unchanged). A wall is reported and the alarm
falls back to the device's own tone. Consent WORDING alone never makes a `consent_wall`: a
cookie banner floating over a video that plays anyway is a successful play.

Deviation from the M18.3 spec table, deliberately: a transport-level navigation failure is a
*successful* command with `reason: "navigation_failed"` rather than a retryable typed error,
because the caller in a firing alarm needs a media receipt it can record and fall back from —
not a `dependency_unavailable` in the middle of the wake sequence. Payload and policy errors
(`validation_error`, `security_scope_error`) stay hard errors.

`media_volume`: the ramp runs **inside the page** — a `setInterval` stepping the media
element's own `volume` from its current level to `level` every **250 ms**, cancelling any ramp
already running, clamping every write to 0..1 and landing exactly on `level`. The op returns
as soon as the interval is armed, so a 20 s wake ramp does not hold a device command open for
20 s; a ramp of **≤ 2 s** (the greeting's duck and restore) is awaited to completion before
the op returns. `level_from` is the element's ACTUAL level, read in the page — `null`, with
`applied: false`, when there is no media element (a documented addition to the spec's shape:
a missing element must not raise in the middle of a greeting). This is the media element's
own volume and never the Windows master volume.

`media_status` reads and changes nothing. `volume` and `duration_s` are `null` rather than a
fabricated `0.0` when there is no element or no known duration (documented addition).

`media_stop` pauses the element, then closes the session with ordinary `session_close`
semantics (including the bounded wait for the browser process to actually exit, reported as
`browser_pid_exited`). Idempotent: stopping a session that is already gone answers
`{"stopped":true,"was_playing":false,…}`. A page that has already died still gets its session
closed — the alarm must end even when the tab did not survive. `media_stop` and `session_open`
results also carry `lifecycle` (§2), as every session-scoped result does.

## 4. Risk classes and enforcement

`READ` (inspect, find, wait, extract, snapshot, screenshot, worker_status, media_status) ·
`NAVIGATE` (session_open/close, navigate, back, forward, tabs, scroll, search, fetch_evidence,
media_play, media_volume, media_stop, click on a plain link) · `REVERSIBLE_WRITE` (fill, select_option, set_checked, click on a
non-submitting control) · `EXTERNAL_COMMUNICATION` (click on a submit control or inside a
`<form>` submit path; pressing Enter is not exposed in v1) · `HIGH_IMPACT` (download; any
click the worker classifies as purchase/delete/send by accessible name markers such as
buy/purchase/pay/delete/remove/send/submit order/satın al/öde/sil/gönder).

The worker classifies `click` by the resolved element (`a[href]` without `onclick` → NAVIGATE;
`button`/`input` with `type=submit` or inside a form → EXTERNAL_COMMUNICATION; marker names →
HIGH_IMPACT; otherwise REVERSIBLE_WRITE) BEFORE acting and refuses with
`security_scope_error` (`retryable:false`, message names the class) when the session policy
does not allow it. Research sessions are READ+NAVIGATE. Cloud Core's research workflow only
ever issues READ/NAVIGATE operations; anything else goes through the confirmation framework.

## 5. Errors

Device taxonomy classes only (DEVICE_PROTOCOL.md §8). Mapping from the browser package:
`validation_error` (bad payload, unknown session, non-http URL, coordinate target),
`capability_missing` (unknown `browser.*` op; channel not installed → `dependency_unavailable`),
`dependency_unavailable` (worker not running, Chrome crashed, `net::ERR_*`, DNS — retryable),
`timeout` (navigation/action timeout — retryable), `ui_target_not_found`, `ui_state_changed`
(detached/closed page — retryable), `provider_rate_limited` (all engines CAPTCHA'd/blocked —
retryable), `security_scope_error` (risk policy refusal — not retryable), `cancelled`,
`internal_bug`. `message` ≤ 2000 chars, never contains page text beyond 200 chars, never a URL
query string with credentials.

## 5a. Destination policy (both sides)

Only public Internet hosts may be navigated to. Cloud Core validates every URL before it
builds a `navigate`/`tab_new`/`fetch_evidence` command, and the worker validates again
before acting (the device is the side that sits on the owner's LAN and tailnet): scheme
`http`/`https` only; no userinfo; host names `localhost`, `*.local`, `*.internal`,
`*.localhost`, `*.home.arpa` and cloud metadata names refused; IP literals and EVERY
address the host resolves to must be outside loopback, RFC 1918, link-local
(`169.254.0.0/16` incl. the metadata address), CGNAT `100.64.0.0/10` (the tailnet),
multicast, reserved, unspecified, IPv6 loopback/link-local/ULA. A refusal is
`security_scope_error` (not retryable) with the query string stripped from the evidence.
Discovery output (search hits, feeds, APIs) is third-party content and gets no exemption.
The worker's `--allow-private-destinations` flag exists for the fixture-site test suite
only; the companion never passes it.

## 6. Untrusted content boundary

Page text is data. The worker: (a) never executes anything based on page content — no page
may cause a navigation, click, download, file write or configuration change; the only inputs
are command payloads; (b) never includes cookies, storage, headers, form values it typed, or
the profile path in any result (results are scanned with the shared forbidden-key rule:
`cookie`, `authorization`, `set-cookie`, `localstorage`, `sessionstorage`, `password`, `token`,
`secret`, `apikey`); (c) counts `injection_markers` with the same pattern list Cloud Core uses
(`ignore (all|previous|prior) instructions`, `system prompt`, `(reveal|print) your (instructions|prompt|secrets)`,
`(execute|run) (the|this) command`, `upload`, `install`, `change (the )?policy`, `you are now`,
`as an ai`, `assistant:`, Turkish: `önceki talimatları yok say`, `komutu çalıştır`, `şifreyi göster`;
the canonical list is `packages/protocol/browser-injection-markers.json`). Before matching, both
sides fold the text the same way: NFKC normalisation, zero-width/joiner characters removed,
whitespace runs collapsed to one space. The detector is telemetry for Cloud Core's flag; the
safety boundary is structural (page text has no path into any action) and does not depend on it.
Cloud Core flags evidence with markers `injection_suspected=true`, keeps it as quoted evidence
only, excludes it from any synthesis prompt as an instruction source (it is wrapped as a
quoted, delimited data block with an explicit "untrusted web content" header) and never writes
it to memory as a fact. Hostile-page tests exist on both sides.

## 7. Companion ↔ Worker stdio protocol (never leaves the machine)

The companion starts the worker as a child process (`BrowserWorkerCommand` + args from its
configuration; no hardcoded path) with stdin/stdout pipes, newline-delimited UTF-8 JSON, one
object per line. stderr is the worker's log (companion forwards it to its own log).

Worker → companion on start: `{"type":"hello","worker_version":"…","protocol_version":1,"capabilities":["browser.session_open",…],"browser":{"channel":"chrome","available":true,"version":"…"},"lifecycle_fault":null|{…}}`.

Companion → worker: `{"type":"exec","request_id":"…","capability":"browser.navigate","payload":{…},"timeout_ms":30000}`;
`{"type":"cancel","request_id":"…"}`; `{"type":"ping"}`; `{"type":"shutdown"}`.

Worker → companion: `{"type":"result","request_id":"…","ok":true,"result":{…}}` or
`{"type":"result","request_id":"…","ok":false,"error":{"class":"timeout","message":"…","retryable":true,"evidence":{…}}}` (`evidence` optional, bounded to 8 KB, structured context such as the lifecycle guard that refused — never page content);
`{"type":"pong","sessions":n}`; `{"type":"log","level":"info","event":"…"}` (optional).

Companion lifecycle: start lazily on the first `browser.*` request (or eagerly when
configured), one worker at a time, restart with backoff after an exit (the in-flight
requests fail with `dependency_unavailable`, retryable), `shutdown` then kill on companion
stop; a request whose `timeout_ms` elapses is cancelled and answered `timeout`. Requests are
executed concurrently by the worker per session but serially within a session.

Worker CLI: `python -m browser_agent.worker --data-dir <dir> [--profile-dir <dir>] [--alarm-profile-dir <dir>] [--channel chrome|chromium] [--visible|--headless] [--idle-timeout-s 600]`. Both profile dirs must be dedicated PagentOS profiles (the package already rejects real `User Data` trees), and `--alarm-profile-dir` (v1.2, default `<profile-dir>-alarm`) must not be, or contain, the research profile.

## 8. Capability advertisement and device selection (Cloud Core side)

The device's `hello.capabilities` is stored on every connection (`device_sessions.connection_metadata` and `devices.capabilities_json` refreshed on hello). Cloud Core's device layer (`app.devices`) exposes inventory, presence (online / stale / offline from heartbeat age), capabilities, health (last hello, last command outcomes, `software_version`) and selection: explicit owner target (device id, name or alias — Turkish aliases `ev`, `iş`, `laptop/dizüstü` resolve through `devices.metadata_json.aliases`) → online → has capability → policy allows → healthiest. No capable device → `no_capable_device` (task fails fast with a Turkish explanation, never a hang).
