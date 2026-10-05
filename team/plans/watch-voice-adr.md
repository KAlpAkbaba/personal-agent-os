# ADR (watch-voice): the watch by voice - four intents, owner's words win, briefing line, announcer

Status: proposed by worker (cycle d20261004); the lead numbers it.

## Decisions

1. **Four intents, five sentences ("beş niyet", return point 4).** create, list, remove, forget
   all. The card's fifth is the undo: "Nöbeti kaldır." is `watch_remove` with no label, which
   removes the watch this session created last (or the only one), else asks one question. One
   intent less to route and to collide; the undo is still its own spoken form.
2. **Router.** `_watch_match` runs beside the routines, before the alarm family. With the noun
   ("nöbet", never "nöbetçi"): question/list -> forget-all (exact forms `unut/unutabilirsin/
   unutalım/unutun` only - "unutma" is memory_remember) -> remove/forget (cancel verbs; plural
   noun or "bütün/tüm/hepsini" = forget all) -> create. Without the noun: an event verb in a
   conditional/temporal form ("çıkınca", "inerse", "değişince") AND a tell verb ("söyle",
   "haber ver"). "Evden/işten çıkınca" (ablative before "çık") is a presence trigger and is not
   a watch. Numbers: the normaliser already spells digits ("20.000" -> "yirmi bin"), so
   `_watch_number_at` reads spelled numbers only.
3. **The owner's words win** over the model's arguments for condition and interval
   (`ResolvedIntent.watch_*`, copied into the turn record in service.py); the model's `url`
   fills the page; with none anywhere, ONE missing-slot question "Hangi sayfayı izleyeyim?"
   (status `needs_clarification`, `WATCH_CLARIFYING_TOOLS`). No second confirmation.
4. **Briefing.** `_watch_sentence`: the readings that notified since the last
   `briefing.morning_delivered` ledger row (24 h when none), newest three, no preference switch,
   no line at all when none. Read in a savepoint: a missing table never breaks the briefing.
5. **Spoken AND in the briefing (return point 4).** A condition spoken by the announcer is
   still one line in the next briefing. Reason: the morning briefing is the owner's one
   summary of what happened; marking "spoken" needs new durable state (a column = a migration,
   outside this area), and a web session's `say` returns False while the frame waits in the
   buffer, so "spoken" is not reliably known. One line, at most three. The lead may reverse.
6. **Announcer** (`app/watch/announce.py`): only `condition_met`; owner_present must be True
   (unknown = no); quiet hours = the owner's ambient window, 23:00-07:00 Istanbul when unset,
   read-only and in a savepoint (fail closed); then `resolve_greeting_allowed`; then the same
   `RealtimeSayBriefingSpeaker` the pending briefings use. Spoken once per process.
   Risk: `greeting_allowed` is the arrival-greeting cooldown, so in practice it may rarely be
   True outside an arrival; the briefing still carries every change. Card-literal on purpose.
7. **Narrative.** `SUBSYSTEM_WATCH` -> "nöbet" in `facts._SUBSYSTEM_TR`.

## ALAN_ISTEGI - the four files outside the area (apply at merge)

A new tool family trips three guards and the corpus harness needs the two tables. With this
exact patch applied locally: guards 123/123 green, Owner Utterance Suite 2770/2770 (heavy slot
ts-32c7a7e0b9d3); the files were then restored byte-for-byte (sha256 checked).

```diff
diff --git a/services/api/app/security/step_up.py b/services/api/app/security/step_up.py
index 30e5a7b4..8f5db056 100644
--- a/services/api/app/security/step_up.py
+++ b/services/api/app/security/step_up.py
@@ -270,6 +270,10 @@ _TIERS: Final[dict[str, str]] = {
     "macro.cancel": TIER_SENSITIVE,
     "macro.run": TIER_SENSITIVE,
     "macro.delete": TIER_SENSITIVE,
+    # watch-voice: creating, removing and forgetting a watch change what the owner set up.
+    "watch.create": TIER_SENSITIVE,
+    "watch.remove": TIER_SENSITIVE,
+    "watch.forget_all": TIER_SENSITIVE,
     # ADR-0197: a tab in the owner's own browser, like media.play.
     "godseye.open": TIER_SENSITIVE,
     "scene.add": TIER_SENSITIVE,
@@ -342,6 +346,7 @@ _TIERS: Final[dict[str, str]] = {
     # gate is noise.
     "routine.list": TIER_OPEN,
     "macro.list": TIER_OPEN,
+    "watch.list": TIER_OPEN,
     "research.finding_detail": TIER_OPEN,
     "research.sources": TIER_OPEN,
     "scene.inspect": TIER_OPEN,
diff --git a/services/api/app/voice/capabilities.py b/services/api/app/voice/capabilities.py
index d4d7d621..4dc6c18b 100644
--- a/services/api/app/voice/capabilities.py
+++ b/services/api/app/voice/capabilities.py
@@ -73,6 +73,8 @@ FAMILY_TR: dict[str, str] = {
     "scene": "3B sahne",
     "state": "Anlık durum",
     "voice": "Ses yönlendirme",
+    # watch-voice: the owner's watches over public pages.
+    "watch": "Nöbetler",
     "weather": "Hava durumu",
 }
 
diff --git a/services/api/tests/unit/test_voice_realtime_sessions.py b/services/api/tests/unit/test_voice_realtime_sessions.py
index 3c6d2e30..fda2465e 100644
--- a/services/api/tests/unit/test_voice_realtime_sessions.py
+++ b/services/api/tests/unit/test_voice_realtime_sessions.py
@@ -482,6 +482,11 @@ def test_create_selects_by_capability_and_returns_the_contract(wired) -> None:
         "macro.run",
         "macro.list",
         "macro.delete",
+        # watch-voice: the owner's watches over public pages.
+        "watch.create",
+        "watch.list",
+        "watch.remove",
+        "watch.forget_all",
         # ADR-0197: God's Eye View in the owner's browser.
         "godseye.open",
         "routine.cancel",
diff --git a/services/api/tests/voice_corpus/harness.py b/services/api/tests/voice_corpus/harness.py
index e5144ce9..bf79ad77 100644
--- a/services/api/tests/voice_corpus/harness.py
+++ b/services/api/tests/voice_corpus/harness.py
@@ -169,6 +169,7 @@ from app.voice.realtime_sessions.research_announcer import ResearchToolCallAnnou
 from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
 from app.voice.realtime_sessions.sideband import RecordingSideband
 from app.voice.simulator import SimulatedRealtimeProvider
+from app.watch.models import Watch, WatchReading
 from app.weather.models import WeatherQueryEvidenceRow
 from app.weather.providers import FakeWeatherProvider
 from app.weather.service import WeatherService
@@ -322,6 +323,9 @@ TABLES = (
     VoiceProfile.__table__,
     # ADR-0196: the router reads the stored macro names on EVERY utterance.
     VoiceMacroRow.__table__,
+    # watch-voice: the owner's watches and their readings.
+    Watch.__table__,
+    WatchReading.__table__,
     NarrationSession.__table__,
     PronunciationEntry.__table__,
     Artifact.__table__,
```
