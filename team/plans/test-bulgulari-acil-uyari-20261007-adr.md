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

**Gerileme testi.** `services/api/tests/unit/test_notification_routes_turkish.py` - gerçek router,
sqlite bildirim tablosu; 404 + "bildirim" + iki istek aynı yanıt.

**Durum.** Kırmızı test işlendi; düzeltme `routes.py` alan dışında olduğu için ALAN_ISTEGI ile
Proje Yöneticisine bırakıldı.
