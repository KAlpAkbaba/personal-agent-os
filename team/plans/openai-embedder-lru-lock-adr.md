## ADR-0256 eki — OpenAI embedder'ın önbelleği de kilitli; ağ çağrısı kilidin dışında (2026-10-03)

Görev `openai-embedder-lru-lock` (ADR-0256 denetleyicisinin açık notu).

**Bağlam.** `OpenAIEmbedder` (`services/api/app/memory/providers.py`) ADR-0256'dan önceki
`LocalEmbedder` ile aynı kilitsiz 512 girişlik LRU'yu taşıyordu: okuma, `move_to_end`, yazma ve
`popitem` kilitsiz. Bellek çalışma zamanının TEK embedder'ı istek iş parçacıklarınca paylaşılır;
sahibin ayarı `openai` (ya da anahtarlı `auto`) iken o nesne budur. Kartın öncülünden bir fark:
anlama motorunun dizin kurulumu bu sınıfı KULLANMAZ - `app/voice/understanding/startup.py`
yalnız `report.active == "local"` iken kurar. Yarış OpenAI'de istek iş parçacıkları arasındadır
(aynı pencere: isabetin okunması ile tazelenmesi arasına başka bir iş parçacığının yazma+atması
girer → `KeyError`).

**Karar.** ADR-0256 ile aynı biçim:

1. Bir `threading.Lock` önbelleği ve `calls` sayacını korur; iki kısa bölüm: (a) arama + isabetse
   `move_to_end`, (b) `calls += 1` + yazma + `move_to_end` + taşma varsa en eskiyi atma. Kopya
   (`list(...)`) kilidin dışında.
2. Sağlayıcı çağrısı (ağ, `timeout_s` 20 s'ye kadar) kilidin DIŞINDA: önbellekteki metin, başka
   bir iş parçacığı API'yi beklerken döner.
3. **Sınıfın farklı olduğu yer:** `calls` artışı `_fetch` içindeydi (kilitsiz, `+=` iş parçacığı
   güvenli değil); artık `embed`'in yazma bölümünde, kilidin içinde. Değeri aynı: yalnızca başarılı
   bir yanıtta bir artar (hata yolunda ne sayaç ne önbellek değişir - test sabitliyor).
4. **Uçuştaki istek tablosu YAZILMADI, ücretli sağlayıcı için de.** Aynı önbelleksiz metni aynı anda
   soran iki iş parçacığı iki istek atar. `text-embedding-3-small` 1M token başına 0,02 $; bir cümle
   ~20 token → çift istek ~4e-7 $. Çakışma ancak aynı metin aynı 100-300 ms'lik ağ penceresinde iki
   kez sorulursa olur; gün boyu bile kuruşun altında. Tablo ise ağ hatası/zaman aşımı yolunda
   bekleyenleri uyandırma ve hata paylaşma demek - bedelinden büyük risk. Sayaç (`calls`) ve
   sağlayıcının kendi kullanım raporu çiftleri görünür kılar; ölçülürse yeniden açılır.
5. Davranış aynı: boyut 512, LRU atma sırası, isabet/ıska, tek iş parçacığında `calls`.

**Kanıt.** `services/api/tests/unit/test_memory_openai_embedder_lock.py` (6 test), sağlayıcının
HTTP istemcisi `httpx.MockTransport` ile değiştirilir (ağ yok). Zorlama makinesi
`test_memory_local_embedder.py`'den içe aktarılır (`_HeldCache`, `_observe_lock`, `_Call`, `_join`,
`_text_vector`), kopyalanmaz. Kilitsiz kodda iki zorlanmış test KIRMIZI (`KeyError('metin 0')`);
"yalnız yazma kilitli" ve "yalnız isabet kilitli" mutantlarının her biri İKİ zorlanmış testi 3/3
KIRMIZI yapar.

**Bilinen sınırlar.** Gerçek OpenAI API'si çağrılmadı (NOT_RUN, bu kart ücretli çağrı yapmaz).
`OpenAIEmbedder` artık `Lock` taşıdığı için pickle/deepcopy edilemez; depoda bunu yapan kod yok.
