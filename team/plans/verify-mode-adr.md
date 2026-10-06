# verify-mode - design decisions (draft for the lead to number)

Status: implemented in run 3 (area widened by the Proje Yöneticisi). Run 2's pure core is kept;
run 3 adds the trigger, the table, the two voice tools, the announcer branch, the REST list and
the web page.

## Context
"Bunu doğrula: ...", "X doğru mu", "şunu kontrol et: ..." -> a claim, a verdict, sources, a
counter-argument, one spoken sentence; recalled later ("geçen hafta neyi doğrulamıştık",
"X hakkında ne bulmuştuk"). Built on the M13 research engine (ADR-0183/0213).

## Decisions
1. Two voice tools in the EXISTING research family (`tools_verify.py`, hub lines only in
   `tools.py`): `research.verify` starts the research run through
   `research_service.start_browser_research` and writes a `pending` `claim_verifications` row;
   `research.verify_recall` settles finished rows and answers by subject and by date.
2. Step-up tiers: `research.verify` SENSITIVE (a crawl on an owner device, as
   `research.start`); `research.verify_recall` OPEN (reads own records, as `memory.search`).
3. Corpus: `research.verify` creates a research task by design, so the harness's "a research
   task was created" allowlist names it; the harness also creates `claim_verifications`.
   Rows: five triggers (RUNNING), one recall, two near-misses ("Doğru söylüyorsun.",
   "Bunu kontrol et.") that start nothing.
4. Router: `Intent.VERIFY_CLAIM` -> CAPABILITY_BY_INTENT `research.verify`;
   `Intent.VERIFY_RECALL` -> QUERY_TOOL_BY_INTENT `research.verify_recall`. Matched right after
   the macro block (a claim may hold any family's words). The rule lives in
   `verify.verify_request_kind` so the router and the tool read one trigger:
   a leading trigger with a separator ("Bunu doğrula: ..."), a leading "doğrula"/"teyit et"
   with >= 3 claim words (never a bare "kontrol et ..."), a trailing ", doğrula", or
   "... doğru mu" after a nominalised verb ("olduğu") or >= 5 words. Recall: a past form of
   "doğrula" ("doğrulamıştık", "doğruladık"), or "hakkında/konusunda" + "bulmuştuk/bulduk".
   A leading trigger wins over a recall word inside the claim.
5. Local mode: `research_topic_of` returns the claim for a verify sentence and the sentence
   itself for a recall - no new turn-record field in service.py.
6. Window: 365 days (the REST ceiling), QUICK mode; never the 3-day research default.
7. Verdict (pure, deterministic): no decisive source -> BELİRSİZ, confidence 0 (ADR-0063);
   only supporting -> DOĞRU; only refuting -> YANLIŞ; both -> KISMEN. Confidence grows with
   agreement and source count, capped at 0.5 for one source; the sentence then says
   "Tek kaynak: ..." (lead decision, run 3). A source dated before the year the claim names is
   said aloud. A four-digit number followed by a unit ("2000 metre", "1950 kişi") is not a year.
8. Stance judge: deterministic seam in `verify.py` - the excerpt sentence carrying >= 60 % of
   the claim's content stems is the quote; it refutes when its negation differs from the
   claim's, or when both name numbers and none match ("8849" vs "8848"); otherwise supports;
   below 60 % neutral (never decides). A source flagged `injection_suspected` is dropped.
   A model-backed judge is a later card and replaces only this section.
9. No automatic checking: the trigger is only the owner's own sentence.
10. Recall: every query word (one-letter fragments dropped: "Everest'i" -> "everest") must
    prefix a Turkish-folded claim word; date words give [since, until) in the OWNER'S clock
    (the tool passes `briefing.local_now`, Istanbul): "geçen hafta" (Monday-to-Monday),
    "bu hafta", "geçen ay(ki)", "bu ay", "dün", "bugün"; whole words ("ödün" is not "dün",
    "geçen ayrıntı" is not "geçen ay"). Newest first.
11. The owner hears the verdict through `research_announcer.py`: it now claims RUNNING
    `research.verify` calls too, settles the row (`verify.settle_task`) and completes the call
    with the verdict sentence as `speech` - not the research summary. A run that FAILED with no
    decisive source says "Doğrulama araştırması tamamlanamadı; hüküm belirsiz." (the truth,
    not "no source").
12. Table `claim_verifications`, migration `0067_claim_verifications` under
    `alembic/versions/` (the card's `migrations/versions/` does not exist). Expand-only;
    `task_id` FK SET NULL; status CHECK (pending|settled); indexes on created_at and task_id.
    NOTE for the lead: another branch of this cycle also has a 0067 (`0067_wake_alarm_song`,
    the dev DB is on it) - re-chain at merge.
13. REST: `GET /v1/research/verifications?q=&since=&until=&limit=` (owner-gated, declared
    before `/{task_id}`); it settles finished rows before reading. Web: `/research/verify`
    lists verdict + confidence, sources with the deciding quote and date, the counter-argument;
    a BELİRSİZ with no source says no source decided.

## Follow-up card suggestions
- `_fold` in verify.py duplicates `intents.turkish_casefold` (kept apart so the pure module does
  not import the 10k-line router at import time).
- An ASCII-folded trigger ("bunu dogrula", "oldugu dogru mu") is read by the regex for
  "dogrula"/"dogru mu", but the nominalised-verb test needs Turkish letters ("oldugu" with
  >= 5 words still passes).
