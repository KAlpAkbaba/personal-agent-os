---
name: m23-app-factory-review
description: M23 App Factory review findings (git diff 486491a..6259f61, merged main@6259f61, 2026-09-08). Critical (verified live, PoC executes) - spec.page_title/page_heading (AppSpec) have none of AppSpec.name's slash/newline restrictions and html.escape() does not neutralize newlines, so a newline+"//" in page_title breaks out of a `//` comment in the generated static-page app.js and becomes live JS that runs in the M13 headless browser session. Medium - Cloud Core validation.py accepts ADS paths (a.txt:x) and mid-segment trailing-space reserved names that the device's ProjectScaffold.cs independently and correctly refuses (defense-in-depth inconsistency only, not exploitable). Device-side path/job/manifest/root mechanics (ProjectScaffold.cs, ProjectManifest.cs, ProjectRunner.cs, JobObject.cs, ProjectRoots.cs) verified sound live via dotnet test (155 Projects tests incl. real python/node children, real junction escape attempt, real env-scrub read from child) and the exercise-URL/oracle boundary in service.py verified honest (server-built from run_port only, no vacuous DOM claims).
metadata:
  type: project
---

Reviewed `git diff 486491a..6259f61` (M23 App Factory, merged to `main` at 6259f61,
2026-09-08): Cloud Core `services/api/app/appfactory/{spec,generator,validation,service,
routes,oracles}.py`, `templates/*`, `voice/realtime_sessions/tools_apps.py`, migration
`20260908_0030_app_projects.py`; device
`devices/windows-agent/src/PagentOS.SessionCompanion/Projects/{ProjectScaffold,
ProjectManifest,ProjectRunner,JobObject,ProjectRoots}.cs`, `Operator/TerminalRunner.cs`
diff (the `<project-entry>` allowlist token); `packages/protocol/DEVICE_PROTOCOL.md` §6l;
web `apps/web/app/lib/uistate/apps.ts` + `lib/cockpit/useAppsControl.ts`. Read
`docs/M23_APP_FACTORY_SPEC.md`, ADR-0086 + addenda 1-3 in `docs/DECISIONS.md` first.
Method continues [[junction-escape-recurring-pattern]] and the M20-M22 verify-live
discipline. See also [[m22-artifact-factory-device-review]], [[m19-digital-operator-security-review]].

**Critical (verified live, working PoC) — `AppSpec.page_title`/`page_heading` carry none
of `name`'s injection defences, and `html.escape()` does not neutralise newlines, so
owner/model text becomes live JavaScript in the generated static-page app.**
`services/api/app/appfactory/spec.py` gives `AppSpec.name` a dedicated validator
(`_name_shape`, lines 132-148) that rejects NUL, `/`, `\`, `..` and a drive prefix — but
`page_title`/`page_heading`/`page_body` (lines 125-127) carry ONLY a `max_length`, no
character restriction at all, not even the NUL/slash checks `name` gets. In
`generator.py::DeterministicAppGenerator._static_page_slots` (line 147),
`title = html.escape(spec.page_title or spec.name)` is substituted into
`templates/static-page/app.js`'s very first line, `// {{PAGE_TITLE}} — nothing to wire
up...`. `html.escape()` neutralises `<`, `>`, `&`, quotes but NOT newlines. Because
`page_title` is unrestricted, a value containing `\n` ends the `//` line comment early;
because `page_title` (unlike `name`) is NOT blocked from carrying `/`, the attacker can
follow with a second `//` that comments out the rest of that original template line,
leaving the following original comment line and the real `(function(){...})()` untouched.

Verified end-to-end with the real repository code (no source edited): built
`AppSpec(name="Notlarim", kind="web_static", template="static-page",
page_title="X\nwindow.__pwned=1;//", page_heading="Notlarım", page_body="hello")`,
ran it through the real `DeterministicAppGenerator().generate(spec)` and the real
`app.appfactory.validation.validate(files)` — **`validate()` returns `ok=True`**, the
project is accepted (this is what `AppFactoryService.create` calls right before handing
the file list to the device). The resulting `app.js`:
```
// X
window.__pwned=1;// — nothing to wire up beyond a page-load log line; this template is a
// static page (docs/M23_APP_FACTORY_SPEC.md §2), not an interactive app.
(function () { ... })();
```
`node --check` on this file reports valid syntax, and running it with `window`/`document`
stubs (`node -e "global.window={};global.document={addEventListener:function(){}};
require('./app_out.js'); console.log(window.__pwned)"`) prints `1` — the injected
statement executes as real top-level JavaScript. Since `exercise()`/`open()`
(`service.py` lines 488-573, 796-880) open the scaffolded, running project's `index.html`
through the EXISTING M13 `BrowserGateway`/headless worker (`browser.session_open` on
`http://127.0.0.1:<run_port>/`), this JS runs inside that browser worker session — full
script execution in a browser context the assistant does not intend to grant, directly
violating the generator module's own stated invariant ("every spec value that reaches
generated source is HTML/JSON-escaped or re-validated... because nothing here trusts
that pipeline blindly a second time" — the developers tested the HTML-injection case
(`test_task_tracker_title_is_html_escaped_and_present`, an `<img onerror=...>` payload)
but never a JS-comment-context newline breakout, and no test in
`test_appfactory_generator.py`/`test_appfactory_spec.py`/`test_appfactory_validation.py`
catches it).

Reachability: `page_title` is populated from the free-form `spec` object argument the
`app.create` voice tool passes straight to `AppSpec.model_validate()`
(`tools_apps.py` — the tool's own JSON Schema is `"spec": {"type": "object"}`, no
character constraints at the tool layer either), so a model completing a static-page spec
from a long piece of owner-supplied or pasted text has no structural reason to avoid
putting a literal newline in `page_title`; this does not require an adversarial model,
only ordinary text containing a newline.

Task-tracker/cli-tool templates use `APP_TITLE = html.escape(spec.name)` for the
equivalent comment slot, and `name`'s validator blocks `/` and `\`, which defeats the
`//`-comment and `/* */`-close techniques (no natural closing delimiter exists elsewhere
in those template files for a block comment or backtick string opened without `/`); a
`\n`-only payload there produces a syntax error (denial of the generated app, not clean
injection) rather than confirmed code execution — still a correctness bug (an owner's
name choice can silently break their own generated app) but not verified as RCE the way
the static-page path is.

**Fix:** at the AppSpec layer, give `page_title`/`page_heading`/`page_body` (and any
future free-text field reaching a template splice point) the same defence as comment-
context substitution actually needs: strip or reject control characters INCLUDING
newline/CR (not just NUL), or — better — never splice free text into a `//`/`/* */`
comment at all; route title-like values only into contexts `json.dumps()` can escape
properly (a JS string literal assignment) or into HTML text nodes (already correctly
escaped). `_require_command_name`'s closed-alphabet approach for `Command.name` is the
right pattern to generalise. Re-validate at the generator's splice point too, per the
module's own stated discipline, not just at `AppSpec` construction.

**Medium — Cloud Core's `validate.py::_check_path` accepts two path shapes the device
independently and correctly refuses, undermining the spec's claim that generation is
"validated before it ever leaves the Cloud Core."** Verified live against the real
`_check_path` (`services/api/app/appfactory/validation.py:87-106`):
`_check_path("a.txt:secret.txt")` and `_check_path("notes/a.txt:hidden")` both return
`None` (accepted) — `PureWindowsPath` only special-cases a `X:` two-character drive
prefix, not a colon embedded later in a segment, so an NTFS Alternate Data Stream path is
not caught. Also `_check_path("notes/CON /file.txt")` (a reserved name with a trailing
space in a NON-final, non-whole-path segment) returns `None` — the whole-path
leading/trailing-whitespace check (`path != path.strip()`) does not catch whitespace
inside an interior segment. Both are actually enforced correctly on the device side
(`ProjectScaffold.NormalisePath`, `devices/.../Projects/ProjectScaffold.cs:79-137`:
`unified.Contains(':')` refuses ADS outright; the reserved-name and trailing-dot/space
checks run per-segment, not just on the whole string) — confirmed by reading
`ProjectScaffold.cs` and by the fact that `ProjectScaffoldTests.cs`/`ProjectRunTests.cs`
(99+56 tests, run live via `dotnet test`, all passing) exercise the device's own path
policy independently. So this is not exploitable as written (device is the last and
correct gate), but it is a real inconsistency with the documented design ("every
generated file set is validated before it ever leaves the Cloud Core") and means Cloud
Core's own refusal message/telemetry for these two cases is wrong (it would report
`ok=True` and only the device would ever refuse, with a less specific error). **Fix:**
mirror `ProjectScaffold.NormalisePath`'s colon check and per-segment
trailing-dot/space check in `validate.py::_check_path`.

**Verified sound (no finding), with the live checks performed:**
- **Junction/link escape of the Projects root and of a project folder**
  (`ProjectRoots.cs`/`ProjectScaffold.cs`): uses `AuthorisedRoots.ResolveFinal`/`IsWithin`
  (resolve-then-contain), NOT the lexical `StartsWith` pattern that was the M3/M8/M19 bug
  ([[junction-escape-recurring-pattern]]). Ran the existing live junction tests
  (`A_junction_inside_a_project_cannot_redirect_a_write_outside_it`,
  `A_junction_under_the_projects_root_is_never_a_project`) via
  `dotnet test --filter "FullyQualifiedName~ProjectScaffoldTests|FullyQualifiedName~ProjectTerminalEntryTests"`:
  56/56 passed, confirming a real NTFS junction inside a project folder cannot redirect a
  write outside it and a junction planted at the Projects root is never treated as a
  project. `AuthorisedRoots.cs` itself is unchanged in this diff (already verified sound
  in [[m22-artifact-factory-device-review]]/M20).
- **The manifest/runtime allowlist** (`ProjectManifest.cs`): matched structurally by
  tokenised argv (never a string-prefix match), `--bind 127.0.0.1` is checked as an exact
  token (`tokens[5] == Loopback`), the port token must equal the manifest's own port or
  the `<port>` placeholder (never `0.0.0.0` or an arbitrary port), composition characters
  (`;|&$(){}` `` ` `` quotes, CR/LF, NUL) are refused before tokenising, `node` runs
  exactly one relative in-project file, `npm` only through `node.exe` + `npm-cli.js`
  (never `npm.cmd`) and only when the template shipped a lockfile. `ProcessStartInfo` uses
  `UseShellExecute=false` and `ArgumentList` (never a shell) — confirmed by reading
  `ProjectRunner.BuildStartInfo`.
- **The Job Object** (`JobObject.cs`): `KILL_ON_JOB_CLOSE`, `LimitJobMemory` (512 MiB),
  `LimitJobTime` (10 min CPU), `LimitActiveProcess` (8), `DIE_ON_UNHANDLED_EXCEPTION`,
  `UI_RESTRICTIONS_ALL`, no breakaway flags ever set — read back from the kernel via
  `ReadLimits()`, not merely asserted at construction. `Terminate()` calls
  `TerminateJobObject` (job members only), never `Process.Kill(pid)`.
- **Real-process live tests** (`dotnet test --filter FullyQualifiedName~Projects`, the
  whole `Projects` test namespace): 99 tests passed, including
  `Stop_ends_only_the_job_and_a_sentinel_started_outside_it_survives` (starts a REAL
  `python.exe` sentinel process OUTSIDE the runner's job and proves `project.stop` and
  runner `Dispose()` never touch it) and
  `The_child_environment_carries_no_pagentos_or_credential_variable` (starts a real node
  child that dumps its OWN actual `process.env` to a file on disk, then the test reads
  that file back and asserts `PAGENTOS_*`/credential-shaped names are absent and `PATH`
  starts with System32) — genuine, not a mock of the scrub function.
- **`run_port`/manifest port**: fixed per built-in template (8765/8766), baked into the
  template file the generator reads, not spec/model-controlled; `AppSpec`
  (`extra="forbid"`) has no field that could smuggle a custom manifest or port through the
  voice tool's `spec` argument. `RequirePortFree` binds `IPAddress.Loopback` only via
  `TcpListener`, so a project can never be bound to `0.0.0.0` or a privileged port
  (`MIN_PORT=1024` enforced both in `validation.py` and `ProjectManifest.ReadPort`).
- **The exercise URL / oracle boundary** (`service.py::exercise`/`open`, `oracles.py`):
  `url = f"http://127.0.0.1:{project.run_port}/"` is built server-side ENTIRELY from
  `project.run_port` (an int the device's own `project.run` result set, stored after a
  real bound port answered) — no spec/model text ever reaches the URL Cloud Core asks the
  browser worker to open. `exercise()` only claims what `BrowserGateway.fetch_evidence`
  itself observed (reachable + non-empty response); it explicitly does NOT claim any DOM
  assertion from `oracles.py` passed — those are recorded on the receipt as what the
  device lab's real headless worker independently proves (confirmed by reading
  `service.py` lines 496-572 and ADR-0086 addendum 2 decision 5). No vacuous-oracle path.
- **`project.delete` truly does not exist**: grepped the whole diff, no delete capability
  name anywhere in `ProjectCapabilities.cs`/`service.py`/`tools_apps.py`. The corpus's
  `app.neg.delete` ("Projeyi sil.") and the two path-in-name negatives
  (`app.neg.path_in_name_dotdot`/`_drive`) and `app.neg.run_nothing_scaffolded` were run
  live (`pytest tests/unit/test_owner_utterance_corpus.py -k "app.create or app.run or
  app.test or app.stop or app.status or app.open or app.list or app.neg"`): 116/116
  passed (matches spec's "≥ 100" bound for the `apps` category).
- **REST identity gating**: `routes.py` router-level `Depends(require_owner_session)`;
  `test_identity_enforcement.py` lists all four routes (`GET /v1/apps`,
  `POST /v1/apps/{id}/{run,stop,test}`); ran the full appfactory + identity-enforcement
  test files live (174 passed) including `test_appfactory_routes.py`'s real-application-
  object + fake-device test of the 404/422/200 mapping.
- **The `<project-entry>` terminal allowlist token** (`TerminalRunner.cs`): the coarse
  `IsUnderAuthorisedRoot` pre-check it also calls is the OLD lexical/junction-vulnerable
  check from [[m19-digital-operator-security-review]], but it is ANDed with
  `_isProjectEntry` (→ `ProjectRoots.IsEntry`, which independently resolves-then-contains
  and requires the resolved file to equal the folder's OWN manifest `entry` exactly) — the
  weak check being bypassable via a junction does not help an attacker because the strong
  check re-derives the real path itself and would not accept a redirected target. Not a
  new vulnerability.
- **The device lab's authenticity** (`ProjectLab.cs`): `Dispose()` calls `Projects.Dispose()`
  unconditionally before deleting the run directory; xUnit calls a fixture's `Dispose()`
  regardless of whether the test method's assertions threw, so cleanup runs on every path
  by construction, not by this code's own effort — confirmed this is the correct reliance,
  not a gap.

**Residual risk.** The Critical finding is the one that matters: it is a genuine
model/owner-text-to-script-execution boundary break in a milestone whose entire premise is
"the assistant writes and runs code on the owner's machine," and it is reachable through
the ordinary `app.create` voice/tool path with no adversarial model required — only a
`page_title` containing a newline. It should block sign-off until fixed. The Medium
Cloud-Core-path-check gap is real but not exploitable today because the device is a sound
second gate; still worth closing so the two validators agree, and because their disagreement
is exactly the kind of drift that has bitten this codebase before when one of two "gates" is
quietly relied upon as the real one.
