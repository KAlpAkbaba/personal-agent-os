# ADR (unnumbered): a song per wake alarm, set by voice in the same sentence

Date: 2026-10-05. Task: alarm-song-by-voice. Owner's words: "alarmda istediğim müzikle beni
uyandıracak ya da istediğim müziği açacak".

## Context
The wake sequence (M18.3) played one global wake song (`PUT /v1/alarms/wake-song`), copied into
each alarm's `resolved_media_identity` when the alarm was created, with the device tone as the
fallback. An alarm could not have its own song, and the voice could not name one.

## Decision
1. `wake_alarms.song` is a nullable jsonb column `{"url", "title"}` (migration 0067,
   expand-only; downgrade drops only the column). `create_alarm(song=...)` and
   `set_alarm_song()` write it; only an http(s) URL is accepted; an alarm that has finished
   takes no song. `PUT /v1/alarms/{id}/song` sets it, and `url: null` clears it.
2. The song order at ring time (`sequence._song_candidates`): the alarm's own song, then the
   global wake song as it stands now (changed tonight, played tomorrow), then the copy taken
   when the alarm was created; each URL is tried only once. A failed play is a separate
   receipt. A fallback song gets its own idempotency key suffix, so it is not swallowed as a
   retry. If no song is verified playing, the tone rings with `media_failure_reason`. When a
   fallback song plays, the failure of the earlier song is still recorded on the row.
   `detail_json.media_played` records which song played (`source`: alarm_song | wake_song).
3. Voice: `alarm_song_query()` reads the title from the raw words just before the wake verb,
   using anchored shapes only: "X çalarak", "X ile", "X şarkısıyla", "X şarkısını çalarak",
   "X'ıyla/'la", and a capitalised bare "Xıyla". The title starts after the last stop word
   (beni, a day word, a digit, a spoken clock word), so neither the time nor the suffix ends
   up in the title. `alarm_song_set_match()` reads "alarmımın şarkısını X yap" (the next
   alarm) and "uyandırma şarkımı X yap / değiştir" (the global song); a question never
   matches. A bare "X çal" stays media.play, and "alarmı kapat" never sets a song.
4. The song is found the way media.play finds one: the device's own search and the first real
   watch URL. The read-back is one sentence: "Yarın 07.00'de Şımarık ile uyandıracağım".

## Consequences
- Old alarms are unchanged: without a song they play the global song, exactly as before.
- Wiring is still pending and needs files outside this area: registering the
  `alarm.set_song` tool and its step-up tier (tools_ambient.py, step_up.py), the
  `ALARM_SONG_SET` intent hook-up, `alarm_create` calling `song_for_create`, the bare
  "yarın 7'de" clock in tr_time.py, and the alarms page in the web app.
