## Şu an üzerinde çalışılan
`understanding-rules-read-lemmas` (ADR-0224 ek 4, adım 1) — alan: `app/voice/intents.py`, `app/voice/understanding/normalize.py`, iki test dosyası, `stt_corpus.py`, ADR metni — makine: sahibin geliştirme PC'si, worktree `worker-understanding-rules-read-lemmas`. İş bitti, dal itildi, ağaç temiz.

**Sonuç:** STT ölçümü 73/106 (%68,9) → **98/106 (%92,45)**; bu adımın çıtası tuttu, sahibin %95 hedefi hâlâ tutmuyor. İki kabul satırında karttan sapma var (aşağıda).

- **sha:** `9c211032115ef6f2bdec2926b29e3fff70a75461` (kod `1d30ee3c`'de; ikinci commit yalnızca ADR metni). 6 dosya, hepsi alanın içinde.
- **Rapor bloğu (`1d30ee3c`):** `total_cases 106, correct 98, acted 97, questions 1, not_understood 2, wrong_device_actions 0, wrong_device_observable_cases 11, confident_wrong_readings 6, correct_rate 0.9245, summary BELOW_TARGET`.
- **Bozulmaya göre:** kibar 29/29, bitişik 24/29, aksan 24/24, uydurma ek 18/21, gerçek 3/3.
- **KNOWN_GAPS:** 33 → 8; 25 çıktı, giren yok.
- **Sahip korpusu (`1d30ee3c`):** 2754/2754, `HEALTHY`, 2756 test geçti. Makinede başka bir pytest koşarken 66 dakika sürdü.
- **Diğer koşular:** STT + regresyon + yeni testler 343 geçti, 1 xfailed (hedef testi); yönlendirici/anlama dosyaları 1020 geçti; yönlendiriciye dokunan 73 dosya daha 1905 geçti; ruff temiz.
- **RED → GREEN:** uygulamadan önce 78 kırmızı / 90 yeşil, sonra 171 yeşil. Yeni dosya `test_understanding_rules_read_lemmas.py`; `test_understanding_normalize.py` genişledi.
- **Yolda bulunan hata:** "Bugünün Show Ana Haber videosunu aç." `bu + günün` diye bölünüyordu; "bugün" kendi başına kelime oldu. 2754 korpus cümlesinin hiçbirinin bölünmediğini tutan bir test eklendi.

**Mutasyonlar** — altısı da RED; her biri yedek kopyadan geri yüklendi, sha256 önce/sonra aynı (`normalize.py e472d80c…`, `intents.py 976c5102…`):
- M1 olumsuz biçim koruması kaldırıldı → 2 kırmızı
- M2 "bilinen kelime bölünmez" kaldırıldı → 5 kırmızı
- M3 yönlendirici katman 1'e sormuyor → 28 kırmızı
- M4 mail/takvim eylem koruması kaldırıldı → 1 kırmızı
- M5 fiil bölmenin ilk yarısı olabiliyor → 4 kırmızı
- M6 tabloda yazılı kibar biçim yeniden okunuyor → 1 kırmızı

**Kanıt sınıfı:** PROVEN_AUTOMATED (iki korpus ve mutasyonlar). PROVEN_REAL **NOT_RUN** — sahibin kibar cümleleri sesle denenmedi. Tam birim paketi (14 693) ve kapı **NOT_RUN**.

**Karttan sapmalar — lead karar vermeli:**
1. **"Raporu okuyun." 0.9 değil, 1.0 kaldı.** Kart bu cümlenin düştüğünü varsayıyor; ölçtüm, düşmüyordu: "okuyun" araştırma-okuma tablosunda yazılı (ADR-0184). Kartın kendi kuralını ("tablo aynen eşleşirse yüzey kazanır, 1.0") izledim; kabul satırı bu cümle için 0.9 istiyordu. "Alarmı kurar mısınız?" da düşmüyordu; soru biçimi hiçbir tabloda olmadığı için artık 0.9.
2. **Mail/takvim koruması daraltıldı.** Eski onarımlar bu ailelere hiç girmiyordu. Yeni okuma yalnızca sorgu sınıfına giriyor ("Maillerime bakın." → `mail_inbox`, "Fatura maillerini bulun." → `mail_search`); "Gönderin." / "Gönderir misin?" hâlâ gönderim olmuyor. Kibar 29/29 bu iki cümle olmadan ulaşılamıyordu.

**Açık riskler:**
- **Bitişik kelime HIGH bandında çalışıyor.** `ResolvedIntent.confidence` 0.75, ama `policy.rule_reading` her onarımı 0.9'a çeviriyor; geri okuma (MEDIUM) için orada tek satır gerekir. Alan dışı olduğu için dokunmadım.
- **Kelime listesi korpus bilinerek seçildi.** Bölme için 28 işlev kelimesi ve 6 isim, kibar biçimler için 27 tablo fiili eklendi. Sahte bölmeye karşı tek güvence yukarıdaki korpus testi.
- **Olumsuz koruması tutucu.** "Yapma" diyen cümlede katman 1 hiç okuma vermiyor; cümle modele kalıyor.
- **"yazın" eşsesli.** Hem "yaz" fiilinin kibar biçimi hem "yaz mevsiminde"; yalnızca bir tablo cümleyi sahiplenirse yön değiştirir.

**Yapılamayan — kalan 8 vaka:**
- 5'i başka niyetin kuralına HIGH'da tam eşleşiyor (3 uydurma ek, 2 bitişik); iş `understanding-confident-wrong`'un.
- 2 bitişik vakanın bir yarısı sözlükte yok ("Uyurkenekranları", "Faturamaillerini"); katman 2'nin işi.
- "Saat yedibuçukta beni uyandır." yönlendiricide artık doğru okunuyor, ama alarm aracı saati ham cümleden ayrıştırıp reddediyor (`when_unparsed`); düzeltme relay/araç tarafında.

**Lead için birleştirmede:**
- `test_stt_utterance_corpus.py` içindeki xfail gerekçesi hâlâ "73/106 = 68.9 %" diyor (alan dışı).
- ADR metni `team/plans/understanding-rules-read-lemmas-adr.md` içinde, numarasız.
