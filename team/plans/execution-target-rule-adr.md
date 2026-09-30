# ADR (unnumbered): the execution_target rule — where a job runs (ADR-0213, PR 1)

Status: proposed by worker `execution-target-rule`; the lead numbers it at merge.

## Decision

`app/execution` holds the decision as pure functions (`decide`, `events`, `forced_target_of`).
It reads no device, database or model; the caller passes the job kind, the spoken word
(already resolved by ADR-0212's device layer), the site's needs and each target's availability.
The deny-list is read through `app.webtask.sites.denied` (the shared JSON), never copied.

## Rule table

| Situation | Chain considered (in order) | Result |
|---|---|---|
| `scheduled` | cloud | cloud; cloud offline → refused `no_target_available` (never owner_chrome/device) |
| `research`, `browser_task` | cloud → owner_chrome → device | first available |
| same, `needs_signed_in_session` | owner_chrome → device | first available; never cloud |
| `desktop` | device | device; offline → refused |
| `compute` | cloud | cloud only |
| `scheduled`, `desktop` or `compute` + `needs_signed_in_session` | (empty) | refused `no_eligible_target` |
| spoken `bulutta` / `bulut` (whole word) | (cloud) | cloud if allowed for the kind, else refused `forced_target_not_allowed` |
| spoken other word (device alias) | (device) | device if allowed and online; alias resolved to nothing → `forced_target_unavailable` (skip `device_unresolved`) |
| forced target offline | (forced) | refused `forced_target_unavailable`, never a fallback |
| cloud run meets `auth_wall`/`captcha`/`challenge` | (cloud) | outcome `ask_owner` (ADR-0207 d.6); no fallback |
| `involves_payment` | — | refused `payment_out_of_scope`, every kind and target |
| acting on a deny-listed site | — | refused `deny_listed_site`, every target; reading is not refused |

Checks run in this order: payment → deny-list (acting) → cloud blocker → forced word → chain.
Skip reasons: `cloud_offline`, `owner_chrome_not_enrolled`, `owner_chrome_device_offline`,
`device_offline`, `device_unresolved`.

## Events (`events(decision)`)

`execution.fallback` once per skipped target (with `reason`), then exactly one
`execution.selected` or `execution.refused` (`ask_owner: true` on a wall). The three strings are
constants in `app/execution/vocabulary.py`; the lead registers them in `app/ledger/vocabulary.py`.

## Open question (decided by nobody here): the owner's "no unattended task" (ADR-0207 d.3) for a cloud job

A cloud job runs while no owner sits at any device. Options:
1. Cloud jobs are exempt: unattended by nature, bounded by the deny-list, no-payment and ask-owner-on-wall rules.
2. A cloud job needs a per-job (or per-schedule) owner approval recorded once, then runs unattended.
3. Cloud jobs are read-only until the owner has watched a first supervised run of that job.
4. Cloud may only act on sites the owner has allow-listed; everything else is read-only.

## Notes

- Signed-in research falls back to `device` after owner_chrome (a device browser may hold the session); reversible, one tuple in `_CHAINS`.
- Not built here: the wiring into the broker/API, the ledger registration, availability probes.
