# WORKLOG — B11-toast: real Windows toasts and toast action buttons (rows 369, 370)

Date: 2026-09-17 · Base: `main` @ `2e09f66` · Worktree branch: `worktree-agent-a1f8c8e2a98ae2404`

This file carries what the integrating session must copy into files this batch may not edit
(`docs/product/*.md`, `docs/DECISIONS.md`): the proposed matrix rows, the ADR draft and the
evidence notes.

## 1. Proposed feature-matrix rows (14 columns, 15 pipes, no pipe inside a cell)

```
| 369 | Desktop toast | desktop.notify gerçek Windows toast'u (Windows.UI.Notifications) gösteriyor: kendi AppUserModelID'si (PagentOS.Companion, sahibin Başlat menüsündeki kısayol, HKLM yok), elle kurulan ve kaçışlanan XML, urgent = reminder senaryosu + yüksek öncelik, grup anahtarı = etiket (yeni bildirim eskisinin yerini alıyor); 'shown' yalnızca Windows toast'u kendi geçmişinde tutunca; sahip bildirimleri kapattıysa hiçbir şey gösterilmiyor (balonla dolanılmıyor); balon yalnızca platform kullanılamazsa, surface ile söyleniyor | desktop.notify | DONE | PA | P1 | 5 | B11 | Notify/WindowsToastSink.cs, Notify/ToastXml.cs, Notify/ToastPlatform.cs, Notify/AppIdentityShortcut.cs, Projects/ShellLinkInterop.cs, Program.cs:BuildToastSink, packages/protocol/desktop-notify.json | ToastXmlTests, WindowsToastSinkTests, AppIdentityShortcutTests, ToastLabTests (gerçek masaüstü), DesktopNotifyContractTests, CameraWiringTests, test_notify_toast_actions.py | lab 2026-09-17: Windows toast'u kabul etti, geçmişten geri okundu (Türkçe metin ve düğme argümanları dahil), yeni kimlikte ilk toast'tan önce ERROR_NOT_FOUND ölçüldü; cihaz yeniden kurulumu + kilitli ekranda görünürlük kanıtı bekliyor | yes | Bu masaüstü (Focus Assist benzeri kural) açılır pencere göstermedi - PowerShell'in kayıtlı kimliği de göstermedi; Windows bunu hiçbir açık API ile söylemiyor, cevap user_state ile ipucu veriyor. Mutasyonlar M1 M3 M4 M5 M7 kırmızı |
| 370 | Toast action buttons | Toast'ta en çok 3 düğme (activationType=foreground, argüman yalnızca action=<id>;notification=<uuid>); basış yalnızca bu sürecin gösterdiği toast ve o toast'un sunduğu eylem için kabul ediliyor, kalp atışı status.notify_actions ile 3 raporda taşınıyor, Cloud Core bir kez, yalnızca toast'un gönderildiği cihazdan (data_json.notify_targets) ve yalnızca satırın sunduğu eylem için data_json.actions_pressed'e yazıyor; hiçbir şey çalıştırılmıyor/açılmıyor | Eylem düğmeleri | DONE | PA | P1 | 369 | B11 | Notify/NotifyActionQueue.cs, Notify/WindowsToastSink.cs:OnActivated, Protocol/HeartbeatStatus.cs:NotifyActions, ActivityStatusReporter.cs, packages/schemas/device-protocol.schema.json, app/notifications/toast.py:parse_action_presses, app/notifications/service.py:record_action, app/ambient/ingest.py (g) | WindowsToastSinkTests, NotifyActionReportingTests, ToastXmlTests, test_notify_toast_actions.py (kalp atışı yolu, yeniden başlatma dahil) | gerçek düğme basışı bu masaüstünde kanıtlanamadı (açılır pencere yok); PAGENTOS_TOAST_PRESS_LAB=1 ile UIA basış labı hazır | yes | Uygulama çalışmıyorken basış gelmez (COM aktivatörü yok) - düğmeli toast'lar çıkışta kaldırılıyor. Güvenlik incelemesi (Medium) giderildi: başka cihazdan gelen basış reddediliyor. Mutasyonlar M2 M6 P1 P2 P3 P4 P5 kırmızı |
```

Pipe count check: each row above has exactly 15 `|` characters (verified with a script before
commit; see §5).

## 2. ADR draft (for `docs/DECISIONS.md`, next free number)

**ADR-XXXX — B11-toast: real Windows toasts in the Session Companion; presses ride the heartbeat**

Status: proposed (2026-09-17, windows-engineer).

Context. `desktop.notify` showed a tray balloon: no buttons (row 370 PARTIAL), no failure
signal (`ShowBalloonTip` returns nothing), and the device's `detail: actions_not_rendered` never
left the device because the answer wrote `detail` only for `shown: false`. Since B48 the
companion targets `net10.0-windows10.0.19041.0`, so `Windows.UI.Notifications` is in process.
The contract named a `desktop.notify.action` device event but no transport existed.

Decisions.

1. **Surface.** WinRT `ToastNotification` under a fixed AppUserModelID `PagentOS.Companion`,
   made valid for an unpackaged process by a per-user Start Menu shortcut
   (`PagentOS Companion.lnk`, target = the companion image) carrying `System.AppUserModel.ID`,
   written with `IShellLinkW` + the link's `IPropertyStore` at companion start and rewritten
   only when target or id differ. No HKLM, no elevation, no new NuGet package (XML built by hand
   through `XmlWriter`).
2. **"Shown" = Windows holds it.** `Show` without error AND the toast found in
   `ToastNotificationManager.History` (tag + group). Never "seen". Otherwise the balloon, with
   `detail: toast_not_in_history`.
3. **The owner's "off" wins.** `DisabledForUser`, `DisabledForApplication`,
   `DisabledByGroupPolicy` answer `shown: false, notifications_disabled` with no balloon; routing
   around the owner's Windows setting with a second surface from the same process would defeat
   it. (The task text suggested a balloon fallback for "notifications disabled by the user"; this
   ADR deliberately narrows that to platform faults.)
4. **ERROR_NOT_FOUND is not a fault.** Measured on 19045: `ToastNotifier.Setting` throws
   `0x80070490` for an id until its first toast is shown, then answers. Treating it as
   "platform unavailable" would make every fresh install balloon-only for ever. The toast is
   tried and decision 2 decides.
5. **Fallback.** The balloon only for: no identity shortcut, any other failure to ask, a
   `Show` that throws, `DisabledByManifest`, or decision 2. The answer says
   `surface: "balloon"`, `actions_rendered: 0`, and `detail` names why.
6. **Priority.** `urgent` → `scenario="reminder"` + a system-handled dismiss button + high
   notifier priority; never `scenario="alarm"` (looping alarm audio is the alarm subsystem's).
   `low` → silent. Tag = hash of `group_key` (replacement mirrors the Cloud Core's grouping) or
   the notification id; group `pagentos`.
7. **Buttons are data.** `activationType="foreground"`, `arguments="action=<id>;notification=<uuid>"`;
   parsed back by an exact-form parser; accepted only for a toast this process showed and an
   action it offered. No protocol activation, no URL, nothing launched (structural test).
8. **Return path.** Heartbeat `status.notify_actions` (schema: ≤16 entries, three string keys,
   closed), filled by `NotifyActionQueue`; each press is carried in 3 consecutive reports
   (heartbeats are fire-and-forget) and the Cloud Core records it once per
   `(notification_id, action_id, pressed_at)` in `data_json.actions_pressed`, only from a
   device the toast was sent to (`data_json.notify_targets`, written by the ladder from
   `DeviceRunResult.device_id` when the device answered `shown: true`; at most 8), only for an
   action its row offered, without touching `read_at`, running nothing. A press with no device,
   or for a row with no recorded target, is refused (fail closed). `actions_pressed` is an
   owner-intent signal bound to the target device; anything that ever acts on it automatically
   must keep that check. No new frame type: an older
   broker ignores the key (its status parser is lenient). Chosen over a new `device_event` frame
   because it reuses the path that already carries the device's own events
   (`local_alarm_fired`, `local_alarm_snoozed`) and needs no broker frame, ack or schema `oneOf`
   change.
9. **Presses need the running companion.** No COM activator (that would need a registered
   CLSID and a launch path). Button-bearing toasts are removed from the Action Center when the
   companion exits; plain toasts stay.
10. **Focus Assist is not claimed.** No public API reports it. The answer carries
    `SHQueryUserNotificationState` as `user_state` and says nothing more.

Consequences. Capability manifest unchanged (13/14/44/104; no new name). Heartbeat status key
set grows to 13; the Device Service projects the new list. Contract `desktop-notify.json`
gains `surface`, `actions_rendered`, `notifier_setting`, `user_state`, `detail` and
`action_event.transport`; bundle re-synced. Rollback: the previous companion build (balloon)
still answers the same request shape; an older Cloud Core ignores `notify_actions`.

Bugs found and fixed in this batch (each with a regression test): (a) `NotifyCapabilities`
dropped `detail` from a shown answer; (b) `Notify` had no try/catch although its doc and a test
name claimed a throwing sink could not kill the companion - the test's "throwing" sink returned
instead of throwing; (c) the device's action-id regex `^[a-z0-9_]+$` accepted `"open\n"` (.NET
`$` matches before a final newline) - now `\z`; (d) the Cloud Core's `build()` had the same
defect with `re.match` and did not enforce the contract's 32-character id limit.

## 3. Evidence notes (for `docs/evidence/b11-toast-2026-09-17.json`)

- Machine: owner desktop, Windows 10 19045, tr-TR, single 1920x1080 display at test time,
  foreground Docker Desktop (not full screen), `SHQueryUserNotificationState` =
  accepts_notifications.
- `ToastLabTests.Windows_accepts_a_real_toast_with_buttons_and_keeps_it_in_its_history`: PASS.
  Answer `{"shown":true,"surface":"toast","notifier_setting":"enabled","actions_rendered":2,"user_state":"accepts_notifications"}`;
  the toast read back from `History.GetHistory("PagentOS.Companion.Lab")` with tag = notification
  id, group `pagentos`, the escaped Turkish body and `action=lab_snooze;notification=<id>`; a second
  send with the same id is still one toast; `Remove` takes it out of the history.
- `ToastLabTests.A_fresh_id_has_no_settings_until_its_first_toast_and_that_toast_is_still_shown`:
  PASS. `ReadSetting` threw `COMException 0x80070490` before the first toast, the sink showed the
  toast anyway (`surface: toast`), and the setting read `enabled` afterwards.
- Spike (not committed): with and without a Start Menu shortcut, `Show` returned and the toast
  was in the history; no toast popup window appeared on this desktop for our ids NOR for
  Windows PowerShell's registered id `{1AC14E77-...}\WindowsPowerShell\v1.0\powershell.exe`
  (Win32 `EnumWindows`: only the Settings and text-input CoreWindows were present). So popup
  suppression on this desktop is a Windows-side rule (most likely Focus Assist), not our
  identity, and the history read-back proves "Windows holds it", not "a popup appeared".
- Lab clean-up verified: no `PagentOS*` link left in the Programs folder; histories cleared for
  every lab id; no per-app key appeared under
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings` for the lab ids.
- Not proven here: a visible popup, a press through the real popup (UIA lab exists behind
  `PAGENTOS_TOAST_PRESS_LAB=1`), a toast over a locked screen.

## 4. READY_FOR_OWNER — live proof after the device agent is reinstalled

1. Reinstall the device agent with the new companion (staged installer as usual). At companion
   start `companion.log` must say `AppUserModelID shortcut Created|Updated|Unchanged` and
   `desktop toasts: Windows toast surface under AppUserModelID PagentOS.Companion`.
2. Turn Focus Assist OFF (Ayarlar > Sistem > Odak yardımı > Kapalı) for the proof, and make
   sure `Ayarlar > Sistem > Bildirimler` lists "PagentOS Companion" as ON after the first toast.
3. From the Cockpit (or `POST /v1/devices/{id}/commands`) send `desktop.notify` with two actions;
   expect `surface: "toast"`, `actions_rendered: 2`; a popup with both buttons appears.
4. Press one: within ~10 s `GET /v1/notifications` shows `data.actions_pressed[0].action_id`.
5. Lock the screen (Win+L), send an `urgent` notification: it should appear on the lock screen
   only if "Kilit ekranında bildirimleri göster" is on - that is the row-369 PROVEN_REAL step.
6. Optional machine proof on a popup-showing desktop:
   `$env:PAGENTOS_TOAST_PRESS_LAB='1'; dotnet test tests\PagentOS.Agent.Tests --filter FullyQualifiedName~ToastLabTests`.

## 5. Security review and fix (2026-09-17)

Independent security review of the branch: no Critical or High findings. One Medium:

- **MEDIUM - press not bound to the device that showed the toast.** `record_action` checked the
  row's offered actions but not `device_id`, so a compromised secondary device could attribute
  a press (guessable ids such as `open`, `snooze`) to a notice only another device showed.
- **Fix (commit after `ef5f95f`).**
  - `DeviceRunResult` gained an optional `device_id`, which `BrokerDeviceAction` fills with
    the device it selected (succeeded, failed or expired).
  - When the device answers `shown: true`, `ToastRung` calls
    `notifications.note_toast_target(row, device_id)`, which adds the id to
    `data_json.notify_targets` (deduplicated, at most 8, only real UUIDs). The row is
    committed by `mark_delivered`.
  - `record_action` refuses (`not_a_target_device`, logged as
    `notification_action_wrong_device`) a press whose device is not in that list, a press
    with no device, and a press for a row with no recorded target.
  - The docstring states that `actions_pressed` is an owner-intent signal bound to the target
    device and must never drive automation without that check. `desktop-notify.json` (bundle
    re-synced) and `DEVICE_PROTOCOL.md` §6q say the same.
  - No new column, so no migration: the target list lives in `data_json` beside `actions`
    and `actions_pressed`.
- **Tests added** (`test_notify_toast_actions.py`): a press from another device is refused; a
  press from the target device is accepted; every device the toast was sent to may press and
  no other; no device / no recorded target is refused; targets are deduplicated and bounded;
  the heartbeat path refuses another device's press; the ladder records the device that
  showed the toast; a not-shown toast records no target; `BrokerDeviceAction` reports the
  device it sent the command to.
- **Mutation proofs**, restored from sha256-verified backups:
  - P4 (device check turned into `device_id is None and ...`): 4 tests RED.
  - P5 (ladder records `None` instead of the device): 1 test RED.
- **Optional item (InHistory poll).** Kept on the `desktop.notify` path on purpose, because
  its result is what the command reports as `shown`. It is now bounded by named constants
  (`HistoryAttempts` = 10, `HistoryPause` = 50 ms, no pause after the last read) and
  documented. A miss measured 564 ms in the lab, which now asserts a 10 s hang guard.
  Notify requests are rare, and the Cloud Core ladder's toast timeout is 15 s.
- **Gates after the fix:**
  - Python suites touching notifications, the ladder or `DeviceRunResult` (38 files):
    851 passed.
  - `test_notify_toast_actions.py` + `test_notifications.py` + `test_protocol_bundle.py`:
    79 passed.
  - `ruff check .`: clean.
  - `dotnet build`: 0 warnings. `dotnet format --verify-no-changes`: clean.
  - Touched device tests (Notify, CameraWiring, Serialization, LivingCore, DeviceVoice):
    198 passed, 1 skipped (the opt-in press lab).
  - Bundle check: clean.

## 6. Gates run

| Gate | Before | After |
|---|---|---|
| `dotnet build` (Debug and Release) | 0 warnings, 0 errors | 0 warnings, 0 errors |
| `dotnet format --verify-no-changes` | - | clean |
| PagentOS.Companion.Audio.Tests | 185 passed | 185 passed |
| PagentOS.Agent.Tests | 1110 passed, 1 skipped | 1187 passed, 2 skipped (new skip: the opt-in UIA press lab) |
| services/api `tests/unit` (full) | - | 11676 passed, 5 skipped, 0 failed |
| `ruff check .` (services/api) | - | clean |
| `scripts/qualify-staged-update.ps1` | - | 89 checks passed |
| `scripts/sync-protocol-bundle.py --check` | - | clean (desktop-notify.json re-synced) |
| Manifest from the built `capabilities` verb | 13/14/44/104 | 13/14/44/104 |

Mutation proofs (each applied to one unique string, rebuilt, focused tests RED, restored from
a sha256-verified backup, never `git checkout --`):

- M1 history read-back ignored -> `A_toast_windows_did_not_keep_is_not_claimed_and_the_balloon_carries_it` RED
- M2 un-offered press queued -> `A_press_the_toast_did_not_offer_is_not_queued` RED
- M3 detail dropped from a shown answer (old bug) -> `AShownAnswerStillCarriesItsDetailAndSurface` RED
- M4 throwing sink escapes Notify (old bug) -> `ASinkThatThrowsIsNotAllowedToKillTheCompanion` RED
- M5 disabled_for_application routed to the balloon -> `A_disabled_notifier_shows_nothing_and_is_not_routed_around(disabled_for_application)` RED
- M6 extra key passes the Session-0 projection -> `Anything_else_drops_the_whole_list_and_never_sends_null` RED
- M7 ERROR_NOT_FOUND treated as a fault -> `The_first_toast_of_a_fresh_install_is_tried_although_windows_has_no_settings_yet` RED
- P1 `re.match` for a heartbeat press id -> `test_a_malformed_press_is_dropped_not_repaired[entry2]` RED
- P2 un-offered press recorded -> 2 tests RED
- P3 repeated press recorded three times -> 2 tests RED

The two `$`-regex regressions (device and Cloud Core) were RED against the unfixed code first:
`ToastXmlTests.Arguments_round_trip_and_nothing_else_is_accepted` and
`DesktopNotifyContractTests.AnActionIdWithATrailingNewlineIsRefused` failed before `\z`.
