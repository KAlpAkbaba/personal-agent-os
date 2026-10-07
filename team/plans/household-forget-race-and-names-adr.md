# ADR draft: household-forget-race-and-names

Status: proposed (worker-4, cycle d20261007). Source: test team round t-r10070152 (staging
da3e26b9, tester-4, ev-stoku; results under K:/AI/tmp-team/testteam/t-r10070152/tj4).

## Re-run table (each case first re-run on this branch's base, main b1f8ef94 + Stage 59 93122258)

| Case | Staging da3e26b9 (test team) | Re-run on main b1f8ef94 | After this card |
|---|---|---|---|
| 8 concurrent DELETE /items/{id} (unut) | 200=2 404=6 | window held: 200=8 (dev PG, scratch DB) | 200=1 404=7, 0 items, 0 events |
| 16 concurrent, half 'bitti' half unut | statuses [200,500], 5xx=6 | 500 (StaleDataError: UPDATE matched 0 rows) | no 5xx, one forget 200, end state explained |
| 32 concurrent, half 'bitti' half unut | statuses [200,404,500], 5xx=2 | 500 (same) | same as 16 |
| name "iki şişe süt" / "iki sise sut" | 200, one item "iki şişe sütü" | still (display "iki şişe sütü") | one item süt, list_quantity "iki şişe" |
| name "taze süt" | stored "taze sütü" | still | "taze süt" |
| name "esmer şeker" (/items) | stored "esmer şekeri" | still | "esmer şeker" |
| ham gövdeler: 100000-deep JSON | 500 | still (json.loads RecursionError) | NOT FIXED: routes.py outside the area - strict xfail + ALAN_ISTEGI |
| RTL mark "\u202etüs" (not on this card) | 200 | not re-run; clean_name refuses only Cc, U+202E is Cf | not in scope |

## Decisions

1. **The item row is locked for the write** (`SELECT ... FOR UPDATE`, `populate_existing`):
   `forget_item` and `_get_or_create` (set_level, add_to_list) take the row by id with a lock.
   A forget that waited behind another forget sees no row and answers 404; a 'bitti' that
   waited behind a forget sees no row and creates the item again (the owner said it after the
   forget). Considered: catching StaleDataError and retrying - rejected, it hides the order
   instead of serialising it. SQLite ignores the lock (unit tests unchanged).
2. **An adjective keeps its noun nominative.** `display_name` used the compound head form
   ("kedi MAMASI") for every two-word name. Now the head stays nominative when it was said
   nominative ("taze süt") or follows a known adjective (`_ADJECTIVES`: "taze sütü" -> "taze
   süt"). Noun compounds said inflected ("kedi mamasını", "bulaşık deterjanını") keep their
   compound head. A bare "kedi mama" now stays as said (was "kedi maması") - accepted.
3. **An amount said at the start of a typed name is the item's amount** (`parse.split_amount`):
   "iki şişe süt" is süt with list_quantity "iki şişe"; a given `quantity` wins; the split
   amount passes `quantity_problem` ("0 litre süt" is 422). The test team's step expected 422
   for "iki sise sut"; the card (Danışman) asked for one item süt - the card was followed.

## Open

- `routes._payload` must catch RecursionError (deep JSON -> 422 body_invalid): ALAN_ISTEGI
  services/api/app/household/routes.py. Red test committed as strict xfail.
- DELETE /list/{id} racing a forget still reads unlocked in routes.py (not reported; untested).
