---
name: m20-cloud-web-document-intelligence-review
description: Findings from the M20 File and Document Intelligence CLOUD CORE and WEB review (git diff cb35298..d2fccdf -- services/api/app/documents, voice tools/intents, apps/web uistate/documents+CockpitPanels, merged main d2fccdf, 2026-09-08); no Critical/High; Medium - zero Cloud-Core-side validation of folder/pattern device arguments (device-only confinement, defense-in-depth gap); Low - raw model-supplied folder string can be persisted as a focus label; Low - inconsistent excerpt bounding (refs capped 500 chars, answer() speech unbounded up to the 64KB extract budget).
metadata:
  type: project
---

Reviewed the Cloud Core and web half of M20 (the device half was reviewed separately, see
[[m20-file-document-intelligence-review]]; ADR-0083 plus addenda 1-3 in docs/DECISIONS.md
are the binding spec). Files: app/documents/models.py, app/documents/index.py,
app/documents/retrieval.py, app/documents/answers.py, app/documents/service.py,
alembic 20260908_0026_document_index.py, voice/realtime_sessions/tools_documents.py,
voice/intents.py additions, operator/models.py FOCUS_KIND_FILE/DOCUMENT/FOLDER,
ledger/vocabulary.py, main.py wiring; apps/web lib/uistate/contract.ts, documents.ts,
labels.ts, core/panels/CockpitPanels.tsx (DocumentsPanel), StateReadout.tsx.

No Critical/High finding. The architecture holds up well: the deterministic Turkish
intent router (app/voice/intents.py's M20 additions) resolves document_ref, question,
pattern and folder from the OWNER'S OWN TRANSCRIBED WORDS and stores them on
ctx.context["last_utterance"]; tools_documents.py's _target()/file_search() prefer these
over the model's own tool-call arguments (the same "owner's words win" rule M19
established) -- so the actual document targeted by "bunu ozetle" / the actual question
asked is never something injected document content or the model's own reasoning can steer,
for the CURRENT call. The retriever (app.documents.retrieval) is fully deterministic
(token-overlap plus literal ref matching, no model in the loop) and answers.py never lets
the cognitive backend choose a ref, only rewrite prose around one (ADR-0083 decision 2) --
a hostile document can get its own text spoken back attributed to itself, but cannot make
the system name a different, un-consented file or fabricate a ref.

Medium -- Cloud Core adds no independent validation of folder/pattern before forwarding
to the device; confinement is single-layer (device-only). DocumentService.search()
(app/documents/service.py around lines 210-227) builds payload["roots"] = [folder] from
the tool's raw string with no isabs/traversal/root-alias check; _resolve_document and
_resolve_target_file's "named target" branches do the same with pattern. This works today
because the M20 device half's resolve-then-contain confinement was independently verified
sound (real-junction tests, addendum 3's closed OOXML/PDF/text bounds) -- but it means
Cloud Core is not a second line of defense if a future device build, a different enrolled
machine, or a firmware downgrade regresses that confinement. _translate_error's
permission_denied speech is generic and never echoes the rejected path (verified sound,
matches addendum 1's "never echoed" rule), so an escape attempt cannot be used as an
existence oracle from the Cloud Core side either way. Fix: a light independent guard in
DocumentService (reject an obviously-absolute path or a ".." segment before even forming
the payload; or at minimum restrict folder to the known alias set _FOLDER_ALIASES resolves
to when it did not come from the router). Flagged as a background task (spawn_task,
task_d9c019b6, not yet applied).

Low -- a model-supplied (not router-supplied) folder string is persisted verbatim as a
FOCUS_KIND_FOLDER label. search()'s multi-hit-with-differing-names branch (service.py
around lines 270-279) calls focus_module.set_focus(db, FOCUS_KIND_FOLDER, folder,
label=folder, ...) with whatever string reached it -- the router's alias-resolved value
("Desktop") in the normal voice path, but the model's own free-form tool argument when the
turn record carried no folder (tools_documents.file_search's fallback to
arguments.get("folder")). Since this only reaches a matching result.ok branch (the device
already accepted the roots), actual path disclosure is not possible, but an adversarial
model turn (steered by injected document content from an earlier answer) could plant a
misleading folder label that a later "bu klasordeki..." utterance would resolve against.
Low because the search itself must still succeed inside authorized roots for this to
matter at all, and the object it stores is a display label, not a live path.

Low -- inconsistent excerpt bounding: refs[].excerpt is capped at 500 chars (_ref_dict,
answers.py around lines 65-71) but the spoken speech field in answer() (answers.py around
lines 288-308) is not separately bounded -- it carries a block's raw text up to whatever
the extraction budget (64 KB total per spec section 2) left it. Not a new authority
boundary crossed (the ceiling is the same one the device/index already enforce, and it is
the owner's own document being read back to the owner), just a design inconsistency worth
tightening if a very large single block (e.g. one dense PDF page near the budget) is ever
hit -- a many-KB TTS utterance is a poor experience, not a leak.

Verified sound:
- No new HTTP endpoint: no app/documents/routes.py, main.py never registers a documents
  router; test_identity_enforcement.py's route-table sweep (independent of this review)
  would catch one if added. Device selection is exclusively by capability
  (document.extract/file.search/file.inspect/file.compare) through the SAME
  BrokerDeviceAction/device_action every other family (M19 operator, alarms) shares --
  test_documents_wiring.py proves identity through the real create_app(), not a mock.
- No delete/move/write tool exists (grepped app/documents/ and tools_documents.py); the
  corpus's doc.neg.delete case ("Bu dosyayi sil.") resolves to Intent.NONE and the
  harness's own cross-check (h.device.capabilities_called() vs case.side_effects, a REAL
  assertion against dispatched capabilities, not a trusted label) proves zero device calls.
- DocumentIndex.upsert is reached only after result.ok in _extract -- a permission_denied,
  unsupported_format or not_found device response is never indexed; the secret-bearing-
  focus corpus case (doc.neg.secret, "Sifre dosyami oku.") proves the tool is called,
  genuinely refused, and no content reaches speech beyond the generic Turkish refusal
  sentence.
- Ledger/receipt persistence never carries excerpts: record_receipt
  (app/actions/receipt.py around lines 437-467) writes
  detail_json=receipt.as_dict(include_speech=False) and ActionReceipt.evidence_refs is
  always [] from DocumentService._receipt -- the refs/structure/changed_refs extras
  (containing the 500-char-bounded excerpts) are added to the in-memory response dict
  AFTER the ledger write, never stored durably. DocumentService._ledger() detail dicts
  carry only ids/counts/booleans, never text.
- document_index.blocks bounded to 64 KB JSON-encoded (MAX_BLOCKS_JSON_BYTES, models.py),
  trimmed from the end with blocks_truncated set -- never a silent cut; unit tests assert
  both the byte cap and that the device's own truncated flag survives under the cap.
- Focus by identity only: FOCUS_KIND_FILE/FOCUS_KIND_DOCUMENT are always keyed by
  file_id/doc_id (content/location hashes), never by title or path string; the ambiguity
  rule (_is_ambiguous_title, exact title string equality) cannot be used to steer identity
  -- the worst a same-title attacker file achieves is triggering the by-path-naming
  fallback more often, which is a safety behavior, not a bypass. test_documents_focus.py
  proves independent stacks and the two-distinct-ids-one-title case directly.
- Migration 20260908_0026_document_index.py is expand-only (create_table/create_index
  only, reversible downgrade drops what it created); UI contract v5
  (app/uistate/contract.py, apps/web contract.ts) is purely additive -- one new state
  token, one new subsystem, refs gated behind .length so a v4 event still parses byte-for-
  byte; KNOWN_CONTRACT_VERSION/CONTRACT_VERSION both bumped to 5 together.
- Publisher-side bus hygiene: app/uistate/publisher.py's _clean_metadata drops every
  list/dict outright and its _FORBIDDEN_KEY_PARTS includes "excerpt"/"content"/"text" --
  so even if DocumentService._publish ever tried to put block text or a refs list on the
  bus, it would be silently dropped before fan-out (confirmed _publish currently only
  sends {file, part}, matching ADR-0083 addendum 2 point 4's own admission that refs is
  not actually wired through yet). Web-side parseDocumentRefs/refString independently cap
  every ref field to MAX_LABEL_CHARS (64) and the list to MAX_DOCUMENT_REFS (8).
- Web rendering is 100% JSX text interpolation -- grepped apps/web/app for
  dangerouslySetInnerHTML: zero matches. DocumentsPanel/StateReadout never fetch (read
  only from the already-delivered CoreTruth/event tail); documentPartPhrase/
  documentRefLine/documentFactsLine all string-join published tokens, no HTML.
- Prompt-injection containment for the CURRENT call: intent-router-supplied document_ref,
  question, pattern and folder (from the owner's real transcript) override the model's own
  tool arguments in tools_documents.py; every tool's description explicitly instructs the
  model "Donen 'speech' metnini aynen oku" (read the returned speech verbatim) and
  file_search's says "sen bir yol UYDURMA" (don't invent a path); a free-form target on
  document.read/summarize/inspect that isn't "current"/"previous" is only ever used as a
  file.search PATTERN (never as a raw path), so it cannot smuggle a path even when model-
  supplied. Residual, not new to M20: block text that becomes speech does re-enter the
  model's own conversation context on a later turn (same architecture M13 research already
  carries for web content) -- no test in this diff specifically proves a document
  containing injection-style text cannot influence a SUBSEQUENT turn's free-form tool
  argument; noted as inherited residual risk, not a regression.

Residual risk: the single-layer confinement (Medium above) and the model-context residual
(previous paragraph) are the two open items; both are architectural trade-offs already
accepted for M13/M19 rather than new holes, but M20 is a good place to add the Cloud-Core-
side guard since the blast radius (owner's files) is larger than M13's (public web text).
See [[m20-file-document-intelligence-review]] for the device-side High (OOXML
decompression bomb, since closed per ADR-0083 addendum 3) and Lows.
