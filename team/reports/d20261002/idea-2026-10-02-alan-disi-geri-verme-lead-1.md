# Split raporu — `idea-2026-10-02-alan-disi-geri-verme` (lead, d20261002)

Split dosyası yazıldı: `team/plans/d20261002-split-idea-2026-10-02-alan-disi-geri-verme.json`. İki kart var; önerinin asıl parçası olan döngüye bağlama bu split'te **yok**, çünkü şu an kartlanamıyor.

## Kartlar

1. **`area-widen-rules`** (bağımlılığı yok, hemen koşabilir)
   - Alan: `scripts/lib/TeamArea.ps1` (yeni), `scripts/tests/team-area.tests.ps1` (yeni), `scripts/quality-gate.ps1`, `team/plans/area-widen-rules-adr.md`.
   - Önerinin (1) ve (2) kuralları saf fonksiyon olarak: `alan_disi: [...]` / `ALAN_ISTEGI: [...]` satırını okur ve genişlet / beklet (`depends_on`) / reddet kararını verir.
   - Korunan yollar tek sabitte, genişletme tavanı iş başına 2, kart geçmişine kim / neden / hangi dosyalar yazılır, ikinci çalıştırma hiçbir şeyi değiştirmez.
   - Dünkü iki durmuş iş fixture olarak testte; üç ayrı mutasyon (korunan yol, çakışma, tavan) kırmızı olmalı.
   - Yeni suite kapıya kendi adımı olarak kaydedilir; `quality-gate.ps1` bu yüzden alanda.

2. **`area-widen-role-lines`** (`depends_on: area-widen-rules`)
   - Alan: `.claude/agents/inspector.md`, `.claude/agents/worker.md`, `scripts/tests/team-area.tests.ps1`, `team/plans/area-widen-role-lines-adr.md`.
   - Denetleyici alan dışı maddeyi hüküm satırının üstüne yapılandırılmış yazar; çalışan kırmızı testten sonra uygulamaya girmeden `ALAN_ISTEGI` ile döner.
   - Test rol dosyalarındaki örnek satırı dosyadan alıp ayrıştırıcıya verir; iki yarı birbirinden kayamaz. Hüküm okuyucusu (`Get-TeamVerdict`) ile aynı raporda çakışmadığı da kanıtlanır.
   - Sözleşme metni iki kartta birebir aynı. Test dosyası paylaşıldığı için bağımlılık zorunlu.

## Neden bu bölme

- Önerinin gösterdiği yer `scripts/team/cycle.ps1:969`. Bu dosya ile `TeamQueue.ps1`, `TeamRun.ps1`, `team-cycle.tests.ps1` ve `fake-claude.ps1` üç onaylı kartın alanında (`cycle-seat-pool`, `researcher-every-cycle`, `model-policy-floor`).
- `Test-TeamSplit`, işteki bir kartla çakışan alanı `depends_on` olsa bile reddediyor ve split'i bütün olarak geri çeviriyor. Bu yüzden kural katmanını yeni dosyalara aldım.
- Rol metinleri yalnızca raporun ne taşıyacağını söylüyor, döngünün ne yapacağını vaat etmiyor.

## Lead'e kalanlar

- **Bağlama kartı `area-widen-cycle-wiring` kesilmedi.** Yukarıdaki üç kart main'e girince kesilmeli. Kapsamı:
  - `cycle.ps1` 959-1001 (çalışan ve denetleyici dalları);
  - "hak sayılmaz" kuralı (`Get-TeamStateAfterInspection`);
  - aynı turda yeniden koşturma;
  - sahte ajanla üç senaryo;
  - `area_history` / `area_widenings` alanlarının kuyruk şemasına girmesi (`queue.schema.json`, şu an `owner-trials-api`'nin alanında).
- **Bağlama gelene kadar davranış değişmiyor.** İş yine ikinci geri vermede durur; lead `alan_disi` satırını okuyup alanı elle genişletir. Bu, önerinin (a) alternatifinin de gerisinde: haktan düşmeme kuralı da bağlamayla gelecek.
- **Ofis etiketi kesilmedi.** "Alanı genişletildi, yeniden koşuyor" metni için `office.py` ve `apps/web/app/core/office` gerekiyor; ikisi de `model-policy-api` / `model-policy-office-ui` alanında. Onlar bitince ayrı kart.
- **`docs/TEAM_PROTOCOL.md` (bölüm 4 ve 10) ve ROADMAP "Approved ideas" satırı** bu koşuda yazılmadı (koşu tek dosya yazabiliyor). İkisi de lead'in, birleştirme adımında; satırdaki iş kimlikleri: `area-widen-rules`, `area-widen-role-lines`, sonra `area-widen-cycle-wiring`.
- **Korunan yolların gerçek dizinleri** (sırlar, LKG, kurtarma kökleri) kartta adıyla yazılı değil. Çalışan bunları depodan bulup ADR'de kaynağıyla yazacak; denetleyici o listeyi ayrıca sınamalı.
- **Protokol boşluğu:** split denetimi, işteki bir kartın arkasına `depends_on` ile sıralanmayı bilmiyor. Bu önerinin (2) kuralı aynı boşluğu döngü tarafında kapatıyor; split tarafı da bağlama kartına eklenmeli.

Hiçbir komut çalıştırılmadı, başka dosya düzenlenmedi, ajan başlatılmadı; kanıt sınıfı yok (yalnız plan).
