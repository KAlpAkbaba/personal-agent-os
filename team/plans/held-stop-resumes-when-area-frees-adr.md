# ADR taslağı: alan sahibini bekleyen durdurulmuş kart, alan boşalınca kendiliğinden döner

- Kart: held-stop-resumes-when-area-frees (d20261007)
- Durum: kabul (Proje Yöneticisi numaralar)

## Bağlam

2026-10-07 11:40 ölçümü: gerekçesi `(alan çakışması: X; o iş bitince)` ile biten üç kart
(migration-rechain-on-merge, alarm-song-by-voice, test-round-io-report) X işten çıktıktan
sonra da durdurulmuş kaldı. Döngü yalnız kendi yazdığı beklemeleri (gerekçe `Proje Yöneticisi: `
önekiyle başlayan, Import-DutyWaits) geri veriyordu; Danışman'ın ya da başka bir döngünün
kararıyla gerekçeye yazılan bekleme hiç yeniden bakılmıyordu.

## Karar

1. `scripts/team/cycle.ps1` Import-DutyWaits, her doldurmada (Resolve-DutyWaits), gerekçesi
   o sonekle biten HER durdurulmuş kartı bekleme sayar - önekli olsun olmasın. Gerekçenin
   sonekten önceki kısmı, kart dönünce işçinin gördüğü gerekçe olur.
2. Öneksiz beklemede adı geçen her kart kuyrukta olmalı; kuyruğun tanımadığı bir ad
   tahmin edilmez, kart dokunulmadan Proje Yöneticisi nöbetine gider (eski davranış).
   Önekli (döngünün kendi) beklemede eski kural sürer: gitmiş tutucu alanı hemen boşaltır.
3. Hiç bekleme sayılmayanlar: `Danışman'a iletildi: ` önekli kartlar (Danışman'ın), `Sahip
   reddetti: ` önekli kartlar ve gerekçesinde `bekletiliyor - sahibin sırası` geçen kartlar
   (sahibin ertelemesi) - bunları bitirmek döngünün işi değil.
4. Alan boş mu sorusu tek kuralla sorulur: Get-DutyReturnBlock (kart 'returned' olsaydı
   Get-TeamAreaHolders + Test-TeamQueue). Boşsa kart döner ve raporun "Protokol boşlukları"
   listesine tek satır yazılır: `alan boşaldı: <id> geri döndü (<eski tutucular> bitti)`.
5. Alanı artık gerekçedekinden başka bir kart tutuyorsa kart durdurulmuş kalır, gerekçe yeni
   tutucuyu adlandırır (`<gerekçe> (alan çakışması: <yeni>; o iş bitince)`), updated_at
   yenilenir ve bir `bekliyor:` satırı yazılır. Yalnız tutucu kümesi değişince yazılır; aynı
   tutucu hâlâ tutuyorsa kart olduğu gibi kalır (her doldurmada yazma yok).
6. Bekleme olarak okunan kart, beklerken Proje Yöneticisi nöbetine verilmez (Get-DutyCandidates
   zaten $dutyWaits'i atlar).

## Sonuçlar

- Üç ölçülen kart bir sonraki döngüde tutucuları bittiyse kendiliğinden döner.
- Kuyruk kuralını bozan bir dönüş yapılmaz (Test-TeamQueue denemesi aynı).
- Risk: biri gerekçeye elle bu soneki yazıp kuyrukta var olan bir kartı adlandırırsa, o kart
  bitince dönüş otomatik olur - sonekin anlamı zaten budur ("o iş bitince").

## Kanıt

`scripts/tests/team-held-stop-resume.tests.ps1` 5/5; altı mutasyonun her biri en az bir vakayı
KIRMIZI yaptı (yedekten geri yükleme, sha256 aynı); team-cycle `-Filter duty` 43/43 ve
`split|office|watch|wait|area` 66/66 yeşil.
