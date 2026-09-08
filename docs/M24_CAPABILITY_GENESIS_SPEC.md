# M24 — Capability Genesis

Status: CLOSED 2026-09-08 (ADR-0087 + addenda; QUALIFICATION Stage 22; Cloud Core release: `PROVEN_REAL` (the release and the surface) / the genesis run itself `PROVEN_AUTOMATED`). Decision record: ADR-0087.
Predecessors: M7 Evolution Engine (`app/evolution`: the capability registry, the gap decision tree with its audited trail, the self-extension pipeline candidate → sandbox → validated → shadow → canary → active, the `DeterministicSkillGenerator` for the CONTROLLED class of pure transforms, the isolated-subprocess `CapabilityDispatcher`, `TaskResumer`), M18.4 (the Evolution Supervisor, the authority kernel, the Approval Center with `authorize` + `confirm_high_risk`, the release ledger, the owner's voice `evolution.control` / `evolution.status`, UiState `evolution.*`), M19 `object_focus`, M23 (the App Factory's `ProjectFiles` policy and bounded runner).

The owner's rule, in one line: **when the assistant lacks a capability it says so, builds one where that is safe, proves it on the thing it was built for, and only then says it has it — and it never fakes the proof with a shortcut written for the test.**

## 1. What exists and what is missing

Exists (M7/M18.4, all tested): a request the registry cannot resolve becomes a `CapabilityGap` with a decision trail (composition really attempted first); the pipeline can generate a skill for a fixed allowlist of PURE OPERATIONS (`slugify`, `word_count`, …), sandbox it, evaluate it, review it independently, roll it out shadow → canary and register it; the dispatcher runs registered skills as isolated subprocesses; a parked task resumes.

Missing (the directive's M24): the loop has never been driven by an OWNER REQUEST against an EXTERNAL INTERFACE. Every generated skill so far transforms a string; none talks to anything. The directive asks for the general case in its safe form: *a small test application exposes a controllable feature for which no adapter exists* — the assistant detects the gap, researches the interface, designs an adapter, implements it, tests it against the running application, classifies its risk and side effects, rolls it out, registers it, USES it, and verifies the result through the application itself. And the owner hears the truth at every step.

## 2. The interface description (the "research" step, made concrete)

`app/genesis/interface.py` — `InterfaceDescription`: what the assistant learns about a controllable thing before it designs anything.

```
InterfaceDescription
  source          : {"kind": "http_spec", "url": "http://127.0.0.1:<port>/spec"} | {"kind": "inline"}
  name            : token (≤ 64, [a-z0-9_.-])
  base_url        : http://127.0.0.1:<port> or http://localhost:<port> ONLY (M24 scope: local applications; any other host refused at parse)
  operations[]    : {id: token, method: GET|POST, path: /token(/token)* (no query, no template beyond {id}), input_schema: bounded JSON-schema subset (object of string|integer|boolean|number fields, required[]), output_schema: same subset, side_effect: "read" | "mutate", idempotent: bool}
  evidence        : {"read_back": <operation id with side_effect read>} — the operation the assistant will use to VERIFY a mutation
  bounds          : ≤ 16 operations, ≤ 16 fields per schema, ≤ 8 KiB total
```

Research = `GET <base_url>/spec` through a bounded fetcher (5 s, 64 KiB, no redirects, `127.0.0.1`/`localhost` only) → `InterfaceDescription.parse` (the choke point: every token re-validated; anything outside the subset refused as `validation_error` naming the path). The description is stored on the gap's trail as the `interface_researched` step. Nothing of it reaches generated source without the same strict-token checks `app/evolution/tokens.py` already enforces (paths, ids, field names are tokens; schemas are re-rendered from the parsed model, never spliced as text).

## 3. The adapter (the "architecture" and "implementation" steps)

`app/genesis/adapter.py` — `AdapterSpec` (an `InterfaceDescription` + the capability ids it will register: one capability per operation, `<name>.<operation>`, version `0.1.0`) and `HttpAdapterGenerator`, a second implementation of the existing `SkillGenerator` Protocol beside `DeterministicSkillGenerator`. It renders the standard generated-skill layout (`manifest.yaml`, `README.md`, `src/<skill>.py`, `tests/test_<skill>.py`, `evals/eval_<skill>.py`, `evals/cases.json`) where `run(payload)`:

- validates the payload against the operation's `input_schema` (a small stdlib validator rendered into the skill — no dependency),
- performs ONE `urllib.request` call to `base_url + path` with the JSON body (POST) or none (GET), a 5 s timeout, no redirects, `Content-Type: application/json`,
- validates the response against `output_schema` and returns it as the result dict; a non-2xx, a timeout or an off-schema body raises the taxonomy error (`dependency_unavailable` / `timeout` / `postcondition_failed`).

The generator is GENERIC over the description: the same code path renders an adapter for any description within the subset. The directive's prohibition ("do not fake success by adding a hardcoded test shortcut") is enforced structurally — §7's generalisation test renders adapters for two different fixture applications with different operations and both must work; the generator source is guard-tested to contain no literal from either fixture (names, paths, ports).

The rendered `tests/` exercise the adapter against a LIVE base URL passed by environment (`PAGENTOS_GENESIS_BASE_URL`, set by the sandbox to the fixture application's port); with no application running the tests fail honestly with `dependency_unavailable`, they never pass on a mock. The rendered `evals/` run each declared example through the live application and compare with the declared expectations. Network permission in the manifest: exactly `[base_url]`; filesystem: none; device: none; secrets: none; external services: `[name]`.

## 4. The manifest, extended (the directive's "new capabilities require")

`app/evolution/manifest.py` gains OPTIONAL keys (additive; every existing manifest and test unchanged): `input_schema`, `output_schema` (the bounded subset), `authority_class` ∈ {`read_only`, `mutating_authorized_asset`, `mutating_unauthorized`}, `side_effect_class` ∈ {`none`, `read`, `mutate_external`}, `evidence_contract` (`{"read_back": <operation>, "postcondition": <text>}`), `rollback_semantics` ∈ {`not_applicable`, `compensating_operation:<id>`, `none_irreversible`}. `HttpAdapterGenerator` fills all six; `validate_manifest` checks their shapes when present. The registry exposes them on `resolve()`; the dispatcher refuses to run a `mutate_external` capability whose `authority_class` is `mutating_unauthorized` (a hard rule in code, not policy text).

## 5. The genesis run (the flow, as a state machine with real states)

`app/genesis/service.py` — `GenesisService.request(utterance-derived CapabilityRequest, interface source)` drives ONE `GenesisRun` (table `genesis_runs`, expand-only migration `20260908_0031_genesis_runs.py`): `id`, `capability_id`, `gap_id`, `state`, `interface_json`, `authority_class`, `side_effect_class`, `approval_required`, `approval_ref`, `skill_version_id`, `evidence_json`, `error_class`, `error_message`, `created_at`, `updated_at`, `session_id`.

States (each a row update AND a ledger row `genesis.<state>` AND a UiState publish — the same three-way discipline as M18.4 §15/§16):

```
capability_missing → researching → designing → building → testing → classifying
   → (awaiting_approval →) rolling_out → registering → available → used → verified
   ↘ failed (from any state; error_class + message; the gap stays open; production untouched)
```

- `capability_missing`: the registry could not resolve; the gap recorded with the M7 trail (composition attempted first — reused, not reimplemented).
- `researching`: `GET /spec` → `InterfaceDescription` (§2).
- `designing`: the `AdapterSpec`; the risk classification input assembled.
- `building`: `HttpAdapterGenerator` in the M7 isolated workspace (the existing `EvolutionPipeline` stages `isolated_workspace` → `sandbox`, unchanged; the pipeline gains a `generator` chosen by the request's `interface` presence).
- `testing`: the M7 sandbox runs the rendered tests + evals against the fixture application (the sandbox policy gains `allow_loopback_http` — loopback only, the one base URL, nothing else; a rendered test that reaches any other host is a sandbox violation and fails the run).
- `classifying`: `authority_class` / `side_effect_class` derived from the description (any `mutate` operation → `mutate_external`), the M18 `derive_risk_tier` on the workspace paths, the supply-chain gate (stdlib only for M24 adapters).
- `awaiting_approval` ONLY when authority requires it: a `mutate_external` capability against an asset that is not recorded as owner-authorized (`AuthorizationProvider.verify(asset_ref)` is None) parks here and publishes `capability.genesis` `awaiting_approval`; the owner's "Onaylıyorum" / "Bu uygulamayı yetkilendir" through the ONE router records the authorization (`POST /v1/security/assets` — the existing asset registry, or the M18 Approval Center `authorize`) and the run continues. An asset already authorized never asks (the constitution: within authorized scope, no repetitive permission questions).
- `rolling_out`: shadow → canary through the existing runners (the eval cases as the rollout cases), then `registering`: `CapabilityRegistry.register` after `gates_passed` — never before.
- `available`: the capability resolves; the receipt says so.
- `used`: the ORIGINAL request is executed through `CapabilityDispatcher` (isolated subprocess) — the task parked by `TaskResumer` resumes.
- `verified`: the `evidence_contract.read_back` operation is dispatched and its result satisfies the declared postcondition (e.g. the counter read back equals the value the mutation reported). Only then is the owner told the thing was done.

Bounds: one run per capability at a time; ≤ 10 minutes end to end; ≤ 3 runs per hour per interface (a repeatedly failing genesis is a defect, not a retry loop — the supervisor's `insufficient_valid_findings` discipline).

## 6. Voice (through the ONE router; every state a truthful sentence)

Intents: `CAPABILITY_REQUEST` (an owner request naming a controllable thing the registry cannot resolve — the router resolves the target from the utterance and the interface catalogue; "Sayaç kutusunu bir artır", "Test lambasını aç", "Sayaç kaç?"), `CAPABILITY_STATUS` ("Yeni yetenek ne durumda?", "Onu yapabiliyor musun artık?"), `CAPABILITY_APPROVE` ("Onaylıyorum", "Bu uygulamayı yetkilendir" while a run awaits approval — a `Confirmation` bound to session + turn, the M21 discipline), `CAPABILITY_CANCEL` ("Vazgeç, yapma"). Tools: `capability.request {target, operation, arguments}`, `capability.status {target?}`, `capability.approve {run}`, `capability.cancel {run}`.

Receipts (`ActionReceipt`, subsystem `genesis`) say exactly one of the directive's six truths, in result-first Turkish: **yetenek yok** ("Bunu henüz yapamıyorum: sayaç kutusu için bir bağdaştırıcım yok. Yapmayı deniyorum."), **aday hazırlanıyor** ("Sayaç kutusu bağdaştırıcısını yazıyorum."), **test ediliyor** ("Sınıyorum."), **hazır** ("Hazır; onayınızı bekliyor." / "Hazır, kaydediyorum."), **kullanılabilir** ("Artık yapabiliyorum: sayaç bir arttı, şimdi 4."), **başarısız** ("Olmadı: testler geçmedi — sayaç kutusu cevap vermiyor."). A refusal is a refusal receipt; a query is not a receipt. Numbers spoken are numbers read back (the M22 rule).

Corpus category `genesis` (≥ 100 cases): the utterances above and their variants (deixis "bunu bir artır" with the fixture in focus, ASR noise, no diacritics); the flow driven end to end in the harness against the fixture application started by the harness on a free port (real HTTP, real subprocess dispatch, no model); negatives: "Sayaç kutusunu sil" → no tool; a target that is not a local application ("google'ı bir artır") → refused before any research; "Onaylıyorum" with nothing awaiting → clarification, no side effect; "Bunu teknik anlat." unchanged; every earlier category unchanged. The forbidden-side-effect count is 0: the fixture application's counter changes ONLY in cases that expect it, asserted by reading the fixture after every case.

## 7. The fixture applications and the generalisation proof (the directive's synthetic scenario)

`services/api/tests/fixtures/genesis/counterbox_app.py` — "Sayaç Kutusu": a stdlib `http.server` on 127.0.0.1:<free port> with `GET /spec` (its `InterfaceDescription`: `read` GET `/counter` → `{value: integer}`; `increment` POST `/counter/increment` `{by: integer}` → `{value}`, side_effect mutate, idempotent false; `reset` POST `/counter/reset` → `{value: 0}`) and the endpoints. `lampbox_app.py` — "Test Lambası": a different shape (`state` GET `/lamp` → `{on: boolean, brightness: integer}`; `set` POST `/lamp` `{on, brightness}`; `toggle` POST `/lamp/toggle`). Both are TEST APPLICATIONS (fixtures), never products; they run only inside tests and the corpus harness.

Proof classes: `test_genesis_end_to_end.py` drives the whole run for the counter box through `GenesisService` (no shortcut: the generator is called with the description fetched from the running fixture; the rendered adapter is executed as a subprocess; the counter really increments; the read-back verifies). `test_genesis_generalisation.py` runs the SAME service against the lamp box and asserts the lamp really toggles — and `test_genesis_no_shortcut_guard.py` asserts the generator and service sources contain none of the fixtures' names, paths or operation ids, and that deleting the fixture's `/spec` makes the run fail at `researching` (nothing cached, nothing hardcoded). The failure matrix: the app down at `testing` → `failed` with `dependency_unavailable`; an off-schema response → `postcondition_failed`; a description outside the subset → `validation_error` at `researching`; a mutating operation on an unauthorized asset → `awaiting_approval`, and a cancel leaves no registration; a second identical request while a run is active → the same run's status, no second run.

## 8. The Living Core and the Cockpit

UI contract v9 (additive): `capability.genesis` with metadata `{capability, state, approval_required?, error_class?}` — states are the §5 names; published by `GenesisService` at each transition from rows only. The Cockpit panel "Yeni Yetenek" lists runs (capability, state, when, the error when failed), an "Onayla" control ONLY in `awaiting_approval` (`POST /v1/genesis/runs/{id}/approve`), "Vazgeç" while active, and the honest empty state "Henüz yeni bir yetenek istenmedi". No progress bar; no "improving".

## 9. Security

- The only reachable network from an adapter: its one loopback base URL (manifest + sandbox + the dispatcher's subprocess environment: `PAGENTOS_GENESIS_BASE_URL` only; the M7 subprocess isolation unchanged).
- The interface description is data from an untrusted local process: parsed at a choke point, bounded, tokens re-validated at every splice; schemas rendered from the model; no `eval`, no format strings from the description.
- Authority: a `mutate_external` capability is never registered as usable without either an authorized asset or an explicit owner approval bound to session + turn; approval by the model's own argument is impossible (the router's recorded owner signal, ADR-0080).
- Nothing in `app/genesis` may write outside the M7 workspace; the supply-chain gate refuses any non-stdlib import in a rendered adapter; recursion limit: a genesis run may not trigger another genesis run.
- The registry rules of M7 unchanged: `active` only after independent evaluation + review + rollout evidence.

## 10. Marks sought

The end-to-end genesis on the counter box PROVEN_AUTOMATED with real HTTP and real subprocess dispatch; the generalisation to the lamp box PROVEN_AUTOMATED; the no-shortcut guard PROVEN_AUTOMATED; voice PROVEN_AUTOMATED (corpus `genesis`); TTS semantics through the loopback proxy where credits allow; the Living Core PROVEN_AUTOMATED; the Cloud Core half PROVEN_REAL (release) with a genesis run executed on production against a fixture application started on the host for the read-back (owner-session gated, the fixture stopped after).
