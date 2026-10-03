# ADR (unnumbered) — The Onay Merkezi's "Dene" list: the page half of the owner's trials (2026-10-03)

Status: accepted (worker, cycle d20261003; the lead numbers it at merge)
Proposal: team/proposals/2026-10-01-deneme-listesi.md (owner approved 2026-10-01). Server half: ADR-0258.

**Decision.**
- `apps/web/app/core/approvals/TrialsList.tsx` renders `GET /v1/team/approvals` `trials` (ADR-0258's
  shape, verbatim) as "Dene (n)" under the gates' list: the sentence in quotes, the machine, the
  expectation, the task's title small beneath, and two real buttons Oldu / Olmadı. An empty or
  absent `trials` (a Cloud Core older than ADR-0258) renders "Denenecek bir şey yok.".
- Olmadı opens "Ne oldu?" (textarea, `required`, `maxLength` 500) and a "Gönder"; empty words are
  refused on the page without a post ("Ne olduğunu yazın; boş gönderilemez."), and so are more
  than 500 characters. The server keeps its own rule (422 `said_required` / `said_too_long`).
- `POST /v1/team/trials/decision {task_id, trial_id, verdict, said}`; `said` is null for an Oldu
  without words. After Oldu the row says "Kaydedildi — kanıt satırını lead yazar"; after Olmadı
  "Düzeltme işi kuyruğa girdi: <fix_task_id>". Any refusal (409 `already_decided`,
  `cycle_running`, `stale_write`, 404, a network error) restores the row with the server's own
  sentence in `role="alert"`; it never becomes a success. The Olmadı box keeps the typed words.
- The machine: a single word gets the Turkish locative by its last vowel and last letter
  ("MAIL'de", "OFIS'te", "LAPTOP'ta"), lowered as English (a host name: no dotless ı); a phrase
  (a space or an apostrophe in it) is shown as written, so the lead can write
  "MAIL'den söyleyin, ofiste açılacak" into `machine`.
- The trial buttons lock with the gates' buttons (`decisionsOpen`), since the server applies the
  same lock rule.
- The Onay Merkezi reports `Sizi bekleyen: n` = approvals + open trials (`waitingCount`). The page
  had no counter of its own before; this line is it.

**For the lead at merge (outside this area).**
- The Ofis owner seat counts `view.approvals.length` of `GET /v1/team/office`:
  `apps/web/app/core/office/officeModel.ts:188` and `:204` (`drawSeat(agent, view.approvals.length)`),
  fed by `services/api/app/team/office.py`. To count open trials there, the office view needs the
  trials (or a count) from `office.py`, and `officeModel.ts` must add them - e.g. via
  `waitingCount` from `approvalsApi.ts`.
- The voice path ("… denemesi oldu") is not in this task.

**Open risks.** Not opened in a browser (no DOM in the tests: the click handlers are covered through
`submitTrial`, the state they set is rendered through `initial`). PROVEN_REAL waits for the owner's
"Dene (3)" and an Oldu after saying one of the sentences.
