# ADR (taslak): Entegrasyon çözücüsü ayrık satır değişikliklerini birleştirir

**Bağlam.** 2026-10-07 12:07'de understanding-plural-context-typos (denetleyici onaylı, dal 5de7fe4f)
resolve_integration'da "ekleme değil" diye Danışman'a düştü. Çakışma tek diff3 bloğuydu: iki taban
satırı (routes.py importları), entegrasyon 1. satırı (`..., Response`), iş 2. satırı (`..., model_validator`)
değiştirmişti. git bitişik satır değişikliklerini çakışma sayar; Resolve-TeamDutyHunk yalnız saf
eklemeyi kabul ediyordu.

**Karar.** Ekleme denetiminden sonra Resolve-TeamDutyDisjointHunk çalışır: taban, ours ve theirs
aynı satır sayısındaysa ve hiçbir satırı iki taraf farklı biçimde değiştirmemişse sonuç satır
satır kurulur: o satırı değiştiren tarafınki, hiçbiri değiştirmediyse ours'unki. Satır sayısı
farklıysa (silme, ekleme dışı her şey) ya da bir satırı iki taraf farklı biçimde değiştirmişse
sonuç yine "ekleme değil" olur. Karşılaştırma tam eşitliktir; JSON'daki "sondaki virgül sayılmaz"
gevşekliği bu yolda uygulanmaz (daha tutucu).

**Neden mekanik.** Her taban satırı için yalnız bir taraf görüş bildirmiştir; birleşim bir tarafın
değişikliğini atmaz ve hiçbir satırı yeniden yazmaz. Bu, diff3'ün tek satır düzeyindeki üç yollu
birleştirme kuralının aynısıdır; git'in bunu çakışma saymasının tek nedeni bağlam penceresidir.

**Güvenlik ağı aynı kalır.** Birleşim sözdizimsel olarak doğru ama anlamca yanlış olabilir (ör. bir
taraf bir adı değiştirir, öteki komşu satırda eski adı kullanır). Bu yüzden team/guards.json
koruyucuları ve işin kendi testleri birleşik ağaçta bugünkü gibi koşar; kırmızıysa "koruyucu
kırmızı" ile Danışman'a gider ve integrate/<cycle> değişmez.

**İlişki.** non-additive-conflict-back-to-worker kartı "ekleme değil"in nereye gideceğine karar
verir; bu kart yalnız neyin "ekleme değil" sayıldığını daraltır.
