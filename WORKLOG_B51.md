# WORKLOG B51 - closing rows 744-748 (proposal for the coordinator)

Branch `worktree-agent-afa051144b322d2fb`. This file is the proposed text only; `docs/product/*.md`,
`docs/DECISIONS.md`, `docs/evidence/*` and `matrix.generated.ts` were not edited.

## What changed

- `app/voice/intents.py`
  - `resolve_intent` is now a wrapper around the unchanged rule table (`_resolve_intent_rules`).
    If the words as heard reach an intent, that intent is returned and nothing re-reads it
    (row 741 is unchanged). Only an utterance that reaches NOTHING gets repair readings:
    `polite` ("Kamerayı kapatır mısın?" is read as "Kamerayı kapat"), then `ascii_fold` (Turkish
    letters folded on both sides of every `_has`/`_has_exact` comparison, through a ContextVar),
    then both together. A repair is taken only when all its readings that route agree on one
    intent, and never into `mail_*`/`calendar_*` (B45/B46 are deferred).
  - Exception: an ALL-CAPS transcript that has "I" and no "İ" (`caps_fold`) is read folded
    first. Its exact reading is not really what was said: "SABAH RUTININI DURDUR" was a bare
    STOP (misroute).
  - `turkish_casefold` now applies NFC first and drops the combining dot that a non-Turkish
    lowercase leaves on "İ". Both used to split words in two.
  - New `ResolvedIntent.route_repair` field, included in `to_dict` and copied into the turn
    record.
  - Matcher paraphrases: the briefing matcher accepts "verir/versene/verin/verebilir"
    ("Bana sabah özetini verir misin?" was misrouted to SUMMARIZE). Routines accept "söyle".
    Alarms accept "ayarla" next to the alarm noun, but not next to a song word.
  - News: question particles, "lütfen/bana/bir" and "yüklendi/yayınlandı" are no longer read
    as a channel name. "Haberleri açar mısın?" used to refuse with `news_source_not_found`.
  - "Günaydin" and "gunaydın" are added as greeting forms.
  - Bug fix: "Kopya dosyaları çöp kutusuna gönder." was routed to document_delete on the
    CURRENT file. It now goes to B32's dedup.
- `app/voice/intent_router.py`: a repaired route costs 0.1 confidence (`REPAIR_CONFIDENCE_COST`).
- `app/voice/realtime_sessions/service.py`: `route_repair` is copied into `last_utterance`. The
  spoken clarification `say` frame now carries `turn` and `purpose: "clarification"`.
- `app/documents/service.py`: `read/summarize/answer/preview/inspect` take `reference=`.
  `_resolve_document` and `_resolve_target_file` let a fresh `document`/`file` referent outrank
  the per-kind "current" focus. A referenced file that has not been read yet is extracted, or
  for inspect it is looked at by its own headers. It never falls back to an older document.
- `app/voice/realtime_sessions/tools_documents.py`: `_reference(ctx, target)` reads
  `deictic_reference` from the router's turn record and never from model arguments. It is
  passed by document.read / summarize / answer / inspect / preview when the target is "current".
- `tests/voice_corpus/corpus.py`: `_B51_PARAPHRASES` adds 82 paraphrase cases. Each one inherits
  the whole contract of a template case (context, tool, response, extras, forbidden tools,
  side effects, preceding turns), and each is expanded with ASR variants. That is +316 cases:
  2364 -> 2680 (paraphrase 242 -> 324, asr_noise 1584 -> 1818).
- `tests/unit/test_intent_router_b51.py`:
  - ASR variants are now seven named kinds: no_apostrophe, filler, dotted_i, ascii, upper,
    nfd, py_lower.
  - `KNOWN_DOTTED_I_LOSSES` (6 entries) is replaced by `KNOWN_ASR_LOSSES` (5 entries). All 5
    are the English-cased ALL-CAPS form of mail/calendar sentences, which are deferred and
    therefore never repaired. A test enforces that every entry is a deferred family.
  - New tests cover polite readings, false-polite questions, the deferred-family guard,
    readings that disagree, confidence, news words, matcher paraphrases, one paraphrase for
    each of the 18 non-deferred registry intents with no corpus template, and the dedup
    regression.
- `tests/unit/test_intent_intelligence_b51_session.py` (new) runs through `build_harness()`,
  i.e. the real `create_app`.

## Results

- `test_owner_utterance_corpus.py`: **2682 passed** (2680 cases + 2 aggregates), 0 forbidden side
  effects, 13m53s. The delete-matcher fix came after that run; the doc/app/art/b51 subset was
  rerun afterwards: 1019 passed.
- Targeted suites: `test_intent_router_b51`, `test_intent_intelligence_b51_session`,
  `test_intent_misroutes`, `test_route_telemetry`, `test_voice_intents`,
  `test_intent_daily_coverage`, `test_documents_tools`, `test_documents_b32`,
  `test_documents_b34`: **427 passed**.
- `ruff check .`: clean. `ruff format` is clean on the touched files.
- Mutation proofs: 20/20 RED, each restored from a sha256-verified backup copy.
  - M1-M3: reference ignored in the tool, ignored in the service, fallback to the older document.
  - M4-M5: frame loses its turn tag; question spoken without the flag.
  - M6-M7: combining dot kept; no NFC.
  - M8-M10: fold pass off; fold matching off; caps fold off.
  - M11-M17: polite readings off; mail/calendar guard removed; disagreeing readings accepted;
    "yapabilir" rewritten; news particle read as a channel; briefing verb forms removed;
    ayarla next to a song word.
  - M18: repair confidence cost removed.
  - M19: `route_repair` not copied into the turn record.
  - M20: dedup routed to delete.
- Registry coverage: 168 of 191 tool intents have a corpus paraphrase. The other 18
  non-deferred intents each have a router-level paraphrase test. The remaining 5 are mail and
  calendar, which are deferred.

## Found, not fixed (out of scope, for a follow-up)

- "Chrome'u açıp YouTube'a gir." resolves to `media_play`, with media_query "chrome'u açıp gir".
  It is a pre-existing direct route and does not come from a repair. The "-ıp" converb is not
  a mission connector: "aç, sonra" works. This belongs to the B39 mission router and B27 media,
  so it was left for its own item.

## Proposed matrix rows (15 pipes each; alternatives spelled with " / ")

| 744 | Ambiguity clarification | İki alanı adlandıran cümleye hangisi, tek alana ne yapılacağı sorulur; yönlendirilmiş cümle asla sormaz; soru tur kaydına yazılır, sesli söylenmesi ayrı bayrakla (`voice_clarify_aloud_enabled`, kapalı); sesli soru tek bir `say` çerçevesidir, sorduğu turu (`turn`) ve amacını (`purpose: clarification`) taşır, yalnız o turun yanıtında gider, sonraki turda yeniden gönderilmez | Soru sorar | DONE | PA | P2 | 743 | B51 | app/voice/intent_router.py:clarification_for; realtime_sessions/service.py SB_SAY (turn etiketi) | test_intent_router_b51.py (iki alan / tek alan / sessizlik / bayrak) · test_intent_intelligence_b51_session.py (gerçek create_app: belirsiz cümle -> soru -> cevap -> document.summarize tamamlanır; bayraksız soru yalnız kayıtta; yönlendirilen tur asla sormaz) | PROVEN_REAL (gerçek uygulama nesnesi, harness); canlı kontrol READY_FOR_OWNER: bayrak açık bir oturumda 'Belge ve alarm konusunda.' -> soru bir kez duyulur, 'Belgeyi özetle.' -> özet, soru tekrar duyulmaz; oturum etkinliğinde iki niyet sırayla | no | varsayılan: kayıtta, sessiz |

| 745 | Reference resolution | 'bunu / şunu / bu dosya' son 30 dakikadaki odak nesnesine çözülür (tur kaydında `deictic_reference`); belge araçları (document.read / summarize / answer / inspect / preview) 'current' hedefte bunu okur: yeni bulunan dosya, hâlâ geçerli eski BELGE odağının önüne geçer; okunmamış dosya çıkarılır, inspect başlıklarına bakar, eski belgeye asla düşmez; referans yalnız yönlendiricinin kaydından gelir, model argümanından değil | 'bunu/şunu' çözülür | DONE | PA | P2 | 49 | B51 | app/voice/intent_router.py:resolve_deictic_reference; realtime_sessions/tools_documents.py:_reference; app/documents/service.py:_resolve_reference | test_intent_router_b51.py (taze / zaman / bayat / işaret yok) · test_intent_intelligence_b51_session.py (arama sonrası 'Bunu özetle.' bulunan dosyayı özetler, tek extract; ikinci 'bunu' indeksten; yeni odak yoksa mevcut belge; inspect eski belgeye düşmez) | PROVEN_REAL (gerçek create_app); canlı kontrol: 'notlar dosyasını bul.' sonra 'Bunu özetle.' -> notlar.md özeti | no | 49 ile aynı iş |

| 746 | Turkish paraphrases | Model olmadan: kural tablosu aynen ilk okumadır; hiçbir yere gitmeyen cümle için onarım okumaları (nazik istek 'X-ır mısın' -> emir kipi; harf katlama), okumalar tek niyette birleşmezse alınmaz, mail/takvime asla (ertelendi); onarılan yol tur kaydında `route_repair`, güven -0.1; eşleyiciler 'verir misin / söyle / ayarla' öğrendi; haber kanalı sözcüğü olarak 'mısın / yüklendi' okunmaz; 82 yeni söyleyiş vakası, 191 araç niyetinden 168'i korpusta, kalan 18'i yönlendirici testinde, 5'i ertelenmiş mail/takvim; model yönlendirici (740) bayrağının arkasında | Geniş | DONE | PA | P2 | — | B51 | app/voice/intents.py:resolve_intent / polite_imperative_readings / _repair_reading; app/voice/intent_router.py:REPAIR_CONFIDENCE_COST | test_intent_router_b51.py (nazik istek; sahte nazik soru; ertelenmiş aile; çelişen okuma; güven; haber sözcükleri; eşleyici söyleyişleri; 18 niyet; kopya çöpe regresyonu) · test_owner_utterance_corpus.py (b51.* vakaları) · test_intent_misroutes.py | üretim turu bekliyor (Karar 0) | no | 741 dokunulmadı: yönlenen cümle yeniden okunmaz |

| 747 | ASR-noise variants | Yönlendirme setinin her cümlesi yedi bozulmayla koşulur (kesme işaretsiz / baş dolgu / noktalı i / ASCII / BÜYÜK HARF / NFD / Türkçe olmayan küçük harf): 0 yanlış yönlendirme; kayıp listesi 6 noktalı-i'den 5 girdiye indi, hepsi ertelenmiş mail/takvim cümlelerinin İngilizce büyük harf biçimi (onarım o ailelere bilerek girmez, test bunu zorlar); turkish_casefold NFC uygular ve 'i̇' birleşik noktasını düşürür; I içeren ve İ içermeyen BÜYÜK HARF metin baştan katlanarak okunur | Dayanıklı | DONE | PA | P2 | 746 | B51 | app/voice/intents.py:turkish_casefold / asr_fold / _FOLD_MATCHING / _is_ambiguous_caps | test_intent_router_b51.py (asla yanlış yönlenmez; aslı nereye gidiyorsa oraya, KNOWN_ASR_LOSSES; altı eski kayıp yeniden yönlenir; birleşik nokta; NFD; BÜYÜK HARF rutin) | — | no | kalan 5 kayıp = B45/B46 ertelemesi |

| 748 | Routing corpus expansion | Varyantlar her ROUTING_SET vakasından otomatik türetilir (yedi tür); sahip korpusu 2364 -> 2680 vaka (söyleyiş 242 -> 324, asr_noise 1584 -> 1818); her B51 söyleyişi bir şablon vakanın tüm sözleşmesini (bağlam, araç, yanıt, yasak araçlar, yan etki, önceki turlar) devralır ve ASR varyantlarıyla genişler | Sürekli büyür | DONE | PA | P2 | 739 | B51 | tests/voice_corpus/corpus.py:_B51_PARAPHRASES; tests/voice_corpus/routing.py; tests/unit/test_intent_router_b51.py | test_owner_utterance_corpus.py (2682 geçti, 0 yasak yan etki) · test_intent_router_b51.py | — | no | — |

## Proposed ADR-0158 amendment paragraph

**Amendment (2026-09-16): no model is needed to close 744-748.**

- *Repair readings.* `resolve_intent` reads the words as heard first, exactly as before, and
  returns any route they reach. Only an utterance that reaches nothing gets repair readings:
  the polite request read as its imperative, the words with Turkish letters folded on both
  sides of each comparison, or both. A repair is taken only when all its routed readings
  agree on one intent, never into the deferred mail/calendar families, is recorded as
  `route_repair`, and costs 0.1 confidence.
- *ALL-CAPS text.* An ALL-CAPS transcript whose "I" is ambiguous is the one text read folded
  first, because its exact casefold is not what was said.
- *Normalisation.* `turkish_casefold` composes to NFC and drops the combining dot of a
  non-Turkish lowercase "İ".
- *"Bunu" is now read.* The document tools pass the turn record's `deictic_reference` for a
  "current" target. A fresh file or document referent outranks the per-kind focus, and an
  unread one is extracted or inspected, never replaced by an older document.
- *Spoken clarification.* The clarification frame names its turn and purpose, and the
  real-application test proves it is sent once and never again.
- *What remains.* The dotted-i loss list is gone. The remaining five ALL-CAPS mail/calendar
  losses are the owner's deferral of B45/B46, not a gap in the router.
- *Rollback.* Removing the repair loop in `resolve_intent` restores exactly B51's first
  behaviour.
