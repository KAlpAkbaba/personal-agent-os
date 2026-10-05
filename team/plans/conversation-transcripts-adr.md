# ADR (number at merge): Conversations are kept as text; a voice is named only with consent

Status: accepted (worker, cycle d20261005, card conversation-transcripts)
Owner request 2026-10-05: "Writes down his conversations, and knows who is speaking" - text
like Wispr Flow, never the audio; tell voices apart; 'bu kim?' / 'bu Ahmet' and from then on
that voice is written under the name.

## Decision

1. **Text only.** A conversation (`conversations`, start/stop, `mode` manual|home) is lines in
   `conversation_segments`: text, time, `is_owner`, the voice's number in that conversation
   (`speaker_no`) and, for a consenting named person, `person_id`. No table has an audio column;
   `POST /v1/conversations/{id}/segments` refuses a body carrying `audio`/`pcm`/`wav`/...
   (422 `audio_refused`). Where a caller still holds the microphone buffer,
   `transcribe_and_drop` runs STT and the embedder on it and zeroes it, also on failure.
2. **Voices apart in memory.** Inside one conversation the derived embeddings are grouped by
   running centroid (`VoiceClusterer`, cosine >= 0.70) in process memory only and dropped at
   stop. A new unnamed voice sets `ask_who` once per conversation ('bu kim?').
3. **Consent before a profile (KVKK).** 'bu Ahmet' without consent records the name only:
   no profile, the lines stay 'Konuşmacı N'. 'Ahmet izin verdi' (`consent_at` + optional note,
   set by the owner) seals the group's centroid with the voice-profile cipher (Fernet, the
   same secret as the owner's profile) into `conversation_people.profile_sealed`; a CHECK
   (`profile_sealed IS NULL OR consent_at IS NOT NULL`) refuses a profile without consent in
   the database itself. Later conversations recognise the person at cosine >= 0.75 (the owner
   verifier's accept bar).
4. **Labels are read, not stored.** 'Sen' (owner, by the caller's flag or the owner verifier
   on his enrolled profile), the person's name, or 'Konuşmacı N'. Deleting a person deletes
   the profile and the name; `person_id` goes NULL (service + FK `ON DELETE SET NULL`) and every
   line reads 'Konuşmacı N' again. 'unut' (DELETE /v1/conversations) deletes every conversation
   and line; people and their consent are separate and deleted per person.
5. **'evde dinle'** is a standing switch (`conversation_settings`, one row).

## KVKK / TCK 133

- A voiceprint that identifies a person is biometric = special-category personal data (KVKK
  m.6): processed only with explicit consent, recorded with its date. Without consent nothing
  biometric outlives the conversation (centroids are in memory only).
- TCK 133 (recording a non-public conversation without consent) concerns recording the
  CONVERSATION. Text transcription of people other than the owner is still a recording of
  their words: the owner starts it, or turns 'evde dinle' on, and is responsible for telling
  those present. The product keeps no audio, shows the owner what is written, and deletes on
  'unut'. This is the owner's personal/household use (KVKK m.28(1)(a) exemption for natural
  persons' household activities) - not legal advice; a business use (Aktivra) needs its own
  basis.

## Diarisation choice (open - measurements NOT_RUN)

The service takes a DERIVED embedding per line from the caller; which model produces it is
not decided here. Candidates: a local speaker-embedding model on the home PC (e.g. an
ECAPA/pyannote-class embedder - licence and CPU cost to measure) vs the STT provider's
diarisation (`gpt-4o-transcribe-diarize`, labels only per request, no cross-conversation
identity, so recognition by name would still need a local embedding). No integrator plan was
attached to the card; no Turkish quality or CPU measurements were made. Thresholds 0.70/0.75
are fixture-tuned and must be re-measured on the chosen model.

## Consequences / follow-ups

- Voice wiring is not in this card's area: the spoken 'bu kim?' prompt from `ask_who`, the
  'bu Ahmet' / 'Ahmet izin verdi' / 'unut' intents (`app/voice/intents.py`), and the
  microphone -> STT -> embedder feed. Until then the API and the web page work; the owner trial
  (PROVEN_REAL) waits for that card.
- `alembic/env.py` should import `app.conversations.models` (autogenerate registration;
  outside the area - one line at merge).
- A process restart mid-conversation loses the groups; new voices continue numbering after the
  highest stored `speaker_no`. A naming waiting for consent in a stopped conversation is not
  kept (the owner names again).
- Memory extraction may read `conversation_segments.text` under the existing owner-approved rules.
