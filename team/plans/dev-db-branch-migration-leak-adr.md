# ADR (taslak, numarayı PY verir): Bir 'database' test slotu dev veritabanını bulduğu şemada bırakır

Tarih: 2026-10-04. Kart: dev-db-branch-migration-leak. İlgili: ADR-0282 (test sırası).

## Bağlam
2026-10-04 17:33'te 29aa2232 üzerindeki yayın kapısı 'Alembic upgrade head' adımında
`Can't locate revision identified by '0066_watches'` ile kırmızı oldu. watch-engine işçisinin
Postgres testleri, 'database' slotuyla PAYLAŞILAN dev yığında koştu; entegrasyon paketinin
`migrated_database` fikstürü dev veritabanını o dalın yeni başına (0066) taşıdı ve orada bıraktı.
Main dahil başka hiçbir ağaç 0066'yı bilmiyor; onların alembic'i başlayamadı. Danışman, watch-engine
worktree'sinden elle 0065'e indirdi.

## Karar
`scripts/team/test-slot.ps1 run`, biletin türleri arasında `database` varsa (`scripts/lib/TestSlots.ps1`):
1. Komuttan önce koşunun kendi ağacının (çalışma klasöründen yukarı `services\api\alembic.ini`)
   ayarlarıyla `alembic_version` tablosunu HAM okur (alembic'siz: bilinmeyen bir revizyon da okunur)
   ve `SEMA_KORUMA kayit <rev>` yazar.
2. Komut bittikten sonra (başarılı ya da başarısız, `finally` içinde, slot hâlâ tutulurken)
   revizyon değiştiyse, yeni revizyonu bilen tek ağaç olan koşunun kendi ağacından
   `uv run alembic downgrade <kayıt>` çalıştırır ve sonucu yeniden okuyarak doğrular.
3. Geri alma ya da doğrulama başarısızsa `<store>\database-hold.json` yazılır, `SEMA_KORUMA BASARISIZ`
   ve bir DURDU satırı basılır; komut başarılı idiyse koşunun çıkış kodu 8 olur. Kilit durdukça her
   `database` ask'i ve run'ı DURDU ile reddedilir (çıkış 6; komut hiç başlamaz), `status` kilidi
   gösterir. Danışman veritabanını onarıp `test-slot.ps1 unblock` ile kilidi kaldırır.
4. Söz yazıya da geçer: komuttan önce `<store>\database-guard.json` (beklenen revizyon, ağaç,
   veritabanı adı, sarmalayıcının ve komutun pid'i + başlangıç zamanı) yazılır; geri alma (ya da
   kilit) bitince silinir. Sarmalayıcı öldürülürse (`finally` çalışmaz; ajanların Bash zaman aşımı
   bunu sık üretir) kayıt kalır. Sonraki `database` run'ı, slotu tutarken ve komutundan ÖNCE:
   - sahibi ölü, komutu da bitmişse: o ağaçtan geri alır (`SEMA_KORUMA olu_kosu geri_alindi`),
     olmazsa (ağaç silinmiş, revizyon bilinmiyor) kilidi yazar ve DURDU ile çıkar (6), komut başlamaz;
   - kayıttaki veritabanı sunucuda artık yoksa (karalama DB silinmiş): `SEMA_KORUMA olu_kosu
     kayit_dusuruldu <db>`, kayıt silinir, kilit yok, komut koşar;
   - sarmalayıcı ölü ama komutu hâlâ çalışıyorsa: hiçbir şeye dokunmaz, komutun pid'ini söyleyen
     DURDU ile çıkar (6). O süreç bitince sonraki run geri alır.
   `ask` bu durumda sırayı değiştirmez (slot sarmalayıcıya aittir, öleni boşalır - ADR-0282 ve
   team-test-slots 6. vaka); yalnız uyarır. Engel `run`'da, komut veritabanına dokunmadan önce.
5. HANGİ veritabanı sorusunun tek kaynağı kayıttır (2026-10-07, üçüncü dönüş; önceki "ağacın ayarları
   başka veritabanını gösteriyorsa reddet" kuralı kalktı). Geri almadaki yoklama, kayıttaki adı
   `PAGENTOS_SLOT_GUARD_DB` ile alır ve ayarların sunucusunda `url.set(database=<ad>)` ile o veritabanını
   okur; `alembic downgrade`, aynı adla türetilmiş `PAGENTOS_DATABASE_URL` komutun ortamında verilerek
   koşar. Sonraki koşunun kendi URL'i/ayarı geri almayı asla yönlendirmez. Neden: ekibin alışkanlığı
   karalama DB + Bash zaman aşımı; ölen karalama koşusunun kaydını URL'i ayarsız (ya da başka bir
   karalamaya bakan) sonraki koşu buluyordu, eski kural sahte kilit yazıyor ve Danışman'a YANLIŞ
   veritabanına downgrade öneriyordu (denetim 2026-10-07, ts-5d064c8bb385). Kilit gerektiğinde DURDU
   satırı hedefi adıyla ve parolası gizli `PAGENTOS_DATABASE_URL=<url>` ile verilen downgrade komutuyla
   yazar. Parolalı URL yalnız bellekte, alembic sürecinin ortamında durur; dosyaya ve satıra girmez.
6. Kayıt yoksa (alembic_version tablosu yok) geri alma yapılmaz: geri almak `downgrade base`, yani
   her şeyi silmek olurdu. Kayıt okunamazsa (Postgres kapalı, uv yok) bu yüksek sesle söylenir ve koşu
   korumasız devam eder.

## Değerlendirilen seçenek: dal başına geçici veritabanı (CREATE DATABASE ... TEMPLATE)
Her dal testi kendi atılabilir veritabanında koşsa paylaşılan veritabanına hiç dokunulmaz; bu
yapısal olarak daha temiz. Bugün seçilmedi çünkü: (a) entegrasyon paketi ve conftest
(`exclusive_database`, `migrated_database`) tek `PAGENTOS_DATABASE_URL`'e bağlı, Temporal işçileri ve
canlı-API uyarısı da aynı veritabanını varsayıyor - değişiklik `services/api/tests` ve kapının
kendisine yayılır (bu kartın alanı dışında); (b) TEMPLATE kopyası, şablona açık bağlantı varken
başarısız olur (dev API / Temporal işçisi bağlı tutar); (c) geri alma bugünkü sızıntıyı tek
dosyada kapatıyor. Önerim: ayrı bir kart olarak, `test-slot.ps1 run -ScratchDatabase` gibi bir
seçenekle başlayıp entegrasyon paketinin URL'i dışarıdan almasını sağlamak.

## guards-integration-tests-own-db ve kapının kendi veritabanı ile ilişkisi (2026-10-07 yeniden kontrol)
Danışman sordu: guards-integration-tests-own-db (8b0353dd, integrate/d20261006) aynı sorunu çözüyor mu?
Hayır, yalnız bir parçasını:
- O kart `scripts/lib/TeamGuards.ps1`'deki birleştirme sonrası KORUYUCULARI (`services/api/tests/integration/`
  altındaki pytest koruyucuları) kendi `pagentos_g_*` veritabanına taşır. Kapı da artık
  (`scripts/lib/GateDatabase.ps1`, ADR-0254) kendi `pagentos_gate_*` veritabanında koşar; yani 2026-10-04'teki
  kırmızı kapı bugün aynı yoldan tekrarlamaz.
- Ama `test-slot.ps1 run` ile ELLE koşulan işçi/denetleyici Postgres testleri hâlâ ayarların gösterdiği
  veritabanına gider; `pagentos_scratch_*` kullanmak bir alışkanlık, zorunluluk değil. URL'i ayarlamayan
  bir koşu paylaşılan `pagentos`'u kendi dalının başına taşır; dev API'si (main) ve kendi veritabanını
  kurmayan her araç o zaman 'Can't locate revision' ile düşer. Bu kartın koruması tam bu yolu kapatır;
  scratch veritabanında koşan bir koşuda kayıt o veritabanını gösterir ve iş yapmadan geçer.
Kart kapanmadı; dal team/nightly/lead (93122258) üstüne rebase edildi, test-slot.ps1'deki pano
satırlarıyla (8322f909) çakışma ikisini yan yana tutarak çözüldü.

## Bilinen sınırlar
- Kapı (`quality-gate.ps1`) slotları kütüphaneden doğrudan alır, `test-slot.ps1`'den geçmez; ama artık
  kendi `pagentos_gate_*` veritabanında koştuğu için paylaşılan veritabanını taşımaz. Kalan boşluk:
  kapı, ölü bir koşunun `database-guard.json` kaydını tamamlamaz (takip kartı).
- `test-slot.ps1` dışında (slotsuz) elle koşulan alembic ya da pytest korunmaz.
- Downgrade fonksiyonu eksik/yanlış yazılmış bir göç geri alınamaz: tam olarak bu durumda kilit devreye
  girer ve Danışman bakar.
- Her korunan koşu 2-4 `uv run python` yoklaması ekler (her biri birkaç saniye).
- Öldürülen bir koşunun geri alınması ancak SONRAKİ `test-slot.ps1 run` (database) ile olur;
  arada slotsuz bir alembic ya da dev API'si koşarsa dev veritabanı hâlâ yeni baştadır.
- Komutun kendi alt süreçleri (ör. pytest'in açtığı bir sunucu) kayıtta yok; yalnız doğrudan komutun
  pid'i izlenir. Sarmalayıcıyla birlikte süreç ağacı öldürülürse (taskkill /T) sorun yok.
- Sunucu ve parola ağacın ayarlarından gelir; kayıt yalnız veritabanı ADINI tutar. Ölen koşu başka bir
  Postgres sunucusundaysa (bugün tek dev yığını var) geri alma bu sunucuda aynı adı arar.
- "Yok" kararı `postgres` bakım veritabanındaki `pg_database` sorgusuna dayanır; o veritabanına
  bağlanılamazsa yoklama hata verir ve kilit yazılır (yanlış "düşürüldü" yerine yüksek sesli kilit).
- Gerçek `pagentos` veritabanı testte hiç yazılmaz: ters yön vakası (9c) ikinci bir karalama DB'yi
  paylaşılanın yerine koyar. Testler panoya not basmaz (PAGENTOS_TEAM_URL/TOKEN_FILE/SEAT silinir).
- Testler Base/Head'i ağacın kendisinden türetir (`alembic heads`, başın Parent'ı); yeni göç
  girdiğinde test kendiliğinden yeni çifte geçer.
