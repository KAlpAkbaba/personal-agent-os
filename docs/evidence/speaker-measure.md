# Ses-izi motoru ölçümü (speaker-engine-measure)

YALNIZ ÖLÇÜM: `app/voice/speaker.py`'ye, konuşma kayıtlarına, hiçbir profile, ayarlara ve web'e dokunulmadı; API'ye bağımlılık eklenmedi. Modeller kendi konteynerinde (`pagentos-speaker-measure`, `pip install --require-hashes`), `--network none --read-only` ile, her dosyanın sha256'sı yüklemeden önce denetlenerek çalıştı.
Bantlar `SpeakerThresholds`'tan okundu: sahip kabul ≥ 0,75, sahip-değil ≤ 0,45; skor `speaker.py`'nin kosinüsüyle aynı aritmetik. DER yakası 0,25 s.

## Karar (yalnız sayılardan)

ev-pc: sahibi konuktan en düşük EER ile ayıran model campplus-zh-en-advanced (EER %0,00, eşik 0,671; 0,75 altında kalan aynı-kişi skoru 28/930, 0,45 üstündeki farklı-kişi skoru 0/440). ev-pc: gerçek zamana (en yüksek RTF 0,862 < 1) ulaşıldı. cpx32-bicimi: sahibi konuktan en düşük EER ile ayıran model campplus-zh-en-advanced (EER %0,00, eşik 0,671; 0,75 altında kalan aynı-kişi skoru 28/930, 0,45 üstündeki farklı-kişi skoru 0/440). cpx32-bicimi: gerçek zamana (en yüksek RTF 0,297 < 1) ulaşıldı. Benimseme kararı burada verilmez: bu sayılar yalnız ölçümdür. Konuğun sesi tek bir kişidir, bir nüfus değildir; tek konuğa karşı EER zayıf bir sayıdır.

## ev-pc

- Tür: ev PC'si (tüm çekirdekler); CPU: Intel(R) Core(TM) i7-14700KF; iş parçacığı: 28; imaj: `pagentos-speaker-measure:2268afba9917 sha256:cf39f4e1e0550255b049290bff078007ce1939e15472c04a18bfed9167281d98`; ölçüm: 2026-10-06T22:06:43Z.
- Sesler: sentetik ses; konuk rızası: var (consent.json); sahip cümlesi: 20; sahip uzun kaydı: 111,6 s; konuk: 66,6 s; doğal konuşma: 65,4 s.

Model | EER % | EER eşiği | aynı kişi < 0,75 (n/N) | farklı kişi > 0,45 (n/N) | belirsiz pay | gömme RTF p95 | tepe bellek MB | yükleme ms
---|---|---|---|---|---|---|---|---
campplus-zh-en-advanced | 0,00 | 0,671 | 28/930 | 0/440 | 0,020 | 0,025 | 375 | 1189
eres2netv2-zh-cn | 0,00 | 0,764 | 0/930 | 2/440 | 0,002 | 0,128 | 1115 | 1200
campplus-voxceleb | 38,18 | 0,461 | 691/930 | 169/440 | 0,376 | 0,023 | 384 | 1120

Diyarizasyon (pyannote-seg-3-0 bölütleyici × gömme modeli):

Bölütleyici | model | girdi | küme | DER % | bulunan konuşmacı | RTF
---|---|---|---|---|---|---
pyannote-seg-3-0 | campplus-zh-en-advanced | eklenti (doğal değil) | 2 | 11,60 | 2 | 0,195
pyannote-seg-3-0 | campplus-zh-en-advanced | eklenti (doğal değil) | esik | 15,51 | 3 | 0,220
pyannote-seg-3-0 | campplus-zh-en-advanced | konuşma (doğal) | 2 | 8,75 | 2 | 0,233
pyannote-seg-3-0 | campplus-zh-en-advanced | konuşma (doğal) | esik | 8,75 | 2 | 0,174
pyannote-seg-3-0 | eres2netv2-zh-cn | eklenti (doğal değil) | 2 | 11,60 | 2 | 0,810
pyannote-seg-3-0 | eres2netv2-zh-cn | eklenti (doğal değil) | esik | 11,60 | 2 | 0,862
pyannote-seg-3-0 | eres2netv2-zh-cn | konuşma (doğal) | 2 | 8,75 | 2 | 0,636
pyannote-seg-3-0 | eres2netv2-zh-cn | konuşma (doğal) | esik | 8,75 | 2 | 0,653
pyannote-seg-3-0 | campplus-voxceleb | eklenti (doğal değil) | 2 | 54,56 | 2 | 0,175
pyannote-seg-3-0 | campplus-voxceleb | eklenti (doğal değil) | esik | 60,25 | 6 | 0,191
pyannote-seg-3-0 | campplus-voxceleb | konuşma (doğal) | 2 | 49,55 | 2 | 0,222
pyannote-seg-3-0 | campplus-voxceleb | konuşma (doğal) | esik | 63,76 | 6 | 0,209

Dinleme (deponun DIŞINDA): `C:\Users\alpak\AppData\Local\PagentOS\speaker-measure\ev-pc` - `eklenti.wav` + `eklenti-zaman.txt`, `konusma.wav` + `konusma-zaman.txt` (varsa).

## cpx32-bicimi

- Tür: VEKİL (cpx32 biçimi, ev PC'sinde `--cpus 4`; sunucunun kendisi değil); CPU: Intel(R) Core(TM) i7-14700KF; iş parçacığı: 4; imaj: `pagentos-speaker-measure:2268afba9917 sha256:cf39f4e1e0550255b049290bff078007ce1939e15472c04a18bfed9167281d98`; ölçüm: 2026-10-06T22:10:05Z.
- Sesler: sentetik ses; konuk rızası: var (consent.json); sahip cümlesi: 20; sahip uzun kaydı: 111,6 s; konuk: 66,6 s; doğal konuşma: 65,4 s.

Model | EER % | EER eşiği | aynı kişi < 0,75 (n/N) | farklı kişi > 0,45 (n/N) | belirsiz pay | gömme RTF p95 | tepe bellek MB | yükleme ms
---|---|---|---|---|---|---|---|---
campplus-zh-en-advanced | 0,00 | 0,671 | 28/930 | 0/440 | 0,020 | 0,009 | 363 | 944
eres2netv2-zh-cn | 0,00 | 0,764 | 0/930 | 2/440 | 0,002 | 0,050 | 1105 | 894
campplus-voxceleb | 38,18 | 0,461 | 691/930 | 169/440 | 0,376 | 0,008 | 371 | 820

Diyarizasyon (pyannote-seg-3-0 bölütleyici × gömme modeli):

Bölütleyici | model | girdi | küme | DER % | bulunan konuşmacı | RTF
---|---|---|---|---|---|---
pyannote-seg-3-0 | campplus-zh-en-advanced | eklenti (doğal değil) | 2 | 11,60 | 2 | 0,076
pyannote-seg-3-0 | campplus-zh-en-advanced | eklenti (doğal değil) | esik | 15,51 | 3 | 0,075
pyannote-seg-3-0 | campplus-zh-en-advanced | konuşma (doğal) | 2 | 8,75 | 2 | 0,070
pyannote-seg-3-0 | campplus-zh-en-advanced | konuşma (doğal) | esik | 8,75 | 2 | 0,068
pyannote-seg-3-0 | eres2netv2-zh-cn | eklenti (doğal değil) | 2 | 11,60 | 2 | 0,283
pyannote-seg-3-0 | eres2netv2-zh-cn | eklenti (doğal değil) | esik | 11,60 | 2 | 0,297
pyannote-seg-3-0 | eres2netv2-zh-cn | konuşma (doğal) | 2 | 8,75 | 2 | 0,255
pyannote-seg-3-0 | eres2netv2-zh-cn | konuşma (doğal) | esik | 8,75 | 2 | 0,273
pyannote-seg-3-0 | campplus-voxceleb | eklenti (doğal değil) | 2 | 54,56 | 2 | 0,072
pyannote-seg-3-0 | campplus-voxceleb | eklenti (doğal değil) | esik | 60,25 | 6 | 0,072
pyannote-seg-3-0 | campplus-voxceleb | konuşma (doğal) | 2 | 49,55 | 2 | 0,069
pyannote-seg-3-0 | campplus-voxceleb | konuşma (doğal) | esik | 63,76 | 6 | 0,068

Dinleme (deponun DIŞINDA): `C:\Users\alpak\AppData\Local\PagentOS\speaker-measure\cpx32-bicimi` - `eklenti.wav` + `eklenti-zaman.txt`, `konusma.wav` + `konusma-zaman.txt` (varsa).

## Notlar

- cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)
- 'eklenti' konuşma doğal değildir: sahip ve konuk kayıtlarından 2-8 s'lik dönüşler, 0,3-1,0 s sessizlikle sabit tohumla eklendi; doğruluk yapı gereği bilinir, örtüşme yok.
- 'küme 2' = konuşmacı sayısı verildi (ana satır); 'esik' = sayı verilmedi, eşik 0,5.
- EER tek bir konuğa karşıdır: konuğun sesi tek bir kişidir, bir nüfus değildir.
- Yayımlanmış EER'ler PyTorch özgünlerine aittir; bu satırlar ONNX dosyalarınındır.
- Plan: STOP-1 (gate): upstream `pyannote/segmentation-3.0` is gated (`gated: "auto"`, HF account + contact-info form). - Ölçülen dosya k2-fsa'nın kapısız MIT ONNX kopyası; kart bölütleyiciyi ölçüme koydu (benimseme kartı bunu yeniden okur).
- Plan: STOP-CHECK-2 (training data, not licence class): eğitim verisinde yalnız-araştırma setleri var; özel, tek sahipli ölçüm için kabul, benimsemeden önce yeniden okunur.
- Silindi: vektörler yalnız konteynerin belleğindeydi ve koşu bitince silindi; hiçbir profile yazılmadı; depoda ses yok; indirilen kayıtlar ve geçici klasörler silindi. Dinleme dosyaları yalnız yukarıdaki klasörde, sahip dinledikten sonra siler.

Benimseme ve bağlantı (mikrofon → bölütleyici → STT → kayıt) ayrı sahip kararlarıdır.
