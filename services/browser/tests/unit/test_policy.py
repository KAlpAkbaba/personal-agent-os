"""Unit tests for browser_agent.policy: risk classes, click classification, enforcement."""

from __future__ import annotations

import pytest

from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.policy import (
    CAPABILITIES,
    CAPABILITY_RISK_CLASS,
    RESEARCH_SESSION_CLASSES,
    ResolvedElement,
    RiskClass,
    classify_click,
    enforce,
    narrow_reopen,
    parse_risk_classes,
)


def test_every_capability_except_click_has_a_static_risk_class() -> None:
    for capability in CAPABILITIES:
        if capability == "browser.click":
            continue
        assert capability in CAPABILITY_RISK_CLASS


def test_research_session_classes_are_read_and_navigate_only() -> None:
    assert RESEARCH_SESSION_CLASSES == frozenset({RiskClass.READ, RiskClass.NAVIGATE})


class TestClassifyClick:
    def test_plain_link_is_navigate(self) -> None:
        element = ResolvedElement(tag="a", role="link", name="Second page", has_href=True)
        assert classify_click(element) == RiskClass.NAVIGATE

    def test_link_with_onclick_is_not_navigate(self) -> None:
        element = ResolvedElement(
            tag="a", role="link", name="Trigger", has_href=True, has_onclick=True
        )
        assert classify_click(element) == RiskClass.REVERSIBLE_WRITE

    def test_submit_button_is_external_communication(self) -> None:
        element = ResolvedElement(tag="button", role="button", name="Submit form", is_submit=True)
        assert classify_click(element) == RiskClass.EXTERNAL_COMMUNICATION

    def test_plain_button_is_reversible_write(self) -> None:
        element = ResolvedElement(tag="button", role="button", name="Greet")
        assert classify_click(element) == RiskClass.REVERSIBLE_WRITE

    @pytest.mark.parametrize(
        "name",
        [
            "Buy now",
            "Purchase",
            "Pay with card",
            "Delete account",
            "Remove item",
            "Send message",
            "Submit order",
            "Satın al",
            "satin al",
            "Öde",
            "Sil",
            "Gönder",
        ],
    )
    def test_high_impact_name_markers(self, name: str) -> None:
        element = ResolvedElement(tag="button", role="button", name=name)
        assert classify_click(element) == RiskClass.HIGH_IMPACT

    def test_high_impact_name_wins_over_submit_control(self) -> None:
        # A "Delete" button that is also a submit control is still HIGH_IMPACT,
        # not merely EXTERNAL_COMMUNICATION (contract §4 priority order).
        element = ResolvedElement(tag="button", role="button", name="Delete", is_submit=True)
        assert classify_click(element) == RiskClass.HIGH_IMPACT

    def test_high_impact_name_wins_over_plain_link(self) -> None:
        element = ResolvedElement(tag="a", role="link", name="Buy now", has_href=True)
        assert classify_click(element) == RiskClass.HIGH_IMPACT


class TestEnforce:
    def test_allowed_class_does_not_raise(self) -> None:
        enforce(frozenset({RiskClass.READ}), RiskClass.READ, capability="browser.extract")

    def test_disallowed_class_raises_security_scope_error(self) -> None:
        with pytest.raises(BrowserError) as exc_info:
            enforce(
                frozenset({RiskClass.READ}), RiskClass.HIGH_IMPACT, capability="browser.download"
            )
        err = exc_info.value
        assert err.error_class == ErrorClass.SECURITY_SCOPE_ERROR
        assert err.retryable is False
        assert "browser.download" in err.message
        assert "HIGH_IMPACT" in err.message

    def test_research_session_refuses_reversible_write(self) -> None:
        with pytest.raises(BrowserError) as exc_info:
            enforce(RESEARCH_SESSION_CLASSES, RiskClass.REVERSIBLE_WRITE, capability="browser.fill")
        assert exc_info.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR


class TestNarrowReopen:
    def test_no_requested_classes_keeps_existing(self) -> None:
        existing = frozenset({RiskClass.READ, RiskClass.NAVIGATE})
        assert narrow_reopen(existing, None) == existing

    def test_narrower_request_is_applied(self) -> None:
        existing = frozenset({RiskClass.READ, RiskClass.NAVIGATE})
        requested = frozenset({RiskClass.READ})
        assert narrow_reopen(existing, requested) == frozenset({RiskClass.READ})

    def test_broader_request_never_widens(self) -> None:
        existing = frozenset({RiskClass.READ})
        requested = frozenset({RiskClass.READ, RiskClass.HIGH_IMPACT})
        assert narrow_reopen(existing, requested) == frozenset({RiskClass.READ})

    def test_disjoint_request_yields_empty_set(self) -> None:
        existing = frozenset({RiskClass.READ})
        requested = frozenset({RiskClass.HIGH_IMPACT})
        assert narrow_reopen(existing, requested) == frozenset()


class TestParseRiskClasses:
    def test_parses_known_values(self) -> None:
        result = parse_risk_classes(["READ", "NAVIGATE"])
        assert result == frozenset({RiskClass.READ, RiskClass.NAVIGATE})

    def test_empty_list_is_validation_error(self) -> None:
        with pytest.raises(BrowserError) as exc_info:
            parse_risk_classes([])
        assert exc_info.value.error_class == ErrorClass.VALIDATION_ERROR

    def test_not_a_list_is_validation_error(self) -> None:
        with pytest.raises(BrowserError):
            parse_risk_classes("READ")

    def test_unknown_value_is_validation_error(self) -> None:
        with pytest.raises(BrowserError) as exc_info:
            parse_risk_classes(["READ", "SUPERUSER"])
        assert exc_info.value.error_class == ErrorClass.VALIDATION_ERROR
