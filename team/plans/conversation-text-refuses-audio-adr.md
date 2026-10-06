# ADR draft: a conversation line refuses audio embedded in its text

- Card: conversation-text-refuses-audio (cycle d20261006)
- Finding: test team round t-manual-20261006e, staging 72884b71, tester-3 - POST
  `/v1/conversations/{id}/segments` with `content = "data:audio/wav;base64,..."` answered 201.

## Decision

`POST /segments` already refused a body whose KEY names audio. It now also refuses, with 422
`audio_refused` and the same Turkish sentence ("Ses kaydı alınmaz; yalnızca yazıya dökülmüş
metin."), a `content` that carries the sound itself. One regex (`EMBEDDED_AUDIO` in
`app/conversations/routes.py`) matches any of:

1. a `data:<type>/<subtype>` URL followed by `;` or `,` (any MIME, any case - a data URL is never
   a spoken sentence);
2. a run of 200+ base64/base64url characters with no space or other separator;
3. a base64 run of 12+ characters opening with an audio file's magic: `UklGR` (RIFF), `SUQz`
   (ID3), `T2dnU` (OggS), `ZkxhQ` (fLaC), `GkXfo` (EBML: webm/mkv). Case-sensitive, so ordinary
   words and the bare abbreviations themselves are not refused.

The check lives in the route (the HTTP edge, where the key check already lives); the service's
4000-character limit is unchanged. Nothing already stored is migrated (staging held only the
test's row).

## Why not more

A transcription never produces any of these shapes; a URL, digits, punctuation and long Turkish
sentences pass (tests prove it). Decoding base64 to sniff bytes was rejected: the three shapes are
enough, and decoding would mean handling untrusted bytes on the request path.

## Evidence

`tests/unit/test_conversation_text_only.py`: 10 refused shapes, 6 accepted, the length limit kept.
RED before the change (10 failed); two mutations (drop the call; break the magic list) RED,
restored from a backup with the same sha256.
