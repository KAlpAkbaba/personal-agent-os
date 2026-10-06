# ADR (taslak, numarayı PY verir): Konuşma cihazlar arasında devralınır

- Kart: conversation-carryover (döngü d20261006), yol haritası satırı "The same JARVIS in the
  house, the car, the suit, the phone" (sıra 4: çalışan bir konuşmanın cihazlar arası devri).
- Durum: kabul edildi (çalışan 3), PROVEN_AUTOMATED; iki cihazda deneme READY_FOR_OWNER.

## Bağlam

Aynı oturuma başka bir istemcinin bağlanması (spec §7 attach) vardı, ama YENİ bir oturum
(evden ofise geçiş, telefon kabuğu, sabah yeniden konuşma) `transcript_summary=''` ile açılıyor
ve bir dakika önceki konuşmayı bilmiyordu. Yerel kipin (ADR-0173) sohbet geçmişi süreç
belleğinde ve oturum kimliğine bağlıydı. Kapanmış bir oturumda başlatılan araştırma bittiğinde
sonuç yalnız kalıcı yazılıyor, sahip o sırada başka cihazda konuşuyorsa duymuyordu.

## Karar

Tek kural, tek modül: `app/voice/realtime_sessions/carryover.py`.

- `pick_previous`: kendisi DIŞINDAKİ, özeti boş olmayan, son hareketi (`updated_at` /
  `closed_at`) pencere içindeki EN SON oturum. `create_session` özeti (`MAX_SUMMARY_CHARS`
  ile kesilmiş) ve varsa `ctx['plan']`'ı kopyalar, `ctx['carried_from']` = {session_id,
  device (sahibin alias'ı > cihaz adı > istemci türü), device_id, ended_at, at (SS:DD,
  Europe/Istanbul)} yazar. `build_instructions` özet bloğunun önüne tek satır ekler:
  "Bu konuşma az önce '<cihaz>' cihazındaki oturumdan devam ediyor (SS:DD); sahip isterse
  kaldığınız yerden sürdür." `carried_from` `voice_session_created` denetim metadata'sına
  girer (metin değil, yalnız kimlik ve etiket). Yeni ledger türü YOK.
- Yerel kip: `assistant.chat` başarılı her yanıttan sonra son turları (bounded, en yeniler
  korunur) oturum satırının `transcript_summary` alanına yazar; geçmiş boşken ve oturum
  devralınmışsa devralınan özet ilk tur çifti olarak tohumlanır (Messages API'de `messages`
  içinde system rolü yok, ilk tur kullanıcınınki olmalı).
- `pick_live`: başlatan DIŞINDAKİ, canlı (created/active, süresi geçmemiş), pencere içinde
  hareket etmiş en son oturum. Araştırma duyurucusu `forward_when_closed=True` ile çağırır:
  başlatan kapanmışsa aynı `tool_completed` çerçevesi, konuşma metni "Daha önce başlattığın
  araştırma bitti:" satırıyla başlayarak canlı oturuma gider; kalıcı kayıt başlatan çağrıda
  kalır; canlı oturum yoksa yol öncekiyle aynı.
- Kapatma anahtarı: `VoicePreferences.conversation_carryover` (varsayılan açık), mevcut
  `PATCH /v1/voice/preferences` ucu. Kapalıyken devralma, yerel kip yazma/tohumlama ve
  canlı oturuma iletme yapılmaz. Arka plan yollarında tercih YAZMADAN okunur
  (`carryover.enabled`, savepoint içinde), çünkü `load_preferences` eksik profili oluşturup
  commit eder.

## Pencere: 30 dakika (tek sabit `CARRYOVER_WINDOW`)

Evden arabaya ya da ofis masasına geçecek kadar uzun; bu sabahki oturumun dün geceki konuyla
açılmayacağı kadar kısa. Sabah "dün ne konuşmuştuk" ihtiyacı belleğin (B17) ve anlatının
işidir, oturum devri değil.

## KVKK / gizlilik

Yeni veri sınıfı yok: özet metni (`transcript_summary`) ücretli oturumda zaten vardı; yerel kip
aynı alanı aynı sınırla yazar. Ses saklanmaz; özet dışında metin saklanmaz; denetim satırına
metin girmez. Anahtar kapalıyken yerel kip metin yazmaz.

## Sınırlar

`voice/intents.py`, `realtime_sessions/tools.py`, `app/main.py`, `config.py`,
`ledger/vocabulary.py` dokunulmadı (başka kartların alanı): yeni niyet, ayar, ledger türü yok.
Kokpit anahtarı ayrı kart. Web kabuğu değişmedi: talimat sunucudan gelir.

Açık risk: web kabuğu (`controller.ts`) bilmediği `call_id`'li `tool_completed` çerçevesini
"bilinmeyen çağrı" diye günlüğe yazıp seslendirmiyor. Canlı oturuma iletilen araştırma
sonucu sunucuda doğru oturuma gider ama bugünkü web kabuğu onu okumaz; kabuğun bu çerçeveyi
seslendirmesi ayrı bir web kartıdır.

## Sahibin deneme cümlesi (READY_FOR_OWNER)

Evde "yarınki toplantıyı konuşalım" deyip oturumu kapat; 30 dakika içinde ofiste (ya da
telefon kabuğunda) yeni oturum aç ve "nerede kalmıştık?" de - JARVIS özetle sürdürür.
