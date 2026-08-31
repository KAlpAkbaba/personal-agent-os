"""Turkish narration subsystem (M4, deterministic half).

This package is the no-owner-action, offline half of the M4 voice milestone:

- ``normalizer`` / ``numbers``: deterministic tr-TR written -> spoken text
  normalization (dates, money, IP/CIDR, versions, abbreviations, ...);
- ``tables``: semantic (not literal) Turkish narration of Markdown tables and
  code/log blocks (VOICE_SPEC §6);
- ``engine``: segmentation of a canonical Markdown artifact into stable semantic
  IDs (section/paragraph/sentence), ordered narration chunks, an ahead-of-play
  planner + chunk cache over an injected TTS provider seam;
- ``commands``: the "oku / dur / devam / tekrar / jump / explain" state machine
  where "dur" always wins and "explain" returns to the exact saved cursor;
- ``models`` / ``service`` / ``runtime`` / ``routes``: cross-device narration
  cursor + pronunciation-dictionary persistence and the REST surface.

Real audio, provider keys and network access live in ``app/voice`` (owned by the
voice provider engineer); nothing here touches them.
"""
