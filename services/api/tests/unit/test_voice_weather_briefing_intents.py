"""Owner Location Context / Live Weather / Morning Briefing voice intents
(docs/DECISIONS.md ADR-0090, task brief §4): every utterance the brief names, its ASR-
shaped (diacritic-free) variants, the QUERY_TOOL_BY_INTENT wiring, and the negative
assertions that prove the routing is deterministic and distinct — weather vs system
status vs the combined briefing never collide (task brief §5).
"""

from __future__ import annotations

from app.voice.intents import (
    CAPABILITY_BY_INTENT,
    KLASS_ACTION,
    KLASS_QUERY,
    QUERY_TOOL_BY_INTENT,
    Intent,
    resolve_intent,
)

# --------------------------------------------------------------------------- weather


def test_bare_hava_nasil_is_a_weather_query_with_no_explicit_place() -> None:
    resolved = resolve_intent("Hava nasıl?")
    assert resolved.intent == Intent.WEATHER_QUERY
    assert resolved.weather_place is None
    assert resolved.klass == KLASS_QUERY
    assert QUERY_TOOL_BY_INTENT[Intent.WEATHER_QUERY] == "weather.current"


def test_explicit_istanbulda_hava_nasil_carries_the_place() -> None:
    resolved = resolve_intent("İstanbul'da hava nasıl?")
    assert resolved.intent == Intent.WEATHER_QUERY
    assert resolved.weather_place == "İstanbul"


def test_asr_shaped_istanbulda_hava_nasil_without_diacritics() -> None:
    resolved = resolve_intent("istanbulda hava nasil")
    assert resolved.intent == Intent.WEATHER_QUERY
    assert resolved.weather_place == "İstanbul"


def test_burda_hava_nasil_resolves_to_current_location_not_a_named_place() -> None:
    resolved = resolve_intent("burda hava nasıl")
    assert resolved.intent == Intent.WEATHER_QUERY
    assert resolved.weather_place is None


def test_bulundugum_yerde_yagmur_var_mi_is_a_weather_query() -> None:
    resolved = resolve_intent("bulundugum yerde yagmur var mi")
    assert resolved.intent == Intent.WEATHER_QUERY
    assert resolved.weather_place is None


def test_ankarada_yarin_yagmur_var_mi_carries_ankara() -> None:
    resolved = resolve_intent("ankarada yarin hava nasil")
    assert resolved.intent == Intent.WEATHER_QUERY
    assert resolved.weather_place == "Ankara"


def test_su_an_bulundugum_yerde_kac_derece() -> None:
    resolved = resolve_intent("Şu an bulunduğum yerde kaç derece?")
    assert resolved.intent == Intent.WEATHER_QUERY
    assert resolved.weather_place is None


def test_a_bare_weather_statement_is_never_read_as_a_query() -> None:
    """ "Bugün hava güzel." is a STATEMENT, not a question — this module already
    documents this exact sentence as the non-briefing case (_INTENT_CORROBORATION); the
    same discipline must hold for the new WEATHER_QUERY vocabulary."""
    resolved = resolve_intent("Bugün hava güzel.")
    assert resolved.intent != Intent.WEATHER_QUERY


# ---------------------------------------------------------------- location: default


def test_setting_the_default_weather_location_is_an_action() -> None:
    resolved = resolve_intent("Varsayılan hava durumu konumumu İstanbul yap.")
    assert resolved.intent == Intent.LOCATION_DEFAULT_SET
    assert resolved.klass == KLASS_ACTION
    assert resolved.location_default_city == "İstanbul"
    assert CAPABILITY_BY_INTENT[Intent.LOCATION_DEFAULT_SET] == "location.set_default"


def test_querying_the_default_location_is_a_query_not_a_set() -> None:
    resolved = resolve_intent("Varsayılan konumum ne?")
    assert resolved.intent == Intent.LOCATION_DEFAULT_QUERY
    assert resolved.klass == KLASS_QUERY
    assert QUERY_TOOL_BY_INTENT[Intent.LOCATION_DEFAULT_QUERY] == "location.get_default"


# ----------------------------------------------------------------- location: source


def test_where_do_you_know_my_location_from() -> None:
    resolved = resolve_intent("Şu an konumumu nereden biliyorsun?")
    assert resolved.intent == Intent.LOCATION_SOURCE_QUERY


def test_which_location_are_you_using() -> None:
    resolved = resolve_intent("Hangi konumu kullanıyorsun?")
    assert resolved.intent == Intent.LOCATION_SOURCE_QUERY


def test_is_my_location_current() -> None:
    resolved = resolve_intent("Konumum güncel mi?")
    assert resolved.intent == Intent.LOCATION_SOURCE_QUERY
    assert QUERY_TOOL_BY_INTENT[Intent.LOCATION_SOURCE_QUERY] == "weather.last_evidence"


# ------------------------------------------------------------------------ briefing


def test_gunaydin_is_the_morning_briefing() -> None:
    resolved = resolve_intent("Günaydın.")
    assert resolved.intent == Intent.MORNING_BRIEFING
    assert QUERY_TOOL_BY_INTENT[Intent.MORNING_BRIEFING] == "briefing.morning"


def test_sabah_ozetimi_ver_is_the_morning_briefing() -> None:
    resolved = resolve_intent("Sabah özetimi ver.")
    assert resolved.intent == Intent.MORNING_BRIEFING


def test_bugun_beni_neler_bekliyor_is_the_morning_briefing() -> None:
    resolved = resolve_intent("Bugün beni neler bekliyor?")
    assert resolved.intent == Intent.MORNING_BRIEFING


def test_sabah_durumunu_anlat_is_the_morning_briefing() -> None:
    resolved = resolve_intent("Sabah durumunu anlat.")
    assert resolved.intent == Intent.MORNING_BRIEFING


def test_sistem_durumu_nasil_is_the_narrow_system_status_query_not_the_briefing() -> None:
    resolved = resolve_intent("Sistem durumu nasıl?")
    assert resolved.intent == Intent.SYSTEM_STATUS_QUERY
    assert resolved.intent != Intent.MORNING_BRIEFING
    assert QUERY_TOOL_BY_INTENT[Intent.SYSTEM_STATUS_QUERY] == "briefing.system_status"


def test_gece_neler_yaptin_is_the_narrow_overnight_query_not_the_briefing() -> None:
    resolved = resolve_intent("Gece neler yaptın?")
    assert resolved.intent == Intent.OVERNIGHT_WORK_QUERY
    assert resolved.intent != Intent.MORNING_BRIEFING
    assert QUERY_TOOL_BY_INTENT[Intent.OVERNIGHT_WORK_QUERY] == "briefing.overnight_work"


# ------------------------------------------------------------- negative assertions


def test_negative_a_plain_durum_question_is_not_the_system_status_query() -> None:
    """ "sistem" is the disambiguator (module comment on ``_system_status_query_match``):
    a bare "durumu anlat" without it must never become SYSTEM_STATUS_QUERY."""
    resolved = resolve_intent("Durumu anlat.")
    assert resolved.intent != Intent.SYSTEM_STATUS_QUERY


def test_negative_gece_alone_is_not_the_overnight_query() -> None:
    resolved = resolve_intent("İyi geceler.")
    assert resolved.intent != Intent.OVERNIGHT_WORK_QUERY


def test_negative_konum_without_a_question_word_is_nothing_of_this_family() -> None:
    resolved = resolve_intent("Konumum İstanbul.")
    assert resolved.intent not in (
        Intent.LOCATION_DEFAULT_SET,
        Intent.LOCATION_DEFAULT_QUERY,
        Intent.LOCATION_SOURCE_QUERY,
    )


def test_negative_derece_without_a_question_word_is_not_a_weather_query() -> None:
    resolved = resolve_intent("On derece soğudu.")
    assert resolved.intent != Intent.WEATHER_QUERY


def test_negative_weather_and_briefing_never_collide() -> None:
    """A weather question about a place must never be read as a morning briefing, and
    vice versa (task brief §5's "deterministic and distinct")."""
    assert resolve_intent("Hava nasıl?").intent == Intent.WEATHER_QUERY
    assert resolve_intent("Günaydın.").intent == Intent.MORNING_BRIEFING
    assert resolve_intent("Hava nasıl?").intent != Intent.MORNING_BRIEFING
    assert resolve_intent("Günaydın.").intent != Intent.WEATHER_QUERY
