# stt-corpus-layer2-remeasure: what the measurement found, and the candidate cards

Run: 2026-10-03 (2026-10-03T08:15:49Z), code at `dd3373ae1c9e6b21c17753af1e02783c289b91cd`
(API tree clean; branch `team/d20261003/worker-stt-corpus-layer2-remeasure`, base main `e9f8c2d6` +
lead record `75f04e05`). Machine: the home PC (i7-14700KF, CPU). Engine:
`local-minishlab/potion-multilingual-128M`, provider `local`, semantic, 1427 shipped exemplars,
index built inline in 360.5 ms; the 106 cases with the engine took 114.8 s (the machine was
shared with other worker seats; 51.0 s on a quieter run). The repeat run gave the same verdict
for every case. Earlier runs at `0dd1dc75` (2026-10-02T23:57:34Z) and `05ca6059`
(2026-10-03T01:50:59Z) gave the same numbers case for case.
Evidence: `docs/evidence/stt-corpus-layer2-remeasure.{json,md}`.

## Headline

- **Without the engine: 73 / 106 = 68.9 %. With the engine as production configures it: 73 / 106
  = 68.9 %. Target 95 %: NOT met.** Layer 2 moved no case into or out of "correct".
- Layer 2 made **0** cases worse and **0** better. 18 cases moved between two FAILING verdicts:
  `not_understood -> wrong_reading` (list in the evidence .md, "Every moved case").
- Wrong-device actions: **0** with the engine, over the **11** observable cases (unchanged; the
  other 95 run on the canonical world's single fake device, where a wrong machine cannot be seen).
- "Confident wrong readings" 8 -> 26. **Read this carefully**: the 18 added are NOT layer 2
  reading a sentence wrongly. In every one of them the resolved intent stays `none`, nothing acts
  differently (`acted_on` and `tool_status` are identical to the no-engine run, case for case), and
  the layer-2 TOP CANDIDATE IS THE MEANT INTENT in 17 of the 18 (the exception:
  `stt.derived.n.open.1.fused`, top `news_summarize` 0.61 for `news_open`). What changed is the
  band: `policy.decide` with no rule match returns the semantic top's band ("recorded for the
  calibration, never acted on", `app/voice/understanding/policy.py:238-243`), so the turn record
  now says HIGH/MEDIUM where it said LOW, and the judge (`stt_harness.judge`, band HIGH/MEDIUM and
  intent != meant) calls that a wrong reading.
- Over all 25 failing cases decided by the semantic layer, the top candidate is the meant intent
  in **23** (exceptions: `n.open.1.fused` -> news_summarize, `e.off.1.fused` -> explain 0.48).

Why the number did not move: layer 2 is configured and consulted on every sentence (the embedder
was asked for every case the rules did not match - acceptance test 2), but by ADR-0224 / ADR-0245
it may only RANK; with no rule match the relay never acts on it. So "did the 25 not understood
move" is answered: **no, and they cannot move while the no-rule branch never acts**. An upper bound
of what acting would give (NOT measured, a count from this run only): the 17 moved cases whose top
candidate is the meant intent at HIGH/MEDIUM -> at most 90 / 106 = 84.9 %, still under 95 %, and
only if the judge's band rules and each canonical case's contract also pass - unknown.

Determinism: a second production-engine run in the same process gave the same verdict for all 106
(`repeat_run: RUN, differing_case_ids: []`).

## Failure classes of the production-engine run: 7 exist (fewer than ten) - all 7 are below

Key = (verdict, distortion, layer). Renderings are corpus sentences (all derived; none of the
three real 2026-09-30 renderings fails). "Files" were found by grep; NOT VERIFIED = not read.

### 1. `understanding-polite-imperative-semantic-acts` - 10 cases
Kibar çoğul emir kipindeki cümlelerde ikinci katman doğru niyeti bulduğu halde hiçbir şey yapılmıyor.
- Key: (wrong_reading, polite, semantic). Count 10.
- Cases: d.off.1.polite, e.off.1.polite, doc.search.1.polite, art.create.document.polite,
  app.create.tracker.polite, genesis.request.increment.polite, scene.create.blender.canonical.polite,
  location.default.set.1.polite, n.open.1.polite, nativeapps.create.win.canonical.polite (all `stt.derived.`).
- Examples: "Ekranları kapatın." (top display_off 0.95, HIGH); "Bana bir görev takip uygulaması
  yapın." (app_factory_create 0.91, HIGH); "Haberleri açın." (news_open 0.61, MEDIUM).
- Layer decided: semantic, top = meant in 10/10, no rule -> not acted, resolved `none`.
- Two ways a fix could go (the lead chooses): teach the polite `-In` form of these verbs to the
  repair reading (16 of 29 polite cases already pass that way), or let a semantic-only decision act
  with a read-back (an ADR-0224 policy change). Files: `app/voice/intents.py` (`_repair_reading`
  ~8582, the repair families ~8517 - NOT VERIFIED), `app/voice/understanding/normalize.py` (polite
  forms ~52 - NOT VERIFIED), `app/voice/understanding/policy.py` `decide` (read: 218-262).

### 2. `understanding-fused-words-semantic-acts` - 8 cases
İki kelimenin bitişik yazıldığı cümlelerde ikinci katman niyeti buluyor ama kurallar eşleşmediği için iş yapılmıyor.
- Key: (wrong_reading, fused, semantic). Count 8.
- Cases: macro.start.1.fused, r.tech.1.fused, a.create.1.fused, am.1.fused, op.app.8.fused,
  mc.search.1.fused, n.open.1.fused, nativeapps.create.win.canonical.fused.
- Examples: "Yarın 7:30'da beniuyandır." (alarm_create 0.95, HIGH); "Hesapmakinesini aç."
  (app_open 0.83, MEDIUM); "Haberleriaç." (news_summarize 0.61 - the wrong top, MEDIUM).
- Layer decided: semantic, top = meant in 7/8.
- Files: `app/voice/understanding/normalize.py` (a split of fused tokens before the rules - NOT
  VERIFIED), `app/voice/understanding/stt-confusions.json` (NOT VERIFIED), `policy.py` `decide`.

### 3. `understanding-fused-words-low-band` - 5 cases
Bitişik yazılmış kısa komutlarda ikinci katmanın güveni düşük kalıyor ve tek soru da sorulmuyor.
- Key: (not_understood, fused, semantic). Count 5.
- Cases: r.create.1.fused, d.inbox.1.fused, d.off.1.fused, e.off.1.fused, op.app.1.fused.
- Examples: "Ekranlarıkapat." (display_off 0.56, LOW); "NotDefteri'ni aç." (app_open 0.56, LOW);
  "Gözünükapat." (explain 0.48 - the wrong top, LOW).
- Layer decided: semantic LOW with no question (a LOW semantic decision asks nothing).
- Files: as class 2, plus `app/voice/understanding/exemplars.json` (fused exemplars) and
  `thresholds.json` (NOT VERIFIED).

### 4. `understanding-fused-words-rule-misroute` - 4 cases
Bitişik yazılmış kelimeler kurallarda yanlış bir niyete tam güvenle gidiyor.
- Key: (wrong_reading, fused, rule). Count 4.
- Cases: c.collision.alarm_create.fused, selfdev.fix.canonical.fused,
  scene.create.blender.canonical.fused, creative.redraw.canonical.fused.
- Examples: "Şubug'ı kendin düzelt." -> memory_correct (HIGH); "Blender'da yenisahne aç." ->
  media_play (HIGH); "Buresmi Paint'te yeniden çiz." -> repeat (HIGH). ("Saat yedibuçukta beni
  uyandır." reaches alarm_create but the canonical case's contract fails - the time, NOT VERIFIED.)
- Layer decided: rule, exact, 1.0 - the shape of 2026-09-30 (wrong, with full confidence).
- Files: `app/voice/intents.py` (media_play bare-title route ~3342, `_MEDIA_PLAY_VERB_STEMS`
  ~7339; memory_correct and repeat tables - NOT VERIFIED), `app/voice/intent_router.py`.

### 5. `understanding-invented-suffix-media-play` - 3 cases
Tanıyıcının uydurduğu bir ek, uygulama açma cümlesini müzik/video çalmaya çeviriyor.
- Key: (wrong_reading, invented_suffix, rule). Count 3.
- Cases: op.app.1.invented_suffix, op.app.8.invented_suffix, selfdev.fix.canonical.invented_suffix.
- Examples: "Notü Defteri'ni aç." -> media_play (HIGH); "Hesapü makinesini aç." -> media_play
  (HIGH); "Şu bug'ı kendinü düzelt." -> memory_correct (HIGH).
- Layer decided: rule, 1.0.
- Files: `app/voice/intents.py` (media_play bare title ~3342 - NOT VERIFIED),
  `app/voice/understanding/stt-confusions.json` / `normalize.py` (NOT VERIFIED).

### 6. `understanding-polite-mail-low-band` - 2 cases
Kibar emirle söylenen e-posta komutlarında ikinci katmanın güveni düşük ve kurallar e-postaya onarım yapmıyor.
- Key: (not_understood, polite, semantic). Count 2.
- Cases: d.inbox.1.polite, mc.search.1.polite.
- Examples: "Maillerime bakın." (mail_inbox 0.33, LOW); "Fatura maillerini bulun." (mail_search 0.53, LOW).
- Layer decided: semantic LOW, no question. The repair reading never routes into mail by design
  (`app/voice/intents.py` ~8517 comment - NOT VERIFIED); a fix is mail exemplars or an explicit
  polite mail rule, and must keep that deferral's reason.
- Files: `app/voice/understanding/exemplars.json`, `app/voice/intents.py` (NOT VERIFIED).

### 7. `understanding-polite-evolution-pause` - 1 case
"Duraklatın" kibar emri, kendini geliştirmeyi durdurma yerine açıklama isteği olarak okunuyor.
- Key: (wrong_reading, polite, rule). Count 1. Case: ev.pause.1.polite.
- Example: "Kendi kendini geliştirmeyi duraklatın." -> explain (HIGH, 1.0).
- Files: `app/voice/intents.py` (evolution_pause / explain tables - NOT VERIFIED).

## Not a failure class, but a finding for the lead
- `stt-judge-semantic-only-band`: the judge and the report's `confident_wrong_readings` count a
  semantic-only turn that ACTS NOTHING as a confident wrong reading (18 of the 26). A harness card
  (stt_harness.judge / build_stt_report: a separate count for "semantic band, no rule, not acted")
  would keep that number meaning what ADR-0224 says. Not done here: this card is measurement only
  and the judge is the ratchet's rule.
