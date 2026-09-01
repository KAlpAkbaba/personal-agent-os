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

**Install Tailscale on this PC and sign in.** One UAC prompt, then one login:

```powershell
winget install --id Tailscale.Tailscale
```

Then launch Tailscale and sign in (create the account if you do not have one). That is the
whole action — I verify it immediately and read-only with `scripts/verify-tailnet.ps1`, and
I do not need the account password or any key for this step.

Why this one now: it is the Windows half of the private network, it needs UAC and a human
login (so it cannot be automated), and it is on the critical path to the milestone's
headline proof — Hetzner Cloud Core → Tailscale → DeviceService → Companion → real Notepad
→ ACK. Your Windows machine keeps **zero** inbound public ports throughout; the verifier
checks that rather than assuming it, and it already passes today.

`gh auth login` turned out **not** to be required — you were already authenticated
(`KAlpAkbaba`, `repo` + `workflow` scopes), so the private repository is created, `main` is
pushed, and CI is running.

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

### 2b. Install Tailscale on this PC (one UAC prompt + one login) — **this is the current action**

Unblocks: criteria 5.2, 5.4 and the whole remote command path. See **Now** above.

### 3. Hetzner account + API token, Tailscale account + auth key

Unblocks: the real cloud deployment and the PC ↔ cloud private-network proof.

Create the accounts and the token/key in each provider's own console — creating an account
and entering billing details is legally your act, not the agent's. Then, in the shell you
will run OpenTofu from:

```bash
export TF_VAR_hcloud_token='...'
export TF_VAR_tailscale_auth_key='...'
export TF_VAR_owner_ssh_public_key="$(cat ~/.ssh/id_ed25519.pub)"
```

Prefer an ephemeral, pre-authorized, tagged Tailscale key (`tag:agent-os-cloud`). Details and
the verification commands are in `infra/opentofu/README.md`.

Also approve the Tailscale install on this Windows PC (one UAC prompt) so the PC and the
cloud host share a tailnet.

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
