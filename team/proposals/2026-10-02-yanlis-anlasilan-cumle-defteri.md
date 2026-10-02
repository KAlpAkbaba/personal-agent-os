# Öneri: Yanlış anlaşılanlar defteri — anlaşılmayan cümlen yazı olarak saklansın, "ne demek istemiştin?" diye sorulsun

Tarih: 2026-10-02 · Araştırmacı · Durum: awaiting_owner
Roadmap: JARVIS tablosu 1. satır "doğal konuşma" (HAVE, "quality work remains"); sıra 6. ADR-0224'ün hedefi
(STT'nin yazdığı cümlelerde ≥ %95) bugün %68,9 ve gerçek cümle sayısı 3. Öğrenilen ders türünden öneri.

## Ne
Sistem bir cümleni anlamadığında ya da yanlış anladığında, tanıyıcının YAZDIĞI cümle (yalnız yazı; ses değil)
bir deftere düşer: hangi kipte (ücretli / yerel), hangi makinede, hangi güvenle okunduğu ile birlikte. Deftere
düşme koşulları dört tanedir: yönlendirici niyet bulamadı; katman 3 "tek soru" sordu; bir eylemin hemen ardından
"hayır / dur" dedin (ADR-0133'ün zaten işaretlediği yanlış yönlendirme adayı); araç "yapamadım" döndü.
Onay Merkezi'nde yeni bir liste görürsün: **"Ne demek istemiştin?"**. Cevabın ("postamı oku") cümleyi gerçek STT
derlemine taşır. 30 gün sonra kendiliğinden silinir; "defteri unut" dersen hemen silinir.
Senin cümlen: "Dün neyi yanlış anladın?"

## Faydası — örneklerle
1. Bugün: 30 Eylül'de MAIL'de "Ofis bilgisayarımdan hesap makinesini aç" dedin; tanıyıcı "Ofisü bilgisayarında …
   açın" yazdı ve Hesap Makinesi MAIL'de açıldı. Bunu yalnız sen sohbette anlattığın için biliyoruz; kayıtta cümle
   yok, hangi kipte duyulduğu da yok (önceki araştırma raporu da bunu bulamadı).
   Bununla: cümle, kipi, makinesi ve güven bandıyla defterde durur; ertesi sabah derlemde bir satırdır.
2. Bugün: STT derlemi 106 cümle; 3'ü senin gerçekten söylediğin, 103'ü bizim türettiğimiz bozulmalar ("Hesapü
   makinesini aç", "Ekranları kapatın"). %68,9 sayısı bu türetilmiş cümlelerle ölçülüyor.
   Bununla: gece raporu iki sayı verir: "türetilmişte %X, senin gerçek cümlelerinde %Y (N cümle)". Hangi
   düzeltme kartının (kibar kip, bitişik sözcük…) önce yapılacağını senin cümlelerin belirler.
3. Bugün: ücretli kipte anlaşılmayan cümleni model kendince cevaplar, cümle hiçbir yere yazılmaz. Yerel kipte
   yalnız SON anlaşılmayan cümle durur, bir sonraki cümle üstüne yazar (`realtime_sessions/service.py:1968-1978`).
   Toplayıcı betik (`collect-stt-corpus.ps1`) hazır ama üretime karşı hiç koşmadı: okuyacağı bir şey yok.
   Bununla: Onay Merkezi'nde "Ne demek istemiştin? (4)"; "Maillerime bakın" satırına "postamı oku" dersin, biter.

Kazanç: derlemdeki gerçek cümle sayısı 3'ten her hafta ölçülen bir sayıya çıkar; %95 hedefi senin cümlelerinle ölçülür.
Kazanmadığımız: cümleyi kendiliğinden düzeltmez (düzeltmeler ayrı kartlar). Ses saklanmadığı için "tanıyıcı mı
yanlış duydu, cümle mi farklıydı" ayrılamaz; o, onaylı `stt-engines-measure` işinin konusu.

## Neden şimdi (aynı kusur iki kez; ADR'nin kendi notu)
- 2026-09-30 denemen (HANDOFF 88-95. satırlar): "Türkçe oku" cevap kipini `detail` yaptı; "Ofisü bilgisayarında …
  açın" yanlış makinede açtı. İkisi de senin anlatmanla kuyruğa girdi, kayıttan değil.
- ADR-0224 ek 4 (2026-10-01), "Found on the way" 1: üretim senin cümleni tek yerde tutuyor (yerel kip, niyet yok,
  bir tur); ücretli oturum hiç tutmuyor; "collecting paid-session renderings needs a recorded, owner-approved
  capture - a new idea for the owner". Aynı ek: "The collector has not been run against production: the real
  renderings are three."
- Dış tarama bu öneriye bir şey eklemedi; bu, içerideki ölçümün gösterdiği boşluk.

## Nasıl
- Seam: (a) tur kaydı (`last_utterance`, aynı dosya); (b) ADR-0133'ün ledger'a sözsüz yazdığı yanlış yönlendirme
  adayı; (c) ADR-0224 katman 3'ün LOW bandı; (d) `scripts/voice/collect-stt-corpus.ps1` — zaten `needs_owner_meaning`
  durumlu öneri üretiyor; (e) Onay Merkezi (`/v1/team/approvals`, "Detay" ile aynı sayfa).
- Değişen: küçük bir tablo (cümle ≤ 2000 karakter, kip, motor, makine, bant, neden, zaman; yalnız ekleme yapan
  migration), dört koşulda yazan tek işlev, 30 günlük süpürme, "defteri unut" niyeti, Onay Merkezi listesi.
- Değişmeyen: denetim (audit) satırı sözsüz kalır; ses hiçbir yerde saklanmaz; yönlendirici ve katmanlar aynı;
  "sadece dinle" kipinde deftere hiçbir şey yazılmaz. Yeni bağımlılık yok, yeni hesap yok.

## Maliyet/risk
Efor: orta. Çalışma: 0 USD. CPX32: günde birkaç satır, ihmal edilebilir. Lisans: yok.
KVKK: kendi sözlerin yazı olarak 30 gün kendi PostgreSQL'inde (Hetzner NBG1) durur; üçüncü tarafa gitmez. **Açık
risk:** ses kimliği yok (ADR-0171: "voiceprint yerine onay"), yani odadaki başka birinin cümlesi de anlaşılmazsa
deftere düşebilir. Karşılığı: kısa saklama, "unut", listeyi yalnız senin görmen, "sadece dinle"de kapalı olması.
Şirket PC'si: ofiste söylenen cümle şirket makinesine değil senin Cloud Core'una yazılır.
Yayın: migration taşıdığı için otomatik yayın kuralının istisnası (ADR-0214 ek 9) — yayında sana sorulur.

## Kanıt planı
PROVEN_AUTOMATED (gerçek relay + dev stack PostgreSQL): anlaşılmayan cümle ücretli ve yerel oturumda satır yazar;
anlaşılan yazmaz; "sadece dinle" yazmaz; "unut" siler; 31. gün süpürülür; toplayıcı satırı öneriye çevirir.
PROVEN_REAL: MAIL'de ücretli kipte beş cümle söylersin, biri bilerek tuhaf; Onay Merkezi'nde onu görür, anlamını
yazarsın; ertesi gece raporunda gerçek cümle sayısı 3'ten 4'e çıkar.

## Karar
Yapalım mı? (Onayın aynı zamanda cümlelerinin yazı olarak 30 gün saklanmasına izindir.) Alternatifler: (a) yalnız
yerel kipte sakla — ücretli kipin cümleleri yine kaybolur; (b) yalnız say, sözü saklama — bugünkü durum, derlem
büyümez; (c) sesi de sakla — önermiyorum, KVKK yükü ağır ve `stt-engines-measure` kendi kayıtlarını kullanıyor.
