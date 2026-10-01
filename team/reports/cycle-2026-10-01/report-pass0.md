# Döngü raporu — cycle-2026-10-01

Makine: MAIL · başladı 2026-09-30T21:02:33Z · bitti 2026-10-01T00:16:17Z

## Hazır olanlar (sha)

- understanding-normalize — ADR-0224 katman 1: Türkçe ek düşürme (kapalı tablo) + STT karışıklık sözlüğü (protokol dosyası) [merged] (af22cac6a2d66fc7cb68781f9f0a72497f648514)
- narrative-collector — Anlatı: dönem + cihaz için deterministik toplayıcı, anlatıcı arayüzü, başarısızlığı atlatmayan denetçi (öneri 2, adım 1) [released] (8544c885761f7ef9e84aa5a09262f69fe3f7650c)
- execution-target-rule — Bulutta yürütme: execution_target kuralı (cloud / owner_chrome / device), düşüş zinciri ve olayları — saf kural tablosu (öneri 1, PR 1) [released] (4a07170eb8f5d2f6dc16621914fa2daedc9c6622)
- idea-2026-09-30-anlati-satiri — Öneri: Anlatı — "bu hafta ne oldu, ofiste ne yaptın, ne başarısız oldu" [done] (sha yok)
- idea-2026-09-30-bulutta-yurutme — Öneri: Bulutta yürütme — "bulut" sanal cihazı, execution_target kuralı, ağsız compute.run [done] (sha yok)
- idea-2026-09-30-gorev-dongusu-bulutta — Öneri: Tarayıcı görev döngüsü PR-C'nin bulutta koşan hali [done] (sha yok)
- onay-merkezi — Onay Merkezi: döngü raporu ve onay bekleyenler Kokpit'te; kabukta Onayla/Reddet, sesle "fikri onayla / yayını onayla" [released] (a2faeaa10e7d539eeb3760c155b97cc0e85de3ff)
- cloud-allow-list — Bulut izin listesi: packages/protocol/browser-cloud-allowlist.json (başlangıçta boş), iki tarafın okuduğu kural, Onay Merkezi'nden ekleme (ADR-0213 eki, seçenek 4) [released] (32a7e9856fb0d72cf120ec00f2e7488b05a37fdc)
- cloud-browser-worker — Bulut tarayıcı işçisi: Cloud Core'da headless Chromium işçisi, sanal cihaz olarak kayıt (device_kind=cloud, alias bulut), bellek tavanı, CPX32'de docker stats ölçümü (ADR-0213 PR 2) [released] (539841a9244ba80ba6020a7bd2e77c2f4924d5df)
- execution-target-wiring — Kural tablosunun bağlanması: araştırma, tarayıcı görevi ve zamanlanmış işler execution_target'tan geçer; düşüş olayları ledger'a; no_capable_device bir düşüş olayı olur (ADR-0213 PR 1b) [released] (c67a0cfaf74b2c9e302640f10059278932be150b)
- narrative-voice — Anlatının ses yolu: 'bu hafta ne oldu / dün ne oldu / ofiste ne yaptın' niyeti, explain sorgu türü, Haiku anlatıcı aynı Protocol arkasında, denetçiden geçmeden konuşulmaz (ADR-0216, adım 2) [released] (04ab501635ee755904de64d02a9c721bda476562)
- team-state-on-cloud-core — Kuyruk, kilit ve Onay Merkezi Cloud Core'a taşınır: team_state tablosu + /v1/team/queue API; cycle.ps1 kuyruğu API'den okur/yazar; ev PC kapalıyken de onay; ofis PC aynı kuyruğu görür [released] (09344f829599da5468014b1066d5fd5ffd1dacdf)
- narrative-intent-wiring — Anlatı niyetinin bağlanması: tek yönlendiricide 'bu hafta ne oldu' → app.narrative.tell; explain'e sorgu türü [merged] (07d8fb1e02b4202516d388d96b77504eda74ffc1)
- ledger-device-stamp — Ledger yazarları cihazı damgalar: detail_json['device'] (anlatının 'ofiste' süzgeci gerçek veride çalışsın) [merged] (463d8043b6cc180641722d10cb5c073af03a9cf9)
- allowlist-editor — Onay Merkezi'nden izin listesine site ekleme/çıkarma: POST /v1/team/allowlist, ledger olayı, listenin işle birlikte işçiye gitmesi [merged] (d50b632c8385a50876838262a76fe89915355ad1)
- cloud-device-registry — Bulut cihazı ve sahibin Chrome'u kayıtta görünür: platform=cloud cihazın 'bulut' alias'ı, owner_chrome etiketi — execution_target'ın okuduğu gerçekler yazılır [merged] (9cb3f9fa1b237b7a538b9885f5eaada9a14639cc)
- cycle-lead-run — Döngüde lead koşusu: onaylı öneriler (roadmap'e hizmet eden) alanlı iş kartlarına bölünür — gece döngüsü insansız ilerler [merged] (3f11bcf4fd2394fa7246f895ff708e08f8269a19)
- maintenance-reboot-script — Bakım penceresi betiği: ADR-0223 prosedürü (ön kontroller, güncelleme, yeniden başlatma, doğrulama, rapor) tek komut + kapıda test [merged] (f1b5193b6e88b94af9d9fa131d3d95a725923fa4)
- operator-postcondition-uwp — Store/UWP uygulamaları (Hesap Makinesi vb.) açıldığında operatör 'açamadım' demesin [merged] (c02fee81eacdd1fc7918ac03f7669c3e2a466c88)

## Onay bekleyenler (fikir / yayın)

Yok.

## Sahibin gerçek cihazda deneyecekleri (cümle cümle, hangi makinede)

Yok.

## Geri verilenler ve nedeni

Yok.

## Durdurulanlar

- understanding-semantic-index — ADR-0224 katman 2: niyet + varlık vektör dizini (yerel potion embedder), bulanık dize, güven puanı ve birleştirici: alan dışı dosya: services/api/app/voice/understanding/__init__.py
- answer-mode-intent-precision — Dil tercihi cümlesi cevap kipi değişikliğine dönüşmesin; asistanın kendi cevabı hafıza adayı olmasın: alan dışı dosya: services/api/tests/voice_corpus/corpus.py
- app-open-named-device-not-dropped — 'Ofis bilgisayarımdan hesap makinesini aç' kendi cihazda açılmasın: 'açın' kipi + adlandırılmış cihaz bağlanamazsa soru: alan dışı dosya: services/api/app/voice/realtime_sessions/service.py

## Harcanan bütçe

- tahmini 21,67 USD (tavan yok)
- koşu sayısı: 32; çakışma: 0; geri verilen: 9
- understanding-semantic-index / integrator: 0,35 USD, 75 sn, tamam
- understanding-normalize / worker: 1,12 USD, 995 sn, tamam
- understanding-semantic-index / worker: 1,42 USD, 986 sn, tamam
- understanding-normalize / inspector: 0,37 USD, 1270 sn, tamam
- understanding-semantic-index / inspector: 0,52 USD, 1269 sn, tamam
- understanding-normalize / worker: 0,25 USD, 669 sn, tamam
- understanding-semantic-index / worker: 0,44 USD, 669 sn, tamam
- understanding-normalize / inspector: 0,38 USD, 660 sn, tamam
- narrative-intent-wiring / worker: 1,31 USD, 668 sn, tamam
- narrative-intent-wiring / inspector: 0,26 USD, 85 sn, tamam
- ledger-device-stamp / worker: 1,65 USD, 404 sn, tamam
- narrative-intent-wiring / worker: 0,16 USD, 30 sn, tamam
- ledger-device-stamp / inspector: 0,42 USD, 413 sn, tamam
- narrative-intent-wiring / inspector: 0,25 USD, 119 sn, tamam
- allowlist-editor / worker: 0,76 USD, 212 sn, tamam
- allowlist-editor / inspector: 0,26 USD, 167 sn, tamam
- cloud-device-registry / worker: 0,81 USD, 156 sn, tamam
- cloud-device-registry / inspector: 0,23 USD, 163 sn, tamam
- cycle-lead-run / worker: 2,33 USD, 1560 sn, tamam
- cycle-lead-run / inspector: 0,31 USD, 261 sn, tamam
- maintenance-reboot-script / worker: 0,93 USD, 250 sn, tamam
- maintenance-reboot-script / inspector: 0,31 USD, 110 sn, tamam
- answer-mode-intent-precision / worker: 0,78 USD, 825 sn, tamam
- answer-mode-intent-precision / inspector: 0,31 USD, 682 sn, tamam
- operator-postcondition-uwp / worker: 0,43 USD, 672 sn, tamam
- answer-mode-intent-precision / worker: 0,60 USD, 786 sn, tamam
- operator-postcondition-uwp / inspector: 0,27 USD, 786 sn, tamam
- operator-postcondition-uwp / worker: 0,16 USD, 26 sn, tamam
- app-open-named-device-not-dropped / worker: 1,37 USD, 1423 sn, tamam
- operator-postcondition-uwp / inspector: 0,85 USD, 142 sn, tamam
- app-open-named-device-not-dropped / inspector: 1,39 USD, 811 sn, tamam
- app-open-named-device-not-dropped / worker: 0,65 USD, 289 sn, tamam

## Açık riskler

Yok.

## Protokol boşlukları

- bekliyor: understanding-threshold-policy -> understanding-normalize, understanding-semantic-index main'e girince
- bekliyor: understanding-corrections-memory -> understanding-threshold-policy main'e girince
- bekliyor: understanding-stt-corpus -> understanding-threshold-policy main'e girince
- integrate/cycle-2026-10-01 üzerinde tam kapı ve main'e birleştirme bu betikte yok; lead yapar, sonra işler 'awaiting_release' olur

