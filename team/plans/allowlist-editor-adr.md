# ADR (unnumbered) - The allow-list editor: owner sites in team_state rows, seed stays the JSON

Context: ADR-0218 named the editor's API and left the store open ("a DB-backed overlay").

Decision:
- The owner's sites are `team_state` rows, `kind="allowlist"`, `key` = registrable domain, `doc` =
  `{added_at, added_by}` (added_by = "shell"). No new table, no migration: `kind` is String(16), no CHECK.
- `app/execution/allowlist_store.py` merges seed (`allowlist.sites()`, the shared JSON, stays empty-at-first) and
  rows at every call; `acting_allowed(url)` keeps the seed module's contract, deny-list first. A row that is
  deny-listed (written behind the editor) is ignored. Unbound (no `bind()`), it answers from the seed only.
- `POST /v1/team/allowlist {site}` (200, `already_listed` when present, no second event), `GET`, and
  `DELETE /v1/team/allowlist/{site}` (404 not listed; 409 `seed_site`). Owner session. 422 codes:
  `empty_site`, `not_a_registrable_domain` (incl. subdomain, IP, junk, >80 chars), `bare_public_suffix`,
  `deny_listed_site`. A bare suffix = one label, or two labels whose first is a second-level word and whose
  last is a 2-letter ccTLD or not an open gTLD (`com.tr`, `co.uk`, `co.example` refused; `co.com`, `web.com.tr` taken).
- Ledger events (subsystem `team`): `allowlist.site_added`, `allowlist.site_removed`; detail `{site, actor:"owner",
  channel:"shell"}`; recorded BEFORE the row, a refusal (503 `ledger_refused`) writes nothing.
- Shell only: no voice channel for widening the list.

Consequences: adding a site applies at once in the API process. The worker still receives only what the
dispatch sends: the lead must pass `effective_sites()` with the job.
