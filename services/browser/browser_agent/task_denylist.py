"""The sites no browser task acts on (contract v1.7, ADR-0207 decision 5).

On a page whose host is in this list the worker serves READ and navigation and
refuses everything that changes the page or moves a file: ``click``, ``fill``,
``select_option``, ``set_checked``, ``download``, ``upload``. The Cloud Core's gate
refuses the same steps before it sends them; this is the device's half, and it is
the half that still holds when the other one is wrong.

``CATEGORIES`` is written verbatim from
``packages/protocol/browser-task-denylist.json`` - the ONE source both sides read -
and ``tests/unit/test_task_denylist.py`` holds the two identical. The worker carries
the list in its own source because an installed worker has no repository beside it.

A host matches when it IS one of a category's domains or a subdomain of one, or when
one of its labels CONTAINS one of the category's ``host_label_parts``. Containment
over-matches on purpose: the cost of a false match is a refusal and a question.
"""

from __future__ import annotations

from typing import Final
from urllib.parse import urlsplit

#: category -> (domains, host_label_parts)
CATEGORIES: Final[dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "bank": (
        (
            "garantibbva.com.tr",
            "isbank.com.tr",
            "akbank.com",
            "yapikredi.com.tr",
            "ziraatbank.com.tr",
            "halkbank.com.tr",
            "vakifbank.com.tr",
            "qnb.com.tr",
            "qnbfinansbank.com",
            "denizbank.com",
            "teb.com.tr",
            "ing.com.tr",
            "kuveytturk.com.tr",
            "albaraka.com.tr",
            "turkiyefinans.com.tr",
            "enpara.com",
            "odeabank.com.tr",
            "fibabanka.com.tr",
            "sekerbank.com.tr",
            "hsbc.com.tr",
            "anadolubank.com.tr",
        ),
        (
            "bank",
            "banka",
        ),
    ),
    "government_identity": (
        (
            "turkiye.gov.tr",
            "edevlet.gov.tr",
            "gib.gov.tr",
            "sgk.gov.tr",
            "nvi.gov.tr",
        ),
        ("edevlet",),
    ),
    "payment_provider": (
        (
            "paypal.com",
            "iyzico.com",
            "iyzipay.com",
            "paytr.com",
            "stripe.com",
            "wise.com",
            "papara.com",
            "ininal.com",
            "payu.com.tr",
            "masterpass.com.tr",
            "bkmexpress.com.tr",
            "pay.google.com",
            "payments.google.com",
            "wallet.google.com",
        ),
        (),
    ),
    "password_manager": (
        (
            "1password.com",
            "bitwarden.com",
            "lastpass.com",
            "dashlane.com",
            "keepersecurity.com",
            "nordpass.com",
            "pass.proton.me",
            "passwords.google.com",
            "myaccount.google.com",
            "account.microsoft.com",
            "appleid.apple.com",
        ),
        (),
    ),
    "employer": (
        ("turka.com",),
        ("turka",),
    ),
    "kolay_monitor": (
        (),
        (
            "kolaymonitor",
            "kolay-monitor",
        ),
    ),
    "own_system": (
        (),
        ("pagentos",),
    ),
}


def host_of(url: str) -> str:
    """The host, lower-cased; ``""`` for anything that is not an http(s) URL."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https"):
        return ""
    return (parts.hostname or "").rstrip(".").lower()


def denied_host(host: str) -> str | None:
    host = (host or "").rstrip(".").lower()
    if not host:
        return None
    labels = host.split(".")
    for category, (domains, parts) in CATEGORIES.items():
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return category
        for part in parts:
            if part and any(part in label for label in labels):
                return category
    return None


def denied(url: str) -> str | None:
    """The category that puts this URL's host on the deny-list, or ``None``."""
    return denied_host(host_of(url))


__all__ = ["CATEGORIES", "denied", "denied_host", "host_of"]
