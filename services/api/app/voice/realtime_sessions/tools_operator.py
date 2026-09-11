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

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from app.actions.receipt import (
    EXECUTION_EXECUTED,
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
from app.operator import capabilities as operator_capabilities
from app.operator import focus as focus_module
from app.operator.capabilities import (
    CAPABILITY_APP_OPEN,
    CAPABILITY_CANCEL,
    CAPABILITY_SHELL,
    CAPABILITY_STATUS,
    CAPABILITY_TYPE,
    CAPABILITY_WINDOW_CONTROL,
    PLAN_BY_SHELL_QUERY,
    PLAN_BY_WINDOW_ACTION,
    PLAN_OPEN_APPLICATION,
    PLAN_TYPE_TEXT,
)
from app.operator.models import FOCUS_KIND_WINDOW
from app.operator.plans import (
    APP_ALLOWLIST,
    activate_window,
    close_window,
    maximize_window,
    minimize_window,
    open_application,
    parse_ipv4,
    previous_window,
    resolve_app_alias,
    restore_window,
)
from app.operator.plans import shell_query as build_shell_query_steps
from app.operator.plans import type_text as build_type_text_steps
from app.operator.service import Plan
from app.operator.task import STATUS_SUCCEEDED
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import contains_secret_reference, normalize_transcript, turkish_casefold

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

OPERATOR_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_APP_OPEN,
    TOOL_WINDOW_CONTROL,
    TOOL_TYPE,
    TOOL_SHELL,
    TOOL_CANCEL,
    TOOL_STATUS,
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

#: Turkish names for the allowlisted app ids (spec §2's ``app.launch`` allowlist).
_APP_TR_NAMES: Final[dict[str, str]] = {
    "notepad": "Not Defteri",
    "calc": "Hesap Makinesi",
    "explorer": "Dosya Gezgini",
    "powershell": "PowerShell",
    "chrome": "Chrome",
    "msedge": "Microsoft Edge",
}

_WINDOW_SUCCESS_TR: Final[dict[str, str]] = {
    "close": "Pencereyi kapattım",
    "maximize": "Pencereyi büyüttüm",
    "minimize": "Pencereyi küçülttüm",
    "restore": "Pencereyi eski haline getirdim",
    "activate": "Pencereyi öne getirdim",
    "previous": "Önceki pencereye döndüm",
}
_WINDOW_FAILURE_TR: Final[dict[str, str]] = {
    "close": "Pencereyi kapatamadım",
    "maximize": "Pencereyi büyütemedim",
    "minimize": "Pencereyi küçültemedim",
    "restore": "Pencereyi eski haline getiremedim",
    "activate": "Pencereyi öne getiremedim",
    "previous": "Önceki pencereye dönemedim",
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
    """"Not Defteri'ni aç" / "Chrome'u aç" / "Tarayıcıyı aç" / "PowerShell aç" (spec §3).

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
    """"Pencereyi büyüt" / "Bunu kapat" / "Önceki pencereye dön" (spec §3). Resolves the
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
    """"Buraya X yaz" / "Bu kutuya X yaz" (spec §3). Refuses a secret-looking payload
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
    window_id, refusal = _resolve_window_id(
        db,
        action="activate",
        window_ref=str(window_ref),
        list_windows=_window_lister(ctx),
    )
    if window_id is None:
        return _clarification(refusal or SPEECH_NO_WINDOW)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _capability_missing(ctx, capability=TOOL_TYPE, requested_state="typed")
    operator = _require_operator(ctx, TOOL_TYPE)
    plan = Plan(
        name=PLAN_TYPE_TEXT,
        goal=f"type into {window_id}",
        steps=build_type_text_steps(window_id, text),
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
    """"IP adresimi göster" / "Bilgisayarın adı ne?" (spec §3) — a QUERY: it dispatches a
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
        if kind == "ip":
            value = parse_ipv4(stdout) or ""
            speech = f"IP adresiniz {value}." if value else SPEECH_IP_FAILURE
        else:
            # The companion's own ``hostname`` output is one line; a canned test fixture
            # may carry more (alarms_support.happy_operator_device_results), so only the
            # first line is ever spoken as the machine's name.
            first_line = stdout.strip().splitlines()[0].strip() if stdout.strip() else ""
            speech = f"Bilgisayarın adı {first_line}." if first_line else SPEECH_HOSTNAME_FAILURE
    else:
        speech = SPEECH_IP_FAILURE if kind == "ip" else SPEECH_HOSTNAME_FAILURE
    return {**(task.action_receipt or {}), "speech": speech}


# ---------------------------------------------------------------------- cancel


def operator_cancel(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """"İptal et." / "Dur." while a task is running (spec §3). Idempotent: nothing
    running is a truthful, calm answer, not an error."""
    del arguments
    operator = ctx.live.get("operator")
    running = bool(operator is not None and operator.is_running())
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
    """"Ne yapıyorsun?" while a task is running (spec §3) — a QUERY over the live task."""
    del arguments
    operator = ctx.live.get("operator")
    running = bool(operator is not None and operator.is_running())
    task = operator.status() if operator is not None else None
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


# ------------------------------------------------------------------ registration


def register_operator_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all six tools (module docstring: ONE line in ``default_registry``)."""
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
                "properties": {"query": {"type": "string", "enum": ["ip", "hostname"]}},
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
    return reg


__all__ = [
    "OPERATOR_TOOL_NAMES",
    "TOOL_APP_OPEN",
    "TOOL_CANCEL",
    "TOOL_SHELL",
    "TOOL_STATUS",
    "TOOL_TYPE",
    "TOOL_WINDOW_CONTROL",
    "operator_app_open",
    "operator_cancel",
    "operator_shell",
    "operator_status",
    "operator_type",
    "operator_window_control",
    "register_operator_tools",
]
