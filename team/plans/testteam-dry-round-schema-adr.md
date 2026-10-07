# ADR (numarasız): Test ekibinin dosya sözleşmesi tek şemada; şemaya göre okuyan, istisna atmayan okuyucu; kuru tur

Görev: testteam-dry-round-schema (önerinin 1/2'si; öneri team/proposals/2026-10-06-test-ekibi-kuru-tur.md, sahip onayladı).

## Bağlam

Test ekibinde iki taraf ayrı yazıldı: rol dosyası (test-lead.md, tester.md) modele "şu biçimde bir
dosya yaz" der, betik (test-round.ps1 + TestTeam.ps1) o dosyayı okur. 6 Ekim'de aynı sınıftan üç
kusur üç yamayla kapandı, iki gerçek tur yandı: 3d430def (senaryosuz doğaçlama iş, tur başlamadı),
75ceba26 (tester/card'sız el yazısı sonuç, kırılma raporu öldü), fc62979c (p95_ms'siz basamak,
ikinci tur yarıda kaldı). Sınıf kapanmadı: bir sonraki yeni alan aynı yoldan bir turu yine öldürür.

## Karar

1. **Şema dosyaları** `scripts/testteam/schema/result.json` ve `plan.json`: zorunlu ve isteğe bağlı
   alanlar TEK yerde. Kendi küçük biçimimiz: `{ version: 1, doc, fields: { <ad>: { required,
   type: string|int|bool|enum|array|object, values (enum), default (null = yok), min_items (array),
   items: { fields } (array), fields (object) } } }`. Neden kendi biçimimiz: Windows PowerShell
   5.1'de JSON Schema kütüphanesi yok, yeni bağımlılık istemiyoruz; ConvertFrom-Json ile okunur.
   Her alanın `default`'u yazılıdır (zorunlu alanlarda null).
2. **Zorunlu / isteğe bağlı kuralı.** Dosya olmadan hiçbir şey söylemeyen alan ZORUNLU; betiğin
   anlamı değiştirmeden varsayılanla doldurabildiği her alan İSTEĞE BAĞLI ve varsayılanı şemada.
   result: zorunlu yalnız `state`; card/tester/family/scenario/staging_sha/screenshot/environment/at
   -> ''; steps -> [] (adım: name '?', method/path/expected/actual '', ok true, ms 0); breaking ->
   yok (what '', tried [] - basamak: load '?', ok/errors 0, p95_ms '?', max_ms 0, first_error '',
   flaky false, repeat yok; first_failure yok, aynı basamak biçimi); refused -> [] (name '?',
   state 'refused'). plan: zorunlu `jobs` (en az bir iş: `min_items: 1`, çünkü New-TestTeamCards
   boş listeyi bağlamada reddeder - kuru tur bunu buldu) ve her işin `family`'si (boş metin de
   eksik); scenario '', improvise false, why ''. Şemada olmayan alan (testçinin `notes`'u) KORUNUR.
   'Senaryosuz iş improvise ister' kuralı New-TestTeamCards'ta kalır; okuyucu yinelemez, kuru tur o
   kuralın test-lead.md'deki `("improvise": true)` cümlesiyle uyuştuğunu kanıtlar.
3. **state değerleri passed|failed|broke|environment.** Kartta passed|failed|broke yazıyordu; ama
   run-scenario.ps1 `environment` yazar ve test-round.ps1 onu işler (oturum öldüyse iletilmez). Onu
   okunamadı saymak 2/2'de her ortam sonucunu 'okunamadı'ya çevirirdi. Suite run-scenario.ps1'in
   yardım bloğundaki `state: passed|failed|broke|environment` listesini okur ve şemanın enum'unu ona
   eşit ister - sözleşme yarıları birbirini okur.
4. **Okuyucu** `scripts/testteam/TestTeamSchema.ps1`: Read-TestTeamSchema (şemanın kendisi
   okunamıyorsa throw - o bizim dosyamız), ConvertTo-TestTeamShape (Readable = Missing boş; Value =
   her alanı varsayılanla doldurulmuş, tipleri çevrilmiş belge: '7' -> 7, 'TRUE' -> true, 'PASSED' ->
   passed; enum dışı ya da tipi tutmayan değer o alanı eksik sayar; Missing / Defaulted noktalı yol:
   `breaking.tried[1].p95_ms`; Why Türkçe tek cümle; belge ne olursa olsun ASLA throw etmez),
   Read-TestTeamResult -Path [-Card] (dosya yok -> 'dosya yok: ...'; boş ya da JSON değil -> 'JSON
   değil: ...'; -Card ile boş/eksik card/tester/family/scenario iş kartından dolar ve Defaulted'da
   adlanır), Read-TestTeamPlan, New-TestTeamHalfDocuments (şemadan üretilen en yarım belgeler ailesi:
   zorunlu-yalniz, iskelet, her zorunlu alan için zorunlu-eksik, az-oge, enum-disi, yanlis-tip, tam,
   metin, fazladan, json-degil, bos, nesne-degil; bugün result 35, plan 13 belge),
   Test-TestTeamRoleNamesSchema.
5. **Readable ve 'okunamadı' ≠ 'failed'.** Okunamayan dosya staging'in hatası değildir: iletilmez,
   raporda adıyla ve sayıyla görünür. Önerinin riski fazla hoşgörülü okuyucunun boş sonucu sessizce
   geçirmesiydi: `state` zorunlu olduğu için state'siz dosya okunamadı kaydıdır (sabit fikstür
   vakası); Defaulted her doldurulan alanı adlar, rapor 'eksik: p95_ms' diyebilir.
6. **Kuru tur suite'i** `scripts/tests/testteam-dry-round.tests.ps1`: üretilen aile + 6 Ekim'in üç
   biçimi kelimesi kelimesine SABİT fikstür (iki taraf tek kaynaktan olmasın) + 'eski okuyucu'
   negatif kontrolü (üç fikstürde adı geçen alan yüzünden düşer; zararsızlaşırsa kırmızı) +
   run-scenario.ps1 yardımını okuyan sözleşme vakası + rol dosyası tablosu (rol -> adlandırması
   gereken şema) ve iki test-lead.md kopyasının bayt eşitliği. Okunabilen her sonuç TestTeam.ps1'in
   DEĞİŞMEYEN Get-TestTeamFailures / Format-TestTeamBreakingReport / Format-TestTeamSeatNote'undan,
   her plan New-TestTeamCards'tan geçer.

## Neden iki kart

test-round.ps1, run-scenario.ps1, tester.md (iki kopya) test-round-keeps-staging-session kartının;
TestTeam.ps1 ve testteam.tests.ps1 one-bad-card-never-stops-the-team kartının alanında. Birbirine
dokunan işler ajanları takmasın (sahip, 2026-10-05): 1/2 sözleşmenin tek kaynağını, okuyucuyu ve
kuru turu kurar, alınmış dosyalara dokunmaz.

**2/2 (tel çekme; o alanlar boşalınca Proje Yöneticisi keser):**
- test-round.ps1 sonuç dosyalarını `Read-TestTeamResult -Card` ile okur (bugünkü `Read-TeamJson` +
  `[string]$result.state` yerine); okunamayan her dosya kopma-noktasi.md'de
  `okunamadı: <testçi> - <eksik alanlar>` satırı ve tur özetinde sayı olur; tur kalanını bitirir;
  okunamadı iletilmez (failed değildir).
- test-round.ps1 planı `Read-TestTeamPlan` ile okur; okunamayan plan turu Why ile durdurur.
- TestTeam.ps1'deki fc62979c (ConvertTo-TestTeamLadderStep varsayılanları) ve 75ceba26
  (Format-TestTeamBreakingReport'ta Get-TeamProperty '?' varsayılanları) yamaları okuyucuya taşınır
  (değerler zaten şemada); testteam.tests.ps1 buna göre.
- tester.md (iki kopya) result.json şemasını adlandırır; suite'in rol tablosuna
  `tester.md -> scripts/testteam/schema/result.json` satırları eklenir.
- Senaryo dosyası şeması (scripts/testteam/schema/scenario.json) + run-scenario.ps1'in okuyucusu,
  kuru tura senaryo ailesi.

**Proje Yöneticisinin tel çekme satırı:** scripts/quality-gate.ps1'deki `testteam` adım listesine
(`foreach ($name in @("team-release", ..., "testteam", ...))`) `"testteam-dry-round"` adı eklenir -
dosya gate-unit-parallel kartının alanında. ci.yml'e satır gerekmez: ci-ps-suites-from-glob'dan
sonra `scripts\tests\*.tests.ps1` glob'u yeni suite'i kendiliğinden koşar (bu kartın depends_on
nedeni: adlanmasa test_ci_covers_every_suite kırmızı olurdu; bu dalda 11/11 yeşil).

## Reddedilen alternatifler

- Her ölümde tek tek yamamak: üç yamada üç kez yapıldı; sınıf açık kaldı, dördüncü alan yine bir
  turu öldürür.
- Testçiye daha katı biçim dayatmak: model yine alan atlar; doğaçlama sonuçları el yazısıdır, katı
  biçim tur kaybını testçiye yükler, hatayı değil.
- state'i isteğe bağlı yapmak: state'siz dosya hiçbir şey söylemez; varsayılan ('passed' ya da
  'failed') ya hata uydurur ya da hatayı yutar.
- JSON Schema kütüphanesi: PS 5.1'de yok; yeni bağımlılık.

## Değişmeyen

test-round.ps1, run-scenario.ps1, TestTeam.ps1, testteam.tests.ps1, tester.md; staging dışı host
reddi; raporun Danışman'a gitmesi; testçinin görevi; test ekibinin koltukları. Yeni bağımlılık yok.
