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

5. Wiring (2026-10-05, 3rd round): `tools_ambient.register_ambient_tools` registers
   `alarm.set_song` (step-up tier SENSITIVE, the same tier as the other alarm tools that
   change state); `alarm_create` calls `song_for_create` and reads the song back in the same
   sentence. A recurring alarm with a title the search resolved is no longer refused as
   "needs media". The router sends a song change as `ALARM_SONG_SET` (the next alarm's
   song, "alarmımın") or `WAKE_SONG_SET` (the global song); both go to `alarm.set_song`.
   The scope IS the intent, because the session's turn record carries no sentence ("turn"
   is the turn number). A title that only points ("Alarm müziğim bu olsun") names no song:
   the tool asks which one and searches nothing. `tr_time`: a bare digit hour with
   -de/-da/-te/-ta right after a day word ("yarın 7'de", "pazartesi 6'da") is a clock;
   the pattern is anchored on both sides.

## Consequences
- Old alarms are unchanged: without a song they play the global song, exactly as before.
- The corpus rows a.wakesong.set.1-3 ("no voice path for the wake song") now go to
  `alarm.set_song` and are refused with `no_song_named` (they name no song).
- The alarms page (apps/web) was left for a separate card.

## Addendum (inspector's return, 2026-10-05)
- The `song` column is the ONE truth for an alarm's own song: `alarm_song()` no longer falls
  back to `media_source`; an alarm created with `media.url` gets that url as its song at
  creation; `set_alarm_song` (set, change, clear) recomputes `media_source` and
  `resolved_media_identity`, so a cleared or replaced song is never a fallback candidate.
  A row written before this card with only `media_source.url` plays that url as the stored
  copy (3rd), after the live wake song.
- Every fallback play has its own idempotency key (`:{source}:{attempt}`): the device
  command client returns the old command for a repeated key.
- One guard (`_names_no_song`) for both readers: bu/şu/o/bir/aynı/her zamanki/seçtiğim/
  istediğim/sevdiğim name no song; nothing is searched, the approved wake song plays.
- The song phrase is taken out of the "when" text first (`alarm_text_without_song`); the
  clock is read from the rest. Clock words (geçe, kala, buçuk, çeyrek, "yediyi") stop the
  title. `tr_time`: "çeyrek gece" (cedilla dropped) is "geçe", not night.
- A song that was not found is said in the same answer ("X şarkısını bulamadım efendim;
  alarm uyandırma şarkınızla / zil sesiyle çalacak.").
