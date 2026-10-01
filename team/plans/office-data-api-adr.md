# ADR (taslak): Ofis sayfasının veri ucu — canlı durum `team_state`'te, koltuk kuralları saf fonksiyonda

Karar (2026-10-01, office-data-api):
- Döngünün canlı durumu (`team/status.json` / `PUT /v1/team/queue/status`) `team_state` tablosunda `kind="status"`, `key="status"` tek satırdır (kilit satırı gibi); YENİ TABLO / MIGRATION YOK. Durum bir nabızdır: en yeni yazım kazanır, sürüm kontrolü yok.
- `GET /v1/team/office` saf `office.office_view(queue, lock, status, approvals, now)` ile üretilir. Canlılık: durumun `updated_at`'i 10 dakikadan eski DEĞİL, kilit tutuluyor ve bayat değil, kilidin `cycle_id`'si durumunkiyle aynı. Aksi halde `running=false`, kimse `working` değil.
- "Bir rolün en yeni görevi" = o rolün en yeni raporunu taşıyan görev. Koşusu olmayan worker koltukları, `returned`/`stopped` olan en yeni üç worker görevini sırayla alır.
- PUT gövdesi katı (pydantic strict, extra=forbid) doğrulanır; yanlış şekil 422. Mağaza yoksa/okunamıyorsa ofis boş döner (200), 500 değil.
Sonuç: `TeamStore` Protocol'üne `read_status`/`put_status` eklendi; tabloya dokunulmadı.
