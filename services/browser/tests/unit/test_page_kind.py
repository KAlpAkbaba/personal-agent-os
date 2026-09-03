"""Unit tests for browser_agent.page_kind: page_kind classification."""

from __future__ import annotations

from browser_agent.page_kind import classify_page


def _classify(**overrides: object):
    defaults = dict(
        title="",
        heading_text="",
        body_text="Plenty of ordinary page content here, well past the empty threshold.",
        has_password_field=False,
        http_status=200,
    )
    defaults.update(overrides)
    return classify_page(**defaults)  # type: ignore[arg-type]


def test_ok_page() -> None:
    result = _classify()
    assert result.page_kind == "ok"
    assert result.site_error is None


def test_auth_wall_from_password_field() -> None:
    result = _classify(has_password_field=True)
    assert result.page_kind == "auth_wall"
    assert result.site_error is not None
    assert result.site_error.kind == "auth_wall"


def test_auth_wall_from_http_401() -> None:
    result = _classify(http_status=401)
    assert result.page_kind == "auth_wall"
    assert result.site_error.http_status == 401


def test_auth_wall_from_http_403() -> None:
    result = _classify(http_status=403)
    assert result.page_kind == "auth_wall"


def test_auth_wall_from_english_title_marker() -> None:
    result = _classify(title="Please sign in")
    assert result.page_kind == "auth_wall"


def test_auth_wall_from_turkish_heading_marker() -> None:
    result = _classify(heading_text="Lütfen oturum aç")
    assert result.page_kind == "auth_wall"


def test_auth_wall_from_turkish_giris_yap_marker() -> None:
    result = _classify(title="Giriş yap")
    assert result.page_kind == "auth_wall"


def test_captcha_from_recaptcha_marker() -> None:
    result = _classify(body_text="Please complete the reCAPTCHA below to continue.")
    assert result.page_kind == "captcha"
    assert result.site_error.kind == "captcha"


def test_captcha_from_hcaptcha_marker() -> None:
    result = _classify(body_text="hCaptcha verification required.")
    assert result.page_kind == "captcha"


def test_captcha_from_turnstile_marker() -> None:
    result = _classify(body_text="Cloudflare Turnstile challenge in progress.")
    assert result.page_kind == "captcha"


def test_captcha_from_verify_human_marker() -> None:
    result = _classify(title="Verify you are human")
    assert result.page_kind == "captcha"


def test_captcha_takes_priority_over_auth_wall() -> None:
    # A page with both a password field AND a captcha marker: captcha wins
    # (checked first per module docstring).
    result = _classify(has_password_field=True, body_text="Please complete the recaptcha")
    assert result.page_kind == "captcha"


def test_blocked_from_marker_text() -> None:
    result = _classify(body_text="Access denied. Unusual traffic detected from your network.")
    assert result.page_kind == "blocked"
    assert result.site_error.kind == "blocked"


def test_blocked_from_http_429() -> None:
    result = _classify(http_status=429)
    assert result.page_kind == "blocked"


def test_error_page_from_http_500() -> None:
    result = _classify(http_status=500)
    assert result.page_kind == "error_page"
    assert result.site_error.kind == "http_error"
    assert result.site_error.http_status == 500


def test_error_page_from_http_503() -> None:
    result = _classify(http_status=503)
    assert result.page_kind == "error_page"
    assert result.site_error.http_status == 503


def test_empty_page_short_body() -> None:
    result = _classify(body_text="   ", http_status=200)
    assert result.page_kind == "empty"
    assert result.site_error is None


def test_empty_not_triggered_by_error_status() -> None:
    # A short/blank body with an error status is still error_page, not empty
    # (error classification happens before the empty check).
    result = _classify(body_text="", http_status=500)
    assert result.page_kind == "error_page"


def test_none_http_status_does_not_crash_and_defaults_ok() -> None:
    result = _classify(http_status=None)
    assert result.page_kind == "ok"
