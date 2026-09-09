---
name: item28-unlock
description: What owner item 28 is, the one elevated command, and the constraints any script that drives the unlocked capabilities must respect (allowlists, roots, quiet levers, project command allowlist)
metadata:
  type: project
---

Owner item 28 is one elevated command, and six milestones' device halves wait on it:

```
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\qualify-staged-update.ps1   # must print STAGED UPDATE QUALIFIED
.\scripts\install-device-service.ps1 -DisplayPower -Operator                            # elevated, one UAC prompt
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\core\qualify-item28-unlocked.ps1
```

29 advertised capabilities become 85. `-Operator` adds 45 and subtracts nothing (proven by
gate 3b). Both switch names are exactly as spelled — see the installer's own param block.

**Constraints any script driving the unlocked capabilities must respect.**

- **The repo checkout is outside every authorised root**, and the roots check is
  resolve-then-contain, so a junction cannot smuggle it in. `%TEMP%\pagentos-operator-fixture`
  IS a default root and the companion runs in the owner's session, so the same path means the
  same directory — copy fixtures there.
- **`app.launch` allowlist**: notepad, calc, mspaint, explorer, powershell, chrome, msedge, or
  an absolute `.exe` under Program Files/Windows. `desktop.open_application`'s separate,
  smaller allowlist is `AppLauncher.DefaultAllowlist()`: notepad, calc, mspaint.
- **`project.scaffold` takes no template name** — the device receives generated file text.
  Templates live in Cloud Core (`services/api/app/appfactory/templates/`). The manifest's
  commands must match a FIXED list token for token: `python -m http.server <port> --bind
  127.0.0.1`, `node <entry>`, `npm --prefix <root> run start|test`, and under the `3d` root the
  Blender/Unity forms. Anything else is `permission_denied` (`command_not_allowlisted`).
- **`scene.inspect` never runs Blender.** It reads `out.json` at the project root plus the
  render that file declares; scaffolding a `3d` project whose files already contain that JSON
  proves the whole read path on a machine with no Blender. Blender's absence surfaces at
  `project.run` as `dependency_unavailable` / `runtime_missing`.
- **Quiet levers.** There is no `test`/`silent` flag on `desktop.alarm_arm` or
  `desktop.play_audio`. Arming an alarm and disarming it makes no sound (nothing rings until
  `desktop.alarm_start`). `desktop.play_audio`'s `level` is clamped to 0…1, so `0` is silent;
  `desktop.alarm_start`'s `wake_volume.end` has no lower bound. The owner-facing "test" flag is
  Cloud Core's `POST /v1/alarms {test: true}`, and the display test is
  `POST /v1/ambient/test-display {delay_seconds}` (5 s device holdoff instead of 120 s).
- **`creative.export_check` does not exist** as an implemented capability anywhere — only in
  M27 prose.

**How to apply:** `scripts\core\qualify-item28-unlocked.ps1` already encodes all of this and
refuses to run against the superseded build (`a3cb04e`) or fewer than 40 capabilities; its
`-DryRun` walks every section without sending anything. Extend that script rather than writing
a new harness. See [[device-identity-chain]] and [[windows-agent-gates]].
