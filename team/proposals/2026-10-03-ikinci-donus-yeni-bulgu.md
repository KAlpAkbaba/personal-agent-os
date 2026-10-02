# Öneri: İkinci dönüş yeni bir bulguysa iş durmasın — durdurma yalnız düzeltilmemiş bulgu tekrar edince

Roadmap: tek bir JARVIS satırı değil; "Repairs and improves itself" ve "How it is built from here" (ekip
döngüsü). `cycle.ps1`'in "iki RETURN işi durdurur" kuralı (satır 116).

## Ne

Bugün denetleyici bir işi ikinci kez geri verince döngü işi durdurur ve lead elle açana kadar bekler —
ikinci dönüşün nedeni ne olursa olsun. Ama son döngüde durdurulan işlerin çoğunda ilk dönüşün maddeleri
KAPANMIŞTI; ikinci dönüş ya yeni, gerçek bir bulguydu ya da yalnız testin gücüyle ilgiliydi (mutasyonu RED
gösteren test). Kural, "işçi düzeltmedi" ile "denetleyici bir şey daha buldu" arasında fark gözetmiyor.
Öneri: denetleyici RETURN raporuna tek bir satır yazar — `onceki_bulgular: kapandi` ya da
`onceki_bulgular: acik [1, 3]`. Döngü: önceki maddeler kapandıysa işi bir kez daha işçiye verir (koşu tavanı 8
olduğu gibi kalır); bir önceki madde hâlâ açıksa ya da bu üçüncü dönüşse eskisi gibi durdurur. Senin
göreceğin: Ofis sayfasında "durduruldu — lead bekleniyor" satırları azalır; sahibin 1. önceliği olan iş gece
boyunca kendiliğinden ilerler.

## Faydası — örneklerle

1. Bugün: `understanding-rules-read-lemmas` (ADR-0224, %68,9 → %95 hedefi, senin 1. önceliğin) ikinci
   denetimde durdu; denetleyici "ilk dönüşün maddeleri kapandı, bir yeni gerçek bulgu var" diyordu. Lead 3 Ekim
   01:25'te elle yeniden açtı.
   Bununla: rapordaki `onceki_bulgular: kapandi` satırıyla iş aynı turda işçiye döner; lead'in eli gerekmez.
2. Bugün: `cycle-auto-integrate` beşinci denetimde "başlangıç penceresinin düzeltmesini ilke olarak kabul etti,
   TESTLERİNİ geri verdi"; lead'in notu: "the stop was only the second-return rule" (3 Ekim 00:05).
   Bununla: kapanmış bulguyla yeni test isteği ayrılır; durma yalnız gerçekten tekrar eden kusurda olur.
3. Bugün: `local-embedder-lru-lock` geri verilenler listesinde yalnız "iki kısmi kilit mutantını deterministik
   RED yap" maddesiyle duruyor.
   Bununla: aynı satır işi otomatik devam ettirir; işçi yalnız istenen testi yazar.

Kazanç: d20261002 raporunda "geri verilenler" listesindeki 4 işin ikisini lead açıkça "yalnız ikinci dönüş
kuralı durdurdu" diye yeniden açtı, üçüncüsü (`local-embedder-lru-lock`) yalnız test maddesiyle bekliyor
(durdurma nedeni raporda yazılı değil — kanıt bu iş için zayıf); her biri lead'in elle müdahalesine kadar boş
bekledi. Ölçü: ikinci dönüşte durdurulan işlerin sayısı ve lead
tarafından elle yeniden açılma sayısı — ikisi de raporlardan sayılabilir.
Kazanmadığımız: sonsuz döngü riskini kaldırmaz (8 koşu tavanı ve "üçüncü dönüş durdurur" kalır); denetleyici
yanlış "kapandi" yazarsa bir tur boşa gider; alan dışı dosya durumunu çözmez (o ADR-0253'ün işi).

## Neden şimdi (aynı kusur üç kez; hepsi kayıtta)

- `team/reports/d20261002.md` "Geri verilenler": `cycle-auto-integrate` ("LEAD (2026-10-03 00:05): reopened -
  the sixth inspection … accepted the fix … in principle and returned the TESTS of it; the stop was only the
  second-return rule"); `understanding-rules-read-lemmas` ("LEAD (2026-10-03 01:25): reopened - the stop was
  only the second-return rule … found the first return's items closed and one new real one");
  `local-embedder-lru-lock` (yalnız mutasyon testleri).
- ADR-0253 (alan genişletme) aynı biçimi bir başka neden için zaten çözdü: "the second return stops it, and it
  waits for the lead to open it by hand … The written rule in `lead.md` did not prevent it twice, so the cycle
  needs a rule it can execute." Bu öneri aynı ilkenin ikinci yarısı.
- `scripts/team/cycle.ps1:116`: "Two RETURNs still stop a task; this only bounds a loop."

## Nasıl

- Sözleşme satırı, ADR-0253'ün `alan_disi:` satırı gibi: tek satır, satır başında anahtar, son satır geçerli;
  ayrıştırıcı `scripts/lib/TeamArea.ps1` yanında saf bir fonksiyon (`Get-TeamPriorFindings`), kendi Pester
  paketi.
- `.claude/agents/inspector.md`: ikinci ve sonraki dönüşte satırı yazmak zorunlu; satır yoksa = "acik"
  (bugünkü davranış — güvenli varsayılan).
- `cycle.ps1`: durdurma kararı RETURN sayısı yerine (dönüş sayısı, önceki bulgular) ikilisinden; tavan 8 aynen.
- Değişmeyen: denetleyicinin bulgu yazma biçimi, lead'in elle açma yolu, alan dışı kuralı.

## Maliyet/risk

- Emek: küçük-orta (iki kart: kural + rol satırı; sonra döngü bağlaması — ADR-0253'ün sırası).
- Çalışma maliyeti: bir iş en fazla bir koşu daha (~2-4 USD) — bugün aynı koşu lead elle açınca zaten yapılıyor.
- CPX32 / KVKK / cihaz / işveren makinesi: etkisi yok (yalnız ev PC'sindeki döngü betiği).

## Kanıt planı

- PROVEN_AUTOMATED: Pester — "kapandi" + ikinci dönüş → devam; "acik [1]" → dur; satır yok → dur; üçüncü
  dönüş → dur; mutasyon RED (satır hep "kapandi" okunursa).
- PROVEN_REAL: bir sonraki gerçek döngüde ikinci dönüşü "kapandi" olan bir işin lead dokunmadan yeniden
  işçiye gittiği durum belgesinde görülür. Senin denemen gerekmez.

## Karar

Yapalım mı?
Alternatifler: lead her turda durdurulanları elle gözden geçirsin (bugünkü hâl; saatler kaybettiriyor);
tavanı üç dönüşe çıkarmak (düzeltilmeyen bulguyu da bir tur daha döndürür — daha kaba).
