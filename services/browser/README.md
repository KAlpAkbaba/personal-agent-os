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
    BrowserSession, ManagedBackend, ExistingSessionBackend,
    BrowserEnrollment, EnrollmentRegistry, TargetSpec,
    BrowserCommandExecutor, CancelToken, with_retry, require_capability,
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
await session.back(); await session.forward()
await session.new_tab(url); await session.list_tabs()
await session.select_tab(0); await session.close_tab(1)
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
```

One-time browser provisioning: `uv run playwright install chromium`
(user-scope download, no UAC).

Tests serve the fixture site in `tests/fixtures/site` from a stdlib
`http.server` on a random loopback port — no internet access. Dynamic
endpoints: `POST /upload` (echoes the SHA-256 of the uploaded bytes) and
`GET /slow` (deterministic slow response for timeout/cancellation scenarios).
Crash tests terminate throwaway browser processes by PID; no owner browser or
profile is ever involved, and nothing binds non-loopback.
