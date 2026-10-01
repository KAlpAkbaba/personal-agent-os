## The Ofis page can be asked by voice: "ekip ne yapıyor?" (office-voice-summary)

Context: the Ofis page (ADR-0234) shows who works on what; the owner wants the same answer by voice,
as one paragraph, without opening the page.

Decision:
- `app/team/speech.py::office_paragraph(view)` is a pure function over `office_view`'s answer, so
  the page and the voice cannot disagree. At most ~60 words: how many of six work and on what
  (titles cut at the first clause, at most three), how many tasks came back, how many approvals
  wait, the usage-limit state when it is not ok, "tahmini" before any dollar figure. Task ids are
  never read. Nothing running: "Ekip şu an çalışmıyor efendim." (waiting approvals / returned
  tasks are still appended).
- One router (`app/voice/intents.py`): new intent `TEAM_STATUS`, a query (tool `team.status`, no
  side effect). Nouns are EXACT words ("ekip", "ajanlar"), never the stem "ajan" (= "ajanda",
  the calendar). "ofiste" counts only with "kim" + "çalış"; "ofiste ne yaptın" (device alias) and
  "bu hafta ne oldu" (narrative) keep their intents.
- Tool `team.status` (`tools_team.py`): empty argument object (the relay filters argument keys),
  result `{status, speech}`; reads `ctx.live["team_store"]`/`["team_root"]`, else a `FileStore` on
  the repository `team/` exactly as the route does.

Consequences: the lead registers the tool (`register_team_tools(reg)` in `default_registry`) and
adds `"team.status": TIER_OPEN` to `app/security/step_up.py`; without the tier the step-up policy
(shadow mode) logs it as `sensitive`. The service should inject `team_store`/`team_root` into
`ctx.live` from `app.state`; until then the FileStore fallback applies (correct only where the
`team/` directory is the queue's home, i.e. not in database mode).
