# Model & Provider Routing

## 1. Principle

No provider is the product architecture.

Define interfaces for:

- reasoning/chat;
- coding;
- embeddings;
- vision;
- realtime voice;
- STT;
- long-form TTS.

## 2. Coding

Initial autonomous engineering backend: Claude Agent SDK/Claude Code ecosystem.

Use separate builder and reviewer contexts.

## 3. Reasoning

The runtime may start with one strong provider, but request routing should expose:

- capability;
- latency class;
- cost class;
- privacy class;
- context requirement;
- tool support.

## 4. Voice

Do not force realtime dialogue and narration through the same provider.

Example policy after benchmark:

```yaml
realtime_dialogue: provider-A
long_form_turkish: provider-B
stt_clean: provider-C
stt_local_fallback: faster-whisper
```

## 5. Fallback

Provider errors should map to typed failure and allow fallback where semantics remain safe. Avoid silently changing a high-quality narration voice mid-paragraph; switch at a segment boundary.
