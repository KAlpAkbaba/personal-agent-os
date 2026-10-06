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
3. a base64 block wrapped into lines - two lines of 40+ base64 characters (optional `=`
   padding, LF or CRLF) and 8 characters of a third: the `base64` CLI / MIME shape (76 a line)
   and PEM (64) keep every line under the 200 run (inspector, first pass: m4a, AAC/ADTS, AMR,
   raw PCM wrapped at 76 answered 201). A sentence line has spaces, so it never matches;
4. a base64 run carrying an audio file's magic and 8+ more characters: `UklGR` (RIFF), `SUQz`
   (ID3), `T2dnU` (OggS), `ZkxhQ` (fLaC), `GkXfo` (EBML: webm/mkv), `IyFBTV` (#!AMR) and
   `GZ0eX` (an m4a/mp4/3gp `ftyp` box four bytes in, after a box size that is a multiple of 4 -
   a phone's default recording). Case-sensitive, so ordinary words and the bare abbreviations
   themselves are not refused.

Shapes 2 and 3 start only where a base64 run starts (a negative lookbehind), so a 4000-character
line of 199-character runs costs ~0.4 ms instead of ~11 ms.

The check lives in the route (the HTTP edge, where the key check already lives); the service's
4000-character limit is unchanged. Nothing already stored is migrated (staging held only the
test's row).

## Why not more

A transcription never produces any of these shapes; a URL, digits, punctuation and long Turkish
sentences pass (tests prove it). Decoding base64 to sniff bytes was rejected: the three shapes are
enough, and decoding would mean handling untrusted bytes on the request path.

No detector is perfect; these escapes stay open on purpose (a follow-up card if the test team
uses them): base85/ascii85, hex split by spaces, base64 broken every few characters by a
zero-width space or by `.`/`=`, base64 wrapped under 40 characters a line, and a sound split
across many segments each under every threshold. The other text fields (title 120, name 80,
note 200 characters) are too narrow to carry real audio. The guard is the edge's; the owner's
rule (text only) is also kept by the client never sending audio.

## Evidence

`tests/unit/test_conversation_text_only.py`: 15 refused shapes, 8 accepted (incl. multi-line
Turkish notes and multi-line links), the length limit kept. First pass: RED before the change
(10 failed); two mutations (drop the call; break the magic list) RED. Return pass: 5 new refused
shapes RED before (76-line m4a, 76-line AAC, 64-line CRLF AAC, short unsplit m4a, 3gp box of 24);
mutations "drop the wrapped-block shape" (2 RED) and "drop `GZ0eX`" (2 RED), restored from a
backup copy with the same sha256.
