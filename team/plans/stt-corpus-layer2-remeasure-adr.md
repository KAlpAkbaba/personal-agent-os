# ADR-0224 addendum (to be numbered by the lead): the STT corpus measured with layer 2 as production configures it

Status: measured 2026-10-03 (run 2026-10-03T01:50:59Z) at `05ca60596cf4f8ffa54c6be3a47d40b5410527d1`
(clean tree; an earlier run at `0dd1dc75` gave the same verdicts case for case).

## The number
- With the engine production builds since ADR-0245: **73 / 106 = 68.9 %**. Without it (addendum 4):
  **73 / 106 = 68.9 %**. Target >= 95 %: **NOT met**. Layer 2 made 0 cases worse and 0 better.
- The 25 not understood did NOT move into "correct". 18 of them moved `not_understood -> wrong_reading`: the decision's band is now the semantic top's
  (HIGH/MEDIUM) while the resolved intent stays `none` and nothing acts differently - in 17 of the
  18 the layer-2 top candidate is the meant intent. The cause is ADR-0224's own rule: a decision with
  no rule match is "recorded for the calibration, never acted on" (`policy.decide`).
- Wrong-device actions: 0, over the same 11 observable cases (the two-device world); the other 95
  cannot show a wrong machine, so the zero says nothing about them. Unchanged.
- "Confident wrong readings" 8 -> 26 is the judge counting those 18 semantic-only, non-acting turns;
  it is not 18 new misreadings.

## How the engine was built
`stt_harness.production_engine()`: `build_embedder(Settings(memory_embedding_provider="local",
memory_local_embedding_model=DEFAULT_LOCAL_MODEL))` then `configure_understanding(settings,
embedder, report=report, spawn=<inline>)` with the shipped `exemplars.json` (1427). The previous
default engine is restored afterwards; each case gets the engine through
`run_stt_case(engine=...)` (patched where `policy.configured_engine` reads it). If the local model
cannot be built it raises `ProductionEngineUnavailable` - never a number from the deterministic
embedder. The embedder is wrapped in a counter only (vectors untouched) to prove it was consulted.

## Machine and model
The home PC's CPU (i7-14700KF), `local-minishlab/potion-multilingual-128M`, index built in
403.7 ms, 106 cases in 51.0 s. Same model and same exemplars file as the Cloud Core; the Cloud
Core's own CPU run is NOT_RUN.

## What is measured
106 cases: 103 derived renderings (four stated distortions) + 3 real ones from the 2026-09-30
trial; all three real ones are correct with and without the engine. Real renderings beyond those
three are NOT_RUN until the misheard notebook (ADR-0254) delivers more.

## Consequence
The 95 % target cannot be reached by layer 2 as configured: it ranks but never acts without a rule.
The candidate cards (team/plans/stt-corpus-layer2-remeasure-candidates.md) are either rule/
normalize work per failure class, or a policy decision to let a semantic-only HIGH/MEDIUM reading
act with a read-back (upper bound from this run, not measured: 90 / 106).
