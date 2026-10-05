# ADR (numarasız): Olumsuz emir koruyucusu - "silme" silen araca gitmez

Kart: destructive-negation-guard (d20261005). Öneri: team/proposals/2026-10-05-olumsuz-emir-silmesin.md.

## Bağlam

Türkçede olumsuz emir tek hecedir (sil -> silme, unut -> unutma, kaldır -> kaldırma, iptal et ->
iptal etme). `app/voice/intents.py` `_has` önek eşleşmesi olduğundan aynı kusur üç kez çıktı:
B16 hafıza (2026-09-13), B27 araştırma iptali (2026-09-14), watch-voice (2026-10-04, 'Nöbetleri
silme.' -> watch_forget_all). Her seferinde tek bir kural düzeltildi; sınıf açık kaldı.

## Karar

`services/api/tests/unit/test_destructive_negation_guard.py` sınıfı kapatan koruyucudur ve
`team/guards.json`'a 7. kayıt (`destructive-negation`) olarak girer. Üretim kodu değişmez.

1. **Kapalı liste `DESTRUCTIVE_TOOLS`**: niyet -> korpus olumlu cümleleri (kimlik + metin) ve
   o cümlenin araca ulaşması için gereken yönlendirici durumu (`operator_running`,
   `executive_run_state`, `artifact_focused`...). Metin listede de tutulur ki tablo dalın
   niyetlerine bağlı olmasın; test korpusla aynı olduğunu sınar. Bu dalda olmayan niyet
   (watch_remove, watch_forget_all: watch-voice birleşince) listede durur, atlanır.
   **Neden kapalı liste**: açık bir sezgi ("adı cancel içeren her şey") hangi cümlenin hangi
   durumla sınandığını söylemez; liste bekçisi yeni niyeti kırmızı yaparak listeye yazdırır.
2. **Liste bekçisi**: niyet değeri ya da aracı `forget|remove|delete|cancel|clear|unut|sil|kaldir`
   içeren, `DESTRUCTIVE_TOOLS`'ta ve gerekçeli `NOT_DESTRUCTIVE`'te olmayan her niyet kırmızı:
   'yeni silen araç olumsuz emir korumasına yazılmadı: <ad>'. Her `*_forget_all` bir ayrılma hâli
   vakası ister ('Nöbetlerimden birini sil.').
3. **Üretici `negate`** (saf): cümle kapalı emir listesindeki bir fiille BİTİYORSA (sil, unut,
   kaldır, temizle, durdur, bırak, vazgeç, dur, yardımcı et/yap) son fiile son ünlüye göre
   -ma/-mayın/-mayınız ya da -me/-meyin/-meyiniz eklenir; 'iptal et' yardımcısını çekimler
   ('iptal etme'). Başka her cümle () verir.
4. **Sınama**: her üretilen cümle sesin tek yönlendiricisi `resolve_intent`'ten, olumlunun
   durumuyla geçer; sonuç HİÇBİR silen niyet olmamalı (yalnız kendi aracı değil - 'Bunu silme'nin
   başka bir silen araca gitmesi de yanlıştır). Olumlunun hâlâ aracına gittiği ayrıca sınanır;
   gitmeyen olumlu, olumsuzu boşa sınar.
5. **KNOWN_OPEN**: main'de bugün kırmızı olan 45 vaka `xfail(strict=True)` ile, gerekçe ve
   düzeltme kartının adıyla. Düzeltilen vaka XPASS olur, bu kırmızıdır, listeden çıkarılır.
6. `destructive_negation_cases.md` üreticinin çıktısıdır; test birebir eşitliği sınar.

## Sınırlar (kapsam dışı)

- STT yanlış yazımı ('sılme', 'sil me' gibi bozulmalar): üretici yalnız doğru yazımı üretir.
- Soru ve dilek kipi ('silmesem mi?', 'silmeyelim mi?'), geniş zaman olumsuzu ('silmezsin'),
  'sakın sil' gibi sözcüksel olumsuzluk: ilk dilim yalnız emir kipi.
- Emirle bitmeyen olumlu cümleler ('... iptal eder misin?', 'Vazgeç, yapma.'): üretilmez;
  capability_cancel'in olumlusu bu yüzden yok (gerekçesi listede).
- Ünsüz yumuşaması gerektiren fiiller (et -> ed-) emirde olmaz; üretici yalnız ek ekler.

## Ölçüm (main 920cf300 tabanı)

13 niyet bu dalda var (2'si watch-voice'u bekliyor); 93 vaka üretildi/listelendi, bu dalda 78'i
koşar (75 üretilmiş + 3 olay). Sonuç: 77 yeşil test, 45 xfail. watch-voice ucunda (719ff2ad) 12
kırmızı: 'Nöbetleri silme.', 'Nöbetlerimden birini sil.', 'Nöbetlerden fiyatı kaldır.', 'Nöbeti
kaldırma.'... - o dal bu koruyucu yeşil olmadan birleşmemeli.

## Düzeltme kartları (KNOWN_OPEN)

Ortak öneri: `intents.py`'de tek bir `_negative_imperative(tokens)` yardımcısı (emir olumsuzları:
etme/etmeyin/etmeyiniz, silme/silmeyin/silmeyiniz, kaldırma/kaldırmayın/kaldırmayınız, unutma...,
vazgeçme..., temizleme...) ve her silen ailenin başında `if _negative_imperative(tokens): return None`.
Her kart kendi vakalarını KNOWN_OPEN'dan çıkarır; kabul: koruyucu XPASS'sız yeşil, olumlular yeşil,
korpus ve test_intent_misroutes yeşil. `_has`'ın önek davranışı değişmez.

### negation-fix-cancel-verb-stems
Dosya `services/api/app/voice/intents.py`. Satır 1727 `_CANCEL_VERB_STEMS = ("iptal","sil","kaldır",
"kaldir")` `_has` ile üç yerde: 1974 (`_routine_match` -> ROUTINE_CANCEL), 2087 (`_memory_match`
'Hafızandan sil' -> MEMORY_FORGET), 2151 (`_alarm_match` -> ALARM_CANCEL). Cümleler ve beklenen
(hiçbir silen niyet değil; MEMORY_REMEMBER / NONE kabul): 'Sabah rutinini iptal etme/etmeyin/
etmeyiniz.'; 'Bunu hafızandan silme/silmeyin/silmeyiniz.'; 'Alarmı iptal etme/etmeyin/etmeyiniz.',
'Sabah alarmını ...', 'Sabah alarmımı ...', 'Alarmı kaldırma/kaldırmayın/kaldırmayınız.' (18 vaka).
Düzeltme: üç dalda `_has(tokens, *_CANCEL_VERB_STEMS)` öncesine olumsuz emir denetimi.

### negation-fix-research-cancel
Satır 4851 `_RESEARCH_CANCEL_NEGATION_FORMS` 'etmeyiniz', 'vazgeçme/vazgeçmeyin/vazgeçmeyiniz'
(ve 'durdurmayınız', 'bırakmayın') içermiyor; 4878 `_has(tokens, *_DISCARD_STEMS)` önek. Cümleler:
'Araştırmayı iptal etmeyiniz.', 'Araştırmadan vazgeçme/vazgeçmeyin/vazgeçmeyiniz.' -> RESEARCH_CANCEL
değil (4 vaka). B27'nin 'iptal etme' vakası yeşil kalır.

### negation-fix-calendar-cancel
Satır 4714 olumsuz listesi 'etmeyiniz' (ve 'silmeyiniz', 'kaldırmayın', 'kaldırmayınız') içermiyor.
Cümleler: 'Perşembeki toplantıyı iptal etmeyiniz.', 'Yarınki randevuyu iptal etmeyiniz.' ->
CALENDAR_CANCEL değil (2 vaka).

### negation-fix-evolution-cancel
Satır 1629 `_has(tokens, *_EVOLUTION_CANCEL_STEMS)` (iptal, vazgeç) olumsuz denetimsiz. Cümleler:
'Bu geliştirmeyi iptal etme/etmeyin/etmeyiniz.', 'Geliştirmeden vazgeçme/vazgeçmeyin/vazgeçmeyiniz.'
-> EVOLUTION_CANCEL değil (6 vaka). EVOLUTION_HOLD ('canlıya alma') değişmez.

### negation-fix-macro
Satır 2849-2853 `_macro_control_match`: `_has(tokens, *_MACRO_DELETE_VERB_STEMS)` önek ve
`_has_exact(tokens, "iptal")` 'etme'yi görmüyor. Cümleler: 'Hareketi iptal etme/etmeyin/etmeyiniz.'
-> MACRO_RECORD_CANCEL değil; 'Yeni mail sekmesi hareketini silme/silmeyin/silmeyiniz.'
(macro_names=('yeni mail sekmesi',)) -> MACRO_DELETE değil (6 vaka).

### negation-fix-operator-exec-cancel
Satır 2438 `_operator_cancel_match` `_has(tokens, "iptal")`; satır 7140 `_executive_match`
`_has_exact(tokens, "iptal")` - ikisi de olumsuz denetimsiz. Cümleler: operator_running=True ile
'İptal etme/etmeyin/etmeyiniz.' -> OPERATOR_CANCEL değil; executive_run_state='running' ile
'Bunu iptal etme/etmeyin/etmeyiniz.', 'Bu işi iptal etme/etmeyin/etmeyiniz.' -> EXEC_CANCEL değil
(9 vaka).

### watch-voice dalı için (KNOWN_OPEN değil: main'de yok)
watch-voice kartının dönüşü: `_watch_*` kurallarında aynı olumsuz denetimi ve ayrılma hâli
('-den/-lerden/-lerimden' + 'birini/fiyatı') -> WATCH_FORGET_ALL değil, tek nöbet seçimi ya da
netleştirme. Koruyucu o dalda yeşil olmadan birleştirilmez.
