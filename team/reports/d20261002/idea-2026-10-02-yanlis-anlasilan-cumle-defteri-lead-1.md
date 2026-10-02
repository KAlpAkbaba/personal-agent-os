Bölme dosyası yazıldı: `team/plans/d20261002-split-idea-2026-10-02-yanlis-anlasilan-cumle-defteri.json`, dört kart. Önerinin iki parçası bu bölmeye giremedi (aşağıda); hiçbir komut koşmadım, başka dosyaya dokunmadım.

**Kartlar**
1. **`misheard-ledger-store`** — yeni paket `services/api/app/voice/misheard/` (`__init__.py` bu kartta), `misheard_utterances` tablosu ve migration `0064`, tek yazan işlev, 30 günlük süpürme, sahibin API'si (`GET/POST/DELETE /v1/voice/misheard`). Birim testleri ve gerçek PostgreSQL testi alanda.
2. **`misheard-relay-wiring`** (1'e bağlı) — dört koşulun `realtime_sessions/service.py` içine bağlanması, ücretli ve yerel oturumda. Denetim satırı, ledger ve `context_json` sözsüz kalır; defterin hatası turu bozmaz.
3. **`misheard-collector`** (1'e bağlı) — `collect-stt-corpus.ps1` üçüncü satır türünü (`misheard`) okur, sahibin yazdığı anlamı öneriye taşır. Derlem dosyası yine elle yazılır.
4. **`misheard-page`** (1'e bağlı) — `/core/misheard` sayfası: anlamı yaz, satırı sil, "Defteri unut"; `CoreControls.tsx` içinde tek bağlantı.

**Neden bu bölme**
- Sıra 1 → (2, 3, 4 paralel): sözleşme metni dört kartta harfi harfine aynı, depo main'e girince üç koltuk dolu.
- 3 ve 4'ü depoya bağladım, çünkü kanıtları gerçek tabloya dayanıyor: toplayıcı testi betiğin kendi `-ShowQuery` SQL'ini koşar, sayfa testi `routes.py` ve `models.py` metnini okur.
- Alınmış alanların hiçbirine girilmedi; API kendi yönlendiricisinde, sayfa kendi dizininde.
- Gece raporundaki iki sayı (türetilmiş / gerçek) zaten var (`stt_harness.py`, `by_origin`); kart açılmadı.

**Bu bölmeye giremeyenler (sonraki kartlar)**
- **Sesli "defteri unut" ve "Dün neyi yanlış anladın?"**: `intents.py` üç, `tools.py` bir onaylı kartın alanında. O zamana kadar silme, sayfadaki düğme ve API ile yapılır.
- **Listenin Onay Merkezi sayfasına gömülmesi**: `apps/web/app/core/approvals`, `owner-trials-page` kartının alanında. Şimdilik ayrı sayfa.

**Öneriden sapan üç karar (ADR'lere yazılacak)**
- **"Niyet bulunamadı" koşulunu daralttım:** satır yalnız anlama katmanı en az bir aday gördüyse ya da cümle bir makine adı andıysa yazılır. Koşul olduğu gibi kalsaydı ücretli kipte her sohbet cümlesi deftere düşerdi. ADR'ye "sahip incelemesi bekliyor" diye yazılır, sahip genişletebilir. Bedeli: adayı olmayan anlaşılmamış bir cümle (önerinin "Maillerime bakın" örneği böyle çıkabilir) deftere düşmez; bunu ölçen bir kabul maddesi koymadım.
- **Sonradan gelen koşullar için cümle süreç belleğinde tutulur:** itiraz ve araç hatası cümleden sonra gelir; cümle 120 saniye bellekte durur, diske, `context_json`'a ya da loga yazılmaz. Bedeli: o arada süreç yeniden başlarsa o aday kaybolur.
- **"Sadece dinle" kipi kodda yok** (ADR-0171'in üçüncü adımı yapılmamış). Koruma depoda bir parametre ve relay'de tek bir yardımcıyla kanıtlanır; gerçek kipe karşı kanıt iddia edilmez.

**Açık kalanlar**
- ROADMAP "Approved ideas" satırı bu koşuda yazılamadı (tek dosya yazma izni); dört kart kimliğiyle sonraki lead koşusuna borç.
- Depo kartı migration taşıdığı için yayını sahibine sorulur (ADR-0214 ek 9).
- Başka bir kart önce `0064` numarasını alırsa depo kartı sessizce yeniden adlandırmaz, `ALAN_ISTEGI` ile döner.
