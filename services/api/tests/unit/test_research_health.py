"""app.research.health.research_health: synthesis posture, no I/O, no secrets."""

from app.config import Settings
from app.research.health import research_health


def test_no_keys_reports_deterministic_effective_provider() -> None:
    settings = Settings(
        _env_file=None, openai_api_key="", voice_openai_api_key="", anthropic_api_key="",
    )
    result = research_health(settings)
    assert result["status"] == "ok"
    assert result["effective_synthesis"] == "deterministic"
    assert result["providers_configured"] == {
        "anthropic": False, "openai": False, "deterministic": True,
    }


def test_openai_key_present_reports_configured_and_effective() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-x", anthropic_api_key="")
    result = research_health(settings)
    assert result["providers_configured"]["openai"] is True
    assert result["effective_synthesis"] == "openai"


def test_explicit_default_synthesis_is_not_overridden_by_auto_resolution() -> None:
    settings = Settings(
        _env_file=None, research_default_synthesis="deterministic",
        openai_api_key="sk-x", anthropic_api_key="sk-y",
    )
    result = research_health(settings)
    assert result["effective_synthesis"] == "deterministic"


def test_result_never_contains_the_raw_key_value() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-super-secret-value")
    result = research_health(settings)
    assert "sk-super-secret-value" not in str(result)
