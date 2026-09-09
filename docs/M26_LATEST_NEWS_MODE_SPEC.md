# M26 addendum — Latest News Mode

Status: CLOSED 2026-09-09 as an M26 addendum (ADR-0092; QUALIFICATION Stage 24 row
24.16; merged and released to production in e6f08ff). Owner item 36 - the exact
"Show Ana Haber" channel URL - is the one thing outstanding, and it is the owner's to
give: this track will not guess a channel from a display name. Predecessors: M13 Research (`app/research`: the pipeline this milestone's
summary mode delegates to rather than duplicating), M18.3 alarm media
(`packages/protocol/BROWSER_CAPABILITIES.md` §1-§3b: the dedicated-persistent-profile
pattern and the verified-not-assumed playback discipline this milestone's playback reuses
structurally), the ONE deterministic router (`app.voice.intents`).

The owner's rule, in one line: **"haberleri aç" plays the real latest eligible video from
a channel whose identity was never guessed; "haberleri özetle" is a different operation
entirely — a research summary that never opens anything.**

## 1. Durable, owner-governed source configuration (`app/news/models.py`, `sources_service.py`)

`NewsSourceRow`: `news_source_id` (an owner/voice-facing slug, primary key), `display_name`,
`provider` (`youtube` today), `channel_input` (verbatim, for re-resolution), `channel_id`
(nullable — `NULL` means "not established"), `identity_status`
(`resolved` | `needs_identity`), `identity_resolved_by`, `enabled`, `priority`,
`content_type`. **The one rule that matters more than any of it**: `channel_id` is set
ONLY by `app.news.identity.resolve_channel_identity` from an authoritative signal — never
from `display_name`. A source created or updated with an unresolvable input (a bare name,
an `/@handle` URL with no live lookup) is persisted `needs_identity` and stays usable for
editing, never for resolution or playback until it resolves. `default_source()` (the
target of a bare "Haberleri aç.") is the lowest-priority ENABLED source whose identity is
RESOLVED — a `needs_identity` source can never be silently picked.

## 2. Channel identity resolution (`app/news/identity.py`)

`resolve_channel_identity(input, fetch_page=None)` accepts, in order of authority: (1) a
bare canonical id (`UC` + 22 chars); (2) a `/channel/UC…` URL — the id is IN the URL; (3) a
`/@handle`/`/c/…`/`/user/…` URL, resolved ONLY through an injected `fetch_page` reading the
channel page's own `<link rel="canonical">`/`channelId` metadata (never resolved in
production without a real fetch; the seam exists for a later live-lookup provider). A bare
display name — no URL at all — NEVER resolves. The owner's own "Show Ana Haber" is left
`needs_identity` deliberately: `docs/OWNER_ACTIONS.md` item 34 asks for the one URL.

## 3. The resolver: what "latest" means (`app/news/resolver.py`, `classification.py`)

`resolve_latest(candidates, content_type, answered_by)` — pure, offline, over
already-fetched `VideoCandidate`s (never search rank, never title similarity — real
`published_at` only):

- `latest_any_news`: the newest upload, unconditionally (the owner configured "no
  filtering" — even a Short or a promo is a legitimate answer for this policy value).
- `latest_full_broadcast` / `latest_main_news`: Shorts (`classification.is_short` —
  explicit signal, else duration ≤ 60s, else a `#shorts`/`/shorts/` marker) and promotional
  content (`is_promo` — fragman/teaser/trailer/promo markers) are excluded outright; among
  what remains, the newest candidate whose title/description carries an explicit bulletin
  marker ("ana haber", "tam bülten", "main news", …) is preferred; when NONE does, the
  newest surviving (non-short, non-promo) candidate is the safer fallback, and the result
  is marked `ambiguous` — recorded, never hidden inside a plain "newest" reason.

Near-duplicate titles (≥ 0.9 normalised token Jaccard, the same rule M13 §2 uses) collapse
onto the newest occurrence before selection; every rejected candidate carries its own
reason (`shorts_excluded` | `promo_excluded` | `duplicate:<id>`) — nothing is silently
dropped. `tests/unit/test_news_resolver.py`'s four fixture scenarios (plain-latest, newest
is a Short, newest is a promo with the bulletin second, near-duplicate titles), each
checked against every content-policy value, ARE this module's specification.

## 4. Providers and the live path (`app/news/provider.py`)

`FixtureNewsProvider` — deterministic, in-memory, what every test and the voice corpus run
against. `YouTubeFeedProvider` — the real, live path (spec's own preference order: prefer
the official channel feed/API first): one `httpx` GET of YouTube's own public per-channel
Atom feed (`/feeds/videos.xml?channel_id=…`), no API key, no browser. Live-qualified
2026-09-08 against NASA's real, public channel (`UCLA_DiR1FfKNvjuUpBHmylQ` — an
unambiguous, well-documented channel chosen specifically because it is NOT the owner's
genuinely ambiguous "Show Ana Haber" request): 15 real candidates, real distinct
`published_at` timestamps, `answered_by=channel_feed`
(`tests/unit/test_news_provider.py::TestYouTubeFeedProviderLive`, `@pytest.mark.live`,
never run by default CI). The channel's Videos-listing and structured-DOM-extraction tiers
(spec's own #2/#3 preference) are NOT implemented — an honest gap: both would need the
governed browser worker driving a real page, which this offline development environment
cannot qualify end-to-end. `NewsUploadProvider` is the seam a later track fills in without
touching the resolver or anything upstream of it.

## 5. Governed playback (`app/news/playback_service.py`; `packages/protocol/BROWSER_CAPABILITIES.md` §1-§2 v1.3)

A THIRD dedicated persistent browser profile, `news` (`services/browser/browser_agent/media.py`):
a sibling directory of both the research profile and the alarm profile, checked distinct
from BOTH at worker startup (`require_distinct_news_profile`) — a news video can never
land in the research browser (device/profile contention with a live research run) and can
never interrupt or replace the owner's wake song (the alarm's own, structurally separate
profile). `session_id` convention: `news-<news_media_context_id>` — the row's own id
(`NewsPlaybackContextRow`) IS the context id.

`open_latest_news`: resolves the latest eligible video, then dispatches
`browser.session_open` (`profile=news, session_kind=media`) then `browser.media_play` —
reusing the EXISTING v1.2 alarm-media operations verbatim (no new device capability): the
same verified-not-assumed playback proof (`media_play`'s own `verified`/`playing` fields,
the strongest evidence the worker actually offers), the same consent/CAPTCHA-wall
classification, on the `news` profile instead of `alarm`. `browser.session_open` succeeding
is NEVER read as proof of playback; only `verified: true` classifies the context
`playing`, everything else `unverified`/`failed` honestly. `close_playback`
(`browser.media_stop`) closes ONLY the named context's own session — idempotent, never
touches another context, never the research or alarm browser.

Live browser-worker unit tests (`services/browser/tests/unit/test_news_profile.py`, 15
cases): the three persistent profiles resolve to three distinct directories; a news+alarm
session pair coexist as separately-owned browsers; a second session on an already-owned
`news` profile is refused (`browser_lifecycle_violation`); the media-op session-kind guard
now names `news` in its fix message too. No browser is launched by any test (a recording
double stands in for `ManagedBackend`, the same discipline `test_media_ops.py` already
established for the alarm surface).

## 6. Summary mode (`app/news/summary.py`) — a DIFFERENT operation entirely

"Haberleri özetle." delegates to the EXISTING M13 research pipeline
(`app.research.service.start_browser_research` / `start_browser_research_workflow`) with a
built Turkish topic (naming the configured source when one is resolved) and a
one-day recency window — never a second research engine, and this path never opens a
browser or plays anything. This is the task brief's own mandatory negative, structurally
enforced: `news.summarize`'s handler contains no device dispatch of any kind.

## 7. Deterministic routing (`app.voice.intents`, ADR-0092)

Three new intents, checked in the ONE router before the M20 document block (so
`NEWS_SUMMARIZE` wins over the generic `SUMMARIZE`/`DOCUMENT_SUMMARIZE` "özetle" claims)
and requiring the "haber" noun stem (so a bare "aç"/"özetle" is never stolen from
DISPLAY_WAKE/EYE_ENABLE/the generic controls):

- `NEWS_OPEN` → capability `news.open` (ACTION) — "Haberleri aç.", "Son haberleri aç.",
  "Show'un son haberini aç.", "Bugünün Show Ana Haber videosunu aç.", "En son yüklenen ana
  haberi aç.", "Haberleri YouTube'dan aç."
- `NEWS_SUMMARIZE` → capability `news.summarize` (ACTION) — "Haberleri özetle.",
  "Haberleri anlat.", "Bugünkü haberleri özetle."
- `NEWS_QUERY_LATEST` → tool `news.query_latest` (QUERY, mutates nothing) — "Son haber ne
  zaman yüklenmiş?", "Şu an hangi haber videosunu açacaksın?" (the "hangi" interrogative is
  its own question trigger, alongside the shared "ne zaman"/"mı" shapes).

`ResolvedIntent.news_source_ref` carries a channel-name HINT the owner's WORDS carried
(the first token that is neither a generic qualifier, the "haber" noun, nor any inflection
of the open/summarize verb — stem-prefix matched so a future-tense "açacaksın" is excluded
too, not only the imperative forms) — `None` when the words named no source at all. The
voice tools (`app/voice/realtime_sessions/tools_news.py`) prefer this hint over the
model's own `news_source_id` argument, fuzzy-matching it against configured sources'
display names/slugs; a hint that matches nothing refuses (`news_source_not_found`) rather
than silently opening a different channel. `news.close` (`playback_id` argument — never
`context_id`, which the relay's own forbidden-key filter refuses as text-shaped) is
registered for cleanup but has no deterministic voice intent yet — the task's own phrase
list named open/summarize/query only.

## 8. Voice corpus (`tests/voice_corpus/corpus.py::_news_cases`, `harness.py::CTX_NEWS_SOURCE_CONFIGURED`)

A REAL, identity-resolved fixture source ("Show Ana Haber", channel id
`UCnewsfixturechannel0000`) with a deterministic `FixtureNewsProvider` behind it (an older
real bulletin, a newer promo — `latest_main_news` must skip the promo), registered on
`ToolContext.live["news_provider"]` exactly like `browser_gateway` is for research —
never the real network in a corpus run. 64 cases through the REAL relay
(`POST /events` → the router → `POST /tool-calls`), covering every phrase in the task
brief verbatim plus ASR-noise variants, asserting: the resolved intent, the mapped
capability, the exact `channel_id`/`video_id` a receipt names, and the mandatory
negatives — `news.open` never reaches `research.start`; `news.summarize` never reaches
`news.open` (and is the ONE other tool besides `research.start` allowed to create a real
research task, by design); `news.query_latest` never opens or summarizes anything; a bare
"aç"/"özetle" with no "haber" noun stays DISPLAY_WAKE/EYE_ENABLE. Two refusal cases (no
source configured at all; a named channel matching none of the configured sources) prove
the identity rule holds at the tool layer too, never only at configuration time.

## 9. A real bug this milestone's own tests found and fixed

Every `app.news.*` service function originally called `db.flush()` instead of
`db.commit()`. Every unit test passed regardless (a single long-lived session reads its
own flushed-but-uncommitted write within the same transaction) — the voice corpus caught
it the hard way: a source seeded in `harness.seed()`'s own session answered
"no_news_source" from the very next request, because a route/tool call opens its OWN
session per call (`ArtifactRuntime.session()`'s own docstring: yield then close, no
commit) and a write that was only flushed is invisible — silently rolled back — the moment
that session closes. Fixed (`db.commit()` throughout `sources_service.py`,
`resolve_service.py`, `playback_service.py`); regression tests added
(`TestCrossSessionPersistence` in all three `tests/unit/test_news_*.py` files) that
deliberately close the writing session before reading in a separate one — the shape that
exposed the defect and the only shape that would catch a recurrence.

## 10. Owner action

`docs/OWNER_ACTIONS.md` item 34: the exact YouTube channel URL for "Show Ana Haber" — the
one channel identity this milestone will not guess. Everything else runs today against the
fixture channel (corpus, unit tests) and, for the live path, against any channel whose
identity is actually established (the NASA qualification, §4).
