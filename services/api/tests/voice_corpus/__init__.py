"""The Owner Utterance Corpus (synthetic voice qualification, 2026-09-07 owner directive).

``corpus``  - the versioned cases: canonical phrases, paraphrases, ASR-noise variants,
              regressions, and deterministic template expansions, each with its expected
              route contract and its forbidden tools.
``harness`` - runs a case through the REAL canonical path: the utterance enters at the
              realtime session's events endpoint (the boundary right after transcription),
              the ONE router resolves it, the contract-expected tool is dispatched through
              the relay with the arguments the persona would pass, the forbidden tools are
              dispatched too so the relay's guards are proven, and side effects are checked
              against the fake device. The report is built from these results.
"""
