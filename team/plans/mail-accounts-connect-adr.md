# ADR-0298 - Mail and calendar accounts connected from the web: Gmail + Microsoft 365 over OAuth 2.0 + PKCE, several named accounts

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
6. **Reading**: a LISTING (inbox, poll, search) reads headers + the provider preview only
   (Gmail `format=metadata` + `snippet`, Graph `$select` with `bodyPreview`) - never the
   message or its attachments; reading ONE message (read, thread, attachment) fetches it raw
   (Gmail `format=raw` / Graph `/$value`) into the existing bounded RFC822 parser. One reader
   and one sender per account live for the process, each on ONE `httpx.Client`. Calendar: Google
   Calendar `events` (singleEvents) and Graph `calendarView`, read-only (writes stay CalDAV).
7. **Several accounts behind one provider**: `MultiAccountMailProvider` / `...Sender` /
   `MultiAccountCalendarProvider` read the account list on every call (a new account is live
   at the next question) and tag every message/event with the account's name. One failing
   account is skipped and reported; a calendar pass that missed an account is marked
   truncated, so the mirror deletes nothing on it. `mail_index` and drafts are keyed by the
   account's ID (`account_key` = `mail_accounts.id`; "" the env account in both wirings),
   never its name: a rename re-announces nothing, a draft made before a rename leaves from the
   same account (a draft whose account was disconnected says so and stays), and switching
   OAuth on beside the env account keeps its index. Each account's unread is counted on its
   own listing, before the merged one is cut to 50.
8. **Speech by name**: the inbox answer names every account ("İş hesabında 3 okunmamış posta
   var, Kişisel hesabında okunmamış posta yok efendim."). A draft names its account in the
   read-back ("Taslak (İş hesabından): ..."); a reply goes from the account the message came
   to; an unknown account name is refused, never sent from another. Send stays behind the
   existing read-back + confirmation gate (owner decision 2026-09-18/19).
9. **No OAuth client configured = no change**: the env account is used exactly as before.
   With a client configured, a still-configured env account rides along as "IMAP"/"Takvim";
   both names are reserved (an owner account cannot take them, in either case folding).
10. **Migration `0068_mail_accounts`** (reversible; one contract-phase step, below; down drops
    the account rows of the `mail_index` cache before restoring the old unique index).

## Contract phase

`0068` declares `contract-phase: ADR-0298`: its `upgrade()` drops the old unique index
`ix_mail_index_provider_message_id`, because one Message-ID must be able to sit in two of the
owner's accounts and that index forbids it. This is compatible with the old colour still
serving during the blue-green drain: the old code looks a `mail_index` row up by
`provider_message_id` with `.first()` / `.scalar()`, so a duplicate row never breaks its read
(the inspector checked it on Postgres, 3rd return), and its inserts get `account_key = ''`
from the server default. The old colour's single-column lookup keeps an index: the new
composite unique index starts with `account_key`, so `upgrade()` re-creates
`ix_mail_index_provider_message_id` NON-unique (the downgrade drops it and restores the unique
one). If 0298 is taken at merge time, the Proje Yöneticisi renumbers it together with the
`down_revision` re-point.

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

- Voice names the account for a new draft: `mail.draft` takes `account` ("İş hesabından").
- Mission-path sends (`app.executive.activities.get_mail_service`) still build the env-only
  service - follow-up card.
- The calendar agenda says each event's account ("Diş hekimi (10:00, Kişisel)").
- Follow-ups (inspector): an unreadable `mail_accounts` reads as "no account"; a `name_taken`
  after the token exchange leaves Google tokens unrevoked; the callback's code/state land in
  the access log (uvicorn) - each a card of its own.
