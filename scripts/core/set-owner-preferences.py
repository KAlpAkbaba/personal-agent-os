"""Persist the owner's three named preferences through the CANONICAL models.

The owner gave three values (2026-09-09) and asked that they be stored through the models
that already exist rather than hardcoded into feature code, and that the YouTube identities
be validated before anything is written:

* wake music   -> `app.alarms.service.set_wake_song` (the ambient policy row's `wake_song`)
* weather home -> `app.location.service.LocationService.set_default` (a `location_context`
                  row with `is_default`, `source=owner_default`)
* news source  -> `app.news.sources_service` (a `news_sources` row whose `channel_id` was
                  RESOLVED, never guessed from the display name)

Nothing here is a constant in feature code: every value arrives as an argument and lands in
a database row that the ordinary read paths already consult.

Idempotent: run it twice and the second run updates rather than duplicating.

Run it inside the production api container:
    ssh root@pagentos-core "docker exec -i -w /srv/pagentos <container>
    /srv/pagentos/.venv/bin/python - --wake-url ... --city ... --news-url ..."
    < scripts/core/set-owner-preferences.py
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any

out: dict[str, Any] = {}


def _factory():
    from app.artifacts.runtime import build_artifact_context
    from app.config import get_settings

    factory, _store = build_artifact_context(get_settings())
    return factory


def set_wake_song(url: str, title: str | None) -> dict[str, Any]:
    """The owner's approved wake song. `alarms.service._media_source` already prefers it
    over the tone for any alarm that names no media of its own, which is the defect the
    owner reported as "the alarm beeps"."""
    from app.alarms import service as alarms_service

    with _factory()() as db:
        saved = alarms_service.set_wake_song(db, url=url, title=title)
        read_back = alarms_service.get_wake_song(db)
    return {"saved": saved, "read_back": read_back}


def set_default_location(city: str, country: str | None) -> dict[str, Any]:
    """The owner's default weather location — tier 3 of `LocationService.resolve`, below a
    fresh trusted device fix and above everything else, exactly as ADR-0091 specifies."""
    from app.location.service import LocationService

    service = LocationService()
    with _factory()() as db:
        row = service.set_default(db, city=city, country=country)
        resolution = service.resolve(db, requested_place=None)
    return {
        "city": row.city,
        "country": row.country,
        "source": row.source,
        "is_default": bool(row.is_default),
        "resolves_to": {
            "resolved": bool(resolution.resolved),
            "reason": resolution.reason,
            "city": getattr(resolution.context, "city", None),
            "source": getattr(resolution.context, "source", None),
            "confidence": getattr(resolution.context, "confidence", None),
        },
    }


def set_news_source(source_id: str, display_name: str, channel_input: str) -> dict[str, Any]:
    """The news source, with its identity RESOLVED from the channel page's own canonical
    link. A display name never binds a channel here - that refusal is the point of the
    module - so this passes the real fetcher and reports what it resolved."""
    from app.news import sources_service
    from app.news.models import CONTENT_TYPE_MAIN_NEWS
    from app.news.page_fetch import fetch_channel_page

    with _factory()() as db:
        existing = sources_service.get_source(db, source_id)
        if existing is None:
            view = sources_service.create_source(
                db,
                news_source_id=source_id,
                display_name=display_name,
                channel_input=channel_input,
                content_type=CONTENT_TYPE_MAIN_NEWS,
                fetch_page=fetch_channel_page,
            )
        else:
            view = sources_service.update_source(
                db,
                source_id,
                display_name=display_name,
                channel_input=channel_input,
                content_type=CONTENT_TYPE_MAIN_NEWS,
                fetch_page=fetch_channel_page,
            )
    return {
        "news_source_id": view.news_source_id,
        "display_name": view.display_name,
        "channel_id": view.channel_id,
        "identity_status": view.identity_status,
        "identity_resolved_by": view.identity_resolved_by,
        "content_type": view.content_type,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wake-url", required=True)
    parser.add_argument("--wake-title", default="")
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", default="")
    parser.add_argument("--news-url", required=True)
    parser.add_argument("--news-id", default="show-ana-haber")
    parser.add_argument("--news-name", default="Show Ana Haber")
    args = parser.parse_args()

    for name, fn in (
        ("wake_song", lambda: set_wake_song(args.wake_url, args.wake_title or None)),
        ("location", lambda: set_default_location(args.city, args.country or None)),
        ("news_source", lambda: set_news_source(args.news_id, args.news_name, args.news_url)),
    ):
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001 - a failure is a RESULT here, reported
            out[name] = {
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-900:],
            }

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if all("error" not in v for v in out.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
