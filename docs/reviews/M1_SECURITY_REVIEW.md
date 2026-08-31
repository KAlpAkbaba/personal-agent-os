# M1 Security Review — 2026-08-31

Independent review of the M1 additions (device broker, device protocol v1, Windows agent) at git HEAD after M1 gates passed. **No High/Critical findings.**

## Findings and dispositions

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | Medium (forward-looking) | Named-pipe identity model does not survive the production Session 0 topology: pipe name and DACL both derive from the *creating* process identity, and there is no mutual auth beyond ACL+name. Works in M1 (both processes run as the same interactive user) but breaks — or invites an unsafe ACL loosening — once the Device Service runs as a real Windows Service (SYSTEM). | **Tracked as a hard gate for the Windows-Service installation step** (owner action in OWNER_ACTIONS_MINIMAL.md): before that ships, decide service identity explicitly and either run the service as the owner account, or ACL the pipe to the known owner-session SID plus add client-SID verification (`RunAsClient`/`GetImpersonationUserName`). The same identity split applies to the key-store `DataDir` default. |
| 2 | Low | Broker WS endpoint had no frame-size cap below uvicorn's 16 MiB default, including pre-auth. | **Fixed at M1**: uvicorn now launched with `--ws-max-size 65536` (E2E/dev scripts); production deployment must carry the same flag (note for the compose/IaC work). Agent already self-caps at 1 MiB. |
| 3 | Low | `POST /commands` accepted unbounded payload JSON into the DB. | **Fixed at M1**: `CreateCommandRequest` validates serialized payload ≤ 64 KiB (422 beyond), with unit test. |
| 4 | Low | Client-supplied `X-Trace-Id` was unvalidated and flowed into `String(128)` DB columns (oversized header → 500). | **Fixed at M1**: middleware sanitizes (charset allowlist, 128-char truncation, fallback to generated UUID), with unit tests. |

## Clean areas (verified)

Enrollment tokens: 32-byte `secrets` entropy, SHA-256 at rest, atomic single-use consumption (no TOCTOU). ECDSA handshake: fresh 32-byte nonce per attempt (no replay), signature over `nonce||device_id`, DER and P1363 accepted safely, indistinguishable rejection genuinely implemented (dummy-key equalized path) and integration-tested. Idempotency: DB unique constraint with race fallback; monotonic ack machine with conflict detection. Loopback guard uses the real TCP peer, not spoofable headers. Agent: private key PKCS#8 with explicit user-only DACL; fixed absolute-path allowlist with `ArgumentList` (no shell injection); no TLS-validation bypass; no inbound socket (outbound WS + local named pipe only). Audit rows capped and secret-free on both sides. No new tracked secrets. E2E/gate scripts: no destructive defaults, opt-in E2E, scoped process cleanup.

Security-testing scope enforcement remains not-yet-applicable (only capability is the 2-entry `desktop.open_application` allowlist); the Authorized Asset Registry gate remains tracked from the M0 review for M6+.
