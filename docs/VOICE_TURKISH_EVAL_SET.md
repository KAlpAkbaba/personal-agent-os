# Turkish Voice Evaluation Seed Set

Claude should expand this into a machine-readable benchmark dataset. These examples are seed coverage, not the entire final set.

## General Turkish

1. Bugün hazırladığım raporun yalnızca önemli bölümlerini oku.
2. Araştırmayı bitirdiğinde bana kısa bir yönetici özeti ver ve bekle.
3. İkinci başlığa geçmeden önce son paragrafı tekrar oku.
4. Bu konuyu ayrıntılı anlatma; karar vermem için bilmem gerekenleri söyle.
5. Hazırladığın belgeyi PDF olarak telefonuma gönder.

## Dates and time

6. Toplantı 31 Ağustos 2026 tarihinde saat 15.30'da başlayacak.
7. 1 Eylül 2026 Salı sabahı saat 08.05'te kontrol et.
8. Rapor 2026-08-31 14:42:18 zaman damgasıyla oluşturuldu.

## Money and percentages

9. Toplam maliyet 1.250.000 Türk lirası ve geçen aya göre yüzde 17,2 arttı.
10. Kur farkı yüzde 3,42 seviyesine geriledi.
11. Tahmini aylık gider 49,95 avro ile 79,90 avro arasında.

## Networking and technical

12. Sunucunun IP adresi 192.168.10.25 ve ağ maskesi /24.
13. 10.20.0.0/16 ağı yalnızca yetkilendirilmiş güvenlik testleri için kayıtlıdır.
14. PostgreSQL 17 bağlantısı 5432 numaralı port üzerinden yapılıyor.
15. Redis 6379 portunda yalnızca private network üzerinde dinliyor.
16. API yanıtı HTTP 503 Service Unavailable döndürdü.
17. TLS 1.3 bağlantısında sertifika zincirini kontrol et.
18. GPU üzerinde CUDA 12 ve cuDNN sürümlerini doğrula.
19. Git commit kimliği a1b2c3d4 ile başlayan sürümü geri al.
20. Dosya yolu C:\AI\personal-agent-os\artifacts\report.pdf.

## Mixed Turkish-English

21. Browser Agent önce DOM ve accessibility tree kullanmalı, vision fallback en son devreye girmeli.
22. Research Engine kaynakları deduplicate etmeli ve source confidence puanı üretmeli.
23. Device Broker outbound WebSocket bağlantısını yeniden kurdu.
24. Last-known-good release otomatik rollback için hazır.
25. Task Completed hook testler geçmeden görevi kapatmamalı.

## Abbreviations

26. API, SQL, GPU, CPU ve RAM terimlerinin kişisel telaffuz sözlüğünü kullan.
27. PDF ve DOCX çıktısını oluştur, HTML sürümünü arşivde tut.
28. STT ve TTS aynı model olmak zorunda değildir.
29. WER değeri test veri setinde ayrı raporlanmalı.
30. UI Automation başarısız olursa vision katmanına geç.

## Long narration paragraph

31. Bu sistem tek kullanıcı için tasarlandığından, karmaşık bir rol ve tenant yönetimi kurmak yerine sahibin cihaz kimliği, aktif oturumu ve ses doğrulama sinyallerini birleştiren sade bir Owner Identity modeli kullanılacaktır. Sistem günlük işlerde onay istemeyecek; ancak bir hedef henüz yetkilendirilmiş varlık listesinde değilse kapsamı kendiliğinden genişletmeyecektir.

32. Araştırma görevi tamamlandığında kanonik rapor Markdown tabanlı semantik yapıda saklanacak, PDF ve DOCX gibi çıktı biçimleri bundan türetilecek, uzun metin seslendirmesi ise yalnızca ihtiyaç duyulan bölümlerde önceden oluşturulan ses parçalarıyla kesintisiz şekilde oynatılacaktır.

## Table narration test

Input table:

| Bileşen | Durum | Gecikme |
|---|---|---:|
| API | Sağlıklı | 82 ms |
| Voice | Sağlıklı | 240 ms |
| Browser Agent | Uyarı | 1,8 sn |

Expected style: explain the important finding in natural Turkish, not literal cell-by-cell dumping unless requested.

## M12 realtime additions (2026-09-02)

Machine-readable form: `services/api/app/voice/datasets.py` (`M12_TERMINOLOGY`,
`M12_TERMINOLOGY_CASES`, `TURKISH_PHONETICS_CASES`, `HESITATION_CASES`,
`M12_INTENT_UTTERANCES`). Acceptance for these is real-only (ACCEPTANCE_TESTS §M12):
the owner listens on the owner's machine; the deterministic gate only proves the sets
exist, cover the list and agree with the normaliser and the intent resolver.

### Mixed Turkish-English terminology (must be benchmarked on at least these)

PagentOS, Tailscale, Hetzner, PostgreSQL, PowerShell, FortiGate, OpenAI, Claude,
Windows, Kubernetes, Redis, Temporal.

33. PagentOS, Tailscale üzerinden Hetzner'daki PostgreSQL veritabanına bağlanıyor.
34. PowerShell betiği FortiGate yapılandırmasını Windows makinesinden okudu.
35. OpenAI ve Claude modellerini aynı görevde karşılaştırdım.
36. Kubernetes kümesinde Redis önbellek, Temporal ise iş akışlarını yönetiyor.

Expected style: each term pronounced as a Turkish speaker naturally says the product
name (no letter-by-letter spelling, no anglicised vowel reduction of the Turkish words
around it); the Turkish suffix attaches with the correct vowel harmony
("Hetzner'daki", "PostgreSQL'e").

### Turkish characters and phonetics: ı İ ğ ş ç ö ü

37. Işık ılık, İstanbul'da ıslık çaldı.  (ı vs i; İ capital dotted)
38. Ağaç yağmurda eğildi, dağ sisle örtüldü.  (ğ lengthening, never a hard g)
39. Şişli'de şaşırtıcı bir çarşı gördüm.  (ş)
40. Çocuklar çiçekli bahçede koşuyor.  (ç)
41. Öğle vakti gölde ördekler yüzüyordu.  (ö, ğ)
42. Üzüm, üç gün üst üste güneş gördü.  (ü)

### Hesitation set (semantic end-of-turn; false-barge rate is measured on this)

The assistant must NOT take the turn at the "…" — the owner is still speaking.

43. şey… raporun ikinci bölümünü bir daha oku
44. yani… aslında sadece OpenAI kısmına bak
45. hani şu… Tailscale ayarını değiştirdiğimiz gün
46. ııı… toplantıyı yarına al
47. bir de… eee… özet geç ama maliyet kısmını atla
48. PostgreSQL'e… yani veritabanına bakar mısın

### Realtime control intents (resolved in Cloud Core, not by keyword alone)

dur (top priority, any state) · devam · tekrar oku · ikinci maddeyi tekrar oku ·
biraz daha yavaş · biraz daha hızlı · özet geç · detaya gir · burayı atla.

Negatives that must not stop: "Durum raporunu oku", "yeterli değil", "kesin bilgi ver",
"susuz kaldım" — matching is on tokens and meaning, never substrings.

### Benchmark targets recorded for the realtime harness (targets, never claims)

barge-in → playback stopped < ~150 ms; end-of-turn → first audible response
~500–700 ms on short turns; tool preamble and tool-done → resumed speech bounded;
no audible gap > ~300 ms inside a response; no silence > ~3 s during a tool without a
preamble. Simulator numbers are gate evidence (`tests/unit/test_realtime_bench.py`);
the owner's machine produces the acceptance numbers via
`GET /v1/voice/realtime/sessions/{id}/benchmark`.
