# Owner Actions — the live queue

`OWNER_ACTIONS_MINIMAL.md` says which actions are unavoidable in principle. This file says
which one to do **now**, in what order the rest come, and what happens automatically after
each. Everything not listed here is the agent's job.

Rules this file follows:

- one action at a time, with the exact command or click, not a research task;
- an action appears only when the work in front of it is finished and verified, so nothing
  here is waiting on the agent;
- each entry says what it unblocks, so a "not now" is an informed decision rather than a
  guess;
- no secret is ever typed into a chat. Where a value is sensitive, the entry names the local
  tool that takes it (`scripts/secret-store.ps1`, a masked prompt, DPAPI-encrypted, owner-only).

Status vocabulary matches `docs/QUALIFICATION.md`: `PROVEN_REAL`, `PROVEN_PROXY`,
`NOT_YET_PROVEN`.

---

## Now

**Evening sequence, 2026-09-07 — the shortest run that closes what only you can judge.**
Everything automatable in M18.2 and M18.3 was finished and gated during the day (ADR-0077,
ADR-0078; contract v10 on main). Four short items remain, each labelled with the one human
boundary it needs. Order matters only where written; D needs no audio and can go first.

> **Voice routing is PROVEN_AUTOMATED (2026-09-07 evening, ADR-0080).** 345 synthetic
> owner utterances now run through the real transcription boundary, the one router and
> the real tools every night (`scripts/core/voice-routing-qualification.ps1`), with zero
> wrong routes and zero forbidden side effects; the nine routing defects the first run
> found are fixed and pinned. What A and C still need from you is the audibility alone.
> The Cockpit's "Ses yönlendirme sınaması" panel shows the recorded state.
>
> **M18.4 foundation landed the same evening (ADR-0081).** The Evolution Supervisor now
> turns incidents and recurring failures into prioritised opportunities on the clock; you
> can say `Kendi kendini geliştirmeyi duraklat/aç`, `Bu geliştirmeyi iptal et`, `Bunu
> canlıya alma`, and ask `Şu an ne geliştiriyorsun?` / `Hangi sürüm çalışıyor?` /
> `Bekleyen aday sürüm var mı?`; the Cockpit has an "Evrim gözetmeni" panel. Nothing
> here needs you.
>
> **Production is blue/green since 2026-09-07 night (ADR-0081 addendum 2).** Your one Tailscale
> SSH check unblocked three qualification runs: the first cutover (4.1 s gap, once), then two
> releases and four rollbacks with zero dropped probes through the edge. Production runs
> c109302 (contract v12) on `api-blue`; every release from here is `release-cloud-core.ps1
> -BlueGreen` (the owner harnesses still call the single-container path; switch them when
> you next run one, or leave them - both work). Items 23b, 24, 25, 26 are unchanged and
> still yours; the M18.3 agent capabilities still wait on item 26's elevated update.
>
> **M18.4 final gap closure, 2026-09-07 late night (ADR-0081 addendum 3).** Production runs
> ae88edd on `api-green`. A release now hands the device sessions to the new colour BEFORE it
> takes HTTP (your agent's presence gap on a switch is its own 1–2 s reconnect, measured;
> the one release from the old colour had a named 61 s gap, once), a promotion killed
> half-way is rebuilt to the last completed one at boot or on demand, and the agent /
> browser-worker staged updates are built and tested (PROVEN_PROXY until your elevated
> run, items 26/27). Nothing new needs you; M19 waits for your word.

| | Item | Needs | Time |
|---|---|---|---|
| A | 23b — click one research, `Bunu teknik anlat.`, `Bir önceki araştırmayı anlat.` | `READY_FOR_OWNER_AUDIO_TEST` (releases the Cloud Core once, to v10) | ~2 min |
| B | 24 — look at the Living Core on your own screen | `READY_FOR_OWNER_VISUAL_TEST` | ~2 min |
| C | 25 — `90 saniye sonra ... test alarmı kur.`, YouTube, `Günaydın efendim`, `Alarmı kapat.` | `READY_FOR_OWNER_AUDIO_TEST` (+ one UAC prompt for the agent update if 26 did not run first) | ~3 min |
| D | 26 — displays off, one key wakes them | `READY_FOR_OWNER_PHYSICAL_TEST` (one UAC prompt: the agent update with `-DisplayPower`; no audio) | ~3 min |
| G | 29 — the OpenAI account behind `PAGENTOS_VOICE_OPENAI_API_KEY` has no credits left (429 'no credits remaining' on 2026-09-07 23:09Z, `docs/evidence/tts-loopback-2026-09-07-230910.json`): add credits so the TTS → STT loopback proxy (`scripts/voice/tts-loopback-qualification.ps1`) can re-sample the documents answers; until then the loopback mark for M20 rests on the first sample and the harness reports PROVIDER_UNAVAILABLE honestly | `READY_FOR_OWNER` (a paid account) | 2 min |
| F | 28 — the same elevated update with `-Operator` turns on the M19 Digital Operator family (app/window/keyboard/pointer/ui/screen/file/terminal) and the M20 documents family (file.search/locate/inspect/read/compare, document.extract) on your agent; until then the voice answers "Bu bilgisayarda operatör yetkisi yok" truthfully (ADR-0082) | `READY_FOR_OWNER` (folded into D's one UAC prompt: `install-device-service.ps1 -DisplayPower -Operator`) | 0 extra |
| E | 27 — the same elevated update now verifies itself: the staged candidate against its manifest before the swap, then Cloud Core must see the device online as 0.2.0 with every promised capability, else the engine rolls back (`install-device-service.ps1`; ADR-0081 addendum 3) | `READY_FOR_OWNER` (folded into D's one UAC prompt; watch for "candidate manifest verified" and "Cloud Core sees the candidate") | 0 extra |

Not repeated: research runs, the Eye, presence transitions - all proven on 2026-09-06.

**M18 is closed — nothing is required of you.** The presence-only run passed
(`OWNER M18 PRESENCE: PASS`: present 0.56 → away 0.41 → present 0.68, the eye closed at
the end), and with it every M18 capability the owner runs could prove is `PROVEN_REAL` from
the Cloud Core's own record (ADR-0064; `docs/evidence/`). Two rows stay open by name and
are optional, whenever you want them: a one-minute media-action run (row 12.13, an
owner-selected item playing on the real chain) and the display-off qualification
(row 12.19, deliberately separate). M18.1 — the Holographic Core visual experience pass —
is implemented and merged (ADR-0065); item 22 is your look at it, when you like.

---

### 25. The wake alarm, end to end — **M18.3 qualification B — `READY_FOR_OWNER_AUDIO_TEST` (one Cloud Core release and one elevated agent update, both inside the run; the whole path is proven end to end without ears, ADR-0078 — only the audibility is yours)**

Say one sentence and be woken ninety seconds later. Pick the YouTube music first: pass its
URL to the command, and the run registers it as your wake song (the system never chooses
one), opens the worker's own alarm profile once at that page so you can accept YouTube's
consent by hand if it appears (the profile keeps it; the worker never clicks anything), and
then asks you to speak.

```powershell
.\scripts\core\owner-m18-3-alarm.ps1 -MusicUrl "https://www.youtube.com/watch?v=..." -OutFile m18-3-alarm-1.json
```

It releases the Cloud Core once if it predates this checkout's contract, and if the
installed agent predates M18.3 it prints the update command for an elevated window (one
UAC prompt; `-DisplayPower` turns display control on) and waits for the device to come
back. Then, on `/core`, say **`90 saniye sonra seçtiğim YouTube müziğiyle test alarmı kur.`**
and wait: the displays wake, the music starts low and rises, and it greets you over the
music. When the script says so, say **`Alarmı kapat.`** It proves every step from the
record — created, armed on the device, fired by the clock at the instant, the display
wake receipt, the media really playing (or the tone, named truthfully), the ramp, the
greeting with its duck and restore, the stop, the cleanup — and finishes by itself.

### 26. Displays off, one key wakes them — **M18.3 qualification C — `READY_FOR_OWNER_PHYSICAL_TEST` (no audio; the script prints the one elevated agent update itself when display control is not yet on, so this can run before item 25)**

```powershell
.\scripts\core\owner-m18-3-display.ps1 -OutFile m18-3-display-1.json
```

It disables the eye first (the camera is deliberately out of this test), arms the display
test, and tells you to keep your hands off the keyboard and mouse. About twenty seconds
later the displays go dark: press **Shift** once, or move the mouse. They come back at
once. The script proves from the record that the darkening was a real receipt, that the
device observed the screen off and then on, that your input was recorded, and that for
the next ninety seconds nothing darkened the screen again although the presence state was
unknown. It finishes by itself; if the eye was on before, say `Gözünü aç.` afterwards.

The automatic, presence-based off (away for fifteen minutes, or asleep for ten with
confidence) stays OFF until you turn it on by voice (`Otomatik ekran kapatmayı aç.`); its
own longer observation comes when you want it.

**Hardened 2026-09-07 (ADR-0079; AMBIENT DISPLAY ENGINEERING COMPLETE).** The policy now
understands your own sentences, including the negations — `Ben yokken ekranları kapat /
kapatma.`, `Uyuduğumda ekranları kapat / kapatma.`, `Otomatik ekran yönetimini aç / kapat.`,
`Ekranı açık tut.` (outranks everything until you lift it with `Ekranı açık tutma.`), `Ben
geri geldiğimde ekranı aç.` — and answers `Ekranları neden kapattın?`, `Neden açık
bıraktın?` and `Şu an ekran politikası ne?` from the record. Quiet hours (e.g. 23:30–07:30)
can be set over `PUT /v1/ambient/policy`; outside them "asleep" needs three times the
evidence. A camera that stops delivering, a stale state, an alarm, or any recent keyboard,
mouse, command or return holds the screens on — and those holdoffs now survive a Cloud
Core restart. Nothing here needs sound; this item stays the one physical check.

### 24. Look at the Living Core — **M18.3 qualification A — `READY_FOR_OWNER_VISUAL_TEST` (web only: no release, no install; gates green 2026-09-07 - 810 web tests, tsc, lint, production build; the in-app preview here sits behind your login, so nobody has seen the WebGL scene but you)**

The small wireframe is gone. `/core` is now a full-viewport gold and amber Core: nine
layers (an outer field, two shells, three orbital rings turning at different rates and
directions, a circuit layer, bounded particle transport, floating processor fragments, an
energy chamber and a warm white-gold nucleus), a near-black ground, and only small overlays
— a caption, a connection dot, a control cluster that fades after four seconds and returns
when you move the pointer, and a slim strip for the room, the camera, the screens, the
alarm and the release path. A fullscreen control (Esc leaves it) and a standalone-app
manifest are there. It still draws only what is true: idle breathing, then listening pulls
inward, speaking pulses with the real playback, the eye's aperture appears with the real
camera, research grows its bounded constellation. A server-rendered preview page was sent
to you; the WebGL scene on your own screen is what this item is about.

```powershell
.\scripts\core\owner-m18-3-core.ps1 -OutFile m18-3-core-1.json
```

It starts the web shell, proves the served build is the Living Core (the document's own
build marker), and prints three things to try: a sentence, `Gözünü aç.` / `Gözünü kapat.`,
and optionally a short research. Scale, depth, colour and motion are yours to judge; the
script records only that the Core really listened and really spoke, and finishes by itself.
Tell me what you would change — the reference you mentioned is welcome as inspiration.

### 23b. Point at one research, then two sentences — **M18.2 follow-up — `READY_FOR_OWNER_AUDIO_TEST` (M18.2 engineering is complete: the focus, the resolver and the result contract are on main and gated; the command releases the Cloud Core once, before the check)**

**Updated 2026-09-07 after your fourth run.** Your record proved the crawling, the titles
and the focus; the defect was narrower and it is fixed at its mechanism (ADR-0077). What
the rows showed: `research.explain` had recorded a clarification as a *succeeded* call
with no research behind it; "Bunu teknik anlat." was answered by `activity.explain`,
which read the model's paraphrase ("bir önceki") instead of your words, narrated the
ledger's telemetry instead of the report, and left a narration open — so "Bir önceki
araştırmayı anlat." became a cursor jump inside it and never reached the previous
research. Now a tool result is a contract: a clarification is its own status (never a
success), a succeeded research answer always names its job and speaks, and on a research
turn both tools give the one answer `research.explain` gives — from that report, bound by
what you said, no narration left behind. The command below is unchanged; it releases the
Cloud Core once (contract v9) before you speak.

**Updated 2026-09-07 after your third run.** The record shows what you heard: on the
earlier contract "Teknik anlat." started a research and explained that one; on the guarded
contract the fresh session asked "iki tamamlanmış araştırmam var … hangisini anlatayım?"
six times in a row, because nothing could take your spoken answer and a page reload had
dropped the conversation's link to the research. The fix is the architecture you described:
a research is a thing you point at — an ID-based, owner-level **focus** that survives
reconnects and reloads (set when a research completes, when its result is spoken, when you
click one in the cockpit or the research page, when you choose one by voice, or when a
"bir önceki" moves it), a bounded stack for "son / bir önceki / ikinci", a deterministic
resolver for "bunu / bu araştırma / bunun kaynakları / bir öncekini / OpenAI araştırması",
follow-up tools that run only against a resolved id, a clarification you can answer ("ilki",
"20:19'daki", "sonuncusu") asked at most once, and no crawl for an unresolved reference.
Research items now show their time, mode and source count so two with the same title can
be told apart.

The retest is short and re-runs nothing:

Your run of 2026-09-06 proved every core row of M18.2 (item 23 below is closed on them).
Your follow-up run then proved the routing, the diagnostics-only-now and the conciseness —
and found the real defect: the model also started a second research on the fresh session.
The fix is architectural, not a phrase: the router now classifies research turns
(new research / follow-up / technical explanation / explicit re-run), a follow-up binds to
the completed job by identity, and `research.start` is refused at the server for a
follow-up turn unless you explicitly ask for a fresh run ("Araştırmayı yeniden yap."). The
check below waits for you, on a new session, releases the Cloud Core once BEFORE it starts
when the deployed one predates the guard, and re-runs no research:

```powershell
.\scripts\core\owner-m18-2-followup.ps1 -OutFile m18-2-followup-1.json
```

1. Open `/core` (the cockpit's research panel) or `/research` and **click one completed
   research** — any one. It becomes the conversation focus; the script continues by itself.
2. Connect voice and say **`Bunu teknik anlat.`** — the diagnostics of exactly that one.
3. Say **`Bir önceki araştırmayı anlat.`** — the one before it in the focus stack.

The script proves from the record that each explanation names the exact job and artifact
it should (the selected one, then the previous one — by id, never by title or time), that
`research.start` ran zero times and zero research tasks were created, that both reports
kept their artifacts (nothing recomputed), that at most one clarification was asked, that
nothing was deployed from your first sentence on, and that the answers stayed within the
technical budget. Paste the `checks` block back here; M18.2 closes on it.

### 23. Speaking continuity and research findings — **DONE on the core rows (2026-09-06 run, session be6d49ce; reconciled in `docs/evidence/m18-2-reconciliation-2026-09-06.json`); the follow-up is item 23b**

**Updated 2026-09-07 after your real run.** Research was functionally working but far too
slow and CAPTCHA-heavy (254 candidates, minutes). The pipeline now has a fast path
(ADR-0068): QUICK is the conversational default — target 60–90 s, hard budget 120 s, two
discovery queries, a ranked shortlist, fetches in waves of four with an early stop once the
evidence is enough; STANDARD (2–3 min) and DEEP are taken only when you say so ("geniş",
"karşılaştırmalı" / "kapsamlı", "derinlemesine", "detaylı"). A CAPTCHA, bot check or
consent wall is left alone: the page is abandoned in one attempt with its reason recorded,
the domain is cooled after a second one, and the next ranked source is taken — nothing is
ever bypassed. The Core shows only coarse Turkish progress ("Kaynaklar aranıyor",
"4 güvenilir kaynak incelendi", "Bulgular doğrulanıyor", "Sonuç hazırlanıyor"). The harness
crash you hit (a bare character in a word list) is fixed and pinned, and the speaking check
now reads the turn's own timestamps. Test B below should finish within about two minutes;
if it takes clearly longer, that is a finding in itself — `Teknik anlat.` afterwards names
what was slow and what was left alone.

Two defects you observed are fixed at their mechanisms. SPEAKING ended early because the
voice controller left the state on the provider's `response.done`, which on WebRTC marks
the end of generation while the audio buffer keeps playing; the provider's own
`output_audio_buffer.stopped` is the true end of playback and now ends the state (with a
silence release and a cap as fallbacks, and an immediate end on `Dur` or barge-in). The
pulse is the playback RMS; a pause is a calmer Core that stays SPEAKING. Research spoke
crawler counts because the explain engine's executive sentence was built from the run's
statistics; worse, a spoken `Araştır` never started the real pipeline at all — the tool
returned "running" and sat there. Now `research.start` starts the same real run as the
REST route, the run's durable report is turned into a deterministic `spoken_result`
(a conclusion, up to three findings each with why it matters, an offer), the announcer
completes the tool call with it, and the diagnostics are spoken only on `Teknik anlat.`,
`Hangi sayfalar elendi?` or `Araştırma sırasında ne sorun oldu?`.

```powershell
.\scripts\core\owner-m18-2.ps1 -OutFile m18-2-1.json
```

It releases the Cloud Core once if it predates this checkout's action contract (the
version is read from the code, never a literal), starts the web shell, and prints three
lines. Sign in at `/core`, connect voice there, then say:

- **A.** `Bana PagentOS'un ne olduğunu beş cümleyle anlat.` — watch the Core stay in
  SPEAKING through the pauses and end at the last word.
- **B.** `Son üç gündeki yapay zekâ ajan gelişmelerini araştır.` — a real run, a few
  minutes: the Core researches, then presents findings, not statistics.
- **C.** `Teknik anlat.` — only now the eliminated pages and the problems.

It finishes by itself after C, from the session's own record: the turn's `first_audio` and
`audio_done` (with the playback basis and the audible duration), the research call's
terminal result and spoken head (checked to contain no crawler words), and the technical
follow-up after it. Paste the `checks` block from `m18-2-1.json` back here.

### 22. Look at the new Core — **optional, when you like (M18.1)**

The Core on `/core` is now a layered structure — nucleus, concentric rings, rotating
topology layers, translucent shells, internal connection paths, two bounded particle flows,
depth and glow — and much larger in Minimal mode. Every motion is a published state or a
real measurement: listening pulls energy inward by your own mic level; speech pulses the
Core with the real playback envelope; thinking and tool work expand the topology; research
grows a restrained constellation of sources; memory retrieval converges toward the Core; a
SHADOW_READY capability appears as a parked peripheral node; the eye draws a thin aperture.
Idle is breath and a minute-scale drift, nothing more. `docs/M18_1_CORE_VISUAL_LANGUAGE.md`
is the design note.

A server-rendered preview of the 2D fallback for ten states was handed over in chat; the
WebGL scene itself has not been rendered on any screen yet (no browser is launched here), so
your look is the visual check. Start the shell as usual, open `/core`, then `/core/cockpit`;
connect voice and say anything, open the eye, ask `Araştır …` if you want to see the
constellation. Quality is High / Balanced / Low in the Core's preferences; Low draws no
particles and no parallax. Say what does not read right, or what reads as motion the system
did not actually have.

**Item 21 — the presence-only run (about six minutes, most of it out of the room) — DONE.**
M18 closed from combined durable evidence, not from one monolithic run. Your final run
(`owner-m18-20260906-181122`) and the eye run before it prove 13 of the 15 acceptance
capabilities from the Cloud Core's own record (`scripts/core/reconcile-m18.ps1`,
`docs/evidence/m18-reconciliation-2026-09-06.json`): voice from `/core`, real listening and
speaking states, cognition on both paths, the eye opened and closed by voice with verified
receipts, the re-enable, one mutation path, a camera presence observation, the alarm over
the device path with its ramp, the ledger, privacy. The long harness's `FAIL` rows were
qualification-selection defects (your session was created nine seconds after the harness
started, while `/core` was still compiling, and the readiness filter excluded it; the
presence watcher then started after your transition had already been recorded). Those are
fixed in the harness, which you do not need to run again. What the record does not hold is
you leaving the room and coming back with the camera on. Item 21 asks for exactly that and
nothing else. Items 19 and 20 are done.

---

### 21. Leave the room and come back — **DONE (2026-09-06, OWNER M18 PRESENCE: PASS; M18 closed)**

Unblocks: `docs/QUALIFICATION.md` row 12.6 (a real presence transition), the last open M18
row. No voice, no deployment, no long wait: the camera is opened from the Core's control
(or by voice, either is fine; that path is already proven), and the script reads the
Presence Engine's durable rows with per-step windows sized by the engine's own policy.

```powershell
.\scripts\core\owner-m18-presence.ps1 -OutFile m18-presence-1.json
```

It starts the web shell, closes the eye if it was open (printed, attributed), and prints
three lines. Open `/core`, sign in, press `Gözü aç` (or say `Gözünü aç.`), sit in view and
move a little. When the terminal prints **`present recorded - LEAVE NOW`**, leave the room
and stay out; the engine needs about 2.5 minutes of sustained absence before it says `away`
(the camera remembers movement for 90 s, then 45 s of sustained evidence). When it prints
**`away recorded - COME BACK`**, come back and sit down in view. It finishes by itself when
your return is recorded, then closes the eye. Every 10 s it prints the live assertion and
the durable transitions so far; each step that is not reached is named with what was seen.
Paste the `checks` block from `m18-presence-1.json` back here.

---

### 20. Your voice opens and closes the eye, and every answer is grounded — **DONE (2026-09-06, session 1ce36ca0: aç → kapat → aç, all three receipts verified; see item 21)**

Unblocks: `docs/QUALIFICATION.md` rows 12.26–12.29 (action receipts; the symmetric enable
path; live current-state answers; the confirmation spoken only after the terminal ACK).

**What changed.** Eye commands are now real tools the assistant must call (`eye.enable`,
`eye.disable`): the browser closes or opens the camera first, the Cloud Core writes the
durable state, reads it back, and returns a receipt whose sentence is the only thing the
assistant may say: `Gözümü kapattım efendim.` / `Gözümü açtım efendim.` / `Gözüm zaten
kapalı efendim.` / `Kamerayı kapatamadım; işlem doğrulanmadı.` — never make-believe.
Current-state questions (`Kendi sisteminde şu anda ne görüyorsun?`, `Kamera açık mı?`) go to
a live-state tool that answers result-first from the runtime and the World Model, with each
fact carrying a source and an age, not from the ledger and not with "kayıtlara bakmalıyım".

**What your first run of this command found (2026-09-06 afternoon).** It waited at
"waiting for a web voice session" and `Gözünü aç` did nothing in `/core`, because the
deployed Cloud Core still advertised the M17 tool set: no `eye.enable`, no `eye.disable`, no
`state.now`. The contract was merged but never released, so there was nothing for the
router to reach. The command now checks that first and releases the Cloud Core once if it is
stale (the same transactional release as before: build, migrate, recreate the api container
only, health check, rollback on failure — about five minutes; nothing on Windows is
touched). Two more things it found: a closed session's `agent.listening` stayed the Core's
current state (sessions now publish `agent.idle` when they end), and a page reload left the
previous voice session open on the Cloud Core (a reload now closes it).

**Your second run of this command (2026-09-06, later that afternoon) did the release.** The
working tree was clean, the transactional Cloud Core release succeeded, production now
advertises `eye.enable`, `eye.disable` and `state.now`, `/core` answered, and the
closed-session leftover was no longer accepted as current state. Then the harness crashed
before your part began: `Get-NewSessions is not recognized`. Both of its wait callbacks had
been bound with `GetNewClosure`, which runs a block in a fresh module scope that cannot see
functions dot-sourced into the script. The fix is structural, not a renamed symbol: the wait
now owns its own fail-fast and progress logic inside the library, callbacks receive
everything as arguments, every local in the wait and the selector is prefixed so a callback's
own variables are never shadowed, and a new static guard parses every harness and library
and fails on any command nothing declares and on any closure. The whole thing was then run
twice against production without you: the deployed-tools check passed with no release, the
web shell started in 3 s, real state was reported truthfully, and the wait stopped after
35 s with the exact reason. That run also exposed that the harness left `next dev` running
after it finished; it now stops the whole process tree and refuses to start when something
already listens on the port. **Production is not redeployed by this run** (the tools are
already there); the one runtime change since (a truthful `agent.idle` at startup) waits for
a later release.

**Your third run (2026-09-06 evening) found the two real defects, and they are fixed.**
The record of session 3eb6fee7 shows every eye tool call reaching the Cloud Core with no
`observed_after`: the provider delivers tool names as `eye__disable` (dots are not allowed),
and the browser compared that against `eye.disable`, so its local action never ran, the
server recorded `capability_missing` and said "işlem doğrulanmadı". What actually closed
your camera at 13:57:33 was the old utterance safety net, 7 ms after the tool call — a
second, unreceipted mutation path. Both are gone: the browser normalises the name in one
place (pinned with the vendor spelling), and the safety net no longer mutates anything (an
utterance is resolved and audited, never executed). `Gözünü aç` never reached the browser at
all for the same reason. Also fixed: the harness could not see your session because you
connected voice before it took its baseline; it now correlates by the session id the Core
itself publishes on the bus. Receipts now carry the session id, the media track's own
readiness and the browser's action trace; a camera that closed but whose record could not
be verified is said as exactly that (`Kamera kapandı ancak işlem kaydını doğrulayamadım.`),
never as "kapatamadım"; and each browser failure has its own sentence (permission, no
camera, camera busy, stream failed, track ended, loop failed).

**Your fourth run (2026-09-06 evening, session 9df439af) proved steps 1 and 2 for real for
the first time** — `Gözünü aç` opened the Logi C615 (browser ACTIVE, track live, runtime
`eye_enabled=true`, `Gözümü açtım efendim.`), `Gözünü kapat` closed it (DISABLED, track
ended, `eye_enabled=false`, `Gözümü kapattım efendim.`) — and step 3 failed three times
with the same trace: `request:stop_local > superseded:stop > state:ENABLING->DISABLED`.
That `stop_local` has one source: the Core's eye control stops the local loop whenever the
bus still says `eye.disabled` and the loop reports running. On a second enable the bus is
still carrying step 2's `eye.disabled`, and the loop starts before the durable enable is
written, so a stale bus state cancelled a newer transition. Step 1 escaped only because the
bus's older `eye.disabled` had decayed by then. Fixed as generation-owned transitions: a
bus-driven stop is a dated request that applies only to an ACTIVE loop older than the
event, never to an enable in flight; late callbacks of an older generation are ignored;
every enable creates a new stream and the trace records the track id. The
`eye.no_hidden_mutation` failure in the same run was a defect in the check itself (it read
the receipt's timestamps from the wrong level and built no windows); fixed against the
production rows, and eye rows now carry the `action_id` of the voice action that wrote
them, so correlation is by identity.

**One command (this run releases the Cloud Core once, about five minutes — the deployed
contract predates these fixes):**

```powershell
.\scripts\core\owner-m18-eye.ps1 -OutFile m18-eye-4.json
```

It refuses to start if port 3000 is already taken (it names the process; stop it or pass
`-SkipWeb`), starts the web shell, waits for `/core`, closes the eye durably if it was open
(printed, attributed), and prints three lines. Sign in at `/core`, connect voice **there**,
then say:

1. **`Gözünü aç.`** — it must answer `Gözümü açtım efendim.`
2. **`Gözünü kapat.`** — it must answer `Gözümü kapattım efendim.`
3. **`Gözünü aç.`** — it must answer `Gözümü açtım efendim.`

Nothing to press. The script finishes by itself the moment the third receipt is verified,
then closes the eye durably (printed, attributed) so no camera is left running. While it
runs it prints, on every change, the Core Voice session, the last routed action and its
action id, the browser's eye state and media track, the receipt with its sentence, the
live-state answer and the runtime read-back. It stops with the exact missing evidence if no
session is correlated within 2 minutes or a step is not verified within 2 minutes of the
previous one. Paste the `checks` block from `m18-eye-4.json` back here, or the whole file if
anything fails. If the browser refuses the camera, the assistant must say exactly that
(`Kamerayı açamadım; tarayıcı kamera izni vermedi.`) — a correct answer, and the file will
show the browser's own trace.

**Item 19 — the long M18 run, from `/core` alone.** Waits for item 20. Everything before it
is built, merged and proven on fakes; it is the only thing that cannot be proven without you:
your real microphone, your real camera, your real room, and a real alarm through the real
chain. About eight minutes, most of it you leaving the room and coming back.

Item 9 (K66 re-qualification) stays open and optional; it is not on M18's path.

---

### 19. The Core, your voice, the camera, one quiet alarm — one run — **DONE except the presence transition (2026-09-06 run owner-m18-20260906-181122, reconciled from the record; the remaining row is item 21)**

Unblocks: `docs/QUALIFICATION.md` Stage 12 rows 12.1, 12.5–12.8, 12.12, 12.16, 12.17 and
the integrated rows 12.21–12.25 (voice from the Core; real listening/speaking states; a
cognitive request from the Core; `Gözünü kapat` by voice; one session, one microphone).

**What happened on the first attempt (2026-09-06 morning), so you know what was real:** the
Cloud Core release went through, the Windows agent update was verified, the alarm pair was
advertised, `/core` answered, and the alarm routine triggered. Then the harness itself
crashed on a PowerShell shape bug (fixed, with a regression suite), the presence model read
you as `away` while you sat in front of the camera (the client was measuring whole-frame
motion; rewritten — the unit is now the grid cell, and presence is a 90 s memory), the Core
showed "Sahip durumu bilinmiyor" because a held state was never republished (fixed — it
heartbeats), and your `Gözünü kapat` was recorded as never having happened because the
harness gated it on something unrelated (fixed). And `/core` and `/voice` were still two
pages: the Core could not hear you. That is the change this attempt qualifies — the voice
session now lives under the Core, and `/voice` is a diagnostics view of the same session.

**What you are checking.** From `http://localhost:3000/core` alone: the Core loads real
state; voice connects there; when you speak the Core actually listens; the answer comes
through the real Realtime system and the Core moves to the generated speech; a real
cognitive request reaches its subsystem; the camera can be enabled; a current presence
observation reaches the World Model and the Core; `Gözünü kapat` really stops perception;
the alarm still works; everything closes; and no second microphone or realtime session is
ever opened. Every one of those is asserted from the Cloud Core's own records — there are no
yes/no questions this time.

**Two things may happen automatically inside the command:** a Cloud Core release if the
deployed core predates this checkout (transactional, rollback on failure), and a stop with
`OWNER M18: NEEDS_AGENT_INSTALL` if the installed agent lacks the alarm pair (it prints the
one install command and never installs anything itself).

**One command:**

```powershell
.\scripts\core\owner-m18.ps1 -OutFile m18-2.json
```

It starts the web shell, waits for `/core`, and prints a seven-line script. Sign in at
`/core`, connect voice **there** (the voice control under the Core, not `/voice`), then:

1. say **`Son yaptıklarını anlat.`**
2. say **`Kendi sisteminde ne görüyorsun?`**
3. enable the camera (`Gözü aç`) and stay in view about 30 seconds
4. leave the room for about 2.5 minutes, then come back and sit down
5. say **`Gözünü kapat.`**
6. a quiet alarm ramps from 5% to 40% over 10 s, rings 15 s and stops by itself
7. disconnect voice on the Core, then press Enter in the terminal

The script watches the Cloud Core the whole time. The presence part needs the 2.5 minutes
away: the engine needs sustained evidence (45 s) to change its mind, and the client
remembers movement for 90 s before it lets you go.

Expected last line: `OWNER M18: PASS`, and `m18-2.json` beside the repo — paste its `checks`
block back here. If any check fails, paste the whole file; it names the failing assertion.

**What it deliberately does not do:** turn your display off (built, gated twice, its own
later qualification — a wrong inference there interrupts unrelated work), deploy the
SHADOW_READY candidate (it asserts the candidate set is unchanged after the run), or store a
single camera frame anywhere (it reads the ledger back and fails if any presence row carries
anything but the structured summary).

Speaker-verification samples (VoiceIdentity) come later and will be their own step.


## Queue

### 1. Windows Service install — **DONE (2026-09-01)**

Completed and qualified: service LocalSystem/Session 0, companion Session 1, pipe DACL
from the real handle, both restart proofs, final credential rotation. The runtime is
frozen; `docs/QUALIFICATION.md` holds the evidence.

### 2. `gh auth login` — **NOT NEEDED (2026-09-01)**

You were already authenticated, so no action was required. Private repository
`KAlpAkbaba/personal-agent-os` created (visibility confirmed `PRIVATE` before any code left
the machine), `main` pushed, CI running.

The first real CI run immediately earned its keep: it found four environment assumptions the
local gate satisfies by accident — including that `pytest -m "not integration"` was
deselecting **all 1418 tests** and asserting nothing. All four are fixed; see the CI section
in `docs/DECISIONS.md`.

### 2b. Install Tailscale on this PC — **DONE (2026-09-01)**

Running, 100.92.148.30 / mail.tail0e6789.ts.net. Criterion 5.2a is PROVEN_REAL: the same
check confirmed the agent still owns no inbound listening socket and no firewall rule
names it.

### 3. Hetzner account + API token — **DONE (2026-09-01)**

Provisioned: server 164238173 (pagentos-core, cpx32, nbg1, running), 100 GB volume,
firewall, SSH key. SKU chosen by the owner after the 15 June 2026 price rise — see
ADR-0033.

### 3b. Break-glass session to finish the tailnet join — **DONE (2026-09-02)**

Completed: the host joined the tailnet as a non-ephemeral node, the temporary SSH rule was
removed automatically, and Stage 5 closed PROVEN_REAL end to end (Hetzner → Tailscale →
DeviceService → Companion → real Notepad → ACK, plus all five recovery scenarios).

Unblocks: 5.2b, 5.3, 5.4 and every criterion after them. See **Now** above.

### 4. Bootstrap the owner credential on the real deployment — **DONE (2026-09-02)**

Completed during the cloud migration: the cloud Owner Credential was minted host-side
through the loopback-only guard and shown exactly once in your local elevated console.
The server keeps only its hash. If it is ever lost, rotate on the host:

```powershell
ssh root@pagentos-core "docker exec pagentos-prod-api uv run python -m app.identity.recover --rotate"
```

### 5. Enroll the owner's browser session

Unblocks: real-browser qualification against sites you are already signed into.

The agent will give you the exact enrollment step when it reaches this point; it needs the
browser closed once, and it never asks for a password.

### 10. M13 Real Browser + Research — release, agent update, first real research — **ready**

Unblocks: `docs/QUALIFICATION.md` Stage 9 rows 9.1–9.10 (`PROVEN_REAL`); today they are
`PROVEN_PROXY` from the local real chain (`scripts/e2e-m13-research.ps1`: dev Cloud Core →
dev DeviceService/Companion → Browser Worker → real Chrome → live Internet → 12 sources →
report → PDF/DOCX artifact → memory → Cloud Core restart mid-job resumed without duplicate
evidence).

What changed since the last release (`a139e28`): the research pipeline and device layer
(ADR-0050), migrations `0012`/`0013`, `PAGENTOS_WORKER_MODE=embedded` on the `api` service,
the `/v1/research` and `/v1/devices/select|PATCH` routes, plus every M12 client fix. The
release transaction is the proven one (ADR-0042): build → migrate → recreate `api` only →
health → contract v2 → key PRESENT → provider listed → real Realtime mint → rollback on any
failure. PostgreSQL, Redis, MinIO, Temporal, the device row and the owner identity are
untouched.

Step A — release the Cloud Core (from the repository root, ordinary shell; the script talks
to `pagentos-core` over the tailnet, prints no secret):

```powershell
.\scripts\cloud\release-cloud-core.ps1
```

Step B — update the Windows agent once (this provisions the Browser Worker: a Python
environment under `C:\Program Files\PagentOS\agent\browser`, the real Chrome channel, a
dedicated PagentOS profile under `%ProgramData%\PagentOS\companion\browser`; it preserves
the tailnet broker endpoints, the device identity and the enrollment; one UAC prompt).

Your first attempt (2026-09-03 16:41 and 17:09) built and staged the M13 binaries and then
failed silently at the swap: the installer still used the pre-engine directory rename, which
NTFS refuses while the service and companion run from those directories — the same incident
the journaled deployment engine was written for on 2026-09-01, which the installer had never
been wired to. It now deploys through that engine (stop by PID → same-volume renames →
ACL → start → health → commit, rollback on any failure), writes a transcript under
`C:\ProgramData\PagentOS\install-logs\`, prints the evidence block (repo HEAD, source /
staged / installed artifact hashes, the executables the SCM and the logon task point at and
are running, the worker path, the installed capability manifest) and **fails loudly** if the
live binary does not answer the `capabilities` verb with the browser family.

```powershell
Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\scripts\install-device-service.ps1"'
```

(`-NoExit` keeps the elevated window open so the last lines are visible: either
`INSTALL VERIFIED …` in green or `INSTALL FAILED: …` in red with the log path.) Then, in an
ordinary shell, confirm the device advertises the browser family and the worker self-check
passes as you:

```powershell
.\scripts\verify-device-service.ps1
```

Step C — the first real research. Start the web client against the Cloud Core and open the
research page:

```powershell
.\scripts\voice\start-web-voice.ps1
```

Open `http://localhost:3000/research`, sign in with the Owner Credential (masked, as on the
voice page), keep the prefilled topic "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli
gelişmeleri araştır.", device "Otomatik" (or type `ev bilgisayarımda araştır` to prove the
explicit target), press **Başlat**. Expect a visible Chrome window on this PC working
through searches and pages for two to four minutes; the page shows the stage and counters
live. When it reads "ready", the report appears (Yönetici Özeti → Bulgular → Neden Önemli →
Sonraki Sinyaller → Ayrıntılar → Kaynaklar), the artifact lands in the inbox with PDF/DOCX,
and the memory entry exists.

Your verdict, in a sentence or two: is the executive summary concise, do the 3–7 findings
matter, are the sources traceable? Paste the task id (the page shows it) or the
"Rapor JSON'unu kopyala" output. Note: without an LLM key on the host the synthesis is the
deterministic provider (findings are provenance summaries, not prose); the owner's existing
OpenAI key on the host makes `auto` pick OpenAI automatically once
`PAGENTOS_OPENAI_API_KEY` is set there — say so if you want that switched on before the run,
it is one `set-cloud-secret.ps1` call with the same key value.

### 11. M13 real browser action through the whole owner path — **ready**

Unblocks: `docs/QUALIFICATION.md` 9.2 (`PROVEN_REAL`), the first real evidence that a
command travels Hetzner → Tailscale → DeviceService → your-session companion → installed
Browser Worker → real Chrome and back, with correlated ids. Step B of item 10 is done
(6b.1/6b.2 `PROVEN_REAL`, 2026-09-03).

Harmless and deterministic: opens `https://example.com/`, reads its title and text, runs one
web search, asks for a download the research policy must refuse, closes the session. Every
step is one device command with its own trace id, awaited on the Cloud Core; the script
records the companion image, the worker process (python from the installed tree, child of
the companion, running as you), the Chrome process on the PagentOS profile, and the
companion's `browser_request` audit rows for these commands. The credential is typed
masked and the session is revoked at the end. From the repository root, ordinary shell:

```powershell
.\scripts\browser\real-browser-smoke.ps1 -OutFile browser-smoke-1.json
```

Expect a Chrome window to appear briefly on this PC. Paste the final lines (each command's
status, `command_id`, `trace`) and `browser-smoke-1.json` (ids, timings, the example.com
title/text and process images — no secret).

### 13. Browser lifecycle: update, PROVE the live worker, then one visible Chrome window — **DONE (2026-09-04)**

Result: live worker 0.3.0, one Chrome process/window, twelve operations on one session, clean exit, owner's Chrome untouched; QUALIFICATION 6b.3, 6b.4 and 9.13 are `PROVEN_REAL`. Do not rerun.

#### (record) what item 13 asked for

Unblocks: `docs/QUALIFICATION.md` 6b.3, 6b.4, 9.13; item 12 resumes after it.

**Your 2026-09-04 result was a deployment truthfulness defect, now root-caused from real
evidence (ADR-0050 item 16).** The installer had really replaced the source tree under
`C:\Program Files\PagentOS\agent\browser`, but the worker never runs that tree: it runs the
copy that uv installs into the tree's venv, and uv had rebuilt that copy from a stale cached
wheel (its cache key is the modification time of `pyproject.toml`, which code-only releases
never change, and the staging path is the same every time). The installer's self-check did
not notice because it ran with the browser tree as working directory, which made Python
import the fresh source instead of the venv copy; the companion starts the worker from its
data directory and got the stale copy. So "INSTALL VERIFIED" was true about files and false
about running code.

What changed: the worker now reports which file it executes and a digest of its whole
package; the source declares its release (0.3.0); the installer always rebuilds the package,
runs every self-check exactly the way the companion runs the worker, compares the venv copy
with the source file by file, and after the swap waits for the companion to start the worker
and checks the live process (pid newer than the deployment, executable inside the installed
venv, reported release equal to the staged source, every pre-swap worker pid gone). Any
disagreement rolls the deployment back and prints `INSTALL FAILED`. The smoke script proves
the live worker against this checkout's release before it asks for any Chrome operation, and
if that fails after an update it says `deployment/version mismatch` and tells you what to
paste; it never asks you to rerun the same command.

Locally verified before asking you: the stale build was reproduced from the same staging
path and refused by the new checks; the fixed build passed; the dev-chain harness now runs a
packaged worker the same way and passed end to end; CI is green.

One command. It updates the agent (one UAC prompt), proves the live worker (release, module
origin, package digest) before any browser operation, then opens one Chrome window, runs
twelve operations in that same session and exits leaving zero PagentOS Chrome:

```powershell
.\scripts\browser\real-browser-smoke.ps1 -UpdateAgentFirst -Mode lifecycle -OutFile browser-lifecycle-1.json
```

Expected last line: `REAL BROWSER SMOKE: PASS`, preceded by `live worker proven: release 0.3.0`.
If it stops with `deployment/version mismatch` or `INSTALL FAILED`, paste the message, the
install log it names and the output of `.\scripts\verify-device-service.ps1`; do not rerun.

### 18. One conversation that qualifies all of M17 - **DONE (2026-09-05, 2nd attempt: OWNER EXPLAIN: PASS, all six cognitive paths)**

Result: Stage 11 is `PROVEN_REAL` and M17 is closed. Do not rerun.

#### (record) what item 18 asked for

The first attempt on 2026-09-05 failed, and it was worth failing: five of your six questions
were answered correctly, one crashed, and one never reached the tool at all. Four of the
five findings were defects in the CHECKER rather than the product - most importantly the
durable record was not saving which subsystem answered, so four reached subsystems were
reported as unreached.

All of that is fixed and re-proven without you: every one of the six now routes correctly
against the live production database, cites its own subsystem's records, and the harness
itself was run end to end against a real production session and passed every check.

The one thing that still cannot be proven without you is that the SPOKEN questions reach the
tool - especially the authority one, which the model previously answered from its own head
instead of from policy.

Unblocks: `docs/QUALIFICATION.md` Stage 11. Under two minutes, six questions, no setup.

M17 is already deployed and already answering these questions correctly from the real
production database - memory and compiled lessons, goals, the world model, the self model,
the Evolution backlog and the authority boundary were each verified against the live rows.
So this is deliberately NOT six tests. The one thing that verification cannot prove is that
you can reach these subsystems by *speaking*, and that is all this asks.

```powershell
.\scripts\voice\owner-explain.ps1 -M17 -OutFile explain-m17.json
```

It starts the voice shell and prints the six questions. Ask them in order, listen, then
press Enter. Expected last line: `OWNER EXPLAIN: PASS`.

What to listen for, because these are the claims being made on your behalf:

* it says how many lessons it has and that they are still candidates - not that it "knows";
* it says **kayitli bir hedefim yok**. That is correct: no goal exists, and inventing one to
  make this look better is the one thing the milestone forbids;
* it separates what the source says, what is installed, what is running and what the record
  proves - and names how many things it is unsure about;
* it gives a real module count from its own index, not a guess;
* it reports the Acceptance Wording Guard as **golgeye hazir, canlida degil**;
* asked whether it can put that live, it answers **Hayir** and says your approval is
  required. That refusal is the milestone.

Nothing here deploys anything. The guard stays SHADOW_READY.

### 17. Re-verify the completed voice session - **DONE (2026-09-05, run automatically; nothing was needed from you)**

Unblocks: `docs/QUALIFICATION.md` 10.5 and 10.6 (10.1-10.4 and 10.7-10.9 are already
PROVEN_REAL from your session).

Your session worked; the acceptance script did not. One check compared the briefing's
generated Turkish to a fixed sentence prefix, and the briefing had just been made shorter,
so it failed a system that was doing exactly the right thing. That check is now structural:
it reads the briefing's own provenance (which ledger events it cited, which research job
they belong to, whether they resolve and are not seeded, and whether the narrated numbers
equal the research run's own record) and never looks at wording.

This command re-reads the session you already ran - no web shell, no new session, nothing to
say - and writes the evidence:

```powershell
.\scripts\voice\owner-explain.ps1 -VerifyOnly -OutFile explain-1.json
```

It asks for the Owner Credential in a masked prompt. Expected last line:
`OWNER EXPLAIN: PASS`. If it says no completed session was found (sessions expire), run the
short voice test from item 16 instead - it is under two minutes.

This is still the only thing needed from you. The Cognitive Foundations built overnight
(memory of experience, lessons, goals, the world and self models, the Evolution lab) add
new questions you can ask by voice - "Ne öğrendin?", "Kendi üzerinde ne geliştiriyorsun?",
"Canlıya alınmayı bekleyen ne var?" - but none of them need a qualification session of
their own. They ride along with the next ordinary voice session you happen to have.

### 16. Ask it what it did — self explanation by voice — **DONE (2026-09-05, product proven; see item 17)**

Unblocks: `docs/QUALIFICATION.md` Stage 10 (10.1–10.6); then Memory on the roadmap.

Voice is now the primary interface to PagentOS. This action proves it can tell you what it
did, from durable evidence, and that you can steer the telling with your voice. One
command does the preparation and the verification; you do the talking.

What it does, in order:

**Your first run (2026-09-04) proved the product and broke the harness.** The Cloud Core
release went through, the web shell never came up from the script, and the script failed
waiting for a session; when you started the shell by hand, the voice UI answered from the
ledger exactly as designed. The harness is fixed (ADR-0051 addendum 2): it now checks
whether the shell already answers, starts it the same way you do and waits until `/voice`
really responds, and after Enter it waits for YOUR session - new since the run began, from
the web shell, with an `activity.explain` call - rather than comparing timestamps. Saying
"Dur" after the answer has finished is now a quiet no-op instead of an error. This rerun
performs NO Cloud Core release (the ledger is already deployed) and touches nothing on
Windows.

1. **Preflight.** Working-tree release blockers, the Cloud Core's realtime provider, and
   the Cloud Core's ledger policy. The ledger is already deployed, so no release happens
   unless the source changed. Nothing on Windows is touched.
2. **Evidence.** Your real research run (`research-1.json`, verdict PASS) is recorded in
   the ledger as `research.qualified` with the file's SHA-256 as its reference - the
   system never types a number by hand - and the ledger then backfills everything else it
   can from the database (research runs and reports, voice sessions, releases,
   incidents). Nothing is seeded.
**Your UX verdict (2026-09-04) - good and usable, three control defects - is addressed
(ADR-0051 addendum 3):** other people's speech no longer interrupts it (a two-lane policy:
control phrases stop it at once, anything else must be stable, near-field owner speech with
a real transcript before playback is cut), the default answer is two to four sentences with
counts and ids kept for "detay" / "teknik" and everything only on "hepsini oku", the
explanation never narrates its own previous narration, and "detaylandır" / "teknik anlat"
are recorded as normalised intents whichever way the assistant routes them. This rerun
performs ONE Cloud Core release (the Cloud Core changed); nothing on Windows.

3. **The session - under two minutes.** If the web voice shell is already running it is
   used; otherwise the script starts it and waits until http://localhost:3000/voice answers
   (first compile can take a minute). Open it, sign in, connect, then:

   1. `Son yaptıklarını anlat.`
   2. Listen: a concise two-to-four sentence briefing (no ids, no hashes).
   3. While it speaks, let another, distant voice talk in the room: it must keep speaking.
   4. `Dur.` — say it WHILE it is speaking; speech must stop at once.
   5. `Devam et.` — it resumes at the sentence it did not finish.
   6. `Teknik anlat.` — a concise technical briefing: versions, evidence, checks.

   Then disconnect and press Enter in the console. If you connect late, the script keeps
   looking for your session for up to ten minutes; a session that existed before the run
   never counts.
4. **Verification.** Your session's durable activity record (tool calls, intents,
   client events - ids and kinds, never a transcript) is fetched and every step is
   checked: the briefing came from the ledger and cites evidence, detail and technical
   were read, `dur` produced a `spoken` report followed by a barge-in with an aligned
   cursor, `devam et` resumed from that cursor, and the ledger holds the explanation and
   the pause. Expected last line: `OWNER EXPLAIN: PASS`.

```powershell
.\scripts\voice\owner-explain.ps1 -OutFile explain-1.json
```

It asks for the Owner Credential in a masked prompt, as usual. Paste `explain-1.json` and
your verdict in a sentence: was the briefing short enough, did it keep speaking over the
other voice, did "dur" stop it at once, and did "devam et" pick up where it stopped?

### 15. The first real Research run — DuckDuckGo, no deployment — **DONE (2026-09-04)**

Your run at 17:28-17:33 UTC ended `OWNER RESEARCH: PASS`: five findings from five distinct publishers, 28 refused pages recorded by reason, Chrome clean before and after, no agent deployment, one Cloud Core release. QUALIFICATION 9.16-9.19 are PROVEN_REAL and the Research Engine milestone is closed. Kept as a non-blocking backlog item: some accepted findings were broad AI news rather than agent-specific developments (ADR-0050 item 24).

Unblocks: `docs/QUALIFICATION.md` 9.16 and 9.17; then the Activity Ledger + Voice Narration
step of the roadmap.

This is the product, not an infrastructure test. One command runs the real research target on
your machine:

> Son uc gundeki yapay zeka ajanlariyla ilgili en onemli gelismeleri arastir. En onemli 5
> gelismeyi sec, neden onemli olduklarini acikla ve kaynaklarini ver.

What it does, in order:

**Your third attempt (2026-09-04) did not crash at all - which is why it was worse.** It
reached `ready`, fetched and ranked twelve pages, quarantined nothing, and published a report
with a fluent Turkish executive summary and ZERO findings. Your acceptance check caught it; the
pipeline did not. Three things were wrong at once, and all three are fixed.

Nothing had ever asked whether a fetched page was about your question. A ten-day-old IBM
Granite model card and two unrelated arXiv abstracts (one on Catalan's constant, one on
dark-matter halo profiles) were ranked as evidence for "the last three days in AI agents", and
every page's retrieval time was quietly standing in for its publication date. A Cloudflare
interstitial titled "Bir dakika lutfen..." was ranked as if it were an article. And zero
findings still counted as a finished report.

Now every fetched page is judged before it can be cited: is it real content or a block page,
is it about the topic, is it inside the window, and is its publication date actually known. A
page whose date cannot be established is marked uncertain rather than backdated to the moment
we fetched it. Each refusal is recorded with its reason - off topic, outside the window, date
uncertain, interstitial, duplicate coverage, not enough content - and the counts appear in the
report itself, so you can tell a thin answer from a thin web. The report has a floor: at least
three findings, each citing the evidence it rests on, target five. If the model returns fewer,
the pipeline rejects that output, retries once against the same evidence, then builds findings
deterministically from the validated evidence, and if there still are not three defensible
ones it fails as `insufficient_valid_findings` instead of handing you an empty answer. It never
invents a finding to reach five. Research policy version 4 carries this, so this rerun performs
exactly one Cloud Core release and no Windows agent install.

**Your second attempt (2026-09-04) got past the numeric defect and failed on the next one of
the same family**: a synthesis model returned a statement without its `label`, and the pipeline
read that key directly, so `KeyError: 'label'` ended a run that had discovered 238 candidates
and fetched and ranked 12 with nothing quarantined. `label` is the provenance taxonomy every
statement must carry, so it is genuinely required - but it comes from a model, so it is now
validated at the boundary. Every research entity has a named, versioned schema; each field is
required, optional (with a defined default) or derived; a violation names the entity type, the
id, the field, the stage, the producer and the schema version; and a malformed statement,
section or finding is quarantined while the rest of the report survives. To stop this being a
one-missing-key-per-run sequence, a test now scans the whole research package and fails if any
producer-controlled payload is read by raw key: that audit found and fixed the model providers'
HTTP envelopes and this Cloud Core's own stored-report reads before you ever ran into them.

**Your first attempt (2026-09-04, task `f6eb5021`) got all the way to ranking**: the Cloud Core
released, the policy became version 1, DuckDuckGo was the provider, 243 candidates were
discovered and 12 were fetched and ranked. It then failed on a typed-data defect: the synthesis
model answered a finding's numeric `importance` field with a Turkish prose sentence and `int()`
on that sentence ended the job. That is fixed at the contract level, not with a try/except:
every numeric field in the pipeline now declares its type, range and provenance and is
validated before use, text fields refuse numbers, and a candidate or finding that breaks its
contract is quarantined with a named reason while the run continues on the valid remainder. The
run fails only if fewer than three valid items remain. Research policy version 2 carries that
contract, so this rerun performs exactly one Cloud Core release and no Windows agent install.

1. **No deployment when nothing changed.** It compares this checkout's browser-worker release
   with the installed one (source AND the copy inside the installed venv that actually runs).
   They match today (0.4.0, installed by your 15:18 run), so it installs nothing: no staging,
   no swap, no service restart. If they ever differ incompatibly it stops and prints the one
   update command instead of doing it silently.
2. **Cloud Core policy.** It asks the Cloud Core for its research policy version. The deployed
   one is version 3 and this checkout expects 4 (the quality gate and the findings contract),
   so it releases the Cloud Core once (the proven transactional release: build, migrate, recreate the api
   container only, health, rollback on any failure). That is the only deployment in this
   command, and only because the source really changed.
3. **The research.** DuckDuckGo discovery (`requested_provider=duckduckgo`,
   `provider=duckduckgo`, `fallback=false`), one persistent Chrome window for the whole job,
   real source pages opened and extracted rather than search snippets, dedup across repeated
   coverage, publication dates respected, then the report: executive summary first, the
   findings with why each matters, and the sources with title, URL, publisher and the device
   command that fetched each one. It also prints what was refused and why, so a short report
   tells you whether the web was thin or the gate was strict.

Expect a visible Chrome window working for two to four minutes. Leave it alone; it closes
itself and the command checks that zero PagentOS Chrome processes remain.

```powershell
.\scripts\research\owner-research.ps1 -OutFile research-1.json
```

It asks for the Owner Credential in a masked prompt, as usual. Expected last line:
`OWNER RESEARCH: PASS`. Paste the printed report (or `research-1.json`) and your verdict in a
sentence: is the executive summary worth reading, do the items matter, are the sources
traceable, and does anything in the refused list look like it should have been kept?

Google is not attempted: it stays available behind `-SearchProvider google` whenever you want
it, with the verification handoff intact.

### 14. Google search with owner verification handoff — **OPTIONAL / not a Research blocker (2026-09-04)**

Unblocks: `docs/QUALIFICATION.md` 9.15 and, when Google answers, 9.12.

Your 2026-09-04 run proved the hard part: interactive mode entered, Google's interstitial was
detected, the state became `WAITING_FOR_OWNER_VERIFICATION`, the existing PagentOS Chrome
window came to the front, and the same session stayed alive while it was polled, with no new
browser lifecycle. It then failed on the way to the fallback with a dictionary error, because
two layers of the smoke each set the `interstitial` key and PowerShell refuses to merge them.
That is fixed at the root: the interstitial is now evidence in exactly one place (the result's
`verification` block), every payload and evidence object is built from a fixed key list, and a
test forbids the merge pattern and any duplicate key.

**The wait is now 45 seconds, not 600.** You do not have to solve anything. Either complete
Google's page in that window within 45 seconds and the same session resumes and retries the
search once, or let it expire and answer `y` when it asks about falling back. Both outcomes
are a PASS and both are recorded honestly:

- you complete it: `verification.outcome=cleared`, `path=handoff_cleared`, `provider=google`,
  `fallback=False`, Google's organic results;
- you let it expire and choose `y`: `verification.outcome=timeout`, `provider=duckduckgo`,
  `fallback=True`, `fallback_reason=google:verification_timeout`, DuckDuckGo's organic
  results, one attempt only, same session, clean close;
- Google does not ask at all (cookies from your earlier verification are in the profile):
  `path=google_ui`, `verification.outcome` null, `provider=google` - also a PASS.

Answering `N` instead closes the session cleanly and stops; that is not a failure either, just
an unfinished qualification.

The command updates the installed agent first (worker 0.4.0 carries the new evidence schema;
one UAC prompt, the installer proves the live worker before anything else):

```powershell
.\scripts\browser\real-browser-smoke.ps1 -UpdateAgentFirst -Mode search -SearchMode interactive -HandoffTimeoutSec 45 -OutFile browser-search-handoff-2.json
```

Paste the final lines and `browser-search-handoff-2.json` whatever the outcome.

### 12. Google-primary search through the installed worker — **DONE (2026-09-04, provider path PROVEN_REAL; Google success not observed)**

Result: worker 0.3.0, schema 2, `requested_provider=google`, Google attempted in the installed
owner-session worker, `captcha interstitial detected; not answered`, `provider=duckduckgo
fallback=True fallback_reason=google:captcha`, DuckDuckGo organic results. Recorded as
QUALIFICATION 9.14 `PROVEN_REAL`; 9.12 (Google organic results) stays open and is item 14's
question. Do not rerun this mode.

#### (record) what item 12 asked for

Unblocks: `docs/QUALIFICATION.md` 9.12; the larger research run (item 10 step C) waits for it.

Runs on the already-installed 0.3.0 worker; nothing is redeployed (the script refuses to
continue if the live worker were not this checkout's release). One `browser.search` with
`engine=auto`: Google is the primary provider, DuckDuckGo the fallback. The output shows
`requested_provider`, the provider actually used, `fallback` and `fallback_reason`, every
attempt, up to five normalized organic results (rank, title, URL), the `command_id` and
`trace` of every device command, and the session identity (`worker_pid`, `browser_pid`,
`session_uid`) plus the local proof that the worker image lives under the installed tree and
is a child of the installed companion. PASS requires `provider=google`, `fallback=False` and
at least one organic result.

If Google shows its "unusual traffic" or consent page, the worker records
`fallback_reason=google:captcha` (or `google:consent`), takes exactly one DuckDuckGo attempt,
and the run ends `FAIL` with that reason in the JSON. That is the honest result: nothing is
retried in a loop, solved or bypassed. Paste it as it is. (An optional later variant,
`-Handoff`, would instead pause with `WAITING_FOR_OWNER_VERIFICATION` so you can complete
Google's page yourself in the visible window and the same session resumes; not part of this
action.)

```powershell
.\scripts\browser\real-browser-smoke.ps1 -Mode search -OutFile browser-search-1.json
```

Expected last line: `REAL BROWSER SMOKE: PASS`, with a `requested_provider=google provider=google fallback=False` line and result lines `#1 ...`. Paste the final lines and `browser-search-1.json`.

### 9. K66 re-qualification after the instrumentation + noise pass — **this is the current action**

Unblocks: rows 6.16–6.21 with attributable numbers; the revised voice target stays NOT
PROVEN until this rerun meets or materially approaches the latency targets and room noise
no longer causes distracting activations (your verdict).

What changed since your session 2 (ADR-0047/0048): every headline latency is now split
into measured components (capture → local gate → RTP send → provider receipt; detect →
stop command → gain zero for barge-in; response created → first delta → playback for
end-of-turn), the fixed ~210 ms barge-in delay was targeted, the K66 false starts and
false barge-ins were addressed without a global threshold, calibration reports whether it
actually measured, and Turkish text renders correctly in the fetched record. Cedar with
the Arbor profile is the default; `Sözleşme: v2` stays.

1. Start and sign in as before (`.\scripts\voice\start-web-voice.ps1`,
   http://localhost:3000/voice). Leave `Ses = Cedar`, mode `Otomatik`. Let the calibration
   finish (the mic area shows `Ortam`), then open **Tanılama** once and copy its JSON.
2. Run the same 14-scenario noise matrix as item 8 Part B (silent room, fan, typing, mouse,
   desk knock, air conditioner, TV speech, another person, music, street, the assistant on
   speakers interrupted with "dur", you quiet, normal, farther), then the hesitation check
   and a headset "dur" barge-in, plus five or six normal turns so the latency samples have
   n ≥ 5.
3. Pull the record:

   ```powershell
   .\scripts\voice\fetch-benchmark.ps1 -Latest -OutFile voice-session-3.json
   ```

Paste the JSON, the Tanılama JSON, and your verdict on: interruptions (did "dur" stop it
at once?), false activations (which scenarios still triggered it), and whether your quiet
or far speech was ever cut. What happens next, automatically: the breakdown says which
component owns each remaining millisecond, the noise counters go into rows 6.16–6.21, and
the K66 profile is pinned from the measured settings.

### 8. Combined voice-character + noise qualification — **DONE (2026-09-03)**

Result recorded in `docs/VOICE_OWNER_FEEDBACK.md`: persistence PROVEN_REAL; Cedar chosen;
latency targets not met (mic→uplink 391/472, EOT→first audio 674/1059, barge-in 210 ms
fixed), K66 false starts 5 / false barge-ins 4; mojibake in the fetched record (reading
side; fixed). The procedure below stays as the reference.

Unblocks: `docs/QUALIFICATION.md` rows 6.15–6.21, and the rest of Stage 6 as a side effect.

Setup, as in item 7: `.\scripts\voice\start-web-voice.ps1`, open
http://localhost:3000/voice, sign in once. New on the page: **Ses** (Marin / Cedar — the
two candidates for your Arbor target; the profile itself is applied on the server), the
microphone-quality area (`Mikrofon`, `Ortam`, `Gürültü bastırma`, `Ses algılama`, a live
meter), the modes **Otomatik / Sessiz ortam / Gürültülü ortam / Çok gürültülü ortam**
(leave `Otomatik`), **Yeniden ölçümle** (recalibrate) and **Tanılama** (diagnostics: noise
floor, speech probability, input level, clipping, the applied browser constraints, and a
"copy JSON" of the K66's real MediaTrack settings/capabilities — paste that JSON once).

**Part A — voice character (5 minutes).** Same short Turkish exchange with `Ses = Marin`,
then again with `Ses = Cedar` (three or four turns each, including one long answer you
just listen to). Verdict, in your words: which is closer to Arbor, and what is still off
(pace, warmth, cheerfulness, "asistan" cadence, Turkish prosody).

*Server version note (2026-09-03).* Your first Connect failed with `HTTP 422` because the
Cloud Core still ran the previous release (contract v1, no `voice` field). The page now
detects the server's contract version and would have said so; and the Cloud Core has since
been released to the current contract (c6f27c5, 2026-09-03): the release qualification
proved contract v2 served, the key inside the container, the provider listed, and `marin`
and `cedar` each minted from Hetzner and echoed unchanged by OpenAI. The page should now
show `Sözleşme: v2` with the `Ses` selector enabled. Part A is on.

**Part B — noise matrix (10–15 minutes), with the winning voice.** Keep the session open
in `Otomatik`; for each scenario do it for ~20 seconds and only speak when the scenario
says so. Watch `Ses algılama`: it should say background, not owner speech, unless you talk.

1. quiet room, silent; 2. computer fan only; 3. typing on your keyboard; 4. mouse clicks;
5. a desk knock; 6. air conditioner / fan; 7. TV speech in the background; 8. another
person speaking nearby; 9. music; 10. street / traffic sound (window open, or a
recording); 11. the assistant speaking through the **speakers** (headset off) — interrupt
it with "dur" while it talks; 12. you speaking quietly; 13. you speaking normally;
14. you speaking from farther away.

Then the hesitation check once more under the final configuration ("Şey... yani... hani
şu..." with a real one-second pause), and a normal "dur" barge-in with the headset on.

**Part C — the numbers.** The page counts false speech starts, false barge-ins, false
turns and calibration results per session and reports them to the Cloud Core, where the
record is durable (it survives disconnect and the provider's secret expiring). Pull the
newest session without typing any id:

```powershell
.\scripts\voice\fetch-benchmark.ps1 -Latest -OutFile voice-session-2.json
```

(For a specific session use the page's **Session ID kopyala** button and pass
`-SessionId <pasted UUID>`; a wrong id lists the recent sessions instead of failing
silently. Your 2026-09-02 session `2b3517ed-…-47898ea23334` is still there.)

Paste the JSON, the diagnostics JSON, and your verdicts (Part A; for Part B which
scenarios still triggered it, and whether your own quiet/far speech was ever cut).
What happens next, automatically: rows 6.15–6.21 are filled from the counts and your
words, the winning voice becomes the configured default, the K66 profile is pinned, and
if background human speech (7–8) still commands the system the owner-directed strategy
(ADR-0043 §4) is the next work item — with honest numbers, not a claim.

### 7. Real Turkish microphone / WebRTC session — **DONE (2026-09-02)**

Completed: first real session on the K66 through the real path. Owner verdict recorded in
`docs/VOICE_OWNER_FEEDBACK.md` (generally good; Arbor target; noise defect). The
procedure below stays as the reference for the plain conversation script.

Unblocks: every `docs/QUALIFICATION.md` Stage 6 row (6.1–6.14). Nothing else can.

What you need: this PC on the tailnet (as for the cloud qualification), a headset or the
laptop microphone, the cloud Owner Credential (typed once into the page; exchanged for a
session and dropped), and a quiet ~10 minutes.

1. Start the web shell against the real Cloud Core (same-origin proxy over Tailscale; no
   change on the host, the running api container is not touched):

   ```powershell
   .\scripts\voice\start-web-voice.ps1
   ```

2. Open http://localhost:3000/voice, sign in once, allow the microphone, press start. The
   page shows the session id (first 8 characters), the provider (`openai-realtime`) and
   the transport. Then, in Turkish, at your normal pace:

   - a free exchange, two or three turns ("Bugün ne yapmamı önerirsin?" and follow-ups);
   - **barge-in**: while it is speaking, cut in with "dur" — playback must stop at once;
     then "devam";
   - **hesitation**: "Şey... yani... hani şu, geçen hafta konuştuğumuz..." with a real
     pause of about a second before you finish the sentence — it must NOT answer early;
   - **mixed terms**: "OpenAI Realtime API ile WebRTC bağlantısı kuruldu mu?";
   - **a long-running tool**: "Son üç gündeki yapay zekâ gelişmelerini araştır" — it should
     say a short natural preamble and keep the session alive; then redirect it mid-task:
     "Sadece OpenAI kısmına bak";
   - **Turkish letters**: "Işık, İstanbul, ğ, ş, ç, ö, ü — bunları tekrar et".

   Note anything that felt wrong (a cut-off, a late stop, a mispronunciation, an English
   accent): that is the subjective row, and only you can fill it.

3. Pull the numbers for the record (the credential is typed into a masked prompt; the
   fetch mints one session and revokes it; the output is ids and timings only):

   ```powershell
   .\scripts\voice\fetch-benchmark.ps1 -SessionId <the full session id from the page> -OutFile voice-session-1.json
   ```

   Paste the printed JSON (or the file's content) to the agent together with your notes.
   What happens next, automatically: the five metrics are recorded against the Stage 6
   rows with their targets, every defect you noticed becomes a tracked fix, and the next
   session is scheduled only when something changed.

### 6. Realtime voice provider credential (OpenAI Realtime) — **DONE (2026-09-02)**

Completed and PROVEN_REAL: key stored (DPAPI), local probe minted every M12 session layer
with `gpt-realtime-2.1`, the M12 Cloud Core was released (ADR-0042) and the key is live
inside the running api container with the provider selected and a real client-secret mint
from Hetzner. The three commands below stay as the reference for the NEXT provider key.

Unblocks: the first real speech-to-speech session (M12), and with it the real-microphone
Turkish voice qualification. Until this key exists the realtime surface is built, gated and
tested against a simulator only; nothing in `docs/QUALIFICATION.md` Stage 6 can move.

What you need: an OpenAI API key with access to the Realtime API (`gpt-realtime`). Create it
in your OpenAI account; the agent never sees it.

Three commands from the repository root, in an ordinary (non-elevated) PowerShell:

1. Store it locally. The prompt is masked; the value is DPAPI-encrypted to your Windows
   account and readable by nothing else:

   ```powershell
   .\scripts\secret-store.ps1 -Set PAGENTOS_VOICE_OPENAI_API_KEY
   ```

2. Prove it works — **DONE (2026-09-02)**. The probe minted the minimal contract and
   then every M12 layer one at a time with `gpt-realtime-2.1`; the only refusal (dotted
   tool names) was fixed the same day. Re-run any time; exit 0 = every layer accepted,
   1 = the minimal contract refused (vendor error printed), 4 = a layer refused (named),
   3 = key not found:

   ```powershell
   .\scripts\secret-store.ps1 -Run "uv run python scripts/realtime_smoke.py --mode probe" -WorkingDirectory services\api
   ```

3. Put it live on the Cloud Core — **this is the current action**. The key is already in
   `/opt/pagentos/.env` on the host (shipped on 2026-09-02, stdin only). What was missing is
   the M12 release itself: the host still ran the 1 Sep tree and image, whose compose had no
   wiring for the variable, so a restart changed nothing (ADR-0042). One command releases
   HEAD, recreates only the api workload, and proves the key inside the running container,
   the provider in `/v1/system/health`, and one real client-secret mint from the host:

   ```powershell
   .\scripts\cloud\release-cloud-core.ps1
   ```

   Add `-Preflight` first to validate without changing anything. For the NEXT secret (any
   provider), the command is `.\scripts\cloud\set-cloud-secret.ps1 -Name <NAME>`, which now
   fails unless the recreated workload actually carries the variable.

Then tell the agent it is done (a word is enough). What happens next, automatically: the
cloud health check is re-verified, and the next action prepared is the real-microphone
session on this PC (headset, Turkish, the M12 evaluation sets), which is the only way
Stage 6 rows become PROVEN_REAL.

Speaker-verification samples (VoiceIdentity) come later and will be their own step.

### 7. A phone

Unblocks: the two mobile criteria that no server-side test can prove — a push arriving on a
locked phone, and microphone capture surviving OS backgrounding.

### 8. Enroll the real assets you authorize for security testing

Unblocks: the Authorized Asset Registry doing anything at all. The registry and its API are
built and tested; what is missing is your actual authorization records, which only you can
make.

---

## Blocking CI only (not M18)

- **GitHub Actions cannot start any job** (2026-09-06, run 33997191892): *"The job was not
  started because recent account payments have failed or your spending limit needs to be
  increased."* Every job refused in two seconds; the workflow file is unchanged since the
  last green run (`e5b3346`). Fix under GitHub → Settings → Billing & plans, then
  `gh run rerun 33997191892`. Until then CI is not a gate; the local gates on the same
  commits are green (API 3401 unit tests, web 420, PowerShell 71 scripts, Windows agent).

## Deferred, not blocking

- Moving the Docker disk image off `C:` (that drive is nearly full). Recommended, not urgent.
- PowerShell 7 — the scripts stay 5.1-compatible on purpose.
