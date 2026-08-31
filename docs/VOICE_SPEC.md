# Voice Core Specification — Turkish First

## 1. Voice is a primary UI

The owner must be able to operate the system without typing for normal workflows.

Separate pipelines:

```text
INPUT                               OUTPUT
Microphone                          Realtime dialogue
  -> AEC/noise/VAD                    -> low-latency voice
  -> speaker verification             -> barge-in
  -> STT/realtime model

Artifact                           Long-form narration
  -> semantic parser                 -> Turkish normalization
  -> narration planner               -> high-quality TTS
  -> chunk/cache                      -> resumable player
```

## 2. Realtime conversation requirements

- Turkish natural speech.
- Low perceived latency.
- Barge-in: owner speech stops current assistant speech quickly.
- Tool-use state can continue while assistant reports short progress.
- “Dur” always has high priority.
- Temporary network loss should fail gracefully and restore session context.
- Audio provider behind interface.

Candidate providers to benchmark at implementation time:

- OpenAI realtime audio models;
- ElevenLabs realtime/conversational voice;
- Azure Speech/realtime composition;
- future/local candidates.

Do not choose only from marketing claims. Run owner-specific A/B tests.

## 3. Long-form narration requirements

Long-form TTS is not the same as realtime dialogue.

Narration must support:

- chapter/section segmentation;
- pause/resume;
- playback-speed preference;
- resume across PC/mobile/web;
- sentence/paragraph cursor;
- “burayı tekrar oku”;
- “ikinci maddeye geç”;
- “bu ne demek?” -> explanation mode -> return to exact cursor;
- cached audio chunks;
- ahead-of-playback generation;
- cancellation of unused future chunks.

## 4. Turkish text normalization

Create a deterministic `tr-TR` narration normalizer before TTS.

Handle:

- dates;
- clock times;
- decimal comma;
- percentages;
- Turkish lira and other currencies;
- thousands/millions;
- ordinal numbers;
- phone numbers;
- IP addresses/CIDR;
- versions;
- email addresses;
- URLs/domains;
- file paths;
- abbreviations;
- English technical terms inside Turkish text;
- tables;
- bullet lists;
- code/log blocks;
- citations/footnotes.

Examples:

`31.08.2026` -> “otuz bir Ağustos iki bin yirmi altı”

`%17,2` -> “yüzde on yedi virgül iki”

`₺1.250.000` -> “bir milyon iki yüz elli bin Türk lirası”

`192.168.1.20` in technical mode -> speak octets separated by “nokta”.

## 5. Pronunciation dictionary

Per-owner editable dictionary:

```yaml
PostgreSQL: preferred spoken form
SQL: preferred spoken form
API: preferred spoken form
VMware: preferred spoken form
TURKA: preferred spoken form
```

Do not encode guesses permanently without evidence. Explicit owner corrections get highest confidence.

If the chosen TTS provider supports pronunciation dictionaries, use them through the provider adapter; otherwise preprocess text/phonemes as supported.

## 6. Tables and technical output

Default semantic narration:

A table becomes a concise verbal interpretation, not a row/column dump.

Code/logs default to:

- result summary;
- critical warnings/errors;
- literal content only when requested.

## 7. TTS provider benchmark

Maintain a `VoiceProvider` interface with capabilities:

- languages;
- streaming;
- long-form stability;
- pronunciation dictionary;
- voice selection;
- speed control;
- cost metadata;
- output format;
- latency.

Initial benchmark candidates:

### ElevenLabs

Evaluate:

- Multilingual v2 for stable long-form Turkish;
- v3 for expressive high-quality narration;
- v3 Conversational/other realtime path if current API quality supports Turkish target.

### Azure Speech

Evaluate current Turkish voices including neural/MAI variants available at implementation time.

### OpenAI

Evaluate current realtime audio for conversation and current TTS endpoint for narration.

## 8. Owner-specific A/B test

Create at least:

- 50 short sentences;
- 30 technical sentences;
- 20 long paragraphs;
- 20 number/date/currency cases;
- 20 mixed Turkish-English technical cases;
- 10 table narrations.

Owner listens blind where possible and chooses A/B. Store preference scores per use case.

## 9. STT benchmark

Measure:

- clean desktop mic;
- laptop mic;
- headset;
- phone;
- office noise;
- car/background noise sample when safely available;
- technical Turkish-English vocabulary.

Metrics:

- WER/CER on labeled set;
- command-intent accuracy;
- endpointing latency;
- false wake/false command rate.

Local fallback candidate: Faster-Whisper.

## 10. Speaker verification

Classification:

- `OWNER`
- `NOT_OWNER`
- `UNCERTAIN`

Signals:

- speaker embedding similarity;
- trusted device/session;
- microphone/device history;
- replay/spoof indicators where available.

Voice alone is not the sole authentication secret.

Enrollment should gather varied owner speech samples. Store encrypted embeddings/derived profiles where possible rather than unlimited raw voice.

## 11. Audio formats

For realtime transport, use provider/WebRTC-compatible codecs.

For narration cache, prefer Opus/AAC suitable for mobile streaming while retaining metadata mapping audio offsets back to document semantic positions.

## 12. Voice memory

Store preferences such as:

```yaml
language: tr-TR
executive_summary_first: true
narration_speed: 1.0
read_headings: true
read_urls: false
read_footnotes: false
barge_in: true
```

System may learn these, but explicit owner instruction overrides inferred behavior.
