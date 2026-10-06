# verify-mode - design decisions (draft, run 1 stopped on ALAN_ISTEGI)

Status: proposed. Nothing implemented yet; this records the design so the next run starts at
the code.

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
