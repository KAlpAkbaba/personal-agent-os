## Şu an üzerinde çalışılan
- Görev: `understanding-rules-read-lemmas` (ikinci tur, denetçinin üç noktası). Alan: `intents.py`, `normalize.py`, `test_understanding_rules_read_lemmas.py`, ADR metni. Makine: sahibin geliştirme PC'si, worktree `worker-understanding-rules-read-lemmas`.

## Rapor
- **sha:** `9756e9eafa135debc4db7baa55aad19b0fc69528`, itildi, worktree temiz. 4 dosya değişti, hepsi alan içinde.

**1. Gerileme düzeltildi**
- Bir tablonun sahip olduğu cümle başka bir niyete ulaşan katman 1 okumasıyla artık ezilmiyor; kibar biçim ve bitişik kelime için aynı kural. Cümle duyulduğu gibi kalıyor (1.0, onarım yok).
- Tek istisna `intents._asks_with_no_verb_of_its_own`: yüzey okuması bir soruysa (`query`), cümle tek tümceyse ve kibar biçim dışında fiil yoksa emir kipi kazanır. "Kendi kendini geliştirmeyi duraklatın." hâlâ `evolution_pause` 0.9.
- RED → GREEN: düzeltmeden önce yeni testlerde 15 başarısız, 6 geçti. Denetçinin üç cümlesi taban niyetlerinde; yanlarında sekiz cümle daha var (bitişik "Şunu hatırla: alarmkur" dahil).
- Kendi sondam (283 cümle): tabana göre niyet değiştiren 49 cümleden 2'si kaldı, ikisi de istenen evolution cümlesi.

**2. Test edilmemiş iki koruma**
- `keeps_slots`: "Şuraya ışıkları söndürün yazar mısın?" sahibin sözünü yazıyor ("ışıkları söndürün"). Eski yanlış adlı test yeniden adlandırıldı.
- Mail/takvim tablosunun sahip olduğu cümle yeniden okunmuyor: üç cümle (`calendar_propose`, `calendar_agenda`, `mail_read`), güven de değişmiyor.

**3. ADR:** karar 2 yeniden yazıldı (yanlış öncül, üç koşul, kapsanmayan durum); karar 5 ve 6 güncellendi.

**Mutasyonlar:** 11'inin 11'i RED; her biri yedek kopyadan geri yüklendi, sha256 önce ve sonra eşit.
- Sahipli cümle koruması kaldırıldı; üç koşulun her biri ayrı ayrı kaldırıldı; tablo-fiil ve katman-1-fiil kontrolleri ayrı ayrı kaldırıldı.
- `keeps_slots = False`; mail/takvim koruması kaldırıldı; "açıkla" sözlükten çıkarıldı.
- Kartın iki mutasyonu: olumsuz biçim koruması (`_says_dont` ve `is_negative`) → RED; bilinen kelime bölünebilir → RED.
- Mutasyonlar `ruff format` öncesi düzende koşuldu (`intents.py` `cf29f6e7…`); commit'teki dosya `d9c42028…`, fark yalnızca biçim.

**Kanıt (PROVEN_AUTOMATED)**
- STT korpusu: `total_cases 106, correct 98, acted 97, questions 1, not_understood 2, wrong_device_actions 0, wrong_device_observable_cases 11, confident_wrong_readings 6, correct_rate 0.9245, BELOW_TARGET`. Kibar 29/29, bitişik 24/29, aksan 24/24, uydurma ek 18/21. `KNOWN_GAPS` 8, değişmedi.
- Sahip korpusu, tek süreçte, commit'teki kaynaklarla: 2756 geçti, 0 başarısız (2754 durum + 2 toplam), 22 dk 24 sn.
- Dört görev dosyası (iki birim dosyası, STT, regresyonlar): 362 geçti, 1 xfail (%95 hedef testi). `ruff check` ve `format` temiz.

**Koşmadıklarım**
- NOT_RUN: tam birim paketi ve `quality-gate.ps1`.
- READY_FOR_OWNER: kibar cümlelerin sesle denenmesi.

**Açık riskler**
- "açıkla" katman 1'in fiil listesine eklendi ("Şunu açıkla gözünü kapatınız" koruması için gerekliydi). Yan etkisi: "açıklayın / açıklar mısın" artık "açıkla" olarak okunuyor, ve tümce sonundaki "açıklama" bir yasak sayılıp o cümlede yeniden okumayı kapatıyor.
- Hiçbir tablonun sahip olmadığı cümlede yalın emrin zayıflığı sürüyor: "Şunu not et: ekranları kapatın." `display_off` 0.9, çünkü yalın hali bu dalda `display_off`. "Ekranları kapatın demedim." de aynı. ADR'de yazılı.
- Tümce kesme koruması noktalamaya bakıyor; STT noktalama yazmazsa geriye fiil kontrolü kalır, o da yalnızca tabloların ve katman 1'in bildiği fiilleri tanır.
- Önceki turdan lead'e kalanlar aynen duruyor: `policy.rule_reading` bitişik okumayı 0.9 sayıyor, ve `test_stt_utterance_corpus.py` içindeki xfail gerekçesi hâlâ "73/106" diyor. İkisi de alan dışı.
