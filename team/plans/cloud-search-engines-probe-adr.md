# ADR (draft, the lead numbers it): the search-engine probe for the Cloud Core's address

Status: proposed (card `cloud-search-engines-probe`, cycle d20261003). Relates to ADR-0213,
ADR-0248 ('At merge (the lead)').

## Context

Before the owner is asked to switch `research_execution_rule_enabled` on, somebody has to
know which search engine answers the cloud worker from the datacentre address without a
verification wall. The only numbers so far are from the home PC's address in a local image
(bing answered 10 results; duckduckgo ended in its captcha twice out of two). Brave in
headless Chromium and every engine from the datacentre address are unmeasured.

## Decision

1. An instrument, `browser_agent.cloud.engine_probe`, in the cloud package (the production
   image carries it once it is rebuilt by ADR-0248 release-order step 2 - the owner's
   approval; nothing here builds the image). It drives the UNCHANGED worker over stdio
   (`SubprocessWorker` + `build_worker_args`: headless Chromium, a dedicated profile on a
   temporary data dir) and reads the outcome `run_search` itself recorded in `attempts`
   (also inside a PROVIDER_RATE_LIMITED error's evidence). It has no classifier of its own.
2. The numbers: the engines are `search_engines.ENGINES` (read, never retyped); three fixed
   public questions (two Turkish, one English, none the owner's); at most **12** requests per
   run, one run per invocation; one attempt per (engine, question), **no retry**; at least
   **20 s** between two requests to the same engine; **45 s** per request; **15 min** for the
   whole run (the rest is `not_run_deadline`). A fifth engine makes the default plan refuse
   until the cap is decided again.
3. No bypass. No captcha is answered, no consent button pressed, no stealth plugin, proxy or
   changed user agent. Every search says `interstitial: "fallback"`, never `handoff` (the
   window is headless; nothing may wait for a person). After an engine's first wall
   (captcha, consent, blocked) or failure (transport_error, error) its remaining questions
   are not sent (`skipped_after_wall`).
4. The host side, `infra/docker/cloud-browser/measure-search-engines.sh`: a THROWAWAY
   container (`docker run --rm`) from `pagentos/cloud-browser:local`, under the limits of
   `compose.fragment.yml` (memory 2g, swap 2g, shm 2gb, pids 512, cpus 2, init, cap-drop
   ALL, no-new-privileges) and a 512m tmpfs at /tmp. It SHARES with the production worker
   the image and the host's outbound address - exactly what is being measured. It does NOT
   share its state, profile, volumes (`/mnt/pagentos-data` is never mounted), broker URL or
   enrollment token; it never execs into, stops, restarts, builds or recreates any
   container and runs no `docker compose`. It refuses below 2.5 GiB of available host
   memory (the CPX32 also runs the api) and exits 3 with one line when the image does not
   carry the module. It writes nothing on the host.
5. Output: a markdown table (one row per planned request: outcome, result COUNT, wall,
   error class, elapsed ms), one summary line per engine ('answered n of 3' or 'wall:
   captcha at query 1') and the meta (date UTC, image digest, worker version, Chromium
   version, "the host's own outbound address (not printed)"). No address, no result title,
   no URL. `--json` adds the same rows as JSON.

## How the lead runs it

After the release, on the host, read-only:
`infra/docker/cloud-browser/measure-search-engines.sh --json`, then saves standard output on
his own PC as `docs/evidence/cloud-search-engines-<date>.md`.

## What the table does not do

It decides nothing by itself. `CLOUD_SEARCH_ORDER` and the setting stay as they are; the
order is the lead's decision from the table (a later card) and the setting is the owner's,
shown the table first. An `empty` row may be a wall the worker has no marker for (the
summary line says so): read it as suspect, not as "answered".

## Evidence

- PROVEN_AUTOMATED: the tool (unit tests, four mutation REDs).
- NOT_RUN: the measurement from the Cloud Core's address - the lead's run on the host.
- NOT_RUN: a run in a locally built image from the home address (would only prove the tool
  runs end to end; it says nothing about the datacentre address).
- NOT VERIFIED: the three wall fixtures (`duckduckgo-captcha.html`, `bing-wall.html`,
  `brave-wall.html`) are hand-reduced, not recordings; whether Bing's and Brave's real wall
  pages carry a marker the worker's `classify_page` knows is unknown.
