---
name: operator-lab-input-facts
description: Three measured Windows input facts from the M19 Digital Operator lab (2026-09-07, track A2) that any keyboard/focus-guard change must respect - lazy assignment of injected input, WindowActions.Activate dropping queued input, Notepad's title dirty-marker prefix
metadata:
  type: project
---

Measured on the owner's Windows 10 machine while fixing the security review's focus-guard finding (ADR-0082 addendum 2, `Operator/InputSynthesizer.cs` InputBatcher, `Operator/TypingGuardTests.cs`):

- **Windows assigns `SendInput` keyboard events to a thread queue when that thread RETRIEVES them, not when `SendInput` returns.** With 64-event batches 5 ms apart, a plain `SetForegroundWindow(B)` from another thread mid-stream left A with 160 characters and B with EXACTLY the last 32-character batch, nothing lost. A per-batch foreground check therefore bounds a misdirected fragment to one batch; it cannot make it zero. Any "check before send" design must report the last batch as uncertain (`uncertain_chars`) rather than claim it reached the target.
- **`WindowActions.Activate` (the `AttachThreadInput` + `SetForegroundWindow` dance) DROPS input already queued for the old foreground thread** - 83 of 224 characters vanished in one run and one stray character reached B. Never use it to model a real focus steal in a test; call `SetForegroundWindow` directly (the test process is entitled because it sent the last input event). This also means the product's own `window.activate` while another window still has unprocessed input would lose that input - an existing behaviour, not yet addressed.
- **Classic Notepad (Windows 10) PREFIXES `*` to its title on the first typed character** (`*Adsız - Not Defteri`), so the focus guard's 24-character title-prefix rule fails after the first batch of any type. The mid-stream check is identity only (`FocusGuard.SameWindow`: handle, pid, image); the title leg stays on the pre-stream `Verify()`. `Registry.Resolve` re-reads the title fresh right before each call, which is why the once-per-call guard never tripped on this.

Also: the device-protocol `errorObject` is closed (`additionalProperties: false`, three fields), so anything the planner needs from a `CapabilityException` must be in the MESSAGE; `CapabilityException.Detail` exists but is in-process only. Junctions for tests: `cmd /c mklink /J link target` (no privilege), `Directory.Delete(link)` removes the link only.

**Why:** each of these cost a red lab run that looked like a code bug and was the platform.
**How to apply:** before touching keyboard synthesis, the focus guard or window activation, re-read this; keep the lab's switch mechanism a plain `SetForegroundWindow`; keep `A + B == text[..typed_chars]` as the assertion shape.
