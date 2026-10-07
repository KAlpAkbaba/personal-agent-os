# ADR taslağı: para defteri - Python'un int-str hane sınırını aşan tutar (test-bulgulari-para-defteri-20261007)

## Bağlam

Test ekibinin t-w10070948 turu (iş tj-t-w10070948-5, staging `b1f8ef94c028b2476ba368b462ffa5a91c4277fc`)
`POST /v1/money/cash` ile 5000 haneli bir tutar gönderdi: 422 `money_refused` beklenirken 500 geldi
(kart test-fail-para-defteri-4ac2d0e8ae). Doğaçlama (improv-para-sinir) sınırı buldu: 4300 hane 422,
4301 hane 500; `/v1/money/questions/{id}/answer` aynı 500'ü verir (tutar soru aranmadan okunur).

Kök neden: `app/money/amounts.py::parse_amount` rakamları `int()`'e verir; Python 3.11+ 4300 haneden
uzun bir dizgede `ValueError` atar (`sys.get_int_max_str_digits`). `parse_amount`'u çağıran her yol
etkilenir: iki rota (`routes._kurus`), sesli cümle (`money_in` -> `_digit_amount`), banka postası
ayrıştırıcıları (`banks.py:129/134/152`; çok uzun rakamlı bir posta yoklayıcı turunu düşürebilir),
gerçek zamanlı ses aracı (`app/voice/realtime_sessions/tools_money.py:74`, `parse_amount(raw) or money_in(...)`).

## Yeniden koşu tablosu

| Vaka | b1f8ef94'te (staging) | Şimdiki kod (dal tabanı 93122258, money/ farkı yok) | Durum |
|---|---|---|---|
| 5000 haneli tutar (cash) | 500 (test ekibi 07:01Z) | 500, ValueError amounts.py:131 | HÂLÂ KIRMIZI |
| 4301 haneli tutar (cash) | 500 | 500 | HÂLÂ KIRMIZI |
| soru yanıtında 5000 haneli tutar | 500 | 500 (routes.py:157 -> _kurus) | HÂLÂ KIRMIZI |
| 4300 haneli tutar | 422 | - | geçiyor (b1f8ef94) |
| '12,505', '1.2.3', boşluk, JSON dizi | 422 | - | geçiyor (b1f8ef94) |

Staging'de yeniden koşu bu çalışmada yapılamadı: `run-scenario.ps1` "ORTAM: staging oturumu geçersiz
(/v1/identity/sessions/current 401)" dedi - yazılım hatası değil, tur başı `seed.ps1` işi. Yeniden üretim
süreç içinde (TestClient, aynı kod) yapıldı.

## Karar (uygulandı)

`parse_amount` hane sayısı aşırı bir dizgeyi tutar saymaz: `int()`'den önce lira haneleri (gruplar
birleştirilmiş) `MAX_LIRA_DIGITS = 15` ile karşılaştırılır, aşan `None` döner. 9-15 hane hâlâ bir tutardır
ve defter tavanı (10 milyon TL, `service.MAX_KURUS`) "fazla büyük" ile reddeder; 16+ hane "anlayamadım"
(`money_refused`). `sys.set_int_max_str_digits` yükseltilmez (DoS koruması bu sınırın amacı). Düzeltme
rotada değil `parse_amount`'ta: iki rota, `money_in`, `banks.py` ve `tools_money.py:74` aynı yerden kapanır.
`None` zaten rotada 422 `money_refused`, cümlede "tutar yok" demektir - yeni hata yolu gerekmez.

## Kanıt

- Regresyon testi `services/api/tests/unit/test_money_amount_digit_limit.py` (9 vaka), 1517a484'teki ve
  önceki ADR ekindeki dosyayla birebir (sha256 `26fd0d71adb2ce8973004837fe7e2118b92c326d1b00c9948798373fe0e41226`).
  Düzeltmesiz kodda 9 failed (PROVEN_AUTOMATED).
- Düzeltmeyle: yeni test + `test_money_ledger.py` + `test_spend_from_conversation.py` 116 passed (PROVEN_AUTOMATED).
- Mutasyon: `MAX_LIRA_DIGITS` 15 -> 10000 (int sınırının üstü): 9 failed; yedekten geri yüklendi,
  `amounts.py` sha256 `59541413…96930` önce/sonra aynı, test yeniden 9 passed (PROVEN_AUTOMATED).
- `tools_money.py:74` ve `banks.py` yolları için ayrı test yok: aynı `parse_amount` çağrısından kapandıkları
  koddan çıkarım (PROVEN_PROXY, `parse_amount` birim vakaları üzerinden).
- Staging'de oturumlu yeniden koşu: NOT_RUN (`/v1/identity/sessions/current` 401, tur başı `seed.ps1` işi;
  ortam sorunu, kimlik bilgisine dokunulmadı). Staging'in düzeltmeli sha'yı sunması sonraki sürüme bağlı.
- Tüm birim takımı: lead'in kapısı koşar.

## Kapsam dışı

Test ekibinin staging'de iptal edilmemiş ~33 nakit kaydı: ayrı temizlik kartı.
