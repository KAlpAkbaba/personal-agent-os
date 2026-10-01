# ADR (taslak): anlatı niyeti tek yönlendiriciden geçer, explain'in `narrative` sorgu türüyle cevaplanır

Bağlam: ADR-0216/0221 `app/narrative`'i yazdı, hiçbir şey çağırmıyordu. `resolve_intent` saf (db yok).
Karar: (1) `resolve_intent` EN SONDA (NONE'dan hemen önce) `recognise()` eşleşirse `Intent.EXPLAIN`,
`query_kind="narrative"` döner - okuma sınıfı, step-up/onay/cihaz komutu yok. En sonda olması gölgeleme
korumasıdır: "bugün ne yaptın" (artifact_list), "bugün neler oldu" (explain today), "ne başarısız oldu"
(explain failures) önceki gibi gider. (2) `explain()` `narrative` türünü, kaynağın isteğe bağlı
`narrative(ask, *, now)` metoduyla cevaplar (mevcut opsiyonel-kaynak kalıbı); metin her seviyede aynıdır.
Kaynakta metot yoksa "Bu konuda kayıt bulamadım." - uydurma yok.
Sonuç: canlı bağlantı için lead'in yapacakları PR notunda. Geri alma: iki dalı silmek yeter.
