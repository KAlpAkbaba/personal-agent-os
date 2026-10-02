# ADR (lead numbers it) — ADR-0224 corrections as built: the owner's correction is vocabulary memory, and the next match uses it

**Status.** Accepted (worker, cycle d20261001, task `understanding-corrections-memory`; second
pass after the inspector's return). The module, the relay call sites, migration 0064 and
`MemoryClass.VOCABULARY` are all on the branch. Three of those files are outside the card's
listed area and were added because the return names them: `alembic/versions/20261001_0064_…`,
`app/memory/types.py`, `tests/integration/test_understanding_vocabulary_postgres.py`,
`app/voice/realtime_sessions/service.py`.

**Decision.**

- **The class.** `MemoryClass.VOCABULARY` (`app/memory/types.py`), allowed by
  `ck_memories_class` from migration `0064_memory_vocabulary_class` (expand-only: the constraint
  gains one value; the downgrade deletes the vocabulary rows and restores the six).
  `app/memory/policy.py` names it `VOCABULARY` and gains decision-table
  row 1a: a `vocabulary` observation WITHOUT the caller's `explicit` flag is IGNORED - no row, no
  candidate, no session stage. With the flag it is the existing row 2 (durable, explicit, 1.0,
  actor OWNER). The secret guard (row 1) still runs first. A synonym is the owner's own word or it
  is nothing: an inferred one would be matched as if the owner had said it.
- **The row.** text `ofüs = ofis (cihaz)` / `hesaplayıcı = Hesap Makinesi (uygulama)`; key
  `<kind>:<folded heard>` (one row per heard word: teaching it again corroborates, teaching it
  differently supersedes - the memory service's own keyed path); value
  `{kind, heard, meant}` with `meant` a canonical alias word or an allow-listed app id; source
  `{kind: voice_correction, session_id}`. A vocabulary row whose value names no such entity (a
  model handing `memory.remember` the class) is not a synonym.
- **`app/voice/understanding/corrections.py`.**
  - `vocabulary(session)`: one query for (id, version) of the active vocabulary rows; the rows
    are read and parsed only when that changed (another process's write included). It also sets
    the turn's vocabulary (a ContextVar, the `_FOLD_MATCHING` precedent) which the rule table
    reads. A store that cannot be read is an empty vocabulary; the read runs in a savepoint so a
    failure cannot abort the relay's transaction. The layer-2 `EntityIndex` gets the rows as
    `(kind, value, surface)` through `policy.read_turn(vocabulary=...)` and is rebuilt only when
    they change (`SemanticEngine` caches by content).
  - `read_turn(...)`: `policy.read_turn` with the vocabulary in it. A device word the owner
    taught, said as taught (bare or with ONE closed case ending - accusative, locative, -ki,
    ablative; never a prefix), binds at 1.0, layer `vocabulary`, BEFORE the confusion list - the
    owner's word outranks a release artefact - unless the sentence already names a machine in a
    closed form. A NEAR form goes to layer 2 through the index and is read back (MEDIUM). The
    candidate's evidence names the synonym (`vocabulary: ofüs = ofis (cihaz)`); the audit block
    still carries names and numbers only.
  - `correctable(...)`: what the relay keeps of a MEDIUM read-back or a LOW device question -
    intent, application, device, band, time and the ONE word the device slot was read from
    (the word before "bilgisayar…"), never the sentence. A HIGH turn keeps nothing.
  - `correction_turn(kept, text)`: called only for a sentence the rule tables left unrouted
    (rule first). After a LOW question the bare answer corrects ("ofis bilgisayarında"); after
    a MEDIUM read-back a correction says no first ("hayır, ev bilgisayarında") - a bare device
    phrase there is a new sentence. "Onu değil, Not Defteri" re-issues the turn with the
    application (no word to learn: the application was read from words the allow-list knows).
    "Ona X deme, Y de" needs no turn before it: the side the system knows is the entity, the
    other is the new word; both known and equal -> `already_known`; both known and different ->
    refused; neither known -> not a correction.
  - **What is never learned** (`known_word`, `not_a_name`): a word the system already reads -
    an alias phrase, a suffixed alias ("ofisi", "evim"), an alias the owner configured on a
    device, words the allow-list reads as an application - and a pointing word ("diğer", "onun",
    any "…ki"). "Ofis bilgisayarında aç" then "hayır, ev bilgisayarında" is a change of mind:
    the turn is re-issued at home and "ofis" keeps meaning the office.
  - **The router's own words name nothing** (`known_word`; the inspector's finding: "ona ofis
    deme, hesap de" was learned as `hesap = ofis (cihaz)` and the next "hesap makinesini aç"
    opened on ofis at HIGH). Refused for either kind: any form of the computer word
    ("bilgisayar…") and a verb of the rule tables - `intents.rule_verb_words()` reads every
    `_…_VERB…` table, so a verb added to a table is refused without a second list; forms are
    matched whole, stems of three letters or more by prefix, shorter stems whole ("aç" must not
    refuse "acer"). Refused as a MACHINE only: any word of an application alias phrase or
    Turkish app name, bare or with one closed case ending ("hesap", "makinesini", "defteri").
    An application may still be taught a word of another's name ("ona hesap makinesi deme,
    hesap de"): the allow-list's own names are read first, so it cannot steal a sentence.
    A heard word longer than 32 characters is `not_a_name` (it becomes a key and a file name).
  - **A guessed word must resemble the alias** (`not_similar`, `GUESSED_WORD_MIN` 0.6). After a
    question or a read-back the heard word is taken by POSITION (the word before
    "bilgisayar…"); "hemen bilgisayarımda … aç" answered "ev" would otherwise teach "hemen = ev"
    and the next "hemen hesap makinesini aç" would launch at home at HIGH. So "ofüs" -> "ofis"
    (0.75) and "evü" -> "ev" (0.67) are learned, "hemen"/"şirket" -> "ev" and "ofisü" -> "ev"
    are not (the turn is still re-issued). The pair form states both sides - nothing is guessed -
    and may teach an unlike word ("ona şirket deme, iş de").
  - **The pair form is a sentence about a name, and ordinary speech is not** (third pass; the
    inspector's finding: in an office session "Bunu bana deme, evde de." was stored as
    `bana = ev (cihaz)` and the next "Bana hesap makinesini aç." ran on the home PC at HIGH).
    Four rules, each with its own RED mutation:
    1. **The address word is required**: `ona` / `buna` / `şuna`. "Onu/bunu … deme" is the thing
       SAID, not the thing named; "Şirket deme, iş de." without it is not a correction.
    2. **The known side is a bare name**: "ev", "iş bilgisayarı", "Not Defteri". A word in a case
       ("evde de", "ofisteki de", "Chrome'da de") says WHERE to say it and names nothing, so
       "Ona aptal deme, evde de." is not a correction.
    3. **A pronoun, an adverb or a politeness word is `not_a_name`**, for either kind (closed
       lists `_PRONOUNS`, `_ADVERBS`, `_POLITENESS` beside the pointing words): "Ona ev deme,
       hemen de." teaches nothing. The lists are a first refusal, not the guarantee.
    4. **A taught machine word binds only where the sentence names a machine**
       (`_machine_named`): followed by the computer word ("ofüs bilgisayarında") or carrying a
       place ending ("ofüste", "ofüsteki", "ofüsten"). This is how the device grammar reads its
       own aliases (ADR-0205/0212: a lone "ev" is left alone). So whatever a vocabulary row
       holds - a word no list knew, a row written by `memory.remember` - "Bana hesap makinesini
       aç" names no machine. Cost: "ofüs hesap makinesini aç" (bare) no longer binds; the
       grammar never bound a bare alias either.
  - **Rule first is the relay's guard, and it is tested there.** "Ona dur deme, ev de." (STOP)
    and "Ona devam et deme, ev de." (RESUME) are sentences the module alone would learn - its
    lists know the operator's verbs, not every word of every rule table - and the relay never
    hands them to it.
  - `learn(...)`: `memory_service.record_observation` with the explicit observation (the existing
    write policy, dedup and audit; it commits on the session it is given, as `_extract_memories`
    does), then the proposal. Never raises; a refusal is a reason (`secret_rejected`, …).
  - **The proposal.** `team/proposals/stt-karisiklik-<kind>-<heard>-<meant>-<hash8>.md`, created
    with exclusive create: once per synonym across sessions and workers. It carries the two
    words, the fuzzy similarity and the candidate entry; never the sentence. The module never
    opens `stt-confusions.json` (a test pins its sha256). Directory: `PAGENTOS_TEAM_PROPOSALS_DIR`,
    else the checkout's `team/proposals`, else none - reported (`proposal_reason:
    no_proposals_dir`), not hidden.
- **`app/voice/intents.py`.** `_app_open_match` asks `corrections.app_for(tokens)` only when the
  allow-list's own names found none. Outside a turn the vocabulary is empty: the corpus reads
  exactly as before.
- **Deletion.** `memory.forget` on the row is all it takes: the next version check drops the
  synonym. "ofüs'ü unut" already routes to MEMORY_FORGET; `named_synonym(vocabulary, text)` gives
  the row a forget sentence names by its heard word.

**Why not a wider match.** A wrong synonym is a wrong-device launch at HIGH - exactly what ADR-0224
forbids - so every doubt resolves to "ask again": closed endings, the known-word refusals, no
learning from a HIGH turn. The owner can always teach a word with the pair form.

**Consequences / open.**
- `memory.forget` still takes an id from a `memory.search` read-back (B18): "ofüs'ü unut" as ONE
  sentence deleting the row needs `tools_memory.py` to call `named_synonym` - not in this area,
  not wired. Today: "ofüs hakkında ne biliyorsun" -> read-back -> "bunu unut".
- A taught application word is only read with an open verb (`app_open`); other intents do not ask
  the vocabulary yet. Application corrections after a read-back re-issue but teach nothing until a
  reading can be made from words the allow-list does not know (layer-2 slots, `understanding-stt-corpus`).
- The pair form teaches silently: it re-issues no turn, and the relay has no receipt speech for
  "öğrendim" (a `say` frame or a tool is outside this area). The audit row says it was written.
  The refusals above close the shapes the inspector found; an ORDINARY unknown word is
  still learned from one sentence ("ona ofis deme, müzik de"), which is what the pair form is
  for - a spoken receipt ("müzik artık ofis demek") is the missing guard and its own task.
  Since the third pass such a row binds only in "müzik bilgisayarında" / "müzikte", never in
  "müzik aç". An APPLICATION word has no such positional rule (the rule table asks `app_for`
  for any open sentence the allow-list could not read): there the lists and the router-word
  refusals are the whole guard.
- Layer 2's entity index still receives every device synonym as a surface form (a NEAR form is
  read back at MEDIUM, never run at HIGH); the positional rule is on the HIGH path only.
- The near-form MEDIUM number (0.67 for "ofüss") is the lexical `DeterministicEmbedder`'s; with
  `LocalEmbedder` it is NOT_RUN.
- On the Cloud Core image there is no checkout: without `PAGENTOS_TEAM_PROPOSALS_DIR` the memory
  row is written and the proposal is not (`no_proposals_dir`). `proposals-on-cloud-core` decides
  where the directory is.

## For the lead at merge

1. **Number the migration against the chain tip at merge.** `0064_memory_vocabulary_class`
   chains from `0063_team_state`. If another branch of this cycle also adds a 0064, one of the
   two is renumbered (file name, `revision`, `down_revision`, and the two `"0063_team_state"`
   literals in `tests/integration/test_understanding_vocabulary_postgres.py`).
2. **The release is a schema release.** `alembic upgrade head` runs before the new colour
   serves; the old colour keeps working beside 0064 (it never writes the class). Do NOT run the
   integration suite of this branch against the shared dev database while sibling worktrees are
   still at 0063: their `alembic upgrade head` cannot locate revision 0064. The proof here ran
   in a scratch database (`PAGENTOS_DATABASE_URL=…/pagentos_scratch_vocab0064`), dropped after.
3. **`MEMORY_CLASSES` now has seven values**, so `memory.remember` / `memory.search`
   (`tools_memory.py` schema enums) and `routes.py` offer `vocabulary`. A row written that way
   without a `{kind, heard, meant}` value naming a real alias or app is not a synonym (tested);
   one with such a value IS - through `remember_explicit`, i.e. the owner's own instruction.
4. `docs/HANDOFF.md`, the ADR number, `docs/DECISIONS.md` (ADR-0224 addendum 4).
5. Nothing for `app/protocol_files.py` or the falsification list: no protocol file is added or
   read. `PAGENTOS_TEAM_PROPOSALS_DIR` is read from the environment by this module only; if it
   should be a `Settings` field, that is `app/config.py`.

## The relay as wired (`app/voice/realtime_sessions/service.py`, `record_client_events`)

Five places, all in the utterance branch: (1) `corrections.vocabulary(db)` before the router
reads the sentence; (2) `correction_turn(...)` for a sentence the tables left unrouted, and the
kept `understanding_correctable` popped (corrected once, or not); (3) a correction that carries
a turn re-issues it (`matched="understanding:correction"`, the device the owner named as the
answered device); (4) `corrections.read_turn` in place of `policy.read_turn`; (5) after the
decision: `correctable(...)` kept on `context_json`, and a learnable pair written through
`learn(db, memory_runtime.embedder, …)` with `understanding_correction {kind, written, reason,
proposed}` on the audit row - no words. `learn` commits the relay's session early (the
`_extract_memories` precedent, and for the same reason). Without a memory runtime the audit
row says `no_runtime` and nothing is written.

Still open in the relay: the turn's vocabulary stays in the ContextVar after the utterance
(a later `resolve_intent` in the same context still reads a taught application word), and the
pair form has no spoken receipt.
