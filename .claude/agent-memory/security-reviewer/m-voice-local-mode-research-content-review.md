---
name: m-voice-local-mode-research-content-review
description: pointer to the 2026-09-19 review of ADR-0173 (free local voice mode) and ADR-0174 (research reads page content) plus the operator site_search feature; no Critical/High, one design-tradeoff Medium
metadata:
  type: project
---

Reviewed commits d508b40..HEAD (961952f site_search, 8596048 CSS-only, 7fa04ea ADR-0173
local voice mode, fc78dc7 ADR-0174 research content) on 2026-09-19. Verdict: RELEASE OK.

Key structural finding (reassuring, worth remembering for future voice reviews): ALL the
safety gates that matter (OWNER_ONLY_ACTIONS in tools_mission.py, mail.send's
`owner_intent_ok` in tools_mail.py, step_up_policy.evaluate, require_live/require_leg,
the forbidden-argument-key filter) key off `ctx.context["last_utterance"]`, which is
rebuilt server-side by `resolve_intent()` from the CURRENT turn's transcript text
regardless of which transport/provider produced that text. This means the local-router
transport (`transport="text"`, services/api/app/voice/providers_local_router.py) cannot
bypass any of them merely by being a different provider — a local-mode client calling a
tool with `arguments: {}` gets exactly the same server-side authorization as the paid
WebRTC path, because the handlers never trust the caller's arguments for the
security-relevant fields, only the router's own re-derived turn record. Confirmed by
reading tools_mission.py L215-236 and tools_mail.py L185-215.

The one real (Medium, accepted-tradeoff-shaped) finding: `apps/web/app/core/VoiceControl.tsx`
only stops the LOCAL session when the "Yerel mod" switch is turned off; turning it ON
never disconnects an already-live paid WebRTC session (`useVoiceSession`'s controller).
`voice-control-local-render.test.tsx` only proves the UI hides the paid controls, not that
the underlying session is torn down. An owner who flips the switch mid-conversation could
end up with two independent live voice-command channels able to both fire real tool calls
from the same or overlapping speech, causing possible duplicate execution (e.g. two
mail.send confirmations, a mission approved twice) since each channel gets its own
call_id/session_id and neither is aware of the other.

Second-order observation, not a live bug: local mode removes the "a model decides whether
to actually call the tool" layer entirely — resolved ACTING_INTENTs fire a tool-call
automatically and unconditionally (apps/web/app/lib/voice/localMode.ts `turnBody`), whereas
in the paid path the model (which sees the resolved intent only as a hint) is the one that
decides to call a tool. This raises the practical impact of an ASR misrecognition (ambient
noise/TV/another speaker) triggering a real action in local mode, more than in the paid
path. This is very likely an intentional tradeoff (matches [[owner-decisions-2026-09-18-voice-authority]]
"no second spoken confirmation, first word applies") but worth naming explicitly in any
future local-mode review since it changes the risk shape of every ACTING_INTENT capability
table entry (CAPABILITY_BY_INTENT in app/voice/intents.py), not just voice-specific tools.

Also confirmed sound: `EphemeralCredential.to_client_dict()` omits `secret` entirely (not
empty string) when a provider mints none; `webrtc.ts` explicitly throws rather than send
"Bearer undefined"/"Bearer " (services/api/app/voice/providers.py L401-413,
apps/web/app/lib/voice/webrtc.ts L187-194). Default provider selection is untouched:
`RealtimeVoiceRuntime.select()` only routes to `_select_text()` when
`transport == TRANSPORT_TEXT` explicitly, and `CreateSessionRequest.transport` is
pydantic-validated against the `TRANSPORTS` enum so no arbitrary transport string reaches
selection logic.

ADR-0174 research content path (fc78dc7): the new `synthesize_thin_content` LLM path
correctly threads through the SAME CRITICAL-1a memory boundary as the full-report path —
`used_provider_name` (not a hardcoded "deterministic") is propagated all the way to
`report_json["synthesis_provider"]`, which is what `_finding_memory_entry()` in
browser_activities.py gates on before letting a finding's `summary` field (LLM-authored,
therefore untrusted) into episodic memory. Confirmed by tracing
services/api/app/research/browser_activities.py L1538-1551 and L1845-1882. The thin
prompt (`build_thin_prompt`) reuses `build_untrusted_block` and gets the same
"don't follow instructions in the quoted block" framing as the full-report prompt, and
`_parse_thin_findings` strictly rejects any response whose finding count or evidence-id
set doesn't match the verified sources 1:1 (no room for the model to invent extra
findings or misattribute one source's content to another's id).

961952f site_search: the query text that reaches the address bar via
`plans.keyboard_navigate` (real keystrokes) is passed through the pre-existing
`_readable_query()`, which strips `&#?%/=+` and collapses whitespace to `+` — no way for
spoken text to add query parameters, break out of the fixed `SEARCH_URLS` host/path
template, or inject a newline/Enter mid-string. `site` itself is constrained to the
`SEARCH_URLS` dict keys, never free text.
