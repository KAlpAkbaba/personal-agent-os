"""The bank's notification mails, read: a card spend, money in, a balance - or nothing.

READ-ONLY by construction: the input is a mail the owner's inbox already holds (``mail_index``:
sender, subject and the first lines of the body); nothing here logs in anywhere, scrapes an
internet-banking page or opens a connection. A mail counts only when its SENDER's domain is one
of the banks below (exactly, or a subdomain of it - ``garantibbva.com.tr.evil.example`` and
``notgarantibbva.com.tr`` are not Garanti) and its words state a movement that HAPPENED
("yapılmıştır", "gelen ... transfer") or a balance ("güncel bakiyeniz"). The bank's own
advertising ("2.000 TL harcamanıza 200 TL bonus!") states neither and is nothing.

Each bank is a small parser: its domains and display name, and - when its mails say something
the shared Turkish patterns do not - its own extra patterns. The shared patterns cover the
shapes the Turkish banks' notices have in common (an amount followed by TL/TRY; "işyerinde" /
"İşyeri:" naming the merchant; "bakiye" naming the balance; "limit" is never the balance).

Pure: no database, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final

from app.money.amounts import parse_amount

KIND_SPEND: Final = "spend"
KIND_INCOME: Final = "income"
KIND_BALANCE: Final = "balance"


@dataclass(frozen=True)
class Bank:
    key: str
    name: str
    domains: tuple[str, ...]
    #: Extra patterns for this bank's own wording: a regex with a named group ``amount``
    #: (a card/account spend) - tried before the shared ones.
    spend_patterns: tuple[re.Pattern[str], ...] = field(default=())


BANKS: Final[tuple[Bank, ...]] = (
    Bank("garanti", "Garanti BBVA", ("garantibbva.com.tr", "garanti.com.tr")),
    Bank("isbank", "İş Bankası", ("isbank.com.tr",)),
    Bank("akbank", "Akbank", ("akbank.com", "akbank.com.tr")),
    Bank("yapikredi", "Yapı Kredi", ("yapikredi.com.tr",)),
    Bank("ziraat", "Ziraat Bankası", ("ziraatbank.com.tr",)),
    Bank("vakifbank", "VakıfBank", ("vakifbank.com.tr",)),
    Bank("halkbank", "Halkbank", ("halkbank.com.tr",)),
    Bank(
        "qnb",
        "QNB",
        ("qnb.com.tr", "qnbfinansbank.com", "enpara.com"),
        # Enpara writes the merchant first: "... MIGROS'tan 120,50 TL'lik alışveriş ...".
        (re.compile(r"(?P<amount>\d[\d.,]*)\s*TL'lik\s+(?:alışveriş|harcama)", re.I),),
    ),
    Bank("denizbank", "DenizBank", ("denizbank.com",)),
    Bank("teb", "TEB", ("teb.com.tr",)),
    Bank("ing", "ING", ("ing.com.tr", "ingbank.com.tr")),
    Bank("kuveytturk", "Kuveyt Türk", ("kuveytturk.com.tr",)),
)

#: The amount a notice writes: "1.234,56 TL", "750,00 TL", "TRY 1,234.56".
_AMOUNT: Final = r"(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
_AMOUNT_TL: Final = re.compile(
    _AMOUNT + r"\s*(?:TL|TRY)\b|(?:TL|TRY)\s*" + _AMOUNT.replace("?P<amount>", "?P<after>"), re.I
)

#: A movement that happened (past tense) - never "harcayın", "kazanın", "bonus".
_DONE: Final = re.compile(
    r"\b(?:yapılmıştır|yapıldı|gerçekleşmiştir|gerçekleşti|gerçekleştirilmiştir|"
    r"çekilmiştir|yatırılmıştır|aktarılmıştır|gönderilmiştir)\b"
    r"|\bgelen\s+(?:havale|eft|fast|transfer|para)"
    r"|\bpara\s+geldi\b",
    re.I,
)
_INCOME: Final = re.compile(
    r"\bhesabınıza\b|\bgelen\s+(?:havale|eft|fast|transfer|para)|\byatırılmıştır\b|"
    r"\bpara\s+geldi\b",
    re.I,
)
_SPEND: Final = re.compile(
    r"\bharcama\b|\balışveriş\b|\bödeme\b|\bçekilmiştir\b|\bhesabınızdan\b|\bkartınız", re.I
)
_BALANCE: Final = re.compile(
    r"bakiye\w*(?:\s+(?:bilgisi|tutarı|durumu))?\s*(?:[:\-]\s*)?" + _AMOUNT + r"\s*(?:TL|TRY)",
    re.I,
)
#: An amount within this many characters after one of these words is not the movement.
_NOT_MOVEMENT: Final = re.compile(r"(?:bakiye|limit|borç|puan|bonus)\w*\W*(?:\w+\W+){0,2}$", re.I)

#: Upper-case words (a merchant as the bank prints it); no dot - "A101 KADIKOY. Güncel ..."
#: must stop at the full stop.
_MERCHANT_CHARS: Final = r"[A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9 &*\-/]{1,38}[A-ZÇĞİÖŞÜ0-9]"
_MERCHANT: Final = (
    re.compile(r"(?:İşyeri|İş yeri|Üye işyeri)\s*:\s*(?P<m>" + _MERCHANT_CHARS + ")"),
    re.compile(r"(?P<m>" + _MERCHANT_CHARS + r")\s+(?:üye\s+)?(?:işyerinde|iş yerinde)"),
    re.compile(r"(?P<m>" + _MERCHANT_CHARS + r")'(?:tan|ten|dan|den)\s"),
)


@dataclass(frozen=True)
class BankNotice:
    bank: Bank
    kind: str
    at: datetime
    amount_kurus: int | None = None
    balance_kurus: int | None = None
    merchant: str | None = None


def bank_for(sender: str) -> Bank | None:
    """The bank a sender address belongs to: the domain itself or a subdomain of it."""
    _, _, domain = (sender or "").strip().lower().rpartition("@")
    domain = domain.strip(">").strip()
    if not domain:
        return None
    for bank in BANKS:
        for known in bank.domains:
            if domain == known or domain.endswith("." + known):
                return bank
    return None


def _movement_amount(bank: Bank, said: str) -> int | None:
    for pattern in bank.spend_patterns:
        if match := pattern.search(said):
            return parse_amount(match.group("amount"))
    for match in _AMOUNT_TL.finditer(said):
        if _NOT_MOVEMENT.search(said[max(0, match.start() - 40) : match.start()]):
            continue
        raw = match.group("amount") or match.group("after")
        if raw and (value := parse_amount(raw)) is not None:
            return value
    return None


def _merchant(said: str) -> str | None:
    for pattern in _MERCHANT:
        if match := pattern.search(said):
            return " ".join(match.group("m").split())
    return None


def parse_notice(sender: str, subject: str, body: str, *, at: datetime) -> BankNotice | None:
    bank = bank_for(sender)
    if bank is None:
        return None
    said = " ".join(f"{subject or ''} . {body or ''}".split())
    balance_match = _BALANCE.search(said)
    balance = parse_amount(balance_match.group("amount")) if balance_match else None
    if _DONE.search(said):
        amount = _movement_amount(bank, said)
        if amount is not None:
            kind = KIND_INCOME if _INCOME.search(said) and not _SPEND.search(said) else KIND_SPEND
            if not _SPEND.search(said) and not _INCOME.search(said):
                return None
            return BankNotice(
                bank=bank,
                kind=kind,
                at=at,
                amount_kurus=amount,
                balance_kurus=balance,
                merchant=_merchant(said) if kind == KIND_SPEND else None,
            )
    if balance is not None:
        return BankNotice(bank=bank, kind=KIND_BALANCE, at=at, balance_kurus=balance)
    return None


__all__ = [
    "BANKS",
    "KIND_BALANCE",
    "KIND_INCOME",
    "KIND_SPEND",
    "Bank",
    "BankNotice",
    "bank_for",
    "parse_notice",
]
