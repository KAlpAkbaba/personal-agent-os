# pagentos-browser — semantic browser automation adapter (M2)

Provider-neutral browser automation package (`browser_agent`) used by the
Personal Agent OS runtime. Built on Playwright (pinned in `uv.lock`), exposed
through semantic operations and a typed error taxonomy.

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
  not expressible in a `TargetSpec`; an attempt to pass unknown fields (e.g.
  `x`/`y`, `xpath`) fails with a typed `validation_error`.
- `accessibility_snapshot()` returns the structured ARIA tree
  (`aria_snapshot`), supporting accessibility-first perception instead of
  pixels.
- `screenshot()` exists for diagnostics/telemetry only, never for control.
- The only coordinate-based entry point is `escape_hatch_click_xy`, named to be
  impossible to use accidentally. Every call logs a
  `browser.escape_hatch_used` warning so telemetry surfaces any drift toward
  the lowest rung. Exactly one explicitly-marked fallback test exercises it;
  all other tests are semantic.

## Public API

```python
from browser_agent import BrowserSession, TargetSpec, BrowserError, with_retry

session = await BrowserSession.launch_dedicated(headless=True)
await session.navigate("http://127.0.0.1:8000/index.html")
await session.click(TargetSpec(role="button", name="Greet"))
await session.fill(TargetSpec(label="Your name"), "Alp")
text = await session.read_text(TargetSpec(test_id="greeting"))
tree = await session.accessibility_snapshot()
result = await session.download(TargetSpec(role="link", name="Download sample"))
# result.path, result.sha256
await session.close()
```

`with_retry(op, attempts=3)` retries only errors whose `retryable` flag is
True, with exponential backoff; terminal errors propagate immediately. When
attempts are exhausted the last typed error is re-raised with
`evidence["retry"]` attached (the original `error_class` is preserved — the
orchestrator owns any collapse to `retry_exhausted`).

## Error taxonomy mapping

Every operation raises only `BrowserError(error_class, message, retryable,
evidence)`. Classes are the browser-relevant subset of
`docs/API_AND_PROTOCOLS.md` §8. Classification is principled: it keys on the
Playwright exception **type**, the explicit operation **phase** (`connect`,
`navigate`, `resolve` = waiting for the semantic locator to attach, `act` =
acting on a resolved target), and — only to split otherwise-identical types —
stable marker strings emitted by Playwright's actionability/connection
subsystems. The authoritative table lives in the `browser_agent/errors.py`
module docstring; summary:

| Playwright failure (type + phase [+ marker]) | error_class | retryable |
| --- | --- | --- |
| `TimeoutError` in phase `resolve` (locator never attached — target resolves to nothing) | `ui_target_not_found` | no |
| `TimeoutError` in phase `act` with an actionability marker ("intercepts pointer events", "element was detached", "not visible", "not stable", …) | `ui_state_changed` | yes |
| `Error` in phase `act`/`navigate` with a DOM/navigation-race marker ("Element is not attached to the DOM", "Execution context was destroyed", "Frame was detached", …) | `ui_state_changed` | yes |
| `TimeoutError` in any other case (navigation, plain action timeout) | `timeout` | yes |
| Any `Error` in phase `connect`, or any `Error` carrying a connection/liveness marker ("connect ECONNREFUSED", "net::ERR_…", "Target … has been closed", …) | `dependency_unavailable` | yes |
| Malformed `TargetSpec` / bad arguments (caught by our validation, never reaches Playwright) | `validation_error` | no |
| Anything else | `internal_bug` | no |

`evidence` always carries `op`, `phase`, the target spec, the current URL where
relevant, and the truncated Playwright message.

## Dedicated vs existing-session (CDP)

- **Dedicated** — `BrowserSession.launch_dedicated(headless=...)` launches a
  Playwright-managed Chromium. Fully isolated; the default for agent tasks.
- **Existing session (CDP)** — `BrowserSession.connect_existing_cdp("http://127.0.0.1:9222")`
  attaches over the Chrome DevTools Protocol to an already-running
  Chromium-family browser started with `--remote-debugging-port=<port>`.
  `close()` disconnects without terminating the external browser.

Attaching the owner's real Edge/Chrome later (documented only, not wired yet):

1. Start the owner's browser with its normal profile plus
   `--remote-debugging-port=<port>` (bound to loopback), e.g.
   `msedge.exe --remote-debugging-port=9222`; the owner-session companion is
   the natural place to manage that lifecycle. Then attach via
   `connect_existing_cdp`. This reuses existing logged-in sessions/cookies.
2. Alternatively, a future browser-extension bridge can expose an equivalent
   attach point without restarting the browser with a debug flag; it would sit
   behind the same `BrowserSession` interface.

The CDP route is E2E-tested by launching a separate throwaway Chromium with a
random `--remote-debugging-port` — no real user browser is touched by tests.

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
uv run pytest -q -m browser  # E2E against the real headless Chromium
```

One-time browser provisioning: `uv run playwright install chromium`
(user-scope download, no UAC).

Tests serve the static fixture site in `tests/fixtures/site` from a stdlib
`http.server` on a random loopback port — no internet access. The mutating
fixture page deterministically produces `ui_target_not_found` (element removed
on a timer) and `ui_state_changed` (pointer-intercepting overlay that later
removes itself, which also demonstrates `with_retry` recovery).
