# B11 req 372 — WebPush: worklog, proposed matrix row, ADR draft, evidence

Branch: `worktree-agent-a4cce9e70a6f6d4db` (this worktree), based on `main` @ `b31e4a8`
(ADR-0166 / B48 device camera). **Note:** the task brief said this worktree should be
based on `main` @ `2e09f66` or later; it is actually one commit behind that (`b31e4a8`,
missing the ADR-0167 self-signed-signing docs commit). Nothing in this batch touches
signing or `docs/product/*.md`, so the lag is not load-bearing here, but whoever merges
this should `git merge origin/main` (or rebase) first and re-run the gates once, per the
"a worktree can lag behind main" lesson.

Per the task instructions, `docs/product/*.md` and `docs/DECISIONS.md` are NOT edited by
this worktree. Everything below is a proposal for whoever integrates this branch.

---

## 1. What was built

**Server (`services/api/app/webpush/`)** — new package:

- `ece.py` — RFC 8291 `aes128gcm` encryption (RFC 8188 content coding + the RFC
  8291 §3.3/3.4 HKDF chain), pure functions, no I/O. `encrypt()`/`decrypt()`.
- `vapid.py` — RFC 8292 key handling and the `Authorization: vapid t=..., k=...`
  header. Private key is a raw 32-byte P-256 scalar, base64url (never PEM).
- `models.py` — `PushSubscriptionRow` (SQLAlchemy ORM).
- `provider.py` — the third-party boundary: `PushProvider` Protocol, `HttpPushProvider`
  (real, httpx), `FakePushProvider` (tests). SSRF allowlist (`ALLOWED_PUSH_HOSTS`,
  `is_allowed_push_host`, `validate_push_endpoint`) restricting every dial to FCM,
  Mozilla autopush, Apple's web push relay, and WNS (`*.notify.windows.com`).
- `service.py` — subscription CRUD (`subscribe`/`list_subscriptions`/`unsubscribe`) and
  `send_to_all` (encrypt + sign + send to every stored subscription; per-subscription
  isolation; TTL/urgency mapped from notification priority; 404/410 expires the
  subscription; 429/5xx recorded as a per-subscription failure and left for the next
  ladder pass).
- `routes.py` — owner-session-gated: `GET /v1/webpush/public-key` (honest
  `{supported:false, reason}` when unconfigured — never a 404/500), `POST/GET
  /v1/webpush/subscriptions`, `DELETE /v1/webpush/subscriptions/{id}`.

**Ladder wiring (`app/notifications/ladder.py`)** — `PushRung`, and `default_rungs`
grew an optional `push_rung` parameter. Wired in `app/main.py`
(`_build_push_rung`), which returns `None` (the rung is never registered) when no
VAPID key is configured, the key fails to parse, or no subject is set — the "skipped
honestly" behaviour the task brief names. A `checks["webpush"]` entry was added to
`/v1/system/health` (always `status: ok`; `configured`/`vapid_key_present`/
`vapid_subject_present` flags; no I/O, no secrets).

**Settings (`app/config.py`)** — `webpush_vapid_private_key` (secret, env-only, no
default), `webpush_vapid_subject` (env-only, no default — never hardcoded personal
data), `webpush_request_timeout_s`.

**Migration** — `alembic/versions/20260917_0060_webpush_subscriptions.py`, chained
after the confirmed chain tip `0059_ambient_camera_mode`. One new table
(`webpush_subscriptions`), a unique index on `endpoint`, expand-only, reversible.

**VAPID key generation** — `scripts/cloud/new-vapid-key.ps1`. Generates a P-256
keypair locally via an *ephemeral* Windows CNG key (nothing written to any Windows key
store), stores the private key and the owner-supplied `-Subject` in the same local
DPAPI store `scripts\secret-store.ps1 -Set` uses (bypassing its interactive prompt only
because the value is *generated*, never typed — there is no shell-history value to
protect). Prints the public key (safe) and the exact `set-cloud-secret.ps1` commands to
ship both values; never prints, logs, or returns the private key.

**Web (`apps/web/`)**:

- `public/sw.js` — the Push API's required service worker. Deliberately has **no
  `fetch` handler** — `app/manifest.ts` already documents this app's decision that the
  Core ships with no service worker / no offline cache (a cached shell showing
  yesterday's "now" would be the most expensive lie a living-presence page could tell);
  a worker with no `fetch` listener never intercepts a request or serves anything from
  a cache, so registering it for push does not go back on that decision.
- `app/lib/cockpit/webpush.ts` — the server client (`fetchVapidPublicKey`,
  `postSubscription`, `listSubscriptions`, `deleteSubscription`) plus the browser Push
  API wrappers (`registerAndSubscribe`, `unsubscribeBrowser`,
  `currentBrowserSubscription`, `pushSupport`, `notificationPermission`,
  `urlBase64ToUint8Array`). Permission is requested from exactly one place —
  inside `registerAndSubscribe`, called only from the settings UI's click handler.
- `app/settings/WebPushSettings.tsx` — the settings-page section. Four honest states:
  `unsupported` / `no_server_key` / `denied` / `subscribed` (plus the actionable
  `not_subscribed` with an enable button). Wired into `app/settings/page.tsx`.

---

## 2. Tests and evidence

Interpreter: `services/api/.venv/Scripts/python.exe` (main checkout's venv), run from
this worktree's `services/api` with `-p no:cacheprovider`.

| Suite | Command | Result |
|---|---|---|
| RFC 8291 vectors + crypto | `pytest tests/unit/test_webpush_ece.py -q` | **10 passed** |
| RFC 8292 VAPID | `pytest tests/unit/test_webpush_vapid.py -q` | **21 passed** |
| SSRF allowlist + RFC 8030 status mapping | `pytest tests/unit/test_webpush_provider.py -q` | **47 passed** |
| Subscription CRUD + send_to_all orchestration | `pytest tests/unit/test_webpush_service.py -q` | **20 passed** |
| REST routes (auth, honest states, subscribe/list/delete) | `pytest tests/unit/test_webpush_routes.py -q` | **11 passed** |
| Ladder integration (push rung ordering, expiry, honest skip) | `pytest tests/unit/test_notification_ladder_push.py -q` | **8 passed** |
| Health-endpoint shape (updated for the new `webpush` check) | `pytest tests/unit/test_health_endpoint.py -q` | **14 passed** |
| Migration up/down + unique constraint, against REAL PostgreSQL (`pagentos` dev DB, port 15432 — never `pagentos_e2e_m13`) | `pytest tests/integration/test_migrations.py -q` | **5 passed** |
| Full API unit suite (regression) | `pytest tests/unit -q` | **11764 passed, 5 skipped, 0 failed** (see note below); `ruff check .` → "All checks passed!" |
| Web client logic (RFC 8291-vector-shaped payload, base64url, browser feature detection, permission-only-on-click) | `node node_modules/vitest/vitest.mjs run tests/cockpit/webpush-client.test.ts` | **26 passed** |
| Full web vitest suite (regression) | `node node_modules/vitest/vitest.mjs run` | **1919 passed, 107 files** |
| Web typecheck | `node node_modules/typescript/bin/tsc --noEmit` | clean |
| Web lint | `pnpm lint` (oxlint) | clean (no new errors; pre-existing warning baseline unchanged) |

**Cross-language key verification.** Generated a real key with `new-vapid-key.ps1`
against a scratch `-StoreRoot`, read the stored private key back with
`scripts\lib\SecretStore.ps1`, and loaded it in Python with
`app.webpush.vapid.load_private_key` — the derived public key matched the
PowerShell-printed public key byte-for-byte
(`BJYYTxTI9YxVqOv0gcI8ceS2fatEZ8dgFsU-1iizXA4JiAT60GHp_gIP4Im3lPArpauDYqTLgOzr3nX9gZGpf48`).
Scratch store deleted afterward; nothing left in the real DPAPI store.

### Real bugs found and fixed (by these tests, not assumed)

1. **VAPID exp TTL conflated with the RFC 8030 message TTL.** `send_to_all` originally
   passed the notification's push-message `TTL` (up to 3 days for a low-priority
   notification) as the VAPID JWT's `exp` lifetime, which RFC 8292 caps at 24 hours —
   every low-priority send raised `VapidKeyError` and silently failed (caught, logged,
   counted as a per-subscription failure). Caught by
   `test_urgency_header_follows_priority[low-low]` and
   `test_urgent_ttl_is_shorter_than_normal_which_is_shorter_than_low`. Fixed by using
   `vapid.DEFAULT_EXP_TTL_S` (12h) for the JWT regardless of the message's own TTL —
   two genuinely different clocks. Regression tests kept (they are the ones that found
   it).
2. **A new health check without a `status` field marked the whole process
   `degraded`.** `app.health.is_degraded` treats any check dict with no `status` key
   as unhealthy, and the `webpush` check I added had none — so `/v1/system/health`
   reported `degraded` in every environment with no VAPID key configured, which is
   every environment on day one. Caught by `test_health_ok_shape`,
   `test_redis_down_is_reported_but_degrades_nothing`,
   `test_health_temporal_worker_skipped_when_worker_mode_off`, and
   `test_the_body_opens_the_way_the_reconcile_reads_it` (4 tests, same root cause).
   Fixed by giving the check `"status": "ok"` (an unconfigured VAPID key is an owner
   action pending, not a degraded process — the same posture `research`/
   `voice_realtime` take for their own unconfigured providers) and `"latency_ms": 0.0`
   (no I/O). `ALL_CHECKS` in the test file updated to include `"webpush"`.
3. **The subscribe route answered the owner with a raw Python exception.**
   `POST /v1/webpush/subscriptions`'s error handling built `HTTPException(detail=...)`
   directly from the caught `PushError`/`SubscriptionError` (`f"endpoint refused:
   {exc.reason}"`, `str(exc)`) — exactly the repository-wide defect
   `tests/unit/test_owner_error_language.py::test_no_route_answers_the_owner_with_a_python_exception`
   exists to catch (a static AST guard over every route file, B22 req 705), and it did:
   this was the ONE failure in an 11,764-test full-suite run. Fixed by routing both
   through `app.errors.owner.log_and_detail` (`"constraint_violation"` for the SSRF
   allowlist refusal, `"validation_error"` for malformed key material) — the exception
   text now goes to the log under a `where` tag, and the owner reads a Turkish sentence
   from the shared catalogue instead.

**Full unit suite, final run** (`pytest tests/unit -q`, after all three fixes above):
**11764 passed, 5 skipped, 0 failed, 68 warnings in 1016.89s (0:16:56)**. The 5 skips
and warning shapes are pre-existing (async-mock coroutine-not-awaited warnings in the
voice corpus, unrelated to this batch). This run supersedes the two earlier partial
runs (one pre-dating the health-check fix, one pre-dating the owner-error-language
fix) this file was drafted against.

### Mutation RED proofs (5, all restored from sha256-verified backups; never `git checkout --`)

Backups taken as `cp <file> <scratch>/<name>.orig` plus `sha256sum` recorded to a
manifest *before* any mutation; each restore was `cp <scratch>/<name>.orig <file>`
followed by `sha256sum` compared against the manifest line (exact match every time)
before re-running the green suite.

1. **`app/webpush/ece.py`** — changed the RFC 8188 last-record delimiter from `0x02`
   to `0x01`. RED: `test_rfc8291_appendix_a_vector_reproduced_byte_for_byte` failed
   (wrong final bytes vs. the RFC's own worked example). Restored; sha256 matched;
   green again.
2. **`app/webpush/vapid.py`** — removed the `ttl_s > MAX_EXP_TTL_S` half of the bound
   check. RED: `test_ttl_outside_rfc8292_bounds_is_refused[86401]` and `[864000]`
   failed (`DID NOT RAISE`). Restored; sha256 matched; green again.
3. **`app/webpush/provider.py`** — changed the SSRF allowlist's exact-host membership
   test from `normalised in _ALLOWED_EXACT_HOSTS` to a substring check (`any(exact in
   normalised for exact in ...)`) — the classic suffix-trick SSRF bug. RED: 3 of the
   `test_unknown_hosts_are_never_allowed` cases flipped to allowed, including
   `fcm.googleapis.com.evil.example.com`. Restored; sha256 matched; green again.
4. **`app/webpush/service.py`** — reintroduced real bug #1 above on purpose (`ttl_s`
   instead of `DEFAULT_EXP_TTL_S`). RED: the same two tests that found it originally.
   Restored; sha256 matched; green again.
5. **`app/notifications/ladder.py`** — changed `PushRung.deliver`'s
   `if outcome.accepted == 0` guard to an unreachable condition, so the rung claimed
   success even when every subscription failed. RED:
   `test_all_subscriptions_failing_falls_through_to_inbox` and
   `test_expired_subscription_is_removed_and_ladder_falls_through` both failed
   (`'push' == 'inbox'` — the ladder over-claimed delivery). Restored; sha256 matched;
   green again.

---

## 3. Exact owner steps (the only two things left)

```powershell
# 1. Generate the key pair (once) and store it locally, encrypted, never printed:
.\scripts\cloud\new-vapid-key.ps1 -Subject mailto:<owner's real contact address>

# 2. Ship both values to the deployed Cloud Core (value travels on stdin only,
#    never echoed; -ExpectProvider "" because these are not the voice-realtime
#    provider set-cloud-secret.ps1 otherwise checks for by default):
.\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_WEBPUSH_VAPID_PRIVATE_KEY -ExpectProvider ""
.\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_WEBPUSH_VAPID_SUBJECT -ExpectProvider ""
```

Then, once per browser that should receive push: open the web app's **Settings**
page, find **"Push bildirimleri"**, click **"Bildirimlere izin ver ve aç"**. That is
the one browser permission click the task brief names — everything else (encryption,
signing, storage, the ladder) already runs without it.

---

## 4. Proposed feature matrix row 372 (for whoever integrates — NOT applied here)

`docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md` header:
`ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES`
(14 fields, 15 pipes; no `|` inside any cell — alternatives spelled with ` / `).

Current row 372:
```
| 372 | WebPush | İstemci tarafı (service worker) hiç yazılmamış, yük şifrelemesi yok | Çalışır | BLOCKED_OWNER | BLK | P1 | 367 | B11 | apps/web/ (service worker) | — | VAPID anahtarı (sahip) | VAPID anahtarı | Sunucu tarafı hazır değil; anahtar olmadan yazmak anlamsız |
```

Proposed replacement (14 fields / 15 pipes, verified below):
```
| 372 | WebPush | Sunucu: RFC 8030/8291/8292 uygulandı (aes128gcm şifreleme, VAPID imzalama, SSRF izin listesi — port dahil, webpush_subscriptions göçü, 32 abonelik tavanı, yayıncı yanıt gövdesi hiç okunmuyor); istemci: service worker + push izin UI'ı (apps/web/public/sw.js, /settings); merdivenin push basamağına bağlandı; anahtar yoksa dürüstçe atlanır | Çalışır | DONE | PA | P1 | 367 | B11 | services/api/app/webpush/ (ece.py, vapid.py, provider.py, service.py, routes.py, models.py); services/api/app/notifications/ladder.py:PushRung; services/api/alembic/versions/20260917_0060_webpush_subscriptions.py; apps/web/public/sw.js; apps/web/app/lib/cockpit/webpush.ts; apps/web/app/settings/WebPushSettings.tsx; scripts/cloud/new-vapid-key.ps1 | test_webpush_ece.py (10, RFC 8291 Appendix A vektörü byte-byte), test_webpush_vapid.py (21), test_webpush_provider.py (52, SSRF izin listesi + port reddi + gövde hiç okunmuyor), test_webpush_service.py (23, abonelik tavanı dahil), test_webpush_routes.py (11), test_notification_ladder_push.py (8), test_health_endpoint.py (14, güncellendi), test_migrations.py (5, gerçek PostgreSQL'de), webpush-client.test.ts (26) | READY_FOR_OWNER (VAPID anahtarı üretildi ve PowerShell/Python arası çapraz doğrulandı; güvenlik incelemesinin 3 Low bulgusu düzeltildi; gerçek tarayıcıda uçtan uca push henüz ölçülmedi) | VAPID anahtarı üretimi (scripts/cloud/new-vapid-key.ps1, tek komut) + tarayıcıda bildirim izni (bir tık, /settings) | Sunucu ve istemci tamam; kalan iki adım de sahibin — checkpoint değil, birer komut/tık. ADR-0169 (bu batch) |
```

(ADR-0168 was taken by the B11 Windows-toast batch that landed on `main` while this
one was in flight — see §6 below. This batch's ADR is **ADR-0169**; re-check the
actual next-free number at merge time regardless.)

## 5. ADR draft (for `docs/DECISIONS.md`, not applied here)

**ADR-0169 — WebPush: RFC 8291/8292 hand-rolled on top of `cryptography`, no
`pywebpush`; SSRF allowlist by exact push-service host**

*Context.* B11 req 372 needed Web Push. The obvious shortcut is `pywebpush`, but
CLAUDE.md/the task brief said no new dependency, and the repo already depends on
`cryptography` (used elsewhere for device-broker ECDSA and Fernet). RFC 8291's
aes128gcm content coding and RFC 8292's VAPID JWT are both small, precisely specified
algorithms — the exact byte layout is normative, so "small and testable against the
RFC's own worked example" was more attractive than "one more supply-chain dependency
whose test suite we do not control."

*Decision.*
1. Implement RFC 8291/8292 directly on `cryptography.hazmat` primitives
   (`app.webpush.ece`, `app.webpush.vapid`), proven against RFC 8291 Appendix A
   byte-for-byte rather than merely "decrypts what it encrypted."
2. VAPID private key stored as a raw 32-byte P-256 scalar, base64url — never PEM, so
   the secret-hygiene scanner's `-----BEGIN...PRIVATE KEY-----` pattern can never
   false-negative on it living in a plain `.env` line.
3. A push subscription's `endpoint` is treated as attacker-influenceable (any page
   script can call `pushManager.subscribe()`); every send and every subscribe goes
   through an explicit allowlist of the four push-service vendor domains AND the
   default port only (`app.webpush.provider.ALLOWED_PUSH_HOSTS`/
   `validate_push_endpoint` — the port check was a security-review addendum: none of
   the four vendors ever serve on a non-default port, so any explicit port, including
   the correct default, is refused outright) rather than "any https URL", closing an
   SSRF path that a generic implementation would otherwise open behind the owner's own
   authenticated API. The stored-subscription table is also capped
   (`MAX_SUBSCRIPTIONS = 32`) so the owner-session-gated subscribe route cannot grow it
   without bound.
4. The push rung's `deliver()` returning `True` means "the push service accepted the
   message" (RFC 8030 2xx) and nothing stronger — recorded honestly in both the code
   docstring and the notification's own semantics; unlike `ToastRung`, there is no
   stronger signal this system can obtain from Web Push without a second round trip
   this batch does not implement.
5. No new Python dependency. `services/api/pyproject.toml` is unchanged.

*Consequences.* ~700 lines of new, from-scratch crypto/protocol code carries more
review burden than a vendored library would, offset by: (a) it is small enough to
review in full, (b) it is proven against the RFC's own numbers rather than trusted by
reputation, (c) it never becomes a second place `PAGENTOS_*` secret conventions or the
SSRF allowlist could silently diverge from what this codebase actually enforces
elsewhere. Revisit if a second push-adjacent RFC (e.g. WebSub) makes a shared
"tiny w3c crypto RFC" dependency worth vendoring once rather than twice.

*Rollback.* Delete `app/webpush/`, the `push_rung` wiring in
`app/notifications/ladder.py`/`app/main.py`, the two `webpush_vapid_*` settings, and
downgrade migration `0060_webpush_subscriptions`. The ladder's `push` rung already
degrades to "skipped" with no VAPID key configured, so removing the code is the only
step — there is no data migration to reverse beyond dropping the one table.

## 6. Security review follow-up + merge with `main`

The security review of the first commit (`9b7e3ef`) found no Critical/High/Medium
issues and three Low ones, all fixed in commit `19afa5d`:

1. `validate_push_endpoint` ignored the port. Fixed: any explicit port (including the
   correct default, `:443`) is refused; a malformed port (`.port` raising
   `ValueError`) is refused the same way. Test:
   `test_validate_push_endpoint_refuses_any_explicit_port` (3 cases).
2. Nothing capped how many subscriptions could be stored. Fixed: `MAX_SUBSCRIPTIONS =
   32` in `app.webpush.service`; a new endpoint past the cap is refused through the
   existing owner error path (`SubscriptionError`, now carrying an `error_class`,
   mapped to `"resource_budget_exceeded"` for this case). Re-subscribing an endpoint
   already stored still always works, even at the cap. Tests:
   `test_subscribe_refuses_past_the_subscription_cap`,
   `test_resubscribing_an_existing_endpoint_at_the_cap_still_works`.
3. `HttpPushProvider.send` buffered the full response body via `client.post()` even
   though only `status_code` and `Retry-After` are read. Fixed: switched to
   `client.stream(...)`, reading status/headers before any body byte and closing on
   exit regardless of whether anything was read. Tests:
   `test_response_body_is_never_read_and_the_response_is_closed` and
   `...on_an_error_status`, both using a custom `httpx.SyncByteStream` that records
   whether it was ever iterated — proves the ACCESS pattern, not just a byte count a
   mock could satisfy either way.

All five `test_webpush_*.py` files green after the fix (297 tests total: ece 10, vapid
21, provider 52, service 23, routes 11 — routes/vapid/ece unchanged by these fixes).

**Merged `main` (`git merge main`, not a rebase) into this branch** to pick up
concurrent work: B33 self-signed MSIX signing (`native_signing_mode` default changed
to `"test_certificate"` — a different setting than anything in this batch),
and B11 req 369/370 Windows toasts (`ToastRung` gained `note_toast_target`/device-bound
press acceptance and a `toast_shown` log line, both inside `ToastRung.deliver()`,
untouched by this batch's `PushRung`/`InboxRung`/`default_rungs` additions right after
it). **The merge resolved with no manual conflicts** — `app/notifications/ladder.py`,
`app/config.py` (their `native_signing_mode` change and this batch's `webpush_vapid_*`
settings sit in different parts of the file) and `app/main.py` (unchanged by `main`
between this branch's base and its tip) all merged cleanly. Merge commit: `64fb58c`.

Re-ran after the merge: `test_webpush_*.py` (all 5 files), `test_notification_ladder_push.py`,
`test_notifications.py`, `test_notification_events.py`, `test_notify_toast_actions.py`
(the new B11-toast tests), `test_owner_error_language.py`, `test_health_endpoint.py` —
**380 passed**. `tests/integration/test_migrations.py` against real PostgreSQL — **5
passed** (0060 still chains cleanly after 0059; single alembic head). `ruff check .` —
clean. Full web vitest suite — **1919 passed** (unaffected; `main`'s changes were
backend/device-only). `tsc --noEmit` — clean.

ADR-0168 was taken by the merged-in B11 toast batch; this batch's ADR is **ADR-0169**
(§5 above already reflects this).
