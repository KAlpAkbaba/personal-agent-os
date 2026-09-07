# M19 Digital Operator — milestone report (2026-09-08)

Owner directive: master directive "CLOSE M18.4 AND COMPLETE M19 -> M28" (M19 section). Decision record: ADR-0082 + addenda 1–4. Spec: `docs/M19_DIGITAL_OPERATOR_SPEC.md`. QUALIFICATION Stage 17.

**IMPLEMENTED**
- Device (Session Companion, `Operator/`): 32 capability names in eight families (`app.*`, `window.*`, `keyboard.*`, `pointer.*`, `ui.*`, `screen.*`, `file.open/reveal`, `terminal.*`), advertised only with `OperatorEnabled` on both the Device Service and the companion; Win32 window registry/actions with stable window ids; the focus guard (handle + pid + image + title prefix; re-verified before every input batch); SendInput Unicode typing, keys, shortcuts, pointer; UI Automation inspect/invoke/set_value/select (bounded, password fields masked); PNG screen capture (pure managed, never persisted); the allowlisted headless terminal runner; resolve-then-contain path roots; a per-application argument policy; caps 30 s / 15 s; three error classes (`focus_mismatch`, `permission_denied`, `postcondition_failed`) added to the device taxonomy, schema and broker validator. `install-device-service.ps1 -Operator` writes the flag to both configs.
- Cloud Core (`app/operator/`): `OperatorTask` (OBSERVE → PLAN → ACT → OBSERVE AGAIN → VERIFY POSTCONDITION, retries ≤ 2, cancel, timeout, modal, receipt trail + ledger rows), deterministic plans (open app, close/maximize/minimize/restore/activate/previous window, type text with UI Automation read-back, shell query, open terminal), the durable `object_focus` stack (migration 0025), `OperatorService`; six voice tools (`operator.app_open/window_control/type/shell/cancel/status`) and the router's intents (`APP_OPEN`, `WINDOW_*`, `TYPE_TEXT`, `SHELL_QUERY`, `OPERATOR_CANCEL/STATUS`, ringing-style awareness of a running task); UI contract v4 (`operator.running/verifying/failed`).
- Web: the contract v4 tokens, the Core's operator channel and captions from published metadata, the Cockpit "Dijital operatör" panel.
- Voice qualification infrastructure: the TTS → STT loopback proxy harness (`app/voice/loopback.py`, `scripts/voice/tts-loopback-qualification.ps1`, ledger `voice.tts_loopback`).

**TESTED** — the lab on real windows: 78/78 on this machine (Notepad lifecycle with Turkish Unicode read back over UI Automation, Explorer fixture, terminal runner, focus guard with a real mid-stream switch, modal detection, cancellation/timeout, payload guards, advertisement over the real pipe, path confinement with a real junction, argument policy); agent suite 475/475; audio suite 135/135; Cloud Core unit 38 new (task loop, focus, tools through `create_app`, wiring) inside the API unit suite; corpus 536/536; web 869/869 + `tsc`; PowerShell suites 90/16/45/21/32/42. CI: the GitHub `windows-latest` runner ran the same lab: 475/475 agent tests (78 lab tests among them) + 135/135 audio on bd21f7f, after four runner-only findings were fixed (bugs 5-7, 17).

**SYNTHETIC OWNER COMMANDS TESTED** — the corpus category `operator`: 123 cases (canonical, paraphrase, ASR noise, deixis "bunu / şunu / öndeki / buraya / bu kutuya / seçili yere", apps, windows, shell, cancel/status while a task runs, negatives). **VOICE ROUTES PASSED**: 536/536 (all categories). **FORBIDDEN ROUTES / SIDE EFFECTS**: 0 ("Buraya şifremi yaz" refused; "Chrome'u aç" never reaches `browser.session_open`; "Bunu kapat" with no window focus → clarification).

**TTS OUTPUT TEST** — every successful case: non-empty Turkish speech + the audible-turn record (PROVEN_AUTOMATED). **LOOPBACK/STT** — real OpenAI tts-1 → whisper-1 over the corpus's 40 distinct spoken answers: 38 matched, 2 degraded ("Core'daki" spoken as "kor…"; the version "0.1.0" garbled), 0 mismatched, mean WER 0.018; evidence `docs/evidence/tts-loopback-2026-09-07-190306.json` (PROVEN_PROXY; physical hearing not claimed).

**BUGS FOUND / FIXED (regression tests for each)**
1. `OptionalInt` used `GetValue<double>()` on typed JSON values (every lab test) — track A.
2. The spawned PowerShell inherited a System32-less PATH ("hostname not recognised") — System directories prepended (also a hardening).
3. Budget expiry answered `cancelled` instead of `timeout` — re-classified.
4. Classic Notepad's Document has no Value pattern — `WM_SETTEXT` fallback with a Text-pattern read-back.
5. Explorer titles carry a locale suffix on English Windows; Notepad shows "Untitled" for a moment before the file loads — the lab re-observes (runner findings).
6. PowerShell's cold start on the runner pushed a cancel past an 8 s bound — 15 s.
7. The first List control under Explorer is not the items view on English Server — every list is read; and Windows Server's Explorer never reports the `/select` item as selected through UI Automation while it plainly lists it — `file.reveal` now observes `file_visible` and `selection_names_file` separately, and the lab asserts the selection on client Windows only (runner findings).
8. Security review, high: an NTFS junction inside a root escaped the lexical containment — resolve-then-contain; the default roots narrowed.
9. Security review, medium: the focus guard checked once per call — re-verified before every input batch (and a real finding on the way: Windows assigns injected input to the thread that retrieves it, so the refusal carries typed/confirmed/uncertain counts).
10. Security review, medium: launch arguments unpoliced — per-application policy.
11. A platform-wide forbidden-key guard blocks a literal `text` tool argument — `operator.type` uses `content` (track B).
12. Turkish casefolding turns "I" into "ı" — the shell-query matcher accepts both.
13. Timestamp ties on Windows' clock made "latest" ambiguous — the object-focus stack (track B) and the presence eye flag (main) write strictly increasing instants; a frozen-clock test pins the eye flag.
14. The OpenAI STT provider sent raw bytes with query parameters (never worked for real) — multipart; `wav_duration_ms` assumed 16 kHz — reads the fmt chunk (voice track).
15. The companion's typed browser timeout raced the service's deadline on a loaded runner — a full second of headroom.
16. The audio bench's wall-clock tool-silence guard tripped on a loaded runner — a bench option.
17. The typing guard's lab test simulates a focus steal with a `SetForegroundWindow` race against a ≤ 4096-character stream. The GitHub runner showed two shapes the first version did not allow for: the steal landing only after the stream had ended (proves nothing either way — now retried with fresh windows, at most three attempts, a late attempt is inconclusive and never a pass), and the foreground going to NO window at all (the runner's desktop) for the rest of the stream — the guard stopped for it exactly as for a real window, and the test now accepts that shape with its own exact expectations (B holds nothing; A holds every confirmed character and never more than typed; the last uncertain batch may have been discarded by a system with no foreground queue) beside a workstation's (B in front: A + B is exactly the typed prefix). Runner findings; nothing in the product changed.
18. The qualification harness wrote every `deployment.cloud_core.released` ledger row with the FIRST cutover's wording ("ilk blue/green geçişi") — run 10's row says so although it was an ordinary green → blue release; the harness now words the row by what happened and names the colour the edge serves. Cosmetic (the `result` sha, the field the release-evidence provider reads, was always right); found by the runtime read-back.

**SECURITY GATE** — independent review (1 high, 2 medium: all fixed and pinned with real-window tests); the gates verified sound: no alternate desktop path, secrets never typed, results scanned, the terminal allowlist, the broker/schema change purely additive, no authority boundary changed.

**CI STATUS** — run 34160418154 on bd21f7f, 7/7 jobs green (API lint + unit 4912 passed / 2 skipped, API integration, Windows agent build + tests 475/475 + audio 135/135, browser agent, web shell build, recovery supervisor, secret hygiene). **DEPLOYMENT STATE** — the M19 Cloud Core (bd21f7f) is LIVE on production: harness run 10 (`docs/evidence/m18-4-qualification-2026-09-07-205235.json`, 16/16 checks) released it green → blue through the canonical blue/green path with no gap (335 probes, 0 dropped, max latency 950 ms), the device sessions handed to blue before it took HTTP (1/1 after 1 s), presence gap 2.22 s, RELEASE = bd21f7f, LAST_KNOWN_GOOD = 338ffec exported; the Windows agent stays 0.1.0 (29 capabilities, no operator family) until the owner's elevated update (item 28). **RUNTIME VERIFICATION** — read back from production after the release with an owner session: `/v1/system/health` release = bd21f7f, last_known_good = 338ffec; `/v1/ui/state/contract` lists 46 states including `operator.running`, `operator.verifying`, `operator.failed`; `/v1/ui/state` reports contract version 4; device MAIL online with 29 capabilities (the operator family absent, as expected for 0.1.0 — the tools answer `capability_missing`); the ledger row `deployment.cloud_core.released` = bd21f7f recorded at 20:54:54Z.

**PROOF CLASS**
| Item | Class |
|---|---|
| Operator families through the companion with re-observed results | PROVEN_REAL (mechanisms: this machine + the GitHub runner) / PROVEN_PROXY (the deployed 0.1.0 agent until item 28) |
| Focus guard, secrets never typed, path roots, argument policy | PROVEN_REAL |
| OperatorTask loop, plans, focus stack, tools through the real app | PROVEN_AUTOMATED |
| Voice routing (corpus `operator`) | PROVEN_AUTOMATED |
| TTS output structure | PROVEN_AUTOMATED |
| Loopback speech semantics | PROVEN_PROXY |
| Living Core operator states, Cockpit panel | PROVEN_AUTOMATED |
| Cloud Core half on production | PROVEN_REAL (release) |

**REMAINING MACHINE-UNVERIFIABLE ITEMS** — the owner's elevated agent update with `-Operator` (item 28; UAC); the Store Notepad (`RichEditD2DPT`) path never exercised (this machine has classic Notepad); browser launches never exercised for real (no browser binary is launched here by rule); a live render of the Core's operator posture (the preview is behind the owner login); physical audibility.

**M19 ENGINEERING CLOSED** — M20 File & Document Intelligence begins.
