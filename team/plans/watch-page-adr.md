# ADR (watch-page): 'Nöbetler' on /routines, through the REST half of the watch contract

Status: proposed (worker-4, cycle d20261004). The lead numbers it.

## Context

The watch engine (Stage 54, `app.watch`) serves `/v1/watches` (GET, POST, DELETE `/{id}`,
DELETE). Voice can make and forget watches; the web had nowhere to see them, which breaks
the rule that web stays fully usable beside voice.

## Decision

1. The section lives on `/routines`, below the routines panel: a watch is the owner's own
   standing task beside the system's routines. Not the Onay Merkezi or the Ofis (the team's
   pages).
2. `apps/web/app/lib/watch/watches.ts` is the only client; `routines/page.tsx` mounts
   `<WatchList />` and holds no request of its own, so family-pages' "no family page sends
   POST/DELETE" guard still holds for the page file (its comment names the exception).
3. Every rule stays on the server: the form sends what was typed; a `WatchRefused` comes back
   as `{detail: {code, message}}` and its `message` (`reason_tr`) stands beside the form.
   One client-side convenience: an empty name is sent as the page's domain (the server
   requires a label; a refusal for that would be noise).
4. Rows are Turkish sentences built on the client: `number_below:20000` -> "20.000'in altına
   inerse", `number_above:150` -> "150'yi geçerse" (the genitive/accusative suffix follows the
   last spoken word of the number), `changed` -> "değişince", `contains:x` -> "'x' geçince";
   numbers are read with the server's own rules (`compare._token_value`); an unknown
   condition is shown as it is, never guessed.
5. A failed list is one line ("Nöbetler okunamadı: <server sentence>") inside the section;
   the routines panel and the policy list render regardless. A request never throws.
6. Kaldır removes the row only after the Cloud Core says it is gone: deleted now, or
   `not_found` (forgotten by voice, removed in another tab - the row would otherwise stay to
   be pressed again); 'Hepsini unut' is ONE DELETE and says the server's count.
7. What the buttons do is `watchHandlers(get, set)` in `WatchList.tsx`, a plain function over
   any store; the component only gives it React state (a copy made once per mount, since the
   handlers read the latest state across their awaits). Tests drive the handlers with a
   mocked `apiFetch`, so the button -> DELETE `/{id}` chain is proven, not two halves.
8. A list that could not be read stays `null`: a refused add never turns it into "Henüz
   nöbet yok", and a successful add reads the list again instead of showing the one new row
   as if it were the whole list (if the re-read fails, the one line stays and the add is
   still said).

## Consequences / open

- The REST `WatchView` carries no reason for an unreadable reading (it is on
  `watch_readings.reason`, not on the view). The row says "okunamadı, art arda N kez (sayfa
  açılmadı ya da aranan yer bulunamadı)"; when the wire gains `last_reason`, the page already
  says that instead. Adding `last_reason` to `WatchView`/`_view` is an api card.
