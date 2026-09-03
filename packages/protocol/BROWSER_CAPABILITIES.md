# Browser capabilities over the device protocol (M13, ADR-0050)

Status: contract v1 — binding for `services/api` (Cloud Core), `devices/windows-agent`
(Session Companion) and `services/browser` (Browser Worker). Change it here first.

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
  `User Data`) or `isolated` (fresh non-persistent context). The owner's real Chrome session
  is NOT reachable through this contract in v1; it stays behind `BrowserEnrollment` +
  `owner_authorized_for_research` (ADR-0035 §4) and a later contract version.
- `policy.allowed_risk_classes`: the classes this session may execute (§4). A research session
  is `["READ","NAVIGATE"]`. The worker enforces this per operation and refuses the rest with
  `security_scope_error` — Cloud Core enforces the same rule before sending (defence in depth).
- `policy.visible`: headful (default true — the owner sees the real Chrome window) or headless.
- `channel`: `chrome` (installed Google Chrome; the qualification target) or `chromium`
  (Playwright's bundled build, CI only). Missing channel → `dependency_unavailable`.

Result: `{"session_id":"…","created":true|false,"channel":"chrome","browser_version":"…","idle_timeout_s":600,"policy":{…}}`.
Reopening an existing session returns `created:false` and the existing policy (policy is not
widened by a second open; a narrower reopen is applied).

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

`browser.search` payload `{"session_id":"…","query":"…","engine":"auto|duckduckgo|bing|brave","max_results":10,"recency_days":3}` →

```json
{"engine":"duckduckgo","query":"…","results":[{"url":"…","title":"…","snippet":"…","published_hint":"2 days ago"}],"page_kind":"ok"}
```

`auto` tries the engines in order and moves to the next on `captcha|blocked|empty`; a search
that ends in a CAPTCHA on every engine fails with `provider_rate_limited` (retryable). Result
links are read from the results list semantically (role=link within the results region);
ads/sponsored entries and the engine's own domains are dropped; `max_results` ≤ 20.

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

## 4. Risk classes and enforcement

`READ` (inspect, find, wait, extract, snapshot, screenshot, worker_status) ·
`NAVIGATE` (session_open/close, navigate, back, forward, tabs, scroll, search, fetch_evidence,
click on a plain link) · `REVERSIBLE_WRITE` (fill, select_option, set_checked, click on a
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

## 6. Untrusted content boundary

Page text is data. The worker: (a) never executes anything based on page content — no page
may cause a navigation, click, download, file write or configuration change; the only inputs
are command payloads; (b) never includes cookies, storage, headers, form values it typed, or
the profile path in any result (results are scanned with the shared forbidden-key rule:
`cookie`, `authorization`, `set-cookie`, `localstorage`, `sessionstorage`, `password`, `token`,
`secret`, `apikey`); (c) counts `injection_markers` with the same pattern list Cloud Core uses
(`ignore (all|previous|prior) instructions`, `system prompt`, `reveal|print your (instructions|prompt|secrets)`,
`execute|run (the|this) command`, `upload`, `install`, `change (the )?policy`, `you are now`,
`as an ai`, `assistant:`, Turkish: `önceki talimatları yok say`, `komutu çalıştır`, `şifreyi göster`).
Cloud Core flags evidence with markers `injection_suspected=true`, keeps it as quoted evidence
only, excludes it from any synthesis prompt as an instruction source (it is wrapped as a
quoted, delimited data block with an explicit "untrusted web content" header) and never writes
it to memory as a fact. Hostile-page tests exist on both sides.

## 7. Companion ↔ Worker stdio protocol (never leaves the machine)

The companion starts the worker as a child process (`BrowserWorkerCommand` + args from its
configuration; no hardcoded path) with stdin/stdout pipes, newline-delimited UTF-8 JSON, one
object per line. stderr is the worker's log (companion forwards it to its own log).

Worker → companion on start: `{"type":"hello","worker_version":"…","protocol_version":1,"capabilities":["browser.session_open",…],"browser":{"channel":"chrome","available":true,"version":"…"}}`.

Companion → worker: `{"type":"exec","request_id":"…","capability":"browser.navigate","payload":{…},"timeout_ms":30000}`;
`{"type":"cancel","request_id":"…"}`; `{"type":"ping"}`; `{"type":"shutdown"}`.

Worker → companion: `{"type":"result","request_id":"…","ok":true,"result":{…}}` or
`{"type":"result","request_id":"…","ok":false,"error":{"class":"timeout","message":"…","retryable":true}}`;
`{"type":"pong","sessions":n}`; `{"type":"log","level":"info","event":"…"}` (optional).

Companion lifecycle: start lazily on the first `browser.*` request (or eagerly when
configured), one worker at a time, restart with backoff after an exit (the in-flight
requests fail with `dependency_unavailable`, retryable), `shutdown` then kill on companion
stop; a request whose `timeout_ms` elapses is cancelled and answered `timeout`. Requests are
executed concurrently by the worker per session but serially within a session.

Worker CLI: `python -m browser_agent.worker --data-dir <dir> [--profile-dir <dir>] [--channel chrome|chromium] [--visible|--headless] [--idle-timeout-s 600]`. The profile dir must be a dedicated PagentOS profile (the package already rejects real `User Data` trees).

## 8. Capability advertisement and device selection (Cloud Core side)

The device's `hello.capabilities` is stored on every connection (`device_sessions.connection_metadata` and `devices.capabilities_json` refreshed on hello). Cloud Core's device layer (`app.devices`) exposes inventory, presence (online / stale / offline from heartbeat age), capabilities, health (last hello, last command outcomes, `software_version`) and selection: explicit owner target (device id, name or alias — Turkish aliases `ev`, `iş`, `laptop/dizüstü` resolve through `devices.metadata_json.aliases`) → online → has capability → policy allows → healthiest. No capable device → `no_capable_device` (task fails fast with a Turkish explanation, never a hang).
