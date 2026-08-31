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
