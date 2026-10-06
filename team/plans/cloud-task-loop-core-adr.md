# ADR (number by the lead) — The browser task loop runs in the cloud: target from execution_target, the owner's list at the gate, a cloud task is not attended (2026-10-07)

**Status.** Accepted under TEAM_PROTOCOL 3a.3 (a policy question is not asked; the most
restrictive safe option is applied). **Sahip incelemesi bekliyor** for item 4 below.
Card `cloud-task-loop-core` (cycle d20261006), 1/3 of the cloud task loop; the worker half
is `cloud-task-loop-worker-writes`, the voice half is `cloud-task-loop-voice` (text below).

## Context

ADR-0207 PR-C (2026-10-02/03) gave the browser task loop a model planner, gate bindings and
a write ceiling, but nothing outside `app/webtask` started a task, and every session it
opened was the owner's Chrome (`profile owner`, every risk class, visible). The cloud
worker's `clamp_command` refuses that payload (`security_scope_error`: only `research`,
only READ + NAVIGATE). ADR-0220 left `app.execution.wiring.choose()` with no web-task call
site; `web_tasks.attended` was always written `True`.

The proposal `team/proposals/2026-10-06-feed-cloud-task-loop-unattended-question.md` asked
the owner whether a cloud task may run while he is away. **That question is already
answered** by the ADR-0213 addendum (owner, 2026-09-30, option 4): "a cloud job ACTS only on
sites in his allow-list and READS everywhere else; scheduled jobs are read-only ... a cloud
job the owner started may keep running when he leaves, because it can act nowhere he did
not name". So it was not asked again (TEAM_PROTOCOL 3a.3).

## Decision

1. **Target.** `app/webtask/target.py::choose_task_target` asks
   `wiring.choose(JobKind.BROWSER_TASK, url=None, acting=False, scheduled=False,
   needs_signed_in_session=False, ledger_required=False, capabilities=TASK_OPERATIONS)`
   (`TASK_OPERATIONS` = `browser.session_open`, `browser.tab_new`, `browser.observe` and
   every `CAPABILITY_OF` value) over ONE registry snapshot; the device is
   `wiring.device_for` (cloud / owner_chrome) or `select_device` (device). Nowhere to run is
   `WebTaskError('no_capable_device')` with the Turkish sentence ("Görevi çalıştıracak
   uygun bir hedef yok." / "Bulut şu anda çevrimiçi değil." / "Bulut bu işi şu anda
   yapamıyor."). The ledger carries `execution.selected` / `execution.fallback` (source
   `execution`, no research id).
2. **acting=False at the start.** A task has no address before its first observation, and
   asking the allow-list then would refuse the cloud to a task that only reads (with the
   empty list, every task). Acting is decided **step by step at the gate** where the page's
   address is known (item 4).
3. **attended.** `start_task_db(target=...)` writes `attended = (target != 'cloud')`; the
   target travels in `state_json.target` (no column, no migration). A cloud task is not
   attended - honestly so: the owner may leave and it goes on, because it acts only where
   he named. `web_task.started` carries `target`, `device_id`, `attended`, `spoken_target`
   in its detail (no new event string). **Nothing routine starts a task**: a unit test reads
   `app/routines`, `app/scheduler`, `app/research`, `app/watch`, `app/briefing` and finds no
   `start_task_db` / `BrowserTaskWorkflow`.
4. **The gate's rule 9 (S4).** Target `cloud`, a step classified above NAVIGATE:
   EXTERNAL_COMMUNICATION and HIGH_IMPACT are **refused even on a listed site**
   (`cloud_risk_not_allowed`) - the most restrictive safe option, **sahip incelemesi
   bekliyor** (Onay Merkezi: "bulutta gönderme ve geri alınamayan işlem izin listesindeki
   sitede de yapılmaz; isterseniz genişletilir"). A REVERSIBLE_WRITE asks
   `allowlist_store.acting_allowed(observation.url)`: off the list ->
   `not_on_owner_allow_list` and the owner hears "Bu sitede bulutta yazamam; Onay
   Merkezi'nden siteyi izin listesine ekle."; deny-listed -> the existing `denied_site` stop
   comes first. The loop handles the refusal as every refusal (the planner hears the reason
   and plans again or ends honestly); the message is put on the row.
5. **The cloud session (S1).** `DeviceTaskBrowser(target='cloud', owner_allow_list=...)`
   opens `profile research`, `channel chromium`, policy `{allowed_risk_classes: [READ,
   NAVIGATE, REVERSIBLE_WRITE], visible: false}`, `cloud_task: true`, `owner_allow_list`
   = `allowlist_store.effective_sites()` read when the round starts. Every other target: the
   payload is byte-for-byte today's, without the two keys. The worker half (S2/S3) is
   `cloud-task-loop-worker-writes`.
6. **The store is bound in the worker process.** Only `create_app` bound
   `allowlist_store`; the rounds run in the Temporal worker, where the gate and the session
   would have read the empty seed alone and refused every write the owner allowed. The
   round activity binds it to its own session factory (found on the way; integration test).
7. **Parking is unchanged in the cloud.** `ask_owner`, `PARKED_AFTER` (3 h) and the
   workflow's `OWNER_WAIT_S` are the same; the owner answers when he is back.
8. **REST.** `app/webtask/routes.py`, `/v1/web-tasks`, behind `require_owner_session`:
   `POST ""` {goal, target_word?, allowed_hosts?} -> 202 {task_id, target, device_id,
   attended}; 409 `task_in_flight` / `no_capable_device`; Temporal unreachable (10 s
   connect bound) -> the row `failed` / `temporal_unavailable` and **503** "Görev
   başlatılamadı: iş akışı sunucusuna ulaşılamıyor." `GET /{id}` = `task_dict` (now with
   `target`, `device_id`); `POST /{id}/read-back` (the surface that SHOWED the step says
   so - a REST confirmation is judged against it like mail's), `/confirm` {source: rest},
   `/decline`, `/continue` {answer}, `/cancel`: the row first, then the `woken` / `cancel`
   signal; a gone workflow is logged, never a 500.
9. **Measurement.** `state_json.planner_calls` and `planner_model_calls` (rounds the model
   planner answered) on every task; the evidence card reads them. Cost per call is NOT
   estimated here (no token count reaches the loop) - the evidence card multiplies.

**Lead's addition at merge (main.py is outside this card's area).** In `create_app`:

```python
from app.webtask.routes import router as web_task_router
app.include_router(web_task_router)
```

`tests/unit/test_webtask_routes.py::test_the_router_answers_on_the_real_application_once_it_is_mounted`
passes as "not mounted yet" until the line is there; from then on it asserts the real app
answers `/v1/web-tasks` with 401/403 (and an unknown sibling path with 404).

## Next card — full text

- id: cloud-task-loop-voice
- title: Tarayıcı görev döngüsü bulutta, 3/3 (ses): sahip 'bulutta ... bul/aç/doldur' der, görev /v1/web-tasks ile aynı yoldan başlar; onay/ret/devam/iptal sesle
- depends_on: cloud-task-loop-core, cloud-task-loop-worker-writes; money-ledger kartı birleşmiş olmalı (aynı dosyalar)
- goal: Sahibin cümlesi bir tarayıcı görevi başlatır. (1) app/voice/intents.py: WEB_TASK_START niyeti - 'bulutta X'i bul / aç / doldur', 'tarayıcıda X yap'; hedef sözcüğü ('bulutta', cihaz takma adı) ResolvedIntent'e kopyalanır (yeni alanlar turn kaydına da - memory voice-tool-argument-keys-are-filtered); araştırma niyetiyle çakışma: 'araştır' araştırmada kalır, 'bul ve özetle' + 'bulutta' web görevi değil araştırmaysa ADR'de seçilir ve korpusta iki yönlü vaka. (2) app/voice/realtime_sessions/tools.py: web_task.start {content, target_word}, web_task.confirm (yalnız read-back'i duyan oturum, sonraki tur; check_gate voice), web_task.decline, web_task.continue {content}, web_task.cancel; argüman adı 'content' (relay filtresi). Başlatma app.webtask.target.choose_task_target + service.start_task_db + BrowserTaskWorkflow (routes.py ile ORTAK bir yardımcı; kod kopyalanmaz). (3) tests/voice_corpus/corpus.py: sahibin cümleleri, en az 12 olumlu + 8 yakın ıska ('bulutlu hava', 'unutma', 'araştır'). (4) Sonuç sahibe kısa bildirilir ve bekler (görev tamamlandı ≠ sonucu okumak). Alan: app/voice/intents.py, app/voice/realtime_sessions/tools.py, tests/voice_corpus/corpus.py, ilgili testler, app/webtask/routes.py (ortak yardımcıya çıkarma). Değişmeyen: app/webtask/gate.py, device_port.py.
- acceptance: korpus yeşil (tüm korpus test-slot heavy ile); ses aracıyla bulut hedefli görev başlar, attended False; onay yalnız aynı oturum + sonraki tur; mutasyon RED: niyet deseni bozulunca korpus kırmızı, onay turu kontrolü silinince test kırmızı; PROVEN_REAL sahibin denemesi yayın sonrası.
- evidence_expected: PROVEN_AUTOMATED (korpus, araç testleri), PROVEN_REAL (sahip: 'bulutta bugünkü yapay zeka haberlerinden birini bul').

## Consequences

- A cloud task can now be started (REST); it reads everywhere, writes reversibly only on
  listed sites, never sends or deletes in the cloud. Until `cloud-task-loop-worker-writes`
  lands, the cloud worker still clamps a session to READ + NAVIGATE and rejects the new
  payload's REVERSIBLE_WRITE (`security_scope_error`): the task then fails honestly at its
  first round (`browser_unavailable`-class), it does not act. Merge order: worker-writes
  first, or both in one release.
- The owner may widen item 4 later (Onay Merkezi); the gate's `CLOUD_RISKS` is the one
  place it changes.
