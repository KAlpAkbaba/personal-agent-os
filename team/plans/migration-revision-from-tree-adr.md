# ADR (taslak): Postgres testleri göç numarasını ağaçtan okur

Kart: migration-revision-from-tree (öneri team/proposals/2026-10-06-goc-numarasi-entegrasyonda.md, 'Nasıl' 3).

## Karar

`services/api/tests/integration/migration_ids.py` tek yardımcıdır: `revision_named(suffix)`
(adı `_<suffix>.py` ile biten TEK göç dosyasının revision'ı; yoksa ya da birden çoksa
`LookupError`, Türkçe mesaj), `parent_of(revision)` (o göçün tek `down_revision`'ı; merge
göçünde hata) ve `head()` (tek baş; iki baş hata). Dört dosya (test_watch_postgres,
test_misheard_postgres, test_understanding_vocabulary_postgres, test_migrations) beş sabiti
bunlarla değiştirdi; testlerin anlamı aynı. Koruyucu `tests/unit/test_migration_revision_literals.py`
`team/guards.json`'ın 7. kaydıdır (`migration-revision-literals`).

## Neden ScriptDirectory

Okuma alembic'in kendi `ScriptDirectory.from_config`'i ile, testlerin yükselttiği aynı
`alembic.ini` + `script_location` kurulumuyla yapılır. Bir regex kopyası alembic'in kabul
ettiği adla ayrışabilir (çok satırlı `down_revision`, tuple, tip açıklamalı atama); alembic'in
çözdüğü ad ise `command.downgrade`'in kabul edeceği addır. Koruyucunun yardımcı testi bunun
tersini bağımsız kaynaktan sınar: `_watches.py` dosyasının METNİNDEKİ `down_revision` ile
`parent_of(revision_named("watches"))` aynı olmalı (iki taraf aynı koddan gelmesin diye).

## Koruyucunun kapsamı

Yalnız `services/api/tests/integration/**/*.py` (migration_ids.py hariç). Birim testlerindeki
"0040_x" gibi kimlikler sahte zincirlerin fikstürüdür, gerçek göç adlamaz; kapsam dışı.
Dosyalar AST ile okunur (BOM'lu dosya için utf-8-sig): yalnız TAMAMI `0\d{3}_[a-z0-9_]+`
olan dize sabiti isabettir; docstring'deki "migration 0066" ya da `test_migration_0064_...`
gibi bir test ADI isabet değildir.

## Bilinen sınır

Koruyucu yalnız dize sabitini görür: parçalardan kurulan ad (`"0065" + "_misheard"`,
f-string, dosyadan okunan ad) ya da docstring içindeki bir revision onu geçer. Bunlar bugün
ağaçta yok; amaç yanlışlıkla yazılan sabiti yakalamaktır, kasıtlı atlatmayı değil.
