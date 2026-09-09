---
name: m26-latest-news-mode-review
description: Latest News Mode (M26 addendum) pre-merge review (2026-09-09). HIGH (live PoC) - the Shorts exclusion is structurally INERT on the only provider that ships; is_short() returning False means "not a Short" OR "no evidence", and YouTubeFeedProvider is permanently the second (Atom feed has no duration, no is_short flag, and every url is built as /watch?v= so the /shorts/ marker can never fire), so an untagged Short posted after the day's bulletin was SELECTED for latest_full_broadcast/latest_main_news and flagged only ambiguous=True, which nothing downstream reads - the owner would be shown a 30-second clip and told it was the main news. All 288 resolver tests passed while it was live because every fixture set duration_s/is_short explicitly, information the real provider never supplies. MEDIUM - "youtube.com" not in netloc is a substring test (youtube.com.evil.example, notyoutube.com, evil-youtube.com.attacker.net all pass), an SSRF primitive and attacker-chosen channel_id the moment the documented fetch_page seam is filled. MEDIUM - untrusted video titles reach verbatim TTS and the model's tool context unbounded and with control characters intact. MEDIUM - the news browser profile is not media-only although the protocol doc says it is. MEDIUM - the feed is parsed with raw ElementTree and no size cap, in a repo that added defusedxml after exactly this finding. LOW - POST /v1/news/open does not validate content_type (unhandled 500 vs the 400 /resolve returns). Verified sound - channel_id regex-validated before the feed URL template, played url always synthesised by our own code, three-way profile isolation uses resolve()+is_relative_to() not a leaf is_symlink(), news.summarize reaches no device capability, close_playback touches only its own row, _news_match checked before DOCUMENT_SUMMARIZE, an unmatched channel hint refuses rather than defaulting, all REST routes owner-gated, migration expand-only.
metadata:
  type: project
---

Reviewed `worktree-agent-ae92cb1700ac78996` before it merged: `app/news/*`, the voice
router's three new intents and `tools_news.py`, migration `0034_news_mode`, and the
PRODUCTION browser worker's new third persistent Chrome profile. 142 API and 13
browser-worker tests were executed live; no browser was launched and no outbound call made.

The reviewer has no Write tool, so the findings were transcribed here by the integrator,
with the fixes recorded beside them. Full text in ADR-0092 addendum 1.

**The HIGH is the "two halves that never met" class again**, and the sharpest example of it
so far. The classifier was correct; the fixtures fed it evidence the production provider
cannot produce; every test passed; the live path was broken. The fix that matters is not
`can_decide_shortness()` — it is that the new regression tests build their candidates
through the REAL `_parse_feed`, so a fixture can no longer supply what production cannot.
Whenever a filter's answer depends on a field, ask which provider actually populates it.

**A test that stops intercepting starts dialling out.** Three "offline" tests patched
`httpx.get`; the size-cap fix changed the call to `httpx.stream`; the patches silently
missed and those tests made REAL requests to YouTube (one returned fifteen genuine NASA
uploads where it expected two fixtures). Only an unrelated count assertion caught it. A
monkeypatch aimed at a third party's API is only as stable as that API's shape — put a
named seam in your own module and patch that.

Related: [[m26-executive-autonomy-review]], [[m26-location-weather-briefing-review]],
[[contract-halves-must-read-each-other]], [[junction-escape-recurring-pattern]].
