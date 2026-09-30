"""The cloud allow-list (ADR-0213 addendum, option 4): a cloud job ACTS only on a listed site.

The rule is written once in ``packages/protocol/browser-cloud-allowlist.json``; the Cloud Core
reads its bundled copy and the worker carries ``SITES`` verbatim.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.execution import allowlist
from app.protocol_files import protocol_file

REPO = Path(__file__).resolve().parents[4]
SHARED = REPO / "packages" / "protocol" / "browser-cloud-allowlist.json"
WORKER_PY = REPO / "services" / "browser" / "browser_agent" / "cloud_allowlist.py"


@pytest.fixture
def listed(monkeypatch: pytest.MonkeyPatch):
    def put(*names: str) -> None:
        monkeypatch.setattr(allowlist, "sites", lambda: tuple(names))

    return put


def test_the_empty_list_allows_nothing(listed) -> None:
    listed()
    assert allowlist.acting_allowed("https://magaza.com.tr/sepet") == (
        False,
        "not_on_owner_allow_list",
    )
    assert allowlist.allowed("https://example.org/") is False


def test_a_listed_site_and_its_subdomain_are_allowed(listed) -> None:
    listed("magaza.com.tr")
    assert allowlist.acting_allowed("https://magaza.com.tr/sepet") == (True, "")
    assert allowlist.acting_allowed("https://odeme.magaza.com.tr:8443/x") == (True, "")


@pytest.mark.parametrize(
    "url",
    [
        "https://magaza.com.tr.evil.example/",
        "https://notmagaza.com.tr/",
        "https://evil.example/?u=magaza.com.tr",
        "https://evil.example/magaza.com.tr",
        "ftp://magaza.com.tr/",
        "not a url",
    ],
)
def test_a_lookalike_of_a_listed_site_is_not_allowed(listed, url: str) -> None:
    listed("magaza.com.tr")
    assert allowlist.acting_allowed(url) == (False, "not_on_owner_allow_list")


def test_a_deny_listed_site_stays_refused_even_when_it_is_listed(listed) -> None:
    listed("isbank.com.tr", "magaza.com.tr")
    assert allowlist.acting_allowed("https://www.isbank.com.tr/") == (False, "deny_listed_site")
    assert allowlist.acting_allowed("https://magaza.com.tr/") == (True, "")


def test_the_shipped_list_is_empty_and_the_bundle_is_the_shared_file() -> None:
    assert json.loads(SHARED.read_text(encoding="utf-8"))["sites"] == []
    assert allowlist.sites() == ()
    assert protocol_file("browser-cloud-allowlist.json") == allowlist.ALLOWLIST_PATH
    assert allowlist.ALLOWLIST_PATH.read_bytes() == SHARED.read_bytes()


def test_an_entry_that_is_not_a_registrable_domain_is_an_error_not_a_quiet_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "a.json"
    bad.write_text(
        json.dumps({"version": 1, "rule": "", "sites": ["odeme.magaza.com.tr"]}), encoding="utf-8"
    )
    monkeypatch.setattr(allowlist, "ALLOWLIST_PATH", bad)
    allowlist.sites.cache_clear()
    try:
        with pytest.raises(ValueError):
            allowlist.sites()
    finally:
        allowlist.sites.cache_clear()


def test_the_workers_copy_is_the_shared_file_verbatim() -> None:
    tree = ast.parse(WORKER_PY.read_text(encoding="utf-8"))
    worker_sites = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "SITES":
            worker_sites = ast.literal_eval(node.value)
    assert worker_sites is not None
    assert list(worker_sites) == json.loads(SHARED.read_text(encoding="utf-8"))["sites"]
