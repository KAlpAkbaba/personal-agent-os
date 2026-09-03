# pagentos-browser — semantic browser automation adapter (M2)

Provider-neutral browser automation package (`browser_agent`) used by the
Personal Agent OS runtime. Built on Playwright (pinned in `uv.lock`), exposed
through a backend adapter seam, semantic operations, a typed error taxonomy
and a browser-command layer with idempotency + cancellation.

## Architecture: transport vs semantics (ADR-0019)

```
BrowserCommandExecutor        idempotency + cancellation (device-protocol-like)
        |
BrowserSession                semantics: TargetSpec ops, typed errors
        |
BrowserBackend  <-- seam -->  ManagedBackend | ExistingSessionBackend | (future VisualFallbackBackend)
```

- `BrowserBackend` owns lifecycle (`connect`/`close`/`is_alive`/`reconnect`),
  the page/tab surface (`current_page`, `list_tabs`, `new_tab`, `close_tab`,
  `select_tab`) and a `capabilities()` declaration.
- `BrowserSession` is written purely against the interface, so every semantic
  operation is transport-agnostic.
- The future visual/coordinate fallback is an explicitly separate adapter
  (`visual_fallback` capability); it is never mixed into the semantic engine.

## Backend adapter table / capability matrix

| Capability | Managed (isolated) | Managed (persistent profile) | ExistingSession (CDP attach) |
| --- | --- | --- | --- |
| `authenticated_session` | no | yes (dedicated agent profile) | yes |
| `downloads` | yes | yes | yes (best-effort) |
| `uploads` | yes | yes | yes (best-effort) |
| `extensions` | no | no | yes |
| `existing_tabs` | no | no | yes |
| `multiple_windows` | yes | yes | yes |
| `visual_fallback` | no | no | no |

- **ManagedBackend (isolated)** — Playwright-managed Chromium, fresh
  non-persistent context. The deterministic/CI/test default; zero dependency
  on any owner browser state.
- **ManagedBackend (persistent)** — same launcher with `profile_dir=` a
  caller-supplied *dedicated agent profile* (persistent context). The
  constructor rejects paths inside real Chrome/Edge/Chromium/Brave
  `User Data` trees; the owner's real profile is never opened by this backend.
- **ExistingSessionBackend** — attaches over loopback CDP to an
  already-running browser, authorized by a `BrowserEnrollment`. It never
  launches a browser and never configures `--remote-debugging-port` itself.
  An `ExistingSessionBackend`'s effective capabilities are the table column
  merged with the enrollment's granted `capability_overrides`.

The orchestrator queries `backend.capabilities()` before dispatching work;
`require_capability(backend, flag)` raises the taxonomy error
`capability_missing` when a flag is not granted (used internally by
`download`/`upload` as well).

## Browser enrollment (owner-browser authorization model)

Attaching the owner's real Chrome/Edge is modeled exactly like device
enrollment (M1): an explicit authorization record, never ad-hoc debug-port
exposure.

- `BrowserEnrollment(id, name, transport, endpoint, capability_overrides,
  created_at)` — one authorized browser attachment.
- Transports: `cdp_loopback` (implemented) and `extension_bridge`
  (**reserved** enum value for the future browser-extension attach path;
  constructing a backend from it is a typed `validation_error`).
- Loopback-only: any non-loopback CDP endpoint is rejected with
  `validation_error` before I/O. No remote debugging port is ever exposed
  non-loopback.
- Raw `--remote-debugging-port` against the owner's default profile is
  explicitly **NOT** the production path. In production the loopback endpoint
  lifecycle is owned by a trusted local component (owner-session companion or
  the extension bridge when it ships); the enrollment hands the endpoint to
  the backend. In M2, tests exercise the attach path against throwaway
  Chromium instances only.
- `EnrollmentRegistry` (in-memory, optionally file-backed JSON) is the
  authorization seam the orchestrator consults. Broker/DB-backed enrollment
  storage and owner approval flows arrive in a later milestone; the record
  shape is stable so that move is mechanical.
- `BrowserSession.connect_existing_cdp(url)` remains as a dev/test
  convenience that wraps the endpoint in an ephemeral enrollment.

## Semantic control-surface hierarchy

The CLAUDE.md browser rule mandates using the highest semantic control surface
available:

1. API/integration
2. DOM/Playwright
3. accessibility tree
4. Windows UI Automation
5. vision
6. raw coordinates only as last resort

How this package enforces it:

- All interaction goes through `TargetSpec` — a semantic locator expressed as
  ARIA **role + accessible name**, visible **text**, form **label**,
  **placeholder**, or **test id**. Raw coordinates and CSS/XPath selectors are
  not expressible in a `TargetSpec`; unknown fields (e.g. `x`/`y`, `xpath`)
  fail with a typed `validation_error`.
- Iframe interaction stays semantic: operations accept `frame="<name>"`
  addressing an `<iframe>` by its `name` attribute.
- `accessibility_snapshot()` returns the structured ARIA tree
  (`aria_snapshot`), supporting accessibility-first perception instead of
  pixels.
- `screenshot()` exists for diagnostics/telemetry only, never for control.
- The only coordinate-based entry point is `escape_hatch_click_xy`, named to
  be impossible to use accidentally. Every call logs a
  `browser.escape_hatch_used` warning. Exactly one explicitly-marked fallback
  test exercises it; all other tests are semantic.
- Ambiguous locators resolve `.first` and `find()` reports `match_count`
  instead of strict-mode errors.

## Public API

```python
from browser_agent import (
    BrowserSession,
    ManagedBackend,
    ExistingSessionBackend,
    BrowserEnrollment,
    EnrollmentRegistry,
    TargetSpec,
    BrowserCommandExecutor,
    CancelToken,
    with_retry,
    require_capability,
)

# Managed (isolated) — deterministic default
session = await BrowserSession.launch_dedicated(headless=True)

# Managed (persistent dedicated profile)
session = await BrowserSession.launch_dedicated(profile_dir="C:/pagentos/profile")

# Existing session via enrollment (production shape)
registry = EnrollmentRegistry(path="enrollments.json")
backend = ExistingSessionBackend(registry.get(enrollment_id))
await backend.connect()
session = BrowserSession(backend)

await session.navigate(url)
await session.back()
await session.forward()
await session.new_tab(url)
await session.list_tabs()
await session.select_tab(0)
await session.close_tab(1)
await session.click(TargetSpec(role="button", name="Greet"))
await session.fill(TargetSpec(label="Your name"), "Alp")
await session.select_option(TargetSpec(label="Favorite color"), value="green")
await session.set_checked(TargetSpec(label="Subscribe to updates"), True)
await session.upload(TargetSpec(label="Choose file"), path)
await session.click(TargetSpec(role="button", name="Frame Greet"), frame="child")
result = await session.download(TargetSpec(role="link", name="Download sample"))
await session.close()
```

`with_retry(op, attempts=3)` retries only errors whose `retryable` flag is
True, with exponential backoff; terminal errors propagate immediately. When
attempts are exhausted the last typed error is re-raised with
`evidence["retry"]` attached (the original `error_class` is preserved — the
orchestrator owns any collapse to `retry_exhausted`).

## Browser-command layer (idempotency + cancellation)

`BrowserCommandExecutor.execute(command_id, idempotency_key, op, cancel_token)`
mirrors the device-protocol command semantics (ADR-0016):

- a duplicate `idempotency_key` after a terminal state replays the recorded
  result/typed error without re-execution (bounded LRU record store);
- a concurrent duplicate while the original is in flight awaits the
  original's terminal result — the op body runs exactly once;
- cancelling via `CancelToken` aborts the in-flight op and raises the
  taxonomy error `cancelled` (retryable=False); cancellation is terminal and
  is itself replayed to duplicates.

## Error taxonomy mapping

Every operation raises only `BrowserError(error_class, message, retryable,
evidence)`. Classes are the browser-relevant subset of
`docs/API_AND_PROTOCOLS.md` §8 (now including `capability_missing` and
`cancelled`). Classification keys on the Playwright exception **type**, the
explicit operation **phase**, and stable marker strings. The authoritative
table lives in the `browser_agent/errors.py` module docstring; summary:

| Failure (type + phase [+ marker]) | error_class | retryable |
| --- | --- | --- |
| `TimeoutError` in phase `resolve` (locator never attached — target resolves to nothing) | `ui_target_not_found` | no |
| `TimeoutError` in phase `act` with an actionability marker ("intercepts pointer events", "element was detached", …) | `ui_state_changed` | yes |
| `Error` in phase `act`/`navigate` with a DOM/navigation-race marker | `ui_state_changed` | yes |
| `TimeoutError` in any other case (navigation, plain action timeout) | `timeout` | yes |
| Any `Error` in phase `connect`, or carrying a connection/liveness marker ("connect ECONNREFUSED", "net::ERR_…", "Target … closed", …). Navigating to a dead port surfaces `net::ERR_CONNECTION_REFUSED` and therefore types as `dependency_unavailable` (documented decision). | `dependency_unavailable` | yes |
| Capability not granted by the backend/enrollment (guard fires before I/O) | `capability_missing` | no |
| Command cancelled via `CancelToken` | `cancelled` | no |
| Malformed `TargetSpec` / bad arguments / unauthorized enrollment or endpoint | `validation_error` | no |
| Anything else | `internal_bug` | no |

`evidence` carries `op`, `phase`, the target spec, frame name where relevant,
the current URL, and the truncated Playwright message.

## Scenario matrix -> test map (ACCEPTANCE_TESTS M2)

| Scenario | Test |
| --- | --- |
| navigate | `test_semantic_e2e.py::test_navigation` |
| back/forward | `test_scenarios.py::test_back_and_forward_across_real_navigations` |
| new tab / close tab / select tab | `test_scenarios.py::test_new_select_and_close_tab` (+ `test_closing_last_tab_is_refused_typed`) |
| text input | `test_semantic_e2e.py::test_fill_by_label_and_placeholder` |
| click by semantic locator | `test_semantic_e2e.py::test_click_by_role_and_name` |
| select/dropdown | `test_scenarios.py::test_select_dropdown_by_value_and_label` |
| checkbox/radio | `test_scenarios.py::test_checkbox_set_checked_and_unchecked`, `test_radio_selection_via_set_checked` |
| form submission (values asserted on result page) | `test_scenarios.py::test_form_submission_result_page_echoes_submitted_values` |
| SPA navigation (pushState, no full load) | `test_scenarios.py::test_spa_pushstate_navigation_without_full_load` |
| iframe interaction | `test_scenarios.py::test_iframe_click_and_read_inside_named_frame` |
| popup/new window | `test_scenarios.py::test_popup_window_appears_as_tab_and_is_interactable` |
| download (hash asserted) | `test_semantic_e2e.py::test_download_saves_file_with_matching_sha256` |
| upload (server echoes hash) | `test_scenarios.py::test_upload_fixture_file_server_echoes_matching_sha256` |
| browser-side error typed (dead port -> `dependency_unavailable`) | `test_scenarios.py::test_navigate_to_dead_port_is_typed_dependency_unavailable` |
| timeout typed | `test_scenarios.py::test_slow_navigation_is_typed_timeout_retryable` |
| stale/detached recovery via `with_retry` | `test_mutating_dom.py::test_with_retry_succeeds_once_target_stabilizes` (+ typed cases in the same file) |
| ambiguous locator (`match_count` > 1, `.first`) | `test_scenarios.py::test_ambiguous_locator_reports_match_count_and_clicks_first` |
| browser crash typed + managed relaunch | `test_backends.py::test_managed_browser_crash_is_typed_and_reconnect_restores` |
| disconnect typed + CDP reattach to fresh throwaway browser | `test_backends.py::test_cdp_disconnect_is_typed_and_reattach_to_fresh_browser` |
| cancellation of in-flight command | `test_commands_e2e.py::test_cancel_in_flight_browser_command_is_typed_cancelled` (unit: `test_commands.py`) |
| duplicate command / idempotency (incl. concurrent) | `test_commands_e2e.py::test_duplicate_browser_command_executes_exactly_once`, `test_concurrent_duplicate_browser_command_executes_exactly_once` (unit: `test_commands.py`) |
| existing-session attach path (throwaway browser) | `test_cdp_existing_session.py`, `test_backends.py::test_enrollment_registry_attach_and_capability_grant_enforced` |
| persistent dedicated profile | `test_backends.py::test_persistent_profile_preserves_state_across_relaunch`, `test_isolated_mode_shares_no_state_between_sessions` |
| capability declarations + orchestrator guard | `tests/unit/test_capabilities.py` |
| enrollment model / loopback-only / reserved transport | `tests/unit/test_enrollment.py` |

## M13 — Browser Worker (stdio protocol)

`browser_agent.worker` is the standalone process the owner-session companion
spawns on the device (`packages/protocol/BROWSER_CAPABILITIES.md`, binding
contract). It speaks newline-delimited JSON over stdin/stdout — **stdout is
protocol-only**; every log line goes to stderr
(`configure_logging(stream=sys.stderr)`) — and turns each `browser.*`
capability into calls against `BrowserSession`/`ManagedBackend`, adding
everything the M2 semantic engine didn't need on its own: sessions, risk
policy, page-kind/website-error classification, metadata/link extraction,
search, and the untrusted-content guards.

```
python -m browser_agent.worker --data-dir <dir> [--profile-dir <dir>]
       [--channel chrome|chromium] [--visible|--headless]
       [--idle-timeout-s 600] [--self-check]
```

`--self-check` prints the `hello` line (worker/protocol version, capability
list, browser channel/version detected **without opening a browser window**
— `browser_agent.detect` resolves the channel's executable and runs
`--version` as a plain subprocess; it only falls back to an actual
headless-launch-then-close if that fails) and exits `0` when the browser is
available, non-zero with a one-line stderr reason otherwise (e.g. Chrome not
installed).

### Sessions

`browser.session_open` creates or reuses one `BrowserSession` per
`session_id`, keyed off a `profile` (`research` — a persistent dedicated
profile under `--profile-dir`, never the owner's real `User Data`; or
`isolated` — a fresh non-persistent context) and a `policy` naming the
session's `allowed_risk_classes`. Reopening an existing session **never
widens** its policy — the effective set is the intersection of the existing
and requested sets (`browser_agent.policy.narrow_reopen`) — except when the
underlying browser process has died (crash/kill): a dead session is
discarded and recreated fresh rather than silently reused. An idle-timeout
background sweep closes sessions that saw no command for `idle_timeout_s`.
Commands against the *same* session run serially (one `asyncio.Lock` per
`session_id`); commands against *different* sessions run concurrently.

### Risk classes (`browser_agent.policy`)

Every capability has a static risk class — `READ`, `NAVIGATE`,
`REVERSIBLE_WRITE`, `EXTERNAL_COMMUNICATION`, `HIGH_IMPACT` — except
`browser.click`, whose class is resolved dynamically from the *resolved*
clicked element, in this priority order:

1. Accessible-name marker (buy/purchase/pay/delete/remove/send/submit
   order/satın al/öde/sil/gönder) -> `HIGH_IMPACT`
2. A submit control, or a click that would submit an enclosing `<form>` ->
   `EXTERNAL_COMMUNICATION`
3. `a[href]` without a JS click handler -> `NAVIGATE`
4. Otherwise -> `REVERSIBLE_WRITE`

`browser.download` is `HIGH_IMPACT` *and* additionally requires a non-empty
`authorization_ref` in the payload — missing either refuses with
`security_scope_error` before any I/O. Every enforcement happens **before**
the operation touches the page (`policy.enforce`), naming the missing class
in the refusal message.

| Capability | Risk class |
| --- | --- |
| `session_open`, `session_close`, `navigate`, `back`, `forward`, `tab_*`, `scroll`, `search`, `fetch_evidence` | NAVIGATE |
| `worker_status`, `inspect`, `find`, `wait`, `extract`, `snapshot`, `screenshot` | READ |
| `fill`, `select_option`, `set_checked` | REVERSIBLE_WRITE |
| `download` | HIGH_IMPACT (+ `authorization_ref`) |
| `click` | dynamic (see above) |

### `page_kind` — website error vs browser error (`browser_agent.page_kind`)

Every navigation-shaped result carries `page_kind ∈ ok | auth_wall | captcha
| error_page | blocked | empty` plus a `site_error` object when it isn't
`ok`. This is a **successful command** describing a website-level problem —
distinct from a *browser*-level problem (`timeout`, `dependency_unavailable`,
`ui_state_changed`, …), which is always a typed command error instead.
Detection order: `captcha` (recaptcha/hcaptcha/turnstile/"verify you are
human") -> `auth_wall` (password field on the landing page, HTTP 401/403, or
a login-title/heading marker incl. Turkish "oturum aç"/"giriş yap") ->
`blocked` (bot-block wording, HTTP 429) -> `error_page` (any other HTTP
≥ 400) -> `empty` (page loaded but rendered near-zero text) -> `ok`. The
worker never attempts to solve a CAPTCHA.

### Extraction (`browser_agent.extraction`) and search (`browser_agent.search_engines`)

`browser.extract` (modes `text`/`links`/`metadata`/`structured`/`all`) and
`browser.fetch_evidence` read metadata from `<meta property="article:
published_time">` -> `<meta name="date">` -> `<time datetime>` -> JSON-LD
`datePublished` (priority order), `og:site_name`/JSON-LD `publisher`,
`<html lang>`, `<link rel=canonical>`, and `meta[name=description]`
(absent -> `null`, never guessed); dates normalise to ISO-8601 UTC when
parseable, otherwise pass through unchanged. Links are deduped, filtered to
`http(s)`, and capped at 200. `fetch_evidence` prefers `<main>`/`<article>`
text over the whole body and counts `injection_markers` (never acts on them
— see below).

`browser.search` parses DuckDuckGo HTML (`html.duckduckgo.com/html`), Bing
and Brave result pages with BeautifulSoup, dropping ads/sponsored entries and
each engine's own domains. `engine="auto"` tries duckduckgo -> bing -> brave
in order, moving to the next engine on a `captcha`/`blocked`/`empty`
`page_kind` **or** on a genuine browser-level failure reaching that engine
(e.g. a network-level abort) — an explicitly-named engine gets no such
fallover, its failure propagates as-is. `provider_rate_limited` (retryable)
is raised only when every attempted engine ended `captcha`/`blocked`.
`recency_days` maps to each engine's own freshness parameter (`df`,
`freshness`, `tf`) where one exists.

### Google through the real UI, owner handoff (contract v1.1, §3a)

One session per research job: the worker keeps the loaded Google results tab
and the profile (cookies, locale, consent) between commands and between
normal runs — nothing is spoofed or masked (no stealth plugins, no
fingerprint/webdriver masking, no CAPTCHA solving of any kind).

`browser.search` for provider `google` (in `auto` or explicit) drives the
real page instead of navigating straight to a results URL: if the session's
current page is already a Google results page with a search box (same host
as `--google-base-url`, a `role=combobox` present), it types the query into
that box (`fill` + `Enter`) in place; otherwise it navigates to the Google
home page first (`--google-base-url`, default `https://www.google.com`; the
locale comes from the payload, else `--locale`, else the machine's user
locale) and then types + submits. Readiness is a DOM/navigation condition —
the results region (`#search`/`#rso`) or an interstitial
(`detect_google_interstitial` on the landed URL/HTML), polled on a bounded
~250 ms interval, never a fixed sleep — capped at ~10 s. `recency_days` is
applied to the already-loaded results page as Google's own time filter
(`append_recency_param` re-navigates the current results URL with
`tbs=qdr:x`) only when requested; that specific re-fetch is by URL, so it is
the one case where the result's `path` is `google_url` instead of the
default `google_ui`. Every other engine keeps its existing URL-based flow
(`path=fallback` when one of them answers).

Payload gains `"interstitial": "fallback" | "handoff"` (default `fallback`,
unattended: an interstitial is recorded as that attempt's outcome and the
next provider is tried, same as before). `"handoff"` (owner present): on an
interstitial the worker brings the Chrome window to the foreground
(`page.bring_to_front()`), leaves the page exactly as it is, and returns a
**successful** result with `"state": "waiting_for_owner_verification"`,
`page_kind` (`captcha`/`consent`), `provider: null`, `results: []`,
`"path": "handoff_pending"`, `"verification_url"` — nothing is retried in a
loop and nothing is solved. `browser.wait` gains `"for":
"verification_cleared"` (READ): polls the session's current page every
~500 ms (`search_engines.is_verification_cleared`, a pure decision function —
URL off `/sorry/`/`consent.google.*` and no interstitial markers in the
HTML) until cleared or `timeout_ms` elapses → `{"satisfied", "url",
"elapsed_ms"}`. After `satisfied: true` the same `browser.search` (same
`session_id`, same query, `interstitial="handoff"`) resumes: if the already-
loaded results page's search box still shows that query, the worker parses
it in place without retyping (`path=handoff_cleared`); otherwise it types the
query again through the normal flow.

`browser.fetch_evidence` gains `"tab": "same" | "new"` (default `same`):
`"new"` opens the URL in a fresh tab, extracts there, closes that tab and
reselects the previously-current tab (so a loaded Google results tab is
never disturbed), and the result carries `"tab_used"`. The old fixed
post-navigation sleep is replaced by a bounded `wait_for_load_state
("networkidle", …)` with exceptions swallowed; the audit log line is written
once the result is known, never before or during the interactive path.

`browser.worker_status`'s `sessions[]` entries gain `"current_url"` (query
string stripped via `redact_url`) so a caller can see what each open session
is looking at without a separate `browser.inspect` round trip; reopening an
existing session (`browser.session_open` on a live `session_id`) still never
recreates the browser or its tabs — it only ever narrows the policy.

### Untrusted content (`browser_agent.injection`)

Page text is data, never instructions: no worker operation ever derives a
navigation/click/download/config decision from what a page says — the only
inputs to any operation are command payloads. `injection.count_injection_markers`
counts instruction-like patterns (English + Turkish, verbatim from
`BROWSER_CAPABILITIES.md` §6) so Cloud Core can flag evidence as
`injection_suspected`; every result additionally passes a last-line
forbidden-key scan (`cookie`, `authorization`, `set-cookie`, `localstorage`,
`sessionstorage`, `password`, `token`, `secret`, `apikey` — matched on a
normalized, separator/case-stripped key so `apiKey`/`api-key`/`APIKEY` are
all caught) and a 48 KiB size cap with `truncated: true`.

## Playwright MCP note

Playwright MCP is used by the *development-time* Claude tooling (interactive
browsing while engineering). This package is the *runtime* adapter the Agent
OS itself executes. They share the Playwright engine but are distinct
integrations; nothing here depends on MCP.

## Running checks

From `services/browser` (use the absolute `uv.exe` path on the dev machine —
spawned-shell PATH is broken there):

```powershell
uv run ruff check .          # lint
uv run pytest -q             # unit suite (no browser binary needed)
uv run pytest -q -m browser  # E2E against real headless Chromium (throwaway)
uv run pytest -q -m live     # opt-in: real Chrome + real network (see below)
```

One-time browser provisioning: `uv run playwright install chromium`
(user-scope download, no UAC).

Tests serve the fixture site in `tests/fixtures/site` from a stdlib
`http.server` on a random loopback port — no internet access. Dynamic
endpoints: `POST /upload` (echoes the SHA-256 of the uploaded bytes),
`GET /slow` (deterministic slow response for timeout/cancellation scenarios)
and `GET /error503` (deterministic HTTP 503 for `page_kind=error_page`
scenarios). Crash tests terminate throwaway browser processes by PID; no
owner browser or profile is ever involved, and nothing binds non-loopback.
`tests/fixtures/site/hostile.html` carries real injection-style text and a
meta-refresh/auto-submit form timed to 30s (present in the DOM for
inspection, harmless during a normal test run) to prove the worker only
*reports* on hostile content, never acts on it. `google-home.html` /
`google-results.html` / `google-sorry.html` / `duckduckgo-results.html`
(contract §3a, `tests/browser/test_google_ui_e2e.py`) let the Google-through-
the-UI, owner-handoff and provider-fallback scenarios run fully offline
against real headless Chromium — a worker started with `--google-base-url
<fixture site>/google-home.html` types into the fixture's search box and
parses the fixture's results exactly like the real page, and
`?simulate=sorry` on that same fixture URL deterministically routes the form
to the interstitial fixture instead, for the handoff scenarios.

`-m live` (`tests/live/`) is opt-in, excluded from the default run, and hits
real public sites and a real search engine through the real installed Google
Chrome (`--channel chrome`): `browser.fetch_evidence` against a real news
page, `browser.search "AI agents" engine=auto`, and a real Google-UI search
in `interstitial="handoff"` mode (contract §3a) — Google may answer from a
qualification machine's address with its own unusual-traffic interstitial;
that is a valid, expected outcome (`state="waiting_for_owner_verification"`)
asserted by shape, not a failure. These are inherently
non-deterministic — target-side anti-bot measures can and do change the
outcome (observed live: a 403 from a real publisher classifies as
`auth_wall` per the contract's own HTTP-401/403 rule even when the real
cause is bot-filtering rather than a login wall; DuckDuckGo's actual
CAPTCHA challenge text doesn't match the contract's fixed marker list so it
surfaces as an empty result set rather than `page_kind=captcha`; Brave
Search hard-aborts the connection for automated requests, which `auto`'s
fallover now survives) — so treat a `live` failure as a qualification signal
to investigate, not a broken gate.
