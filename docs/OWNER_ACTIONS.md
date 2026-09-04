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

Two independent actions are ready; do them in whichever order suits the evening:

- **Item 10 — M13 Real Browser + Research**: release the Cloud Core, update the Windows agent
  once (UAC), then run the first real research from the web page. Everything before it is
  built, reviewed and proven on the local real chain (real Chrome, live Internet).
- **Item 9 — K66 voice re-qualification** (unchanged from this morning).

**Item 8 below: the combined voice-character + noise qualification session.** Your first
real session (item 7) said: generally good, keep the architecture, target voice Arbor, and
one real defect — the K66 microphone lets room noise drive turns. The response is on
`main` (ADR-0043/0044): the Arbor profile through the closest supported voice, a layered
microphone pipeline with calibration, a local speech gate and per-device profiles, owner
modes with `Otomatik` default. It is qualified as one session, voice and noise together,
because aggressive input processing can change conversational timing. No DSP tuning is
asked of you; the system converges from its measurements plus your verdicts.

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

### 10. M13 Real Browser + Research — release, agent update, first real research — **ready**

Unblocks: `docs/QUALIFICATION.md` Stage 9 rows 9.1–9.10 (`PROVEN_REAL`); today they are
`PROVEN_PROXY` from the local real chain (`scripts/e2e-m13-research.ps1`: dev Cloud Core →
dev DeviceService/Companion → Browser Worker → real Chrome → live Internet → 12 sources →
report → PDF/DOCX artifact → memory → Cloud Core restart mid-job resumed without duplicate
evidence).

What changed since the last release (`a139e28`): the research pipeline and device layer
(ADR-0050), migrations `0012`/`0013`, `PAGENTOS_WORKER_MODE=embedded` on the `api` service,
the `/v1/research` and `/v1/devices/select|PATCH` routes, plus every M12 client fix. The
release transaction is the proven one (ADR-0042): build → migrate → recreate `api` only →
health → contract v2 → key PRESENT → provider listed → real Realtime mint → rollback on any
failure. PostgreSQL, Redis, MinIO, Temporal, the device row and the owner identity are
untouched.

Step A — release the Cloud Core (from the repository root, ordinary shell; the script talks
to `pagentos-core` over the tailnet, prints no secret):

```powershell
.\scripts\cloud\release-cloud-core.ps1
```

Step B — update the Windows agent once (this provisions the Browser Worker: a Python
environment under `C:\Program Files\PagentOS\agent\browser`, the real Chrome channel, a
dedicated PagentOS profile under `%ProgramData%\PagentOS\companion\browser`; it preserves
the tailnet broker endpoints, the device identity and the enrollment; one UAC prompt).

Your first attempt (2026-09-03 16:41 and 17:09) built and staged the M13 binaries and then
failed silently at the swap: the installer still used the pre-engine directory rename, which
NTFS refuses while the service and companion run from those directories — the same incident
the journaled deployment engine was written for on 2026-09-01, which the installer had never
been wired to. It now deploys through that engine (stop by PID → same-volume renames →
ACL → start → health → commit, rollback on any failure), writes a transcript under
`C:\ProgramData\PagentOS\install-logs\`, prints the evidence block (repo HEAD, source /
staged / installed artifact hashes, the executables the SCM and the logon task point at and
are running, the worker path, the installed capability manifest) and **fails loudly** if the
live binary does not answer the `capabilities` verb with the browser family.

```powershell
Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\scripts\install-device-service.ps1"'
```

(`-NoExit` keeps the elevated window open so the last lines are visible: either
`INSTALL VERIFIED …` in green or `INSTALL FAILED: …` in red with the log path.) Then, in an
ordinary shell, confirm the device advertises the browser family and the worker self-check
passes as you:

```powershell
.\scripts\verify-device-service.ps1
```

Step C — the first real research. Start the web client against the Cloud Core and open the
research page:

```powershell
.\scripts\voice\start-web-voice.ps1
```

Open `http://localhost:3000/research`, sign in with the Owner Credential (masked, as on the
voice page), keep the prefilled topic "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli
gelişmeleri araştır.", device "Otomatik" (or type `ev bilgisayarımda araştır` to prove the
explicit target), press **Başlat**. Expect a visible Chrome window on this PC working
through searches and pages for two to four minutes; the page shows the stage and counters
live. When it reads "ready", the report appears (Yönetici Özeti → Bulgular → Neden Önemli →
Sonraki Sinyaller → Ayrıntılar → Kaynaklar), the artifact lands in the inbox with PDF/DOCX,
and the memory entry exists.

Your verdict, in a sentence or two: is the executive summary concise, do the 3–7 findings
matter, are the sources traceable? Paste the task id (the page shows it) or the
"Rapor JSON'unu kopyala" output. Note: without an LLM key on the host the synthesis is the
deterministic provider (findings are provenance summaries, not prose); the owner's existing
OpenAI key on the host makes `auto` pick OpenAI automatically once
`PAGENTOS_OPENAI_API_KEY` is set there — say so if you want that switched on before the run,
it is one `set-cloud-secret.ps1` call with the same key value.

### 11. M13 real browser action through the whole owner path — **ready**

Unblocks: `docs/QUALIFICATION.md` 9.2 (`PROVEN_REAL`), the first real evidence that a
command travels Hetzner → Tailscale → DeviceService → your-session companion → installed
Browser Worker → real Chrome and back, with correlated ids. Step B of item 10 is done
(6b.1/6b.2 `PROVEN_REAL`, 2026-09-03).

Harmless and deterministic: opens `https://example.com/`, reads its title and text, runs one
web search, asks for a download the research policy must refuse, closes the session. Every
step is one device command with its own trace id, awaited on the Cloud Core; the script
records the companion image, the worker process (python from the installed tree, child of
the companion, running as you), the Chrome process on the PagentOS profile, and the
companion's `browser_request` audit rows for these commands. The credential is typed
masked and the session is revoked at the end. From the repository root, ordinary shell:

```powershell
.\scripts\browser\real-browser-smoke.ps1 -OutFile browser-smoke-1.json
```

Expect a Chrome window to appear briefly on this PC. Paste the final lines (each command's
status, `command_id`, `trace`) and `browser-smoke-1.json` (ids, timings, the example.com
title/text and process images — no secret).

### 13. Browser lifecycle: update, PROVE the live worker, then one visible Chrome window — **DONE (2026-09-04)**

Result: live worker 0.3.0, one Chrome process/window, twelve operations on one session, clean exit, owner's Chrome untouched; QUALIFICATION 6b.3, 6b.4 and 9.13 are `PROVEN_REAL`. Do not rerun.

#### (record) what item 13 asked for

Unblocks: `docs/QUALIFICATION.md` 6b.3, 6b.4, 9.13; item 12 resumes after it.

**Your 2026-09-04 result was a deployment truthfulness defect, now root-caused from real
evidence (ADR-0050 item 16).** The installer had really replaced the source tree under
`C:\Program Files\PagentOS\agent\browser`, but the worker never runs that tree: it runs the
copy that uv installs into the tree's venv, and uv had rebuilt that copy from a stale cached
wheel (its cache key is the modification time of `pyproject.toml`, which code-only releases
never change, and the staging path is the same every time). The installer's self-check did
not notice because it ran with the browser tree as working directory, which made Python
import the fresh source instead of the venv copy; the companion starts the worker from its
data directory and got the stale copy. So "INSTALL VERIFIED" was true about files and false
about running code.

What changed: the worker now reports which file it executes and a digest of its whole
package; the source declares its release (0.3.0); the installer always rebuilds the package,
runs every self-check exactly the way the companion runs the worker, compares the venv copy
with the source file by file, and after the swap waits for the companion to start the worker
and checks the live process (pid newer than the deployment, executable inside the installed
venv, reported release equal to the staged source, every pre-swap worker pid gone). Any
disagreement rolls the deployment back and prints `INSTALL FAILED`. The smoke script proves
the live worker against this checkout's release before it asks for any Chrome operation, and
if that fails after an update it says `deployment/version mismatch` and tells you what to
paste; it never asks you to rerun the same command.

Locally verified before asking you: the stale build was reproduced from the same staging
path and refused by the new checks; the fixed build passed; the dev-chain harness now runs a
packaged worker the same way and passed end to end; CI is green.

One command. It updates the agent (one UAC prompt), proves the live worker (release, module
origin, package digest) before any browser operation, then opens one Chrome window, runs
twelve operations in that same session and exits leaving zero PagentOS Chrome:

```powershell
.\scripts\browser\real-browser-smoke.ps1 -UpdateAgentFirst -Mode lifecycle -OutFile browser-lifecycle-1.json
```

Expected last line: `REAL BROWSER SMOKE: PASS`, preceded by `live worker proven: release 0.3.0`.
If it stops with `deployment/version mismatch` or `INSTALL FAILED`, paste the message, the
install log it names and the output of `.\scripts\verify-device-service.ps1`; do not rerun.

### 15. The first real Research run — DuckDuckGo, no deployment — **DONE (2026-09-04)**

Your run at 17:28-17:33 UTC ended `OWNER RESEARCH: PASS`: five findings from five distinct publishers, 28 refused pages recorded by reason, Chrome clean before and after, no agent deployment, one Cloud Core release. QUALIFICATION 9.16-9.19 are PROVEN_REAL and the Research Engine milestone is closed. Kept as a non-blocking backlog item: some accepted findings were broad AI news rather than agent-specific developments (ADR-0050 item 24).

Unblocks: `docs/QUALIFICATION.md` 9.16 and 9.17; then the Activity Ledger + Voice Narration
step of the roadmap.

This is the product, not an infrastructure test. One command runs the real research target on
your machine:

> Son uc gundeki yapay zeka ajanlariyla ilgili en onemli gelismeleri arastir. En onemli 5
> gelismeyi sec, neden onemli olduklarini acikla ve kaynaklarini ver.

What it does, in order:

**Your third attempt (2026-09-04) did not crash at all - which is why it was worse.** It
reached `ready`, fetched and ranked twelve pages, quarantined nothing, and published a report
with a fluent Turkish executive summary and ZERO findings. Your acceptance check caught it; the
pipeline did not. Three things were wrong at once, and all three are fixed.

Nothing had ever asked whether a fetched page was about your question. A ten-day-old IBM
Granite model card and two unrelated arXiv abstracts (one on Catalan's constant, one on
dark-matter halo profiles) were ranked as evidence for "the last three days in AI agents", and
every page's retrieval time was quietly standing in for its publication date. A Cloudflare
interstitial titled "Bir dakika lutfen..." was ranked as if it were an article. And zero
findings still counted as a finished report.

Now every fetched page is judged before it can be cited: is it real content or a block page,
is it about the topic, is it inside the window, and is its publication date actually known. A
page whose date cannot be established is marked uncertain rather than backdated to the moment
we fetched it. Each refusal is recorded with its reason - off topic, outside the window, date
uncertain, interstitial, duplicate coverage, not enough content - and the counts appear in the
report itself, so you can tell a thin answer from a thin web. The report has a floor: at least
three findings, each citing the evidence it rests on, target five. If the model returns fewer,
the pipeline rejects that output, retries once against the same evidence, then builds findings
deterministically from the validated evidence, and if there still are not three defensible
ones it fails as `insufficient_valid_findings` instead of handing you an empty answer. It never
invents a finding to reach five. Research policy version 4 carries this, so this rerun performs
exactly one Cloud Core release and no Windows agent install.

**Your second attempt (2026-09-04) got past the numeric defect and failed on the next one of
the same family**: a synthesis model returned a statement without its `label`, and the pipeline
read that key directly, so `KeyError: 'label'` ended a run that had discovered 238 candidates
and fetched and ranked 12 with nothing quarantined. `label` is the provenance taxonomy every
statement must carry, so it is genuinely required - but it comes from a model, so it is now
validated at the boundary. Every research entity has a named, versioned schema; each field is
required, optional (with a defined default) or derived; a violation names the entity type, the
id, the field, the stage, the producer and the schema version; and a malformed statement,
section or finding is quarantined while the rest of the report survives. To stop this being a
one-missing-key-per-run sequence, a test now scans the whole research package and fails if any
producer-controlled payload is read by raw key: that audit found and fixed the model providers'
HTTP envelopes and this Cloud Core's own stored-report reads before you ever ran into them.

**Your first attempt (2026-09-04, task `f6eb5021`) got all the way to ranking**: the Cloud Core
released, the policy became version 1, DuckDuckGo was the provider, 243 candidates were
discovered and 12 were fetched and ranked. It then failed on a typed-data defect: the synthesis
model answered a finding's numeric `importance` field with a Turkish prose sentence and `int()`
on that sentence ended the job. That is fixed at the contract level, not with a try/except:
every numeric field in the pipeline now declares its type, range and provenance and is
validated before use, text fields refuse numbers, and a candidate or finding that breaks its
contract is quarantined with a named reason while the run continues on the valid remainder. The
run fails only if fewer than three valid items remain. Research policy version 2 carries that
contract, so this rerun performs exactly one Cloud Core release and no Windows agent install.

1. **No deployment when nothing changed.** It compares this checkout's browser-worker release
   with the installed one (source AND the copy inside the installed venv that actually runs).
   They match today (0.4.0, installed by your 15:18 run), so it installs nothing: no staging,
   no swap, no service restart. If they ever differ incompatibly it stops and prints the one
   update command instead of doing it silently.
2. **Cloud Core policy.** It asks the Cloud Core for its research policy version. The deployed
   one is version 3 and this checkout expects 4 (the quality gate and the findings contract),
   so it releases the Cloud Core once (the proven transactional release: build, migrate, recreate the api
   container only, health, rollback on any failure). That is the only deployment in this
   command, and only because the source really changed.
3. **The research.** DuckDuckGo discovery (`requested_provider=duckduckgo`,
   `provider=duckduckgo`, `fallback=false`), one persistent Chrome window for the whole job,
   real source pages opened and extracted rather than search snippets, dedup across repeated
   coverage, publication dates respected, then the report: executive summary first, the
   findings with why each matters, and the sources with title, URL, publisher and the device
   command that fetched each one. It also prints what was refused and why, so a short report
   tells you whether the web was thin or the gate was strict.

Expect a visible Chrome window working for two to four minutes. Leave it alone; it closes
itself and the command checks that zero PagentOS Chrome processes remain.

```powershell
.\scripts\research\owner-research.ps1 -OutFile research-1.json
```

It asks for the Owner Credential in a masked prompt, as usual. Expected last line:
`OWNER RESEARCH: PASS`. Paste the printed report (or `research-1.json`) and your verdict in a
sentence: is the executive summary worth reading, do the items matter, are the sources
traceable, and does anything in the refused list look like it should have been kept?

Google is not attempted: it stays available behind `-SearchProvider google` whenever you want
it, with the verification handoff intact.

### 14. Google search with owner verification handoff — **OPTIONAL / not a Research blocker (2026-09-04)**

Unblocks: `docs/QUALIFICATION.md` 9.15 and, when Google answers, 9.12.

Your 2026-09-04 run proved the hard part: interactive mode entered, Google's interstitial was
detected, the state became `WAITING_FOR_OWNER_VERIFICATION`, the existing PagentOS Chrome
window came to the front, and the same session stayed alive while it was polled, with no new
browser lifecycle. It then failed on the way to the fallback with a dictionary error, because
two layers of the smoke each set the `interstitial` key and PowerShell refuses to merge them.
That is fixed at the root: the interstitial is now evidence in exactly one place (the result's
`verification` block), every payload and evidence object is built from a fixed key list, and a
test forbids the merge pattern and any duplicate key.

**The wait is now 45 seconds, not 600.** You do not have to solve anything. Either complete
Google's page in that window within 45 seconds and the same session resumes and retries the
search once, or let it expire and answer `y` when it asks about falling back. Both outcomes
are a PASS and both are recorded honestly:

- you complete it: `verification.outcome=cleared`, `path=handoff_cleared`, `provider=google`,
  `fallback=False`, Google's organic results;
- you let it expire and choose `y`: `verification.outcome=timeout`, `provider=duckduckgo`,
  `fallback=True`, `fallback_reason=google:verification_timeout`, DuckDuckGo's organic
  results, one attempt only, same session, clean close;
- Google does not ask at all (cookies from your earlier verification are in the profile):
  `path=google_ui`, `verification.outcome` null, `provider=google` - also a PASS.

Answering `N` instead closes the session cleanly and stops; that is not a failure either, just
an unfinished qualification.

The command updates the installed agent first (worker 0.4.0 carries the new evidence schema;
one UAC prompt, the installer proves the live worker before anything else):

```powershell
.\scripts\browser\real-browser-smoke.ps1 -UpdateAgentFirst -Mode search -SearchMode interactive -HandoffTimeoutSec 45 -OutFile browser-search-handoff-2.json
```

Paste the final lines and `browser-search-handoff-2.json` whatever the outcome.

### 12. Google-primary search through the installed worker — **DONE (2026-09-04, provider path PROVEN_REAL; Google success not observed)**

Result: worker 0.3.0, schema 2, `requested_provider=google`, Google attempted in the installed
owner-session worker, `captcha interstitial detected; not answered`, `provider=duckduckgo
fallback=True fallback_reason=google:captcha`, DuckDuckGo organic results. Recorded as
QUALIFICATION 9.14 `PROVEN_REAL`; 9.12 (Google organic results) stays open and is item 14's
question. Do not rerun this mode.

#### (record) what item 12 asked for

Unblocks: `docs/QUALIFICATION.md` 9.12; the larger research run (item 10 step C) waits for it.

Runs on the already-installed 0.3.0 worker; nothing is redeployed (the script refuses to
continue if the live worker were not this checkout's release). One `browser.search` with
`engine=auto`: Google is the primary provider, DuckDuckGo the fallback. The output shows
`requested_provider`, the provider actually used, `fallback` and `fallback_reason`, every
attempt, up to five normalized organic results (rank, title, URL), the `command_id` and
`trace` of every device command, and the session identity (`worker_pid`, `browser_pid`,
`session_uid`) plus the local proof that the worker image lives under the installed tree and
is a child of the installed companion. PASS requires `provider=google`, `fallback=False` and
at least one organic result.

If Google shows its "unusual traffic" or consent page, the worker records
`fallback_reason=google:captcha` (or `google:consent`), takes exactly one DuckDuckGo attempt,
and the run ends `FAIL` with that reason in the JSON. That is the honest result: nothing is
retried in a loop, solved or bypassed. Paste it as it is. (An optional later variant,
`-Handoff`, would instead pause with `WAITING_FOR_OWNER_VERIFICATION` so you can complete
Google's page yourself in the visible window and the same session resumes; not part of this
action.)

```powershell
.\scripts\browser\real-browser-smoke.ps1 -Mode search -OutFile browser-search-1.json
```

Expected last line: `REAL BROWSER SMOKE: PASS`, with a `requested_provider=google provider=google fallback=False` line and result lines `#1 ...`. Paste the final lines and `browser-search-1.json`.

### 9. K66 re-qualification after the instrumentation + noise pass — **this is the current action**

Unblocks: rows 6.16–6.21 with attributable numbers; the revised voice target stays NOT
PROVEN until this rerun meets or materially approaches the latency targets and room noise
no longer causes distracting activations (your verdict).

What changed since your session 2 (ADR-0047/0048): every headline latency is now split
into measured components (capture → local gate → RTP send → provider receipt; detect →
stop command → gain zero for barge-in; response created → first delta → playback for
end-of-turn), the fixed ~210 ms barge-in delay was targeted, the K66 false starts and
false barge-ins were addressed without a global threshold, calibration reports whether it
actually measured, and Turkish text renders correctly in the fetched record. Cedar with
the Arbor profile is the default; `Sözleşme: v2` stays.

1. Start and sign in as before (`.\scripts\voice\start-web-voice.ps1`,
   http://localhost:3000/voice). Leave `Ses = Cedar`, mode `Otomatik`. Let the calibration
   finish (the mic area shows `Ortam`), then open **Tanılama** once and copy its JSON.
2. Run the same 14-scenario noise matrix as item 8 Part B (silent room, fan, typing, mouse,
   desk knock, air conditioner, TV speech, another person, music, street, the assistant on
   speakers interrupted with "dur", you quiet, normal, farther), then the hesitation check
   and a headset "dur" barge-in, plus five or six normal turns so the latency samples have
   n ≥ 5.
3. Pull the record:

   ```powershell
   .\scripts\voice\fetch-benchmark.ps1 -Latest -OutFile voice-session-3.json
   ```

Paste the JSON, the Tanılama JSON, and your verdict on: interruptions (did "dur" stop it
at once?), false activations (which scenarios still triggered it), and whether your quiet
or far speech was ever cut. What happens next, automatically: the breakdown says which
component owns each remaining millisecond, the noise counters go into rows 6.16–6.21, and
the K66 profile is pinned from the measured settings.

### 8. Combined voice-character + noise qualification — **DONE (2026-09-03)**

Result recorded in `docs/VOICE_OWNER_FEEDBACK.md`: persistence PROVEN_REAL; Cedar chosen;
latency targets not met (mic→uplink 391/472, EOT→first audio 674/1059, barge-in 210 ms
fixed), K66 false starts 5 / false barge-ins 4; mojibake in the fetched record (reading
side; fixed). The procedure below stays as the reference.

Unblocks: `docs/QUALIFICATION.md` rows 6.15–6.21, and the rest of Stage 6 as a side effect.

Setup, as in item 7: `.\scripts\voice\start-web-voice.ps1`, open
http://localhost:3000/voice, sign in once. New on the page: **Ses** (Marin / Cedar — the
two candidates for your Arbor target; the profile itself is applied on the server), the
microphone-quality area (`Mikrofon`, `Ortam`, `Gürültü bastırma`, `Ses algılama`, a live
meter), the modes **Otomatik / Sessiz ortam / Gürültülü ortam / Çok gürültülü ortam**
(leave `Otomatik`), **Yeniden ölçümle** (recalibrate) and **Tanılama** (diagnostics: noise
floor, speech probability, input level, clipping, the applied browser constraints, and a
"copy JSON" of the K66's real MediaTrack settings/capabilities — paste that JSON once).

**Part A — voice character (5 minutes).** Same short Turkish exchange with `Ses = Marin`,
then again with `Ses = Cedar` (three or four turns each, including one long answer you
just listen to). Verdict, in your words: which is closer to Arbor, and what is still off
(pace, warmth, cheerfulness, "asistan" cadence, Turkish prosody).

*Server version note (2026-09-03).* Your first Connect failed with `HTTP 422` because the
Cloud Core still ran the previous release (contract v1, no `voice` field). The page now
detects the server's contract version and would have said so; and the Cloud Core has since
been released to the current contract (c6f27c5, 2026-09-03): the release qualification
proved contract v2 served, the key inside the container, the provider listed, and `marin`
and `cedar` each minted from Hetzner and echoed unchanged by OpenAI. The page should now
show `Sözleşme: v2` with the `Ses` selector enabled. Part A is on.

**Part B — noise matrix (10–15 minutes), with the winning voice.** Keep the session open
in `Otomatik`; for each scenario do it for ~20 seconds and only speak when the scenario
says so. Watch `Ses algılama`: it should say background, not owner speech, unless you talk.

1. quiet room, silent; 2. computer fan only; 3. typing on your keyboard; 4. mouse clicks;
5. a desk knock; 6. air conditioner / fan; 7. TV speech in the background; 8. another
person speaking nearby; 9. music; 10. street / traffic sound (window open, or a
recording); 11. the assistant speaking through the **speakers** (headset off) — interrupt
it with "dur" while it talks; 12. you speaking quietly; 13. you speaking normally;
14. you speaking from farther away.

Then the hesitation check once more under the final configuration ("Şey... yani... hani
şu..." with a real one-second pause), and a normal "dur" barge-in with the headset on.

**Part C — the numbers.** The page counts false speech starts, false barge-ins, false
turns and calibration results per session and reports them to the Cloud Core, where the
record is durable (it survives disconnect and the provider's secret expiring). Pull the
newest session without typing any id:

```powershell
.\scripts\voice\fetch-benchmark.ps1 -Latest -OutFile voice-session-2.json
```

(For a specific session use the page's **Session ID kopyala** button and pass
`-SessionId <pasted UUID>`; a wrong id lists the recent sessions instead of failing
silently. Your 2026-09-02 session `2b3517ed-…-47898ea23334` is still there.)

Paste the JSON, the diagnostics JSON, and your verdicts (Part A; for Part B which
scenarios still triggered it, and whether your own quiet/far speech was ever cut).
What happens next, automatically: rows 6.15–6.21 are filled from the counts and your
words, the winning voice becomes the configured default, the K66 profile is pinned, and
if background human speech (7–8) still commands the system the owner-directed strategy
(ADR-0043 §4) is the next work item — with honest numbers, not a claim.

### 7. Real Turkish microphone / WebRTC session — **DONE (2026-09-02)**

Completed: first real session on the K66 through the real path. Owner verdict recorded in
`docs/VOICE_OWNER_FEEDBACK.md` (generally good; Arbor target; noise defect). The
procedure below stays as the reference for the plain conversation script.

Unblocks: every `docs/QUALIFICATION.md` Stage 6 row (6.1–6.14). Nothing else can.

What you need: this PC on the tailnet (as for the cloud qualification), a headset or the
laptop microphone, the cloud Owner Credential (typed once into the page; exchanged for a
session and dropped), and a quiet ~10 minutes.

1. Start the web shell against the real Cloud Core (same-origin proxy over Tailscale; no
   change on the host, the running api container is not touched):

   ```powershell
   .\scripts\voice\start-web-voice.ps1
   ```

2. Open http://localhost:3000/voice, sign in once, allow the microphone, press start. The
   page shows the session id (first 8 characters), the provider (`openai-realtime`) and
   the transport. Then, in Turkish, at your normal pace:

   - a free exchange, two or three turns ("Bugün ne yapmamı önerirsin?" and follow-ups);
   - **barge-in**: while it is speaking, cut in with "dur" — playback must stop at once;
     then "devam";
   - **hesitation**: "Şey... yani... hani şu, geçen hafta konuştuğumuz..." with a real
     pause of about a second before you finish the sentence — it must NOT answer early;
   - **mixed terms**: "OpenAI Realtime API ile WebRTC bağlantısı kuruldu mu?";
   - **a long-running tool**: "Son üç gündeki yapay zekâ gelişmelerini araştır" — it should
     say a short natural preamble and keep the session alive; then redirect it mid-task:
     "Sadece OpenAI kısmına bak";
   - **Turkish letters**: "Işık, İstanbul, ğ, ş, ç, ö, ü — bunları tekrar et".

   Note anything that felt wrong (a cut-off, a late stop, a mispronunciation, an English
   accent): that is the subjective row, and only you can fill it.

3. Pull the numbers for the record (the credential is typed into a masked prompt; the
   fetch mints one session and revokes it; the output is ids and timings only):

   ```powershell
   .\scripts\voice\fetch-benchmark.ps1 -SessionId <the full session id from the page> -OutFile voice-session-1.json
   ```

   Paste the printed JSON (or the file's content) to the agent together with your notes.
   What happens next, automatically: the five metrics are recorded against the Stage 6
   rows with their targets, every defect you noticed becomes a tracked fix, and the next
   session is scheduled only when something changed.

### 6. Realtime voice provider credential (OpenAI Realtime) — **DONE (2026-09-02)**

Completed and PROVEN_REAL: key stored (DPAPI), local probe minted every M12 session layer
with `gpt-realtime-2.1`, the M12 Cloud Core was released (ADR-0042) and the key is live
inside the running api container with the provider selected and a real client-secret mint
from Hetzner. The three commands below stay as the reference for the NEXT provider key.

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

3. Put it live on the Cloud Core — **this is the current action**. The key is already in
   `/opt/pagentos/.env` on the host (shipped on 2026-09-02, stdin only). What was missing is
   the M12 release itself: the host still ran the 1 Sep tree and image, whose compose had no
   wiring for the variable, so a restart changed nothing (ADR-0042). One command releases
   HEAD, recreates only the api workload, and proves the key inside the running container,
   the provider in `/v1/system/health`, and one real client-secret mint from the host:

   ```powershell
   .\scripts\cloud\release-cloud-core.ps1
   ```

   Add `-Preflight` first to validate without changing anything. For the NEXT secret (any
   provider), the command is `.\scripts\cloud\set-cloud-secret.ps1 -Name <NAME>`, which now
   fails unless the recreated workload actually carries the variable.

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
