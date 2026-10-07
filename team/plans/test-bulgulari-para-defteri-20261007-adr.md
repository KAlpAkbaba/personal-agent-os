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

## Karar (uygulandı, dönüş 3)

Düzeltme onaylı `money-amount-input-edges` kartınınkiyle aynıdır: `services/api/app/money/amounts.py`
o dalın (`team/d20261007/worker-money-amount-input-edges`, b89193ee) dosyasıyla bayt bayt aynı
(sha256 `4792b8acb7d5ca0f1a6b0d8d6668eef018466abc72f462c81a4438126f67fd30`). `parse_amount`, `int()`'den
önce `MAX_AMOUNT_CHARS = 64` karakterden uzun dizgeyi `None` sayar; regex yalnız ASCII rakam okur.
Önceki dönüşün `MAX_LIRA_DIGITS = 15` sınırı kaldırıldı: edges testi 30 haneli tutarı okunur sayar
(defter tavanı `service.MAX_KURUS` "fazla büyük" ile reddeder), 15 hane sınırı onu kırardı; iki dalın
aynı fonksiyonu farklı değiştirmesi birleştirmede çakışırdı. `sys.set_int_max_str_digits` yükseltilmez.
Bu kartın katkısı: test ekibi bulgusunun regresyon testi `test_money_amount_digit_limit.py` (4301 ve 5000
haneli string tutar, nakit rotası ve soru yanıtı rotası dâhil, 9 vaka). Dal team/nightly/lead (81240096)
üstüne yeniden tabanlandı; edges dalı birleştirilmedi.

## Kanıt

- Regresyon testi `services/api/tests/unit/test_money_amount_digit_limit.py` değişmedi (sha256
  `26fd0d71adb2ce8973004837fe7e2118b92c326d1b00c9948798373fe0e41226`). Düzeltmesiz kodda 9 failed
  (54dca82e, PROVEN_AUTOMATED).
- Düzeltmeyle: digit_limit + `test_money_ledger.py` + `test_spend_from_conversation.py` 116 passed;
  `test_money_amount_edges.py` (edges dalında, aynı amounts.py sha'sıyla) 21 passed (PROVEN_AUTOMATED).
- Mutasyon: `MAX_AMOUNT_CHARS` 64 -> 6400 (int sınırının üstü): digit_limit 9 failed; yedekten geri
  yüklendi, sha256 `4792b8ac…7fd30` önce/sonra aynı, yeniden 9 passed (PROVEN_AUTOMATED).
- `tools_money.py:74` ve `banks.py` yolları aynı `parse_amount`'tan kapanır (PROVEN_PROXY).
- Staging'de oturumlu yeniden koşu: NOT_RUN (staging düzeltmesiz sürümü sunuyor; sonraki sürümden
  sonra test ekibi turu). Tüm birim takımı: lead'in kapısı koşar.

## Kapsam dışı

Test ekibinin staging'de iptal edilmemiş ~33 nakit kaydı: ayrı temizlik kartı.
