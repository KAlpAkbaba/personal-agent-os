# Öneri: Deneme listesi — üçüncü kapı (gerçek cihaz kanıtı) Onay Merkezi'nde görünsün

Tarih: 2026-10-01 · Araştırmacı · Durum: awaiting_owner
Roadmap: tek bir JARVIS satırı değil, **"Definition of done"un kendisi** — her satır ancak PROVEN_REAL kanıtla HAVE
olur ve o kanıtı yalnız sahip üretir. Öğrenilen ders türünden öneri.

## Ne
Onay Merkezi'ne "fikir" ve "yayın"ın yanına üçüncü liste: **"Dene"**. Yayınlanmış her iş için sahibin söyleyeceği
cümle, hangi makinede söyleyeceği ve ne görmesi gerektiği tek satırda durur. Sahip dener, kabukta "Oldu / Olmadı"ya
basar ya da sesle söyler: "Hesap makinesi denemesi oldu." "Oldu" → lead QUALIFICATION satırını sahibin sözünü alıntılayarak
PROVEN_REAL yazar. "Olmadı" → sahibin söylediği cümleyle kuyruğa bir düzeltme işi düşer.

## Faydası — örneklerle
1. Bugün: 38.3–38.5'in üç cümlesi ("Türkçe oku", "Hesap makinesini aç", "Ofis bilgisayarımdan hesap makinesini aç")
   yalnız HANDOFF'un 54. satırında, düz yazı içinde. Son üç döngü raporunun "Sahibin gerçek cihazda deneyecekleri"
   bölümü üçünde de "Yok".
   Bununla: Onay Merkezi'nde "Dene (3)" görünür; her satırda cümle + "MAIL'de" / "MAIL'den, ofiste açılacak".
2. Bugün: anlatının sesi 2026-09-30'da yayınlandı (`narrative-voice`), ama QUALIFICATION 36.4 ("bu hafta ne oldu")
   hâlâ NOT_YET_PROVEN; kimse sana "bunu bir söyle" demedi.
   Bununla: yayın biter bitmez "Dene: 'Bu hafta ne oldu?' — herhangi bir makinede, 30 sn'lik özet duymalısın" düşer.
3. Bugün: denediğin şeyin sonucu sohbetten lead'e, lead'den QUALIFICATION'a elle taşınıyor (30.10 böyle kapandı);
   olmayanlar da (2026-09-30'daki üç kusur) aynı yoldan kuyruğa girdi.
   Bununla: "Olmadı, 'açamadım' dedi" dediğin an iş kuyrukta; "Oldu" dediğin an satır kapanmaya hazır.

Kazanç: açık deneme sayısı tek yerde ve sayılabilir (bugün QUALIFICATION'da READY_FOR_OWNER / NOT_YET_PROVEN 77 kez
geçiyor); yayın → PROVEN_REAL arası gün sayısı ölçülür hale gelir.
Kazanmadığımız: denemeyi senin yerine yapmaz; ses kalitesi, "JARVIS gibi hissettiriyor" hükmü yine senin.

## Neden şimdi (aynı kusur, üç kez)
- `team/reports/adr0224-02.md`, `office-01.md`, `cycle-2026-10-01.md`: "Sahibin gerçek cihazda deneyecekleri: Yok" —
  aynı gün QUALIFICATION 38.3, 38.4, 38.5 "READY_FOR_OWNER", 36.4 ve 36.7 "NOT_YET_PROVEN", 36.6 "not opened in a
  browser by anyone".
- Neden boş: `scripts/lib/TeamRun.ps1:405` yalnız `awaiting_real_evidence` durumundaki işlerin `owner_trials`
  alanını listeliyor. Bu iki ad şemada var (`queue.schema.json:67,162`) ama `services/api/app` ve `apps/web`
  içinde onları okuyan/yazan kod yok; işler `released`'ta kalıyor. (Canlı kuyruğu okuyamadım; tohum dosyası
  `team/queue.json`'da `owner_trials` taşıyan iş yok — tohum eskidir, kesin değil.)
- TEAM_PROTOCOL 3a.2(c) bu listeyi Onay Merkezi'nde vaat ediyor; ADR-0217 "iki kapı" kurdu, üçüncüsü kurulmadı.

## Nasıl
- Seam: kuyruk şemasındaki hazır alanlar + `/v1/team/queue` + `apps/web/app/core/approvals` + "fikri onayla" ses ailesi.
- Değişen: (a) denetleyici raporundaki READY_FOR_OWNER satırı `owner_trials`'a yapılandırılmış yazılır (cümle,
  makine, beklenen); (b) yayın adımı denemesi olan işi `awaiting_real_evidence`'a taşır; (c) Onay Merkezi "Dene"
  listesi + Oldu/Olmadı; (d) "Oldu" denince sistem o cümlenin o cihazda gerçekten duyulduğunu tur kaydından arar
  ve bulduğunu kanıt olarak iliştirir; (e) açık eski satırlar bir kez lead tarafından girilir.
- Değişmeyen: PROVEN_REAL'i yine yalnız lead yazar, senin sözünü alıntılayarak; hiçbir satır kendiliğinden kapanmaz.

## Maliyet/risk
Efor: küçük-orta (API alanı + sayfa bölümü + betikte iki geçiş + denetleyici rol metni). Çalışma maliyeti: 0.
CPX32: ihmal edilebilir. Lisans/bağımlılık: yok. KVKK: yeni veri yok (cümleler zaten ledger'da). Risk: denemeden
"Oldu" demek — (d) bunu zayıflatır ama tümüyle kapatmaz. Onay Merkezi şu an döngü koşarken karar reddediyor;
bu iş kuyruktaki `proposals-on-cloud-core`'dan SONRA gelmeli.

## Kanıt planı
PROVEN_AUTOMATED: denemesi olan yayınlanmış iş raporda ve API'de görünür (alan okunmazsa KIRMIZI); "Olmadı" bir iş açar.
PROVEN_REAL: Onay Merkezi'nde 38.3–38.5'in üç cümlesini görürsün, MAIL'de denersin, "oldu" dersin; üç satır senin
sözünle PROVEN_REAL olur.

## Karar
Yapalım mı? Alternatifler: (a) yalnız rapora liste (sayfa yok) — en ucuz, ama raporu açmanı ister; (b) bugünkü gibi
sohbetten — çalışıyor, ama satırlar birikiyor ve hangisinin beklediği tek yerde görünmüyor.
