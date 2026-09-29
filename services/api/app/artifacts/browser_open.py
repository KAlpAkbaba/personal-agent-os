"""A report opened as a new tab in the owner's own Chrome, on a device that cannot fetch a
file (ADR-0210).

"Son araştırmayı aç" from the office PC (2026-09-29). That machine is the employer's and is
enrolled WITHOUT the Digital Operator on purpose (ADR-0203): the documents family, and with it
``file.fetch``, rides the Operator flag and is not advertised, so the one open path this system
had ended in ``no_capable_device`` and the owner heard "cihazda açamadım". What that machine
DOES carry is the browser worker, which drives the owner's own Chrome through the enrolment
they made (ADR-0113, ADR-0183) - the same browser the research itself ran in.

The rule, and the whole of it:

* the device the open would go to advertises ``file.fetch`` -> the file path, exactly as
  before (:mod:`app.artifacts.open_service`, untouched: download by single-use token, verify
  the hash, open with the default application). This module is not consulted past its probe;
* it does not, but the device advertises the browser capabilities this needs -> the report's
  HTML render, as a NEW tab in the owner's Chrome: ``browser.session_open`` on the ``owner``
  profile (READ + NAVIGATE only), ``browser.tab_new`` with the URL, ``browser.inspect`` to see
  what loaded, ``browser.session_close`` (the tab stays, the session was only the hand that
  opened it);
* it advertises neither -> the file path, which answers ``capability_missing`` as it always did;
* (ADR-0208) the SESSION's own device can open a report only the browser way while another
  online device could fetch a file -> the browser way, on the session's device: the owner at the
  office does not get the report on the home PC.

What it never does:

* fall back to another profile. ``owner`` is the owner's Chrome and the default session kind is
  the one the worker gates on the enrolment's research grant (the device's to give; the cloud
  never names or reads it: ``test_browser_transfer_contract``); a refusal is
  said in Turkish and nothing else is tried;
* navigate to anything but the URL it minted a moment ago. There is no parameter through which
  a caller names one, and the worker's own destination policy is not asked to bend (a refusal
  of the address is reported, see ``ERROR_DESTINATION_REFUSED``);
* download a file through the browser. Only an ``html`` render opens; a report that has only PDF
  or DOCX is said, not opened;
* send anything to a device other than the one it selected, which is the one it probed.

This module decides a route and runs the steps. It does not select a device (that is
``app.devices.selection`` behind the one ``DeviceActionPort``, whose own ``selection_for`` is
asked, so what is probed and what is done cannot drift) and it does not choose the artifact.
"""

from __future__ import annotations

import uuid
from typing import Any, Final
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.artifacts import service
from app.artifacts.models import RENDER_STATE_VALID
from app.artifacts.open_service import _SPEECH as _OPEN_SPEECH
from app.artifacts.open_service import (
    CAPABILITY_FILE_FETCH,
    ERROR_CAPABILITY_MISSING,
    ERROR_NOT_FOUND,
    OpenOutcome,
)
from app.artifacts.render_view_store import (
    ALLOWED_FORMAT,
    VIEW_PATH,
    RenderViewStore,
    get_render_view_store,
)
from app.devices.capabilities import has_capability
from app.devices.selection import REASON_SESSION_AFFINITY, SelectionResult
from app.logging import get_logger
from app.media.playback_service import (
    CAPABILITY_SESSION_CLOSE,
    CAPABILITY_SESSION_OPEN,
    CAPABILITY_TAB_NEW,
    OWNER_ATTACHED_PROFILE,
    TIMEOUT_SESSION_OPEN_S,
)
from app.routines.dispatch import DeviceActionPort

logger = get_logger("app.artifacts.browser_open")

CAPABILITY_INSPECT: Final = "browser.inspect"
#: What the open needs of the device it goes to. The family marker ``browser.chrome`` satisfies
#: all of them (``app.devices.capabilities.has_capability``), as it does for every browser tool.
BROWSER_CAPABILITIES: Final[tuple[str, ...]] = (
    CAPABILITY_SESSION_OPEN,
    CAPABILITY_TAB_NEW,
    CAPABILITY_INSPECT,
)

TIMEOUT_TAB_S: Final = 60.0
TIMEOUT_INSPECT_S: Final = 20.0
#: ``session_id`` convention, mirroring ``owner-media-`` and ``owner-page-``: valid for the
#: worker (``^[A-Za-z0-9_.:-]+$``, 1-128 characters).
SESSION_PREFIX: Final = "owner-report-"

VIA_BROWSER: Final = "browser"
#: The capability that put the report on the owner's screen, for the receipt's ``path``.
PATH_BROWSER: Final = CAPABILITY_TAB_NEW

ERROR_NO_HTML_RENDER: Final = "no_html_render"
ERROR_NO_ORIGIN: Final = "no_origin"
ERROR_OWNER_BROWSER_MISSING: Final = "owner_browser_missing"
ERROR_OWNER_BROWSER_NOT_AUTHORIZED: Final = "owner_browser_not_authorized"
ERROR_OWNER_BROWSER_UNREACHABLE: Final = "owner_browser_unreachable"
ERROR_DESTINATION_REFUSED: Final = "destination_refused"
ERROR_BROWSER_OPEN_FAILED: Final = "browser_open_failed"
ERROR_PAGE_UNCONFIRMED: Final = "page_unconfirmed"
ERROR_DEVICE_CHANGED: Final = "device_changed"

_SPEECH: Final[dict[str, str]] = {
    ERROR_NO_HTML_RENDER: (
        "Bu çıktının tarayıcıda açılabilecek bir HTML hali yok efendim; "
        "PDF ya da Word dosyasını tarayıcıyla indirmem, açmadım."
    ),
    ERROR_NO_ORIGIN: (
        "Rapor adresini kuramadım efendim; cihazın sunucuya bağlandığı adres bilinmiyor."
    ),
    ERROR_OWNER_BROWSER_MISSING: (
        "Bu bilgisayarda kendi tarayıcınıza bağlı değilim efendim; raporu açamadım. "
        "Chrome yetkilendirmesini bir kez yapmamız gerekiyor."
    ),
    ERROR_OWNER_BROWSER_NOT_AUTHORIZED: (
        "Bu bilgisayardaki tarayıcınız araştırma için yetkilendirilmemiş efendim; "
        "başka bir tarayıcıda açmadım."
    ),
    ERROR_OWNER_BROWSER_UNREACHABLE: (
        "Kendi tarayıcınıza ulaşamadım efendim; Chrome yeniden başlamış olabilir. Raporu açamadım."
    ),
    ERROR_DESTINATION_REFUSED: (
        "Bu bilgisayardaki tarayıcı ajanı sunucunun adresini açmayı reddetti efendim "
        "(özel ağ adresi); raporu tarayıcıda açamadım."
    ),
    ERROR_BROWSER_OPEN_FAILED: "Raporu tarayıcıda açamadım efendim.",
    ERROR_PAGE_UNCONFIRMED: (
        "Sekmeyi açtım efendim ama raporun yüklendiğini doğrulayamadım; ekranda görüyor musunuz?"
    ),
    ERROR_DEVICE_CHANGED: "Komut başka bir cihaza gitti efendim; raporu açmadım.",
}

#: The worker's own vocabulary for why ``session_open`` on the ``owner`` profile said no
#: (BROWSER_CAPABILITIES.md sections 2 and 5), translated once, here.
_SESSION_OPEN_TRANSLATION: Final[dict[str, str]] = {
    # No ``cdp_loopback`` enrolment record.
    "capability_missing": ERROR_OWNER_BROWSER_MISSING,
    # An enrolment that does not carry the research grant (worker.py: the default
    # session kind on the owner profile is gated on it).
    "security_scope_error": ERROR_OWNER_BROWSER_NOT_AUTHORIZED,
    # The enrolled Chrome is not running with its debugging port.
    "dependency_unavailable": ERROR_OWNER_BROWSER_UNREACHABLE,
    "no_capable_device": ERROR_CAPABILITY_MISSING,
}


def speech_for(error_class: str) -> str:
    return _SPEECH.get(error_class) or _OPEN_SPEECH[error_class]


def route_for(device_action: Any) -> SelectionResult | None:
    """The device this open goes to by the BROWSER, or ``None`` for every case that is the
    file path (the answer whenever this cannot be sure of anything else - a port that cannot be
    asked, a probe that raises - because it is the path that has always run).

    ``selection_for`` is the port's own probe over the same ``_select_for`` its ``run`` uses
    (ADR-0208, ADR-0209), so the device probed here is the device the steps below go to."""
    try:
        selection_for = getattr(device_action, "selection_for", None)
        if not callable(selection_for):
            return None
        fetch = selection_for(CAPABILITY_FILE_FETCH)
        browser = selection_for(CAPABILITY_SESSION_OPEN)
    except Exception:  # noqa: BLE001 - "I cannot tell" is the old path, never a new failure
        return None
    if browser is None:
        return None
    if not all(has_capability(browser.device.capabilities, c) for c in BROWSER_CAPABILITIES):
        return None
    if fetch is None:
        # Nothing that is online can fetch a file, so the file path could only refuse.
        return browser
    if browser.reason == REASON_SESSION_AFFINITY and fetch.device.id != browser.device.id:
        # ADR-0208: the session's own device can do this only the browser way. Taking the
        # file path would act on the other machine while the owner sits at this one.
        return browser
    return None


def origin_for(device_id: uuid.UUID, configured: str) -> str:
    """The origin the owner's browser is sent to. The operator's setting first
    (``PAGENTOS_ARTIFACT_DOWNLOAD_ORIGIN``): it is explicit. Else the origin THIS device dialled
    for its own connection, recorded at the handshake - which is a fact about that device, but
    also the ``Host`` header the edge forwards, and the edge's ``$host`` may not carry the port.
    Empty when neither is known: a browser is never sent a relative URL."""
    candidate = (configured or "").strip().rstrip("/")
    if not candidate:
        try:
            from app.devices.status import get_status_registry

            candidate = (get_status_registry().dial_origin(device_id) or "").strip().rstrip("/")
        except Exception:  # noqa: BLE001 - a registry that cannot answer is "unknown"
            candidate = ""
    parts = urlsplit(candidate) if candidate else None
    if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    if parts.username is not None or parts.path not in ("", "/") or parts.query:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


class _DeviceMovedError(Exception):
    """The device the report was routed to is no longer the one the port would send to."""


def _still_on(device_action: Any, capability: str, expected: uuid.UUID) -> bool:
    """Whether ``device_action`` would send ``capability`` to ``expected`` right now. A port
    that cannot be probed answers True: :func:`route_for` already required one that can, so
    this only ever says no on a probe that ran and named another device (or none)."""
    probe = getattr(device_action, "selection_for", None)
    if not callable(probe):
        return True
    try:
        chosen = probe(capability)
    except Exception:  # noqa: BLE001 - a probe that fails is "cannot tell", not "moved"
        return True
    return chosen is not None and chosen.device.id == expected


def _spoken_device(selection: SelectionResult) -> str:
    aliases = [a for a in selection.device.aliases if str(a).strip()]
    word = str(aliases[0]).strip() if aliases else selection.device.name
    return word[:1].upper() + word[1:]


def _scrub(text: object, token: str) -> str:
    """A device's message with the bearer token removed: a worker may repeat the URL it was
    given, and the token must not be readable in a receipt, a ledger row or a log line."""
    return str(text or "").replace(token, "[t]")[:200]


def _refused(
    error_class: str,
    selection: SelectionResult,
    *,
    artifact_id: uuid.UUID,
    fmt: str | None = None,
) -> OpenOutcome:
    return OpenOutcome(
        False,
        None,
        speech_for(error_class),
        error_class,
        artifact_id=str(artifact_id),
        format=fmt,
        via=VIA_BROWSER,
        device_id=str(selection.device.id),
        device=selection.device.name,
        spoken_device=_spoken_device(selection),
    )


def open_in_owner_browser(
    session: Session,
    device_action: DeviceActionPort,
    selection: SelectionResult,
    *,
    artifact_id: uuid.UUID,
    fmt: str | None,
    configured_origin: str,
    idempotency_prefix: str,
    view_store: RenderViewStore | None = None,
) -> OpenOutcome:
    """Open ``artifact_id``'s HTML render as a new tab in the owner's Chrome on
    ``selection.device`` (which :func:`route_for` returned)."""
    artifact = service.get_artifact(session, artifact_id)
    version = service.get_current_version(session, artifact.id) if artifact is not None else None
    if artifact is None or version is None:
        return OpenOutcome(
            False,
            None,
            _OPEN_SPEECH[ERROR_NOT_FOUND],
            ERROR_NOT_FOUND,
            artifact_id=str(artifact_id),
        )
    if fmt and fmt != ALLOWED_FORMAT:
        # Named, so answered as named: never silently swapped for the HTML one.
        return _refused(ERROR_NO_HTML_RENDER, selection, artifact_id=artifact_id, fmt=fmt)
    row = service.get_render(session, version.id, ALLOWED_FORMAT)
    if row is None or row.state != RENDER_STATE_VALID:
        return _refused(ERROR_NO_HTML_RENDER, selection, artifact_id=artifact_id)
    origin = origin_for(selection.device.id, configured_origin)
    if not origin:
        return _refused(ERROR_NO_ORIGIN, selection, artifact_id=artifact_id, fmt=row.format)

    store = view_store or get_render_view_store()
    handle = store.put(artifact_id=artifact_id, fmt=row.format, content_hash=row.content_hash)
    url = f"{origin}{handle.path()}"
    token = handle.token
    expected = selection.device.id
    session_id = f"{SESSION_PREFIX}{uuid.uuid4().hex[:16]}"
    steps: list[str] = []

    def refused(error_class: str) -> OpenOutcome:
        return _refused(error_class, selection, artifact_id=artifact_id, fmt=row.format)

    def run(capability: str, payload: dict[str, Any], name: str, timeout_s: float):
        # Before every command, the probe the port's own ``run`` would answer: the device is
        # still the one the report was routed to. A device that went offline in between would
        # otherwise make the ordinary rule pick ANOTHER machine, and the tab would open there.
        if not _still_on(device_action, capability, expected):
            raise _DeviceMovedError(capability)
        result = device_action.run(
            capability=capability,
            payload=payload,
            idempotency_key=f"{idempotency_prefix}:{name}",
            timeout_s=timeout_s,
        )
        steps.append(capability)
        return result

    def elsewhere(result: Any) -> bool:
        acted = getattr(result, "device_id", None)
        return acted is not None and acted != expected

    try:
        opened = run(
            CAPABILITY_SESSION_OPEN,
            {
                "session_id": session_id,
                # The owner's attached Chrome. The session kind is left at the worker's
                # default (research): that is the one the worker gates on the enrolment's
                # research grant, so a browser the owner did not authorise for
                # this is refused by the device itself, not by our reading of a record.
                "profile": OWNER_ATTACHED_PROFILE,
                "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
                "channel": "chrome",
            },
            "session_open",
            TIMEOUT_SESSION_OPEN_S,
        )
    except _DeviceMovedError:
        return refused(ERROR_DEVICE_CHANGED)
    if not opened.ok:
        error_class = _SESSION_OPEN_TRANSLATION.get(opened.error_class, ERROR_BROWSER_OPEN_FAILED)
        logger.warning(
            "artifact_browser_open_session_refused",
            artifact_id=str(artifact_id),
            device=selection.device.name,
            device_error=opened.error_class,
            device_message=_scrub(opened.message, token),
        )
        return refused(error_class)
    try:
        if elsewhere(opened):
            return refused(ERROR_DEVICE_CHANGED)
        tab = run(
            CAPABILITY_TAB_NEW,
            {"session_id": session_id, "url": url},
            "tab_new",
            TIMEOUT_TAB_S,
        )
        if not tab.ok or elsewhere(tab):
            message = _scrub(tab.message, token)
            error_class = ERROR_DEVICE_CHANGED if elsewhere(tab) else ERROR_BROWSER_OPEN_FAILED
            if (
                not tab.ok
                and tab.error_class == "security_scope_error"
                and "destination" in message.lower()
            ):
                # The worker's own URL policy (BROWSER_CAPABILITIES.md section 5a) refuses a
                # private or tailnet address. Honoured, never bypassed: said.
                error_class = ERROR_DESTINATION_REFUSED
            logger.warning(
                "artifact_browser_open_tab_refused",
                artifact_id=str(artifact_id),
                device=selection.device.name,
                error_class=error_class,
                device_error=tab.error_class,
                device_message=message,
            )
            return refused(error_class)
        seen = run(CAPABILITY_INSPECT, {"session_id": session_id}, "inspect", TIMEOUT_INSPECT_S)
    except _DeviceMovedError:
        return refused(ERROR_DEVICE_CHANGED)
    finally:
        # The tab is the owner's now; the session was only the hand that opened it. A close
        # that fails - or that would now go to another machine, which is refused like any
        # other step - is a stray attach, never the owner's answer.
        try:
            run(
                CAPABILITY_SESSION_CLOSE,
                {"session_id": session_id},
                "session_close",
                TIMEOUT_SESSION_OPEN_S,
            )
        except Exception:  # noqa: BLE001 - cleanup must never replace the real outcome
            logger.warning("artifact_browser_open_close_failed", session_id=session_id)

    seen_result = seen.result if seen.ok and isinstance(seen.result, dict) else {}
    on_our_page = urlsplit(str(seen_result.get("url") or "")).path == VIEW_PATH
    title = str(seen_result.get("title") or "").strip() or None
    device_fields = {
        "via": VIA_BROWSER,
        "device_id": str(expected),
        "device": selection.device.name,
        "spoken_device": _spoken_device(selection),
    }
    if not (seen.ok and seen_result.get("page_kind") == "ok" and on_our_page):
        # ``tab_new`` succeeding means a tab exists; only what ``inspect`` read says the
        # report is in it. A weaker result is never reported as a stronger one.
        return OpenOutcome(
            True,
            None,
            speech_for(ERROR_PAGE_UNCONFIRMED),
            ERROR_PAGE_UNCONFIRMED,
            artifact_id=str(artifact_id),
            format=row.format,
            **device_fields,
        )
    where = f"{_spoken_device(selection)} cihazında "
    logger.info(
        "artifact_browser_opened",
        artifact_id=str(artifact_id),
        device=selection.device.name,
        steps=steps,
    )
    return OpenOutcome(
        True,
        "opened",
        f"{where}raporu tarayıcıda açtım efendim.",
        None,
        window_title=title,
        artifact_id=str(artifact_id),
        format=row.format,
        **device_fields,
    )


__all__ = [
    "BROWSER_CAPABILITIES",
    "ERROR_BROWSER_OPEN_FAILED",
    "ERROR_DESTINATION_REFUSED",
    "ERROR_DEVICE_CHANGED",
    "ERROR_NO_HTML_RENDER",
    "ERROR_NO_ORIGIN",
    "ERROR_OWNER_BROWSER_MISSING",
    "ERROR_OWNER_BROWSER_NOT_AUTHORIZED",
    "ERROR_OWNER_BROWSER_UNREACHABLE",
    "ERROR_PAGE_UNCONFIRMED",
    "PATH_BROWSER",
    "VIA_BROWSER",
    "open_in_owner_browser",
    "origin_for",
    "route_for",
    "speech_for",
]
