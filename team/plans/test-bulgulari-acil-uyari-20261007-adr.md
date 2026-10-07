# ADR draft - test bulguları acil-uyari (2026-10-07)

**Bağlam.** Test ekibi (tur t-d20261007, iş tj2b-edge, staging `d74a8daa83389d87a3ad68e1177e99e802e816b3`)
bir bulgu yazdı: `test-fail-acil-uyari-42135182f6` - "olmayan bildirim 404 Turkce mi". Var olmayan
bir bildirimi okundu işaretlemek (`POST /v1/notifications/{id}/read`) 404 dönüyor, gövde ise İngilizce
`{"detail":"unknown notification"}`; yanıtta "bildirim" yok.

**Yeniden koşu (2026-10-07, aynı staging sha `d74a8daa`).**

| Bulgu | Staging sha | Sonuç |
|---|---|---|
| test-fail-acil-uyari-42135182f6 | d74a8daa83389d87a3ad68e1177e99e802e816b3 | HÂLÂ KALDI: 404, yanıtta 'bildirim' yok |

Zaten geçen bulgu yok.

**Karar.** Sahibe görünen 404 metni Türkçe olur: `services/api/app/notifications/routes.py`
`mark_read` -> `HTTPException(404, detail="bildirim bulunamadı")`. Durum kodu (404) ve aynı isteğin
ikinci kez aynı yanıtı vermesi değişmez. Web paneli (`markNotificationRead`) metne değil koda
bakıyor (`HTTP 404`), metin değişikliği onu bozmaz.

**Gerileme testi.** `services/api/tests/unit/test_notification_routes_turkish.py` (önerilen yol) - gerçek
router, sqlite bildirim tablosu; 404 + "bildirim" + iki istek aynı yanıt. Dosya kartın alanı dışında
olduğu için daldan çıkarıldı (geri dönüş nedeni: "alan dışı dosya"); tam metni aşağıda. 57ac579d'de
işlenmiş haliyle RED çıktısı: `AssertionError: assert 'bildirim' in 'unknown notification'` (1 failed).

**Durum.** Düzeltme (`routes.py`) ve test dosyası alan dışında; ikisi için ALAN_ISTEGI Proje
Yöneticisine bırakıldı. Alan genişleyince: testi bu metinle ekle, RED gör, `detail` metnini değiştir,
GREEN, mutasyon (metni geri İngilizceye çevir) RED, sha256 ile geri yükle.

**Kırmızı testin tam metni** (`services/api/tests/unit/test_notification_routes_turkish.py`):

```python
"""The test team's finding test-fail-acil-uyari-42135182f6 (staging d74a8daa, 2026-10-07):
marking a notification that does not exist as read answered 404 "unknown notification" -
the owner's surfaces are Turkish, and the answer must say which thing was not found
("bildirim").
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.identity.dependencies import require_owner_session
from app.notifications.models import NotificationRow
from app.notifications.routes import router


def _client() -> TestClient:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    NotificationRow.__table__.create(engine)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_owner_session] = lambda: None
    app.state.artifacts = SimpleNamespace(session=sessionmaker(bind=engine, expire_on_commit=False))
    return TestClient(app)


def test_reading_a_notification_that_does_not_exist_says_so_in_turkish() -> None:
    client = _client()
    missing = uuid.UUID("6f1c2b9e-0000-4000-8000-000000000001")

    first = client.post(f"/v1/notifications/{missing}/read")
    again = client.post(f"/v1/notifications/{missing}/read")

    assert first.status_code == 404
    assert "bildirim" in first.json()["detail"]
    assert again.status_code == 404
    assert again.json() == first.json()
```
