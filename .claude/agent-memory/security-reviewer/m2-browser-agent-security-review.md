---
name: m2-browser-agent-security-review
description: Summary of the M2 browser agent (services/browser) security review findings (2026-08-31) and where the writeup lives
metadata:
  type: project
---

Reviewed M2 additions (`services/browser/browser_agent`: backends.py, enrollment.py, commands.py, session.py,
targets.py, errors.py, obs_logging.py, plus tests/fixtures/CI) at git HEAD (commit 42c7741, "feat(m2): browser
backend adapter model, enrollment and full scenario matrix", after d132a0f). Full findings were returned
directly in the review response, not saved as a repo file — this memory is the pointer/summary.

**One High finding** (loopback-check bypass), rest Medium/Low. Design quality is otherwise strong: frame-name
CSS-injection guard is correctly implemented (rejects `"`/`\` before building the attribute selector),
download filename path-traversal is blocked via `Path(...).name`, `fill()` values are deliberately excluded
from telemetry (only the target spec is logged, never entered secrets), real-profile-dir guard is
case-insensitive and resolves symlinks/junctions, `ExistingSessionBackend` never launches a browser or adds a
debug-port flag itself, reserved `extension_bridge` transport is unreachable, fixture HTTP server binds
`127.0.0.1` only.

Key findings worth remembering (see also [[m1-device-broker-security-review]] for the sibling M1 writeup and
[[project_m1_pipe_identity_gap]] for the still-open forward-looking item from that review):

1. **High** — `is_loopback_endpoint()` in `browser_agent/enrollment.py` (~line 56-67) uses
   `host.startswith("127.")` instead of validating the hostname is actually a `127.0.0.0/8` IP literal (or
   `localhost`/`::1`). A hostname like `127.evil.example` or `127.0.0.1.evil.example` passes the "loopback"
   gate as a bare string match but DNS-resolves off-box, so `ExistingSessionBackend`/`connect_over_cdp()`
   would attach to an attacker-controlled CDP endpoint while the code believes it enforced loopback-only.
   This is the *only* real gate in M2 (EnrollmentRegistry persistence/owner-approval is explicitly deferred),
   so it matters even before the extension bridge ships. Fix: use `ipaddress.ip_address(host).is_loopback`
   plus an exact `"localhost"` string check — no prefix/substring matching on hostnames. No test currently
   covers this shape of endpoint (existing parametrized rejection tests in `tests/unit/test_enrollment.py`
   only try non-"127."-prefixed hosts).
2. **Medium-High** — `BrowserSession.navigate()`/`new_tab()` (`session.py`) have no URL-scheme allowlist
   before `page.goto()`. `file://` enables local file read + exfiltration via `read_text`/`screenshot`/
   `download`; `javascript:` executes in the currently-loaded origin's context, which is a session-hijack
   primitive if that origin is the owner's authenticated site via `ExistingSessionBackend`. This is the load-
   bearing prompt-injection-boundary gap in M2 — nothing stops page content (or a naive orchestrator) from
   driving `navigate()` to a dangerous scheme. Fix: allowlist `http(s)`/`about:blank` explicitly in both
   methods.
3. **Medium** — full URLs (with query strings) are logged in cleartext structured JSON on every
   navigate/new_tab/download op and in `BrowserError.evidence["url"]`/`["endpoint_url"]` on failures. OAuth
   callback/magic-login/reset tokens in query strings would land in logs verbatim. Fix: strip query/fragment
   before logging, keep scheme+host+path only.
4. **Medium** — `upload()` (and `screenshot(path=...)`) accept an arbitrary caller-supplied local filesystem
   path with no sandboxing to a designated directory (unlike `download()`'s `save_dir` pattern). Defense-in-
   depth gap for prompt-injection-driven exfiltration ("upload this file") even though the primary control
   belongs to the orchestrator's tool-call authorization layer.
5. **Low** — `BrowserCommandExecutor.execute()` (`commands.py`) keys replay solely on caller-supplied
   `idempotency_key`, not bound to `command_id`/op identity/args (mirrors ADR-0017 device semantics by
   design). Fine as long as upstream key generation is unique-per-command; no enforcement exists in this
   module if that assumption is ever violated.
6. **Low** — `EnrollmentRegistry` file persistence (`enrollment.py` `_save`/`_load`) does atomic
   write-then-`os.replace` correctly (no torn writes) and fails closed on corrupt JSON, but applies no
   explicit file-permission hardening — inherits parent dir ACL. Same class of gap as the M1 device
   keystore/pipe DACL item; worth the same explicit-DACL treatment before the extension-bridge milestone
   since this file becomes the sole gate for attaching to the owner's real authenticated browser.

Disposition style to match (see `docs/reviews/M0_SECURITY_REVIEW.md` / `M1_SECURITY_REVIEW.md`): findings
table with Severity/Finding/Disposition, plus a "Clean areas" paragraph, if asked to produce
`docs/reviews/M2_SECURITY_REVIEW.md`.
