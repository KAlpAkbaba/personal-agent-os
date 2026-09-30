# Öneri: Anlatı — "bu hafta ne oldu, ofiste ne yaptın, ne başarısız oldu"

Tarih: 2026-09-30 · Araştırmacı · Durum: awaiting_owner

## Ne
Roadmap satırı: JARVIS tablosu "her şeyi kaydeden ve istediğim zaman bana anlatan" (PARTIAL),
sıra 2c. Ledger ve hafıza üzerinde TEK bir konuşulan anlatı; başarısızlıklar dahil, istenince.
Sahibin cümlesi: "Bu hafta ne oldu?" → yaklaşık 30 saniyelik Türkçe özet: ne bitti, ne
başarısız oldu ve neden, hangi cihazda (ofis / ev / bulut) ne yapıldı; "ayrıntı" denirse
kayıtlara inilir. Uzun sonuç kendiliğinden dayatılmaz (CLAUDE.md: kısa haber ver, bekle).

## Neden şimdi
- Roadmap'in tek "tanım tam ama bileşen dağınık" satırı; sahibin tanımının ikinci yarısı
  ("istediğim zaman bana anlatan").
- Parçalar mevcut (koda bakarak): `app/ledger/service.query()` (zaman/alt sistem/durum
  süzgeci, en fazla 200 satır), `latest()` ("son ne yaptın"), `app/explain/engine.py`
  (kanıt-önce bileşim; sorgu türleri arasında `QUERY_FAILURES`; kanıtsız soru belirsizlikle
  yanıtlanır), briefing kuyruğu, hafıza (M5). Eksik olan, benim okuduğum kadarıyla: hafta
  ölçeğinde birleştirme, **hafıza ile ledger'ın aynı anlatıda buluşması**, cihaz adına göre
  filtre ("ofiste"), ve başarısızlığın *neden* ile birlikte anılması. Doğrulanmadı:
  explain'in "bu hafta" gibi geniş aralıkları bugün ne kadarını kapsadığını çalıştırıp
  denemedim — işçi ilk iş bunu ölçecek.
- Dışarıdan kanıt (zayıf-orta): özetlemede iki başarısızlık türü var — sadakatsiz içerik ve
  **atlanan olay**; sadece "dayanaklı mı" ölçen değerlendirme tarih/sayı hatalarını kaçırır
  ([FutureAGI 2026](https://futureagi.com/blog/llm-summarization-evaluation-deep-dive-2026/),
  [NTS-CoT, arXiv 2606.13171](https://arxiv.org/abs/2606.13171v1)). Bunlar blog/ön-baskı;
  ben yöntem değil, *risk listesi* olarak kullanıyorum. Sahibin en çok kızacağı hata, bir
  başarısızlığı anlatmamak — tam da atlama türü.

## Nasıl
- **Bileşen:** yeni `app/narrative/` — (1) *toplayıcı*: deterministik kod, dönemi ve cihazı
  çözer, ledger olaylarını (başarısızlar ÖNCE), ilgili hafıza kayıtlarını ve `no_capable_device`
  gibi olayları sayar ve gruplar; (2) *anlatıcı*: model yalnızca toplayıcının verdiği sayı ve
  kayıtlarla Türkçe cümle kurar; (3) *denetçi*: deterministik — her başarısız/tamamlanan
  olayın anlatıda geçtiğini ve her sayının kayıttan geldiğini kontrol eder, yoksa anlatı
  reddedilir ve "N başarısız iş var" cümlesi koda eklenir.
- **Mevcut yüzeye:** `explain` motoruna yeni bir sorgu türü olarak (QUERY_PERIOD), yeni ayrı
  sistem değil; `voice/intents` ve tek yönlendirici; anlatım TTS'i mevcut narration yolu
  (STT/TTS ayrı alt sistem kuralı korunur).
- **Değişmeyen:** ledger salt okunur; hafıza silme/düzeltme semantiği; kanıtsız soruya uydurma
  yok; yeni bulut/dış servis yok.

## Maliyet/risk
- **Efor:** orta (toplayıcı+denetçi kod, anlatıcı ucuz model çağrısı, niyet, testler).
- **Çalışma maliyeti:** talep başına bir küçük model çağrısı (Haiku sınıfı, ADR-0207 ile aynı
  ucuz katman); CPU/bellek ek yok.
- **Lisans:** yeni bağımlılık yok.
- **Gizlilik:** anlatı sahibin verisini zaten sahip olunan modele verir (sohbet/araştırma ile
  aynı yol). Hafıza içeriği uzun sesle okunmasın diye sahip ayarı: varsayılan özet, ayrıntı
  istenince. Ofis/işveren makinesi olaylarında `turka.com` ve Kolay Monitor gibi yasaklı alanların
  içeriği ledger'a zaten yazılmıyorsa anlatıya da girmez — doğrulanacak.
- **Risk:** "ne başarısız oldu" cevabının eksik olması (atlama) ve sahte güven. Denetçi + test
  bunun için.

## Kanıt planı
- PROVEN_AUTOMATED: sabit bir ledger/hafıza veri kümesi; her başarısız olay anlatıda anılır
  (mutasyon: toplayıcı bir başarısızlığı düşürür → denetçi RED); ikinci koşuda değişiklik yok;
  kanıtsız dönem → "kayıt yok".
- PROVEN_PROXY: gerçek model, gerçek dev veritabanı, üç örnek soru; çıktı elle sayılarla
  karşılaştırılır (iki saat sonra, ikinci kez, aynı olay kümesi).
- PROVEN_REAL: **sahip** "bu hafta ne oldu" ve "ofiste ne yaptın" der; anlatıda bildiği bir
  başarısızlığın eksik olup olmadığına bakar; son hüküm onun.

## Karar
**Anlatıyı önce ledger + başarısızlık odaklı (hafıza katkısı ikinci adım) olarak yapalım mı?**
Alternatifler: (1) hafıza+ledger birlikte tek PR; (2) yalnız Kokpit'te yazılı haftalık özet, ses
sonra; (3) hiç yeni bileşen yok, `explain`'e "bu hafta" ifadesi eklenir (en küçük, ama hafızayı
kapsamaz).
