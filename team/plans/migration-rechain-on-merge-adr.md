# ADR (taslak, numarayı lead verir): göç zinciri entegrasyonda kendiliğinden yeniden kurulur

- Durum: kabul (kart migration-rechain-on-merge, öneri team/proposals/2026-10-06-goc-numarasi-entegrasyonda.md, sahip onayladı)
- Tarih: 2026-10-06

## Bağlam

Paralel iki işçi göçünü aynı uca (ör. 0066_watches) kurar, ikisi de kendine 0067 der. Tek tek doğrudur,
denetçi onaylar; entegrasyon dalında yan yana gelince zincirin iki ucu olur, iş "entegrasyon dalında
çakışma" ile durur ve Danışman elle yeniden numaralar (ADR-0224 eki). 5 Ekim'de dört kart + onları
bekleyen iki kart bu yüzden durdu.

## Karar

`Merge-TeamBranch` başarılı bir `--no-ff` merge'den sonra dalın EKLEDİĞİ `alembic/versions/*.py`
dosyalarını `scripts/lib/TeamMigrationChain.ps1`'in saf kuralıyla entegrasyon dalının tek ucuna
yeniden numaralar: dosya adındaki ve revision'daki NNNN öneki, down_revision (ilk yeni göçte uç,
sonrakilerde bir önceki yeni göç), dalın değiştirdiği `services/api/tests/**` dosyalarındaki aynı
belirteçler (eski revision, ilk yeni göçün eski ebeveyni, eski dosya gövdesi; tam belirteç, tek geçiş).
Sonra `uv run pytest tests/unit/test_migration_model_agreement.py tests/unit/test_migration_compatibility.py -q`
koşulur. Kırmızıysa ya da kural "stop" derse merge geri alınır (`git reset --hard` merge öncesi HEAD),
görev bugünkü yoldan döner ama nedeni yazılı: `entegrasyon dalında çakışma: göç zinciri: <neden>`.

Durdurma nedenleri: dallı göç (tuple down_revision, branch_labels), 32 karakteri aşan revision id,
entegrasyon dalı zaten iki uçlu, dalın göçleri tek zincir değil, base'de var olan bir dosya,
aynı tablo (yeni göç ile entegrasyon dalında olup main'de OLMAYAN bir göç aynı tabloya dokunuyor -
`op.create_table/drop_table/add_column/drop_column/alter_column/create_index/batch_alter_table`'ın
ilk dize argümanı). Aynı tablo denetimi yalnız yeniden numaralama gerektiğinde yapılır: zaten uçta
duran bir göçü yazan işçi alttaki göçleri görmüştür.

## Neden amend

Yeniden yazım merge commit'in İÇİNE alınır (`git commit --amend`, mesaja
` (migration rechained: <eski> -> <yeni> on <uç>)`). Entegrasyon dalının HEAD'i tek bir merge commit
kalır, ikinci ebeveyni iş dalının ucudur: `Undo-TeamMerge` (HEAD^2 = dal ucu) ve "already merged"
(`merge-base --is-ancestor`) değişmeden çalışır. Ayrı bir commit HEAD^2'yi kaybettirir ve geri almayı
bozar (mutasyon b bunu kırmızı gösterir). İş dalının kendisi (`refs/heads/<dal>`) asla yazılmaz.

## Postgres testi kapıya bırakıldı

Bu adımda yalnız metin okuyan iki birim testi koşulur (saniyeler, slot istemez). Merge ekip kilidi
altındadır; veritabanı slotu beklemek koltuk harcar. `alembic upgrade head`'i gerçekten koşan
tests/integration kapıda zaten koşar - bu daralma bilerek yapıldı.

## Bilinen sınırlar

- Geri verilip yeniden birleşen dal yeniden adlandırılmış kopyanın üstüne biner; git'in rename
  tespiti çoğunu çözer, çözemezse bugünkü yol (çakışma, Danışman).
- Hub dosya çakışmaları ve aynı tabloya dokunan iki iş bu kartın dışında: durur, nedeni yazılı.
- Göç içerikleri değişmez (yalnız belirteçler); yayın betiğinin şema denetimi ve genişletme-yalnız
  kuralı, `scripts/lib/TeamRelease.ps1`, `docs/TEAM_PROTOCOL.md` değişmedi. Yeni bağımlılık yok
  (Alembic Converger önerisi reddedildi).
- `.github/workflows/ci.yml` alan dışı: yeni suite'in oraya eklenmesi lead'in işi
  (test_ci_covers_every_suite.py onu okur).

## Ek (denetim geri dönüşü, returns=1)

- Yeniden zincirleme ya da tek uç kontrolü istisna fırlatırsa (uv 900 s'de zaman aşımı, uv.exe
  yok, dosya yazılamadı) Merge-TeamBranch bunu yakalar: merge geri alınır (reset --hard + göç ve
  test klasörlerinde git clean), Merged=$false, Conflict=$true, Detail "göç zinciri: tek uç testi
  koşulamadı: <ileti>". Döngü ölmez, entegrasyon dalı ilerlemiş kalmaz.
- NNNN_ önekli olmayan dosya adı (money_ledger.py) ya da revision id'si artık durdurmaz: ad/id
  olduğu gibi kalır, yalnız NNNN önekli olanlar yeniden numaralanır; zinciri belirleyen
  down_revision her durumda uca bağlanır. Zaten doğru uçtaysa Action 'none' (bugünkü yol).
