# ADR (numara lead'de): Bozuk Türkçe kapıda - kural katmanı (`app/team/mojibake.py`)

Tarih: 2026-10-04 · Kart: mojibake-write-rule · Öneri: team/proposals/2026-10-03-bozuk-turkce-kapida-dursun.md, bölüm (1), kural yarısı

## Bağlam
2026-10-03'te 28 ekip kaydının Türkçesi BOM'suz `.ps1` yüzünden bozuldu ("Proje YÃ¶neticisi"):
Windows PowerShell 5.1 BOM'suz dosyayı cp1252 okur, UTF-8 baytları ikişer/üçer Latin harfe döner.
Kayıtlar elle onarıldı; kapıda bir kural yoktu.

## Karar
1. **Tek harf değil, ÇİFT.** "São Paulo", "Ãlvaro", "ÃO" gerçek adlardır; tek başına `Ã`, `Ä`, `Å`
   (ya da `â`) ASLA isabet değildir. İsabet yalnız UTF-8'in cp1252 okunuşunun tam dizisidir.
2. **Liste ve kaynağı** (`MOJIBAKE_PAIRS`, sıralı): `harf.encode("utf-8").decode("cp1252")`:
   `Ã¶ ö`, `Ã¼ ü`, `Ã§ ç`, `Ã– Ö`, `Ãœ Ü`, `Ã‡ Ç`, `Ä± ı`, `Ä° İ`, `ÄŸ ğ`, `Äž Ğ`, `ÅŸ ş`, `Åž Ş`,
   ve noktalama: `â€” —` (uzun tire), `â€“ –` (kısa tire), `â€œ “`, `â€™ ’` (kıvrık tırnaklar; ekip
   metinlerinde sık, üç baytlık dizileri gerçek metinde rastlantıyla çıkmaz). `”` (E2 80 9D) bilerek
   yok: 0x9D cp1252'de tanımsız, PowerShell'in okuması ortama göre değişir. Test her çifti kendi harf
   listesinden üretir (beklenen taraf modülden kopyalanmaz) ve modüldeki her çifti geri üretir.
3. **En erken isabet**: tek regex, uzun çiftler önce; `find_mojibake` -> `MojibakeHit(pair, correct, offset)`.
4. **`scan_payload`**: str/dict/list/tuple'ı yineleme OLMADAN (yığınla) gezer, ilk isabeti noktalı
   yolla verir (`sections.0.text`); dict ANAHTARLARI da taranır, anahtardaki isabetin yolu
   `<üst>.[anahtar N]` (anahtarın metni yankılanmaz). Döngü güvenli (id kümesi), derinlik
   `MAX_DEPTH = 64` ile sınırlı (daha derini taranmaz; 10 000 derinlik RecursionError vermez).
5. **Ret metni yankılanmaz (KVKK).** `mojibake_detail` yalnız `code='mojibake'`, `field`, `pair`,
   `correct` ve şu kalıpta Türkçe `reason` döner: "metin bozuk kodlanmış görünüyor: 'Ã¶' → 'ö'
   olmalı; betiği BOM'lu kaydet". Reddedilen metin sahibin kişisel verisini taşıyabilir; ne
   yanıtta ne logda yer alır. Yol (alan adı) yalnız şemanın alan adlarını/indislerini taşır.

## Bağlama kartı ne yapacak (bu kart HİÇBİR şey bağlamadı)
`services/api/app/team/routes.py` içindeki dört yazma ucu - `put_task`, `post_report`,
`post_proposal`, `put_status` - gövdeyi doğruladıktan sonra, HERHANGİ bir store yazımından ÖNCE:
```python
found = scan_payload(body.model_dump())
if found is not None:
    raise HTTPException(status_code=422, detail=mojibake_detail(*found))
```
Panonun not gönderimi (board / routes_board, `find_note_secret` denetiminin hemen yanında) aynı
çağrıyı not metni ve alanları için yapar. Okuma uçları değişmez; mevcut satırlara dokunulmaz
(geçmiş kayıtlar ayrı, salt-okunur bir sorguyla ölçülür). 422 gövdesi loglanırken de yalnız
`code/field/pair` yazılır.

## Ölçü
Öneri ölçüsü: döngü raporunda **döngü başına 422 mojibake sayısı** (bağlama kartı sayar). Hedef:
üretim deposunda sıfır `Ã¶`/`Ä±`/`ÅŸ` satırı (salt-okunur SSH sorgusu) ve kasıtlı bir BOM'suz
yazımın 422 alması - PROVEN_REAL bağlama kartına aittir.

## Geri alma
Saf modül, hiçbir yere bağlı değil: silmek yeterli. Yanlış pozitif görülürse çift listeden çıkarılır.
