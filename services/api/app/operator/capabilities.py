"""The Digital Operator's capability vocabulary — one name per action, declared once.

Until 2026-09-10 the operator had TWO names for every action and nothing reconciled
them. The voice tool registry declared ``operator.type``; the action receipt the ledger
recorded was minted by an f-string in ``app.operator.service`` as
``f"operator.{task.plan_name}"`` and came out ``operator.type_text``. The Evolution
Supervisor reads a failed receipt's ``capability`` straight into an incident title, so
production's ``evolution_opportunities`` held

    Tekrarlayan eylem hatası: operator.type_text (validation_error)

for a defect whose code lives behind ``operator.type``. Asking the self-model where that
capability was implemented returned ``capability_not_indexed`` with the six real operator
tools as candidates — because the name in the incident was never a string in the source at
all. It was assembled at runtime, and no honest static index can contain a name like that.

This is the shape ``docs/DECISIONS.md`` already calls **two clocks for one decision**, and
the repository's answer to that shape is to remove the second clock rather than to keep
synchronising it. So:

**One vocabulary reaches the ledger: the registered tool capability.** A receipt for the
run that typed text says ``operator.type`` — the same string ``ToolSpec(name=TOOL_TYPE)``
registers, the same string the tool's own refusal path already wrote, and the same string
the self-model can point at a file and a line for. The plan that was run is not lost: it
travels in the receipt's evidence and in the activity event's ``detail_json``, where it is
detail about the action rather than the name of it.

**The mapping is the one declared source.** :data:`RECEIPT_BY_PLAN` is to this package what
``RECEIPT_BY_DEVICE_CALL`` is to ``app.alarms.sequence``: an enumeration, not a rule, with a
structural test (``tests/unit/test_operator_capability_vocabulary.py``) asserting the plans
this package can actually build and the plans in the table are the same set. A plan added
later without a receipt capability fails the suite at authoring time — and fails
:class:`app.operator.service.Plan`'s own construction at runtime, BEFORE a device is
touched, rather than after the action ran and there is a receipt to lose.

**The names already written stay readable.** ``activity_events`` holds rows minted under the
old vocabulary and they are evidence: a row says what was true when it was written, and
rewriting it to make a later query tidy is not a correction, it is a loss.
:data:`RETIRED_RECEIPT_CAPABILITIES` is therefore a CLOSED list — every name an operator
receipt was ever written under before 2026-09-10, mapped to the capability that answers it
now. Its keys are string literals in a module-level mapping precisely so
``app.selfmodel.indexer`` indexes them the same way it indexes
``RECEIPT_BY_DEVICE_CALL``'s (ADR-0111), which makes ``where_is_capability`` answer for a
two-year-old incident title as readily as for today's.

It is closed, not extensible. A new plan never earns a retired name, because a new plan's
receipts are born under the tool capability. The structural test asserts the list is exactly
what it is, so growing it is a deliberate act with a test to change, not a side effect.

Nothing here imports anything: the vocabulary must be readable from the service that mints
receipts, from the voice tools that build plans, and from a test, without any of the three
importing the other two.
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------- the capabilities

#: The six capabilities the Digital Operator advertises. These are the names
#: ``app.voice.realtime_sessions.tools_operator.register_operator_tools`` registers with
#: ``ToolSpec(name=...)``, and — since 2026-09-10 — the only names an operator action
#: receipt is ever written under.
CAPABILITY_APP_OPEN: Final = "operator.app_open"
CAPABILITY_WINDOW_CONTROL: Final = "operator.window_control"
CAPABILITY_TYPE: Final = "operator.type"
CAPABILITY_SHELL: Final = "operator.shell"
CAPABILITY_CANCEL: Final = "operator.cancel"
CAPABILITY_STATUS: Final = "operator.status"
#: B27 req 735 (matrix row 104 "Çağıran yok"): the device has answered ``screen.capture``
#: since M19 and nothing in Cloud Core ever asked. One device call, no plan - so it has
#: no row in ``RECEIPT_BY_PLAN`` and mints its receipt under this name directly.
CAPABILITY_SCREENSHOT: Final = "operator.screenshot"
#: B28 req 92/93: one key or one modifier chord, through the focus guard - the device has
#: answered ``keyboard.key`` / ``keyboard.shortcut`` since M19 and nothing asked.
CAPABILITY_KEY: Final = "operator.key"
#: B28 req 94-98, 107: the pointer family (move, click, double/right click, scroll).
#: Raw coordinates are the LAST rung of the interaction ladder (spec §2), and this tool
#: is the place that rule is enforced rather than described.
CAPABILITY_POINTER: Final = "operator.pointer"
#: B29 req 100/101/103: UI Automation actions (invoke, set value, select) - the highest
#: semantic rung below the API, each verified by an INDEPENDENT read after acting.
CAPABILITY_UI: Final = "operator.ui"
#: B29 req 99/102: the UI Automation tree, read; the text a control holds, read.
CAPABILITY_INSPECT: Final = "operator.inspect"
#: B29 req 105: the visual rung - a capture described by a vision provider.
CAPABILITY_SEE: Final = "operator.see"
#: B30 req 82: close an application (WM_CLOSE first, terminate only with force) - the
#: device has answered ``app.close`` since M19 and nothing asked.
CAPABILITY_APP_CLOSE: Final = "operator.app_close"
#: B30 req 119/120: processes - list by name, stop under the allowlist policy.
CAPABILITY_PROCESS: Final = "operator.process"
#: B30 req 121/122: services - status by name, restart under the allowlist policy.
CAPABILITY_SERVICE: Final = "operator.service"
#: B39 req 127-130: a multi-step mission - planned from the owner's sentence, run as a
#: closed loop (observe, decide, act, verify, replan), parked for the owner's yes when
#: they ask to see the plan first, paused/resumed/cancelled by voice or REST.
CAPABILITY_MISSION: Final = "operator.mission"
#: ADR-0199 stage 2: the pinch-mouse's own tool - ``{"action":"begin"}`` opens a
#: receipted pointer-streaming session on the MEDIA window and mints the single-use
#: token the browser presents on the pointer WebSocket; ``{"action":"end"}`` closes it.
#: The WebSocket itself never mints a receipt under this name - the tool and the
#: WebSocket's own three endings (the ``end`` frame, the socket closing, 60s of
#: silence) all write through the ONE shared helper
#: (``app.voice.realtime_sessions.pointer_session.end_receipt``).
CAPABILITY_POINTER_SESSION: Final = "operator.pointer_session"

OPERATOR_CAPABILITIES: Final[tuple[str, ...]] = (
    CAPABILITY_APP_OPEN,
    CAPABILITY_WINDOW_CONTROL,
    CAPABILITY_TYPE,
    CAPABILITY_SHELL,
    CAPABILITY_CANCEL,
    CAPABILITY_STATUS,
    CAPABILITY_SCREENSHOT,
    CAPABILITY_KEY,
    CAPABILITY_POINTER,
    CAPABILITY_UI,
    CAPABILITY_INSPECT,
    CAPABILITY_SEE,
    CAPABILITY_APP_CLOSE,
    CAPABILITY_PROCESS,
    CAPABILITY_SERVICE,
    CAPABILITY_MISSION,
    CAPABILITY_POINTER_SESSION,
)

# ----------------------------------------------------------------- the plan names

#: A plan's name is its identity inside ``app.operator.task`` — it is what
#: ``operator.status`` speaks ("type_text işlemi üzerinde çalışıyorum") and what the
#: activity event carries as detail. It is NOT a capability and never becomes one.
PLAN_OPEN_APPLICATION: Final = "open_application"
PLAN_TYPE_TEXT: Final = "type_text"
#: B30 req 82.
PLAN_CLOSE_APPLICATION: Final = "close_application"

#: ``operator.window_control``'s ``action`` argument -> the plan that performs it. The
#: tool used to build this name with ``f"{action}_window"``; declaring it means the six
#: plan names exist in the source, and the structural test can check the tool's own
#: accepted action list against these keys.
PLAN_BY_WINDOW_ACTION: Final[dict[str, str]] = {
    "activate": "activate_window",
    "close": "close_window",
    "maximize": "maximize_window",
    "minimize": "minimize_window",
    "previous": "previous_window",
    "restore": "restore_window",
    # B30 req 84/85: the two geometry actions the device has answered since M19.
    "move": "move_window",
    "resize": "resize_window",
}

#: ``operator.shell``'s ``query`` argument -> the plan that answers it, for the same
#: reason. B30 req 118 adds ``whoami``; every command the Cloud Core sends is held against
#: the device's patterns by ``packages/protocol/operator-allowlists.json``.
PLAN_BY_SHELL_QUERY: Final[dict[str, str]] = {
    "hostname": "shell_query_hostname",
    "ip": "shell_query_ip",
    "whoami": "shell_query_whoami",
}

#: B30 req 119-122: ``operator.process`` / ``operator.service`` ``action`` -> plan.
PLAN_BY_PROCESS_ACTION: Final[dict[str, str]] = {
    "list": "process_list",
    "stop": "process_stop",
}
PLAN_BY_SERVICE_ACTION: Final[dict[str, str]] = {
    "status": "service_status",
    "restart": "service_restart",
}

#: B28: ``operator.key``'s two shapes and ``operator.pointer``'s ``action`` argument -> the
#: plan that performs it, declared for the same reason the window table is.
PLAN_PRESS_KEY: Final = "press_key"
PLAN_PRESS_SHORTCUT: Final = "press_shortcut"
PLAN_BY_POINTER_ACTION: Final[dict[str, str]] = {
    "move": "pointer_move",
    "click": "pointer_click",
    "double_click": "pointer_double_click",
    "right_click": "pointer_right_click",
    "scroll": "pointer_scroll",
}

#: B29: ``operator.ui``'s ``action`` argument -> the plan, and the two read plans behind
#: ``operator.inspect``.
PLAN_BY_UI_ACTION: Final[dict[str, str]] = {
    "invoke": "ui_invoke",
    "set_value": "ui_set_value",
    "select": "ui_select",
}
PLAN_UI_READ: Final = "ui_read"
PLAN_UI_INSPECT: Final = "ui_inspect"
#: ``operator.inspect``'s two modes -> the plan, so the tool builds its plan name from a
#: declared mapping (the structural test reads exactly that shape).
PLAN_BY_READ_MODE: Final[dict[str, str]] = {"read": PLAN_UI_READ, "inspect": PLAN_UI_INSPECT}

#: ADR-0199: ``operator.pointer_session``'s ``{"action":"begin"}`` - ``window.activate``
#: -> ``pointer.stream_begin``, declared the way ``PLAN_PRESS_KEY`` is. There is
#: deliberately no plan name for ``end``: the receipt it writes carries counts
#: (``moves``/``buttons``/``dropped``/``duration_ms``) no ``OperatorTask`` trail holds,
#: and it is reached from three places (the tool, the socket closing, 60s of silence),
#: not one dispatch through ``OperatorService.start_task`` - see
#: ``app.voice.realtime_sessions.pointer_session.end_receipt``.
PLAN_POINTER_SESSION_BEGIN: Final = "pointer_session_begin"

# ------------------------------------------------------- plan -> receipt capability

#: The one declared source: every plan this package can run, and the capability its
#: receipt and its activity event are recorded under. Six actions collapse onto
#: ``operator.window_control`` and two onto ``operator.shell`` because that is what the
#: owner reached for — one tool — and a receipt is a record of the capability that was
#: commanded, not of the step sequence chosen to satisfy it.
#:
#: Written out row by row rather than derived from the two maps above: this table is what
#: a reader consults to answer "what will the ledger say when this runs", and a
#: comprehension would make them evaluate one to find out. The structural test asserts the
#: three agree.
RECEIPT_BY_PLAN: Final[dict[str, str]] = {
    "open_application": CAPABILITY_APP_OPEN,
    "type_text": CAPABILITY_TYPE,
    "activate_window": CAPABILITY_WINDOW_CONTROL,
    "close_window": CAPABILITY_WINDOW_CONTROL,
    "maximize_window": CAPABILITY_WINDOW_CONTROL,
    "minimize_window": CAPABILITY_WINDOW_CONTROL,
    "previous_window": CAPABILITY_WINDOW_CONTROL,
    "restore_window": CAPABILITY_WINDOW_CONTROL,
    "shell_query_hostname": CAPABILITY_SHELL,
    "shell_query_ip": CAPABILITY_SHELL,
    "shell_query_whoami": CAPABILITY_SHELL,
    # B30 req 82/84/85/119-122.
    "close_application": CAPABILITY_APP_CLOSE,
    "move_window": CAPABILITY_WINDOW_CONTROL,
    "resize_window": CAPABILITY_WINDOW_CONTROL,
    "process_list": CAPABILITY_PROCESS,
    "process_stop": CAPABILITY_PROCESS,
    "service_status": CAPABILITY_SERVICE,
    "service_restart": CAPABILITY_SERVICE,
    # B28 req 92-98: the input family. Two plans collapse onto ``operator.key`` and five
    # onto ``operator.pointer`` for the reason the window family's six do.
    "press_key": CAPABILITY_KEY,
    "press_shortcut": CAPABILITY_KEY,
    "pointer_move": CAPABILITY_POINTER,
    "pointer_click": CAPABILITY_POINTER,
    "pointer_double_click": CAPABILITY_POINTER,
    "pointer_right_click": CAPABILITY_POINTER,
    "pointer_scroll": CAPABILITY_POINTER,
    # B29 req 99-103: three UI Automation actions onto ``operator.ui``, two reads onto
    # ``operator.inspect``.
    "ui_invoke": CAPABILITY_UI,
    "ui_set_value": CAPABILITY_UI,
    "ui_select": CAPABILITY_UI,
    "ui_read": CAPABILITY_INSPECT,
    "ui_inspect": CAPABILITY_INSPECT,
    # ADR-0199 stage 2.
    "pointer_session_begin": CAPABILITY_POINTER_SESSION,
}

# --------------------------------------------------------------- the retired names

#: CLOSED as of 2026-09-10. Every capability name an operator action receipt was written
#: under before this vocabulary was unified, mapped to the capability that answers it now.
#: These rows are still in ``activity_events`` and in ``evolution_opportunities`` titles;
#: this table is how ``where_is_capability`` turns one of them back into a file to open.
#:
#: Keys are literals in a module-level mapping ON PURPOSE — that is the shape
#: ``app.selfmodel.indexer._mapped_capabilities`` indexes (ADR-0111), the same shape that
#: makes ``app.alarms.sequence.RECEIPT_BY_DEVICE_CALL``'s receipt names findable.
#:
#: Nothing appends to this. A plan added after 2026-09-10 mints under the tool capability
#: from its first run, so it has no retired name to record; the structural test asserts the
#: list is exactly what is written here.
RETIRED_RECEIPT_CAPABILITIES: Final[dict[str, str]] = {
    "operator.activate_window": CAPABILITY_WINDOW_CONTROL,
    "operator.close_window": CAPABILITY_WINDOW_CONTROL,
    "operator.maximize_window": CAPABILITY_WINDOW_CONTROL,
    "operator.minimize_window": CAPABILITY_WINDOW_CONTROL,
    "operator.open_application": CAPABILITY_APP_OPEN,
    "operator.previous_window": CAPABILITY_WINDOW_CONTROL,
    "operator.restore_window": CAPABILITY_WINDOW_CONTROL,
    "operator.shell_query_hostname": CAPABILITY_SHELL,
    "operator.shell_query_ip": CAPABILITY_SHELL,
    "operator.type_text": CAPABILITY_TYPE,
}

#: The f-string that minted them, kept as the one place the old rule is written down. The
#: structural test uses it to prove every retired name really is
#: ``"operator." + <a plan this package runs>`` and not an invention.
RETIRED_RECEIPT_PREFIX: Final = "operator."


def receipt_capability_for_plan(plan_name: str) -> str:
    """The capability a plan's receipt and activity event are recorded under.

    ``KeyError`` for an unknown plan, and that is the intended failure: it cannot be
    reached from a running action because :class:`app.operator.service.Plan` refuses to be
    constructed with a name this table does not carry, so the error surfaces before a
    device is touched rather than after one was and the receipt is the thing that broke.
    """
    return RECEIPT_BY_PLAN[plan_name]


def is_known_plan(plan_name: str) -> bool:
    return plan_name in RECEIPT_BY_PLAN


def capability_for_retired_name(name: str) -> str | None:
    """The capability that answers a receipt name written under the old vocabulary."""
    return RETIRED_RECEIPT_CAPABILITIES.get(name)


__all__ = [
    "CAPABILITY_APP_CLOSE",
    "CAPABILITY_APP_OPEN",
    "CAPABILITY_CANCEL",
    "CAPABILITY_INSPECT",
    "CAPABILITY_PROCESS",
    "CAPABILITY_SERVICE",
    "PLAN_BY_PROCESS_ACTION",
    "PLAN_BY_SERVICE_ACTION",
    "PLAN_CLOSE_APPLICATION",
    "CAPABILITY_KEY",
    "CAPABILITY_MISSION",
    "CAPABILITY_POINTER",
    "CAPABILITY_POINTER_SESSION",
    "CAPABILITY_SCREENSHOT",
    "CAPABILITY_SEE",
    "CAPABILITY_SHELL",
    "CAPABILITY_UI",
    "PLAN_BY_POINTER_ACTION",
    "PLAN_BY_READ_MODE",
    "PLAN_BY_UI_ACTION",
    "PLAN_UI_INSPECT",
    "PLAN_UI_READ",
    "PLAN_POINTER_SESSION_BEGIN",
    "PLAN_PRESS_KEY",
    "PLAN_PRESS_SHORTCUT",
    "CAPABILITY_STATUS",
    "CAPABILITY_TYPE",
    "CAPABILITY_WINDOW_CONTROL",
    "OPERATOR_CAPABILITIES",
    "PLAN_BY_SHELL_QUERY",
    "PLAN_BY_WINDOW_ACTION",
    "PLAN_OPEN_APPLICATION",
    "PLAN_TYPE_TEXT",
    "RECEIPT_BY_PLAN",
    "RETIRED_RECEIPT_CAPABILITIES",
    "RETIRED_RECEIPT_PREFIX",
    "capability_for_retired_name",
    "is_known_plan",
    "receipt_capability_for_plan",
]
