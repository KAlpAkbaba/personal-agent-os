# Döngü raporu — pilot-02

Makine: MAIL · başladı 2026-09-30T10:14:52Z · bitti 2026-09-30T10:55:42Z

## Hazır olanlar (sha)

- narrative-collector — Anlatı: dönem + cihaz için deterministik toplayıcı, anlatıcı arayüzü, başarısızlığı atlatmayan denetçi (öneri 2, adım 1) [released] (8544c885761f7ef9e84aa5a09262f69fe3f7650c)
- execution-target-rule — Bulutta yürütme: execution_target kuralı (cloud / owner_chrome / device), düşüş zinciri ve olayları — saf kural tablosu (öneri 1, PR 1) [released] (4a07170eb8f5d2f6dc16621914fa2daedc9c6622)
- idea-2026-09-30-anlati-satiri — Öneri: Anlatı — "bu hafta ne oldu, ofiste ne yaptın, ne başarısız oldu" [done] (sha yok)
- idea-2026-09-30-bulutta-yurutme — Öneri: Bulutta yürütme — "bulut" sanal cihazı, execution_target kuralı, ağsız compute.run [done] (sha yok)
- idea-2026-09-30-gorev-dongusu-bulutta — Öneri: Tarayıcı görev döngüsü PR-C'nin bulutta koşan hali [done] (sha yok)
- onay-merkezi — Onay Merkezi: döngü raporu ve onay bekleyenler Kokpit'te; kabukta Onayla/Reddet, sesle "fikri onayla / yayını onayla" [released] (a2faeaa10e7d539eeb3760c155b97cc0e85de3ff)
- cloud-allow-list — Bulut izin listesi: packages/protocol/browser-cloud-allowlist.json (başlangıçta boş), iki tarafın okuduğu kural, Onay Merkezi'nden ekleme (ADR-0213 eki, seçenek 4) [merged] (32a7e9856fb0d72cf120ec00f2e7488b05a37fdc)
- cloud-browser-worker — Bulut tarayıcı işçisi: Cloud Core'da headless Chromium işçisi, sanal cihaz olarak kayıt (device_kind=cloud, alias bulut), bellek tavanı, CPX32'de docker stats ölçümü (ADR-0213 PR 2) [merged] (539841a9244ba80ba6020a7bd2e77c2f4924d5df)
- execution-target-wiring — Kural tablosunun bağlanması: araştırma, tarayıcı görevi ve zamanlanmış işler execution_target'tan geçer; düşüş olayları ledger'a; no_capable_device bir düşüş olayı olur (ADR-0213 PR 1b) [merged] (c67a0cfaf74b2c9e302640f10059278932be150b)
- narrative-voice — Anlatının ses yolu: 'bu hafta ne oldu / dün ne oldu / ofiste ne yaptın' niyeti, explain sorgu türü, Haiku anlatıcı aynı Protocol arkasında, denetçiden geçmeden konuşulmaz (ADR-0216, adım 2) [merged] (04ab501635ee755904de64d02a9c721bda476562)
- team-state-on-cloud-core — Kuyruk, kilit ve Onay Merkezi Cloud Core'a taşınır: team_state tablosu + /v1/team/queue API; cycle.ps1 kuyruğu API'den okur/yazar; ev PC kapalıyken de onay; ofis PC aynı kuyruğu görür [merged] (09344f829599da5468014b1066d5fd5ffd1dacdf)

## Onay bekleyenler (fikir / yayın)

Yok.

## Sahibin gerçek cihazda deneyecekleri (cümle cümle, hangi makinede)

Yok.

## Geri verilenler ve nedeni

Yok.

## Durdurulanlar

Yok.

## Harcanan bütçe

- 10,14 USD / tavan 30,00 USD
- koşu sayısı: 16; çakışma: 0; geri verilen: 4
- cloud-browser-worker / integrator: 0,51 USD, 80 sn, tamam
- cloud-allow-list / worker: 0,39 USD, 107 sn, tamam
- cloud-browser-worker / worker: 1,73 USD, 365 sn, tamam
- cloud-allow-list / worker: 0,28 USD, 80 sn, tamam
- cloud-browser-worker / worker: 0,23 USD, 80 sn, tamam
- cloud-allow-list / inspector: 0,23 USD, 47 sn, tamam
- cloud-browser-worker / inspector: 0,30 USD, 64 sn, tamam
- execution-target-wiring / worker: 0,70 USD, 172 sn, tamam
- narrative-voice / worker: 1,00 USD, 229 sn, tamam
- execution-target-wiring / inspector: 0,27 USD, 55 sn, tamam
- narrative-voice / inspector: 0,30 USD, 63 sn, tamam
- execution-target-wiring / worker: 0,26 USD, 43 sn, tamam
- team-state-on-cloud-core / worker: 3,22 USD, 1172 sn, tamam
- execution-target-wiring / inspector: 0,20 USD, 137 sn, tamam
- team-state-on-cloud-core / worker: 0,24 USD, 147 sn, tamam
- team-state-on-cloud-core / inspector: 0,28 USD, 178 sn, tamam

## Açık riskler

Yok.

## Protokol boşlukları

- integrate/pilot-02 üzerinde tam kapı ve main'e birleştirme bu betikte yok; lead yapar, sonra işler 'awaiting_release' olur

