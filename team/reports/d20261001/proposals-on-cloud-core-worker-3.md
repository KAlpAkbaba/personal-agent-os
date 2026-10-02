## Şu an üzerinde çalışılan
- **Görev:** `proposals-on-cloud-core` (dönüş turu, denetleyicinin listesi)
- **Alan:** `services/api/app/team/store.py`, iki test dosyası, `team/plans/proposals-on-cloud-core-adr.md`
- **Makine / dal:** ev PC'si, `team/d20261001/worker-proposals-on-cloud-core`; ağaç temiz, dal itildi

## Rapor
**sha:** `c7e007635004bcfe0588ac451b2877ba608a30be`. Bu turda 4 dosya değişti; dalda `team/nightly/lead`'e karşı 11 dosya var, hepsi alanın içinde. `git merge-tree` `db1ff2d9`'a karşı çakışmasız.

**Denetleyicinin dört maddesi**
1. **U+0000 → 422, hiçbir şey yazılmaz.** Kural depoda (`_check_proposal`), iki depoda da aynı. Yarım surrogate çifti de depoda reddediliyor. Aynı delik `put_report`'ta vardı; o da kapandı.
2. **Windows aygıt adları reddediliyor** (`nul/con/aux/prn/com0-9/lpt0-9` kökleri, ilk noktadan önceki kısım). `proposal_name_problems` içinde, iki depoda; rapor adları için de. Karttaki `com1-9/lpt1-9` yerine `0-9` aldım (üst küme).
3. **`1df514f0` cherry-pick edildi** (`cbf55d38`). Dalda bilinen iki RED kalmadı.
4. **Yapılmadı, lead'e:** `voice/realtime_sessions/tools_team.py` içindeki `list_pending` çağrısına depo geçirilecek; dosya alanın dışında. ADR metninde yazılı.

**RED → GREEN** (PROVEN_AUTOMATED)
- Unit, `test_team_proposals.py`: düzeltmeden önce 31 failed / 103 passed; sonra 132 passed.
- Gerçek PostgreSQL (`pagentos-postgres`), `test_team_proposals_postgres.py`: önce 2 failed / 6 passed (`UntranslatableCharacter`; `DID NOT RAISE Invalid`), sonra 8 passed.
- Son koşu: ekip unit dosyaları, ilgili wiring dosyaları ve ratchet 325 passed; üç ekip entegrasyon dosyası 13 passed.
- `ruff check` ve `ruff format --check` temiz.

**Mutasyon** (`store.py` yedek kopyadan geri alındı, her seferinde sha256 `e0ce9cbc…` eşleşti)

| Mutasyon | Unit | PostgreSQL |
|---|---|---|
| U+0000 denetimi kaldırıldı | 12 RED | 1 RED |
| Öneri adında aygıt denetimi kaldırıldı | 17 RED | 1 RED |
| Rapor metni denetimi kaldırıldı | 4 RED | 1 RED |
| Rapor adında aygıt denetimi kaldırıldı | 4 RED | hayatta (PG testi yok) |
| Surrogate denetimi kaldırıldı | 4 RED | hayatta (PG testi yok) |

Kartın iki asıl mutasyonu (FileStore reddi; depodan okuma yerine dosya okuma) bu turda yeniden koşulmadı: NOT_RUN. O kod değişmedi.

**Yolda olanlar**
- İlk test koşusu asıldı: `com1.md` FileStore'da seri portu açıyor. Süreci öldürdüm. Testler FileStore'a artık yalnız `nul` adlarını veriyor; diğer kökler kuralın kendisinde doğrulanıyor.
- RED koşusu dev veritabanında bir `nul.md` satırı bıraktı. Bir sonraki koşu bu yüzden 1 failed verdi ve satırı kendi sildi; test artık bu anahtarı `finally` içinde temizliyor. Kalan test satırı: 0.
- İlk mutasyon turunda iki mutasyon uygulanmadı (kabuk kaçışı; biri etkisizdi). Betik dosyasından yeniden koşuldu; tablo o sonuçlar.

**Yapılamayan / açık riskler**
- **Yarım surrogate rotadan geçince 500.** Pydantic gövdeyi reddediyor ve hiçbir şey yazılmıyor, ama uygulamanın doğrulama-hatası cevabı girdiyi kodlayamıyor. Her rotada geçerli, hata işleyici alanın dışında. Rota testini bu yüzden çıkardım; kural depoda test ediliyor.
- **`put_task`'ta U+0000:** görev belgesi de JSONB, aynı şekilde reddedilir. Ölçülmedi: NOT_RUN.
- **Tam unit paketi (~5400), `quality-gate.ps1`, mypy:** NOT_RUN.
- **Web sayfası** hâlâ `cycle_running` ile kilitliyor (`approvals-detail-view`).
- **Sahibin ev PC'si kapalıyken fikir metnini okuması:** READY_FOR_OWNER.
