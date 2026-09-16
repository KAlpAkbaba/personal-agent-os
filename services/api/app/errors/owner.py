"""B22 req 705: the owner never meets a Python exception.

Thirty-three route handlers answered a failed request with `HTTPException(detail=str(exc))`,
and the exceptions behind them are developer English: "no matching weekday within a week —
refusing to guess", "only a ringing alarm can be snoozed", "unknown IANA timezone: 'x'".
The web renders `detail` verbatim (`describeErrorDetail` → `neden: <string>`), so those
sentences are what the owner reads when their alarm does not get set. The matrix files this
BROKEN and trust-breaking, and it is right: a system that answers in the language of its own
stack traces is telling the owner they are the wrong audience for their own assistant.

This module is the one door between an exception and a person:

* `owner_detail()` builds the response body — the SAME shape `VoiceError.to_dict()` already
  produces and the web client already parses, so nothing new has to learn a second shape;
* `owner_sentence()` is the Turkish, from `app.errors.catalog`, with an optional
  caller-supplied sentence for the cases where the domain knows something more specific
  than its class does ("geçmiş bir zaman söyledin");
* `looks_like_developer_text()` is the guard's eye — it recognises a Python exception the
  way a reader would, and `test_no_route_answers_with_a_python_exception` uses it.

The exception's own text is not dropped: it goes to the LOG with the trace id, which is
where it was always useful and never presentable.
"""

from __future__ import annotations

import re
from typing import Any

from app.errors.catalog import describe
from app.logging import get_logger

logger = get_logger("app.errors.owner")

#: Signatures a Python failure leaves in a string. Deliberately conservative: each one is
#: something no Turkish sentence written for the owner would ever contain.
_DEVELOPER_SIGNATURES: tuple[re.Pattern[str], ...] = (
    # "ValueError: ...", "app.voice.errors.VoiceError: ..." - a class name then a colon.
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Failure|Refused)\b\s*:"),
    # A bare exception repr: "KeyError('device_id')"
    re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception)\(.*\)"),
    # A traceback, or a file/line the owner cannot act on.
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"\bFile \"[^\"]+\", line \d+"),
    # A dotted module path: app.alarms.service, sqlalchemy.exc.OperationalError
    re.compile(r"\b(?:app|sqlalchemy|httpx|pydantic|asyncio|fastapi)\.[a-z_]+\.[A-Za-z_]"),
    # An SQL statement, which arrives with SQLAlchemy's own messages.
    re.compile(r"\b(?:SELECT|INSERT INTO|UPDATE|DELETE FROM)\b\s", re.IGNORECASE),
)


def looks_like_developer_text(text: object) -> bool:
    """True when this string carries a Python failure rather than a sentence.

    Used by the guard test AND at runtime by `owner_sentence`, so a caller that passes an
    exception's text as a "specific message" is refused rather than trusted. The two uses
    are the point: the invariant is checked where it is stated and where it is enforced.
    """
    value = text if isinstance(text, str) else ""
    if not value:
        return False
    return any(pattern.search(value) for pattern in _DEVELOPER_SIGNATURES)


def owner_sentence(error_class: str | None, *, specific: str | None = None) -> str:
    """The Turkish the owner reads for this failure.

    ``specific`` is for a domain that knows more than its class does — an alarm that can say
    "geçmiş bir zaman söyledin" rather than "isteği anlayamadım". It is REFUSED when it looks
    like developer text, because that is exactly how a `str(exc)` would sneak back in: a
    handler passing what it thinks is a helpful message which is really the exception.
    """
    if specific and not looks_like_developer_text(specific):
        return specific.strip()
    if specific:
        logger.warning("owner_message_refused_developer_text", error_class=error_class or "")
    return describe(error_class).sentence()


def owner_detail(
    error_class: str,
    *,
    specific: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The body of an owner-facing error response.

    Same shape the voice family has produced since M12 (`error_class` / `message` /
    `details`), which the web client already understands — one shape for the product rather
    than a second one invented here. `details` carries FIELD NAMES and numbers, never text
    from an exception.
    """
    body: dict[str, Any] = {
        "error_class": error_class,
        "message": owner_sentence(error_class, specific=specific),
    }
    if details:
        body["details"] = details
    return body


def log_and_detail(
    error_class: str,
    exc: BaseException,
    *,
    specific: str | None = None,
    details: dict[str, Any] | None = None,
    where: str = "",
) -> dict[str, Any]:
    """Put the exception where it belongs — the log — and answer the owner in Turkish.

    The developer text is not lost: `where` plus the exception type and message go to the
    structured log under this request's trace id, which is the surface that exists for
    diagnosing a failure. What crosses to the owner is a sentence.
    """
    logger.warning(
        "owner_facing_failure",
        error_class=error_class,
        where=where or type(exc).__name__,
        error=f"{type(exc).__name__}: {exc}",
    )
    return owner_detail(error_class, specific=specific, details=details)


__all__ = [
    "log_and_detail",
    "looks_like_developer_text",
    "owner_detail",
    "owner_sentence",
]
