"""The Digital Operator's voice tools (docs/M19_DIGITAL_OPERATOR_SPEC.md §3, §4).

Six tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_operator_tools`), the same discipline ``tools_ambient``/``tools_evolution``
already establish for their own families. Every ACTION returns an
``app.actions.receipt.ActionReceipt``-shaped result whose ``speech`` is read verbatim;
``operator.window_control`` and ``operator.type`` may instead answer a clarification
(``status: "needs_clarification"``) when there is no window to act on or nothing was said
to type — never a guess.

What these tools deliberately do NOT do: pick a window, an app id or a shell reading from
the model's own free-form argument when the owner's WORDS already settled it. Every one of
``application`` / ``window_ref`` / ``text_to_type`` / ``shell_query`` on the turn's resolved
intent (``app.voice.intents``) is preferred over the same-named model argument — the "the
owner's words win" rule ADR-0079 §7 and the alarm snooze minutes already established.

The device port and the operator runtime both come from ``ctx.live`` (``device_action``,
``operator`` — ``app.main.create_app`` registers both on the SAME objects; a test injects
fakes the same way, docs/M18_ACTION_CONTRACT.md §4).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.ledger.vocabulary import SUBSYSTEM_OPERATOR
from app.logging import get_logger
from app.operator import adapters as app_adapters
from app.operator import allowlists as app_allowlists
from app.operator import capabilities as operator_capabilities
from app.operator import focus as focus_module
from app.operator.capabilities import (
    CAPABILITY_APP_CLOSE,
    CAPABILITY_APP_OPEN,
    CAPABILITY_CANCEL,
    CAPABILITY_INSPECT,
    CAPABILITY_KEY,
    CAPABILITY_POINTER,
    CAPABILITY_POINTER_SESSION,
    CAPABILITY_PROCESS,
    CAPABILITY_SCREENSHOT,
    CAPABILITY_SEE,
    CAPABILITY_SERVICE,
    CAPABILITY_SHELL,
    CAPABILITY_STATUS,
    CAPABILITY_TYPE,
    CAPABILITY_UI,
    CAPABILITY_WINDOW_CONTROL,
    PLAN_BY_POINTER_ACTION,
    PLAN_BY_PROCESS_ACTION,
    PLAN_BY_READ_MODE,
    PLAN_BY_SERVICE_ACTION,
    PLAN_BY_SHELL_QUERY,
    PLAN_BY_UI_ACTION,
    PLAN_BY_WINDOW_ACTION,
    PLAN_CLOSE_APPLICATION,
    PLAN_OPEN_APPLICATION,
    PLAN_POINTER_SESSION_BEGIN,
    PLAN_PRESS_KEY,
    PLAN_PRESS_SHORTCUT,
    PLAN_TYPE_TEXT,
)
from app.operator.models import FOCUS_KIND_WINDOW
from app.operator.plans import (
    APP_ALLOWLIST,
    MAX_REPEAT,
    POINTER_ACTIONS,
    POINTER_SPACES,
    UI_ACTIONS,
    UI_EXPECTATIONS,
    activate_window,
    close_app,
    close_window,
    is_browser_image,
    maximize_window,
    minimize_window,
    move_window,
    open_application,
    parse_ipv4,
    pointer_session_begin,
    press_key,
    press_shortcut,
    previous_window,
    process_list,
    process_stop,
    resize_window,
    resolve_app_alias,
    restore_window,
    service_restart,
    service_status,
    tree_text,
    ui_invoke,
    ui_read,
    ui_select,
    ui_set_value,
    valid_key,
    valid_shortcut,
)
from app.operator.plans import pointer as build_pointer_steps
from app.operator.plans import shell_query as build_shell_query_steps
from app.operator.plans import type_text as build_type_text_steps
from app.operator.service import Plan
from app.operator.task import STATUS_SUCCEEDED
from app.operator.vision import DEFAULT_QUESTION_TR, VisionError
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import contains_secret_reference, normalize_transcript, turkish_casefold
from app.voice.realtime_sessions import pointer_session

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

# ------------------------------------------------------------------ tool names
#
# The names themselves live in ``app.operator.capabilities`` — one declaration read by the
# registry here, by the service that mints the receipt, and by the structural test that
# keeps the two the same string. These aliases keep the ``TOOL_*`` spelling the rest of
# this module and its tests already use.

TOOL_APP_OPEN: Final = CAPABILITY_APP_OPEN
TOOL_WINDOW_CONTROL: Final = CAPABILITY_WINDOW_CONTROL
TOOL_TYPE: Final = CAPABILITY_TYPE
TOOL_SHELL: Final = CAPABILITY_SHELL
TOOL_CANCEL: Final = CAPABILITY_CANCEL
TOOL_STATUS: Final = CAPABILITY_STATUS
TOOL_SCREENSHOT: Final = CAPABILITY_SCREENSHOT
TOOL_KEY: Final = CAPABILITY_KEY
TOOL_POINTER: Final = CAPABILITY_POINTER
TOOL_UI: Final = CAPABILITY_UI
TOOL_INSPECT: Final = CAPABILITY_INSPECT
TOOL_SEE: Final = CAPABILITY_SEE
TOOL_APP_CLOSE: Final = CAPABILITY_APP_CLOSE
TOOL_PROCESS: Final = CAPABILITY_PROCESS
TOOL_SERVICE: Final = CAPABILITY_SERVICE
TOOL_POINTER_SESSION: Final = CAPABILITY_POINTER_SESSION

OPERATOR_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_APP_OPEN,
    TOOL_WINDOW_CONTROL,
    TOOL_TYPE,
    TOOL_SHELL,
    TOOL_CANCEL,
    TOOL_STATUS,
    TOOL_SCREENSHOT,
    TOOL_KEY,
    TOOL_POINTER,
    TOOL_UI,
    TOOL_INSPECT,
    TOOL_SEE,
    TOOL_APP_CLOSE,
    TOOL_PROCESS,
    TOOL_SERVICE,
    TOOL_POINTER_SESSION,
)

#: B29: the UI Automation family's sentences and refusals.
SPEECH_NO_TARGET: Final = "Hangi düğme ya da alan?"
SPEECH_NO_VALUE: Final = "Ne yazmamı istersiniz?"
SPEECH_NO_ITEM: Final = "Hangi öğeyi seçeyim?"
SPEECH_UI_INVOKED_TR: Final = "{name} düğmesine bastım efendim."
SPEECH_UI_SET_TR: Final = "Değeri yazdım efendim."
SPEECH_UI_SELECTED_TR: Final = "{item} seçildi efendim."
SPEECH_UI_NOT_FOUND: Final = "Bunu pencerede bulamadım efendim."
SPEECH_UI_UNVERIFIED_TR: Final = "{name} düğmesine bastım ama bir sonuç göremedim efendim."
SPEECH_UI_FAILURE: Final = "Arayüz eylemini yapamadım efendim."
SPEECH_READ_EMPTY: Final = "Bu alanda metin yok efendim."
SPEECH_READ_TR: Final = "Şöyle yazıyor: {text}"
SPEECH_INSPECT_TR: Final = "Pencerede {count} öğe gördüm efendim; başlıcaları: {names}."
SPEECH_NO_VISION: Final = (
    "Görüntü anlamlandırma sağlayıcısı tanımlı değil efendim; ekranı tarif edemiyorum."
)
SPEECH_VISION_FAILED: Final = "Ekranı tarif edemedim efendim."
ERROR_UI_TARGET_NOT_FOUND: Final = "ui_target_not_found"
ERROR_NO_VISION_PROVIDER: Final = "dependency_unavailable"
MAX_READ_CHARS: Final = 600

#: B28 req 107: the pointer is the LAST rung of spec §2's ladder (API -> DOM -> UI
#: Automation -> keyboard -> visual -> raw coordinates). ``operator.pointer`` therefore
#: refuses a move or a click that does not say it is the last resort - the model has to
#: state that the UI Automation and keyboard rungs were tried or do not apply, and the
#: statement travels in the receipt. Scrolling is the one exception: it targets the
#: window's own centre, never an element, so there is no higher rung to prefer.
ERROR_COORDINATE_NOT_LAST_RESORT: Final = "coordinate_not_last_resort"
SPEECH_COORDINATE_POLICY: Final = (
    "Koordinatla tıklamak son çare efendim; önce arayüz ağacını ya da klavyeyi denerim."
)
#: The companion's own refusal when the foreground moved between OBSERVE and ACT
#: (``FocusGuard``, spec §1 invariant 2). Named here so the sentence can say it.
ERROR_FOCUS_MISMATCH: Final = "focus_mismatch"
SPEECH_FOCUS_LOST: Final = "Pencere önden çekildi efendim; gönderimi durdurdum."
SPEECH_KEY_SUCCESS_TR: Final = "{key} tuşuna {times}bastım efendim."
SPEECH_SHORTCUT_SUCCESS_TR: Final = "{keys} kısayolunu {times}gönderdim efendim."
#: ADR-0195: a count past the plan's bound, refused in words rather than done once.
SPEECH_TOO_MANY_REPEATS_TR: Final = "Bir cümlede en fazla {max} kere yapabilirim efendim."
SPEECH_KEY_FAILURE: Final = "Tuşa basamadım efendim."
SPEECH_NO_KEY: Final = "Hangi tuş?"
SPEECH_POINTER_SUCCESS_TR: Final = {
    "move": "İmleci taşıdım efendim.",
    "click": "Tıkladım efendim.",
    "double_click": "Çift tıkladım efendim.",
    "right_click": "Sağ tıkladım efendim.",
    "scroll": "Kaydırdım efendim.",
}
SPEECH_POINTER_FAILURE: Final = "İşaretçi eylemini yapamadım efendim."
SPEECH_SCROLL_REPEATED_TR: Final = "{count} kere kaydırdım efendim."
#: Turkish for the key names, for the sentence ("Enter tuşuna bastım").
KEY_TR: Final[dict[str, str]] = {
    "enter": "Enter",
    "escape": "Escape",
    "tab": "Tab",
    "backspace": "Geri",
    "delete": "Sil",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Page Up",
    "pagedown": "Page Down",
    "up": "Yukarı ok",
    "down": "Aşağı ok",
    "left": "Sol ok",
    "right": "Sağ ok",
    "space": "Boşluk",
}
#: "Aşağı kaydır" = three notches towards the user (negative), "yukarı" away (positive) -
#: the companion's own sign convention (``IInputSynthesizer.Scroll``).
SCROLL_NOTCHES_BY_DIRECTION: Final[dict[str, int]] = {"down": -3, "up": 3}
#: Where a spoken scroll lands when the device's window list carries no rect: a point
#: inside any real window rather than its corner.
SCROLL_FALLBACK_POINT: Final = (50, 50)

#: B27 req 735: the device operation ``operator.screenshot`` asks for
#: (docs/M19_DIGITAL_OPERATOR_SPEC.md §2: ``{window_id?, format}`` -> ``{width, height,
#: png_base64}``, never persisted by the companion). Same string as the agent's
#: ``ProtocolConstants.ScreenCapture``.
DEVICE_SCREEN_CAPTURE: Final = "screen.capture"
TIMEOUT_SCREEN_CAPTURE_S: Final = 20.0
#: Where the bytes go when there is an object store to put them in: the receipt carries
#: the KEY and the hash, never the image - a ledger row is not a place for two megabytes
#: of base64, and ``app.presence.observations`` already refuses image-shaped keys.
SCREENSHOT_KEY_PREFIX: Final = "screenshots"
SPEECH_SCREENSHOT_NO_DEVICE: Final = (
    "Bağlı bilgisayar ekran görüntüsü yeteneği bildirmiyor efendim."
)
SPEECH_SCREENSHOT_FAILED: Final = "Ekran görüntüsünü alamadım efendim."
SPEECH_SCREENSHOT_UNVERIFIED: Final = (
    "Ekran görüntüsünü alamadım efendim; cihaz bir görüntü döndürmedi."
)

#: The ``action`` values ``operator.window_control`` accepts — the keys of the declared
#: action -> plan table, so an action this tool takes and a plan the ledger can name are
#: the same set by construction rather than by two lists agreeing.
_WINDOW_ACTIONS: Final[tuple[str, ...]] = tuple(sorted(PLAN_BY_WINDOW_ACTION))
_INTENT_TO_WINDOW_ACTION: Final[dict[str, str]] = {
    "window_close": "close",
    "window_maximize": "maximize",
    "window_minimize": "minimize",
    "window_restore": "restore",
    "window_previous": "previous",
}

#: Turkish names for the allowlisted app ids (spec §2's ``app.launch`` allowlist) - from
#: the shared contract since B30, so the sentence names what the device would launch.
_APP_TR_NAMES: Final[dict[str, str]] = dict(app_allowlists.APP_NAMES_TR)

_WINDOW_SUCCESS_TR: Final[dict[str, str]] = {
    "close": "Pencereyi kapattım",
    "maximize": "Pencereyi büyüttüm",
    "minimize": "Pencereyi küçülttüm",
    "restore": "Pencereyi eski haline getirdim",
    "activate": "Pencereyi öne getirdim",
    "previous": "Önceki pencereye döndüm",
    "move": "Pencereyi taşıdım",
    "resize": "Pencereyi boyutlandırdım",
}
_WINDOW_FAILURE_TR: Final[dict[str, str]] = {
    "close": "Pencereyi kapatamadım",
    "maximize": "Pencereyi büyütemedim",
    "minimize": "Pencereyi küçültemedim",
    "restore": "Pencereyi eski haline getiremedim",
    "activate": "Pencereyi öne getiremedim",
    "previous": "Önceki pencereye dönemedim",
    "move": "Pencereyi taşıyamadım",
    "resize": "Pencereyi boyutlandıramadım",
}

#: B30 req 118-122: the shell's third query and the process/service sentences.
SPEECH_WHOAMI_FAILURE: Final = "Kullanıcı adını okuyamadım efendim."
SPEECH_NO_PROCESS: Final = "Hangi uygulamayı sonlandırayım?"
SPEECH_NO_SERVICE: Final = "Hangi servis?"
SPEECH_PROCESS_POLICY: Final = (
    "Bu süreci sonlandırmam politikaya aykırı efendim; yalnız izin listesindeki "
    "uygulamaları kapatırım."
)
SPEECH_PROCESS_LIST_FAILURE: Final = "Süreçleri okuyamadım efendim."
SPEECH_SERVICE_POLICY: Final = (
    "Bu servisi yeniden başlatmam politikaya aykırı efendim; izin listesinde yok."
)
SPEECH_SERVICE_ELEVATION: Final = (
    "Servisi yeniden başlatmak yönetici yetkisi istiyor efendim; bunu siz onaylamalısınız."
)
SERVICE_STATE_TR: Final[dict[str, str]] = {
    "running": "çalışıyor",
    "stopped": "durmuş",
    "paused": "duraklatılmış",
    "start pending": "başlıyor",
    "stop pending": "duruyor",
}
#: The Turkish names the owner uses for a few Windows services -> the service name.
SERVICE_ALIASES_TR: Final[dict[str, str]] = {
    "yazdırma": "Spooler",
    "yazdirma": "Spooler",
    "yazıcı": "Spooler",
    "yazici": "Spooler",
    "spooler": "Spooler",
    "güncelleme": "wuauserv",
    "guncelleme": "wuauserv",
    "windows update": "wuauserv",
    "bluetooth": "bthserv",
    "ses": "Audiosrv",
    "zaman": "W32Time",
}

SPEECH_NO_OPERATOR_AUTHORITY: Final = "Bu bilgisayarda operatör yetkisi yok efendim."
SPEECH_SECRET_REFUSED: Final = "Şifreleri ben yazmam efendim."
SPEECH_NO_TEXT: Final = "Ne yazmamı istersiniz?"
SPEECH_NO_WINDOW: Final = "Hangi pencere?"
SPEECH_NO_PREVIOUS_WINDOW: Final = "Dönebileceğim önceki bir pencere yok efendim."
SPEECH_NOTHING_RUNNING: Final = "Şu anda çalışan bir işlem yok efendim."
SPEECH_CANCELLED: Final = "İptal ettim efendim."
SPEECH_NOT_DOING_ANYTHING: Final = "Şu anda bir şey yapmıyorum efendim."
SPEECH_TYPE_SUCCESS: Final = "Yazdım efendim."
#: Only for a run that REACHED the keyboard and could not read the text back afterwards.
#: A plan that never got that far says which step stopped it instead — telling the owner
#: "metni doğrulayamadım" when no key was ever pressed sends them looking in the wrong
#: place, which is exactly what happened on 2026-09-09.
SPEECH_TYPE_FAILURE: Final = "Yazamadım efendim; metni doğrulayamadım."
SPEECH_TYPE_NOT_ATTEMPTED: Final = "Yazamadım efendim; pencereyi öne getiremedim."
SPEECH_IP_FAILURE: Final = "IP adresini okuyamadım efendim."
SPEECH_HOSTNAME_FAILURE: Final = "Bilgisayarın adını okuyamadım efendim."
_SHELL_FAILURE_TR: Final[dict[str, str]] = {
    "ip": SPEECH_IP_FAILURE,
    "hostname": SPEECH_HOSTNAME_FAILURE,
    "whoami": SPEECH_WHOAMI_FAILURE,
}

logger = get_logger("app.voice.realtime_sessions.tools_operator")

ERROR_UNKNOWN_APPLICATION: Final = "unknown_application"
ERROR_SECRET_REFUSED: Final = "secret_refused"

#: ``w-<positive long handle>-<ulong creation tick>`` — the device's own window id shape.
_WINDOW_ID_SHAPE: Final = re.compile(r"^w-(?P<handle>[0-9]+)-(?P<tick>[0-9]+)$")
_LONG_MAX: Final = 2**63 - 1
_ULONG_MAX: Final = 2**64 - 1


def _speech_no_window_named(db: Session, name: str, live: list[dict[str, Any]]) -> str:
    """No window carries that title. Name what IS open rather than asking blindly: the
    owner can then point at one of them.

    ``live`` is the DEVICE's own list, already fetched by the caller — the desktop as it is
    now, including windows this operator never opened. It is preferred over the remembered
    stack, and the caller asks for it ONCE per resolution.
    """
    known = [title for title in (str(w.get("title") or "") for w in live) if title] or [
        entry.label for entry in focus_module.stack(db, FOCUS_KIND_WINDOW) if entry.label
    ]
    if not known:
        return SPEECH_NO_WINDOW
    listed = ", ".join(dict.fromkeys(known))
    return f"'{name}' diye bir pencere görmüyorum efendim; açık olanlar: {listed}."


def _speech_ambiguous_window(matched: list[tuple[str, str]]) -> str:
    titles = ", ".join(dict.fromkeys(title for _id, title in matched if title))
    return f"Hangisi efendim: {titles}?"


#: Applications whose ``app.launch`` hands the request to a running instance and exits at
#: once, without bringing that instance forward (``plans.open_application`` measured the pid
#: mismatch; 2026-09-19 measured the window staying behind). For these, a window already on
#: the desktop is the thing to activate, never something to launch again.
_HANDOFF_APPS: Final[frozenset[str]] = frozenset({"chrome", "msedge"})


def _window_already_open(ctx: ToolContext, canonical: str) -> str | None:
    """The id of a window ``canonical`` already has on the desktop, for the hand-off
    applications only; ``None`` when there is none (or no device to ask)."""
    if canonical not in _HANDOFF_APPS:
        return None
    lister = _window_lister(ctx)
    image = app_allowlists.APP_IMAGES.get(canonical, "").strip().lower()
    if lister is None or not image:
        return None
    for window in lister():
        if not isinstance(window, dict):
            continue
        seen = str(window.get("image") or "").strip().lower()
        window_id = window.get("window_id")
        if (
            (seen == image or seen.endswith("/" + image))
            and isinstance(window_id, str)
            and window_id
        ):
            return window_id
    return None


def _window_lister(ctx: ToolContext) -> Callable[[], list[dict[str, Any]]] | None:
    """A closure that asks the DEVICE for its open windows, or ``None`` when there is no
    device to ask. Called at most once per resolution, and only when a spoken name matched
    nothing this operator already remembers."""
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return None

    def _list() -> list[dict[str, Any]]:
        result = device_action.run(
            capability="window.list",
            payload={},
            idempotency_key=f"window-list-{uuid.uuid4()}",
            timeout_s=10.0,
        )
        if not getattr(result, "ok", False):
            return []
        windows = result.result.get("windows") if isinstance(result.result, dict) else None
        return list(windows) if isinstance(windows, list) else []

    return _list


def _once(
    lister: Callable[[], list[dict[str, Any]]] | None,
) -> Callable[[], list[dict[str, Any]]] | None:
    """The same device listing for the resolver and the adapter lookup: ONE ``window.list``
    per tool call, never two readings of a desktop that may differ between them."""
    if lister is None:
        return None
    cache: dict[str, list[dict[str, Any]]] = {}

    def _cached() -> list[dict[str, Any]]:
        if "windows" not in cache:
            cache["windows"] = lister()
        return cache["windows"]

    return _cached


def _allowlist_speech() -> str:
    names = ", ".join(_APP_TR_NAMES.get(a, a) for a in APP_ALLOWLIST)
    return f"Bunu açamam efendim; açabildiklerim: {names}."


# ------------------------------------------------------------------ shared plumbing


def _action_id(ctx: ToolContext) -> str:
    return ctx.call_id or str(uuid.uuid4())


def _turn_record(ctx: ToolContext, *, ttl_s: float = 600.0) -> dict[str, Any]:
    """The ONE router's record of this turn's utterance (``application`` /
    ``text_to_type`` / ``shell_query`` / ``window_ref`` / ``intent``), or ``{}`` when none
    is fresh — a tool call carries no utterance of its own (the same reason
    ``tools.py::_turn_record`` and ``tools_ambient._turn_policy_changes`` exist)."""
    record = dict(ctx.context.get("last_utterance") or {})
    if not record:
        return {}
    raw_at = record.get("at")
    if raw_at:
        try:
            at = datetime.fromisoformat(str(raw_at).replace("Z", "+00:00"))
        except ValueError:
            return record
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        if (ctx.now - at).total_seconds() > ttl_s:
            return {}
    return record


def _receipt(
    ctx: ToolContext,
    *,
    capability: str,
    requested_state: str,
    execution: str,
    terminal: str,
    server: dict[str, Any],
    speech: str,
    error_class: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    receipt = ActionReceipt(
        action_id=_action_id(ctx),
        capability=capability,
        requested_state=requested_state,
        execution_status=execution,
        terminal_status=terminal,
        observed_after={"server": server, "local": {}},
        evidence_refs=[{"kind": "realtime_session", "ref": str(ctx.session_id)}],
        error_class=error_class,
        speech=speech,
        started_at=ctx.now,
        completed_at=now,
        session_id=str(ctx.session_id),
        observed_at=now,
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, SUBSYSTEM_OPERATOR)
    return receipt.as_dict()


def _capability_missing(
    ctx: ToolContext, *, capability: str, requested_state: str
) -> dict[str, Any]:
    return _receipt(
        ctx,
        capability=capability,
        requested_state=requested_state,
        execution=EXECUTION_REFUSED,
        terminal=TERMINAL_FAILED,
        server={"reason": "no_device_runtime"},
        speech=SPEECH_NO_OPERATOR_AUTHORITY,
        error_class="capability_missing",
    )


def _clarification(speech: str) -> dict[str, Any]:
    return {"status": "needs_clarification", "speech": speech, "candidates": []}


def _is_window_id(value: str) -> bool:
    """True when the device's own parser would accept ``value`` as a window id.

    Faithful to ``WindowRegistry.TryParseHandle`` on the Windows companion
    (devices/windows-agent/.../Operator/WindowRegistry.cs): split on "-", exactly three
    parts, the literal "w", a POSITIVE ``long`` handle and a ``ulong`` creation tick, all
    decimal digits (``NumberStyles.None`` — no sign, no whitespace, no separators). A
    structural test reads that C# source so the two halves cannot drift apart.

    This exists because a window TITLE is not a window id, and the device says so with a
    non-retryable ``validation_error`` before it looks at any window at all.
    """
    match = _WINDOW_ID_SHAPE.match(value)
    if match is None:
        return False
    handle = int(match.group("handle"))
    return 0 < handle <= _LONG_MAX and int(match.group("tick")) <= _ULONG_MAX


def _title_matches(name: str, title: str) -> bool:
    """Turkish-case-insensitive containment in either direction, because an owner says
    "not defteri" for a window whose real title is "Adsız - Not Defteri"."""
    needle = turkish_casefold(name).strip()
    label = turkish_casefold(title).strip()
    return bool(needle and label and (needle in label or label in needle))


def _identity_title(title: str) -> str:
    """A title with the decoration that is not identity removed.

    Notepad prefixes "*" while a document has unsaved changes, so ONE window is called
    "Adsız - Not Defteri" and then "*Adsız - Not Defteri" a keystroke later. Treating those
    as two different windows produced a question the owner could not answer — measured on
    the real device: "Hangisi efendim: Adsız - Not Defteri, *Adsız - Not Defteri?"
    """
    return turkish_casefold(title).strip().lstrip("*").strip()


def _recency(db: Session) -> dict[str, int]:
    """window_id -> how recently the owner was in it (0 = most recent), from the focus
    stack. This is ALL the remembered stack is used for now: it is a memory of what was,
    and a window it remembers may have been closed minutes ago."""
    return {
        entry.object_id: index
        for index, entry in enumerate(focus_module.stack(db, FOCUS_KIND_WINDOW))
    }


def _live_windows(list_windows: Callable[[], list[dict[str, Any]]] | None) -> list[dict[str, Any]]:
    """What the DEVICE says is open right now, or ``[]`` when it cannot be asked.

    A window the owner opened by hand was never observed by this operator, so it is not in
    the focus stack. It IS on the desktop, and the device will say so.
    """
    if list_windows is None:
        return []
    try:
        return [window for window in list_windows() if isinstance(window, dict)]
    except Exception:  # noqa: BLE001 - a lookup that fails is "I don't know", never a crash
        logger.warning("operator_window_list_failed")
        return []


def _named_live_windows(
    db: Session, live: list[dict[str, Any]], name: str
) -> list[tuple[str, str]]:
    """``(window_id, title)`` for the windows OPEN RIGHT NOW whose title matches ``name``,
    most recently focused first.

    Only live windows are candidates. The focus stack used to supply them, and it remembers
    windows that were closed long ago — on the real device that produced a question about
    two Notepads that no longer existed."""
    matched = [
        (str(window.get("window_id") or ""), str(window.get("title") or ""))
        for window in live
        if window.get("window_id") and _title_matches(name, str(window.get("title") or ""))
    ]
    recency = _recency(db)
    return sorted(matched, key=lambda pair: recency.get(pair[0], len(recency) + 1))


def _resolve_window_id(
    db: Session,
    *,
    action: str,
    window_ref: str,
    list_windows: Callable[[], list[dict[str, Any]]] | None = None,
) -> tuple[str | None, str | None]:
    """(window_id, refusal_speech).

    ``window_ref`` outside {"current", "previous"} is a window id ONLY when it has the
    device's shape; otherwise it is a NAME the owner or the model said, and this resolves
    it against the durable focus stack — the server's own record of real windows, written
    from real device receipts (``app.operator.service._maybe_set_window_focus``).

    It never forwards an unrecognised string as an id. Doing so was a real owner-visible
    defect (2026-09-09): the model passed ``target: "Not Defteri"``, this function handed
    it to the device verbatim, and eight ``window.activate`` calls were refused with
    ``'Not Defteri' is not a window id (expected w-<hwnd>-<tick>)`` while the correct id
    for that very window — ``w-10160952-365601875``, labelled "Adsız - Not Defteri" —
    sat in the focus stack, written thirteen seconds earlier by the ``app.launch`` that
    opened it. Nothing was ever typed. The module docstring's promise ("never a window id
    the model guessed") is now what the code does.

    A NAME is resolved against the windows the DEVICE says are open right now, ordered by
    the focus stack's recency. The remembered stack is not a source of candidates: it holds
    windows this operator once observed, including ones closed long ago, and on the real
    device that produced "Hangisi efendim: Adsız - Not Defteri, *Adsız - Not Defteri?" —
    a question about two Notepads that no longer existed, and which differed only by
    Notepad's unsaved-changes marker. One device call answers both which windows exist and
    what to call them.

    Several live windows may still match. When they carry the same title (ignoring that
    marker) the owner cannot tell them apart either, so the most recently focused one wins.
    When the titles genuinely DIFFER, this asks.
    """
    if window_ref == WINDOW_REF_MEDIA:
        return _media_window(_live_windows(list_windows))
    if window_ref not in ("current", "previous") and window_ref:
        if _is_window_id(window_ref):
            return window_ref, None
        live = _live_windows(list_windows)
        matched = _named_live_windows(db, live, window_ref)
        if not matched:
            return None, _speech_no_window_named(db, window_ref, live)
        labels = {_identity_title(label) for _id, label in matched}
        if len(labels) > 1:
            return None, _speech_ambiguous_window(matched)
        return matched[0][0], None
    if action == "previous" or window_ref == "previous":
        entry = focus_module.previous(db, FOCUS_KIND_WINDOW)
        if entry is None:
            return None, SPEECH_NO_PREVIOUS_WINDOW
        return _confirm_alive(entry.object_id, list_windows, fall_back_to_foreground=False)
    entry = focus_module.current(db, FOCUS_KIND_WINDOW)
    if entry is None:
        # Nothing to confirm, and no basis for a substitute. "Buraya" points at whatever the
        # owner was last in, and this operator has no record of one — the window in front
        # may be something they are not even looking at. Ask, and touch no device to do it.
        return None, SPEECH_NO_WINDOW
    return _confirm_alive(entry.object_id, list_windows, fall_back_to_foreground=True)


#: ADR-0198: the window a hand gesture's key is for. The shell's own window never - the
#: owner has two screens, the last click was on the cockpit, and every arrow went there.
WINDOW_REF_MEDIA: Final = "media"
#: Title fragments that mark a window as the thing being watched (case-folded compare).
_MEDIA_TITLE_MARKS: Final[tuple[str, ...]] = (
    "youtube",
    "netflix",
    "prime video",
    "disney",
    "twitch",
    "vimeo",
    "exxen",
    "blutv",
    "puhu",
    "tabii",
    "tod tv",
    "vlc",
    "mpc-hc",
    "media player",
    "izle",
    "film",
    "dizi",
    "video",
    "bölüm",
)
#: The shell's own windows, by title: never a target for a gesture.
_SHELL_TITLE_MARKS: Final[tuple[str, ...]] = ("personalagentos", "pagentos")


def _media_window(live: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """The window a gesture's key goes to (``WINDOW_REF_MEDIA``): in the device's own
    z-order (top first), the first window whose title says it is a player or a video,
    else the foreground window, else the topmost - never a window of the shell itself.
    ``(None, speech)`` when the desktop has nothing but the shell."""
    candidates = [
        w
        for w in live
        if w.get("window_id")
        and not any(
            mark in turkish_casefold(str(w.get("title") or "")) for mark in _SHELL_TITLE_MARKS
        )
    ]
    if not candidates:
        return None, SPEECH_NO_WINDOW
    for window in candidates:
        title = turkish_casefold(str(window.get("title") or ""))
        if any(mark in title for mark in _MEDIA_TITLE_MARKS):
            return str(window["window_id"]), None
    for window in candidates:
        if window.get("foreground"):
            return str(window["window_id"]), None
    return str(candidates[0]["window_id"]), None


def _confirm_alive(
    window_id: str | None,
    list_windows: Callable[[], list[dict[str, Any]]] | None,
    *,
    fall_back_to_foreground: bool,
) -> tuple[str | None, str | None]:
    """Hold a REMEMBERED window id against the desktop before anything acts on it.

    The focus stack is a memory, and the owner closes windows. On 2026-09-09 20:00 the
    remembered "current" window had been closed and the device answered
    ``ui_target_not_found`` twice. Worse, a minute earlier the remembered current window
    was a Notepad while the owner was talking about the Chrome they had just asked for,
    and "youtube.com" was typed into the Notepad (ADR-0101).

    For "current" the honest fallback is the window the device says is in FRONT right now —
    that is what "current" means. For "previous" there is no such fallback: a previous
    window that is gone is gone, and this asks instead of picking something else.
    """
    live = _live_windows(list_windows)
    if not live:
        # Nothing to check against: keep the remembered answer rather than inventing one.
        return (window_id, None) if window_id else (None, SPEECH_NO_WINDOW)
    if window_id and any(str(w.get("window_id") or "") == window_id for w in live):
        return window_id, None
    if not fall_back_to_foreground:
        return None, SPEECH_NO_PREVIOUS_WINDOW
    foreground = next((w for w in live if w.get("foreground")), None)
    if foreground is None or not foreground.get("window_id"):
        return None, SPEECH_NO_WINDOW
    return str(foreground["window_id"]), None


def _require_db(ctx: ToolContext, tool: str) -> Session:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _require_operator(ctx: ToolContext, tool: str) -> Any:
    operator = ctx.live.get("operator")
    if operator is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the operator runtime"
        )
    return operator


# ------------------------------------------------------------------------ app.open


def operator_app_open(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Not Defteri'ni aç" / "Chrome'u aç" / "Tarayıcıyı aç" / "PowerShell aç" (spec §3).

    The allowlisted app id the owner's WORDS named wins over the model's own
    ``application`` argument; an unmatched name is refused, naming the allowlist — this
    NEVER reaches ``browser.session_open``/``browser.navigate`` for "Chrome'u aç": the
    only capability this tool ever dispatches is ``app.launch``.
    """
    turn = _turn_record(ctx)
    canonical = turn.get("application") if isinstance(turn.get("application"), str) else None
    if not canonical:
        raw = arguments.get("application")
        if isinstance(raw, str) and raw.strip():
            _, tokens, _ = normalize_transcript(raw)
            canonical = resolve_app_alias(tokens)
    if (
        canonical not in APP_ALLOWLIST
        and turn.get("intent") == "mission_start"
        and isinstance(turn.get("mission_request"), str)
    ):
        # Production 2026-09-19 09:39:15: "YouTube'u aç" - the router had already resolved
        # the owner's sentence to a MISSION (a site is not an application), and the model
        # still reached for app_open("YouTube"). The owner's words win over the model's tool
        # choice: the mission the router planned is started, not a refusal read out.
        from app.voice.realtime_sessions.tools_mission import operator_mission

        return operator_mission(ctx, {"action": "start", "content": turn["mission_request"]})
    if canonical not in APP_ALLOWLIST:
        return _receipt(
            ctx,
            capability=TOOL_APP_OPEN,
            requested_state="opened",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"requested": arguments.get("application"), "allowlist": list(APP_ALLOWLIST)},
            speech=_allowlist_speech(),
            error_class=ERROR_UNKNOWN_APPLICATION,
        )
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_APP_OPEN, requested_state="opened")
    operator = _require_operator(ctx, TOOL_APP_OPEN)
    already = _window_already_open(ctx, canonical)
    if already is not None:
        # Production 2026-09-19 09:39:34: "Chrome'u aç" with Chrome already running behind
        # the window the owner was looking at - ``app.launch`` handed the request to that
        # Chrome and exited, no window came forward, and the owner heard "Chrome açamadım"
        # with Chrome open. What the mission planner already does (req 113): the window that
        # is there is brought forward; nothing is launched a second time.
        plan = Plan(
            name=PLAN_OPEN_APPLICATION, goal=f"open {canonical}", steps=activate_window(already)
        )
    else:
        plan = Plan(
            name=PLAN_OPEN_APPLICATION, goal=f"open {canonical}", steps=open_application(canonical)
        )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    name_tr = _APP_TR_NAMES.get(canonical, canonical)
    if task.status == STATUS_SUCCEEDED:
        speech = f"{name_tr} açtım efendim."
    else:
        speech = f"{name_tr} açamadım efendim."
    return {**(task.action_receipt or {}), "speech": speech}


# --------------------------------------------------------------- window control


def operator_window_control(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Pencereyi büyüt" / "Bunu kapat" / "Önceki pencereye dön" (spec §3). Resolves the
    target window through the durable focus stack — never a window id the model guessed."""
    turn = _turn_record(ctx)
    action = _INTENT_TO_WINDOW_ACTION.get(str(turn.get("intent") or "")) or str(
        arguments.get("action") or ""
    )
    if action not in _WINDOW_ACTIONS:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"operator.window_control needs 'action' in {_WINDOW_ACTIONS}",
        )
    window_ref = turn.get("window_ref") or arguments.get("window") or "current"
    db = _require_db(ctx, TOOL_WINDOW_CONTROL)
    window_id, refusal = _resolve_window_id(
        db,
        action=action,
        window_ref=str(window_ref),
        list_windows=_window_lister(ctx),
    )
    if window_id is None:
        return _clarification(refusal or SPEECH_NO_WINDOW)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_WINDOW_CONTROL, requested_state=action)
    operator = _require_operator(ctx, TOOL_WINDOW_CONTROL)
    steps_by_action = {
        "close": close_window,
        "maximize": maximize_window,
        "minimize": minimize_window,
        "restore": restore_window,
        "activate": activate_window,
        "previous": previous_window,
    }
    if action in ("move", "resize"):
        # B30 req 84/85: geometry comes from the model's arguments (a spoken sentence
        # carries no pixels); the device re-observes the rect within 8 px.
        keys = ("x", "y") if action == "move" else ("width", "height")
        values = []
        for key in keys:
            raw = arguments.get(key)
            if not isinstance(raw, int | float) or isinstance(raw, bool):
                raise VoiceError(
                    VoiceErrorClass.VALIDATION_ERROR,
                    f"operator.window_control {action} needs integer '{keys[0]}' and '{keys[1]}'",
                )
            values.append(int(raw))
        steps = (
            move_window(window_id, values[0], values[1])
            if action == "move"
            else resize_window(window_id, values[0], values[1])
        )
    else:
        steps = steps_by_action[action](window_id)
    plan = Plan(
        name=PLAN_BY_WINDOW_ACTION[action],
        goal=f"{action} window {window_id}",
        steps=steps,
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    if task.status == STATUS_SUCCEEDED:
        speech = f"{_WINDOW_SUCCESS_TR[action]} efendim."
    elif task.error_class == "modal_open":
        speech = (
            f"{_WINDOW_FAILURE_TR[action]} efendim; açık bir pencere var, "
            "önce onu kapatmam gerekiyor."
        )
    else:
        speech = f"{_WINDOW_FAILURE_TR[action]} efendim."
    return {**(task.action_receipt or {}), "speech": speech}


# ------------------------------------------------------------------------- type


def operator_type(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Buraya X yaz" / "Bu kutuya X yaz" (spec §3). Refuses a secret-looking payload
    outright ("Şifreleri ben yazmam"); the text the owner's words extracted wins over the
    model's own ``content`` argument.

    The wire argument is named ``content``, not ``text``: the realtime relay refuses any
    tool-call argument key that is audio/transcript/text-shaped at the HTTP boundary
    (``app.voice.realtime_sessions.service.FORBIDDEN_KEY_PARTS`` — a literal ``"text"``
    key is rejected with a 422 before this handler ever runs), so the payload the model
    passes cannot be called that even though the device capability it becomes
    (``keyboard.type``'s own ``text`` field) keeps the M19 spec's name.
    """
    turn = _turn_record(ctx)
    arg_text = arguments.get("content")
    arg_text = arg_text.strip() if isinstance(arg_text, str) else ""
    turn_text = turn.get("text_to_type")
    turn_text = turn_text.strip() if isinstance(turn_text, str) else ""
    if contains_secret_reference(arg_text) or contains_secret_reference(turn_text):
        return _receipt(
            ctx,
            capability=TOOL_TYPE,
            requested_state="typed",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={},
            speech=SPEECH_SECRET_REFUSED,
            error_class=ERROR_SECRET_REFUSED,
        )
    text = turn_text or arg_text
    if not text:
        return _clarification(SPEECH_NO_TEXT)
    db = _require_db(ctx, TOOL_TYPE)
    window_ref = turn.get("window_ref") or arguments.get("target") or "current"
    # ONE device listing for the resolver and the browser check below (``_once``).
    lister = _once(_window_lister(ctx))
    window_id, refusal = _resolve_window_id(
        db,
        action="activate",
        window_ref=str(window_ref),
        list_windows=lister,
    )
    if window_id is None:
        return _clarification(refusal or SPEECH_NO_WINDOW)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_TYPE, requested_state="typed")
    operator = _require_operator(ctx, TOOL_TYPE)
    target = next((w for w in (lister() if lister else []) if w.get("window_id") == window_id), {})
    plan = Plan(
        name=PLAN_TYPE_TEXT,
        goal=f"type into {window_id}",
        steps=build_type_text_steps(
            window_id, text, browser=is_browser_image(str(target.get("image") or ""))
        ),
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    return {**(task.action_receipt or {}), "speech": _type_speech(task)}


def _type_speech(task: Any) -> str:
    """The sentence the owner hears for a typing run — the step that actually stopped it.

    ``type_text`` is activate -> type -> verify, and the old code read only the task's
    final status, so EVERY failure said "metni doğrulayamadım" (I could not verify the
    text). On 2026-09-09 the owner was told exactly that for a run whose first step never
    reached the keyboard: the plan died on ``window.activate`` and no key was pressed.
    A sentence about verification is only honest once something was typed.
    """
    if task.status == STATUS_SUCCEEDED:
        return SPEECH_TYPE_SUCCESS
    typed = any(
        receipt.ok and receipt.capability == "keyboard.type" for receipt in (task.receipts or [])
    )
    return SPEECH_TYPE_FAILURE if typed else SPEECH_TYPE_NOT_ATTEMPTED


# ------------------------------------------------------------------------ shell


def operator_shell(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "IP adresimi göster" / "Bilgisayarın adı ne?" (spec §3) — a QUERY: it dispatches a
    read-only allowlisted command and answers from what it read, never from a guess."""
    turn = _turn_record(ctx)
    kind = turn.get("shell_query") if isinstance(turn.get("shell_query"), str) else None
    kind = kind or str(arguments.get("query") or "")
    if kind not in PLAN_BY_SHELL_QUERY:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"operator.shell needs 'query' in {set(PLAN_BY_SHELL_QUERY)}",
        )
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_SHELL, requested_state=kind)
    operator = _require_operator(ctx, TOOL_SHELL)
    plan = Plan(
        name=PLAN_BY_SHELL_QUERY[kind],
        goal=f"shell query {kind}",
        steps=build_shell_query_steps(kind),
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    if task.status == STATUS_SUCCEEDED:
        stdout = str(task.last_observed.get("stdout") or "")
        first_line = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
        if kind == "ip":
            value = parse_ipv4(stdout) or ""
            speech = f"IP adresiniz {value}." if value else SPEECH_IP_FAILURE
        elif kind == "whoami":
            # B30 req 118: ``whoami`` answers DOMAIN\\user; the owner hears the user part.
            user = first_line.rsplit("\\", 1)[-1] if first_line else ""
            speech = f"Kullanıcı adınız {user}." if user else SPEECH_WHOAMI_FAILURE
        else:
            # The companion's own ``hostname`` output is one line; a canned test fixture
            # may carry more (alarms_support.happy_operator_device_results), so only the
            # first line is ever spoken as the machine's name.
            speech = f"Bilgisayarın adı {first_line}." if first_line else SPEECH_HOSTNAME_FAILURE
    else:
        speech = _SHELL_FAILURE_TR.get(kind, SPEECH_HOSTNAME_FAILURE)
    return {**(task.action_receipt or {}), "speech": speech}


# ------------------------------------------------- B30: app close, processes, services


def _app_close_speech(task: Any, name_tr: str) -> str:
    if task.status == STATUS_SUCCEEDED:
        return f"{name_tr} uygulamasını kapattım efendim."
    if task.error_class == "modal_open":
        return f"{name_tr} uygulamasını kapatamadım efendim; kaydedilmemiş bir şey soruyor."
    return f"{name_tr} uygulamasını kapatamadım efendim."


def operator_app_close(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Not Defteri'ni kapat." / "Chrome'u kapat." (B30 req 82) — ``app.close`` on the
    window the owner named (or the current one): WM_CLOSE, never a kill unless ``force``,
    a save prompt reported as ``modal_open`` rather than answered for the owner."""
    turn = _turn_record(ctx)
    canonical = turn.get("application") if isinstance(turn.get("application"), str) else None
    if not canonical:
        raw = arguments.get("application")
        if isinstance(raw, str) and raw.strip():
            _, tokens, _ = normalize_transcript(raw)
            canonical = resolve_app_alias(tokens)
    db = _require_db(ctx, TOOL_APP_CLOSE)
    lister = _once(_window_lister(ctx))
    if canonical:
        # The application's own window, from the device's list (by image), not a guess.
        image = app_allowlists.APP_IMAGES.get(canonical, "")
        window_id = next(
            (
                str(w.get("window_id"))
                for w in _live_windows(lister)
                if str(w.get("image") or "").replace("\\", "/").rsplit("/", 1)[-1].lower() == image
            ),
            None,
        )
        if window_id is None:
            name_tr = _APP_TR_NAMES.get(canonical, canonical)
            return _receipt(
                ctx,
                capability=TOOL_APP_CLOSE,
                requested_state="closed",
                execution=EXECUTION_NOOP,
                terminal=TERMINAL_ALREADY,
                server={"application": canonical, "reason": "not_running"},
                speech=f"{name_tr} zaten açık değil efendim.",
            )
        name_tr = _APP_TR_NAMES.get(canonical, canonical)
    else:
        window_ref = turn.get("window_ref") or arguments.get("window") or "current"
        window_id, refusal = _resolve_window_id(
            db, action="close", window_ref=str(window_ref), list_windows=lister
        )
        if window_id is None:
            return _clarification(refusal or SPEECH_NO_WINDOW)
        name_tr = "Öndeki"
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_APP_CLOSE, requested_state="closed")
    operator = _require_operator(ctx, TOOL_APP_CLOSE)
    force = arguments.get("force") is True
    plan = Plan(
        name=PLAN_CLOSE_APPLICATION,
        goal=f"close application {window_id}",
        steps=close_app(window_id, force=force),
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    return {**(task.action_receipt or {}), "speech": _app_close_speech(task, name_tr)}


def _spoken_name(turn: dict[str, Any], arguments: dict[str, Any], key: str) -> str:
    spoken = turn.get(key)
    if isinstance(spoken, str) and spoken.strip():
        return spoken.strip()
    argued = arguments.get("name")
    return argued.strip() if isinstance(argued, str) else ""


def operator_process(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Chrome çalışıyor mu?" / "Hangi uygulamalar açık?" (B30 req 119) and "Chrome'u
    sonlandır." (req 120) — ``process.list`` is a read; ``process.stop`` is allowed only
    for an image the shared contract names as stoppable, refused with ``permission_denied``
    otherwise, before any device is asked."""
    turn = _turn_record(ctx)
    action = str(arguments.get("action") or "")
    if turn.get("intent") == "process_query":
        action = "list"
    elif turn.get("intent") == "process_stop":
        action = "stop"
    if action not in PLAN_BY_PROCESS_ACTION:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"operator.process needs 'action' in {tuple(PLAN_BY_PROCESS_ACTION)}",
        )
    name = _spoken_name(turn, arguments, "process_name")
    canonical = resolve_app_alias(normalize_transcript(name)[1]) if name else None
    image = app_allowlists.APP_IMAGES.get(canonical or "", "") or name
    if action == "stop":
        if not image:
            return _clarification(SPEECH_NO_PROCESS)
        if not app_allowlists.image_stoppable(image):
            return _receipt(
                ctx,
                capability=TOOL_PROCESS,
                requested_state="stopped",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"requested": image, "reason": "not_in_stop_policy"},
                speech=SPEECH_PROCESS_POLICY,
                error_class="permission_denied",
            )
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_PROCESS, requested_state=action)
    operator = _require_operator(ctx, TOOL_PROCESS)
    if action == "list":
        steps = process_list(image or None)
    else:
        steps = process_stop(image, force=arguments.get("force") is True)
    plan = Plan(
        name=PLAN_BY_PROCESS_ACTION[action], goal=f"process {action} {image or '*'}", steps=steps
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    out = {**(task.action_receipt or {})}
    if action == "list":
        observed = task.last_observed or {}
        processes = observed.get("processes") if task.status == STATUS_SUCCEEDED else None
        if not isinstance(processes, list):
            out["speech"] = SPEECH_PROCESS_LIST_FAILURE
            return out
        names = sorted(
            {str(p.get("name") or p.get("image") or "") for p in processes if isinstance(p, dict)}
        )
        names = [n for n in names if n]
        if image:
            label = _APP_TR_NAMES.get(canonical or "", image)
            out["speech"] = (
                f"{label} çalışıyor efendim ({len(processes)} süreç)."
                if processes
                else f"{label} çalışmıyor efendim."
            )
        else:
            out["speech"] = (
                f"{len(names)} uygulama açık efendim: {', '.join(names[:8])}."
                if names
                else "Açık bir uygulama görünmüyor efendim."
            )
        out["processes"] = processes
        return out
    label = _APP_TR_NAMES.get(canonical or "", image)
    if task.status == STATUS_SUCCEEDED:
        out["speech"] = f"{label} sürecini sonlandırdım efendim."
    elif task.error_class == "permission_denied":
        out["speech"] = SPEECH_PROCESS_POLICY
    else:
        out["speech"] = f"{label} sürecini sonlandıramadım efendim."
    return out


def operator_service(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Yazdırma servisi çalışıyor mu?" (B30 req 121) and "Spooler servisini yeniden
    başlat." (req 122) — ``service.status`` is a read; ``service.restart`` is allowed only
    for a service the shared contract names, and the device refuses an unelevated companion
    with ``permission_denied`` (UAC stays the owner's)."""
    turn = _turn_record(ctx)
    action = str(arguments.get("action") or "")
    if turn.get("intent") == "service_query":
        action = "status"
    elif turn.get("intent") == "service_restart":
        action = "restart"
    if action not in PLAN_BY_SERVICE_ACTION:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"operator.service needs 'action' in {tuple(PLAN_BY_SERVICE_ACTION)}",
        )
    name = _spoken_name(turn, arguments, "service_name")
    name = SERVICE_ALIASES_TR.get(name.lower(), name)
    if not name:
        return _clarification(SPEECH_NO_SERVICE)
    if action == "restart" and not app_allowlists.service_restartable(name):
        return _receipt(
            ctx,
            capability=TOOL_SERVICE,
            requested_state="restarted",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"requested": name, "reason": "not_in_restart_policy"},
            speech=SPEECH_SERVICE_POLICY,
            error_class="permission_denied",
        )
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_SERVICE, requested_state=action)
    operator = _require_operator(ctx, TOOL_SERVICE)
    steps = service_status(name) if action == "status" else service_restart(name)
    plan = Plan(name=PLAN_BY_SERVICE_ACTION[action], goal=f"service {action} {name}", steps=steps)
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    out = {**(task.action_receipt or {})}
    state = str((task.last_observed or {}).get("state") or "")
    if action == "status":
        if task.status == STATUS_SUCCEEDED and state:
            out["speech"] = f"{name} servisi {SERVICE_STATE_TR.get(state.lower(), state)} efendim."
        elif task.error_class == "ui_target_not_found":
            out["speech"] = f"{name} adında bir servis yok efendim."
        else:
            out["speech"] = f"{name} servisinin durumunu okuyamadım efendim."
        out["state"] = state or None
        return out
    if task.status == STATUS_SUCCEEDED:
        out["speech"] = f"{name} servisini yeniden başlattım efendim."
    elif task.error_class == "permission_denied":
        out["speech"] = SPEECH_SERVICE_ELEVATION
    else:
        out["speech"] = f"{name} servisini yeniden başlatamadım efendim."
    return out


# ---------------------------------------------------------------------- cancel


def _mission_active(ctx: ToolContext) -> bool:
    if ctx.db is None:
        return False
    try:
        from app.operator.mission_service import active_mission

        return active_mission(ctx.db) is not None
    except Exception:  # noqa: BLE001 - a deployment without the missions table
        return False


def operator_cancel(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "İptal et." / "Dur." while a task is running (spec §3). Idempotent: nothing
    running is a truthful, calm answer, not an error."""
    del arguments
    operator = ctx.live.get("operator")
    running = bool(operator is not None and operator.is_running())
    if not running and _mission_active(ctx):
        # B39 (req 129): "Dur." while a MISSION runs is the mission's cancel.
        from app.voice.realtime_sessions.tools_mission import mission_control

        return mission_control(ctx, "cancel")
    if not running:
        return _receipt(
            ctx,
            capability=TOOL_CANCEL,
            requested_state="cancelled",
            execution=EXECUTION_NOOP,
            terminal=TERMINAL_ALREADY,
            server={"running": False},
            speech=SPEECH_NOTHING_RUNNING,
        )
    operator.cancel()
    return _receipt(
        ctx,
        capability=TOOL_CANCEL,
        requested_state="cancelled",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server={"running": True},
        speech=SPEECH_CANCELLED,
    )


# ---------------------------------------------------------------------- status


def operator_status(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Ne yapıyorsun?" while a task is running (spec §3) — a QUERY over the live task."""
    del arguments
    operator = ctx.live.get("operator")
    running = bool(operator is not None and operator.is_running())
    task = operator.status() if operator is not None else None
    if (not running or task is None) and _mission_active(ctx):
        # B39 (req 129): "Ne yapıyorsun?" while a MISSION runs is the mission's status.
        from app.voice.realtime_sessions.tools_mission import mission_control

        return mission_control(ctx, "status")
    if not running or task is None:
        return {"speech": SPEECH_NOT_DOING_ANYTHING, "task": task.as_dict() if task else None}
    observed = task.last_observed if isinstance(task.last_observed, dict) else {}
    window = observed.get("window") if isinstance(observed.get("window"), dict) else None
    window_title = str((window or {}).get("title") or observed.get("title") or "")
    step_no = task.current_step + 1
    if window_title:
        speech = (
            f"{task.plan_name} işlemi üzerinde çalışıyorum efendim; {step_no}. adımdayım, "
            f"{window_title} penceresindeyim."
        )
    else:
        speech = f"{task.plan_name} işlemi üzerinde çalışıyorum efendim; {step_no}. adımdayım."
    return {"speech": speech, "task": task.as_dict()}


def _input_speech(task: Any, *, success: str, failure: str) -> str:
    """The sentence for an input run, naming the guard when the guard stopped it."""
    if task.status == STATUS_SUCCEEDED:
        return success
    if task.error_class == ERROR_FOCUS_MISMATCH:
        return SPEECH_FOCUS_LOST
    return failure


def _spoken_key(turn: dict[str, Any], arguments: dict[str, Any]) -> tuple[str | None, list[str]]:
    """(key, chord): the owner's own words first (``key_press`` on the turn, "enter" or
    "ctrl+s"), the model's ``key`` / ``keys`` arguments only when the router named none."""
    spoken = turn.get("key_press")
    if isinstance(spoken, str) and spoken.strip():
        parts = [p.strip().lower() for p in spoken.split("+") if p.strip()]
        if len(parts) == 1:
            return parts[0], []
        return None, parts
    keys = arguments.get("keys")
    if isinstance(keys, list) and keys:
        return None, [str(k).strip().lower() for k in keys if str(k).strip()]
    key = arguments.get("key")
    if isinstance(key, str) and key.strip():
        return key.strip().lower(), []
    return None, []


def _spoken_repeat(turn: dict[str, Any], arguments: dict[str, Any]) -> int:
    """How many times (ADR-0195): the owner's own count first (``repeat_count`` on the
    turn, "beş kere"), the model's ``count`` argument only when the router read none,
    and 1 when neither said anything. Not bounded here: the caller refuses an oversized
    count in its own words rather than quietly doing it once."""
    spoken = turn.get("repeat_count")
    if isinstance(spoken, int) and not isinstance(spoken, bool) and spoken >= 1:
        return spoken
    raw = arguments.get("count")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 1:
        return raw
    return 1


def _times_tr(count: int) -> str:
    """ "5 kere " for a spoken receipt, nothing for once."""
    return f"{count} kere " if count > 1 else ""


def operator_key(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Enter'a bas." / "Ctrl S'ye bas." (B28 req 92/93) — one key or one chord into the
    focused window, through the companion's focus guard, re-observed after.

    The plan is ``window.activate`` -> ``keyboard.key|shortcut`` with ``retries=0`` on the
    input step: a chord that was sent and not read back is not sent twice (Ctrl+Z twice is
    two undos). The device's ``focus_mismatch`` mid-plan ends the task with that class and
    the receipt counts the steps that ran before it (req 110).

    ADR-0195 (owner, 2026-09-21): "Yukarı tuşuna beş kere bas." is five guarded key steps
    after the one activate, and the receipt says "5 kere" - the owner no longer says the
    sentence five times. More than :data:`MAX_REPEAT` in one sentence is refused aloud.
    """
    turn = _turn_record(ctx)
    key, chord = _spoken_key(turn, arguments)
    if key is None and not chord:
        return _clarification(SPEECH_NO_KEY)
    count = _spoken_repeat(turn, arguments)
    if count > MAX_REPEAT:
        return _clarification(SPEECH_TOO_MANY_REPEATS_TR.format(max=MAX_REPEAT))
    if key is not None and not valid_key(key):
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR, f"'{key}' is not a key keyboard.key accepts"
        )
    if chord and not valid_shortcut(chord):
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"{chord!r} is not a chord keyboard.shortcut accepts (modifiers then one key)",
        )
    db = _require_db(ctx, TOOL_KEY)
    window_ref = turn.get("window_ref") or arguments.get("window") or "current"
    window_id, refusal = _resolve_window_id(
        db, action="activate", window_ref=str(window_ref), list_windows=_window_lister(ctx)
    )
    if window_id is None:
        return _clarification(refusal or SPEECH_NO_WINDOW)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_KEY, requested_state="pressed")
    operator = _require_operator(ctx, TOOL_KEY)
    times = _times_tr(count)
    if key is not None:
        plan = Plan(
            name=PLAN_PRESS_KEY,
            goal=f"press {key} x{count} in {window_id}",
            steps=press_key(window_id, key, count=count),
        )
        success = SPEECH_KEY_SUCCESS_TR.format(key=KEY_TR.get(key, key.upper()), times=times)
    else:
        plan = Plan(
            name=PLAN_PRESS_SHORTCUT,
            goal=f"press {'+'.join(chord)} x{count} in {window_id}",
            steps=press_shortcut(window_id, chord, count=count),
        )
        success = SPEECH_SHORTCUT_SUCCESS_TR.format(
            keys="+".join(KEY_TR.get(k, k.upper()) for k in chord), times=times
        )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    return {
        **(task.action_receipt or {}),
        "repeat_count": count,
        "speech": _input_speech(task, success=success, failure=SPEECH_KEY_FAILURE),
    }


def _window_centre(windows: list[dict[str, Any]], window_id: str) -> tuple[int, int]:
    """The centre of ``window_id`` in window space, from the device's own rect."""
    for window in windows:
        if window.get("window_id") != window_id:
            continue
        rect = window.get("rect") if isinstance(window.get("rect"), dict) else {}
        try:
            width, height = int(rect.get("width") or 0), int(rect.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        if width > 0 and height > 0:
            return max(0, width // 2), max(0, height // 2)
    return SCROLL_FALLBACK_POINT


def operator_pointer(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Aşağı kaydır." (B28 req 98), and the model's own move/click/double/right click
    (req 94-97) — the last rung of the ladder, taken only when said to be (req 107).

    A move or a click without ``last_resort: true`` is a REFUSED receipt: the policy is the
    action, and the refusal is its record. Screen-space coordinates need it too. A scroll
    needs no justification (no element is being targeted) and, when spoken, lands on the
    window's own centre computed from the device's rect - never a guessed coordinate.
    """
    turn = _turn_record(ctx)
    scroll_direction = turn.get("scroll_direction")
    spoken_scroll = (
        isinstance(scroll_direction, str) and scroll_direction in SCROLL_NOTCHES_BY_DIRECTION
    )
    action = "scroll" if spoken_scroll else str(arguments.get("action") or "")
    if action not in POINTER_ACTIONS:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            f"operator.pointer needs 'action' in {POINTER_ACTIONS}",
        )
    space = str(arguments.get("space") or "window")
    if space not in POINTER_SPACES:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "space must be 'window' or 'screen'")
    last_resort = arguments.get("last_resort") is True
    reason = str(arguments.get("reason") or "")[:200]
    if action != "scroll" and not last_resort:
        return _receipt(
            ctx,
            capability=TOOL_POINTER,
            requested_state=action,
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={
                "reason": ERROR_COORDINATE_NOT_LAST_RESORT,
                "action": action,
                "space": space,
                "interaction_level": "pointer",
            },
            speech=SPEECH_COORDINATE_POLICY,
            error_class=ERROR_COORDINATE_NOT_LAST_RESORT,
        )
    db = _require_db(ctx, TOOL_POINTER)
    window_ref = turn.get("window_ref") or arguments.get("window") or "current"
    lister = _once(_window_lister(ctx))
    window_id, refusal = _resolve_window_id(
        db, action="activate", window_ref=str(window_ref), list_windows=lister
    )
    if window_id is None:
        return _clarification(refusal or SPEECH_NO_WINDOW)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_POINTER, requested_state=action)
    delta: int | None = None
    if action == "scroll":
        if spoken_scroll:
            delta = SCROLL_NOTCHES_BY_DIRECTION[str(scroll_direction)]
        else:
            raw = arguments.get("delta")
            delta = int(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) else None
    x_arg, y_arg = arguments.get("x"), arguments.get("y")
    have_point = all(isinstance(v, int | float) and not isinstance(v, bool) for v in (x_arg, y_arg))
    if have_point:
        x, y = int(x_arg), int(y_arg)  # type: ignore[arg-type]
    elif action == "scroll" and space == "window":
        x, y = _window_centre(_live_windows(lister), window_id)
    else:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, "operator.pointer needs 'x' and 'y'")
    # ADR-0195: "üç kere aşağı kaydır" - the spoken count repeats the scroll step; a
    # click is the model's own last resort and is never repeated from words.
    count = _spoken_repeat(turn, arguments) if action == "scroll" else 1
    if count > MAX_REPEAT:
        return _clarification(SPEECH_TOO_MANY_REPEATS_TR.format(max=MAX_REPEAT))
    try:
        steps = build_pointer_steps(
            window_id, action, x=x, y=y, space=space, delta=delta, count=count
        )
    except ValueError as exc:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, str(exc)) from exc
    operator = _require_operator(ctx, TOOL_POINTER)
    plan = Plan(
        name=PLAN_BY_POINTER_ACTION[action],
        goal=f"pointer {action} x{count} at ({x},{y}) {space} in {window_id}",
        steps=steps,
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    success = SPEECH_POINTER_SUCCESS_TR[action]
    if count > 1:
        success = SPEECH_SCROLL_REPEATED_TR.format(count=count)
    out = {
        **(task.action_receipt or {}),
        "repeat_count": count,
        "speech": _input_speech(task, success=success, failure=SPEECH_POINTER_FAILURE),
    }
    if last_resort:
        # The justification travels with the receipt (req 107): a reader of the ledger
        # sees WHY the last rung was taken, in the model's own words.
        server = out.get("observed_after", {}).get("server")
        if isinstance(server, dict):
            server["last_resort_reason"] = reason
    return out


def _focused_image(lister: Callable[[], list[dict[str, Any]]] | None, window_id: str) -> str:
    """The process image behind ``window_id``, from the device's own window list."""
    for window in _live_windows(lister):
        if window.get("window_id") == window_id:
            return str(window.get("image") or "")
    return ""


def _ui_query(
    turn: dict[str, Any], arguments: dict[str, Any], adapter: app_adapters.AppAdapter
) -> dict[str, str]:
    """The element query: the owner's spoken target first ("Tamam" -> a button named
    Tamam; "belge" -> the adapter's document control), then the model's explicit query
    keys, then the model's plain ``target`` name."""
    spoken = turn.get("ui_target")
    if isinstance(spoken, str) and spoken.strip():
        known = app_adapters.spoken_target_query(adapter, spoken)
        if known is not None:
            return known
        return app_adapters.button_query(spoken.strip())
    explicit = {
        key: str(arguments[key]).strip()
        for key in app_adapters.QUERY_KEYS
        if isinstance(arguments.get(key), str) and str(arguments[key]).strip()
    }
    if explicit:
        return explicit
    target = arguments.get("target")
    if isinstance(target, str) and target.strip():
        known = app_adapters.spoken_target_query(adapter, target)
        return known if known is not None else {"name": target.strip()}
    return {}


def _ui_speech(task: Any, *, success: str, failure: str, unverified: str | None = None) -> str:
    if task.status == STATUS_SUCCEEDED:
        return success
    if task.error_class == ERROR_FOCUS_MISMATCH:
        return SPEECH_FOCUS_LOST
    if task.error_class == ERROR_UI_TARGET_NOT_FOUND:
        return SPEECH_UI_NOT_FOUND
    if unverified and task.error_class == "postcondition_failed":
        return unverified
    return failure


def operator_ui(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Tamam düğmesine tıkla." / set a control's value / select an item (B29 req
    100/101/103) — UI Automation, the highest semantic rung below the API, each action
    verified by an INDEPENDENT read afterwards (req 111): an invoke whose element neither
    changed nor went away is a failure, never a success.
    """
    turn = _turn_record(ctx)
    action = str(arguments.get("action") or "")
    if turn.get("intent") == "ui_invoke":
        action = "invoke"
    if action not in UI_ACTIONS:
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR, f"operator.ui needs 'action' in {UI_ACTIONS}"
        )
    # Nothing to act on, or a secret: decided before any device is asked anything.
    if not _ui_query(turn, arguments, app_adapters.GENERIC):
        return _clarification(SPEECH_NO_TARGET)
    if action == "set_value":
        raw_value = arguments.get("value")
        raw_value = raw_value.strip() if isinstance(raw_value, str) else ""
        if not raw_value:
            return _clarification(SPEECH_NO_VALUE)
        if contains_secret_reference(raw_value):
            return _receipt(
                ctx,
                capability=TOOL_UI,
                requested_state="set_value",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={},
                speech=SPEECH_SECRET_REFUSED,
                error_class=ERROR_SECRET_REFUSED,
            )
    db = _require_db(ctx, TOOL_UI)
    window_ref = turn.get("window_ref") or arguments.get("window") or "current"
    lister = _once(_window_lister(ctx))
    window_id, refusal = _resolve_window_id(
        db, action="activate", window_ref=str(window_ref), list_windows=lister
    )
    if window_id is None:
        return _clarification(refusal or SPEECH_NO_WINDOW)
    adapter = app_adapters.adapter_for(_focused_image(lister, window_id))
    query = _ui_query(turn, arguments, adapter)
    if not query:
        return _clarification(SPEECH_NO_TARGET)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_UI, requested_state=action)
    label = query.get("name") or query.get("automation_id") or query.get("control_type") or ""
    try:
        if action == "invoke":
            expect = arguments.get("expect")
            expect = str(expect) if isinstance(expect, str) and expect in UI_EXPECTATIONS else None
            expect_arg = arguments.get("expect_value")
            expect_arg = str(expect_arg) if isinstance(expect_arg, str) and expect_arg else None
            steps = ui_invoke(window_id, query, expect=expect, expect_arg=expect_arg)
            success = SPEECH_UI_INVOKED_TR.format(name=label)
            unverified: str | None = SPEECH_UI_UNVERIFIED_TR.format(name=label)
        elif action == "set_value":
            value = str(arguments.get("value") or "").strip()
            steps = ui_set_value(window_id, query, value)
            success = SPEECH_UI_SET_TR
            unverified = None
        else:
            item = arguments.get("item")
            item = item.strip() if isinstance(item, str) else ""
            if not item:
                return _clarification(SPEECH_NO_ITEM)
            steps = ui_select(window_id, query, item)
            success = SPEECH_UI_SELECTED_TR.format(item=item)
            unverified = None
    except ValueError as exc:
        raise VoiceError(VoiceErrorClass.VALIDATION_ERROR, str(exc)) from exc
    operator = _require_operator(ctx, TOOL_UI)
    plan = Plan(
        name=PLAN_BY_UI_ACTION[action], goal=f"ui {action} {query} in {window_id}", steps=steps
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    out = {
        **(task.action_receipt or {}),
        "speech": _ui_speech(
            task, success=success, failure=SPEECH_UI_FAILURE, unverified=unverified
        ),
    }
    server = out.get("observed_after", {}).get("server")
    if isinstance(server, dict):
        server["adapter"] = adapter.image or "generic"
        server["query"] = dict(query)
    return out


def operator_inspect(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Ekrandaki metni oku." / the UI Automation tree of the focused window (B29 req
    99/102) — a QUERY: one bounded ``ui.inspect``, spoken as the text the control holds
    or as what the window contains."""
    turn = _turn_record(ctx)
    db = _require_db(ctx, TOOL_INSPECT)
    window_ref = turn.get("window_ref") or arguments.get("window") or "current"
    lister = _once(_window_lister(ctx))
    window_id, refusal = _resolve_window_id(
        db, action="activate", window_ref=str(window_ref), list_windows=lister
    )
    if window_id is None:
        return _clarification(refusal or SPEECH_NO_WINDOW)
    adapter = app_adapters.adapter_for(_focused_image(lister, window_id))
    query = _ui_query(turn, arguments, adapter)
    reading = turn.get("intent") == "ui_read" or bool(query) or arguments.get("read") is True
    if reading and not query and adapter.document_query:
        query = dict(adapter.document_query)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_INSPECT, requested_state="read")
    operator = _require_operator(ctx, TOOL_INSPECT)
    mode = "read" if reading else "inspect"
    plan = Plan(
        name=PLAN_BY_READ_MODE[mode],
        goal=f"ui {mode} {query} in {window_id}",
        steps=ui_read(window_id, query or None),
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    out = {**(task.action_receipt or {})}
    root = (task.last_observed or {}).get("root") if task.status == STATUS_SUCCEEDED else None
    if root is None:
        out["speech"] = _ui_speech(task, success="", failure=SPEECH_UI_FAILURE)
        return out
    if reading:
        text = tree_text(root)[:MAX_READ_CHARS]
        out["speech"] = SPEECH_READ_TR.format(text=text) if text else SPEECH_READ_EMPTY
        out["text"] = text
    else:
        from app.operator.plans import _tree_nodes

        nodes = _tree_nodes(root)
        names = [str(n.get("name")).strip() for n in nodes if str(n.get("name") or "").strip()]
        out["speech"] = SPEECH_INSPECT_TR.format(
            count=len(nodes), names=", ".join(names[:6]) or "adsız öğeler"
        )
        out["node_count"] = len(nodes)
    out["root"] = root
    return out


def operator_see(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Ekranda ne var?" (B29 req 105) — the VISUAL rung: one ``screen.capture``, one
    question to the vision provider, the answer spoken. No provider configured is a
    refusal that says so; the picture is never persisted and never guessed about."""
    question = arguments.get("question")
    question = question.strip() if isinstance(question, str) and question.strip() else ""
    question = question or DEFAULT_QUESTION_TR
    provider = ctx.live.get("vision_provider")
    if provider is None:
        return _receipt(
            ctx,
            capability=TOOL_SEE,
            requested_state="described",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": "no_vision_provider"},
            speech=SPEECH_NO_VISION,
            error_class=ERROR_NO_VISION_PROVIDER,
        )
    device = ctx.live.get("device_action")
    if device is None:
        return _capability_missing(ctx, capability=TOOL_SEE, requested_state="described")
    action_id = _action_id(ctx)
    result = device.run(
        capability=DEVICE_SCREEN_CAPTURE,
        payload={"format": "png"},
        idempotency_key=f"see:{action_id}",
        timeout_s=TIMEOUT_SCREEN_CAPTURE_S,
    )
    encoded = (result.result or {}).get("png_base64") if result.ok else None
    raw: bytes | None = None
    if isinstance(encoded, str) and encoded:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raw = None
    if not raw:
        missing = result.error_class in ("no_capable_device", "capability_missing")
        return _receipt(
            ctx,
            capability=TOOL_SEE,
            requested_state="described",
            execution=EXECUTION_REFUSED if missing else EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            server={"reason": result.error_class or "no_image"},
            speech=SPEECH_SCREENSHOT_NO_DEVICE if missing else SPEECH_SCREENSHOT_FAILED,
            error_class="capability_missing" if missing else (result.error_class or "unverified"),
        )
    try:
        answer = provider.describe(raw, question=question)
    except VisionError as exc:
        return _receipt(
            ctx,
            capability=TOOL_SEE,
            requested_state="described",
            execution=EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            server={"reason": exc.error_class, "provider": getattr(provider, "name", "?")},
            speech=SPEECH_VISION_FAILED,
            error_class=exc.error_class,
        )
    out = _receipt(
        ctx,
        capability=TOOL_SEE,
        requested_state="described",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server={
            "provider": answer.provider,
            "model": answer.model,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "interaction_level": "visual",
        },
        speech=answer.text,
    )
    out["answer"] = answer.as_dict()
    return out


def operator_screenshot(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Ekran görüntüsü al." (B27 req 735) — ONE ``screen.capture`` on the device.

    The first caller the capability has had. The device is asked through the same port
    every other action uses, so "no device advertises screen.capture" comes back as the
    registry's own answer (``no_capable_device`` -> ``capability_missing``) rather than a
    sentence written here; and a device that answers without an image is an UNVERIFIED
    failure, never a success with nothing behind it.
    """
    del arguments
    device = ctx.live.get("device_action")
    if device is None:
        return _capability_missing(
            ctx, capability=CAPABILITY_SCREENSHOT, requested_state="captured"
        )
    action_id = _action_id(ctx)
    result = device.run(
        capability=DEVICE_SCREEN_CAPTURE,
        payload={"format": "png"},
        idempotency_key=f"screenshot:{action_id}",
        timeout_s=TIMEOUT_SCREEN_CAPTURE_S,
    )
    if not result.ok:
        missing = result.error_class in ("no_capable_device", "capability_missing")
        return _receipt(
            ctx,
            capability=CAPABILITY_SCREENSHOT,
            requested_state="captured",
            execution=EXECUTION_REFUSED if missing else EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            server={"reason": result.error_class, "detail": (result.message or "")[:200]},
            speech=SPEECH_SCREENSHOT_NO_DEVICE if missing else SPEECH_SCREENSHOT_FAILED,
            error_class="capability_missing" if missing else (result.error_class or "failed"),
        )
    payload = result.result or {}
    encoded = payload.get("png_base64")
    raw: bytes | None = None
    if isinstance(encoded, str) and encoded:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raw = None
    if not raw:
        return _receipt(
            ctx,
            capability=CAPABILITY_SCREENSHOT,
            requested_state="captured",
            execution=EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            server={
                "reason": "no_image",
                "width": payload.get("width"),
                "height": payload.get("height"),
            },
            speech=SPEECH_SCREENSHOT_UNVERIFIED,
            error_class="unverified",
        )
    digest = hashlib.sha256(raw).hexdigest()
    stored_key: str | None = None
    artifacts = ctx.live.get("artifacts_runtime")
    if artifacts is not None:
        key = f"{SCREENSHOT_KEY_PREFIX}/{ctx.session_id}/{action_id}.png"
        try:
            artifacts.store.put(key, raw, "image/png")
            stored_key = key
        except Exception:  # noqa: BLE001 - the image is the action; storing it is evidence
            logger.warning("screenshot_store_failed", action_id=action_id)
    width = payload.get("width")
    height = payload.get("height")
    size = f"{width}×{height}" if width and height else f"{len(raw)} bayt"
    speech = f"Ekran görüntüsünü aldım efendim; {size}."
    speech += " Kaydettim." if stored_key else " Kaydedemedim; yalnız bu oturumda."
    return _receipt(
        ctx,
        capability=CAPABILITY_SCREENSHOT,
        requested_state="captured",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server={
            "width": width,
            "height": height,
            "bytes": len(raw),
            "sha256": digest,
            "stored_key": stored_key,
        },
        speech=speech,
    )


# ---------------------------------------------------------- ADR-0199: pointer session


def operator_pointer_session(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """The pinch-mouse's own tool (ADR-0199 stage 2): ``{"action":"begin"}`` opens a
    receipted pointer-streaming session on the MEDIA window (the same
    ``WINDOW_REF_MEDIA`` rule a gesture's key uses) and returns the single-use token the
    browser presents on ``GET /v1/voice/realtime/sessions/{id}/pointer``;
    ``{"action":"end"}`` closes it the same way the socket's own three endings do (the
    client's ``end`` frame, the socket closing, 60s of silence — all through
    ``app.voice.realtime_sessions.pointer_session.end_receipt``).

    Unlike every other operator tool, this one reads NO WORDS: ``_turn_record`` is never
    consulted. A gesture (a pinch closing into a fist) carries no utterance to read one
    from, and the browser always calls this with an explicit ``action`` — the module
    docstring's own contract, restated here as code: ``action`` is required, never
    inferred.
    """
    action = str(arguments.get("action") or "")
    if action not in ("begin", "end"):
        raise VoiceError(
            VoiceErrorClass.VALIDATION_ERROR,
            "operator.pointer_session needs 'action' in ('begin', 'end')",
        )
    if action == "begin":
        return _pointer_session_begin(ctx)
    return _pointer_session_end(ctx)


def _pointer_session_begin(ctx: ToolContext) -> dict[str, Any]:
    db = _require_db(ctx, TOOL_POINTER_SESSION)
    # A second "begin" while one is already open (mid-stream re-arm) abandons the old
    # bookkeeping rather than leaving two records to confuse "end": the owner closing
    # their hand again is opening a NEW stream, not multiplying one.
    pointer_session.clear(ctx.context)
    window_id, refusal = _resolve_window_id(
        db, action="activate", window_ref=WINDOW_REF_MEDIA, list_windows=_window_lister(ctx)
    )
    if window_id is None:
        return _clarification(refusal or SPEECH_NO_WINDOW)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_POINTER_SESSION, requested_state="open")
    operator = _require_operator(ctx, TOOL_POINTER_SESSION)
    session = pointer_session.new_session_id()
    plan = Plan(
        name=PLAN_POINTER_SESSION_BEGIN,
        goal=f"open pointer stream {session} in {window_id}",
        steps=pointer_session_begin(window_id, session),
    )
    task = operator.start_task(ctx.db, plan, device_action, session_id=str(ctx.session_id))
    if task.status != STATUS_SUCCEEDED:
        return {
            **(task.action_receipt or {}),
            "status": "refused",
            "speech": pointer_session.SPEECH_OPEN_FAILED,
        }
    stream_token = pointer_session.new_stream_token()
    record = pointer_session.build_record(
        session=session, window_id=window_id, stream_token=stream_token, started_at=ctx.now
    )
    ctx.context[pointer_session.CONTEXT_KEY] = record
    return {
        **(task.action_receipt or {}),
        "status": "open",
        "stream_token": stream_token,
        "expires_at": record["expires_at"],
        "speech": pointer_session.SPEECH_OPENED,
    }


def _pointer_session_end(ctx: ToolContext) -> dict[str, Any]:
    record = pointer_session.record_from_context(ctx.context)
    if record is None:
        return _receipt(
            ctx,
            capability=TOOL_POINTER_SESSION,
            requested_state="closed",
            execution=EXECUTION_NOOP,
            terminal=TERMINAL_ALREADY,
            server={"moves": 0, "buttons": 0, "dropped": 0, "duration_ms": 0},
            speech=pointer_session.SPEECH_NOTHING_OPEN,
        )
    started_at = pointer_session.parse_iso(record.get("started_at")) or ctx.now
    out = pointer_session.end_receipt(
        ctx.db,
        session_id=ctx.session_id,
        action_id=_action_id(ctx),
        record=record,
        device_action=ctx.live.get("device_action"),
        # Reached via the tool relay rather than the WebSocket's own ending: no frame
        # was ever streamed on THIS path, so the honest count is zero — the socket's own
        # endings pass their real, live-tallied counts to the same function.
        moves=0,
        buttons=0,
        dropped=0,
        started_at=started_at,
        now=ctx.now,
    )
    pointer_session.clear(ctx.context)
    return out


# ------------------------------------------------------------------ registration


def register_operator_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all seven tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_APP_OPEN,
            description=(
                "Bir MASAÜSTÜ UYGULAMASINI AÇAR: 'Not Defteri'ni aç', 'Chrome'u aç', "
                "'Tarayıcıyı aç', 'Hesap makinesini aç', 'PowerShell aç' denince HER ZAMAN "
                "bu araç çağrılır. 'application' alanına sahibin söylediği adı ver; sunucu "
                "izin verilen listeyle kendisi eşler. Bir tarayıcı açma isteği İÇİN ASLA "
                "browser.session_open ya da browser.navigate ÇAĞIRMA - her zaman bu aracı "
                "kullan. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"application": {"type": "string", "maxLength": 100}},
                "additionalProperties": False,
            },
            handler=operator_app_open,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_WINDOW_CONTROL,
            description=(
                "Bir PENCEREYİ yönetir: kapatır ('bunu kapat', 'bu pencereyi kapatsana', "
                "'öndeki pencereyi kapat'), büyütür ('pencereyi büyüt'), küçültür ('bu "
                "pencereyi küçült'), eski haline getirir, ya da önceki pencereye döner "
                "('önceki pencereye dön'). 'action' alanına close/maximize/minimize/"
                "restore/activate/previous'tan birini ver; hangi pencere olduğunu sunucu "
                "kendi odak kaydından çözer. 'window' alanını NORMALDE HİÇ VERME. "
                "Yalnızca sahip birden fazla pencere arasından belirli birini "
                "söylediyse o pencerenin ADINI yaz; sunucu adı kendi kaydıyla eşler. "
                "Pencere kimliği UYDURMA. Sunucu bir pencere bulamazsa ya da ad birden "
                "fazla pencereye uyuyorsa 'needs_clarification' döner - o soruyu aynen "
                "sor. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(_WINDOW_ACTIONS)},
                    "window": {
                        "type": "string",
                        "maxLength": 128,
                        "description": (
                            "Optional. The window's NAME as the owner said it, or an id "
                            "'w-<handle>-<tick>' returned by an earlier result. Omit it "
                            "to act on the focused window."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            handler=operator_window_control,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_TYPE,
            description=(
                "Odaktaki pencereye METİN YAZAR: 'buraya X yaz', 'bu kutuya X yaz', "
                "'seçili yere X yaz'. 'content' alanına yazılacak metni ver. Şifre, "
                "parola ya da PIN gibi görünen bir metin YAZMA İSTEĞİNİ HER ZAMAN "
                "REDDET; sunucu da kendi tarafında reddeder. 'target' alanını NORMALDE "
                "HİÇ VERME - sunucu odaktaki pencereyi kendi kaydından bilir. Yalnızca "
                "sahip belirli bir pencere söylediyse o pencerenin ADINI yaz; pencere "
                "kimliği UYDURMA. Sunucu hangi pencereye yazacağını bilemezse ya da ne "
                "yazılacağı belli değilse 'needs_clarification' döner - o soruyu aynen "
                "sor. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "maxLength": 2000},
                    "target": {
                        "type": "string",
                        "maxLength": 128,
                        "description": (
                            "Optional. The window's NAME as the owner said it, or an id "
                            "'w-<handle>-<tick>' returned by an earlier result. Omit it "
                            "to type into the focused window."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            handler=operator_type,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_SHELL,
            description=(
                "Bilgisayarın IP adresini ('IP adresimi göster', 'IP adresim ne?') ya da "
                "adını ('Bilgisayarın adı ne?') SÖYLER. 'query' alanına 'ip' ya da "
                "'hostname' ver. Kayıttan/cihazdan okur, tahmin etmez. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "enum": list(PLAN_BY_SHELL_QUERY)}},
                "additionalProperties": False,
            },
            handler=operator_shell,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_CANCEL,
            description=(
                "Çalışmakta olan bir OPERATÖR GÖREVİNİ İPTAL EDER ('iptal et', bir görev "
                "sürerken 'dur'). Hiçbir şey çalışmıyorsa bunu olduğu gibi söyler. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=operator_cancel,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_STATUS,
            description=(
                "Bir OPERATÖR GÖREVİ sürerken 'Ne yapıyorsun?' diye sorulunca hangi adımda "
                "olduğunu ve hangi pencerede olduğunu SÖYLER. Dönen 'speech' metnini aynen "
                "oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=operator_status,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_SCREENSHOT,
            description=(
                "EKRAN GÖRÜNTÜSÜ ALIR: 'ekran görüntüsü al', 'ekranın görüntüsünü al', "
                "'screenshot al', 'ekranı yakala' denince bu araç çağrılır. Görüntüyü "
                "sunucu saklar; sana boyutu ve saklandığı anahtar döner, görüntünün "
                "kendisi dönmez. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=operator_screenshot,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_KEY,
            description=(
                "Odaktaki pencereye TEK BİR TUŞ ya da KISAYOL gönderir: 'Enter'a bas', "
                "'Escape'e bas', 'Tab tuşuna bas', 'Ctrl S'ye bas'. 'key' tek tuş adı "
                "(enter, escape, tab, backspace, delete, home, end, up, down, left, right, "
                "space, f1..f12); 'keys' kısayol için ['ctrl','s'] gibi. Metin yazmak için "
                "operator.type kullanılır. Hangi tuş olduğunu sahibin sözcüğünden SUNUCU "
                "okur. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "maxLength": 16},
                    "keys": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 16},
                        "maxItems": 4,
                    },
                    "window": {"type": "string", "maxLength": 100},
                    # ADR-0195: how many times; the owner's spoken count wins over this.
                    "count": {"type": "integer", "minimum": 1, "maximum": MAX_REPEAT},
                },
                "additionalProperties": False,
            },
            handler=operator_key,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_POINTER,
            description=(
                "İŞARETÇİ: 'aşağı kaydır' / 'yukarı kaydır' denince action=scroll; "
                "taşıma/tıklama (move, click, double_click, right_click) YALNIZ SON ÇARE: "
                "önce arayüz ağacı (ui.*) ve klavye denenir, olmuyorsa "
                "last_resort=true ve 'reason' ile çağrılır; aksi hâlde araç reddeder. "
                "x,y pencere uzayında (space='window'); 'delta' kaydırma çentiği. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(POINTER_ACTIONS)},
                    "x": {"type": "integer", "minimum": -20000, "maximum": 20000},
                    "y": {"type": "integer", "minimum": -20000, "maximum": 20000},
                    "space": {"type": "string", "enum": list(POINTER_SPACES)},
                    "delta": {"type": "integer", "minimum": -50, "maximum": 50},
                    # ADR-0195: scroll repeats only; the owner's spoken count wins.
                    "count": {"type": "integer", "minimum": 1, "maximum": MAX_REPEAT},
                    "last_resort": {"type": "boolean"},
                    "reason": {"type": "string", "maxLength": 200},
                    "window": {"type": "string", "maxLength": 100},
                },
                "additionalProperties": False,
            },
            handler=operator_pointer,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_UI,
            description=(
                "ARAYÜZ AĞACI (UI Automation) ile eyler — tıklamadan ÖNCE denenecek yol: "
                "action=invoke bir düğmeye basar ('Tamam düğmesine tıkla' → name='Tamam'); "
                "action=set_value bir alanın değerini yazar ('value'); action=select bir "
                "listeden öğe seçer ('item'). Hedef: name / automation_id / name_prefix / "
                "control_type ya da 'target'. invoke için beklenen sonucu söyleyebilirsin: "
                "expect=window_gone|element_gone|element_present|value_ends_with "
                "(+expect_value); sunucu sonucu bağımsız okur, doğrulanmayan eylem "
                "başarısız raporlanır. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(UI_ACTIONS)},
                    "target": {"type": "string", "maxLength": 200},
                    "name": {"type": "string", "maxLength": 200},
                    "automation_id": {"type": "string", "maxLength": 200},
                    "name_prefix": {"type": "string", "maxLength": 200},
                    "control_type": {"type": "string", "maxLength": 64},
                    "value": {"type": "string", "maxLength": 2000},
                    "item": {"type": "string", "maxLength": 200},
                    "expect": {"type": "string", "enum": list(UI_EXPECTATIONS)},
                    "expect_value": {"type": "string", "maxLength": 200},
                    "window": {"type": "string", "maxLength": 100},
                },
                "additionalProperties": False,
            },
            handler=operator_ui,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_INSPECT,
            description=(
                "Odaktaki pencerenin ARAYÜZ AĞACINI okur (UI Automation): 'ekrandaki "
                "metni oku', 'ne yazıyor' denince alanın metnini SÖYLER; hedef "
                "verilmezse pencerenin öğelerini sayar ve başlıcalarını adlandırır. "
                "Hiçbir şeyi değiştirmez. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 200},
                    "name": {"type": "string", "maxLength": 200},
                    "automation_id": {"type": "string", "maxLength": 200},
                    "name_prefix": {"type": "string", "maxLength": 200},
                    "control_type": {"type": "string", "maxLength": 64},
                    "read": {"type": "boolean"},
                    "window": {"type": "string", "maxLength": 100},
                },
                "additionalProperties": False,
            },
            handler=operator_inspect,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_SEE,
            description=(
                "EKRANI TARİF EDER (görüntü anlamlandırma): 'ekranda ne var', 'ekranı "
                "anlat' denince bir ekran görüntüsü alır ve görüntü sağlayıcısına sorar; "
                "'question' sahibin sorusu. Sağlayıcı tanımlı değilse bunu olduğu gibi "
                "söyler. Arayüz ağacı yetiyorsa önce operator.inspect kullanılır. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"question": {"type": "string", "maxLength": 500}},
                "additionalProperties": False,
            },
            handler=operator_see,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_APP_CLOSE,
            description=(
                "Bir MASAÜSTÜ UYGULAMASINI KAPATIR: 'Not Defteri'ni kapat', 'Chrome'u "
                "kapat'. Önce düzgün kapatma (WM_CLOSE); kaydedilmemiş bir şey soruyorsa "
                "bunu söyler, sahibin yerine cevaplamaz; 'force' yalnız sahip isterse. "
                "'application' sahibin söylediği ad. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "application": {"type": "string", "maxLength": 100},
                    "window": {"type": "string", "maxLength": 100},
                    "force": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            handler=operator_app_close,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_PROCESS,
            description=(
                "SÜREÇLER: action=list çalışan uygulamaları/süreçleri sayar ('hangi "
                "uygulamalar açık', 'Chrome çalışıyor mu' → name); action=stop bir süreci "
                "sonlandırır ('Chrome'u sonlandır') — YALNIZ izin listesindeki uygulamalar, "
                "sistem süreçleri asla; politika dışı istek reddedilir. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(PLAN_BY_PROCESS_ACTION)},
                    "name": {"type": "string", "maxLength": 100},
                    "force": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            handler=operator_process,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_SERVICE,
            description=(
                "WINDOWS SERVİSLERİ: action=status bir servisin durumunu söyler "
                "('yazdırma servisi çalışıyor mu'); action=restart yeniden başlatır — YALNIZ "
                "politikanın adlandırdığı servisler ve yönetici yetkisi varsa; aksi hâlde "
                "dürüstçe reddeder. 'name' servis adı (Spooler gibi) ya da sahibin sözü. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(PLAN_BY_SERVICE_ACTION)},
                    "name": {"type": "string", "maxLength": 100},
                },
                "additionalProperties": False,
            },
            handler=operator_service,
        )
    )
    reg.register(
        ToolSpec(
            name=operator_capabilities.CAPABILITY_POINTER_SESSION,
            description=(
                "El hareketiyle FARE AKIŞI açar/kapatır (ADR-0199): tarayıcı bir sıkma "
                "(pinch) jestini FARE MODUNA çevirdiğinde 'action':'begin' ile çağırır - "
                "MEDIA penceresini öne getirir ve tarayıcının imleç akışını göndereceği "
                "tek kullanımlık bir jeton döner; el açıldığında/akış bittiğinde "
                "'action':'end' ile kapatır. Bu araç konuşulan sözcükleri OKUMAZ - "
                "'action' HER ZAMAN açıkça verilir, asla tahmin edilmez. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["begin", "end"]}},
                "required": ["action"],
                "additionalProperties": False,
            },
            handler=operator_pointer_session,
        )
    )
    # B39 (req 127-130): the mission tool is one of the operator's own - registered here so
    # the declared capabilities and the registered tools stay one set.
    from app.voice.realtime_sessions.tools_mission import register_mission_tools

    register_mission_tools(reg)
    return reg


__all__ = [
    "DEVICE_SCREEN_CAPTURE",
    "OPERATOR_TOOL_NAMES",
    "TOOL_APP_OPEN",
    "TOOL_APP_CLOSE",
    "TOOL_CANCEL",
    "TOOL_INSPECT",
    "TOOL_KEY",
    "TOOL_POINTER",
    "TOOL_POINTER_SESSION",
    "TOOL_PROCESS",
    "TOOL_SCREENSHOT",
    "TOOL_SEE",
    "TOOL_SERVICE",
    "TOOL_SHELL",
    "TOOL_STATUS",
    "TOOL_TYPE",
    "TOOL_UI",
    "TOOL_WINDOW_CONTROL",
    "operator_app_close",
    "operator_app_open",
    "operator_cancel",
    "operator_inspect",
    "operator_key",
    "operator_pointer",
    "operator_pointer_session",
    "operator_process",
    "operator_screenshot",
    "operator_see",
    "operator_service",
    "operator_shell",
    "operator_status",
    "operator_type",
    "operator_window_control",
    "register_operator_tools",
]
