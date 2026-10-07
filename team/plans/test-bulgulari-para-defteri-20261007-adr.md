# ADR taslağı: para defteri - Python'un int-str hane sınırını aşan tutar (test-bulgulari-para-defteri-20261007)

## Bağlam

Test ekibinin t-w10070948 turu (iş tj-t-w10070948-5, staging `b1f8ef94c028b2476ba368b462ffa5a91c4277fc`)
`POST /v1/money/cash` ile 5000 haneli bir tutar gönderdi: 422 `money_refused` beklenirken 500 geldi
(kart test-fail-para-defteri-4ac2d0e8ae). Doğaçlama (improv-para-sinir) sınırı buldu: 4300 hane 422,
4301 hane 500; `/v1/money/questions/{id}/answer` aynı 500'ü verir (tutar soru aranmadan okunur).

Kök neden: `app/money/amounts.py::parse_amount` rakamları `int()`'e verir; Python 3.11+ 4300 haneden
uzun bir dizgede `ValueError` atar (`sys.get_int_max_str_digits`). `parse_amount`'u çağıran her yol
etkilenir: iki rota (`routes._kurus`), sesli cümle (`money_in` -> `_digit_amount`), banka postası
ayrıştırıcıları (`banks.py`; çok uzun rakamlı bir posta yoklayıcı turunu düşürebilir).

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

## Karar (önerilen, uygulama ALAN_ISTEGI bekliyor)

`parse_amount` hane sayısı aşırı bir dizgeyi tutar saymaz: `int()`'den önce lira hanelerinin uzunluğu
makul bir üst sınırla (ör. 15 hane; 10.000.001 TL zaten "fazla büyük" ile reddediliyor) karşılaştırılır,
aşan `None` döner. `sys.set_int_max_str_digits` yükseltilmez (DoS koruması bu sınırın amacı).
`None` zaten rotada 422 `money_refused`, cümlede "tutar yok" demektir - yeni hata yolu gerekmez.

## Kanıt

Regresyon testi: `services/api/tests/unit/test_money_amount_digit_limit.py` (9 vaka; şimdiki kodda 9/9
KIRMIZI - ValueError / 500). Düzeltme ve mutasyon kanıtı alan genişletildikten sonra.
