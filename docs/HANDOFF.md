# Devir notu — 2026-09-19 akşamı

Yeni bir Claude oturumu (başka hesap dahil) buradan devam eder. Önce `CLAUDE.md`, sonra bu
dosya, sonra gerektikçe `docs/DECISIONS.md` sonundaki ADR-0170…0178. Sahibe Türkçe yaz.

## Şu anki durum

- **Üretim:** Cloud Core `2336300` (api-blue), last-known-good `85b5432`, recovery bundle
  `2336300`'e pinli, `pagentos-bluegreen-reconcile.timer` aktif, sağlık `ok`.
  Realtime sağlayıcıları: `local-router`, `openai-realtime`.
- **Cihaz (sahibin PC'si, "MAIL"):** ajan `0.6.0+b4f8ff9`, 105 yetenek (`screen.ocr` dahil).
  `f53527e`'deki tek karakter tuş desteği (InputSynthesizer) **henüz kurulu değil**.
- **Çalışma ağacı temiz**, her şey `origin/main`'de. Arka planda iş yok.
- **CI yok:** GitHub Actions kapalı (sahip ödeyemiyor). Kanıt yereldir; sahip 2026-09-19'da
  "her seferinde tüm testleri koşma" dedi → dokunulan paketler + hedefli korpus yeter.

## Nasıl yayınlanır / kurulur

- Bulut: `scripts/cloud/release-cloud-core.ps1 -BlueGreen` (asla `2>&1` ile değil; kirli
  ağaçta `-AllowDirty` yalnız HEAD'i gönderir). Sonra sunucuda
  `bash /opt/pagentos/app/scripts/cloud/install-recovery-supervisor.sh <TAM 40 haneli sha>`
  (kısa sha reddedilir). SSH: `root@100.90.158.26` (Tailscale).
- Cihaz: yalnız sahip, tek UAC —
  `Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\scripts\install-device-service.ps1" -DisplayPower -Operator'`
  (iki anahtar da zorunlu). Önce `devices/windows-agent` Release derle +
  `scripts/qualify-staged-update.ps1 -Quiet`.
- Üretim kayıtlarını okumak: `docker exec -i pagentos-prod-postgres psql -U pagentos -d pagentos_prod`
  (tablolar: `operator_missions`, `device_commands`, `realtime_tool_calls`, `research_runs`,
  `research_evidence`, `devices`).

## Bugün yayınlananlar (sahip henüz CANLI denemedi — işaretli olanlar)

| Commit | Ne | Canlı denendi mi |
|---|---|---|
| 42ce5a9 | Görev heartbeat'i — "YouTube'u aç" yeniden çalışıyor | ✅ |
| 961952f | "Arama kısmına X yaz" = açık sekmenin sitesinde arama | kısmen |
| 8596048 | /core düzen (canlı göstergesi, kırpılmayan kartlar, 4K yazı) | — |
| 7fa04ea | Ücretsiz yerel mod (Chrome Web Speech + yönlendirici + tarayıcı sesi) | ✅ |
| 67fff03, b4f8ff9 | Cihaz: ekran yakalama çerçeveye sığar; `screen.ocr` + JPEG | ✅ |
| 6615ad4, 27e2067 | Video adı yerel OCR ile bulunur, tüm Chrome pencereleri, doğru monitör; yanlış video açılırsa geri döner | ✅ ("Adana Adliyesi 4" açıldı) / geri dönme denenmedi |
| f53527e | "Videoyu durdur/başlat/başa al", "X sekmesine geç", "0 tuşuna bas" | ❌ (tuşlar cihaz kurulumu bekliyor) |
| c968d04 | Araştırma sayfaları sahibin Chrome'unda, OCR ile (ADR-0177) | ❌ başarısız oldu → 2336300 |
| 60060b2 | Araştırma: emekli model kimliği + sağlayıcı yedeği | ❌ |
| 52fd71b | Yerel modda serbest sohbet = Claude Haiku (`claude-haiku-4-5`) | ❌ |
| 85b5432 | Chrome'a yazma OCR ile doğrulanır | ❌ |
| 2336300 | Araştırma: Türkçe kaynaklar önce, OCR paragraf birleştirme (ADR-0178) | ❌ |

## Sahibin kararları (bugün)

- Yerel mod: STT = Chrome Web Speech; komutlar yönlendiricide; serbest sohbet = Claude Haiku.
- Araştırma: "Her şeyi kendi Chrome'umda, gözümün önünde yapsın" (ADR-0177).
- Ekranda bulma: önce yerel OCR (ücretsiz), görsel sağlayıcı yalnız konum/oynatıcı için.
- İkinci sesli onay yok (mail gönderme hariç) — ADR-0171 ek.
- "Birden fazla pencere varsa ikisine de baksın."

## Sıradaki işler

1. Sahibin canlı denemelerini kayıtlardan doğrula (yukarıdaki ❌ satırlar). Özellikle
   araştırma: `research_runs.progress_json` / `events_json` — kaç sayfa, hangi tarayıcı,
   kaç kaynak doğrulandı, `research_reports.synthesis_provider`.
2. Cihaz kurulumunu sahipten iste (tek karakter tuşlar için), sonra "0 tuşuna bas" dene.
3. "Yanlış yere tıkladım" gibi sahibin yorum cümleleri görev sanılıp ekranda aranıyor —
   `app/voice/intents.py` `_mission_start_match` / `mission.py` `_segment_click_text`.
4. Araştırma arama bölgesi (`region: tr`) cihaz tarafında uygulanmıyor (ADR-0178 not).
5. `research_announcer.py` başarısız aşamada rapor yerine `run.error` okuyor (ADR-0178 not).
6. `scripts/verify-device-service.ps1` 6b.4: DateTime taşması (ayrı iş çipi açıldı).
7. Test defteri (claude.ai artefaktı) eski hesaba ait; kaynağı
   `scripts/…` değil, oturumun scratchpad'inde idi — gerekirse `default_registry()`'den
   yeniden üret.

## Bilinen tuzaklar

- `services/api/app/operator/plans.py` CRLF; yama betikleri CRLF-duyarlı olmalı.
- Bash heredoc'larda ters eğik çizgi bozulur → dosya araçlarını kullan.
- Mutasyon kanıtında dosyayı yedekten sha256 ile geri yükle, asla `git checkout --`.
- Tam birim paketi ~25 dk, tam korpus ~12 dk; aynı anda kalite kapısıyla koşma
  (`test_contract_falsification` canlı bir dosyayı geçici siler).
- Alt ajanlar worktree'de izole çalışır; sonuçları yama olarak ana ağaca uygula,
  `docs/DECISIONS.md` çakışırsa ADR metnini sona ekle.
