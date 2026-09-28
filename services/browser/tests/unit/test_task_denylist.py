"""Contract v1.7 on the worker: the task deny-list and the risk ceiling (ADR-0207 PR-B).

The device's half of two rules the Cloud Core's gate also applies - and the half that
still holds when the other one is wrong:

* on a site of the deny-list the worker serves READ and navigation and refuses whatever
  changes the page or moves a file, for every session;
* a click carries the class it was gated at, and an element that is MORE than that is
  refused - the step was not what it looked like, and it is never performed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from browser_agent import policy, task_denylist, worker
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.policy import RiskClass, enforce_ceiling

REPO_ROOT = Path(__file__).resolve().parents[4]
DENYLIST_JSON = REPO_ROOT / "packages" / "protocol" / "browser-task-denylist.json"


def _shared() -> dict[str, Any]:
    # No skip when the file is missing (test_contract_falsification's rule).
    return json.loads(DENYLIST_JSON.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ one source


def test_the_workers_list_is_the_shared_file_verbatim() -> None:
    shared = _shared()["categories"]
    assert list(task_denylist.CATEGORIES) == list(shared)
    for name, body in shared.items():
        domains, parts = task_denylist.CATEGORIES[name]
        assert list(domains) == body["domains"], name
        assert list(parts) == body["host_label_parts"], name


def test_the_list_holds_what_the_owner_named() -> None:
    """ADR-0207 decision 5: banks, e-Devlet, payment providers, password managers, the
    employer's systems (turka.com and the company's panels), Kolay Monitor."""
    categories = _shared()["categories"]
    for name in (
        "bank",
        "government_identity",
        "payment_provider",
        "password_manager",
        "employer",
        "kolay_monitor",
    ):
        assert name in categories, name
        assert categories[name]["domains"] or categories[name]["host_label_parts"], name
    assert "turka.com" in categories["employer"]["domains"]
    assert "turkiye.gov.tr" in categories["government_identity"]["domains"]


def test_every_entry_is_lower_case_and_a_bare_host() -> None:
    for name, (domains, parts) in task_denylist.CATEGORIES.items():
        for domain in domains:
            assert domain == domain.lower().strip("."), (name, domain)
            assert "/" not in domain and ":" not in domain and " " not in domain, (name, domain)
            assert "." in domain, (name, domain)
        for part in parts:
            assert part == part.lower() and len(part) >= 4, (name, part)


# ------------------------------------------------------------------ matching


@pytest.mark.parametrize(
    ("url", "category"),
    [
        ("https://www.isbank.com.tr/", "bank"),
        ("https://sube.garantibbva.com.tr/giris", "bank"),
        ("https://AKBANK.com/", "bank"),
        ("https://internetbankaciligi.example.com.tr/", "bank"),
        ("https://www.foodbank.example.org/", "bank"),  # over-matched on purpose
        ("https://giris.turkiye.gov.tr/Giris/gir", "government_identity"),
        ("https://www.paypal.com/checkout", "payment_provider"),
        ("https://sandbox-api.iyzipay.com/", "payment_provider"),
        ("https://vault.bitwarden.com/", "password_manager"),
        ("https://passwords.google.com/", "password_manager"),
        ("https://mail.turka.com/owa", "employer"),
        ("https://panel.turka.com.tr/", "employer"),
        ("https://app.kolaymonitor.com/", "kolay_monitor"),
        ("https://kolay-monitor.example.net/", "kolay_monitor"),
        ("https://pagentos-core.example.ts.net:8001/", "own_system"),
    ],
)
def test_a_denied_site_is_recognised_from_its_address(url: str, category: str) -> None:
    assert task_denylist.denied(url) == category


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/search?q=isbank",
        "https://news.example.org/banka-haberleri",  # the word is in the PATH
        "https://example.org/?next=https://www.paypal.com/",
        "https://mail.google.com/mail/u/0/",
        "https://mail.proton.me/u/0/inbox",
        "https://www.youtube.com/",
        "https://www.trendyol.com/",
        "https://notpaypal.com.example.org/",
        "https://paypal.com.evil.example/",
        "",
        "about:blank",
        "file:///C:/Users/owner/bank.html",
        "javascript:alert(1)",
    ],
)
def test_a_site_that_only_mentions_one_is_not_one(url: str) -> None:
    assert task_denylist.denied(url) is None


def test_the_host_is_read_from_the_address_and_nothing_else() -> None:
    assert (
        task_denylist.host_of("https://user:pw@WWW.IsBank.com.tr.:443/x?y=1") == "www.isbank.com.tr"
    )
    assert task_denylist.host_of("not a url") == ""
    assert task_denylist.denied_host("") is None


# ------------------------------------------------------------------ the worker refuses


def _state(url: str) -> Any:
    page = SimpleNamespace(url=url)
    return SimpleNamespace(
        browser_session=SimpleNamespace(backend=SimpleNamespace(current_page=page))
    )


@pytest.mark.parametrize(
    "capability",
    [
        "browser.click",
        "browser.fill",
        "browser.select_option",
        "browser.set_checked",
        "browser.download",
        "browser.upload",
    ],
)
def test_on_a_denied_site_every_write_is_refused(capability: str) -> None:
    with pytest.raises(BrowserError) as caught:
        worker._refuse_on_denied_site(_state("https://sube.isbank.com.tr/hesap?id=42"), capability)
    error = caught.value
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR and error.retryable is False
    assert error.evidence["reason"] == "denied_site" and error.evidence["category"] == "bank"
    assert error.evidence["host"] == "sube.isbank.com.tr"
    assert "id=42" not in str(error.evidence) and "id=42" not in error.message


def test_on_any_other_site_the_check_says_nothing() -> None:
    assert (
        worker._refuse_on_denied_site(_state("https://www.trendyol.com/"), "browser.click") is None
    )


def test_every_operation_that_writes_asks_the_deny_list_before_anything_else() -> None:
    """A source check, named as one: the behaviour is the parametrised test above. This
    holds that no writing operation was added, or edited, without the call."""
    source = Path(worker.__file__).read_text(encoding="utf-8")
    for name in ("click", "fill", "select_option", "set_checked", "download", "upload"):
        start = source.index(f"async def _op_{name}(")
        body = source[start : source.index("    async def _op_", start + 10)]
        first = body.split(") -> dict[str, Any]:", 1)[1].lstrip().splitlines()[0]
        assert first.strip() == f'_refuse_on_denied_site(state, "browser.{name}")', (name, first)
    for name in ("navigate", "back", "observe", "extract", "snapshot", "scroll", "tab_new"):
        start = source.index(f"async def _op_{name}(")
        body = source[start : source.index("    async def _op_", start + 10)]
        assert "_refuse_on_denied_site" not in body, name


# ------------------------------------------------------------------ the risk ceiling


@pytest.mark.parametrize(
    ("actual", "ceiling"),
    [
        (RiskClass.REVERSIBLE_WRITE, "NAVIGATE"),
        (RiskClass.EXTERNAL_COMMUNICATION, "REVERSIBLE_WRITE"),
        (RiskClass.HIGH_IMPACT, "EXTERNAL_COMMUNICATION"),
        (RiskClass.HIGH_IMPACT, "READ"),
    ],
)
def test_an_element_above_the_ceiling_is_refused(actual: RiskClass, ceiling: str) -> None:
    with pytest.raises(BrowserError) as caught:
        enforce_ceiling(actual, ceiling, capability="browser.click")
    error = caught.value
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR and error.retryable is False
    assert error.evidence["reason"] == "above_ceiling"
    assert error.evidence["risk_class"] == str(actual) and error.evidence["risk_ceiling"] == ceiling


@pytest.mark.parametrize(
    ("actual", "ceiling"),
    [
        (RiskClass.NAVIGATE, "NAVIGATE"),
        (RiskClass.NAVIGATE, "REVERSIBLE_WRITE"),
        (RiskClass.REVERSIBLE_WRITE, "HIGH_IMPACT"),
        (RiskClass.HIGH_IMPACT, "HIGH_IMPACT"),
        (RiskClass.HIGH_IMPACT, None),  # a caller from before v1.7 names no ceiling
    ],
)
def test_at_or_below_the_ceiling_and_without_one_nothing_is_refused(
    actual: RiskClass, ceiling: str | None
) -> None:
    assert enforce_ceiling(actual, ceiling, capability="browser.click") is None


@pytest.mark.parametrize("ceiling", ["", "high", "HIGH", 3, True, ["HIGH_IMPACT"], "NONE"])
def test_a_ceiling_that_is_not_a_risk_class_is_a_validation_error(ceiling: Any) -> None:
    with pytest.raises(BrowserError) as caught:
        enforce_ceiling(RiskClass.READ, ceiling, capability="browser.click")
    assert caught.value.error_class == ErrorClass.VALIDATION_ERROR


def test_the_order_is_the_contracts() -> None:
    assert [c.value for c in policy.RISK_ORDER] == [
        "READ",
        "NAVIGATE",
        "REVERSIBLE_WRITE",
        "EXTERNAL_COMMUNICATION",
        "HIGH_IMPACT",
    ]
    assert set(policy.RISK_ORDER) == set(RiskClass)
