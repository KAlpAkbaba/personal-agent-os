# ADR (numara lead'den): JARVIS önemli bir şey olunca sahibi telefonla arar (Twilio)

Tarih: 2026-10-05 · Görev: jarvis-calls-owner · Sahibin isteği 2026-10-05 (Twilio deneme hesabı açık, ayda ~75 dk)

## Karar

1. **Sağlayıcı arayüzü** `app.telephony.provider.TelephonyProvider` (`place_call`, `call_status`).
   Tek uygulama `TwilioTelephony`: Twilio REST, httpx ile (`POST /2010-04-01/Accounts/{SID}/Calls.json`,
   `To/From/Twiml/Timeout=30`, basic auth; `GET .../Calls/{sid}.json`). SDK yok: iki istek için gerekmiyor.
2. **Tek numara.** `OwnerCaller` yalnız ayarlardaki sahip numarasını arar (E.164'e normalize edilir,
   boşluk/tire/`00` kabul). Başka numara `CallRefused`, Twilio'ya hiç gidilmez, ledger'a `telephony.call_refused`.
3. **Ne söylenir.** Metin bizim TTS'imizle (alarm selamlamasının sağlayıcısı) WAV olur, Cloud Core onu
   256-bit, tek kullanımlık, 10 dakikalık bir url'den sunar (`GET /v1/telephony/audio/{token}`, oturumsuz:
   Twilio'nun oturumu yok, belirteç yetkidir; alarmın `AudioStore`'u). TwiML `<Play>` bunu çeker. Bizim TTS
   yoksa / sahte ton sağlayıcısıysa / hata verirse / `PAGENTOS_TELEPHONY_PUBLIC_BASE_URL` https değilse
   `<Say language="tr-TR" voice="Polly.Filiz">`.
4. **Ne önemli** (`app.telephony.policy`): `security.critical` (kritik), `release.failed` (bugünkü
   `recovery.alert` ve `release.rolled_back` bildirimleri buna eşlenir), `alarm.call_me` ('beni ara' alarmı),
   `spend.unanswered`, `aktivra.important`, ayar sayfasındaki test. Başka her tür: arama yok.
   Sessiz saatler (bildirim merdiveninin 23:00-07:30 İstanbul'u): yalnız kritik ve sahibin kendi istediği
   (alarm, test) geçer. Saatte en çok 3 arama, her neden için (kritik dahil); sayı ledger'dan, yeniden
   başlatma sıfırlamaz. Sessiz saat / sınır yüzünden yapılmayan arama da ledger satırıdır ve kesindir
   (sabah aranmaz; bildirim merdiveni sabah ulaştırır).
5. **Sonrası.** Arama + 5 dk: Twilio durumu okunur. `completed` = açıldı (sesli mesaj da olabilir; deneme
   hesabı ayırt ettirmez). `busy/no-answer/failed/canceled` = bir kez daha aranır; o da açılmazsa
   `telephony.unanswered` bildirimi (kritikse urgent). Üçüncü arama yok. Bekleyen tekrar süreç
   belleğindedir: arada yeniden başlatma o tek tekrarı kaybettirir, ledger yine aramanın yapıldığını söyler.
6. **Ledger** alt sistemi `telephony`: `call_placed` (neden, call SID, deneme, ses türü, maskeli numara),
   `call_ended` (Twilio durumu), `call_skipped`, `call_refused`, `call_failed`. Bildirim kaynaklı kararın
   `source_ref`'i `notification:<id>`: bir bildirim = bir karar, geçişler ve yeniden başlatmalar boyunca.
7. **Gizli bilgiler.** Account SID ve auth token yalnız Cloud Core env dosyasında
   (`PAGENTOS_TELEPHONY_TWILIO_ACCOUNT_SID/_AUTH_TOKEN`, `SecretStr`, compose iletir, `set-cloud-secret.ps1`
   ile kurulur). Hiçbir rota döndürmez; sayfa yalnız "bağlı / bağlı değil", maskeli sahip numarası ve Twilio
   numarasını görür. **Bulunan hata:** httpx her isteğin url'sini INFO'da logluyor ve Twilio url'si Account
   SID taşıyor; `httpx`/`httpcore` loglarına SID'i `***` yapan bir filtre eklendi (test bunu yakaladı).
8. **Numaralar web'den değiştirilmez**: sahip numarası aramanın tek güvenlik sınırı; tarayıcıdan
   değişebilen bir sınır çalınmış bir oturumla değişir. Env dosyasında durur, sayfa gösterir.
9. **Deneme hesabı**: her arama İngilizce "You have a trial account..." uyarısıyla başlar ve tuşa basmayı
   ister; JARVIS ondan sonra konuşur. Hesap yükseltilince kalkar. Sayfa bunu söyler.

## Açık / doğrulanmamış (bu koşuda web erişimi yoktu, entegratör planı yoktu)

- Twilio'nun Türkiye'ye dakika ücreti, Türkiye arayan-kimliği kuralları, `Polly.Filiz` sesinin hesapta
  açık olduğu: NOT_RUN - entegratör/denetleyici doğrulamalı. Lisans: harici kütüphane eklenmedi (httpx mevcut).
- Döngü (`telephony_loop`, 30 sn) kuruldu ama lifespan'de başlatılmadı: başlatmak sağlık koruma
  testlerinin (test_bounded_delivery, test_health_endpoint) kümelerine `telephony_calls` eklemeyi ister.
- Açık ses rotası test_identity_enforcement'ın bilinçli-açık uç listesine eklenmeli.
- PROVEN_REAL: sahibin 'Test araması yap' denemesi - READY_FOR_OWNER.
