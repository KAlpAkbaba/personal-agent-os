## ADR (no number yet): a standing language preference is not an answer level; the assistant's reply is not a memory

Context. Owner's trial 2026-09-30 20:11 UTC: "Bundan sonra araştırma raporlarını her zaman Türkçe oku" became RESEARCH_OPEN
(answer_level=detail), the model called research.answer_mode, the tool preferred the turn's 'detail' and the durable register
became detail; 39 s later the summary line "| Asistan: Bundan sonra araştırmaları ayrıntılı anlatacağım efendim." was filed as a preference candidate.

Decision.
1. intents._research_open_match: a read verb with a standing marker ("bundan sonra/artık/hep/her zaman") and no level word is not
   "araştırmayı oku"; it returns None. The marker test is one shared helper (`_is_standing_sentence`) also used by `_answer_mode_match`
   (no second phrase table). No language-preference intent exists, so the sentence falls to the ordinary path (Intent.NONE, the
   model/ack path); a dedicated language preference is a separate feature.
2. research_answer_mode takes the level ONLY from a turn record whose intent is research_answer_mode and whose answer_level is a
   level; otherwise VALIDATION_ERROR with a spoken sentence. The model's `level` argument is no longer consulted (it is a paraphrase).
3. memory.extraction: summary is split on '|' / newline into lines; lines prefixed Asistan:/Assistant: are counted skipped and never
   filed; Sahip:/Owner: prefixes are stripped from the owner's lines.
4. intents._executive_start_match: a standing sentence carrying a read verb is never an executive start. With RESEARCH_OPEN declining,
   the trial sentence would otherwise have fallen into EXEC_START (action class, exec_shape research_report) and could launch a
   research-report mission from a preference (inspector, second pass). It now resolves to Intent.NONE (klass query).
5. "Bundan sonra raporlari ayrintili oku" / "...teknik oku" (a level word, read verb) route the one-off narration controls
   DETAIL / TECHNICAL, not the durable register: the answer-mode phrase needs anlat/konus/cevap/soyle. Deliberate, pinned by a test;
   the owner who wants the standing register says "bundan sonra ayrintili anlat".

Consequences. A model-only call with no owner level word now gets a question instead of a silent register change. The three
sentences are corpus cases r.lang.1-3 (tests/voice_corpus/corpus.py, added with the lead's return note; one file outside the card's area list).
