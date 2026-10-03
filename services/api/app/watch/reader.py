"""The watch's cloud reader: one reading, on the cloud worker or not at all.

A reading is a SCHEDULED job (ADR-0213): ``rule.decide`` is asked with ``JobKind.SCHEDULED``
and ``acting=False``, whose chain is the cloud alone - a home machine that is online is never
a fallback, and when the cloud is down the reading fails with no command sent to anyone.
``wiring.choose`` is deliberately not called: it writes ``execution.*`` ledger rows on every
call (240 readings a day would bury the narrative); the reading's own row in
``watch_readings`` is the record. The ADR-0257 setting (``routines_execution_rule_enabled``)
is a routine's switch and is not read here.

The destination is checked again at every reading (a name can be repointed into the tailnet
after the watch was made - DNS rebinding); the gateway checks it once more before sending.
The page text comes back in memory (``Observation.text``) for the comparison and is never
stored.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol

from sqlalchemy.orm import Session

from app.devices.types import DeviceView
from app.execution import rule, wiring
from app.execution.rule import Availability, Decision, ExecutionRequest, JobKind, Target
from app.logging import get_logger
from app.research.browser_gateway import BrowserDispatchError, PageDigest
from app.research.destination import DestinationPolicyError, validate_fetch_target

logger = get_logger("app.watch.reader")

#: What the cloud worker must serve for a reading.
CAPABILITIES: Final = ("browser.session_open", "browser.fetch_evidence")

REASON_CLOUD_OFFLINE: Final = "Bulut şu anda çevrimiçi değil."
REASON_FORBIDDEN: Final = "Bu adres herkese açık bir sayfa değil; okunmadı."
REASON_SELECTOR_EMPTY: Final = "Seçici sayfada bir şey bulmadı."
REASON_NO_TEXT: Final = "Sayfa metni okunamadı."
_PAGE_KIND_REASONS: Final = {
    "auth_wall": "Sayfa giriş istiyor.",
    "captcha": "Sayfa robot doğrulaması istiyor.",
    "challenge": "Sayfa robot doğrulaması istiyor.",
    "blocked": "Sayfa erişimi engelledi.",
}
_ERROR_REASONS: Final = {
    "security_scope_error": REASON_FORBIDDEN,
    "timeout": "Sayfa zamanında açılmadı.",
    "capability_missing": "Bulut tarayıcısı bu okumayı yapamıyor.",
    "validation_error": "Okuma isteği geçersizdi.",
}


@dataclass(frozen=True, slots=True)
class Observation:
    """One reading's result. ``text`` is in memory only, for the comparison."""

    ok: bool
    text_sha256: str | None = None
    text: str = ""
    reason_tr: str = ""
    error_class: str | None = None


class PageGateway(Protocol):
    def fetch_page_digest(self, url: str, *, selector: str | None = None) -> PageDigest: ...

    def close_session(self) -> None: ...


class Reader(Protocol):
    def read(
        self, db: Session, *, url: str, selector: str | None, watch_id: uuid.UUID
    ) -> Observation: ...


def _failed(reason: str, error_class: str) -> Observation:
    return Observation(ok=False, reason_tr=reason, error_class=error_class)


def _cloud_view(views: list[DeviceView]) -> DeviceView | None:
    """The online cloud device that serves every capability a reading needs, or None."""
    probe = Decision(JobKind.SCHEDULED, "selected", Target.CLOUD, (Target.CLOUD,), (), "watch")
    return wiring.device_for(probe, views, capabilities=CAPABILITIES)


def _from_digest(digest: PageDigest) -> Observation:
    kind = digest.page_kind or "ok"
    if kind in _PAGE_KIND_REASONS:
        return _failed(_PAGE_KIND_REASONS[kind], kind)
    if digest.http_status is not None and digest.http_status >= 400:
        return _failed(f"Sayfa açılamadı (HTTP {digest.http_status}).", "http_error")
    if digest.selector_matched is False:
        return _failed(REASON_SELECTOR_EMPTY, "selector_unmatched")
    if digest.text_sha256 is None:
        return _failed(REASON_NO_TEXT, "no_text")
    return Observation(ok=True, text_sha256=digest.text_sha256, text=digest.excerpt)


class CloudReader:
    """Reads one page on the cloud worker through ``DeviceBrowserGateway``.

    ``views`` gives the registry snapshot for a session; ``gateway_factory(device_id,
    task_id)`` builds the gateway for the chosen cloud device (production: a
    ``DeviceBrowserGateway`` on the research profile). Every reading has its own browser
    session id, so its idempotency keys are new and the session is closed after it.
    """

    def __init__(
        self,
        *,
        views: Callable[[Session], list[DeviceView]],
        gateway_factory: Callable[[uuid.UUID, str], Any],
    ) -> None:
        self._views = views
        self._gateway_factory = gateway_factory

    def read(
        self, db: Session, *, url: str, selector: str | None, watch_id: uuid.UUID
    ) -> Observation:
        try:
            validate_fetch_target(url)
        except DestinationPolicyError:
            return _failed(REASON_FORBIDDEN, "security_scope_error")
        cloud = _cloud_view(list(self._views(db)))
        decision = rule.decide(
            ExecutionRequest(
                job_kind=JobKind.SCHEDULED,
                availability=Availability(cloud_online=cloud is not None),
                url=url,
                acting=False,
            )
        )
        if cloud is None or decision.outcome != "selected" or decision.target is not Target.CLOUD:
            return _failed(REASON_CLOUD_OFFLINE, wiring.NO_CAPABLE_DEVICE)
        task_id = f"watch-{watch_id.hex[:12]}-{uuid.uuid4().hex[:12]}"
        gateway = self._gateway_factory(cloud.id, task_id)
        try:
            digest = gateway.fetch_page_digest(url, selector=selector)
        except BrowserDispatchError as exc:
            logger.info("watch_read_failed", watch_id=str(watch_id), error=exc.error_class)
            reason = _ERROR_REASONS.get(exc.error_class, "Sayfa okunamadı.")
            return _failed(reason, exc.error_class)
        finally:
            try:
                gateway.close_session()
            except Exception:  # noqa: BLE001 - best effort; the reading is already decided
                pass
        return _from_digest(digest)
