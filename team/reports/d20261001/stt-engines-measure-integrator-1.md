# Integrator report — stt-engines-measure (d20261001)

**Soniox's published terms do not allow training on customer audio, and the real-time API stores nothing.** The only retention is the Async/file API (30 days, deletable), which the plan forbids the adapter to use. Two things are not clean: processing is in the US by default, and I could not read the DPA or a sub-processor list without an account.

Plan: `team/plans/stt-engines-measure-integration.md` — written in the main checkout, uncommitted; the worker's worktree needs it handed over.

**There is nothing to measure today: 0 recordings, 0 seconds of owner speech.**
- **Audio files:** none tracked, none untracked in the checkout, none under `%LOCALAPPDATA%\PagentOS`, none in `docs/evidence` (259 files).
- **Enrolment:** embeddings only; the keyword enrolment zeroes its frames.
- **K66:** text notes of two sessions (2026-09-02 and 09-03), no audio.
- **Fixtures:** 22 text labels in `STT_CASES`, whose "audio" is a sine tone only the fake recogniser can read.
- **Confusions:** `stt-confusions.json` holds one entry (`ofisü` → `ofis`), so the "list from the confusion cases" has one real case; the plan proposes the twenty sentences.
- **Engines configured on this PC:** OpenAI only (key in the DPAPI store). No Soniox or Azure key; faster-whisper is not installed.

**Choices**
- **Soniox: adapt.** Own adapter over `websockets` 17.1 (already locked), no vendor SDK, no new dependency.
- **OpenAI: reuse** `OpenAISTTProvider` for both `whisper-1` and `gpt-4o-transcribe`. The second is the model the live session transcribes with, so it is the baseline that matters.
- **faster-whisper 1.2.1 + large-v3-turbo: measurement only, out of tree** (`uv run --with`, behind a `-LocalWhisper` switch). Both MIT; Turkish verified in Whisper's language list.
- **Parakeet v3: rejected.** Its model card lists 25 languages and Turkish is not one (CC-BY-4.0).
- **Chrome Web Speech: not measurable from files**; it appears in the table as NOT_RUN.

**Soniox terms** (Terms of Service and Privacy Policy both dated 2026-06-29, read 2026-10-01; links in the plan)
- Soniox Inc., California; EU region "enabled by request".
- Usage metadata is logged; the adapter must send no `client_reference_id`.
- Turkish is listed, but no Turkish accuracy figure is published.
- Price is 0.12 USD/hour, so twenty sentences cost about 0.004 USD.

**Footprint on the home PC** (i7-14700KF, 47.8 GB RAM, RTX 5060 Ti 16 GB, C: 70 GB free) — estimated from the upstream table and file size, not measured, since measuring means installing:
- Soniox and OpenAI: none locally.
- large-v3-turbo: about 2–2.5 GB VRAM on GPU or 1.5–2 GB RAM on CPU int8, plus 1.6 GB on disk, which must go to E:.

**Risks**
1. **The acceptance pair does not differ in `Intent`.** Both sentences resolve to `app_open` / `calc` today; only the named device differs (`('ofis',)` vs `()`). A count on `.intent` alone gives 0, not 1. The plan defines the signature to compare (§7.1).
2. **Two OpenAI models share one `provider.name`**, so the table must key by its own engine label or they merge into one row.
3. **Recordings must be WAV.** Windows 10's Voice Recorder writes `.m4a`, which the existing OpenAI request builder (outside the area) sends as `speech.bin`. Who records twenty WAV files is unsolved in this card.
4. **The API key travels in Soniox's first frame**, so "sends nothing before the first audio chunk" means not even connecting until a chunk is in hand.
5. **The evidence JSON holds the owner's transcripts** in a GitHub-hosted repo — harmless for scripted sentences, but the summary must say so.
6. **Unverified:** CTranslate2 on an RTX 50-series card (CPU fallback planned), and the Soniox model name `stt-rt-v5`, which comes from a docs example.

**Lead decisions**
- Whether `-LocalWhisper` may exist (out-of-tree install and a 1.6 GB download on the home PC).
- Who records the WAV files.
- Whether the §7.1 intent signature is accepted.

**READY_FOR_OWNER**
- Twenty WAV recordings (list in plan §4).
- By separate approval, a Soniox account: ask for the EU region and read the DPA in the Console before the first run.

I added no dependency, wrote no feature code, opened no account and sent no audio anywhere. The THIRD_PARTY entry text is in plan §10 for the lead to append.
