# ADR (numarasız - lead numaralar): Yerel embedder'ın önbelleği kilitli, model çağrısı kilidin dışında

Tarih: 2026-10-02 · Görev: `local-embedder-lru-lock` · Durum: önerildi (worker)

## Bağlam

ADR-0245 anlama motorunun dizinini açılışta bir daemon iş parçacığında kurar: ~1430 örnek cümle
BELLEK ÇALIŞMA ZAMANININ embedder'ından geçer, aynı nesneyi istek iş parçacıkları da kullanır.
`LocalEmbedder._cache` (512 girişlik LRU, `OrderedDict`) kilitsizdi. Bozan sıra tek ve dardır:
bir iş parçacığı isabet eden girişi OKUR (`get`), yerini tazelemeden (`move_to_end`) önce başka
bir iş parçacığı yeni bir metni yazıp EN ESKİ girişi atar - atılan giriş az önce okunansa
tazeleme `KeyError` verir. Kurulum iş parçacığında bu, "motor yapılandırılmadı" demektir:
yakalanır, süreç ömrünce öyle kalır, yalnızca günlükte görünür (denetleyici bulgusu 4,
`understanding-engine-startup-inspector-1.md`). Ölçüm: kilitsiz kodda 8 iş parçacığı x 2000
karışık embed (geçiş aralığı 1 µs) 10 koşunun 7'sinde `KeyError` ile bitti.

## Karar

1. **Bir `threading.Lock` önbelleği ve `calls` sayacını korur.** İki kısa bölüm: (a) arama +
   isabetse yerini tazeleme, (b) yazma + sayaç + taşma varsa en eskiyi atma. Kopya
   (`list(cached)`) kilidin dışındadır: saklanan vektör hiç değiştirilmez, her çağıran kendi
   listesini alır.
2. **Model çağrısı kilidin DIŞINDADIR.** Yavaş bir embedding başka hiçbir isteği bekletmez;
   önbellekteki bir metin, başka bir iş parçacığı modelin içindeyken döner.
3. **Aynı önbelleksiz metni aynı anda soran iki iş parçacığı ikisi de hesaplar.** Kabul edilen
   bedel: aynı metin, aynı vektör, iki model çağrısı (`calls` iki artar), önbellekte TEK giriş.
   İstek başına "uçuşta" tablosu (ikincisi birinciyi beklesin) yazılmadı: model metin başına
   ~0,3 ms, çakışma seyrek, tablo yeni bir kilit sırası ve hata yolu demek.
4. **Davranış aynı kalır:** boyut 512, atma sırası (en az son kullanılan), isabet/ıska anlamı,
   `calls`'ın tek iş parçacığındaki değeri. `OpenAIEmbedder` bu görevde DEĞİŞMEDİ (aşağıda).

## Maliyet (ev bilgisayarı, gerçek model `potion-multilingual-128M`, 2026-10-02)

Aynı süreçte, turlar sırayla (önce/sonra/önce/…), 15 tur x 2 koşu; makinede başka çalışanların
test koşuları vardı, gürültü farktan büyük:

| ölçü | önce (kilitsiz) | sonra (kilitli) | tur başına fark (medyan) |
|---|---|---|---|
| önbellekteki metin, `embed()` | 842 - 1200 ns | 1100 - 1471 ns | +233 … +299 ns |
| önbelleksiz metin, `embed()` | 416 - 516 µs | 373 - 551 µs | +6 … +18 µs (gürültü içinde) |
| dizin kurulumu, 1427 örnek | 407 - 749 ms | 442 - 668 ms | -16 … +18 ms (gürültü içinde) |

Sakin makinede kilitsiz kod (ayrı süreç, iki koşu): önbellekte 762-771 ns, önbelleksiz
266-276 µs, kurulum 417-447 ms. Okuma: kilit çağrı başına ~0,25 µs ekler; kurulumda bu
1427 x 2 kilit ≈ 1 ms'nin altıdır ve ölçülemedi.

## Kanıt

`tests/unit/test_memory_local_embedder.py` (4 yeni test). Sıra zorlanır, uyku yoktur: kapılı
sahte model B'yi model çağrısının içinde tutar, önbelleğin `get`'i A'yı okuma ile tazeleme
arasında tutar, kilidin gözlemcisi "bu iş parçacığı kilitte bekliyor"u olay yapar. Kilitsiz
kodda (HEAD) 3/3 koşu `KeyError('metin 0')`. Mutasyonlar: kilit yok / yalnız arama kilitsiz /
yalnız yazma kilitsiz → zorlanmış test KIRMIZI; model çağrısı kilidin içinde → "önbellekteki
metin o sırada döner" ve "ikisi de hesaplar" KIRMIZI.

## Bilinen sınırlar

- `OpenAIEmbedder` aynı kilitsiz LRU kalıbını taşır (aynı dosya, `OpenAIEmbedder.embed`). Dizin kurulumu onu
  hiç kullanmaz (ADR-0245 yalnız yerel sağlayıcıda kurar), ama istek iş parçacıkları paylaşır;
  aynı pencere orada da vardır. Bu görevin kapsamı dışında bırakıldı - ayrı kart.
- Kilit GIL'i paylaşmayı değiştirmez: kurulum yine olay döngüsüyle CPU için yarışır
  (denetleyici bulgusu 3).
- `LocalEmbedder` artık bir `Lock` taşıdığı için `copy.deepcopy` / pickle edilemez; depoda
  bunu yapan kod yok.
