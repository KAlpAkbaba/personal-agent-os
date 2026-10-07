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

Regresyon testi: aşağıdaki Ek (hedef yol `services/api/tests/unit/test_money_amount_digit_limit.py`, 9 vaka; şimdiki kodda 9/9
KIRMIZI - ValueError / 500). Düzeltme ve mutasyon kanıtı alan genişletildikten sonra.

## Ek: kırmızı regresyon testi (alan genişletilince `services/api/tests/unit/test_money_amount_digit_limit.py` olarak konur)

Şimdiki kodda 9/9 KIRMIZI (6 ValueError amounts.py:131, 3 rota 500; commit 1517a484 üzerinde koşuldu). Dosya, alan dışı olduğu için daldan kaldırıldı; içerik birebir aşağıda.

```python
"""The test team's para-defteri finding (test-fail-para-defteri-4ac2d0e8ae, staging b1f8ef94).

A cash amount of more than 4300 digits answered 500: ``parse_amount`` handed the digits to
``int()``, which raises ``ValueError`` past Python's int-str limit (``sys.get_int_max_str_digits``).
The same 500 on ``/v1/money/questions/{id}/answer`` - the amount is read before the question
is looked up. A number that long is not one amount: ``None`` -> 422 ``money_refused``.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.main import create_app
from app.money import amounts
from app.money.models import MONEY_TABLES
from app.notifications.models import NotificationRow
from tests.identity_support import authenticate, install_identity

HUGE = [
    "9" * 4301,
    "9" * 5000,
    "1." + ".".join(["999"] * 1500),
    "9" * 5000 + ",50",
    "9" * 5000 + " TL",
]


@pytest.mark.parametrize("raw", HUGE, ids=["4301", "5000", "grouped", "frac", "tl"])
def test_an_amount_past_the_int_digit_limit_is_not_an_amount(raw: str) -> None:
    assert amounts.parse_amount(raw) is None


def test_a_sentence_with_a_huge_digit_word_has_no_amount() -> None:
    assert amounts.money_in("9" * 5000 + " lira harcadım") == []


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (*MONEY_TABLES, NotificationRow.__table__):
        table.create(engine, checkfirst=True)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    test_client = TestClient(app, raise_server_exceptions=False)
    authenticate(app, test_client, settings=settings)
    yield test_client
    engine.dispose()


@pytest.mark.parametrize("digits", [4301, 5000])
def test_cash_with_a_huge_amount_is_refused_not_500(client, digits: int) -> None:
    response = client.post("/v1/money/cash", json={"amount": "9" * digits})
    assert response.status_code == 422, response.text
    assert "money_refused" in response.text


def test_a_question_answer_with_a_huge_amount_is_refused_not_500(client) -> None:
    response = client.post(
        f"/v1/money/questions/{uuid.uuid4()}/answer", json={"answer": "yes", "amount": "9" * 5000}
    )
    assert response.status_code == 422, response.text
```
