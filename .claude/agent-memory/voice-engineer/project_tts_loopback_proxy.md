---
name: project-tts-loopback-proxy
description: 2026-09-07 TTS->STT loopback proxy harness (app/voice/loopback.py, tts-loopback-qualification.ps1) on branch voice-loopback; baseline numbers, the marks it may claim, what is still open
metadata:
  type: project
---

The response half of the synthetic corpus exists on branch `voice-loopback` (worktree
`E:\AI\pagentos-wt-voice-loopback`, not pushed, not merged as of 2026-09-07 evening):
`app/voice/loopback.py` + `loopback_cli.py`, `scripts/voice/tts-loopback-qualification.ps1`,
ledger event `voice.tts_loopback`, VOICE_SPEC §7a, QUALIFICATION 15.10, ADR-0080 addendum.

Baseline (real OpenAI tts-1 -> whisper-1, 40 of the corpus's 44 distinct spoken sentences):
38 matched / 2 degraded / 0 mismatched / 0 error, mean WER 0.018, evidence
`docs/evidence/tts-loopback-2026-09-07-190306.json`. The two degraded are product findings
left standing: "Core'daki" spoken as "kor", and version "0.1.0" read as "sıfır nokta bir
nokta sıfır" garbled by the TTS - pronunciation-dictionary / version-reading candidates.

**Why:** owner directive 2026-09-07: every milestone's synthetic corpus needs a RESPONSE
test with honest classes - AUDIO GENERATION PROVEN_AUTOMATED/PROVEN_PROXY, LOOPBACK
SPEECH SEMANTICS PROVEN_PROXY always, PHYSICAL OWNER HEARING NOT_CLAIMED always.

**How to apply:** from M19 on, run the script after the corpus; never claim hearing from
it; a DEGRADED summary is a finding to name. Still open: `-Post` needs a Cloud Core release
carrying `voice.tts_loopback` (production refuses it with 422 until then); the OpenAI key is
in the DPAPI store as PAGENTOS_VOICE_OPENAI_API_KEY - pass it only via env to the child,
never print. Whisper writes suffix apostrophes ("sekiz'e"); the comparison folds them.
Related: [[project-m12-realtime-state]].
