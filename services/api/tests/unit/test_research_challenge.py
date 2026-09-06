"""app.research.challenge: the challenge/CAPTCHA policy's pure decision logic
(M18.2, ADR-0068). No I/O, no DB — the domain-cooldown bookkeeping that
app.research.browser_activities persists is exercised directly against
Postgres/SQLite in tests/unit/test_research_browser_activities.py; this file
is the state-transition function underneath it, isolated.
"""

from __future__ import annotations

from app.research.challenge import (
    CHALLENGE_PAGE_KINDS,
    CHALLENGE_PAGE_VALIDITY_KINDS,
    DOMAIN_COOLDOWN_THRESHOLD,
    domain_of,
    is_challenge,
    is_domain_cooled,
    record_challenge,
)


def test_domain_of_normalizes_www_and_case() -> None:
    assert domain_of("https://WWW.Example.com/a/b?c=1") == "example.com"
    assert domain_of("https://example.com/a") == "example.com"


def test_domain_of_handles_a_malformed_url_without_raising() -> None:
    # urlsplit tolerates this (no scheme/authority => empty netloc); domain_of
    # never raises either way, which is what this test actually pins.
    assert domain_of("not a url at all") == ""


def test_is_challenge_recognizes_every_owner_named_kind() -> None:
    # CAPTCHA, consent walls, bot-verification interstitials, login walls (owner rule 3).
    for kind in ("captcha", "consent", "interstitial", "login_required"):
        assert is_challenge(kind) is True
    assert is_challenge("normal_content") is False
    # access_denied (a paywall/403) is deliberately NOT a challenge: bypassing a
    # subscription wall is not the "never bypass a CAPTCHA" problem this exists for.
    assert is_challenge("access_denied") is False


def test_is_challenge_also_recognizes_the_devices_own_page_kind() -> None:
    assert is_challenge("normal_content", "captcha") is True
    assert is_challenge("normal_content", "blocked") is True
    assert is_challenge("normal_content", "ok") is False


def test_challenge_page_kind_sets_are_consistent_with_evidence_page_kinds() -> None:
    from app.research.evidence import PAGE_KINDS

    assert CHALLENGE_PAGE_KINDS <= set(PAGE_KINDS)
    assert CHALLENGE_PAGE_VALIDITY_KINDS  # non-empty, named explicitly


def test_first_challenge_does_not_cool_the_domain() -> None:
    update = record_challenge(None, "blocked.example.com")
    assert update.challenge_counts == {"blocked.example.com": 1}
    assert update.cooled_domains == []
    assert update.challenged_pages == 1
    assert update.newly_cooled is False


def test_second_challenge_on_the_same_domain_cools_it() -> None:
    progress = {"challenge_counts": {"blocked.example.com": 1}, "challenged_pages": 1}
    update = record_challenge(progress, "blocked.example.com")
    assert update.challenge_counts == {"blocked.example.com": 2}
    assert update.cooled_domains == ["blocked.example.com"]
    assert update.challenged_pages == 2
    assert update.newly_cooled is True
    assert update.challenge_counts["blocked.example.com"] >= DOMAIN_COOLDOWN_THRESHOLD


def test_a_third_challenge_does_not_re_add_an_already_cooled_domain() -> None:
    progress = {
        "challenge_counts": {"blocked.example.com": 2},
        "cooled_domains": ["blocked.example.com"],
        "challenged_pages": 2,
    }
    update = record_challenge(progress, "blocked.example.com")
    assert update.cooled_domains == ["blocked.example.com"]  # not duplicated
    assert update.newly_cooled is False  # already was cooled; this isn't the transition


def test_challenges_on_different_domains_are_independent() -> None:
    progress = {"challenge_counts": {"a.example.com": 1}, "challenged_pages": 1}
    update = record_challenge(progress, "b.example.com")
    assert update.challenge_counts == {"a.example.com": 1, "b.example.com": 1}
    assert update.cooled_domains == []


def test_is_domain_cooled_reads_the_stored_list() -> None:
    assert is_domain_cooled(None, "x.example.com") is False
    assert is_domain_cooled({"cooled_domains": ["x.example.com"]}, "x.example.com") is True
    assert is_domain_cooled({"cooled_domains": ["x.example.com"]}, "y.example.com") is False
