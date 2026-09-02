# Owner Actions — the live queue

`OWNER_ACTIONS_MINIMAL.md` says which actions are unavoidable in principle. This file says
which one to do **now**, in what order the rest come, and what happens automatically after
each. Everything not listed here is the agent's job.

Rules this file follows:

- one action at a time, with the exact command or click, not a research task;
- an action appears only when the work in front of it is finished and verified, so nothing
  here is waiting on the agent;
- each entry says what it unblocks, so a "not now" is an informed decision rather than a
  guess;
- no secret is ever typed into a chat. Where a value is sensitive, the entry names the local
  tool that takes it (`scripts/secret-store.ps1`, a masked prompt, DPAPI-encrypted, owner-only).

Status vocabulary matches `docs/QUALIFICATION.md`: `PROVEN_REAL`, `PROVEN_PROXY`,
`NOT_YET_PROVEN`.

---

## Now

**Item 6 below: the realtime voice provider credential.** M12's real-provider path now
exists behind the abstraction (OpenAI Realtime adapter, ADR-0038), every fake-provider gate
passes, and the only thing between the system and a real speech-to-speech session is a key
that only you can create. Three local commands, none of which shows the value anywhere.
Nothing else is waiting on you: the real-microphone Turkish voice session comes after this
and will be its own single action.

---

## Queue

### 1. Windows Service install — **DONE (2026-09-01)**

Completed and qualified: service LocalSystem/Session 0, companion Session 1, pipe DACL
from the real handle, both restart proofs, final credential rotation. The runtime is
frozen; `docs/QUALIFICATION.md` holds the evidence.

### 2. `gh auth login` — **NOT NEEDED (2026-09-01)**

You were already authenticated, so no action was required. Private repository
`KAlpAkbaba/personal-agent-os` created (visibility confirmed `PRIVATE` before any code left
the machine), `main` pushed, CI running.

The first real CI run immediately earned its keep: it found four environment assumptions the
local gate satisfies by accident — including that `pytest -m "not integration"` was
deselecting **all 1418 tests** and asserting nothing. All four are fixed; see the CI section
in `docs/DECISIONS.md`.

### 2b. Install Tailscale on this PC — **DONE (2026-09-01)**

Running, 100.92.148.30 / mail.tail0e6789.ts.net. Criterion 5.2a is PROVEN_REAL: the same
check confirmed the agent still owns no inbound listening socket and no firewall rule
names it.

### 3. Hetzner account + API token — **DONE (2026-09-01)**

Provisioned: server 164238173 (pagentos-core, cpx32, nbg1, running), 100 GB volume,
firewall, SSH key. SKU chosen by the owner after the 15 June 2026 price rise — see
ADR-0033.

### 3b. Break-glass session to finish the tailnet join — **DONE (2026-09-02)**

Completed: the host joined the tailnet as a non-ephemeral node, the temporary SSH rule was
removed automatically, and Stage 5 closed PROVEN_REAL end to end (Hetzner → Tailscale →
DeviceService → Companion → real Notepad → ACK, plus all five recovery scenarios).

Unblocks: 5.2b, 5.3, 5.4 and every criterion after them. See **Now** above.

### 4. Bootstrap the owner credential on the real deployment — **DONE (2026-09-02)**

Completed during the cloud migration: the cloud Owner Credential was minted host-side
through the loopback-only guard and shown exactly once in your local elevated console.
The server keeps only its hash. If it is ever lost, rotate on the host:

```powershell
ssh root@pagentos-core "docker exec pagentos-prod-api uv run python -m app.identity.recover --rotate"
```

### 5. Enroll the owner's browser session

Unblocks: real-browser qualification against sites you are already signed into.

The agent will give you the exact enrollment step when it reaches this point; it needs the
browser closed once, and it never asks for a password.

### 6. Realtime voice provider credential (OpenAI Realtime) — **this is the current action**

Unblocks: the first real speech-to-speech session (M12), and with it the real-microphone
Turkish voice qualification. Until this key exists the realtime surface is built, gated and
tested against a simulator only; nothing in `docs/QUALIFICATION.md` Stage 6 can move.

What you need: an OpenAI API key with access to the Realtime API (`gpt-realtime`). Create it
in your OpenAI account; the agent never sees it.

Three commands from the repository root, in an ordinary (non-elevated) PowerShell:

1. Store it locally. The prompt is masked; the value is DPAPI-encrypted to your Windows
   account and readable by nothing else:

   ```powershell
   .\scripts\secret-store.ps1 -Set PAGENTOS_VOICE_OPENAI_API_KEY
   ```

2. Prove it works — **DONE (2026-09-02)**. The probe minted the minimal contract and
   then every M12 layer one at a time with `gpt-realtime-2.1`; the only refusal (dotted
   tool names) was fixed the same day. Re-run any time; exit 0 = every layer accepted,
   1 = the minimal contract refused (vendor error printed), 4 = a layer refused (named),
   3 = key not found:

   ```powershell
   .\scripts\secret-store.ps1 -Run "uv run python scripts/realtime_smoke.py --mode probe" -WorkingDirectory services\api
   ```

3. Ship it to the cloud host over Tailscale SSH. The value travels on stdin only, lands in
   `/opt/pagentos/.env` (0600, root), the API restarts, and the script confirms from this
   machine that the realtime surface now lists `openai-realtime`:

   ```powershell
   .\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_VOICE_OPENAI_API_KEY
   ```

Then tell the agent it is done (a word is enough). What happens next, automatically: the
cloud health check is re-verified, and the next action prepared is the real-microphone
session on this PC (headset, Turkish, the M12 evaluation sets), which is the only way
Stage 6 rows become PROVEN_REAL.

Speaker-verification samples (VoiceIdentity) come later and will be their own step.

### 7. A phone

Unblocks: the two mobile criteria that no server-side test can prove — a push arriving on a
locked phone, and microphone capture surviving OS backgrounding.

### 8. Enroll the real assets you authorize for security testing

Unblocks: the Authorized Asset Registry doing anything at all. The registry and its API are
built and tested; what is missing is your actual authorization records, which only you can
make.

---

## Deferred, not blocking

- Moving the Docker disk image off `C:` (that drive is nearly full). Recommended, not urgent.
- PowerShell 7 — the scripts stay 5.1-compatible on purpose.
