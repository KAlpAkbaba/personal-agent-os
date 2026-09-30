# ADR (unnumbered) - The cloud allow-list: a cloud job ACTS only on the owner's listed sites

Context: ADR-0213 addendum, option 4. A cloud browser job must not click, fill or transfer
on a site the owner never named; reading is safe everywhere (the deny-list still bars acting on banks etc.).

Decision:
- ONE shared file `packages/protocol/browser-cloud-allowlist.json` (`version`, `rule`, `sites`),
  empty at first: a cloud job acts nowhere until the owner adds a site.
- `sites` holds registrable domains (lower-case, `site_of(d) == d`, contains a dot). A malformed entry
  is an error at load, never skipped. A host matches when it IS a site or a subdomain of one (`endswith("." + site)`,
  not substring).
- The deny-list wins: `app.webtask.sites.denied` (worker: `task_denylist.denied`) is asked first; a
  deny-listed site is never allowed even when listed.
- Cloud: `app/execution/allowlist.py` `acting_allowed(url) -> (bool, reason)`, reasons `not_on_owner_allow_list`,
  `deny_listed_site`; reads the bundled copy (`BUNDLED`). Worker: `browser_agent/cloud_allowlist.py` carries `SITES`
  verbatim; both suites hold it equal to the shared file. `app/execution/rule.py` is unchanged; task
  execution-target-wiring calls `acting_allowed`.
- Known limit: the registrable-domain check uses the small suffix table of sites.py, so a bare public
  suffix outside that table (e.g. `co.example`) would pass validation; the editor must also refuse it.

The editor (NOT built here) needs: `POST /v1/team/allowlist` body `{"site": "<registrable domain>"}` (and
`DELETE /v1/team/allowlist/{site}`); the API normalises, rejects deny-listed and non-registrable input with 422,
writes a ledger event (`allowlist.site_added` / `allowlist.site_removed`, site, actor=owner), and must persist
the list where the Cloud Core reads it at run time (the bundled JSON is the seed only; a DB-backed overlay
read by `sites()` is the follow-up) and push the new list to the worker with the job, since the worker's
verbatim copy is only the seed.

Consequences: an empty list means every cloud job is read-only; adding a site is the only widening path.
