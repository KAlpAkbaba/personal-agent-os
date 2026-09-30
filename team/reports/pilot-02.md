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

## Lead'in kapanışı

- Beş iş `integrate/pilot-02`'de; ADR-0218 (izin listesi), ADR-0219 (bulut işçisi), ADR-0220 (kuralın
  bağlanması), ADR-0221 (anlatının sesi), ADR-0222 (ekibin durumu veritabanında).
- Lead'in bağladıkları: izin listesi protokol paketinde + sözleşme sicilinde; bulut işçisi üretim
  compose'unda KENDİ profilinde (yayın başlatmaz); Playwright etiketi kilit dosyasına sabit; göç 0063
  alembic'te; ekip deposu ayarla açılır (`PAGENTOS_TEAM_STORE`, varsayılan `file`); THIRD_PARTY kaydı.
- BİLEREK bağlanmayanlar: kural tablosunun çağrı yerleri (kayıtta bulut cihazı / owner_chrome etiketi
  yazan yok; bağlansaydı her araştırma `no_capable_device` olurdu) ve anlatı niyeti (tek yönlendirici).
  İkisi de kuyrukta iş.

### Onay Merkezi'nde birikenler (döngüyü bloklamaz)

- **YAYIN BEKLİYOR:** `integrate/pilot-02` tam kapıdan geçip main'e girince. İçinde göç 0063
  (`team_state`, yalnız ekleme), compose değişikliği (profilli servis + bir env satırı).
- **Sahip adımları — bulut işçisi (ADR-0219), yayından sonra, sunucuda:**
  1. `mkdir -p /mnt/pagentos-data/cloud-browser/{state,data} && chown -R 10001:10001 /mnt/pagentos-data/cloud-browser`
  2. Kayıt belirteci üretip `/mnt/pagentos-data/cloud-browser/state/enroll.token` dosyasına yazmak
     (`scripts/cloud/mint-enrollment-token.sh`; sahip oturumu + loopback ister).
  3. `docker compose -f /opt/pagentos/app/infra/docker/docker-compose.prod.yml --env-file /opt/pagentos/.env --profile cloud-browser up -d --build cloud-browser`
  4. Ölçüm: `MEASURE_WINDOW_S=90 /opt/pagentos/app/infra/docker/cloud-browser/measure-memory.sh pagentos-prod-cloud-browser <kanıt.json>`
  5. Cihaza `bulut` alias'ı (`scripts/core/set-device-aliases.ps1`).
- **Sahip adımları — ekibin durumu Cloud Core'da (ADR-0222):** yayından sonra `.env`'e
  `PAGENTOS_TEAM_STORE=database`, kuyruğun bir kez tohumlanması, sahip oturum belirtecinin bir dosyaya
  yazılması (`cycle.ps1 -QueueUrl … -QueueToken <dosya>`).
- **Gerçek cihaz denemeleri (READY_FOR_OWNER):** Onay Merkezi sayfası (`/core/approvals`); pilot-01'den
  kalan dört cümle (MAIL kurulumundan sonra).

### Bu geceki döngüye (02:00) bırakılan işler

`narrative-intent-wiring`, `ledger-device-stamp`, `allowlist-editor`, `cloud-device-registry`,
`cycle-lead-run` — hepsi `approved`, alanları çakışmıyor. `execution-call-sites` bilerek kuyrukta yok:
bulut cihazı gerçekten var olana kadar bekler.

### Ölçüm (pilot-01 + pilot-02)

| | pilot-01 | pilot-02 |
|---|---|---|
| iş | 3 | 5 |
| koşu | 14 | 16 |
| maliyet | 5,89 USD | 10,14 USD |
| döngü süresi | 26 dk (3 parça) | 41 dk |
| geri verme | 3 | 4 (3'ü alan dışı dosya, 1'i eksik test) |
| çakışma | 0 | 0 |

Geri vermelerin çoğu "alan dışı dosya": işçi haklı olarak bir dosyaya ihtiyaç duyuyor ama kart onu
saymıyor. Kartları yazan lead'in (benim) alanları daha tam yazması gerekiyor; `cycle-lead-run` işi bu
denetimi betiğe alıyor.
