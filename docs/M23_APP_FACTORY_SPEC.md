# M23 — App Factory

Status: DRAFT (owner master directive 2026-09-07, M23 section; kickoff at the M22 gate). Decision record: ADR-0086 (written at kickoff).
Predecessors: M6/M18.4 evolution (`CodingBackend` Protocol with `DeterministicCodingBackend` and `ClaudeCodingBackend`; `SkillGenerator`; the sandbox policy and protected trees), M13 browser (the device browser worker through `BrowserGateway` / `DeviceBrowserGateway`: `browser.session_open`, `browser.fetch_evidence`, headless only under the window monitor), M19 Digital Operator (`terminal.*` allowlisted read-only, `file.open`, the focus guard), M20/M22 (`AuthorisedRoots`, `file.fetch`, validation by independent readers).

The owner's rule, in one line: **an app the assistant made exists when it has been scaffolded into a real project on the owner's machine, run there in a bounded process, exercised through the browser or the Digital Operator, and its own tests have passed — and nothing of that touches the owner's other projects.**

## 1. The `AppProject` model

`app_projects` (expand-only migration): `id`, `name`, `kind ∈ {web_static, web_api, cli}` (desktop/mobile are M28), `template` (`task-tracker`, `static-page`, `cli-tool`), `spec_json` (an `AppSpec`: name, kind, entities/fields/screens for `task-tracker`, commands for `cli`), `device_id`, `root_path` (under the device's `Projects` root: `%USERPROFILE%\Documents\PagentOS Projects\<slug>` — a NEW authorised root added for M23, the ONLY root the device may write source into), `state ∈ {planned, scaffolded, running, tested, failed, stopped}`, `run_port`, `last_run_log_ref`, `test_report_json`, timestamps; focus kind `project` on the M19 stack.

## 2. Generation (Cloud Core, `app/appfactory/`)

`AppGenerator` (Protocol): `generate(spec) -> ProjectFiles` (a list of `{path, text}`, bounded: ≤ 200 files, ≤ 2 MiB, paths relative, no `..`, no absolute, no reserved names). `DeterministicAppGenerator` renders the built-in templates — the sample **task-tracker** is a static single-page app (HTML + CSS + vanilla JS, `localStorage`, Turkish UI, a `tests/` folder with a Node test runner script over the app's pure functions) and a `README.md`; `ClaudeAppGenerator` (optional, behind the same Protocol, the CLI backend M18.4 already has) may fill a template's marked slots, never write outside the file list. Every generated file set is validated before it leaves the Cloud Core: the path policy above, a size bound, no secrets (the M13 secret scanner), and a template manifest that names the entry point, the run command (from a fixed allowlist: `node`, `python -m http.server`, `npm --prefix … run start` only with a lockfile the template shipped), the test command and the port.

## 3. Device capabilities (family `projects`, `OperatorEnabled`-gated; §6l of the protocol)

| capability | payload → result |
|---|---|
| `project.scaffold` | `{project_id, slug, files: [{path, text}], manifest}` → `{root_path, files_written, sha256_by_path}`; writes ONLY under `Projects\<slug>` (resolve-then-contain; a slug is a plain name; an existing non-empty folder with a different `project_id` marker is refused — never overwrite an owner project; the marker `.pagentos-project.json` names the project id) |
| `project.run` | `{project_id, command_key}` → `{pid, port, url, started_at}`; the command comes from the manifest's allowlist (the payload names a KEY, never a command line), runs in a Windows Job Object with kill-on-close, memory ≤ 512 MiB, CPU-time bound, cwd = the project root, an environment scrubbed of secrets (`PAGENTOS_*` and known credential variables removed), stdout/stderr captured to a bounded log under the project root, bound to `127.0.0.1` on the port the manifest names (free-port check), ≤ 30 min lifetime, at most 2 running projects; the process is NEVER `Chrome`, `PowerShell`, `Node`, `Python` of the owner's — it is the companion's own child in its own job |
| `project.status` | `{project_id}` → `{state, pid, port, uptime_s, log_tail}` |
| `project.stop` | `{project_id}` → `{stopped: true}` (the job object closed — only the companion's own children die) |
| `project.test` | `{project_id}` → `{exit_code, passed, failed, report_tail}` (the manifest's test command in the same bounded job, ≤ 5 min) |

## 4. Exercising the app

- A `web_static` / `web_api` project is opened through the EXISTING browser worker (`browser.session_open` on `http://127.0.0.1:<port>/`, headless, the worker's own profile; `browser.fetch_evidence` for the DOM/screenshot) — the M13 gateway, no new browser path; DOM assertions per template (the task-tracker: add a task through the form, see it listed, mark it done, reload, still there).
- A `cli` project is exercised through `project.test` and, for its help text, through `terminal.execute` extended by ONE allowlisted entry: the project's own entry command under its root (`node <root>\cli.js --help`), read-only.
- The Digital Operator may additionally open the project folder (`file.reveal`) and the README (`file.open`).

## 5. Voice

Tools `app.create {template, name, spec}`, `app.run {target}`, `app.test {target}`, `app.stop {target}`, `app.status {target}`, `app.open {target}` (the browser evidence + the folder), `app.list {}`; intents `APP_CREATE` ("Bana bir görev takip uygulaması yap", "Küçük bir web sayfası uygulaması oluştur: adı Notlarım"), `APP_RUN` ("Uygulamayı çalıştır"), `APP_TEST` ("Testleri çalıştır"), `APP_STOP` ("Uygulamayı durdur"), `APP_STATUS` ("Uygulama çalışıyor mu?"), `APP_OPEN` ("Uygulamayı aç"), `APP_LIST` ("Hangi uygulamaları yaptın?"); every receipt names the project, the port when running, the test counts; the corpus category `apps` ≥ 100 with negatives ("Projeyi sil" → no tool; a project name with a path → refused; "Uygulamayı çalıştır" with nothing scaffolded → clarification).

## 6. The lab and the automated qualification

Device lab (real files under the run's `Projects` root inside `%TEMP%\pagentos-operator-fixture`, the real dispatcher): scaffold the task-tracker → files and marker present, an outside path refused, an owner folder without the marker refused; run → a real `python -m http.server` child in a job object on a free port, `GET /` answers the app's HTML, the log captured, two projects at once, a third refused; stop → the child gone, nothing else killed; test → the template's tests pass; a manifest with a non-allowlisted command refused before any process; the environment scrub proven (a `PAGENTOS_TEST_SECRET` set in the companion's environment is absent in the child's). Cloud Core: the generator's outputs validated against `services/api/tests/fixtures/apps/` (the task-tracker's expected file list and the DOM oracle), the tools through `create_app`, the corpus; browser evidence through the M13 fake gateway in unit tests and the real headless worker in the device lab for the DOM assertions. UI contract v8 `app.factory` (`{project, state, port?}`), the Cockpit "Uygulamalar" panel.

## 7. Marks sought

Scaffold / run / stop / test in a bounded job PROVEN_REAL on this machine and the runner; the DOM exercise of the sample app PROVEN_REAL (headless worker) on this machine; generation + validation PROVEN_AUTOMATED; voice PROVEN_AUTOMATED; the deployed agent PROVEN_PROXY (item 28); the Cloud Core half PROVEN_REAL (release).
