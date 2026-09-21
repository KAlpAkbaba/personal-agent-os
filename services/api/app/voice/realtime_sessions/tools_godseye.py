"""God's Eye View by voice (owner addition 3 of 2026-09-21, docs/DECISIONS.md ADR-0197).

One tool: ``godseye.open`` - "Dünya gözünü aç." opens the page the Cloud Core serves
(``Settings.gods_eye_url``, the aux workload on the tailnet) in the OWNER'S OWN browser:
``browser.session_open`` on the owner-attached profile (the same profile and the same
narrow READ+NAVIGATE policy the media player uses), a NEW tab (never the tab the owner is
working in), ``browser.navigate`` to the URL, then the session is closed again - the tab
stays, the session was only the way to open it. No content is chosen for the owner: the
URL is configuration, and an empty one is said aloud rather than opened as nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.config import get_settings
from app.media.playback_service import (
    CAPABILITY_SESSION_CLOSE,
    CAPABILITY_SESSION_OPEN,
    CAPABILITY_TAB_NEW,
    OWNER_ATTACHED_PROFILE,
    TIMEOUT_SESSION_OPEN_S,
)

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_GODS_EYE_OPEN: Final = "godseye.open"
CAPABILITY_NAVIGATE: Final = "browser.navigate"
SESSION_KIND: Final = "page"
TIMEOUT_NAVIGATE_S: Final = 30.0

SPEECH_OPENED_TR: Final = "Dünya gözünü açtım efendim."
SPEECH_NOT_DEPLOYED_TR: Final = "Dünya gözü henüz kurulu değil efendim."
SPEECH_NO_DEVICE_TR: Final = "Bilgisayarınıza şu an ulaşamıyorum efendim; dünya gözünü açamadım."
SPEECH_NO_BROWSER_TR: Final = (
    "Tarayıcınıza bağlanamadım efendim; dünya gözünü açamadım. "
    "Chrome'u yetkilendirmek için enroll-owner-chrome betiğini çalıştırın."
)
SPEECH_FAILED_TR: Final = "Dünya gözünü açamadım efendim."


def _settings(ctx: ToolContext) -> Any:
    return ctx.live.get("settings") or get_settings()


def _refused(reason: str, speech: str, **extra: Any) -> dict[str, Any]:
    return {"status": "refused", "reason": reason, "speech": speech, **extra}


def godseye_open(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Dünya gözünü aç." - the configured page, in a new tab of the owner's browser."""
    del arguments
    url = str(getattr(_settings(ctx), "gods_eye_url", "") or "").strip()
    if not url:
        return _refused("not_deployed", SPEECH_NOT_DEPLOYED_TR)
    device_action = ctx.live.get("device_action")
    if device_action is None:
        return _refused("no_device_runtime", SPEECH_NO_DEVICE_TR)
    session_id = f"owner-page-{ctx.call_id or ctx.session_id}"
    prefix = f"godseye:{session_id}"
    opened = device_action.run(
        capability=CAPABILITY_SESSION_OPEN,
        payload={
            "session_id": session_id,
            "session_kind": SESSION_KIND,
            "profile": OWNER_ATTACHED_PROFILE,
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
            "channel": "chrome",
        },
        idempotency_key=f"{prefix}:session_open",
        timeout_s=TIMEOUT_SESSION_OPEN_S,
    )
    if not opened.ok:
        speech = (
            SPEECH_NO_BROWSER_TR
            if opened.error_class in ("capability_missing", "dependency_unavailable")
            else SPEECH_FAILED_TR
        )
        return _refused("browser_unavailable", speech, error_class=opened.error_class)
    try:
        tab = device_action.run(
            capability=CAPABILITY_TAB_NEW,
            payload={"session_id": session_id},
            idempotency_key=f"{prefix}:tab_new",
            timeout_s=TIMEOUT_SESSION_OPEN_S,
        )
        if not tab.ok:
            return _refused("tab_failed", SPEECH_FAILED_TR, error_class=tab.error_class)
        navigated = device_action.run(
            capability=CAPABILITY_NAVIGATE,
            payload={"session_id": session_id, "url": url},
            idempotency_key=f"{prefix}:navigate",
            timeout_s=TIMEOUT_NAVIGATE_S,
        )
        if not navigated.ok:
            return _refused("navigate_failed", SPEECH_FAILED_TR, error_class=navigated.error_class)
    finally:
        # The tab is the owner's now; the session was only the hand that opened it.
        device_action.run(
            capability=CAPABILITY_SESSION_CLOSE,
            payload={"session_id": session_id},
            idempotency_key=f"{prefix}:session_close",
            timeout_s=TIMEOUT_SESSION_OPEN_S,
        )
    body = navigated.result if isinstance(navigated.result, dict) else {}
    return {
        "status": "opened",
        "url": url,
        "observed_url": body.get("url"),
        "speech": SPEECH_OPENED_TR,
    }


def register_godseye_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_GODS_EYE_OPEN,
            description=(
                "DÜNYA GÖZÜNÜ (God's Eye View: canlı uçaklar, gemiler, uydular, depremler "
                "üzerinde 3B dünya) sahibin kendi tarayıcısında YENİ SEKMEDE açar: 'dünya "
                "gözünü aç', \"god's eye view'ı aç\" denince. Dönen 'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=godseye_open,
        )
    )
    return reg


__all__ = ["TOOL_GODS_EYE_OPEN", "godseye_open", "register_godseye_tools"]
