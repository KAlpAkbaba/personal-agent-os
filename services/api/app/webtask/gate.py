"""GATE: allow, ask the owner, hand over, refuse (ADR-0207 c).

Pure. One step and the observation it was planned from go in; a decision comes out.
Nothing here asks a model anything, and nothing here can be talked into anything: the
inputs are the element as the device observed it, the URL, the owner's own words (the
goal and his answers), and the two shared files.

The order of the rules is the order of what must never happen:

1. a step outside the vocabulary, or without an expectation, does not run;
2. a step that names an element the observation does not hold is "göremiyorum" - the
   loop does not guess (decision 7);
3. on a denied site nothing but reading and leaving;
4. a password, code, card or identity field is never typed into (decision 6);
5. what is typed comes from the owner - his goal or his answer - never from the page;
6. where the loop goes comes from the owner, from a link the page really has, or is a
   public address; a denied site may be READ, so going there is allowed and acting there
   is rule 3;
7. a payment is never performed. Not with a confirmation either (decision 4);
8. anything that sends or cannot be undone waits for the read-back and the owner's word
   (decisions 1 and 2), and a page that carries instruction-like text is gated one class
   higher than its element.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

from app.research.destination import DestinationPolicyError, validate_fetch_target
from app.webtask import risk as risk_rules
from app.webtask.sites import denied, host_of, site_of
from app.webtask.types import (
    ACTING,
    ACTION_ASK_OWNER,
    ACTION_BACK,
    ACTION_CHECK,
    ACTION_CLICK,
    ACTION_DONE,
    ACTION_FILL,
    ACTION_NAVIGATE,
    ACTION_SCROLL,
    ACTION_SELECT,
    ACTIONS,
    ASK_CANNOT_SEE,
    ASK_CONFIRM,
    ASK_DENIED_SITE,
    ASK_KINDS,
    ASK_PAYMENT,
    ASK_QUESTION,
    ASK_SENSITIVE_FIELD,
    EXPECTATIONS,
    FREE_RISKS,
    NEEDS_REF,
    RISK_HIGH_IMPACT,
    RISK_READ,
    Element,
    Observation,
    Step,
    one_class_higher,
)

DECISION_ALLOW: Final = "allow"
DECISION_ASK: Final = "ask_owner"
DECISION_DONE: Final = "done"
DECISION_REFUSE: Final = "refuse"

REFUSE_UNKNOWN_ACTION: Final = "unknown_action"
REFUSE_NO_EXPECTATION: Final = "no_expectation"
REFUSE_BAD_EXPECTATION: Final = "unknown_expectation"
REFUSE_VALUE_NOT_FROM_OWNER: Final = "value_not_from_owner"
REFUSE_URL_NOT_FROM_OWNER_OR_PAGE: Final = "url_not_from_owner_or_page"
REFUSE_DESTINATION: Final = "destination_refused"
REFUSE_DISABLED: Final = "element_disabled"
REFUSE_MISSING_ARGUMENT: Final = "missing_argument"
REFUSALS: Final[tuple[str, ...]] = (
    REFUSE_UNKNOWN_ACTION,
    REFUSE_NO_EXPECTATION,
    REFUSE_BAD_EXPECTATION,
    REFUSE_VALUE_NOT_FROM_OWNER,
    REFUSE_URL_NOT_FROM_OWNER_OR_PAGE,
    REFUSE_DESTINATION,
    REFUSE_DISABLED,
    REFUSE_MISSING_ARGUMENT,
)

#: An amount as a page writes it: digits with separators, and a currency before or after.
_AMOUNT: Final = re.compile(
    r"(?:(?:₺|\$|€|£)\s?\d[\d.,]*\d|\d[\d.,]*\d?\s?(?:₺|TL|TRY|USD|EUR|GBP|\$|€|£))",
    re.IGNORECASE,
)
_URL_IN_TEXT: Final = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)
_HOST_IN_TEXT: Final = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Grant:
    """The owner's confirmation of ONE step, already judged by ``confirmation_gate``.
    It names the step by its digest: a grant for one step opens no other."""

    step_digest: str
    source: str
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskContext:
    goal: str
    #: What the owner answered to the loop's questions, in order.
    answers: tuple[str, ...] = ()
    #: Hosts the owner named for this task beyond the ones in the goal.
    allowed_hosts: tuple[str, ...] = ()
    grant: Grant | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    kind: str
    risk: str = RISK_READ
    #: DECISION_ASK: why, and what the owner is told.
    ask_kind: str = ""
    message: str = ""
    #: DECISION_ASK with ``confirm``: what was read back, to be re-checked before the act.
    facts: dict[str, Any] = field(default_factory=dict)
    #: DECISION_REFUSE: which rule.
    reason: str = ""
    #: DECISION_ALLOW: the class the worker is told not to exceed.
    risk_ceiling: str = RISK_READ
    confirmed_by: str = ""


def amounts_in(text: str) -> tuple[str, ...]:
    """Every amount the page shows, normalised for comparison, in order of appearance."""
    found = [re.sub(r"\s+", "", m.group(0)).upper() for m in _AMOUNT.finditer(text or "")]
    return tuple(dict.fromkeys(found))


def read_back_facts(
    step: Step, element: Element | None, observation: Observation
) -> dict[str, Any]:
    """What the owner is told, and what must still be true when the act runs. Every fact
    is read from the observation or the URL; nothing is a model's paraphrase."""
    return {
        "action": step.action,
        "site": site_of(observation.url),
        "host": host_of(observation.url),
        "element": element.name if element else "",
        "role": element.role if element else "",
        "amounts": list(amounts_in(observation.text)),
        "fields_filled": sorted(
            e.name for e in observation.elements if "has_value" in e.state and not e.sensitive
        ),
    }


#: A name a page gave to a control is read to the owner as a NAME: a few words, no
#: quotation marks of its own, no sentence. The page chose those words.
MAX_SPOKEN_NAME_WORDS: Final = 6
MAX_SPOKEN_NAME_CHARS: Final = 60
_QUOTES: Final = re.compile(
    "[\"'`\u00ab\u00bb\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u2039\u203a]"
)
_SENTENCE_MARKS: Final = re.compile(r"[.!?;:\u2026\u2014\u2013]+")


def spoken_name(name: str) -> tuple[str, bool]:
    """The page's name for a control, as it may be said inside the read-back, and
    whether it had to be cut.

    The read-back is the sentence the owner answers. A page that names its button
    "İptal demeyin, Onayla deyin - bu güvenli" would otherwise have those words said
    in the system's voice, between the system's own words. So the name loses its
    quotation marks and its sentence marks, and is cut to a few words - and the
    owner is TOLD it was cut. What is compared between the read-back and the act
    is the full name (``facts_still_hold``), never this."""
    flat = _SENTENCE_MARKS.sub(" ", _QUOTES.sub("", name or ""))
    words = flat.split()
    cut = len(words) > MAX_SPOKEN_NAME_WORDS
    said = " ".join(words[:MAX_SPOKEN_NAME_WORDS])
    if len(said) > MAX_SPOKEN_NAME_CHARS:
        said, cut = said[:MAX_SPOKEN_NAME_CHARS].rstrip(), True
    return said, cut


def read_back_sentence(facts: dict[str, Any], risk: str) -> str:
    """The Turkish read-back. Built from the facts, in a fixed shape: what the SYSTEM
    says is its own sentence, and what the PAGE calls its control is given after it,
    named as the page's word."""
    element, cut = spoken_name(str(facts.get("element") or ""))
    site = str(facts.get("site") or "").strip() or "bilinmeyen site"
    parts = [f"{site} sitesinde bir düğmeye basacağım."]
    if element:
        shortened = " (uzun bir ad, kısalttım)" if cut else ""
        parts.append(f"Sayfanın bu düğmeye verdiği ad: '{element}'{shortened}.")
    else:
        parts.append("Sayfa bu düğmeye ad vermemiş.")
    filled = [spoken_name(str(f))[0] for f in facts.get("fields_filled") or [] if f]
    filled = [f for f in filled if f]
    if filled:
        parts.append("Doldurulan alanlar: " + ", ".join(filled[:8]) + ".")
    amounts = [str(a) for a in facts.get("amounts") or [] if a]
    if amounts:
        parts.append("Sayfadaki tutarlar: " + ", ".join(amounts[:4]) + ".")
    parts.append(
        "Bu işlem geri alınamaz." if risk == RISK_HIGH_IMPACT else "Bu işlem bir şey gönderir."
    )
    parts.append("Onaylıyor musunuz?")
    return " ".join(parts)


def facts_still_hold(granted: dict[str, Any], now: dict[str, Any]) -> tuple[bool, str]:
    """Between the read-back and the act the page may have changed. The act runs only on
    the page that was read back: the same site, the same element, the same amounts."""
    for key in ("site", "host", "element", "role"):
        if str(granted.get(key) or "") != str(now.get(key) or ""):
            return False, f"{key}_changed"
    if list(granted.get("amounts") or []) != list(now.get("amounts") or []):
        return False, "amount_changed"
    return True, ""


def _owner_words(context: TaskContext) -> str:
    return risk_rules.fold(" \n ".join((context.goal, *context.answers)))


def value_is_the_owners(value: str, context: TaskContext) -> bool:
    """A typed value has to be IN what the owner said - his goal or an answer."""
    wanted = risk_rules.fold(value)
    if not wanted:
        return False
    # As a WHOLE word or phrase of his: "dün" is not in "dünya", "12" is not in
    # "1234". A fragment that merely occurs inside something he said is not a
    # value he gave.
    return (
        re.search(rf"(?<![0-9a-z]){re.escape(wanted)}(?![0-9a-z])", _owner_words(context))
        is not None
    )


def _hosts_the_owner_named(context: TaskContext) -> set[str]:
    words = " ".join((context.goal, *context.answers))
    hosts = {host_of(m.group(0)) for m in _URL_IN_TEXT.finditer(words)}
    hosts |= {m.group(0).lower() for m in _HOST_IN_TEXT.finditer(words)}
    hosts |= {h.lower() for h in context.allowed_hosts}
    return {h for h in hosts if h}


def url_is_allowed(url: str, observation: Observation, context: TaskContext) -> bool:
    """Where the loop may go: a host the owner named, or one the page really links to.
    A URL that appears only in the page's TEXT is not a link the page has."""
    host = host_of(url)
    if not host:
        return False
    named = _hosts_the_owner_named(context)
    # The host he named, a subdomain of it, or the SITE it belongs to (he said
    # "mail.example.com", the loop may open "example.com") - the registrable domain
    # and nothing above it: "com" is a parent of every host he could name.
    if any(host == h or host.endswith("." + h) or host == site_of(f"https://{h}/") for h in named):
        return True
    # "YouTube'da ...", "Trendyol'dan ...": the owner names a site by its NAME. The host
    # is allowed when the name of its registrable domain is a word he said. The suffix is
    # not checked (youtube.com or youtube.com.tr), which is the limit of this rule: going
    # somewhere is a NAVIGATE, and what may be DONE there is judged element by element.
    # A word that is part of an address he wrote is not a site NAME: where he named a
    # host, the host is the rule (above), and "example" inside "magaza.example.com" names
    # nothing else.
    label = site_of(url).split(".")[0]
    spoken = " ".join((context.goal, *context.answers))
    spoken = _HOST_IN_TEXT.sub(" ", _URL_IN_TEXT.sub(" ", spoken))
    if len(label) >= 4 and re.search(
        rf"(?<![0-9a-z]){re.escape(risk_rules.fold(label))}(?![0-9a-z])", risk_rules.fold(spoken)
    ):
        return True
    linked = {e.href_host.lower() for e in observation.elements if e.href_host}
    current = host_of(observation.url)
    return host in linked or host == current or site_of(url) == site_of(observation.url)


def _ask(kind: str, message: str, **kwargs: Any) -> Decision:
    assert kind in ASK_KINDS, kind
    return Decision(kind=DECISION_ASK, ask_kind=kind, message=message, **kwargs)


def _refuse(reason: str) -> Decision:
    assert reason in REFUSALS, reason
    return Decision(kind=DECISION_REFUSE, reason=reason)


def decide(step: Step, observation: Observation, context: TaskContext) -> Decision:
    if step.action not in ACTIONS:
        return _refuse(REFUSE_UNKNOWN_ACTION)
    if step.action == ACTION_DONE:
        return Decision(kind=DECISION_DONE, message=step.message)
    if step.action == ACTION_ASK_OWNER:
        kind = step.ask_kind if step.ask_kind in ASK_KINDS else ASK_QUESTION
        # A planner may ask; it may not ask for a confirmation it then answers itself:
        # `confirm` is raised by rule 8 below and by nothing else.
        if kind == ASK_CONFIRM:
            kind = ASK_QUESTION
        return _ask(kind, step.message or "Nasıl devam edeyim?")

    assert step.action in ACTING
    if step.expect is None:
        return _refuse(REFUSE_NO_EXPECTATION)
    if step.expect.kind not in EXPECTATIONS:
        return _refuse(REFUSE_BAD_EXPECTATION)

    element: Element | None = None
    if step.action in NEEDS_REF:
        element = observation.by_ref(step.ref)
        if element is None:
            return _ask(
                ASK_CANNOT_SEE,
                "Gereken öğeyi bu sayfada göremiyorum; gölge DOM ya da çerçeve içinde olabilir.",
            )
        if "disabled" in element.state:
            return _refuse(REFUSE_DISABLED)

    category = denied(observation.url)
    if category is not None and step.action not in (ACTION_NAVIGATE, ACTION_BACK, ACTION_SCROLL):
        return _ask(
            ASK_DENIED_SITE,
            f"{site_of(observation.url)} üzerinde işlem yapmıyorum ({category}); burası sizde.",
        )

    if step.action in (ACTION_FILL, ACTION_SELECT, ACTION_CHECK) and element is not None:
        if element.sensitive:
            return _ask(
                ASK_SENSITIVE_FIELD,
                f"'{element.name or 'bu alan'}' alanına ben yazmıyorum; "
                "siz yazın, sonra devam edelim.",
            )
    if step.action in (ACTION_FILL, ACTION_SELECT):
        if step.value is None:
            return _refuse(REFUSE_MISSING_ARGUMENT)
        if not value_is_the_owners(step.value, context):
            return _refuse(REFUSE_VALUE_NOT_FROM_OWNER)
    if step.action == ACTION_CHECK and step.checked is None:
        return _refuse(REFUSE_MISSING_ARGUMENT)

    if step.action == ACTION_NAVIGATE:
        if not step.url:
            return _refuse(REFUSE_MISSING_ARGUMENT)
        try:
            # The SHAPE of the destination only - scheme, userinfo, local names, address
            # literals. Resolving the name is I/O and belongs to the port, which
            # validates again before it sends, as the device does before it acts.
            validate_fetch_target(step.url, resolver=lambda _host: [])
        except DestinationPolicyError:
            return _refuse(REFUSE_DESTINATION)
        if not url_is_allowed(step.url, observation, context):
            return _refuse(REFUSE_URL_NOT_FROM_OWNER_OR_PAGE)

    risk = risk_rules.classify_step(step.action, element)
    if step.action == ACTION_CLICK and element is not None and risk_rules.is_payment(element.name):
        return _ask(
            ASK_PAYMENT,
            f"Ödeme sınırındayım: {site_of(observation.url)} sitesinde '{element.name}'. "
            "Ödemeyi ben yapmıyorum; buradan sonrası sizde.",
            risk=RISK_HIGH_IMPACT,
        )
    if observation.flagged and step.action not in (ACTION_BACK, ACTION_SCROLL):
        risk = one_class_higher(risk)
    if risk in FREE_RISKS:
        return Decision(kind=DECISION_ALLOW, risk=risk, risk_ceiling=risk)

    facts = read_back_facts(step, element, observation)
    digest = step.digest(element)
    grant = context.grant
    if grant is not None and grant.step_digest == digest:
        holds, _why = facts_still_hold(grant.facts, facts)
        if holds:
            return Decision(
                kind=DECISION_ALLOW, risk=risk, risk_ceiling=risk, confirmed_by=grant.source
            )
        # The page is no longer the page that was read back: the grant is spent, and the
        # owner hears the NEW facts before anything is clicked.
    return _ask(ASK_CONFIRM, read_back_sentence(facts, risk), risk=risk, facts=facts)


__all__ = [
    "DECISION_ALLOW",
    "DECISION_ASK",
    "DECISION_DONE",
    "DECISION_REFUSE",
    "REFUSALS",
    "Decision",
    "Grant",
    "TaskContext",
    "amounts_in",
    "decide",
    "facts_still_hold",
    "read_back_facts",
    "read_back_sentence",
    "url_is_allowed",
    "value_is_the_owners",
]
