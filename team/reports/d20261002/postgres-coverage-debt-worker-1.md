## Şu an üzerinde çalışılan
`postgres-coverage-debt` (denetleyicinin ikinci iadesi) — alan: iki entegrasyon test dosyası ve ADR metni — makine: sahibin geliştirme PC'si, dev stack PostgreSQL. İş bitti, dal itildi, ağaç temiz.

## Rapor
- **sha:** `314637e2cef84d21c446278087b173a6010b881b` — `team/d20261001/worker-postgres-coverage-debt`, origin ile aynı.
- **Değişen dosyalar:** 3, hepsi alan içinde (`test_routines_alarms_postgres.py`, `test_memory_tables_postgres.py`, `team/plans/postgres-coverage-debt-adr.md`). `main...HEAD` farkı hâlâ 4 dosya. Mandal testine dokunulmadı.

**İade maddeleri**
1. **ADR kural 3 düzeltildi.** Artık 2001'deki bir anın, hafta günü ve saati tutan her kurulu `schedule` rutinini ateşlediğini açıkça yazıyor (denetleyicinin ölçümüyle). Yılın koruduğu tek şeyin gelecekteki bir `at` tetikleyicisi olduğunu; `presence` ve `condition` tetikleyicilerinin de korunmadığını söylüyor. Modül docstring'i de aynı şekilde düzeltildi.
2. **Koruma eklendi.** Dosyadaki her `evaluate_due` ve her alarm `tick` çağrısı `_evaluate_due` / `_tick` sarmalayıcısından geçiyor. Sarmalayıcı önce `_refuse_foreign_armed` ile kurulu rutinleri okuyor; testin yaratmadığı biri varsa satırları adlandırarak `pytest.fail` ediyor. Bulut-çalma ve koşul-tetikleyici testleri dahil 8 çağrı yeri.
3. **Bağlantılar geri veriliyor.** İki dosyadaki 11 `owner_client` kullanımı yerel `_client` üzerinden açılıyor; istemci açıkken bağlanan motorlar çıkışta kapatılıyor (ortak kimlik çalışma zamanı hariç).

**Yeni test, RED→GREEN**
- `test_the_engine_is_never_run_over_an_armed_routine_that_is_not_the_tests_own`: hafta içi 07:30 rutini, sahibi olmayan bir testin gözünden.
- Koruma yokken RED: `Failed: DID NOT RAISE Failed`. Korumayla GREEN.
- Aynı test, sahibi çağırınca rutinin 2001-09-12'de `triggered` olduğunu da gösteriyor.

**Mutasyon kanıtı** (yedekten geri yükleme, sha256 önce/sonra aynı: `3d2a20a5…` ve `8894a745…`)
- Koruma `_evaluate_due`'dan çıkarıldı: RED.
- Koruma `_tick`'ten çıkarıldı: RED.
- Sahiplik koşulu ters çevrildi: RED.
- İki dosyada `engine.dispose()` kapatıldı: testler yeşil kalıyor, bağlantı tepesi 54'ten 125'e çıkıyor (boşta 43). Bunu bir test iddia etmiyor, yalnızca ölçüm.

**Koşular (dev stack PostgreSQL) — PROVEN_AUTOMATED**
- İki yeni dosya: 19 passed, 11 xfailed.
- Tam entegrasyon paketi, tek koşu: **133 passed, 11 xfailed**, 202 s. Tepe 223 bağlantı (1 sn örnekleme; denetleyicinin 291'iyle aynı yöntem değil, karşılaştırmayın).
- Mandal testi: 3 passed. `ruff check .` temiz, `ruff format --check` 3 dosyada temiz.
- Koşu sonrası: 0 rutin, 0 alarm, 0 `pgcov` varlığı, 43 bağlantı.

**Yapmadıklarım / NOT_RUN**
- Tam birim paketi ve `quality-gate.ps1 -Fast` (uygulama kodu değişmedi).
- Tam entegrasyon paketi yalnızca bir kez koşuldu; önceki `too many clients` dalgalanmasının tekrarlamadığı kanıtlanmadı.
- `tick`'in 2001'de yabancı alarmı oynatmadığı yalnızca kod okumayla; ADR'de böyle yazıyor.
- Denetleyicinin olası 11. kusuru (`experience/routes.py:227`) alan dışı, dokunulmadı.

**Açık riskler**
- `_client` iki dosyada kopya, çünkü `conftest.py` alan dışı. Doğru yeri `owner_client`'ın kendisi; paketin geri kalanını da rahatlatır. Lead için kuyruk önerisi, ADR kural 5'te yazılı.
- Koruma ile motor çağrısı tek işlem değil: paket kilidi diğer pytest koşularını dışarıda tutar, aynı veritabanındaki canlı bir API'yi tutmaz.
- Açık bir uygulama nesnesinin 10 saniyelik rutin saati gerçek `now` ile çalışır ve korunmaz; kural (2099 tetikleyicisi dışında kurulu rutin bırakma) sürüyor.
- Geliştiricinin kendi kurulu rutini varsa bu dosyadaki motor testleri artık bilerek kırmızı olur; mesaj ne yapılacağını söylüyor.
