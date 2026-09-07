# M19 — Digital Operator

Status: IN PROGRESS (started 2026-09-07 late night under the owner's master directive "CLOSE M18.4 AND COMPLETE M19 -> M28"). Decision record: ADR-0082.
Predecessors: `packages/protocol/DEVICE_PROTOCOL.md` (the one command envelope, the Session Companion as the only interactive executor), `docs/M18_ACTION_CONTRACT.md` (receipts, the one router, `state.now`), M13 `BROWSER_CAPABILITIES.md` (the browser worker), ADR-0076/0077 (ID-based object focus, the reference resolver), ADR-0080 (the Owner Utterance Corpus), ADR-0081 (self-evolution foundation, blue/green, the device handoff).

The owner's rule for this milestone, in one line: **the system observes, operates and verifies the owner's Windows environment through the canonical path — Cloud Core → capability contract → Device Service → Session Companion → the owner's interactive session — and never assumes a click succeeded.**

## 1. Canonical path and the two invariants

```
owner utterance / task
  → the ONE router (app/voice/intents.py) → operator tool (app/voice/realtime_sessions/tools_operator.py)
  → OperatorTask (app/operator/): OBSERVE → PLAN → ACT → OBSERVE AGAIN → VERIFY POSTCONDITION
  → device command (DeviceCommandClient, at-least-once / effectively-once, DEVICE_PROTOCOL §5)
  → Device Service (Session 0: routes the interactive family over the authenticated pipe)
  → Session Companion (the owner's session: the only process that touches windows and input)
  → receipt (M18 action contract §5.5) with observed_after from a second OBSERVE
```

Invariant 1 — **no alternate desktop-control path.** Every M19 capability is an interactive-family name in `AgentCapabilities` (`IsInteractive`), executed by the companion, reachable only through a device command. No REST route, no script, no worker touches the desktop directly.

Invariant 2 — **the focus guard.** Before any keyboard or pointer action the companion compares the EXPECTED window (handle + process image + title prefix, from the plan's OBSERVE) with the ACTUAL foreground window at the moment of acting. A mismatch is a refusal (`error.class = "focus_mismatch"`, retryable) that carries both, so the planner re-resolves or gives up; it is never a retry into whatever window is in front. No secret is ever typed: payload text goes through the same forbidden-key scan as every tool argument, and `keyboard.type` refuses a payload flagged `secret: true` outright — secrets are the password manager's job.

## 2. Capability families (all interactive; the companion executes; the service routes)

Names, payloads and results. Every result carries `observed: {...}` — what the companion saw AFTER acting, never what it intended. `window_id` is the companion's stable id for an HWND for the life of that window (`w-<hwnd>-<create tick>`); `pid` is the OS process id. Every family member is advertised only when `OperatorEnabled` is on in the service configuration (the installer's `-Operator` switch; off by default until the M19 gate is green on this machine, then on).

| Capability | Payload | Result / postcondition observed |
|---|---|---|
| `app.launch` | `{application, args?}` — an allowlisted name (`notepad`, `calc`, `explorer`, `powershell`, `chrome`, `msedge`) or an absolute path under Program Files / Windows | `{pid, window_id?, title?}` — the window that appeared for that pid within 10 s (or `window_id: null` and `observed.window_appeared: false`) |
| `app.list` | `{}` | `{applications: [{pid, image, name, window_count, windows: [window_id...]}]}` for processes with a top-level window |
| `app.activate` | `{pid \| window_id}` | `{window_id, foreground: true}` — verified by `GetForegroundWindow` after |
| `app.close` | `{pid \| window_id, force?: false}` | `{closed: bool, method: "wm_close" \| "terminated"}` — WM_CLOSE first; a still-alive process after 5 s is reported, not killed, unless `force` |
| `window.list` | `{pid?}` | `{windows: [{window_id, pid, title, image, state: normal\|minimized\|maximized, rect, foreground}]}` |
| `window.current` | `{}` | `{window: {...}}` the foreground window, or null |
| `window.activate` / `.minimize` / `.maximize` / `.restore` / `.close` | `{window_id}` | `{window: {...}}` re-observed after the action; `observed.state` must equal the requested state or the result is `failed/postcondition_failed` |
| `window.move` | `{window_id, x, y}` | `{window: {...}}` with `rect.x/y` within 8 px of the request |
| `window.resize` | `{window_id, width, height}` | `{window: {...}}` with `rect` within 8 px |
| `keyboard.type` | `{window_id, text, secret?: false}` | `{typed_chars, window_id}` — Unicode via `SendInput` KEYEVENTF_UNICODE; guarded (§1) |
| `keyboard.key` | `{window_id, key}` (`enter`, `escape`, `tab`, `backspace`, `f1`..`f12`, arrows, `home`, `end`, `delete`) | `{key, window_id}` |
| `keyboard.shortcut` | `{window_id, keys: ["ctrl","s"]}` | `{keys, window_id}` |
| `pointer.move` / `.click` / `.double_click` / `.right_click` | `{window_id, x, y, space: "window" \| "screen"}` | `{x, y, window_id}` — window space by default; guarded |
| `pointer.scroll` | `{window_id, x, y, delta}` | `{delta}` |
| `ui.inspect` | `{window_id, depth?: 3, max_nodes?: 200}` | `{root: {automation_id, name, control_type, bounds, value?, children: [...]}}` — UI Automation tree, bounded |
| `ui.invoke` | `{window_id, automation_id \| name+control_type}` | `{invoked: true, element: {...}}` — Invoke pattern |
| `ui.set_value` | `{window_id, automation_id \| name, value}` | `{element: {...}, observed_value}` — Value pattern, read back |
| `ui.select` | `{window_id, automation_id \| name, item}` | `{element, selected: item}` — Selection pattern, read back |
| `screen.capture` | `{window_id?, format: "png"}` | `{width, height, png_base64}` (≤ 2 MB; a window or the primary screen) — never persisted by the companion |
| `screen.inspect` | `{window_id?}` | `{monitors: [...], foreground: window, cursor: {x,y}}` |
| `browser.open` / `browser.navigate` / `browser.inspect` | the M13 family (`browser.session_open`, `browser.navigate`, `browser.read`/`browser.dom_inspect`) — already built; M19 adds the operator-facing aliases in the tool layer only | as M13 |
| `file.open` | `{path}` — inside an owner-authorised root | `{opened: true, path, window_id?}` (ShellExecute; `desktop.open_artifact` stays for artifacts) |
| `file.reveal` | `{path}` | `{revealed: true, window_id}` — Explorer with the item selected |
| `terminal.open` | `{shell: "powershell"}` | `{pid, window_id}` |
| `terminal.execute` | `{command, timeout_s?: 30}` — an ALLOWLISTED read-only command (`hostname`, `whoami`, `ipconfig`, `Get-Date`, `Get-ComputerInfo -Property ...`, `Get-Process -Name ...`, `Get-ChildItem <authorised root>`) | `{exit_code, stdout, stderr, duration_ms}` — run headless in the owner's session (`powershell -NoProfile -NonInteractive -Command`), never in the visible terminal window |
| `terminal.status` | `{pid}` | `{alive, exit_code?}` |

Risk classes (`docs/SECURITY_MODEL.md`): `app.list`, `window.list/current`, `ui.inspect`, `screen.*`, `terminal.status` = READ_ONLY; `app.activate`, `window.*` (not close), `pointer.move`, `keyboard.key` navigation = REVERSIBLE_LOCAL; `app.launch`, `app.close`, `window.close`, `keyboard.type`, `pointer.click*`, `ui.invoke/set_value/select`, `file.open/reveal`, `terminal.open` = MUTATING_LOCAL; `terminal.execute` = MUTATING_LOCAL bounded by the allowlist (anything not allowlisted is `permission_denied`, never run). No EXTERNAL_SIDE_EFFECT capability exists in M19.

Interaction preference (the browser rule, generalised): official API → browser DOM (the M13 worker) → UI Automation (`ui.*`) → keyboard navigation (`keyboard.key/shortcut`) → visual targeting (`screen.capture` + the model) → raw coordinates (`pointer.*` in screen space) last. The planner records which level it used in the receipt (`observed_after.local.interaction_level`).

## 3. The companion side (`PagentOS.SessionCompanion/Operator/`)

- `WindowRegistry` — `EnumWindows` / `GetWindowThreadProcessId` / `GetWindowText` / `IsWindowVisible` / `GetWindowPlacement` / `GetWindowRect` / `GetForegroundWindow`, stable `window_id`s; `WindowActions` — `ShowWindow`, `SetForegroundWindow` (with the `AttachThreadInput` + `AllowSetForegroundWindow` dance and a verified retry), `MoveWindow`, `PostMessage(WM_CLOSE)`.
- `FocusGuard` — `Expect(window_id)` → `Verify()` compares handle, pid, image and title prefix with the foreground; refuses on mismatch with both sides in the error.
- `InputSynthesizer` — `SendInput` (KEYEVENTF_UNICODE for text; virtual keys for `key`/`shortcut`; mouse absolute moves in screen space converted from window space).
- `UiAutomationInspector` — `System.Windows.Automation` (the `UIAutomationClient` assembly of the Windows Desktop runtime; `<UseWPF>true</UseWPF>` on the companion is NOT taken — the project references `UIAutomationClient`/`UIAutomationTypes` directly to keep the process console-only), bounded tree walk, patterns Invoke/Value/SelectionItem.
- `ScreenCapture` — `PrintWindow` / `BitBlt` to PNG (`System.Drawing` is not used; a minimal PNG encoder over `Bitmap`-free GDI capture keeps the companion's dependency set), never written to disk.
- `TerminalRunner` — allowlisted commands only, `Process` with redirected output, a hard timeout, `NoProfile -NonInteractive`.
- `OperatorCapabilities` — the dispatch table added to `CompanionRuntime.Execute` (one `case` per name, every name in `AgentCapabilities.Operator`), the `OperatorEnabled` gate in `AgentServiceOptions`, the family's per-command cap (30 s; `app.launch` 15 s).

## 4. The Cloud Core side

- `app/operator/` — `OperatorTask` (goal, steps `[{capability, payload, precondition, postcondition, timeout_s, retries ≤ 2}]`, the loop OBSERVE → PLAN → ACT → OBSERVE → VERIFY; each step's postcondition is a predicate over the re-observed result, e.g. `window.state == maximized`, `ui value == text`), `plans.py` (deterministic plans for the voice tools: open app, close current, maximize current, type into current, go back to the previous window, open PowerShell, show IP), `focus.py` — the window/app object focus (`current_window_id`, `previous_window_id`, `current_app_pid`), an owner-level durable stack in the `object_focus` table (migration 0025; one generic table for M19–M28 object kinds: `kind`, `object_id`, `label`, `selected_at`, `source`) — `research_focus` stays as it is.
- Voice tools (`tools_operator.py`): `operator.app_open`, `operator.window_control` (close/maximize/minimize/restore/activate/previous), `operator.type`, `operator.shell` (IP address, hostname), `operator.cancel` (the running task), `operator.status`; each answers through a receipt; `activity.explain` gains the `operator_now` kind ("Ne yapıyorsun?" while a task runs).
- Intents (`intents.py`): `APP_OPEN` (Not Defteri'ni aç / Chrome'u aç / Tarayıcıyı aç / PowerShell aç / hesap makinesini aç), `WINDOW_CLOSE` (bunu kapat / bu pencereyi kapat / öndeki pencereyi kapat), `WINDOW_MAXIMIZE`, `WINDOW_MINIMIZE`, `WINDOW_RESTORE`, `WINDOW_PREVIOUS` (önceki pencereye dön), `TYPE_TEXT` (buraya X yaz / bu kutuya X yaz / seçili yere X yaz), `SHELL_QUERY` (IP adresimi göster / bilgisayarın adı ne), plus the existing `STOP` / cancel routed to `operator.cancel` while an operator task is running (ringing-style awareness: `operator_running` in `resolve_intent`).
- The Living Core: `UiState.OPERATOR_RUNNING = "operator.running"` / `operator.verifying` / `operator.failed` (contract v4, additive), published from OperatorTask transitions; the Cockpit shows the current step and the observed window.
- Receipts: every operator action is an `ActionReceipt` (`capability = operator.*`, `observed_after.local` = the second OBSERVE), subsystem `operator`; ledger rows `operator.task.started/completed/failed/cancelled`.

## 5. The Digital Operator test lab (automated, real, on this machine)

`devices/windows-agent/tests/PagentOS.Agent.Tests/Operator/` — xunit, real processes, harmless apps, each test cleans up its own windows: Notepad launch → window observed → activate → `keyboard.type` "Merhaba Dünya ğüşöçı İ" → `ui.inspect` reads the Document's value back → exact Unicode compare → maximize/restore/move/resize with re-observed rects → close → verified gone; File Explorer on a fixture folder (`%TEMP%\pagentos-operator-fixture\`) → `ui.select` the file → open → verified; browser via the fake worker for routing and the headless e2e suite for the DOM; `terminal.execute hostname` → exit 0 + the machine's name; wrong-focus prevention (two Notepads, expect A, bring B to front → `focus_mismatch`, nothing typed into B — verified by reading B's document); modal detection (Notepad "Save?" dialog → `ui.inspect` finds the dialog, `app.close` reports `closed: false, modal: {...}`); cancellation (a long `terminal.execute` cancelled → `cancelled`); timeout; bounded retries; companion restart recovery (a task mid-flight survives a companion restart as `failed/dependency_unavailable` with the receipt saying so, and the next command works). Cloud Core: `tests/unit/test_operator_task.py` (the loop, postconditions, retries, cancellation, the focus objects) with a fake device; `tests/unit/test_operator_tools.py` through the real app object; corpus category `operator` (≥ 120 cases incl. ASR noise, deixis, negatives: "Bunu kapat" with no window focus → clarification not a close; "Buraya şifremi yaz" → refused; "Chrome'u aç" never reaches `browser.session_open`).

## 6. Owner-machine execution and honesty

The lab runs on this machine under the test runner in the OWNER's session (the same session the companion uses): Notepad windows will open and close on the owner's desk during the run. Tests never touch a window they did not create, never type into a window they did not just verify, and never run a shell command outside the allowlist. The installed agent is 0.1.0 (item 26 pending): the real-device proof is the test lab (real processes, real windows, real UI Automation), classified PROVEN_REAL for the mechanisms and PROVEN_PROXY for the deployed-agent path until the owner's elevated update ships 0.2.0 with `-Operator`.

## 7. Exit criteria and marks

| Criterion | Mark sought |
|---|---|
| Every family member executes through the companion with a re-observed result; the focus guard refuses a mismatch; secrets are never typed | PROVEN_REAL (test lab) |
| OperatorTask: OBSERVE → PLAN → ACT → OBSERVE → VERIFY, retries bounded, cancel, timeout, modal, restart recovery | PROVEN_REAL (lab) + PROVEN_AUTOMATED (Cloud Core) |
| Voice: the corpus category `operator` green with 0 forbidden routes; TTS structural checks per case | PROVEN_AUTOMATED |
| Living Core states from real task state | PROVEN_AUTOMATED |
| The deployed agent path (0.2.0 with `-Operator`) | PROVEN_PROXY until item 26/27 |
