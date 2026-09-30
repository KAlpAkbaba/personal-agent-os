# Döngü raporu — pilot-01

Makine: MAIL · başladı 2026-09-30T06:17:35Z · bitti 2026-09-30T06:21:18Z

## Hazır olanlar (sha)

- narrative-collector — Anlatı: dönem + cihaz için deterministik toplayıcı, anlatıcı arayüzü, başarısızlığı atlatmayan denetçi (öneri 2, adım 1) [merged] (8544c885761f7ef9e84aa5a09262f69fe3f7650c)
- execution-target-rule — Bulutta yürütme: execution_target kuralı (cloud / owner_chrome / device), düşüş zinciri ve olayları — saf kural tablosu (öneri 1, PR 1) [merged] (4a07170eb8f5d2f6dc16621914fa2daedc9c6622)
- idea-2026-09-30-anlati-satiri — Öneri: Anlatı — "bu hafta ne oldu, ofiste ne yaptın, ne başarısız oldu" [done] (sha yok)
- idea-2026-09-30-bulutta-yurutme — Öneri: Bulutta yürütme — "bulut" sanal cihazı, execution_target kuralı, ağsız compute.run [done] (sha yok)
- idea-2026-09-30-gorev-dongusu-bulutta — Öneri: Tarayıcı görev döngüsü PR-C'nin bulutta koşan hali [done] (sha yok)
- onay-merkezi — Onay Merkezi: döngü raporu ve onay bekleyenler Kokpit'te; kabukta Onayla/Reddet, sesle "fikri onayla / yayını onayla" [merged] (a2faeaa10e7d539eeb3760c155b97cc0e85de3ff)

## Onay bekleyenler (fikir / yayın)

Yok.

## Sahibin gerçek cihazda deneyecekleri (cümle cümle, hangi makinede)

Yok.

## Geri verilenler ve nedeni

Yok.

## Durdurulanlar

Yok.

## Harcanan bütçe

- 0,74 USD / tavan 15,00 USD
- koşu sayısı: 2; çakışma: 0; geri verilen: 0
- onay-merkezi / worker: 0,48 USD, 124 sn, tamam
- onay-merkezi / inspector: 0,26 USD, 97 sn, tamam

## Açık riskler

Yok.

## Protokol boşlukları

- integrate/pilot-01 üzerinde tam kapı ve main'e birleştirme bu betikte yok; lead yapar, sonra işler 'awaiting_release' olur

## Lead'in kapanışı

- Üç iş `integrate/pilot-01`'de: narrative-collector `8544c885`, execution-target-rule `4a07170e`,
  onay-merkezi `a2faeaa1`. Lead'in bağladıkları: ledger sözlüğü (5 olay tipi + `team` alt sistemi),
  `main.py` router, kabukta "Onay Merkezi →" bağlantısı, şema alanları, döngü raporunda yayın bayrağı.
- Web kapısı: oxlint uyarısız hata yok, tsc temiz, vitest 2012/2012. Python: yeni paketlerin 143 testi +
  bağlama testleri yeşil. Tam kapı `integrate/pilot-01` üzerinde koşuyor (rapor bittiğinde HANDOFF'ta).
- Bu döngünün toplamı: 5,89 USD (araştırmacı 0,82 + işler 5,07); 2 araştırmacı + 7 işçi + 5 denetleyici
  koşusu; 3 geri verme (2 denetleyici, 1 lead), 0 çakışma; iki işçi paralel 4,5 dk.
- Öneri (pilot sonu): gece döngüsü HENÜZ açılmasın - önce bir döngü daha (pilot-02: bulut işçisi,
  entegratörle) elle; paralel işçi 2 yeterli; bütçe tavanı döngü başına 30 USD, koşu başına 5 USD.
- Sahibe soru (pilot-02 öncesi): bulutta koşan bir iş için "sahip yokken görev yok" (ADR-0207 k.3)
  — ADR-0213'teki dört seçenekten hangisi?

