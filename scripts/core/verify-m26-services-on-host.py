"""M26 additive tracks, driven inside the PRODUCTION api container.

Weather, location and the briefing deliberately have no REST routes (ADR-0091 decision 9):
they are voice-tool surfaces, and the branch that built them could not touch `apps/web`'s
contract file. That leaves exactly one honest way to prove them on production - run the
REAL services against the deployed tree, in the deployed container, with the deployed
configuration - which is what this does. A gate row that says PROVEN_REAL has to name
something that ran, not something a unit test showed could run.

Read-mostly. `WeatherService.current` writes one `weather_query_evidence` row (that IS the
capability: an answer the owner can ask about afterwards) and one ledger event; nothing
else is written, nothing is deleted, and no device action is dispatched. The weather call
reaches Open-Meteo, which is the point - "live weather" means a real provider answered.

Run it with (one line, from the repo root):
    ssh root@pagentos-core "docker exec -i -w /srv/pagentos pagentos-prod-api-green
    /srv/pagentos/.venv/bin/python -" < scripts/core/verify-m26-services-on-host.py

The container's `/usr/local/bin/python` is NOT the app's interpreter - the workload runs
under `uv run` against `/srv/pagentos/.venv`, so the bare one has no sqlalchemy and no
pydantic. Use the venv's python and `-w /srv/pagentos`, or every import fails.
"""

from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime

out: dict[str, object] = {"checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z")}


def record(name: str, fn) -> None:
    try:
        out[name] = fn()
    except Exception as exc:  # noqa: BLE001 - a failure here is a RESULT, reported not raised
        out[name] = {
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-1200:],
        }


def _factory():
    from app.artifacts.runtime import build_artifact_context
    from app.config import get_settings

    factory, _store = build_artifact_context(get_settings())
    return factory


# ------------------------------------------------------------------ live weather


def weather() -> dict[str, object]:
    """Built the way `app.main` builds it - the same providers, from the same settings -
    so what this proves is the DEPLOYED wiring, not a construction only this script uses."""
    from app.config import get_settings
    from app.location.providers import (
        build_ip_coarse_location_provider,
        build_windows_location_provider,
    )
    from app.location.service import LocationService
    from app.weather.providers import build_weather_provider
    from app.weather.service import WeatherService

    settings = get_settings()
    service = WeatherService(
        location_service=LocationService(
            windows_provider=build_windows_location_provider(settings),
            ip_provider=build_ip_coarse_location_provider(settings),
        ),
        provider=build_weather_provider(settings),
    )
    with _factory()() as db:
        result = service.current(db, requested_place="İstanbul", session_id=None)
    # The M18 action contract nests it: a receipt's readings live under
    # `observed_after.server`, never at the top level.
    server = (result.get("observed_after") or {}).get("server") or {}
    observation = server.get("observation") or {}
    location = server.get("location") or {}
    return {
        "execution": result.get("execution_status"),
        "terminal": result.get("terminal_status"),
        "error_class": result.get("error_class"),
        "speech": result.get("speech"),
        "provider": observation.get("provider"),
        "observed_at": observation.get("observed_at"),
        "temperature_c": observation.get("temperature_c"),
        "location_source": location.get("source"),
        "resolution_reason": location.get("resolution_reason"),
        "evidence_id": result.get("evidence_id"),
    }


# ------------------------------------------------------------- location resolution


def location() -> dict[str, object]:
    from app.location.service import LocationService

    service = LocationService()
    with _factory()() as db:
        default = service.get_default(db)
        resolution = service.resolve(db, requested_place=None)
    return {
        "default_set": default is not None,
        "default_city": getattr(default, "city", None),
        "resolved": bool(resolution.resolved),
        "reason": resolution.reason,
        "source": getattr(resolution.context, "source", None),
        "confidence": getattr(resolution.context, "confidence", None),
        "speech": getattr(resolution, "speech", None),
    }


# --------------------------------------------------------------- morning briefing


def briefing() -> dict[str, object]:
    from app.briefing.service import BriefingService
    from app.config import get_settings

    with _factory()() as db:
        result = BriefingService().build(db, settings=get_settings(), live={}, session_id=None)
    speech = str(result.get("speech") or "")
    server = (result.get("observed_after") or {}).get("server") or {}
    return {
        "execution": result.get("execution_status"),
        "speech_len": len(speech),
        "speech_head": speech[:700],
        "sections": server.get("sections"),
    }


# --------------------------------------------------------------------- news mode


def news() -> dict[str, object]:
    from app.news import sources_service

    with _factory()() as db:
        views = sources_service.list_sources(db)
    return {
        "count": len(views),
        "sources": [
            {
                "id": v.news_source_id,
                "display_name": v.display_name,
                "identity_status": v.identity_status,
                "channel_id": v.channel_id,
                "content_type": v.content_type,
            }
            for v in views
        ],
    }


# ------------------------------------------------------------------- the executive


def executive() -> dict[str, object]:
    """The authority claim, read from the DEPLOYED code rather than from the gate's own
    prose: how many step kinds exist, and how many of them are high-risk (must be zero)."""
    from app.executive.graph import RISK_HIGH_RISK, STEP_KIND_PROFILES
    from app.executive.spec import MAX_ACTIVE_RUNS

    return {
        "step_kinds": len(STEP_KIND_PROFILES),
        "high_risk_kinds": sorted(
            k for k, v in STEP_KIND_PROFILES.items() if getattr(v, "risk_class", None) == RISK_HIGH_RISK
        ),
        "max_active_runs": MAX_ACTIVE_RUNS,
    }


for _name, _fn in (
    ("weather", weather),
    ("location", location),
    ("briefing", briefing),
    ("news", news),
    ("executive", executive),
):
    record(_name, _fn)

print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
