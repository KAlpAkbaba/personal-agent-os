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

   **Twilio ses bağlantısına nasıl ulaşır (Cloud Core yalnız tailnet'te).** Ulaşamaz, ve bu karar
   onu açmaz. Cloud Core'un tek HTTPS kapısı `tailscale serve`'dir; Funnel (genel internet) ADR'si
   gereği reddedilir (`scripts/cloud/enable-web-tailnet-https.sh` Funnel'ı açık bulursa durur).
   Twilio internettedir, tailnet'e giremez. Bu yüzden:
   - **Bugün varsayılan `<Say language="tr-TR">`'dir.** `PAGENTOS_TELEPHONY_PUBLIC_BASE_URL` boştur;
     JARVIS Twilio'nun Türkçe sesiyle konuşur. Arama yine çalar, yine Türkçe anlatır; yalnız ses
     bizim alarm sesimiz değil Twilio'nunkidir. Sayfa bunu "Twilio'nun Türkçe sesi" diye gösterir.
   - **Tailnet adresi genel adres sayılmaz** (`is_public_https_origin`): `*.ts.net`, 100.64/10,
     özel, loopback, link-local, `localhost`/`.local`/`.internal` verilirse yine `<Say>`. Yanlışlıkla
     tailnet adı yazılsa Twilio'nun "an application error has occurred" demesi yerine JARVIS konuşur
     (test + mutasyon RED).
   - **`<Play>` yolu yalnız sahip internetten erişilebilir bir HTTPS kökü verdiğinde açılır**; o kök
     yalnız `/v1/telephony/audio/{token}`'ı geçirmelidir (belirteç 256 bit, tek kullanım, 10 dk).
     Bu bir genel açıklık kararıdır, sahibin (READY_FOR_OWNER), bu görev vermez.
   - **Önerilen sonraki iş (ayrı kart):** WAV'ı Cloud Core'dan değil, mevcut S3-uyumlu nesne
     deposundan 10 dakikalık imzalı (presigned) GET url'si ile vermek; arama bitince nesne silinir.
     Cloud Core'a hiçbir gelen bağlantı açılmaz, bizim ses Twilio'ya ulaşır.
4. **Ne önemli** (`app.telephony.policy`): `security.critical` (kritik), `release.failed` (bugünkü
   `recovery.alert` ve `release.rolled_back` bildirimleri buna eşlenir), `alarm.call_me` ('beni ara' alarmı),
   `spend.unanswered`, `aktivra.important`, ayar sayfasındaki test. Başka her tür: arama yok.
   Sessiz saatler (bildirim merdiveninin 23:00-07:30 İstanbul'u): yalnız kritik ve sahibin kendi istediği
   (alarm, test) geçer. Saatte en çok 3 arama, her neden için (kritik dahil); sayı ledger'dan, yeniden
   başlatma sıfırlamaz. Sessiz saat / sınır yüzünden yapılmayan arama da ledger satırıdır ve kesindir
   (sabah aranmaz; bildirim merdiveni sabah ulaştırır).
5. **Sonrası.** Arama + 5 dk: Twilio durumu okunur. `completed` = açıldı (sesli mesaj da olabilir; deneme
   hesabı ayırt ettirmez). `busy/no-answer/failed/canceled` = bir kez daha aranır; o da açılmazsa
   `telephony.unanswered` bildirimi (kritikse urgent). Üçüncü arama yok. **Tekrar da bir aramadır**
   (denetleyici iadesi 2026-10-05: 3 cevapsız aramanın tekrarı 5 dakikada 6 arama ediyordu): saatlik
   sınır ve sessiz saat tekrar için de sorulur; durdurulan tekrar `call_skipped` satırıdır
   (`attempt: 2`, `source_ref call:<sid>:retry_skipped`) ve bildirim hemen gider. Sessiz saatte
   kritik ve sahibin istediği tekrar yine çalar, diğerleri (sürüm hatası, harcama, Aktivra) çalmaz. Bekleyen tekrar süreç
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
- Döngü (`telephony_loop`, 30 sn) lifespan'de başlatılır, kapanışta ilk durdurulur; sağlıkta
  `telephony_calls` (danışma) satırı. `test_bounded_delivery.py` haritası ve
  `test_health_endpoint.py` `ALL_CHECKS` aynı committe güncellendi (3. dönüş).
- Açık ses rotası `test_identity_enforcement.py` `EXPECTED_OPEN`'da
  `("GET", "/v1/telephony/audio/{token}")` (alarm ses rotasının ikizi: Twilio'nun çektiği
  tek kullanımlık, 10 dakikalık ses; oturum taşımaz).
- Sondaki noktalı ad (`foo.ts.net.`) kırpılarak `.ts.net` kontrolüne takılır.
- PROVEN_REAL: sahibin 'Test araması yap' denemesi - READY_FOR_OWNER.
