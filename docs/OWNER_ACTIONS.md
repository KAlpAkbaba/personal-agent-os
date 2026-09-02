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

**Resume at the cloud owner bootstrap.** The broker switch succeeded and is preserved (the
agent dials `http://100.90.158.26:8001`); the device row in the cloud database is intact. The
bootstrap returned 403 because it was called on the host against the *published* port, so
the API saw it arriving from the Docker bridge, not from 127.0.0.1, and its loopback-only
guard refused - correctly. No credential was minted; the cloud owner does not exist yet.

The fix makes the request from inside the API's own network namespace (`docker exec`), which
only root on the host can do - the guard is unchanged. In an **elevated** PowerShell at the
repository root:

```powershell
.\scripts\cloud\migrate-agent-to-cloud.ps1 -BrokerHost 100.90.158.26 -StartPhase Bootstrap
```

If a line saying `Tailscale SSH requires an additional check` with a login URL appears, open
that URL in your browser and the session continues - it is Tailscale's periodic
re-authentication for root SSH, not an error. The credential is then shown **once, in that
console**; put it in your password manager, paste it at the prompt, and the DPAPI automation
session is minted. If the mint fails now, the script stops before the prompt and says why.

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

### 3b. Break-glass session to finish the tailnet join — **this is the current action**

Unblocks: 5.2b, 5.3, 5.4 and every criterion after them. See **Now** above.

### 4. Bootstrap the owner credential on the real deployment

Unblocks: authenticated use of the deployed system. One command, on the machine hosting the
API, and the credential is shown exactly once:

```powershell
.\scripts\bootstrap-owner-credential.ps1
```

Put it in your password manager immediately. The server keeps only its SHA-256 hash, the
script writes nothing to disk, and it refuses to run under PowerShell transcription because a
transcript would capture the credential in plaintext. If it is ever lost, rotate on the host
with `.\scripts\bootstrap-owner-credential.ps1 -Rotate`.

### 5. Enroll the owner's browser session

Unblocks: real-browser qualification against sites you are already signed into.

The agent will give you the exact enrollment step when it reaches this point; it needs the
browser closed once, and it never asks for a password.

### 6. Voice provider keys, and recording owner speech samples

Unblocks: real Turkish STT/TTS qualification and speaker enrollment.

Create accounts for the providers that survive the benchmark, then store each key locally —
typed into a masked prompt, encrypted to your Windows account, never in the repository and
never in a chat:

```powershell
.\scripts\secret-store.ps1 -Set PAGENTOS_VOICE_<PROVIDER>_API_KEY
```

Then record several short samples: quiet room, normal office, mildly noisy. The agent will
say exactly how many and how long when it reaches that step.

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
