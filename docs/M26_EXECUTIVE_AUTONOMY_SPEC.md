# M26 — Executive Autonomy

Status: CLOSED 2026-09-09 (ADR-0089; QUALIFICATION Stage 24 rows 24.1-24.17; Cloud Core release e6f08ff, one executive run COMPLETED on production). Decision record: ADR-0089.
Predecessors: the Goal Engine and the Cognitive Core loop (`app/goals`: goals above tasks, criteria satisfied only from evidence, the legal-transition table), the Routine Engine (`app/routines`: a closed action vocabulary, conditions, the real dispatcher), the durable research workflows on Temporal (`app/research/{workflow,browser_workflow}.py`: intent lives in Temporal, activities idempotent, the workflow holds no state), the task state machine (`app/artifacts/models.py`: `CREATED … READY … FAILED_RECOVERABLE`), the M18 action contract (receipts, `state.now`), M20 documents, M21 mail/calendar with the confirmation gate, M22 artifacts, M23 apps, M25 scenes.

The owner's rule, in one line: **a multi-step job is a durable graph of steps the assistant can be asked about, paused, resumed, corrected and cancelled at any moment, that ends in an honest state — done, partly done with what is missing named, or stopped — and never takes an external high-risk action on its own.**

## 1. The task graph (the structured plan, spec-first)

`app/executive/plan.py` — `TaskGraph`: `goal` (the owner's words, bounded), `steps[]` (≤ 24) each with:

```
id            : token (s1, s2, …)
kind          : from the CLOSED vocabulary below
inputs        : {name: "<step id>.<output name>" | literal}   (only earlier steps; a DAG is enforced)
precondition  : {check: focus_exists | step_done | device_capability | artifact_valid | account_present | none, arg?}
postcondition : {evidence: artifact_id | document_refs | research_report | draft_id | proposal_id | project_id | scene_id | text, min?}
timeout_s     : ≤ 900
retry         : {max_attempts ≤ 3, backoff_s ≤ 60, only_on: [dependency_unavailable, timeout]}
risk_class    : read | mutate_local | mutate_external | high_risk
compensation  : none | discard_draft | stop_project | delete_render   (closed; never "delete the owner's file")
```

Step kinds (each maps to ONE existing service call — no new capability is implemented in M26): `research.run` (M13, the browser research through the device), `research.synthesize` (the report's executive summary as text), `documents.find` / `documents.compare` / `documents.extract` (M20), `artifacts.create` (M22: a spec built from earlier outputs — document / spreadsheet / presentation), `artifacts.render`, `mail.analyze_thread` (M21 read), `mail.draft` (M21: a draft, NEVER `mail.send`), `calendar.propose` (a proposal, never a commit), `apps.create` / `apps.test` (M23), `scene.create` / `scene.render` (M25), `synthesis` (the final owner-facing summary: what was made, where it is, what is missing — the "notify briefly and wait" step, constitution). `high_risk` kinds do not exist in the vocabulary: sending, paying, deleting and publishing are not steps a graph can carry; a plan that needs them ends at a `mail.draft`/`calendar.propose` step whose result is read back to the owner and waits for the M21 confirmation by the owner's own turn.

## 2. The planner (deterministic reference; the model seam inert)

`app/executive/planner.py` — `RuleBasedExecutivePlanner`: from the owner's request to a `TaskGraph` by recognising the directive's shapes and their variants through the ONE router's slots (the topic, the time window, the folder/file focus, the mail thread focus, the requested outputs — "Word raporu", "sunum", "Excel", "yönetici özeti", "cevap taslağı"): (a) research → synthesize → document artifact (+ presentation) → synthesis; (b) folder compare → documents.find (focus) → documents.compare → spreadsheet artifact + a summary document → synthesis; (c) mail thread → mail.analyze_thread → documents.find (the named files) → mail.draft → synthesis (waiting for the owner). `ClaudeExecutivePlanner` behind the same Protocol may propose a graph, which is VALIDATED by the same `graph.py` rules before anything runs (the DAG, the vocabulary, the bounds, no high-risk kind) — the model never executes, it only proposes data.

## 3. The durable execution (Temporal, the M13 discipline)

`app/executive/workflow.py` — `ExecutiveWorkflow` (workflow id `executive-{run_id}`): sequences the graph in topological order, fanning out independent steps (≤ 3 concurrent); every step is ONE activity `run_step(run_id, step_id)` that is idempotent by `(run_id, step_id, attempt)` and calls the existing service; the workflow holds no state — the `executive_runs` / `executive_steps` rows (migration `0033`, expand-only) are written by the activities, and the workflow resumes from Temporal history after a Cloud Core restart (proven with the time-skipping test environment as the research workflows are). Signals: `pause` (finishes the running activity, starts none), `resume`, `cancel` (cancels the running activity with a bounded grace, runs the compensation of every step that has one, marks the rest `cancelled`), `retry_step(id)`, `amend(step)` (appends a validated step — "Sunumu da ekle" adds a presentation artifact step depending on the synthesis inputs). Queries: `status` (the counts and the current step), `explain` (the current step in one sentence: what it is doing and what it waits for).

Step states: `pending → ready → running → verified | failed_recoverable → (retrying → running) | failed → compensated | skipped | cancelled`; a step is `verified` only when its postcondition evidence exists (an artifact row with `valid`, a research report row, document refs, a draft row) — the activity reads it back, never trusts the call's return. Run states: `planned → running → paused → running → completed | partial | cancelled | failed`; `partial` names every step that did not verify and why, and the synthesis still runs over what exists (the honest partial-result state the directive asks for).

Recovery (the failure matrix, all tested): a document unavailable → `documents.find` fails `not_found` → dependent steps `skipped` with the reason, the run `partial`; a browser fetch failure → `research.run` retried per policy, then `failed_recoverable` → the run continues without it if independent steps remain, else `partial`; an artifact generator failure → the step `failed`, its compensation `delete_render`, the run `partial`; a step timeout → cancelled at `timeout_s`, retried once if the policy allows, else `failed`; a Cloud Core restart mid-run → the workflow resumes and the idempotent activity does not repeat a verified step.

## 4. Authority

- No step kind can send, pay, delete, publish or change a system setting; `mutate_external` exists only as `mail.draft` / `calendar.propose` (drafts and proposals), whose result waits for the owner's M21 confirmation — the graph itself never confirms.
- `mutate_local` steps (artifacts, apps, scenes) run under the same device authority the families already have (`OperatorEnabled`, the roots, the job bounds); a graph adds no authority.
- The owner's session owns the run: `start`, `pause`, `resume`, `cancel`, `retry`, `amend` by voice through the ONE router or by the owner-gated REST routes; the model cannot start a run by argument alone — the router's recorded turn is what `executive.start` acts on.
- Bounds: ≤ 2 runs active at once; ≤ 24 steps; ≤ 60 min per run wall clock; every activity's timeout ≤ 15 min.

## 5. Voice (the ONE router; multi-turn corpus category `executive`)

Intents `EXEC_START` (the directive's three: "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, Word raporu ve sunum hazırla.", "Bu klasördeki teklifleri karşılaştır, Excel oluştur ve yönetici özeti hazırla.", "Bu mail zincirini analiz et, ilgili dosyaları bul ve cevap taslağı hazırla." and their variants), `EXEC_STATUS` ("Ne yapıyorsun?", "Ne durumda?"), `EXEC_EXPLAIN` ("Şu an tam olarak ne yapıyorsun?"), `EXEC_PAUSE` ("Bu işi durdur", "Bekle"), `EXEC_RESUME` ("Devam et"), `EXEC_RETRY` ("İkinci adımı tekrar dene", "Araştırmayı tekrar dene"), `EXEC_AMEND` ("Sunumu da ekle", "Excel'i de hazırla"), `EXEC_CANCEL` ("Bunu iptal et", "Vazgeç"). Tools `executive.start | status | explain | pause | resume | retry | amend | cancel`; receipts read the run back ("Araştırma bitti, rapor hazır, sunum yazılıyor: 3/5 adım" / "Durduruldu: 2 adım tamam, 3 bekliyor" / "Kısmen bitti: klasördeki üç teklif karşılaştırıldı, Excel hazır; özet yazılamadı — belge bulunamadı"). The corpus gains **conversations**: a case may carry `preceding_turns[]` (the same session, earlier utterances executed first — the harness runs them through the same router) so "Devam et" is tested after "Bu işi durdur" after a start; ≥ 120 cases; negatives: "Bu maili gönder" inside a run → refused (no such step; the M21 gate stands), "Devam et" with nothing paused → clarification, "İkinci adımı tekrar dene" with one step → clarification, "Bunu iptal et" with no run → clarification; every earlier category unchanged; forbidden side effects 0 (no mail sent, no calendar committed, measured by the harness counters).

## 6. The Living Core and the Cockpit

UI contract v11 `executive.run` `{run, state, step, done, total}` (scalars) published from rows at every transition; the Cockpit "Görevler" panel: the runs with their steps and states, the current step's explanation, "Duraklat / Devam / İptal" behind the owner-gated routes (`GET /v1/executive/runs`, `GET /v1/executive/runs/{id}`, `POST /v1/executive/runs/{id}/pause|resume|cancel|retry|amend`), the partial state honestly listing what is missing, the empty state "Devam eden bir iş yok".

## 7. Marks sought

**What production can actually reach, measured 2026-09-08 17:30Z** (`GET /v1/devices` with the owner session, against release 20db267): the deployed Windows agent advertises 29 capabilities — the whole browser family (`browser.navigate/click/extract/fetch_evidence/snapshot/session_open/...`, 25 of them) plus `desktop.alarm_start|alarm_stop|open_application|open_artifact`. It does NOT advertise the documents family (`file.search`, `document.extract`), the projects family, or `scene.inspect`, because those wait on the elevated agent update (owner item 28). So shape (a) — research through the owner's device, synthesize, a document artifact — is the run that can be proven REAL on production, and it is the one the gate asks for; shapes (b) and (c) stay unit-proven until item 28 and item 30. This is recorded at kickoff rather than discovered at the gate.


The graph, the planner, the durable workflow (worker restart, pause/resume/cancel/retry/amend, the failure matrix) PROVEN_AUTOMATED (the Temporal test environment); voice PROVEN_AUTOMATED (multi-turn corpus); TTS per credits; the Living Core PROVEN_AUTOMATED; the Cloud Core PROVEN_REAL (release) with ONE real executive run on production — shape (a) with the real browser research through the owner's device and a real document artifact, read back through `/v1/executive/runs/{id}` and the artifact's validation; shapes (b) and (c) real on production only when the owner's folder/mail account exist (items 30/28), else the unit proof stands.

## 8. Security

- The planner's output is data validated by `graph.py` before any activity runs; the model seam cannot add a kind, a route or an authority.
- Activities call existing services with the run's owner session; no new device capability, no new network; the workflow never holds secrets.
- Cancel is bounded and compensations are from the closed vocabulary; nothing of the owner's is deleted by a compensation.
- A run's ledger rows and receipts carry step ids and evidence refs, never document bodies or drafts in full.
