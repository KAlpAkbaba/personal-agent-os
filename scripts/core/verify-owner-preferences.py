"""The owner's three sentences, through the ONE router, against the saved preferences.

    "Yarın 07:30'da beni uyandır."  -> the SAVED wake song, never the tone fallback
    "Hava nasıl?"                   -> İstanbul, unless a fresher trusted device fix wins
    "Haberleri aç."                 -> the configured channel's latest eligible BULLETIN,
                                       chosen by real published_at order

Each is resolved by `app.voice.intents.resolve_intent` - the same deterministic router the
realtime relay uses, at the canonical transcription boundary - and then driven against the
REAL services and the REAL rows, so what is proven is the owner's own configuration and not
a fixture.

Side effects, stated: NONE by default. The wake check reads the approved song and asks the
pure `_media_source` what an utterance naming no media resolves to, so no alarm is created
and the owner is not scheduled to be woken by a test. The news sentence resolves and does
not open a browser unless `--open` is given. The weather sentence writes one
`weather_query_evidence` row, which IS the capability rather than a side effect of it.
"""

from __future__ import annotations

import argparse
import json
import traceback
from datetime import UTC, datetime
from typing import Any

out: dict[str, Any] = {
    "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")
}


def _factory():
    from app.artifacts.runtime import build_artifact_context
    from app.config import get_settings

    factory, _store = build_artifact_context(get_settings())
    return factory


def _route(utterance: str) -> dict[str, Any]:
    """The ONE router's own answer, before any tool runs."""
    from app.voice.intents import resolve_intent

    resolved = resolve_intent(utterance)
    return {
        "utterance": utterance,
        "intent": resolved.intent.value,
        "matched": getattr(resolved, "matched", None),
        "alarm_minutes": getattr(resolved, "alarm_minutes", None),
        "weather_place": getattr(resolved, "weather_place", None),
        "news_source_ref": getattr(resolved, "news_source_ref", None),
    }


# ------------------------------------------------------------------- the alarm


def wake_alarm() -> dict[str, Any]:
    """The sentence the owner reported as "it beeps", checked where the decision is made.

    `_media_source` is a pure function of (the alarm's own media, the approved wake song),
    and the utterance names NO media - so this needs no alarm, creates nothing, and cleans
    nothing up. The defect the owner reported was exactly this branch: with `media=None` it
    used to fall to the tone even when a song had been approved.
    """
    from app.alarms import service as alarms_service

    result: dict[str, Any] = {"route": _route("Yarın 07:30'da beni uyandır.")}
    with _factory()() as db:
        approved = alarms_service.get_wake_song(db)
    result["approved_wake_song"] = approved

    # media=None is what an utterance that names no song produces. `_media_source` returns
    # (what the owner ASKED for, what will actually PLAY) - and they are deliberately
    # different here: `{"kind": "tone"}` is the vocabulary's only word for "named nothing",
    # while the resolved identity is the approved song. Getting these two the wrong way
    # round makes a correct system look broken, which is what happened on the first run of
    # this script.
    asked_for, will_play = alarms_service._media_source(None, wake_song=approved)
    result["owner_asked_for"] = asked_for
    result["will_actually_play"] = will_play
    result["plays_youtube_not_tone"] = bool(
        will_play and will_play.get("kind") == "youtube" and will_play.get("url")
    )
    result["url_is_the_saved_song"] = bool(
        will_play and approved and will_play.get("url") == (approved or {}).get("url")
    )
    result["side_effects"] = "none - no alarm created, nothing scheduled"
    return result


# ----------------------------------------------------------------- the weather


def weather() -> dict[str, Any]:
    from app.config import get_settings
    from app.location.providers import (
        build_ip_coarse_location_provider,
        build_windows_location_provider,
    )
    from app.location.service import LocationService
    from app.weather.providers import build_weather_provider
    from app.weather.service import WeatherService

    settings = get_settings()
    location_service = LocationService(
        windows_provider=build_windows_location_provider(settings),
        ip_provider=build_ip_coarse_location_provider(settings),
    )
    service = WeatherService(
        location_service=location_service, provider=build_weather_provider(settings)
    )
    result: dict[str, Any] = {"route": _route("Hava nasıl?")}
    with _factory()() as db:
        resolution = location_service.resolve(db, requested_place=None)
        result["resolution"] = {
            "resolved": bool(resolution.resolved),
            "reason": resolution.reason,
            "city": getattr(resolution.context, "city", None),
            "source": getattr(resolution.context, "source", None),
            "confidence": getattr(resolution.context, "confidence", None),
        }
        answer = service.current(db, requested_place=None, session_id=None)
    server = (answer.get("observed_after") or {}).get("server") or {}
    result["answer"] = {
        "execution": answer.get("execution_status"),
        "terminal": answer.get("terminal_status"),
        "speech": answer.get("speech"),
        "location_source": (server.get("location") or {}).get("source"),
        "provider": (server.get("observation") or {}).get("provider"),
        "observed_at": (server.get("observation") or {}).get("observed_at"),
    }
    return result


# -------------------------------------------------------------------- the news


def news(open_browser: bool) -> dict[str, Any]:
    from app.news import resolve_service, sources_service

    result: dict[str, Any] = {"route": _route("Haberleri aç.")}
    with _factory()() as db:
        source = sources_service.default_source(db)
        result["source"] = (
            None
            if source is None
            else {
                "news_source_id": source.news_source_id,
                "display_name": source.display_name,
                "channel_id": source.channel_id,
                "identity_status": source.identity_status,
                "content_type": source.content_type,
            }
        )
        if source is None or source.channel_id is None:
            result["error"] = "no source with a resolved identity"
            return result
        outcome = resolve_service.resolve_for_source(db, source)
    res = outcome.result
    selected = res.selected
    result["resolved"] = {
        "reason": res.reason,
        "answered_by": res.answered_by,
        "considered": res.considered,
        "rejected": [{"video": v, "why": w} for v, w in res.rejected],
        "selected": None
        if selected is None
        else {
            "video_id": selected.video_id,
            "title": selected.title,
            "published_at": selected.published_at.isoformat(),
        },
    }
    result["opened_browser"] = False
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--open", action="store_true", help="actually open the news video"
    )
    args = parser.parse_args()

    for name, fn in (
        ("wake_alarm", wake_alarm),
        ("weather", weather),
        ("news", lambda: news(args.open)),
    ):
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001 - a failure is a RESULT, reported not raised
            out[name] = {
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-1000:],
            }

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
