## The narrative's voice path: an intent, a model narrator behind the same Protocol, a device stamp (ADR-0216 step 2)

**Decision.** `app/narrative/intent.py` recognises "what happened" as `NarrativeAsk(period, device, failures_only)`:
a question shape ("ne oldu / ne yaptın / ne başarısız oldu") AND at least a period, a device or a failure word.
A window the collector cannot resolve (geçen hafta, hafta sonu, bu ay) is no ask - never answered with today's rows.
No period -> `bugün` (`bu hafta` for failures). A leading "işte" is 'well then', never the work machine.
`model_narrator.py` asks the existing `assistant_chat.ChatProvider` (Haiku) - no new client; the facts go
in as JSON inside an UNTRUSTED block the prompt says to quote, never obey (a summary cannot forge the end marker).
`ModelNarrator.narrate()` returns a `Narration` (text, source model|rule, verdict, draft_verdict, repaired,
fallback_reason): audit -> repair -> audit; still rejected, empty, not-ok or a raising provider -> the rule text,
none of the model's words. An empty period never calls the model. `device_writer.stamp_device()` puts the canonical
device word into `detail_json["device"]` without mutating or overwriting.

**Why.** ADR-0216: a spoken summary must not hide a failure, whichever narrator writes it. A model that is
trusted only after the auditor passes it costs a rule text at worst.

**Open.** The ChatProvider's own system prompt says "you cannot see the system's records"; the prompt overrides
that for this turn. A `system=` override on `assistant_chat` would be cleaner (outside this area).
`failures_only` is carried, not yet applied: the narrative still lists every fact.
