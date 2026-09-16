---
name: project-b11-webpush-status
description: B11 req 372 WebPush completion status, branch/commit, what remains for the owner
metadata:
  type: project
---

B11 req 372 (WebPush) completed 2026-09-17 on branch `worktree-agent-a4cce9e70a6f6d4db`
@ commit `6333011` (after a security review + `git merge main`; first landed at
`9b7e3ef`), in worktree `E:\AI\...\.claude\worktrees\agent-a4cce9e70a6f6d4db`. Not
pushed. Full server package at `services/api/app/webpush/` (RFC 8030/8291/8292, no new
dependency, `cryptography.hazmat` only). Migration `0060_webpush_subscriptions` chains
after `0059_ambient_camera_mode`. Ladder wiring: `app.notifications.ladder.PushRung` +
`app.main._build_push_rung`. Web: `apps/web/public/sw.js` (push+notificationclick
only, no fetch handler) + `app/lib/cockpit/webpush.ts` + `app/settings/
WebPushSettings.tsx`. VAPID key generation: `scripts/cloud/new-vapid-key.ps1`
(ephemeral CNG key; cross-verified against Python `app.webpush.vapid.load_private_key`
— public keys matched byte-for-byte). ADR draft renumbered to **ADR-0169** (ADR-0168
was taken by the B11 Windows-toast batch merged in from `main`).

Security review (no Critical/High/Medium; 3 Low, all fixed): endpoint port ignored by
the SSRF allowlist, no cap on stored subscriptions (now `MAX_SUBSCRIPTIONS=32`), and
`HttpPushProvider.send` buffering the full response body (now `client.stream(...)`,
body never read, connection always closed). `git merge main` (not rebase) picked up
concurrent B33 self-signed-MSIX and B11 Windows-toast work with **zero manual
conflicts** — different regions of `ladder.py`/`config.py`, `main.py` untouched by
`main` in that window.

Evidence: 117 new backend unit tests + 26 web vitest tests, all green. Full API unit
suite 11764 passed/5 skipped/0 failed. Full web vitest 1919 passed. `ruff check .`
clean. `next build`/`tsc --noEmit` clean. 5 mutation RED proofs (RFC 8188 delimiter,
RFC 8292 24h ceiling, SSRF allowlist substring bug, VAPID-exp/message-TTL conflation,
ladder over-claiming delivery), each restored from a sha256-verified backup.

**Owner steps remaining** (the only two things left): `scripts/cloud/new-vapid-key.ps1
-Subject mailto:...` then two `set-cloud-secret.ps1 ... -ExpectProvider ""` calls; then
one browser click on the Settings page's "Bildirimlere izin ver ve aç".

Full proposed feature-matrix row 372 text (15 pipes), an ADR-0168 draft, and complete
evidence are in `WORKLOG_B11_WEBPUSH.md` at the worktree root — NOT applied to
`docs/product/*.md` or `docs/DECISIONS.md` (task explicitly excluded those; whoever
merges applies them). Note: this worktree's `main` base (`b31e4a8`) is one commit
behind the tip that minted ADR-0167 — re-check the next-free ADR number at merge time.

See [[feedback_health_check_status_field_required]] and
[[feedback_owner_facing_http_exception_guard]] for two real bugs this batch's own test
suite caught that are worth knowing before writing the next new health check or route.
