"""Unit tests for the Playwright -> taxonomy mapping (no browser needed)."""

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from browser_agent import BrowserError, ErrorClass, Phase, map_playwright_error


def test_timeout_in_resolve_is_ui_target_not_found() -> None:
    err = map_playwright_error(
        PlaywrightTimeoutError("Timeout 500ms exceeded waiting for locator"),
        phase=Phase.RESOLVE,
        op="click",
    )
    assert err.error_class is ErrorClass.UI_TARGET_NOT_FOUND
    assert err.retryable is False


def test_timeout_in_act_with_intercept_marker_is_ui_state_changed() -> None:
    err = map_playwright_error(
        PlaywrightTimeoutError(
            'Timeout 500ms exceeded.\n<div id="overlay"></div> intercepts pointer events'
        ),
        phase=Phase.ACT,
        op="click",
    )
    assert err.error_class is ErrorClass.UI_STATE_CHANGED
    assert err.retryable is True


def test_timeout_in_act_with_detached_marker_is_ui_state_changed() -> None:
    err = map_playwright_error(
        PlaywrightTimeoutError("Timeout 500ms exceeded. element was detached from the DOM"),
        phase=Phase.ACT,
        op="fill",
    )
    assert err.error_class is ErrorClass.UI_STATE_CHANGED
    assert err.retryable is True


def test_timeout_without_marker_is_plain_timeout() -> None:
    for phase in (Phase.ACT, Phase.NAVIGATE, Phase.OTHER):
        err = map_playwright_error(
            PlaywrightTimeoutError("Timeout 5000ms exceeded"), phase=phase, op="navigate"
        )
        assert err.error_class is ErrorClass.TIMEOUT
        assert err.retryable is True


def test_connect_phase_error_is_dependency_unavailable() -> None:
    err = map_playwright_error(
        PlaywrightError("browserType.connectOverCDP: something went wrong"),
        phase=Phase.CONNECT,
        op="connect_existing_cdp",
    )
    assert err.error_class is ErrorClass.DEPENDENCY_UNAVAILABLE
    assert err.retryable is True


def test_connection_marker_is_dependency_unavailable_in_any_phase() -> None:
    for message in (
        "connect ECONNREFUSED 127.0.0.1:9222",
        "net::ERR_CONNECTION_REFUSED at http://127.0.0.1:1/",
        "Target page, context or browser has been closed",
    ):
        err = map_playwright_error(PlaywrightError(message), phase=Phase.NAVIGATE, op="navigate")
        assert err.error_class is ErrorClass.DEPENDENCY_UNAVAILABLE, message
        assert err.retryable is True


def test_dom_race_error_in_act_is_ui_state_changed() -> None:
    for message in (
        "Element is not attached to the DOM",
        "Execution context was destroyed, most likely because of a navigation",
        "Frame was detached",
    ):
        err = map_playwright_error(PlaywrightError(message), phase=Phase.ACT, op="click")
        assert err.error_class is ErrorClass.UI_STATE_CHANGED, message
        assert err.retryable is True


def test_unknown_playwright_error_is_internal_bug() -> None:
    err = map_playwright_error(
        PlaywrightError("something completely unexpected"), phase=Phase.ACT, op="click"
    )
    assert err.error_class is ErrorClass.INTERNAL_BUG
    assert err.retryable is False


def test_non_playwright_error_is_internal_bug() -> None:
    err = map_playwright_error(ValueError("boom"), phase=Phase.ACT, op="click")
    assert err.error_class is ErrorClass.INTERNAL_BUG
    assert err.retryable is False


def test_evidence_carries_op_phase_and_playwright_details() -> None:
    err = map_playwright_error(
        PlaywrightTimeoutError("Timeout 500ms exceeded"),
        phase=Phase.RESOLVE,
        op="find",
        evidence={"target": {"role": "button", "name": "Greet"}},
    )
    assert err.evidence["op"] == "find"
    assert err.evidence["phase"] == "resolve"
    assert err.evidence["target"] == {"role": "button", "name": "Greet"}
    assert err.evidence["playwright_error"] == "TimeoutError"
    assert "Timeout" in err.evidence["playwright_message"]


def test_browser_error_is_an_exception_with_message() -> None:
    err = BrowserError(ErrorClass.TIMEOUT, "slow", retryable=True)
    with pytest.raises(BrowserError):
        raise err
    assert "slow" in str(err)


def test_navigable_url_scheme_allowlist():
    import pytest as _pytest

    from browser_agent.errors import BrowserError, require_navigable_url

    require_navigable_url("http://127.0.0.1:8080/x", op="navigate")
    require_navigable_url("https://example.test/", op="navigate")
    require_navigable_url("about:blank", op="navigate")
    bad_urls = (
        "file:///C:/Windows/win.ini",
        "javascript:alert(1)",
        "data:text/html,x",
        "ftp://host/x",
        "no-scheme",
    )
    for bad in bad_urls:
        with _pytest.raises(BrowserError) as exc_info:
            require_navigable_url(bad, op="navigate")
        assert str(exc_info.value.error_class) == "validation_error"


def test_redact_url_strips_query_and_fragment():
    from browser_agent.errors import redact_url

    assert redact_url("https://h.example/cb?code=SECRET&state=x#frag") == "https://h.example/cb"
    assert redact_url("http://127.0.0.1:8080/path") == "http://127.0.0.1:8080/path"
