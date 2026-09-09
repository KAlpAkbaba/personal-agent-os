"""Weather and location voice tools (docs/DECISIONS.md ADR-0090). Four tools, registered
from ``tools.default_registry()`` by ONE added line (:func:`register_weather_tools`), the
same discipline every other family establishes (module docstring of
``app.voice.realtime_sessions.tools_calendar``).

The place, if any, comes from the ROUTER's own extraction (``ctx.context["last_utterance"]
["weather_place"]``/``["location_default_city"]`` — ``app.voice.intents._extract_place``),
never re-parsed here: "owner's words win over the model's argument", the same rule every
other family in this module already follows for its own fields.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.location.service import LocationService
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import turkish_casefold
from app.weather.service import WeatherService

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_WEATHER_CURRENT: Final = "weather.current"
TOOL_WEATHER_LAST_EVIDENCE: Final = "weather.last_evidence"
TOOL_LOCATION_GET_DEFAULT: Final = "location.get_default"
TOOL_LOCATION_SET_DEFAULT: Final = "location.set_default"

WEATHER_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_WEATHER_CURRENT,
    TOOL_WEATHER_LAST_EVIDENCE,
    TOOL_LOCATION_GET_DEFAULT,
    TOOL_LOCATION_SET_DEFAULT,
)

SPEECH_NO_DEFAULT = "Ayarlı bir varsayılan hava durumu konumunuz yok efendim."
SPEECH_DEFAULT_NEEDS_A_CITY = "Hangi şehri varsayılan yapmamı istersiniz efendim? Söyler misiniz?"


def _weather_service(ctx: ToolContext, tool: str) -> WeatherService:
    service = ctx.live.get("weather_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the weather service"
        )
    return service


def _location_service(ctx: ToolContext, tool: str) -> LocationService:
    service = ctx.live.get("location_service")
    if service is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the location service"
        )
    return service


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    return dict(ctx.context.get("last_utterance") or {})


def _corroborated_place(turn: dict[str, Any], argument: Any) -> str | None:
    """A place the MODEL proposed, accepted only if the owner's own words carry it.

    "Owner's words win over the model's argument" is this family's stated rule, and the
    router's ``_extract_place`` implements the strong half of it: a known city in the
    utterance is canonicalised and used. But its gazetteer is a closed list, so for every
    place outside it the tool used to fall straight through to ``arguments["city"]`` /
    ``arguments["place"]`` - free text the model generated, never checked against what
    was actually said. A review found this live: "Varsayilan hava durumu konumumu Paris
    yap." extracted nothing and the durable default became whatever the model typed. On a
    single-owner system whose model routinely reads documents, mail and web pages, that is
    a state mutation an injected instruction could aim.

    So the argument is corroborated instead of trusted: it is accepted only when it
    appears in the owner's own transcript for this turn (Turkish-casefolded, and matched
    against a suffix-stripped form of each spoken token so "Paris'te"/"Adiyaman'i" carry
    "Paris"/"Adiyaman"). An uncorroborated argument is not a place - the caller asks the
    owner rather than acting on it.
    """
    if not argument:
        return None
    proposed = str(argument).strip()
    if not proposed:
        return None
    said = str(turn.get("turn") or "")
    if not said:
        # No transcript on this turn (a REST/companion caller, or a session that never
        # recorded one): the model's word cannot be corroborated, so it is not used.
        return None
    needle = turkish_casefold(proposed).strip("'’")
    if not needle:
        return None
    for raw in said.replace("'", " ").replace("’", " ").split():
        token = turkish_casefold(raw).strip('.,!?;:()"')
        if token == needle or token.startswith(needle):
            return proposed
    return None


# --------------------------------------------------------------------------- weather


def weather_current(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hava nasıl?" / "İstanbul'da hava nasıl?" / "Kaç derece?" (task brief §4). The
    place named in the utterance (``weather_place``, the router's own extraction) is
    preferred over the model's own ``place`` argument, the same "owner's words win"
    rule every other family follows; with neither, ``app.location.service.
    LocationService.resolve`` decides the place through its full order."""
    db = _require_db(ctx, TOOL_WEATHER_CURRENT)
    service = _weather_service(ctx, TOOL_WEATHER_CURRENT)
    turn = _turn_record(ctx)
    place = turn.get("weather_place") or _corroborated_place(turn, arguments.get("place"))
    return service.current(
        db,
        requested_place=str(place) if place else None,
        device_id=ctx.device_id,
        session_id=str(ctx.session_id),
    )


def weather_last_evidence(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hangi konumun havasını söyledin?" / "Konumum güncel mi?" / "Hangi konumu
    kullanıyorsun?" (task brief §2, §4) — reads the record of the LAST answer, never the
    model's memory of what it said."""
    del arguments
    db = _require_db(ctx, TOOL_WEATHER_LAST_EVIDENCE)
    service = _weather_service(ctx, TOOL_WEATHER_LAST_EVIDENCE)
    return service.last_evidence(db, session_id=str(ctx.session_id))


# -------------------------------------------------------------------------- location


def location_get_default(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Varsayılan konumum ne?" (task brief §4)."""
    del arguments
    db = _require_db(ctx, TOOL_LOCATION_GET_DEFAULT)
    service = _location_service(ctx, TOOL_LOCATION_GET_DEFAULT)
    row = service.get_default(db)
    if row is None:
        return {
            "status": "no_default",
            "speech": SPEECH_NO_DEFAULT,
        }
    return {
        "status": "ok",
        "city": row.city,
        "region": row.region,
        "country": row.country,
        "speech": f"Varsayılan hava durumu konumunuz {row.city} efendim.",
    }


def location_set_default(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Varsayılan hava durumu konumumu İstanbul yap." (task brief §4) — a durable,
    LOCAL setting; never invented (task brief §1: "Do NOT invent one" applies just as
    much to the tool as to the resolver — a bare "Varsayılan konumu ayarla." with no
    city named asks the owner which, rather than guessing)."""
    db = _require_db(ctx, TOOL_LOCATION_SET_DEFAULT)
    service = _location_service(ctx, TOOL_LOCATION_SET_DEFAULT)
    turn = _turn_record(ctx)
    city = turn.get("location_default_city") or _corroborated_place(turn, arguments.get("city"))
    if not city:
        return {"status": "needs_clarification", "speech": SPEECH_DEFAULT_NEEDS_A_CITY}
    row = service.set_default(db, city=str(city))
    return {
        "status": "ok",
        "city": row.city,
        "speech": f"Varsayılan hava durumu konumunuzu {row.city} yaptım efendim.",
    }


# ------------------------------------------------------------------------ registration


def register_weather_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all four tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_WEATHER_CURRENT,
            description=(
                "Güncel hava durumunu bildirir: 'Hava nasıl?', 'İstanbul'da hava nasıl?', "
                "'Kaç derece?'. Sahip bir yer adı söylediyse 'place' alanına aynen ver "
                "('İstanbul', 'Ankara' ...); söylemediyse 'place' alanını boş bırak - "
                "konum otomatik çözülür. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"place": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=weather_current,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_WEATHER_LAST_EVIDENCE,
            description=(
                "Son verdiğin hava durumu cevabının hangi konum için ve nereden geldiğini "
                "anlatır: 'Hangi konumun havasını söyledin?', 'Konumum güncel mi?', "
                "'Hangi konumu kullanıyorsun?'. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=weather_last_evidence,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_LOCATION_GET_DEFAULT,
            description=(
                "Varsayılan hava durumu konumunu okur: 'Varsayılan konumum ne?'. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=location_get_default,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_LOCATION_SET_DEFAULT,
            description=(
                "Varsayılan hava durumu konumunu ayarlar: 'Varsayılan hava durumu "
                "konumumu İstanbul yap.'. Sahibin söylediği şehri 'city' alanına aynen "
                "ver. Sahip bir şehir söylemediyse bu aracı ÇAĞIRMA, hangi şehri "
                "istediğini sor. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=location_set_default,
        )
    )
    return reg


__all__ = [
    "TOOL_LOCATION_GET_DEFAULT",
    "TOOL_LOCATION_SET_DEFAULT",
    "TOOL_WEATHER_CURRENT",
    "TOOL_WEATHER_LAST_EVIDENCE",
    "WEATHER_TOOL_NAMES",
    "location_get_default",
    "location_set_default",
    "register_weather_tools",
    "weather_current",
    "weather_last_evidence",
]
