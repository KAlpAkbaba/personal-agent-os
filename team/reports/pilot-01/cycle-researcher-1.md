I wrote the three proposals, each ready for the owner's decision. I ran no tests and changed no code. I did not touch ROADMAP or the queue.

**Files written**
1. `team/proposals/2026-09-30-bulutta-yurutme.md` — the ADR-0213 cloud execution proposal.
2. `team/proposals/2026-09-30-anlati-satiri.md` — the narrative (roadmap 2c).
3. `team/proposals/2026-09-30-gorev-dongusu-bulutta.md` — browser task loop PR-C in the cloud. It depends on proposal 1 and should not start before it is approved.

**Findings the lead should know**
- **CPX41 no longer exists.** The 15 June 2026 Hetzner price table lists CPX22/32/42/52/62 and no CPX41 ([Hetzner](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)). The real 16 GB step is **CPX42 at €69.49/month** against CPX32 at €35.49, about €34/month more. ROADMAP 2b and the ADR-0206 reopen condition both say "CPX41" and need correcting.
- **ADR-0213 is not written.** `docs/DECISIONS.md` has no such ADR. Only ROADMAP 2b and the bootstrap prompt mention it. Proposal 1 says the first job after approval is to write it.
- **`ManagedBackend` already exists.** It is a headless Playwright backend with a dedicated profile in `services/browser/browser_agent/backends.py`, so the cloud worker needs no new browser code. That backend is what proposal 1 builds on. Its main risk is Chromium memory next to the API on an 8 GB host. The proposal starts on CPX32 with a 2 GB limit and moves to CPX42 only if the measurement shows it is tight.
- **PR-C is blocked on the owner.** ADR-0207's `<MAĞAZA>` and `<WEBMAIL>` placeholders are still empty. Only tasks T1, T2 and T4 can run in the cloud, because T3 and T5 need the owner's signed-in Chrome. That is why proposal 3 is worth asking about.
- **A decision needs re-reading.** ADR-0207 decision 3 says "no unattended task". A cloud task can keep running when the owner leaves. Proposal 3 suggests allowing a task the owner started to continue, while no routine may start a new one. This widens the owner's own decision, so it has to be asked explicitly.
- **Narrative:** `ledger.query`, `latest` and `explain/engine.py` (which has a failures query) already exist. I could not confirm how well `explain` handles "bu hafta" today, so I did not claim a gap there. The proposal makes a deterministic checker guarantee that every failure appears in the account. That guards against omission, the failure the owner would mind most.

**Evidence is thin here**
- The Playwright memory figures and the sandbox comparisons come from blogs, so on-host measurement has to be the real proof.
- The summarization-risk sources are a blog and a preprint.
- I did not read the gVisor issue tracker. I named that as a task for the integrator.
- I did not verify the price of a small second VPS or the cost of a task's model calls.
- I did not read the licences of Stagehand or Browser Use, because I recommend against using them.

**Questions for the owner (one per proposal)**
- Proposal 1: start on CPX32 with measurement, and ask separately about CPX42 (+€34/month) if it is tight?
- Proposal 2: build the narrative on the ledger and failures first, with memory as step two?
- Proposal 3: prove T1, T2 and T4 in the cloud first, and let a task the owner started keep running after they leave?

The lead should queue all three as `awaiting_owner`.
