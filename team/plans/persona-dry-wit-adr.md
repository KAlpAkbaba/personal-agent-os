# ADR (numara lead'in): Kişilik, kuru mizah — tek kural, tek anahtar, gerçek konuşmanın altında

Durum: Kabul (persona-dry-wit, döngü d20261006). ROADMAP JARVIS satırı "Personality, dry wit:
PARTIAL - tune, never at the cost of truthful speech (ADR-0063)".

## Bağlam
Ne ücretli gerçek zamanlı persona (`persona.py`) ne de yerel kipin sohbet istemi
(`assistant_chat.SYSTEM_PROMPT_TR`) mizah hakkında tek satır içeriyordu. Kuralsız bir model ya
hiç nükte yapmaz ya da yanlış yerde yapar: başarısız bir işin ya da bir para sorusunun üstüne
yapılan şaka, hiç kişilik olmamasından kötüdür.

## Karar
1. **Tek kaynak:** `app/voice/wit.py` içinde `WIT_TR` (kural metni), `WIT_FORBIDDEN_ZONES_TR`
   (yasak yerler) ve `WIT_REQUIRED_PHRASES` (sözleşme testinin aradığı kapalı liste). Persona ve
   yerel sohbet AYNI nesneyi taşır (`is` ile test edilir); modül okurlarının hiçbirini içe
   aktarmaz (yapısal test) — iki ayrı metin zamanla ayrışırdı.
2. **Kural:** kuru, alçak sesli, ender; bir yanıtta en çok bir kısa cümle, çoğu yanıtta hiç;
   sahibi küçümseyen ya da şakayı onun üstüne kuran hiçbir şey; belirsizlikte nükte değil tek kısa
   soru. **Yasak yerler:** hata/başarısızlık, güvenlik/kimlik/yetki, para/harcama, sağlık,
   alarm/uyandırma, acil ya da önemli bildirim, brifing, anlatım, araştırma sonucu, sahibin
   "ciddi ol"/"espri yapma" dediği tur ve bir aracın döndürdüğü `speech` metni (aynen okunur,
   önüne arkasına nükte eklenmez). Gerekçe: ADR-0063 gerçek konuşma — nükte bir olguyu
   değiştiremez, süsleyemez, yapılmamış iş için "yaptım" diyemez (`FAKE_COMPLETION_PHRASES`
   metinde geçmez, test edilir); constitution "notify briefly" — sonuç bildirimi kısa kalır.
3. **Anahtar:** `VoicePreferences.humor` = `'dry'` | `'off'`, varsayılan `'dry'` (sahibin JARVIS
   hedefi mizahı istiyor). Bilinmeyen değer `'dry'`ye düşer (kurucu, `validate`, `from_row`);
   sahibin yazdığı değer `owner_set`e girer ve çıkarımla ezilmez; eski satırlar `'dry'` okunur.
4. **Sıra:** persona'da stil + tercih cümlelerinden sonra, telaffuz ve bellek bloklarından önce
   (bellek hâlâ EN SON). Yerel sohbette `SYSTEM_PROMPT_TR` → `WIT_TR` → sahip hakkında bilinenler;
   soru metnine hiçbir koşulda girmez.
5. **Yerel kipin anahtarı okuması:** `assistant_chat.owner_humor(db)` sahip profilini YALNIZ
   OKUR (`load_preferences` satır yaratıp commit eder; tur işlemi içindeki bir araç bunu yapmamalı),
   hiç yükseltmez, okuyamazsa `'dry'`. `tools_assistant.py`'de tek satır: `humor` yalnız `'off'`
   iken geçirilir — `'dry'` her sağlayıcının varsayılanı, böylece `humor` parametresini henüz
   almayan sahte sağlayıcılar (ör. `test_assistant_chat.py`) kırılmaz. `owner_humor` okuma
   hatasında `db.rollback()` yapar (Postgres'te başarısız SELECT işlemi iptal bırakır); rollback
   hatası yutulur.
6. **Tercih API'si:** `app/voice/routes.py` `PreferencesUpdate` (`extra="forbid"`) `humor`
   alanını taşır (`str | None`, desen `^(dry|off)$`); `PATCH /voice/preferences {"humor":"off"}`
   sahibin anahtarı kapatma yoludur, bilinmeyen değer 422. Rota başka değişiklik istemedi
   (`updates` sözlüğü `update_preferences`'a gider).
7. **Belge:** `docs/VOICE_SPEC.md` §12 varsayılan tercih listesinde `humor: dry` ve `off` anahtarı.
8. **Eski test:** `test_assistant_chat.py` istek testi `system == SYSTEM_PROMPT_TR` yerine
   `startswith(SYSTEM_PROMPT_TR)` bekler — varsayılan açık nükte system'e WIT_TR ekler, bilerek.

## Sonraki kart
- Sesle "espri yapma / mizahı kapat / mizahı aç" niyeti (`intents.py`, hub; money-ledger kartında
  dokunuluyor) — bu kartta YOK.

## Ölçülemeyen (dürüstçe)
Bir dil modelinin nükteyi gerçekten yerinde yapıp yapmadığı yalnız sahibin dinlemesiyle bilinir:
READY_FOR_OWNER (ev PC, ücretli realtime ve yerel kip ayrı ayrı): "Günaydın, bugün nasılsın?" →
en çok bir kısa kuru nükte, sonra iş; "hesabımda ne kadar var" / "alarmı kapat" / "ne başarısız
oldu" → nükte yok, araç metni aynen; "espri yapma" → o tur nükte yok. Son kapı sahibin "JARVIS
gibi mi" yargısıdır (ADR-0034 §6). Testler yalnız metnin varlığını, tek kaynağı, sırayı ve
anahtarı kanıtlar. Yeni bağımlılık yok; model adı metinde yok; paralı çağrı yok.
