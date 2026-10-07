# ADR draft - office-plain-summaries: the Ofis speaks in plain Turkish

Status: proposed (worker, cycle d20261007). Number: the lead's.

## Context

The owner, 2026-10-07 11:50, on a screenshot of the Ofis: seat labels read
`health (tj-t-w10071102-5)`, breaking points `kopma: yük 32, 0 hata / 32, p95 13152 ms`;
"tüm işlemleri yönetici özeti olarak görmek istiyorum ... sesli konuşmayı test ediyorum gibi
bir özet yeterli".

## Decision

1. One place, pure functions: `apps/web/app/core/office/officePlain.ts`.
   - A test job's label: the job's own summary (`<family> (<id>): <summary_tr>` on the board
     note) when present, else `FAMILY_PLAIN_TR[family]` (scenario ids and families both named),
     else `Yeni bir özelliği test ediyor`. Never the job id or the family slug.
   - A breaking point is one sentence built from its numbers (`yük`, `E hata / N`, `p95 X ms`):
     `Aynı anda 32 istekte cevap 13 saniyeye çıkıyor; hata yok, yavaşlıyor` or
     `...; 64 istekten 5'i hata veriyor` (Turkish accusative after a number from the word it is
     read with). The test lead's long `Danışman'a, ...` report goes through the same function;
     `kırılma bulunmadı` becomes `Bu turda sistem zorlanmadı, kırılma bulunmadı`; text without
     numbers keeps its first sentence.
   - A software seat's label: the card's `summary_tr` when the seat carries one, else the
     Turkish title cut at the first `:` or `(`.
2. The technical text stays reachable: a software seat's full title in the panel a click opens
   (unchanged); a test seat gets a `<details>` "ayrıntı" with the raw job (`iş: nobet (tj-...)`)
   and the raw breaking text. The aria-label uses the plain words.
3. `office-plain.test.tsx` reads `scripts/testteam/scenarios/*.json`: a new scenario whose
   `id` or `family` has no sentence in `FAMILY_PLAIN_TR` fails the web suite.

## Consequences / open

- No software card carries `summary_tr` yet, and `officeModel.ts`/`officeApi.ts` do not pass
  one (outside this card's area). `OfficeScene` accepts `summaryTr` on a seat; until a card
  and the model carry it, the cut title is shown. A follow-up card: `summary_tr` on the team
  card schema -> office view API -> `DrawnSeat`.
- `TestTeam.ps1` does not write a job summary into the `iş:` note yet (outside this area);
  the family map covers every current scenario, and a planned job's summary can be appended
  as `: <summary_tr>` later without a web change.
- The test seat's detail is styled inline (office.css was not in the area).
