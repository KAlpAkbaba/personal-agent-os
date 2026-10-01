# ADR (numarasız - lead numaralar): Ofis koşuyu sayar, koltuğu değil

Tarih: 2026-10-02 · Görev: `office-worker-seats` · Durum: önerildi (worker)

## Bağlam

Sahip, 2026-10-01: döngü DÖRT çalışan + bir denetim koşarken `/core/office` üç çalışan koltuğu
gösterdi (dördüncü koşu hiçbir yerde yoktu) ve üst çubuk beş koşu varken `koşan ajan 4/6` dedi.
Aynı akşam üç denetleyici koşusu TEK çalışan denetleyici ve `1/6` olarak göründü. Sebep tek:
`office_view` sekiz sabit koltuğu sayıyordu, koşuları değil; rol başına ilk koşu dışındakiler
ve dördüncü çalışandan sonrası sessizce atılıyordu.

## Karar

1. **Koltuklar veriden gelir.** Çalışan koltukları `worker-1..N`, `N = max(4, canlı çalışan
   koşusu)`. Sıra değişmez: lead, researcher, integrator, worker-1..N, inspector, owner. Dört
   koltuk her zaman vardır; beşinci ve altıncı (ve gerekirse fazlası) döngü o kadar koşarsa.
   Bir koşu asla koltuksuz kalmaz - üst sınır yoktur, sınırı döngünün kendi yuva sayısı koyar.
2. **`running_agents` canlı KOŞU sayısıdır** (koltuğa oturan her koşu), çalışan koltuk sayısı
   değil. `capacity = max(6, running_agents)`: döngü kendi yuva sayısını bildirene kadar sayı
   kapasiteyi aşamaz (`7/6` hiç yazılmaz).
3. **Her koltuk `runs` taşır**: o koltuğun canlı koşuları, başlangıç sırasıyla, her biri
   `{task_id, task_title, since}`. Çalışan koltuğunda en çok bir tane; lead / researcher /
   integrator / inspector koltuğunda o rolün HER canlı koşusu. Koltuğun kendi `task_id` /
   `task_title` / `since` alanları İLK koşunundur: eski okuyucular (sesli özet dahil) bozulmaz.
   Canlı koşusu olmayan koltukta `runs` boştur; bekliyor / döndü kuralları aynı kalır ve
   "döndü" kuralı boş çalışan koltuklarını bugünkü gibi sırayla doldurur (artık dördüncüye kadar).
4. **Sayfa gönderileni çizer.** Sayfada sekizlik sabit liste yok: koltuk adı kimlik deseninden
   çıkar (`worker-<n>` -> `Çalışan <n>`, bilinen beş rol kendi adı). Tanınmayan bir koltuk
   kimliği, başında kimse olmayan düz bir masa olarak ve kimliğiyle çizilir; sayfa çökmez.
   `SeatId` artık `string`, `runs` tipte isteğe bağlı (eski API cevabı eskisi gibi çizilir).
5. **Birden çok koşusu olan koltuk** `çalışıyor ×3` rozetini (hareket tercihi ne olursa olsun)
   ve ilk işin başlığını gösterir; `aria-label` "…, çalışıyor, 3 koşu" der. Sağ panel önce
   "Koşan işler (3)" listesini (başlık · saat), altında ilk işin kartını ve raporunu gösterir.
   Tek koşulu koltukta liste ve rozet yoktur - bugünkü görünüm.
6. Üst çubuk `running_agents` / `capacity` değerlerini API'den okur (zaten öyleydi; artık
   testle kilitli: sayfa koltuk saymaya dönerse kırmızı).

## Sonuçlar

- Dokuz koltuk, office-page-polish'in ızgarasında 4+4+1 dizilir (sahip tek başına üçüncü
  satırda); altı çalışanla 4+4+3. Izgaraya dokunulmadı.
- `model-policy-api` ve `model-policy-office-ui` bu şeklin üstüne kurulur: koşu başına bilgi
  `runs[i]`'ye, koltuk başına bilgi koltuğa eklenir.
- **Alan dışı, lead için:** `app/team/speech.py` (sesli özet) hâlâ çalışan KOLTUKLARI sayıyor
  ("altı kişiden bir çalışan" - üç denetim koşarken). `cycle.running_agents` okumalı; ayrı kart.
- Durum şeması (`StatusRequest`) yuva sayısı taşımıyor; taşıdığında `capacity` ondan okunur.

## Reddedilenler

- *Rol koltuklarını da çoğaltmak (inspector-1..3):* sıra sözleşmesini ve sayfanın yerleşimini
  bozar; görev kartı tek koltuk + `runs` istedi.
- *Çalışan koltuklarını altıda sabitlemek:* boş ofiste iki ölü masa; yedinci koşu yine kaybolur.
