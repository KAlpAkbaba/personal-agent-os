# Döngü raporu — pilot-01

Makine: MAIL · başladı 2026-09-30T05:54:49Z · bitti 2026-09-30T06:11:48Z

## Hazır olanlar (sha)

- narrative-collector — Anlatı: dönem + cihaz için deterministik toplayıcı, anlatıcı arayüzü, başarısızlığı atlatmayan denetçi (öneri 2, adım 1) [merged] (8544c885761f7ef9e84aa5a09262f69fe3f7650c)
- execution-target-rule — Bulutta yürütme: execution_target kuralı (cloud / owner_chrome / device), düşüş zinciri ve olayları — saf kural tablosu (öneri 1, PR 1) [merged] (4a07170eb8f5d2f6dc16621914fa2daedc9c6622)
- idea-2026-09-30-anlati-satiri — Öneri: Anlatı — "bu hafta ne oldu, ofiste ne yaptın, ne başarısız oldu" [done] (sha yok)
- idea-2026-09-30-bulutta-yurutme — Öneri: Bulutta yürütme — "bulut" sanal cihazı, execution_target kuralı, ağsız compute.run [done] (sha yok)
- idea-2026-09-30-gorev-dongusu-bulutta — Öneri: Tarayıcı görev döngüsü PR-C'nin bulutta koşan hali [done] (sha yok)

## Onay bekleyenler (fikir / yayın)

Yok.

## Sahibin gerçek cihazda deneyecekleri (cümle cümle, hangi makinede)

Yok.

## Geri verilenler ve nedeni

Yok.

## Durdurulanlar

- onay-merkezi — Onay Merkezi: döngü raporu ve onay bekleyenler Kokpit'te; kabukta Onayla/Reddet, sesle "fikri onayla / yayını onayla": alan dışı dosya: apps/web/app/core/approvals/approvalsApi.ts, apps/web/app/core/approvals/page.tsx

## Harcanan bütçe

- 3,95 USD / tavan 30,00 USD
- koşu sayısı: 8; çakışma: 0; geri verilen: 3
- narrative-collector / worker: 0,95 USD, 270 sn, tamam
- execution-target-rule / worker: 0,60 USD, 252 sn, tamam
- narrative-collector / inspector: 0,26 USD, 63 sn, tamam
- execution-target-rule / inspector: 0,22 USD, 73 sn, tamam
- execution-target-rule / worker: 0,25 USD, 50 sn, tamam
- onay-merkezi / worker: 1,32 USD, 609 sn, tamam
- execution-target-rule / inspector: 0,23 USD, 45 sn, tamam
- onay-merkezi / worker: 0,13 USD, 45 sn, tamam

## Açık riskler

Yok.

## Protokol boşlukları

- integrate/pilot-01 üzerinde tam kapı ve main'e birleştirme bu betikte yok; lead yapar, sonra işler 'awaiting_release' olur

