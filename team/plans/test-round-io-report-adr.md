# ADR draft: the test team's input/output report (test-round-io-report)

Status: proposed (worker-4, cycle d20261006). The lead numbers it.

## Context

The owner, 2026-10-06: "test ekibinin yaptığı işlemleri ve aldığı sonuçların girdi çıktı olarak
raporlarını istiyorum incelemek için". A scenario result kept per step only a status or a short
match; the round's only human file (`kopma-noktasi.md`) lived under the run temp root and went
with the temp sweep.

## Decision

1. `run-scenario.ps1` records per step `input` (`{method, path, headers, body}` - the JSON body as
   sent; a web step `{action, url, sentence, wav, expect}`) and `output` (`{status, body}`).
   Bodies are **masked first, cut second** (`ConvertTo-TestTeamShownText`): every JSON field
   whose name holds token / secret / password, a Bearer value, an `Authorization:` line and the
   session token itself become `***`; then the text is cut at 4096 characters with a
   `…[kesildi: N karakterin ilk 4096'i]` marker. Masking first means a secret on the cut is never
   half shown.
2. `test-round.ps1` writes `test-raporu.md` in the round folder at the end of every round: the
   round, staging sha, counts, the plan's `why` per job, then per tester and per step
   `Girdi` / `Beklenen` / `Çıktı` / `Sonuç` with the ms (fenced with four backticks, none
   allowed inside), the ladder as a `yük | hata | p95` table, the forwarded cards. The body of
   the round runs in `try/finally`: a round that dies (or stops at cap 0 / no plan) writes what
   it had, headed `**yarım kaldı: <why>**`. A report that cannot be written or sent is said and
   never stops the round.
3. With `-QueueUrl` the report is POSTed to the Cloud Core's new owner-only route
   `/v1/team/test-reports` (`app/team/test_reports.py`): POST (text ≤ 256 KB of UTF-8 → else
   413 `too_large`; malformed → 422), GET list (no text; newest first), GET `/{round}` (with
   text). Same round again replaces it; the last 50 rounds are kept, the rest dropped on the
   write that passes the bound. Storage follows the queue: `team_state` rows of kind
   `test_report` (key = round, `updated_at` = a microsecond stamp kept strictly increasing) - no
   migration - or `team/test-reports.json` in file mode. The posted text is cut to 256 KB with a
   marker; the round folder keeps the whole.
4. The Ofis gets `OfficeTestReports.tsx` ("Test raporları": round, date, geçti · kaldı · koptu,
   a dead round's `yarım kaldı`), each button opening the report's text.

## After the first inspection (2026-10-07)

- The database path counts a round's own old row out of the "others" (as the file path did): 50
  kept and one of them sent again stays 50 (was 49 on Postgres).
- A cut is idempotent: a body that already ends in `…[kesildi: N karakterin ilk M'i]` keeps N
  when the report reads it (was cut again and said 4134 of a 6012-character answer). A cut steps
  one back rather than split a surrogate pair.
- The route refuses NUL and lone surrogates in `text`/`unfinished` with a 422 (were a JSONB
  `DataError` and a `UnicodeEncodeError`, both 500). The round cleans its report before it sends
  (`ConvertTo-TestTeamCleanText`: NUL shown as `␀`, a lone half as U+FFFD), so a staging body with
  binary in it still reaches the Ofis.

## Not done here (outside the card's area)

- A Postgres integration test of `DbRoundReports` (put/list/get/keep 50/NUL refused) belongs in
  `services/api/tests/integration/`; the inspector proved the 49/50 there by hand.

- `services/api/app/main.py` must `include_router(app.team.test_reports.router)`; until then
  the real application answers 404 (strict-xfail test `test_the_real_application_serves_the_reports`
  turns into a failure once wired - remove the marker then).
- `apps/web/app/core/office/page.tsx` (or `OfficeView.tsx`) must mount `<OfficeTestReports />`
  under the test seats.

## Consequences

- Result files and reports grow (≤ 4 KB per body per step). Secrets are masked by field name and
  by the session token; a secret under an innocent field name (e.g. `"code"`) is not.
- The list endpoint reads the stored documents whole (≤ 50 × 256 KB worst case).
