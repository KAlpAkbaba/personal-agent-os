"""Voice macros - a recorded sequence of spoken actions the owner names and replays
(owner note 2 of 2026-09-21, docs/DECISIONS.md ADR-0196).

The owner's word for one is a "hareket". "Yeni hareket oluştur" starts a recording on the
realtime session; every tool call that follows is both DONE and kept; "hareketi bitir"
ends the recording and asks for a name; the next sentence is the name; from then on
"<ad> aç" replays the kept calls, in order, through the very same tool handlers.

``naming`` is pure (keys and matching), ``models`` is the row, ``service`` is the
recording state on the session and the durable rows. The tools live in
``app.voice.realtime_sessions.tools_macros``; the router's words in ``app.voice.intents``.
"""
