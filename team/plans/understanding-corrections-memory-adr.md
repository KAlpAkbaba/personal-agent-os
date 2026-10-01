# ADR (lead numbers it) — ADR-0224 corrections as built: the owner's correction is vocabulary memory, and the next match uses it

**Status.** Accepted (worker, cycle d20261001, task `understanding-corrections-memory`). Built and
tested inside the area; **the relay call sites and the migration are outside it and are the lead's
at merge** (below). Until they land, nothing in production calls this module and no `vocabulary`
row can be written on PostgreSQL.

**Decision.**

- **The class.** `app/memory/policy.py` gains `VOCABULARY` (value `vocabulary`) and decision-table
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
  - **A guessed word must resemble the alias** (`not_similar`, `GUESSED_WORD_MIN` 0.6). After a
    question or a read-back the heard word is taken by POSITION (the word before
    "bilgisayar…"); "hemen bilgisayarımda … aç" answered "ev" would otherwise teach "hemen = ev"
    and the next "hemen hesap makinesini aç" would launch at home at HIGH. So "ofüs" -> "ofis"
    (0.75) and "evü" -> "ev" (0.67) are learned, "hemen"/"şirket" -> "ev" and "ofisü" -> "ev"
    are not (the turn is still re-issued). The pair form states both sides - nothing is guessed -
    and may teach an unlike word ("ona şirket deme, iş de").
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
- The near-form MEDIUM number (0.67 for "ofüss") is the lexical `DeterministicEmbedder`'s; with
  `LocalEmbedder` it is NOT_RUN.
- On the Cloud Core image there is no checkout: without `PAGENTOS_TEAM_PROPOSALS_DIR` the memory
  row is written and the proposal is not (`no_proposals_dir`). `proposals-on-cloud-core` decides
  where the directory is.

## For the lead at merge

1. **Migration (required before any Postgres run; `ck_memories_class` rejects the row today).**
   `alembic/versions/2026MMDD_0064_memory_vocabulary_class.py`, `down_revision = "0063_team_state"`,
   expand-only (the old colour never writes the class):
   ```python
   _OLD = ("preference", "episodic", "project", "semantic", "procedural", "voice_preference")
   _NEW = (*_OLD, "vocabulary")
   def _in(values): return "memory_class IN (" + ", ".join(f"'{v}'" for v in values) + ")"
   def upgrade():
       op.drop_constraint("ck_memories_class", "memories", type_="check")
       op.create_check_constraint("ck_memories_class", "memories", _in(_NEW))
   def downgrade():
       op.execute("DELETE FROM memories WHERE memory_class = 'vocabulary'")
       op.drop_constraint("ck_memories_class", "memories", type_="check")
       op.create_check_constraint("ck_memories_class", "memories", _in(_OLD))
   ```
   The unit suite runs on SQLite, which has no such constraint: **the unit tests cannot see this.**
   An integration run on the dev stack's Postgres (write one vocabulary row through `learn`) is
   the proof; NOT_RUN here.
2. **`app/memory/types.py`** (frozen foundation, outside the area): add
   `VOCABULARY = "vocabulary"` to `MemoryClass`. `policy.VOCABULARY` then IS that member
   (`getattr(MemoryClass, "VOCABULARY", …)`), and `service.supersede_memory`
   (`MemoryClass(old.memory_class)`, line 648) stops raising ValueError for a vocabulary row.
   Check the three places that enumerate `MEMORY_CLASSES` (`tools_memory.py` schema enums,
   `routes.py`) - the class becomes offerable to `memory.remember`; a row written that way without
   a `{kind, heard, meant}` value is not a synonym (tested).
3. **The relay** (`app/voice/realtime_sessions/service.py`, `record_client_events`) - the patch
   below was applied to this tree, proved, and reverted byte-for-byte (sha256
   `be5fbe2b…fba8ad` before and after). With it applied: the two relay tests in
   `test_understanding_corrections.py` run (they are skipped until the relay's source names
   `understanding_corrections`) and pass, 394 tests of the relay / understanding / memory
   suites pass, and the Owner Utterance Suite is 2756 passed / 0 failed (917 s). Without the
   patch (this branch as committed) the two relay tests are SKIPPED, not green.
4. `docs/HANDOFF.md`, the ADR number, `docs/DECISIONS.md` (ADR-0224 addendum 4).
5. Nothing for `app/protocol_files.py` or the falsification list: no protocol file is added or
   read. `PAGENTOS_TEAM_PROPOSALS_DIR` is read from the environment by this module only; if it
   should be a `Settings` field, that is `app/config.py` (outside the area).

### The relay patch (5 hunks)

```diff
@@ imports
 from app.voice.spoken_device import resolve_without_device_phrase
+from app.voice.understanding import corrections as understanding_corrections
 from app.voice.understanding import policy as understanding_policy
@@ record_client_events, the utterance branch, before the router reads the sentence
             # words the subject helpers below read.
+            # ADR-0224 corrections: the owner's synonyms as the memory rows hold them NOW (a
+            # version check; parsed again only when a row changed), for the rule table and
+            # the layers of this turn.
+            understanding_corrections.vocabulary(db)
             intent: ResolvedIntent
             intent, subject_text, spoken_devices = resolve_without_device_phrase(
@@ before `answered = (`
             answered_device: str | None = None
+            # ... and whether it CORRECTS the turn before (a MEDIUM read-back or that
+            # question): rule first - only a sentence the tables left unrouted is looked at.
+            correction = (
+                understanding_corrections.correction_turn(
+                    ctx.get(understanding_corrections.CORRECTABLE_KEY),
+                    text or "",
+                    now=now,
+                    aliases=enrolled_aliases(db),
+                )
+                if intent.intent is Intent.NONE
+                else None
+            )
+            ctx.pop(understanding_corrections.CORRECTABLE_KEY, None)  # corrected once, or not
             answered = (
@@ after the `if answered is not None:` block; and the read_turn call
                     matched="understanding:answer",
                 )
+            elif correction is not None and correction.intent:
+                # "Hayır, ev bilgisayarında" / "onu değil, Not Defteri": the corrected turn
+                # is re-issued with the slot the owner named, on the machine they named.
+                answered_device = correction.device
+                intent = replace(
+                    intent,
+                    intent=Intent(correction.intent),
+                    application=correction.application,
+                    klass="",
+                    capability=None,
+                    matched="understanding:correction",
+                )
             machine_named = names_unbound_machine(text or "", spoken_devices)
-            decision = understanding_policy.read_turn(
+            decision = understanding_corrections.read_turn(
                 text or "",
@@ after the `if decision.missing == understanding_policy.SLOT_DEVICE:` block
                     "at": now.isoformat().replace("+00:00", "Z"),
                 }
+            # ADR-0224 corrections: what the owner may correct in the next sentence (the
+            # slots and the ONE word the device slot was read from, never the sentence) ...
+            correctable = understanding_corrections.correctable(
+                text or "",
+                intent=intent.intent.value,
+                application=intent.application,
+                decision=decision,
+                now=now,
+            )
+            if correctable is not None:
+                ctx[understanding_corrections.CORRECTABLE_KEY] = correctable
+            # ... and the pair this sentence taught, through the memory write policy (explicit,
+            # class vocabulary) - counts and names on the audit row, never the words.
+            if correction is not None and correction.learnable:
+                if memory_runtime is None:
+                    meta["understanding_correction"] = {"written": False, "reason": "no_runtime"}
+                else:
+                    learned = understanding_corrections.learn(
+                        db, memory_runtime.embedder, correction, session_id=row.id
+                    )
+                    meta["understanding_correction"] = {
+                        "kind": correction.kind,
+                        "written": learned.written,
+                        "reason": learned.reason,
+                        "proposed": learned.proposal_written,
+                    }
             reference = resolve_deictic_reference(db, intent.tokens, now=now)
```

Notes on the patch: `learn` commits the relay's session early (the `_extract_memories` precedent,
and for the same reason); `understanding_correctable` is one more key on `context_json`, replaced
or removed by every utterance; the audit row gains `understanding_correction {kind, written,
reason, proposed}` - no words.
