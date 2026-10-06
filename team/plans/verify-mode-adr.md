# verify-mode - design decisions (draft; runs 1 and 2 stopped on ALAN_ISTEGI)

Status: proposed. Run 2 implemented the pure verdict core `app/research/verify.py` (decisions
7 and 10, 18 unit tests); the voice tools, the table and the announcer wait for the area.

## Run 2 additions
10. Recall (pure): every query word must prefix a Turkish-folded claim word (İ->i, I->ı);
    date words give a [since, until) window - "geçen hafta" is the previous Monday-to-Monday
    week, "bu hafta", "geçen ay", "bu ay", "dün", "bugün"; words matched whole so "dünya" is
    not "dün". Newest first.
11. The owner hears the verdict through the announcer: `research_announcer.py` scans only
    `research.start` and speaks the research summary; it must also pick up `research.verify`
    and speak `verify.spoken_sentence` when the run settles (inspector, run 1). Out of area.
12. The table's migration goes under `services/api/alembic/versions/` (the card named a
    `migrations/versions/` folder that does not exist). `models.py` gets the table only
    together with its migration - a model without one would break the schema-drift guard.
13. Spoken sentence: "Hüküm: <doğru|yanlış|kısmen doğru> (güven yüzde N). Kaynak: <title>,
    <1 Eylül 2026>." + "Kaynaklardan biri iddianın konusundan eski." when a kept source
    predates the year the claim names; no decisive source -> "Bunu doğrulayacak bir kaynak
    bulamadım; hüküm belirsiz."

## Context
"Bunu doğrula: ...", "X doğru mu", "şunu kontrol et: ..." -> a claim, a verdict, sources, a
counter-argument, one spoken sentence; recalled later ("geçen hafta neyi doğrulamıştık",
"X hakkında ne bulmuştuk"). Built on the M13 research engine (ADR-0183/0213).

## Decisions
1. Two voice tools in the EXISTING research family: `research.verify` (starts the research
   run, writes a pending `claim_verifications` row) and `research.verify_recall` (settles
   pending rows from finished runs, answers by text and date). The research family keeps
   `app/voice/capabilities.py` untouched (a new family needs a Turkish family name there).
2. Step-up tiers: `research.verify` SENSITIVE (a crawl on an owner device, as
   `research.start`); `research.verify_recall` OPEN (reads own records, as `memory.search`).
   Needs `app/security/step_up.py` (outside the card's area).
3. Corpus: `research.verify` creates a research task by design, so the harness's
   "a research task was created" allowlist (`research.start`, `news.summarize`) must name it.
   Needs `tests/voice_corpus/harness.py` (outside the card's area).
4. Router: `Intent.VERIFY_CLAIM` -> CAPABILITY_BY_INTENT (it starts work, like
   NEWS_SUMMARIZE); `Intent.VERIFY_RECALL` -> QUERY_TOOL_BY_INTENT. Matched early (after the
   macro block): the claim may contain any family's noun. Recall before claim
   ("doğrulamıştık" starts with "doğrula"). "X doğru mu" counts only with a nominalised claim
   ("... olduğu doğru mu") or >= 5 claim words, so "Bu dosya doğru mu?" stays
   artifact.validate. Near-misses: "doğru söylüyorsun", "Uygulamayı doğrula".
5. Local mode (no model writes arguments): `research_topic_of` returns the claim for a verify
   sentence (the claim IS the topic of the research the verify starts) and the recall
   sentence for a recall one - no new turn-record field in service.py.
6. Window: 365 days (the REST ceiling), never the 3-day research default - a fact check is
   about whatever date the claim names.
7. Verdict (pure, deterministic): no decisive source -> BELİRSİZ, confidence 0 (ADR-0063);
   only supporting -> DOĞRU; only refuting -> YANLIŞ; both or mixed -> KISMEN. Confidence
   grows with agreement and source count, capped at 0.5 for one source. A source dated
   before the year the claim names is said aloud. Codes stored ASCII
   (dogru/yanlis/kismen/belirsiz), labels Turkish.
8. Stance judge: deterministic seam (keyword overlap + negation markers) behind a Protocol;
   a model-backed judge is a later card.
9. No automatic checking: the trigger is only the owner's own sentence.
