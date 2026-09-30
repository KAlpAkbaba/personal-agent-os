"""The cloud allow-list on the worker: the device's half of ADR-0213 addendum, option 4."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from browser_agent import cloud_allowlist

SHARED = (
    Path(__file__).resolve().parents[4] / "packages" / "protocol" / "browser-cloud-allowlist.json"
)


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch):
    def put(*names: str) -> None:
        monkeypatch.setattr(cloud_allowlist, "SITES", tuple(names))

    return put


def test_the_workers_copy_is_the_shared_file_verbatim() -> None:
    # No skip when the file is missing (test_contract_falsification's rule).
    assert list(cloud_allowlist.SITES) == json.loads(SHARED.read_text(encoding="utf-8"))["sites"]


def test_the_empty_list_allows_nothing(listed) -> None:
    listed()
    assert cloud_allowlist.acting_allowed("https://magaza.com.tr/") == (
        False,
        "not_on_owner_allow_list",
    )


def test_a_listed_site_and_its_subdomain_are_allowed(listed) -> None:
    listed("magaza.com.tr")
    assert cloud_allowlist.acting_allowed("https://magaza.com.tr/") == (True, "")
    assert cloud_allowlist.allowed("https://odeme.magaza.com.tr/x") is True


@pytest.mark.parametrize(
    "url",
    ["https://magaza.com.tr.evil.example/", "https://notmagaza.com.tr/", "ftp://magaza.com.tr/"],
)
def test_a_lookalike_of_a_listed_site_is_not_allowed(listed, url: str) -> None:
    listed("magaza.com.tr")
    assert cloud_allowlist.acting_allowed(url) == (False, "not_on_owner_allow_list")


def test_a_deny_listed_site_stays_refused_even_when_it_is_listed(listed) -> None:
    listed("isbank.com.tr", "magaza.com.tr")
    assert cloud_allowlist.acting_allowed("https://www.isbank.com.tr/") == (
        False,
        "deny_listed_site",
    )
    assert cloud_allowlist.acting_allowed("https://magaza.com.tr/") == (True, "")
