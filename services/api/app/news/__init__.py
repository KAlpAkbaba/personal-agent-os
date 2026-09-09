"""Latest News Mode (docs/M26_LATEST_NEWS_MODE_SPEC.md).

Durable, owner-governed news-source configuration; a real latest-upload resolver
(never search rank, never guessed channel identity); governed playback through the
browser worker's dedicated ``news`` profile (packages/protocol/BROWSER_CAPABILITIES.md
§1/§2, v1.3); and a separate summary mode that reuses the existing M13 research
pipeline rather than a second one. See the package's own modules for the split:

- ``models`` — the three durable rows (``news_sources``, ``news_resolutions``,
  ``news_playback_contexts``).
- ``contracts`` — the closed content-policy vocabulary.
- ``classification`` — pure Shorts/promo/bulletin classification (no network).
- ``resolver`` — the pure "what does 'latest' mean" decision function the fixtures in
  ``tests/unit/test_news_resolver.py`` exercise.
- ``provider`` — where candidates come from: a deterministic ``FixtureNewsProvider``
  and the real ``YouTubeFeedProvider`` (the channel's own Atom feed, Cloud Core
  ``httpx``, no browser).
- ``identity`` — resolving an owner-given channel URL to its EXACT canonical channel
  id; never guesses from a display name.
- ``sources_service`` — CRUD for configured sources.
- ``summary_service`` — routes "haberleri özetle" through the existing research
  pipeline; never plays anything.
- ``playback_service`` — governed playback through the browser worker's own ``news``
  profile/context.
- ``routes`` — the REST surface.
"""

from __future__ import annotations
