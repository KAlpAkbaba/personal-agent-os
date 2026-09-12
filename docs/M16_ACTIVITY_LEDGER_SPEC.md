# M16 — Activity Ledger + Self Explanation + Stateful Voice Narration (2026-09-04)

Voice becomes the primary owner interface to all of PersonalAgentOS. The owner must not
need PowerShell, dashboards or development output to know what the system did.

The target experience, verbatim from the owner:

> Owner: **Son yaptıklarını anlat.**
> PagentOS: *Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı. Beş sonuç
> ve beş farklı kaynak ürettim. Dokuz konu dışı sayfayı, on bir ara doğrulama sayfasını,
> dört tarih dışı sonucu ve bir tekrar eden olayı eledim. Tarayıcı temiz şekilde kapandı.
> Research Engine artık gerçek ortamda doğrulanmış durumda. Bilginize.*
> Owner: **Araştırmayı detaylandır.** → the actual findings, from durable evidence.
> Owner: **Teknik olarak ne değişti?** → technical evidence.
> Owner: **Dur.** → speech stops at once. Owner: **Devam et.** → resumes at the same
> semantic point.

Everything spoken is retrieved from durable evidence first. No seeded or demo events exist
anywhere in this milestone; a question with no evidence is answered with "bu konuda kayıt
bulamadım", never with an invented state.

## 0. What is reused, unchanged

| Existing piece | Role in M16 |
|---|---|
| Realtime session control plane (`app/voice/realtime_sessions`): create → media leg → tool relay → events → attach → close; sideband `say`/`narration_cursor`/`tool_completed` | The only voice transport. Not redesigned. Cloud Core never carries audio: the provider speaks the text a tool returns. |
| Client barge-in ordering (`apps/web/app/lib/voice/controller.ts:bargeIn`): local playback silenced first, then provider cancel, then report | Preserved verbatim. "Dur" stops speech through this path before Cloud Core is even told. |
| Narration engine (`app/narration`): `build_plan` over artifact Markdown, `Cursor(section_id, paragraph_id, sentence_index, char_offset)`, `commands.apply` (dur always wins; explain-then-return restores the exact cursor), `narration_sessions` durable cursor | The semantic state behind "devam et", "ikinci madde", "önceki maddeyi açıkla", "bunu atla". The briefing is an artifact, so the engine applies without change. |
| Intent resolver (`app/voice/intents.py`) + `apply_to_narration` bridge; tools `voice.intent`, `narration.control` | Extended with the explanation intents; scope rules unchanged. |
| `audit_events` (`app/broker/audit.py`), `research_runs.events_json`, `research_reports`, `releases`, `incidents`, `tasks`, `realtime_tool_calls` | The durable evidence the ledger is backfilled from. |
| Task → Artifact → Presentation, `executive_summary` column, notify-briefly-and-wait | A briefing is an artifact (`kind=activity_briefing`); its executive section is the notification, the rest waits. |

## 1. Canonical Activity Ledger (`app/ledger`)

One durable, structured, append-only stream for every current and future subsystem.

### 1.1 Table `activity_events` (migration `0015_activity_ledger`)

| column | type | notes |
|---|---|---|
| `event_id` | uuid PK | |
| `occurred_at` | timestamptz | when the thing happened (from the evidence) |
| `recorded_at` | timestamptz | when the ledger wrote it |
| `event_type` | text | dotted vocabulary, §1.3 |
| `subsystem` | text | `research` `browser` `cloud_core` `device_service` `session_companion` `deployment` `voice` `memory` `goal` `self_model` `evolution` `ledger` |
| `module` | text nullable | e.g. `browser_worker`, `research_pipeline`, `diagnostic_observer` |
| `version` | text nullable | e.g. worker `0.4.0`, research policy `4` |
| `status` | text | `started` `completed` `failed` `skipped` `pending` `info` |
| `severity` | text | `info` `notice` `warning` `critical` |
| `action` | text | short verb phrase, machine-oriented |
| `result` | text nullable | short outcome, machine-oriented |
| `production_state` | text | `n/a` `idea` `designed` `built` `tested` `reviewed` `shadow_ready` `approval_required` `deployed` `rolled_back` |
| `command_id` | uuid nullable | |
| `trace_id` | text nullable | |
| `research_job_id` | uuid nullable | the research task id |
| `browser_session_id` | text nullable | |
| `related_goal_id` | uuid nullable | reserved for the Cognitive Core |
| `related_module_id` | text nullable | reserved for the Self Model / Evolution Engine |
| `evidence_refs` | json list | `[{"kind": "research_report", "ref": "<task_id>"}, {"kind": "audit_event", "ref": "123"}, {"kind": "artifact", "ref": "<uuid>"}, {"kind": "file", "ref": "research-1.json", "digest": "sha256:..."}]` |
| `factual_summary` | text | one Turkish sentence stating only what the evidence supports |
| `detail_json` | json | structured counts and identifiers (never page text, never transcripts, never credentials) |
| `source` | text | `live` or `backfill:<table>` |
| `source_ref` | text | idempotency key within `source`, e.g. `research_runs:<task_id>:ready` |

Unique `(source, source_ref)`. Indexes on `occurred_at`, `subsystem`, `event_type`,
`research_job_id`, `trace_id`.

### 1.2 Writer and reader (`app/ledger/service.py`)

- `record(session, ActivityEvent) -> ActivityEventRow` — idempotent on `(source, source_ref)`;
  a second write with the same key returns the existing row and changes nothing.
- `query(session, *, since, until, subsystems, statuses, event_types, research_job_id, limit)`
  newest first.
- `latest(session, *, subsystems=None)` — the most recent completed/failed event, for "son ne
  yaptın".
- `backfill(session) -> BackfillReport` — §1.4; safe to run at every API start.

Live writers added in this milestone (each a small call at an existing transition):

| subsystem | where | event_type |
|---|---|---|
| research | `BrowserResearchWorkflow` ready/failed (via `persist_artifact_activity` / `fail_run_activity`) | `research.completed`, `research.failed`, `research.quality_gate` |
| voice | `realtime_sessions.service` create/attach/close, tool call `activity.explain` | `voice.session.created` … `voice.explained` |
| deployment | `POST /v1/ledger/events` from the release/owner scripts with owner credentials (the script's own evidence file is the source; `evidence_refs` carries its digest) | `deployment.cloud_core.released`, `deployment.agent.skipped`, `deployment.agent.installed` |
| ledger | backfill runs | `ledger.backfill` |

### 1.3 Event vocabulary

Current: `research.planned` `research.completed` `research.failed` `research.quality_gate`
`browser.session.opened` `browser.session.closed` `browser.search` `voice.session.created`
`voice.session.attached` `voice.session.closed` `voice.explained` `voice.narration.paused`
`voice.narration.resumed` `deployment.cloud_core.released` `deployment.cloud_core.rolled_back`
`deployment.agent.installed` `deployment.agent.skipped` `memory.remembered`
`ledger.backfill` `briefing.queued` `briefing.delivered`.

Reserved for the Evolution Engine (M17 writes the lab-side ones; the production-side
writers come with M18). The last five were added on 2026-09-05: without them five backlog
transitions - `researching`, `qualifying`, and `rejected`/`quarantined`/`superseded` outside
a failed test run - produced no ledger event at all, so a candidate could be parked in
quarantine and neither the ledger nor the owner's briefing would ever say so. Every backlog
transition now writes exactly one event; `tests_failed` still wins over the generic closure
event when the failure really came out of a test or eval run, because it says why.
`evolution.idea_created` `evolution.module_designed` `evolution.build_started`
`evolution.build_completed` `evolution.tests_passed` `evolution.tests_failed`
`evolution.security_review_passed` `evolution.benchmark_completed` `evolution.shadow_ready`
`evolution.owner_approval_required` `evolution.deployed` `evolution.rolled_back`
`evolution.researching` `evolution.qualifying` `evolution.rejected` `evolution.quarantined`
`evolution.superseded`, each with
`related_module_id`, `version`, `production_state` and `evidence_refs` (test report, review
record, benchmark record). The Self Model answers "Diagnostic Observer'da sorun ne?" from
these events plus `incidents`, and says which of it is fact and which is inference.

**Knowledge and execution are separate permissions.** The ledger and the Self Explanation
engine are read-only over evidence; nothing in them can start, deploy, promote or roll back
anything. Production execution keeps its own path (release transaction, owner approval).

### 1.4 Backfill from durable evidence — never fabrication

Only rows that already exist in canonical tables become events, and every event carries
the reference it was derived from. Sources and the exact mapping:

| source | event | occurred_at | evidence_refs |
|---|---|---|---|
| `research_runs` with stage `ready` + `research_reports` | `research.completed` with `detail_json = report.stats` (findings, sources, rejected_by_reason), `research_job_id`, `version = research policy` when known | run `updated_at` | research_report, artifact, memory |
| `research_runs` with stage `failed` | `research.failed` with `error` class | `updated_at` | research_run |
| `research_runs.events_json` ranking entries carrying `rejected` | `research.quality_gate` | event `at` | research_run |
| `audit_events` category `voice_realtime` actions `voice_session_created/closed/attached` | `voice.session.*`, one row per (session, state) | `created_at` | audit_event |
| `releases` | `deployment.<component>.released/rolled_back` with `production_state` | `promoted_at` / `rolled_back_at` | release |
| `incidents` | `incident.opened` (severity from the row) | `first_seen_at` | incident |

Idempotency key = `<table>:<pk>:<state>`. Backfill is re-runnable; a re-run records nothing
new for unchanged rows.

**A live writer's fact is not backfilled again.** `(source, source_ref)` uniqueness cannot see
that `live` and `backfill:<table>` are describing one fact, so every source a live writer also
covers is guarded on the natural key instead: research on `(event_type, research_job_id)`, voice
on the live writer's own key `realtime_sessions:<id>:<state>`, built by
`ledger_service.voice_session_source_ref` so both halves read one definition. Voice had no such
guard until 2026-09-12, and every session created since the live writer shipped therefore had two
ledger rows — three attaches and a close made one session eight rows where it should have been
three. Voice dedup is per **(session, state)**: attaching is repeatable and the live writer
collapses every attach into the one row its key names, so the backfill emits only the oldest
audit row of each pair and means the same thing. A session from before the live writer shipped
has no live row and is still backfilled, which is what the backfill is for.

## 2. Self Explanation Engine (`app/explain`)

Evidence first, then words. `explain(session, question, *, level, now) -> Briefing`:

1. `classify(question) -> ExplainQuery` — Turkish, deterministic:
   `last_activity` (son ne yaptın / son yaptıklarını anlat), `today` (bugün neler yaptın),
   `failures` (ne başarısız oldu), `problems_now` (şu anda sorun var mı),
   `subsystem_status` (araştırma motoru ne durumda → subsystem `research`),
   `why_failed` (neden başarısız olmuştu), `evidence` (kanıtı ne),
   `research_detail` (araştırmayı detaylandır), `technical` (teknik olarak ne değişti /
   teknik anlat), `module_problem` (X'te sorun ne).
2. Retrieval from the ledger and the records it points at (research report for findings;
   incidents for problems; releases for versions). Nothing is answered from model memory.
3. Composition into a `Briefing`: sections at three levels, each statement typed
   `known_fact` (directly supported by an evidence ref) / `inference` (derived, says so) /
   `uncertainty` (evidence missing or contradictory, says so). Numbers are spoken as
   Turkish words by the existing normaliser at narration time.

The briefing is persisted as an artifact `kind=activity_briefing` whose canonical Markdown is:

```
# Özet
<executive: 3-6 sentences, the owner briefing; ends with "Bilginize.">
# Ayrıntı
1. <item: title — summary — why it matters — evidence ref>
...
# Teknik
1. <versions, contracts, policy numbers, counts, trace/command ids, tests>
...
# Kanıt
- <evidence refs, one per line>
```

Sections are the narration levels. `executive` = `# Özet`, `detailed` = `# Ayrıntı`,
`technical` = `# Teknik`. Terminal logs are never read verbatim unless the owner says so
("logu aynen oku").

## 3. Stateful voice narration over the existing transport

### 3.1 Tools (registered in `realtime_sessions/tools.py`)

`activity.explain(question, level?)` — builds the briefing (§2), creates the artifact and a
`narration_sessions` row attached to this realtime session (`row.narration_session_id`),
sets the cursor to the start of the requested level, and returns

```
{"speech": "<the text of that section, verbatim>", "level": "executive",
 "briefing_id": "...", "narration_session_id": "...", "sections": ["Özet", "Ayrıntı", "Teknik"],
 "evidence_count": 3, "facts": 5, "inferences": 1, "uncertainties": 0}
```

The persona instructs the provider: *when a tool result carries `speech`, say it verbatim
in a natural tone and add nothing.*

`narration.control(utterance)` — unchanged contract, extended result: `speech` = the text
from the (new) cursor to the end of the current section, so "devam et" resumes at the exact
sentence, "ikinci madde" reads item 2 onward, "önceki maddeyi açıkla" reads the previous
item's detailed form and then returns to the saved cursor (`ACIKLA`/`ACIKLA_BITTI`), "bunu
atla" skips the current item, "teknik anlat" jumps to `# Teknik`, "özetle" to `# Özet`,
"detay ver" / "araştırmayı detaylandır" to `# Ayrıntı`.

### 3.2 Knowing where "dur" landed

The client already receives the provider's output transcript (`response_text` events). It
now reports one more state event, `spoken`, with the assistant transcript spoken so far
(top-level `text`, like `utterance`) at two moments: when it cuts the assistant off
(barge-in, before the `barge_in_start` timing event) and when a response completes. Cloud
Core aligns that transcript against the narration plan from the current cursor — the last
fully spoken sentence is found by normalised prefix match — and persists the cursor at the
next sentence (`voice.narration.paused`). The transcript itself is never stored (the audit
scrubber refuses `text`, by design). If no `spoken` event arrives, the cursor stays at the
start of the section last handed out, which is still a correct semantic point.

New state event kind `spoken` → `STATE_EVENT_KINDS`; not a timing kind. The wire contract
model does not change, so `CONTRACT_VERSION` stays 2.

### 3.3 Intents

`Intent` gains `TECHNICAL` ("teknik anlat", "teknik olarak ne değişti"), `EXPLAIN_PREVIOUS`
("önceki maddeyi açıkla"), and the explanation questions resolve to `EXPLAIN` with the
question preserved so the provider calls `activity.explain`. Stop priority is unchanged.

## 4. Proactive briefing policy (`app/ledger/briefing.py`)

| event class | policy | delivery |
|---|---|---|
| critical runtime/security problem (`severity=critical`) | `immediate` | live session: sideband `say` now; else persisted, spoken at the next session start |
| owner-requested long task finished (`research.completed/failed` for an owner task) | `completion` | one short sentence: *"Efendim, bilginize; araştırma tamamlandı. Beş önemli sonuç çıkardım. İsterseniz özetini anlatabilirim."* |
| a future module reaches `shadow_ready` | `once` | told once, marked delivered |
| ordinary autonomous development | `digest` | accumulated; spoken as one summary on request or at the next session |
| low-value internal activity | `ledger_only` | never spoken unless asked |

Table `pending_briefings`: `briefing_id`, `created_at`, `policy`, `priority`, `speech`,
`event_ids` (json), `delivered_at`, `delivered_via` (`say` | `session_instructions` |
`push`), `expires_at`. A pending briefing is injected into the next session's instructions
as *"İletilmeyi bekleyen not: …"* and marked delivered when that session reports its first
`response_done`, so nothing is lost when no voice session is active.

## 5. Acceptance (real, owner machine)

One owner action, `scripts/voice/owner-explain.ps1 -OutFile explain-1.json`: starts the web
voice client exactly as today, prints the six phrases to say, waits for Enter, then fetches
the session's durable record and asserts from evidence rows only:

1. an `activity.explain` tool call succeeded with `level=executive` and non-empty `speech`
   derived from a `research.completed` ledger event that was **backfilled from
   `research_reports`** (the real 2026-09-04 run), never seeded;
2. a `narration.control` call resolved `DETAIL` and returned the findings section;
3. a `narration.control` call resolved `TECHNICAL`;
4. a `spoken` event followed by `barge_in_start`/`playback_stopped` and a
   `voice.narration.paused` ledger event with a cursor;
5. a `RESUME` intent whose `speech` starts at that cursor's sentence;
6. session closed cleanly.

Deployment: one transactional Cloud Core release (new tables and routes); the web client
runs from the checkout as before; no Windows component is touched.
