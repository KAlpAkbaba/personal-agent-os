"""The Digital Operator (docs/M19_DIGITAL_OPERATOR_SPEC.md).

OBSERVE -> PLAN -> ACT -> OBSERVE AGAIN -> VERIFY POSTCONDITION over the owner's Windows
desktop, through the same canonical path every M18 capability uses: the ONE router
(``app.voice.intents``) -> a voice tool (``app.voice.realtime_sessions.tools_operator``)
-> a ``DeviceActionPort`` (``app.routines.dispatch.DeviceActionPort``, the SAME protocol
``app.alarms.sequence.WakeSequence`` runs against) -> the device -> a receipt.

``task.py`` is the loop and its data; ``plans.py`` the deterministic step sequences the
voice tools build; ``focus.py`` the durable, generic object-focus stack (window/app, ADR-
0082); ``service.py`` the process-wide runtime that runs a plan synchronously inside a
tool call and keeps the one currently-running task.
"""

from __future__ import annotations
