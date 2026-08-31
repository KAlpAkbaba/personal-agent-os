# M2 Security Review — 2026-08-31

Independent review of the M2 browser agent (backends, enrollment, commands, session) after M2 gates passed. Companion verification: independent test-engineer reproduced all M2 acceptance criteria PASS with zero defects.

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | High | `is_loopback_endpoint` used `host.startswith("127.")` — a routable DNS name like `127.evil.example` passed the loopback gate, making a remote CDP attach possible via a tampered enrollment record. | **Fixed at M2**: host must be literal `localhost` or an IP literal with `ipaddress.is_loopback`; DNS names are never trusted. Regression tests for `127.evil.example`, `127.0.0.1.evil.example`, `localhost.evil.example`. |
| 2 | Medium-High | No URL-scheme allowlist on `navigate`/`new_tab` — `file://` (local file read/exfil) and `javascript:` (script execution in an attached authenticated origin) were reachable through the automation API. | **Fixed at M2**: `require_navigable_url` allowlists `http`/`https` (+`about:blank`) in session and backend paths; `validation_error` otherwise. Unit + browser regression tests. |
| 3 | Medium | Full URLs including query strings were logged in telemetry/evidence (OAuth callbacks, magic links, tokens). | **Fixed at M2**: `redact_url` strips query+fragment at every URL logging/evidence site (navigate, new_tab, resolve/act evidence, a11y snapshot, escape hatch). Unit test. |
| 4 | Medium | `upload()` accepted arbitrary local paths (page-content-influenced exfiltration surface); `screenshot(path=…)` was the arbitrary-write mirror. | **Fixed at M2**: sessions take an explicit `file_io_root`; upload sources and screenshot paths must resolve under it, otherwise `validation_error`; with no root configured both are refused. Browser regression test. |
| 5 | Low | Idempotency replay keyed only on `idempotency_key` — a key collision could silently return another command's result. | **Fixed at M2**: optional `op_fingerprint` binds recorded/in-flight terminals to command identity; a mismatched duplicate fails loudly (`validation_error`). Unit test. Orchestrator should pass a capability+args hash when wiring in. |
| 6 | Low | Enrollment registry file (the future real-browser authorization gate) has atomic writes and fail-closed parsing but no explicit ACL hardening. | **Tracked**: apply the M1 keystore explicit-DACL treatment to the registry file before the extension-bridge transport ships (same gate as M1 finding #1 owner action). |

## Clean areas (verified)

Real-profile guard robust against traversal/case/symlink tricks; `ExistingSessionBackend` never launches and never sets debug ports itself; `reconnect()` cannot be retargeted mid-session; `extension_bridge` transport unreachable (validation_error); frame-name injection blocked (quote/backslash rejected); download filenames sanitized against traversal; `fill()` values never logged; fixture server loopback-only with no internet dependency; CI browser job pinned and secret-free; no tracked secrets; Playwright hash-pinned; no path lets an agent touch the owner's real browser/profile without an enrollment record; no coordinate clicking outside the single warning-logged escape hatch.
