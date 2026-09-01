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

**Install the Windows Service.** One UAC prompt, then one sign-out. Everything in front of
it is finished: the Session-0 identity model is implemented, independently security-reviewed
(one Critical found in the fix itself and corrected), independently verified with every check
mutation-tested, and the full gate passes 12/12 including the real Notepad end-to-end.

```powershell
# In an ELEVATED PowerShell, at the repository root:
.\scripts\install-device-service.ps1
```

Sign out and back in so the companion starts in your session, then:

```powershell
.\scripts\verify-device-service.ps1
```

Send me what that prints. It is what converts four Stage-1 criteria from `PROVEN_PROXY` to
`PROVEN_REAL`, and it unblocks everything after it. Details below.

---

## Queue

### 1. Install the Windows Service (one UAC prompt) — **this is the current action**

Unblocks: Stage 1 criteria 1.1 and 1.4–1.7 moving from `PROVEN_PROXY` to `PROVEN_REAL`, all
of Stage 2, and every later stage that needs the agent running unattended.

```powershell
# From an ELEVATED PowerShell, at the repository root:
.\scripts\install-device-service.ps1
```

Then sign out and back in (the companion starts as a logon task), and:

```powershell
.\scripts\verify-device-service.ps1
```

Afterwards the agent takes over: it reads the verification output, updates the qualification
matrix with what is now proven for real, and continues.

### 2. `gh auth login` (browser OAuth, one time)

Unblocks: the private GitHub repository and CI. Everything else about CI is already written.

```bash
gh auth login
```

The agent then creates the private repository, pushes, and confirms the first CI run.

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
