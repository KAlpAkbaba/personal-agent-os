"""Challenge/CAPTCHA policy (M18.2 owner rule 3): never bypass, detect fast,
never repeat, never let one blocked domain eat the run's whole budget.

The owner's real run (2026-09-06 session a4455670) spent much of its budget on
CAPTCHA/challenge pages: several URLs from the same blocked site were each
fetched, each judged only much later at ranking, and nothing ever remembered
that the site had already refused. This module is the memory that was
missing — pure, deterministic functions over the run's own
``progress_json`` (no I/O, no clock reads beyond what a caller passes in), so
:mod:`app.research.browser_activities` can call it right after a fetch
returns and :mod:`app.research.browser_workflow` never spends a second
attempt on a URL a challenge already refused.

Detection reuses :func:`app.research.eligibility.classify_page_validity` —
the SAME text-based classifier the quality gate already uses to recognise a
CAPTCHA/consent/interstitial/login page — so "detect quickly" means exactly
"one call, right after the fetch returns, using the same signals the gate
was always going to compute anyway" rather than a second, competing
detector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.research.evidence import PAGE_KIND_BLOCKED, PAGE_KIND_CAPTCHA

#: page_validity outcomes (app.research.eligibility.classify_page_validity) that
#: mean "this fetch never reached real content because something is actively
#: gating it" — the owner's list: CAPTCHA, consent walls, bot-verification
#: interstitials, login walls. access_denied is deliberately NOT included: a
#: paywall/403 is a content-availability problem, not a challenge to avoid
#: bypassing, and folding it in would cool a domain that simply requires a
#: subscription rather than one that is actively challenging automation.
CHALLENGE_PAGE_VALIDITY_KINDS: frozenset[str] = frozenset(
    {"captcha", "consent", "interstitial", "login_required"}
)

#: Device-reported page_kind values (app.research.evidence.PAGE_KINDS) that mean
#: the same thing from the OTHER side of the fetch (the worker/device classified
#: it before Cloud Core's own text classifier ever ran).
CHALLENGE_PAGE_KINDS: frozenset[str] = frozenset({PAGE_KIND_CAPTCHA, PAGE_KIND_BLOCKED})

#: A domain challenged this many times in one run is cooled: its remaining
#: candidates are skipped without navigation for the rest of the run (owner
#: rule 3: "so five URLs from the same blocked site do not each spend the
#: budget"). One challenge is corroborated as "maybe just this page"; a second
#: is the site itself.
DOMAIN_COOLDOWN_THRESHOLD = 2


def domain_of(url: str) -> str:
    """The bare host a challenge/cooldown decision keys on (``www.`` stripped,
    lower-cased) — the same normalisation ``app.research.evidence._normalize_url``
    applies to the netloc half of a URL, kept local here since only the host
    matters for this decision, never the path or query."""
    try:
        host = urlsplit(url.strip()).netloc.lower()
    except ValueError:
        return url.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_challenge(page_validity: str, page_kind: str = "") -> bool:
    """True when either signal says this fetch hit an active challenge."""
    return page_validity in CHALLENGE_PAGE_VALIDITY_KINDS or page_kind in CHALLENGE_PAGE_KINDS


@dataclass(frozen=True, slots=True)
class ChallengeUpdate:
    """The result of recording one challenged fetch: the new progress fields to
    persist, and whether this fetch is the one that just cooled its domain
    (diagnostics/event-trail wording only)."""

    challenge_counts: dict[str, int]
    cooled_domains: list[str]
    challenged_pages: int
    newly_cooled: bool


def record_challenge(progress: dict[str, Any] | None, domain: str) -> ChallengeUpdate:
    """Pure update: given the run's current ``progress_json`` (or ``None``) and
    the domain that was just challenged, return the new counters. The caller
    persists them (``runs_service.update_run(..., progress=update.__dict__)``-
    shaped); this function never touches the database itself."""
    current = progress or {}
    counts = {str(k): int(v) for k, v in (current.get("challenge_counts") or {}).items()}
    cooled = [str(d) for d in (current.get("cooled_domains") or [])]
    total = int(current.get("challenged_pages") or 0) + 1

    counts[domain] = counts.get(domain, 0) + 1
    newly_cooled = False
    if counts[domain] >= DOMAIN_COOLDOWN_THRESHOLD and domain not in cooled:
        cooled = [*cooled, domain]
        newly_cooled = True

    return ChallengeUpdate(
        challenge_counts=counts,
        cooled_domains=cooled,
        challenged_pages=total,
        newly_cooled=newly_cooled,
    )


def is_domain_cooled(progress: dict[str, Any] | None, domain: str) -> bool:
    cooled = (progress or {}).get("cooled_domains") or []
    return domain in cooled


def challenge_count_for(progress: dict[str, Any] | None, domain: str) -> int:
    counts = (progress or {}).get("challenge_counts") or {}
    return int(counts.get(domain, 0))


__all__ = [
    "CHALLENGE_PAGE_KINDS",
    "CHALLENGE_PAGE_VALIDITY_KINDS",
    "DOMAIN_COOLDOWN_THRESHOLD",
    "ChallengeUpdate",
    "challenge_count_for",
    "domain_of",
    "is_challenge",
    "is_domain_cooled",
    "record_challenge",
]
