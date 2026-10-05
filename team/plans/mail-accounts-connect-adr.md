# ADR (unnumbered) - Mail and calendar accounts connected from the web: Gmail + Microsoft 365 over OAuth 2.0 + PKCE, several named accounts

Card: mail-accounts-connect (cycle d20261005). The owner, 2026-10-05: "e-posta bağlamayı
arayüzden ver, Gmail ve M365 bağlayayım, 3 hesap, isimlendirmek istiyorum".

## Context

Until now: one IMAP/SMTP account and one CalDAV/ICS calendar from env settings. Microsoft 365
no longer accepts password IMAP (basic auth retired) and Gmail wants OAuth; the owner wants
three accounts, each with a name he chooses, connected from the page.

## Decision

1. **Plain `httpx`, no SDK (integrator step).** The flow is three form posts (code exchange,
   refresh, Google revoke) and two profile GETs. `google-auth`/`google-auth-oauthlib`
   (Apache-2.0) and `msal` (MIT) are licence-compatible but add two dependency trees, their
   own token caches (a second place a token could live) and nothing the tests need; `httpx`
   is already a dependency. No third-party relay. `THIRD_PARTY_COMPONENTS.md`: no change.
2. **Authorization code + PKCE (S256)** for both providers. `start` stores only the SHA-256 of
   the `state` and the verifier Fernet-encrypted (`mail_account_pending`, 15 min). The callback
   (`GET /v1/accounts/oauth/callback`) cannot carry the owner's bearer token (a top-level
   navigation from Google/Microsoft), so the single-use hashed state IS its authority: the
   pending row is deleted before anything is sent; unknown/replayed/expired states are refused
   before any request leaves. It answers with a Turkish page and redirects nowhere. It joins
   the deliberate unauthenticated list (ADR-0027 family) - see ALAN_ISTEGI.
3. **Tokens at rest**: Fernet with a key from `PAGENTOS_ACCOUNTS_TOKEN_SECRET` via the existing
   `app.voice.crypto.derive_fernet_key`. The dev default is refused for any non-loopback base.
   No route returns a token column (`AccountsService._public` is the only shape); logs carry
   the exception class or HTTP status, never a body.
4. **Scopes**: Google `gmail.readonly gmail.send calendar openid email` (offline + consent);
   Microsoft `offline_access openid email User.Read Mail.Read Mail.Send Calendars.ReadWrite`,
   tenant `common` (work + personal). A rotated Microsoft refresh token replaces the old one.
5. **Disconnect**: Google - revoke the refresh token, then delete. Microsoft has no per-app
   revoke of a delegated token (`revokeSignInSessions` would sign the owner out of every app),
   so the tokens are deleted and the answer names myapps.microsoft.com for the consent.
6. **Reading**: Gmail `format=raw` / Graph `/$value` feed the existing bounded RFC822 parser,
   so one parser and one set of MIME bounds serve IMAP, Gmail and Graph. Calendar: Google
   Calendar `events` (singleEvents) and Graph `calendarView`, read-only (writes stay CalDAV).
7. **Several accounts behind one provider**: `MultiAccountMailProvider` / `...Sender` /
   `MultiAccountCalendarProvider` read the account list on every call (a new account is live
   at the next question) and tag every message/event with the account's name. One failing
   account is skipped and reported; a calendar pass that missed an account is marked
   truncated, so the mirror deletes nothing on it. `mail_index` is keyed (account, Message-ID).
8. **Speech by name**: the inbox answer names every account ("İş hesabında 3 okunmamış posta
   var, Kişisel hesabında okunmamış posta yok efendim."). A draft names its account in the
   read-back ("Taslak (İş hesabından): ..."); a reply goes from the account the message came
   to; an unknown account name is refused, never sent from another. Send stays behind the
   existing read-back + confirmation gate (owner decision 2026-09-18/19).
9. **No OAuth client configured = no change**: the env account is used exactly as before.
   With a client configured, a still-configured env account rides along as "IMAP"/"Takvim".
10. **Migration `0068_mail_accounts`** (expand-only, reversible; down drops the account rows
    of the `mail_index` cache before restoring the old unique index).

## For the owner (READY_FOR_OWNER)

Redirect URL to register at both providers (exactly):
`https://<Cloud Core tailnet name>.ts.net/v1/accounts/oauth/callback`
(the page Ayarlar > Hesaplar, `/settings/accounts`, shows the real one once
`PAGENTOS_ACCOUNTS_PUBLIC_BASE_URL` is set).

- Google Cloud Console: enable Gmail API + Google Calendar API; consent screen External, add
  yourself as test user, then "Publish app" (In production) - in Testing mode Google drops the
  refresh token after 7 days; the "unverified app" warning is expected for your own app.
  Credentials > OAuth client ID > Web application > the redirect URL above.
- Microsoft Entra: App registrations > New; "any organizational directory and personal
  Microsoft accounts"; Web redirect URI as above; API permissions (Graph, delegated): Mail.Read,
  Mail.Send, Calendars.ReadWrite, User.Read, offline_access (a work tenant may need admin
  consent); Certificates & secrets > new client secret (renew before it expires).
- Cloud Core env: `PAGENTOS_ACCOUNTS_PUBLIC_BASE_URL`, `PAGENTOS_ACCOUNTS_TOKEN_SECRET` (a long
  random value, kept), `PAGENTOS_ACCOUNTS_GOOGLE_CLIENT_ID/_SECRET`,
  `PAGENTOS_ACCOUNTS_MICROSOFT_CLIENT_ID/_SECRET`; restart. Then connect the three accounts on
  the page and name them. Sending also needs the existing `PAGENTOS_MAIL_SEND_ENABLED`.

## Consequences / follow-ups

- Voice cannot yet NAME the account for a new draft ("İş hesabından gönder"): the service takes
  `account`, the voice tool (`tools_mail.mail_draft`) does not pass it yet - follow-up card.
- Mission-path sends (`app.executive.activities.get_mail_service`) still build the env-only
  service - follow-up card.
- Gmail `format=raw` downloads attachments with the message on a listing; acceptable at the
  poll's `since` watermark, revisit if an inbox listing is slow.
