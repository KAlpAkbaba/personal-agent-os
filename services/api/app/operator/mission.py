"""Operator missions: OBSERVE -> DECIDE -> ACT -> VERIFY -> REPLAN, bounded (B39 req 106,
112-115, 127-130).

A ``Mission`` is what the owner asked for in one breath - "Chrome'u aç ve YouTube'a gir",
"Ayarlarda Bluetooth'u aç", "Not Defteri'ni aç ve merhaba yaz" - as a short list of
:class:`MissionStep` (what each part asks for, never how). ``run_mission_step`` is the
closed loop for ONE step: it OBSERVES the desktop first, DECIDES the concrete
``OperatorStep`` plan from what it sees (the same plans ``app.operator.plans`` builds for
the voice tools, so nothing here is a second way to touch the desktop), ACTS through the
one ``DeviceActionPort`` and VERIFIES through those plans' own postconditions; when that
fails it reads the failure's class and chooses from a declared table - try again, look
again and rebuild the plan, climb one rung of the interaction ladder (a vision provider
locating what the UI Automation tree could not), or stop and ask the owner. Every round is
one entry in the mission's trail.

What this module is NOT: a model deciding the next click. The decision table is declared,
the plans are the deterministic ones, the ladder is spec §2's, and every bound is a
constant below. The owner sees the plan before anything runs when they ask for it
(``preview``), and can pause or cancel between rounds.
"""

from __future__ import annotations

import base64
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from app.operator import plans
from app.operator.adapters import adapter_for, button_query
from app.operator.allowlists import APP_IMAGES, APP_NAMES_TR
from app.operator.task import (
    ERROR_CANCELLED,
    ERROR_MODAL_OPEN,
    ERROR_POSTCONDITION_FAILED,
    ERROR_PRECONDITION_FAILED,
    ERROR_TIMEOUT,
    LEVEL_API,
    LEVEL_KEYBOARD,
    LEVEL_POINTER,
    LEVEL_UI_AUTOMATION,
    LEVEL_VISUAL,
    STATUS_SUCCEEDED,
    OperatorStep,
    OperatorTask,
    new_task,
    run_task,
)
from app.operator.vision import VisionError, VisionProvider
from app.routines.dispatch import DeviceActionPort

# ------------------------------------------------------------------ vocabulary

MISSION_PLANNED: Final = "planned"
MISSION_AWAITING_APPROVAL: Final = "awaiting_approval"
MISSION_RUNNING: Final = "running"
MISSION_PAUSED: Final = "paused"
MISSION_SUCCEEDED: Final = "succeeded"
MISSION_FAILED: Final = "failed"
MISSION_CANCELLED: Final = "cancelled"
MISSION_STATUSES: Final[tuple[str, ...]] = (
    MISSION_PLANNED,
    MISSION_AWAITING_APPROVAL,
    MISSION_RUNNING,
    MISSION_PAUSED,
    MISSION_SUCCEEDED,
    MISSION_FAILED,
    MISSION_CANCELLED,
)
TERMINAL_MISSION_STATUSES: Final = frozenset({MISSION_SUCCEEDED, MISSION_FAILED, MISSION_CANCELLED})

STEP_PENDING: Final = "pending"
STEP_RUNNING: Final = "running"
STEP_DONE: Final = "done"
STEP_FAILED: Final = "failed"
STEP_SKIPPED: Final = "skipped"

#: What a mission step asks for - the whole vocabulary the rule planner emits. Each has a
#: DECIDE function below that turns it into a concrete plan from a fresh observation.
KIND_APP_OPEN: Final = "app_open"
KIND_NAVIGATE: Final = "navigate"
KIND_TYPE_TEXT: Final = "type_text"
KIND_UI_INVOKE: Final = "ui_invoke"
KIND_WINDOW_CLOSE: Final = "window_close"
KIND_SETTINGS_OPEN: Final = "settings_open"
KIND_EXPLORER_OPEN: Final = "explorer_open"
KIND_IDE_OPEN_FILE: Final = "ide_open_file"
KIND_OFFICE_TYPE: Final = "office_type"
#: 2026-09-18 (owner: "ekrandaki söylediğim şeyin yerini bulup mouse'u götürüp sol klik
#: ile açacak"): a thing the owner can SEE on the screen, clicked where it is.
KIND_CLICK_TEXT: Final = "click_text"
#: Owner scenario 2026-09-18: the browser as the owner uses it - tabs, and the video that
#: is already on the screen but not playing.
KIND_TAB_SWITCH: Final = "tab_switch"
KIND_TAB_CLOSE: Final = "tab_close"
KIND_TAB_NEW: Final = "tab_new"
KIND_VIDEO_PLAY: Final = "video_play"
#: "Arama kısmına Tosun Paşa yaz", "YouTube sekmesinde aramaya X yaz": the site's own search,
#: on the tab that is open (owner, 2026-09-19 - the sentence had been typed into the address
#: bar whole, as text, and the model then improvised with Ctrl+K).
KIND_SITE_SEARCH: Final = "site_search"
STEP_KINDS: Final[tuple[str, ...]] = (
    KIND_APP_OPEN,
    KIND_NAVIGATE,
    KIND_TYPE_TEXT,
    KIND_UI_INVOKE,
    KIND_WINDOW_CLOSE,
    KIND_SETTINGS_OPEN,
    KIND_EXPLORER_OPEN,
    KIND_IDE_OPEN_FILE,
    KIND_OFFICE_TYPE,
)
#: Steps that touch the browser family (req 127: a mixed plan is operator + browser steps
#: over the ONE device port; nothing else distinguishes them).
BROWSER_KINDS: Final = frozenset({KIND_NAVIGATE})

# ------------------------------------------------------------ the failure taxonomy

#: Req 114/115: what one failure class earns. Declared, bounded, read by the loop - never a
#: heuristic on the message text.
STRATEGY_RETRY: Final = "retry"  # the same plan once more (a transient device fault)
STRATEGY_REOBSERVE: Final = "reobserve"  # look again and rebuild the plan (req 113)
STRATEGY_ESCALATE: Final = "escalate"  # the next rung of the ladder (req 115, 106)
STRATEGY_OWNER: Final = "owner"  # stop; this is the owner's to decide (req 115)
STRATEGY_STOP: Final = "stop"  # the owner already said so

STRATEGY_BY_ERROR_CLASS: Final[dict[str, str]] = {
    ERROR_TIMEOUT: STRATEGY_RETRY,
    "dependency_unavailable": STRATEGY_RETRY,
    "device_error": STRATEGY_RETRY,
    "internal_bug": STRATEGY_RETRY,
    ERROR_POSTCONDITION_FAILED: STRATEGY_REOBSERVE,
    ERROR_PRECONDITION_FAILED: STRATEGY_REOBSERVE,
    "ui_target_not_found": STRATEGY_REOBSERVE,
    "focus_mismatch": STRATEGY_REOBSERVE,
    "no_current_window": STRATEGY_REOBSERVE,
    "window_not_found": STRATEGY_REOBSERVE,
    ERROR_MODAL_OPEN: STRATEGY_OWNER,
    "permission_denied": STRATEGY_OWNER,
    "validation_error": STRATEGY_OWNER,
    "security_scope_error": STRATEGY_OWNER,
    "capability_missing": STRATEGY_OWNER,
    "no_capable_device": STRATEGY_OWNER,
    "vision_unavailable": STRATEGY_OWNER,
    ERROR_CANCELLED: STRATEGY_STOP,
}
#: A class the table does not name is the owner's: the loop never guesses at a retry.
STRATEGY_DEFAULT: Final = STRATEGY_OWNER

MAX_RETRIES_PER_STEP: Final = 2
MAX_REPLANS_PER_STEP: Final = 2
MAX_ROUNDS_PER_STEP: Final = 6
MAX_MISSION_STEPS: Final = 6

#: Spec §2's ladder, top rung first. ``escalate`` moves one rung DOWN this tuple from the
#: rung the failed plan used; only the UI Automation -> visual move exists today (req 106:
#: the vision provider is the fifth rung and the pointer the sixth, used together).
LADDER: Final[tuple[str, ...]] = (
    LEVEL_API,
    "dom",
    LEVEL_UI_AUTOMATION,
    LEVEL_KEYBOARD,
    LEVEL_VISUAL,
    LEVEL_POINTER,
)
#: The kinds whose plan may be rebuilt on the visual rung after the tree failed them.
VISUAL_FALLBACK_KINDS: Final = frozenset({KIND_UI_INVOKE})

# ------------------------------------------------------------------- the records


class MissionClarificationNeeded(Exception):
    """The sentence the owner hears when their words made no mission."""

    def __init__(self, speech: str) -> None:
        self.speech = speech
        super().__init__(speech)


@dataclass(slots=True)
class MissionStep:
    id: str
    kind: str
    args: dict[str, Any]
    label_tr: str
    status: str = STEP_PENDING
    rounds: int = 0
    retries: int = 0
    replans: int = 0
    level: str = ""
    error_class: str = ""
    message: str = ""
    observed: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "args": dict(self.args),
            "label_tr": self.label_tr,
            "status": self.status,
            "rounds": self.rounds,
            "retries": self.retries,
            "replans": self.replans,
            "level": self.level,
            "error_class": self.error_class,
            "message": self.message,
            "observed": dict(self.observed),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MissionStep:
        return cls(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or ""),
            args=dict(raw.get("args") or {}),
            label_tr=str(raw.get("label_tr") or ""),
            status=str(raw.get("status") or STEP_PENDING),
            rounds=int(raw.get("rounds") or 0),
            retries=int(raw.get("retries") or 0),
            replans=int(raw.get("replans") or 0),
            level=str(raw.get("level") or ""),
            error_class=str(raw.get("error_class") or ""),
            message=str(raw.get("message") or ""),
            observed=dict(raw.get("observed") or {}),
        )


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass(slots=True)
class Mission:
    id: uuid.UUID
    goal: str
    steps: list[MissionStep]
    status: str = MISSION_PLANNED
    #: Req 130: "önce göster" - the plan is spoken and nothing runs until ``approved``.
    preview: bool = False
    approved: bool = False
    current_step: int = 0
    #: Every round of every step, in order: what was observed, what was decided, what the
    #: device answered, what strategy the failure earned. The evidence, not the summary.
    trail: list[dict[str, Any]] = field(default_factory=list)
    error_class: str = ""
    message: str = ""
    #: Set when the loop stopped for the owner (req 115): the sentence and the step.
    escalation: dict[str, Any] | None = None
    pause_requested: bool = False
    cancel_requested: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mission_id": str(self.id),
            "goal": self.goal,
            "steps": [s.as_dict() for s in self.steps],
            "status": self.status,
            "preview": self.preview,
            "approved": self.approved,
            "current_step": self.current_step,
            "step_count": len(self.steps),
            "trail": [dict(t) for t in self.trail],
            "error_class": self.error_class,
            "message": self.message,
            "escalation": dict(self.escalation) if self.escalation else None,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Mission:
        return cls(
            id=uuid.UUID(str(raw["mission_id"])),
            goal=str(raw.get("goal") or ""),
            steps=[MissionStep.from_dict(s) for s in raw.get("steps") or []],
            status=str(raw.get("status") or MISSION_PLANNED),
            preview=bool(raw.get("preview")),
            approved=bool(raw.get("approved")),
            current_step=int(raw.get("current_step") or 0),
            trail=[dict(t) for t in raw.get("trail") or []],
            error_class=str(raw.get("error_class") or ""),
            message=str(raw.get("message") or ""),
            escalation=dict(raw["escalation"]) if raw.get("escalation") else None,
            created_at=_parse(raw.get("created_at")) or datetime.now(UTC),
            started_at=_parse(raw.get("started_at")),
            completed_at=_parse(raw.get("completed_at")),
        )

    def plan_speech(self) -> str:
        """Req 130: the plan as the owner hears it before anything runs."""
        parts = [f"{i + 1}. {s.label_tr}" for i, s in enumerate(self.steps)]
        return "Planım şu efendim: " + "; ".join(parts) + ". Başlayayım mı?"


# -------------------------------------------------------------------- the ports


@dataclass(slots=True)
class MissionPorts:
    """Everything the loop may touch: the ONE device port (operator and browser families
    both ride it), the vision provider when one is configured, and nothing else."""

    device: DeviceActionPort
    vision: VisionProvider | None = None


@dataclass(slots=True)
class Observation:
    """What the desktop looked like right before a decision (req 112: observe FIRST)."""

    foreground: dict[str, Any] | None
    windows: list[dict[str, Any]]

    def window_of_image(self, image: str) -> dict[str, Any] | None:
        wanted = _bare_image(image)
        for window in self.windows:
            if _bare_image(str(window.get("image") or "")) == wanted:
                return window
        if self.foreground and _bare_image(str(self.foreground.get("image") or "")) == wanted:
            return self.foreground
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "foreground": dict(self.foreground) if self.foreground else None,
            "window_count": len(self.windows),
        }


def _bare_image(image: str) -> str:
    return image.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def observe(ports: MissionPorts, mission_id: uuid.UUID, tag: str) -> Observation:
    """``window.current`` + ``window.list``: two reads, no action. A device that cannot
    answer is an empty observation, and the decision that follows says so."""
    fg = ports.device.run(
        capability="window.current",
        payload={},
        idempotency_key=f"mission:{mission_id}:{tag}:current",
        timeout_s=10.0,
    )
    listed = ports.device.run(
        capability="window.list",
        payload={},
        idempotency_key=f"mission:{mission_id}:{tag}:list",
        timeout_s=10.0,
    )
    foreground = None
    if fg.ok and isinstance(fg.result, dict) and isinstance(fg.result.get("window"), dict):
        foreground = dict(fg.result["window"])
    windows: list[dict[str, Any]] = []
    if listed.ok and isinstance(listed.result, dict):
        windows = [dict(w) for w in listed.result.get("windows") or [] if isinstance(w, dict)]
    return Observation(foreground=foreground, windows=windows)


# ------------------------------------------------------------------- DECIDE


class NeedsOwner(Exception):
    """A decision that cannot be made without the owner (req 115): the class names why."""

    def __init__(self, error_class: str, speech: str) -> None:
        self.error_class = error_class
        self.speech = speech
        super().__init__(speech)


@dataclass(slots=True)
class Decision:
    plan_name: str
    steps: list[OperatorStep]
    level: str
    note: str = ""
    #: False for a plan that only PREPARES the step (a search typed before the thing can be
    #: found on the screen): the loop re-observes and decides again instead of marking the
    #: step done.
    finishes_step: bool = True


#: Req 123: the Settings pages the owner names -> the search words the Settings app
#: itself understands in this locale (measured 2026-09-15 on the owner's desktop:
#: typing the word into the Settings search box and pressing Enter opens the page).
SETTINGS_PAGES: Final[dict[str, str]] = {
    "bluetooth": "Bluetooth",
    "wifi": "Wi-Fi",
    "wi-fi": "Wi-Fi",
    "kablosuz": "Wi-Fi",
    "ağ": "Ağ",
    "ag": "Ağ",
    "ekran": "Ekran",
    "ses": "Ses",
    "güncelleme": "Windows Update",
    "guncelleme": "Windows Update",
    "bildirim": "Bildirimler",
    "gizlilik": "Gizlilik",
    "hesap": "Hesaplar",
    "saat": "Saat",
    "dil": "Dil",
    "yazıcı": "Yazıcılar",
    "yazici": "Yazıcılar",
    "fare": "Fare",
    "klavye": "Klavye",
    "depolama": "Depolama",
    "pil": "Pil",
    "uygulama": "Uygulamalar",
}
#: The Settings window's title in this locale (the host is ApplicationFrameHost.exe, so a
#: window is recognised by its title, never by its image).
SETTINGS_TITLES: Final = ("Ayarlar", "Settings")

#: Req 124: the folders the owner names -> the shell folder Explorer navigates to when the
#: name is typed into its address bar (locale-independent), and the title it shows after.
EXPLORER_FOLDERS: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "indirilenler": ("shell:Downloads", ("İndirilenler", "Downloads")),
    "belgeler": ("shell:Personal", ("Belgeler", "Documents")),
    "belgelerim": ("shell:Personal", ("Belgeler", "Documents")),
    "masaüstü": ("shell:Desktop", ("Masaüstü", "Desktop")),
    "masaustu": ("shell:Desktop", ("Masaüstü", "Desktop")),
    "resimler": ("shell:My Pictures", ("Resimler", "Pictures")),
    "videolar": ("shell:My Video", ("Videolar", "Videos")),
    "müzik": ("shell:My Music", ("Müzik", "Music")),
    "muzik": ("shell:My Music", ("Müzik", "Music")),
}

#: Req 127: the sites the owner names without a domain.
KNOWN_SITES: Final[dict[str, str]] = {
    "youtube": "https://www.youtube.com/",
    "google": "https://www.google.com/",
    "github": "https://github.com/",
    "wikipedia": "https://tr.wikipedia.org/",
    "vikipedi": "https://tr.wikipedia.org/",
    "twitter": "https://x.com/",
    "x": "https://x.com/",
    "instagram": "https://www.instagram.com/",
    "linkedin": "https://www.linkedin.com/",
    "reddit": "https://www.reddit.com/",
    "netflix": "https://www.netflix.com/",
    "spotify": "https://open.spotify.com/",
    "gmail": "https://mail.google.com/",
    "outlook": "https://outlook.live.com/",
}
_DOMAIN_RE: Final = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)+$")
_SETTINGS_PAGE_RE: Final = re.compile(r"^[A-Za-zÇĞİÖŞÜçğıöşü\- ]{1,40}$")

MISSION_SESSION_KIND: Final = "operator_mission"
OWNER_ATTACHED_PROFILE: Final = "owner"
#: The browsers a mission drives by keyboard - the app id a plan opens -> its image. The
#: owner's Chrome is the default (decision 2026-09-18); a browser the owner NAMED ("Microsoft
#: Edge'i aç ve YouTube'a git", production 2026-09-19 09:39) is the one the steps after it use.
CHROME_IMAGE: Final = "chrome.exe"
BROWSER_APP_IMAGES: Final[dict[str, str]] = {"chrome": CHROME_IMAGE, "msedge": "msedge.exe"}
BROWSER_NAMES_TR: Final[dict[str, str]] = {CHROME_IMAGE: "Chrome", "msedge.exe": "Edge"}
_IN_YOUR_BROWSER_TR: Final[dict[str, str]] = {
    CHROME_IMAGE: "Chrome'unuzda",
    "msedge.exe": "Edge'inizde",
}


def _browser_image_of(step: MissionStep) -> str:
    """The browser this step drives: the one the plan opened before it, when that was not
    Chrome (``_with_browser_in_front`` stamps it), else the owner's Chrome."""
    image = str(step.args.get("browser") or "")
    return image if image in BROWSER_NAMES_TR else CHROME_IMAGE


def _window_id_of(window: dict[str, Any] | None) -> str | None:
    if not window:
        return None
    value = window.get("window_id")
    return str(value) if value else None


def _require_foreground(obs: Observation, what: str) -> str:
    window_id = _window_id_of(obs.foreground)
    if window_id is None:
        raise NeedsOwner("no_current_window", f"{what} için öndeki pencereyi göremedim efendim.")
    return window_id


def _decide_app_open(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    application = str(step.args.get("application") or "")
    image = APP_IMAGES.get(application, "")
    already = obs.window_of_image(image) if image else None
    if already is not None and _window_id_of(already):
        # Req 113: the plan changes with what is seen - the application is already on the
        # desktop, so the mission activates that window instead of launching a second one.
        return Decision(
            "activate_window",
            plans.activate_window(str(already["window_id"])),
            LEVEL_API,
            note=f"{application} zaten açık; pencereyi öne aldım",
        )
    return Decision("open_application", plans.open_application(application), LEVEL_API)


def _decide_navigate(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    """Req 127: the browser half of a plan, in the OWNER'S OWN Chrome (owner decision
    2026-09-18: "kendi Chrome'umu kullansın, sanki ben klavyeyi ve mouse'u kullanıyormuşum
    gibi"). Attached through its debugging endpoint when that is open - the DOM rung, the
    highest this browser offers. Otherwise driven the way the owner drives it: its window in
    front, Ctrl+L, the address typed, Enter, the title read back. There is no fallback to a
    separate automation profile any more: a window the owner does not use is not "Chrome'dan
    aç", and it is what made every mission fail on 2026-09-18 (a profile name the agent never
    had)."""
    url = str(step.args.get("url") or "")
    if not url.startswith(("http://", "https://")):
        raise NeedsOwner("validation_error", "Gidilecek adresi anlayamadım efendim.")
    browser = _browser_image_of(step)
    if browser == CHROME_IMAGE and _attach_owner_chrome(step, mission_id):
        return Decision(
            "browser_navigate",
            plans.browser_navigate(url),
            "dom",
            note="sahibin kendi tarayıcısında",
        )
    window = _owner_chrome_window(obs, browser)
    if window is None:
        raise NeedsOwner(
            "dependency_unavailable",
            f"Açık bir {BROWSER_NAMES_TR[browser]} penceresi bulamadım efendim.",
        )
    return Decision(
        "keyboard_navigate",
        plans.keyboard_navigate(str(window["window_id"]), url),
        LEVEL_KEYBOARD,
        note=f"{_IN_YOUR_BROWSER_TR[browser]}, klavyeyle",
    )


def _owner_chrome_window(obs: Observation, image: str = CHROME_IMAGE) -> dict[str, Any] | None:
    """The owner's browser window (Chrome unless the plan named another): the one in front
    when it is that browser, else any open one."""
    fg = obs.foreground
    if fg and _bare_image(str(fg.get("image") or "")) == image and _window_id_of(fg):
        return fg
    window = obs.window_of_image(image)
    return window if window is not None and _window_id_of(window) else None


def _attach_owner_chrome(step: MissionStep, mission_id: uuid.UUID) -> bool:
    """True when the owner's running Chrome answers on its debugging endpoint. The usual
    answer is no - an everyday Chrome is not started with one - and that is not a failure,
    only the reason the keyboard rung is used."""
    ports: MissionPorts | None = _PORTS.get(mission_id)
    if ports is None:
        raise NeedsOwner("dependency_unavailable", "Tarayıcıya ulaşamadım efendim.")
    attached = ports.device.run(
        capability="browser.session_open",
        payload={
            "session_id": f"mission-{mission_id}",
            "session_kind": "media",
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
            "channel": "chrome",
            "profile": OWNER_ATTACHED_PROFILE,
        },
        idempotency_key=f"mission:{mission_id}:{step.id}:session:{step.rounds}",
        timeout_s=20.0,
    )
    if attached.ok:
        return True
    if attached.error_class in ("capability_missing", "dependency_unavailable"):
        return False
    raise NeedsOwner(attached.error_class or "device_error", "Tarayıcı oturumu açılamadı efendim.")


#: The ports of the missions being run right now, keyed by mission id - so a decision
#: that must ask the device (a browser session) reaches the same port the loop acts on.
_PORTS: dict[uuid.UUID, MissionPorts] = {}


def _decide_type_text(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    text = str(step.args.get("text") or "")
    if not text:
        raise NeedsOwner("validation_error", "Ne yazacağımı anlayamadım efendim.")
    window_id = _require_foreground(obs, "yazmak")
    return Decision("type_text", plans.type_text(window_id, text), LEVEL_UI_AUTOMATION)


def _decide_ui_invoke(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    name = str(step.args.get("name") or "")
    if not name:
        raise NeedsOwner("validation_error", "Hangi düğmeye basacağımı anlayamadım efendim.")
    window_id = _require_foreground(obs, "düğmeye basmak")
    return Decision(
        "ui_invoke", plans.ui_invoke(window_id, button_query(name)), LEVEL_UI_AUTOMATION
    )


def _decide_window_close(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del step, mission_id
    window_id = _require_foreground(obs, "kapatmak")
    return Decision("close_window", plans.close_window(window_id), LEVEL_API)


def _decide_settings_open(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    page = str(step.args.get("page") or "")
    if page and not _SETTINGS_PAGE_RE.match(page):
        raise NeedsOwner(
            "validation_error", "Hangi ayar sayfasını istediğinizi anlayamadım efendim."
        )
    already = next((w for w in obs.windows if str(w.get("title") or "") in SETTINGS_TITLES), None)
    return Decision(
        "open_settings",
        plans.open_settings(page, existing_window_id=_window_id_of(already)),
        LEVEL_KEYBOARD if page else LEVEL_API,
    )


def _decide_explorer_open(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del obs, mission_id
    folder = str(step.args.get("folder") or "")
    entry = EXPLORER_FOLDERS.get(folder.lower())
    if entry is None:
        raise NeedsOwner("validation_error", f"'{folder}' klasörünü tanımıyorum efendim.")
    target, titles = entry
    return Decision("explorer_open", plans.explorer_open(target, titles), LEVEL_KEYBOARD)


def _decide_ide_open_file(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    file_name = str(step.args.get("file") or "")
    if not file_name:
        raise NeedsOwner("validation_error", "Hangi dosyayı açacağımı anlayamadım efendim.")
    window = obs.window_of_image("code.exe")
    if window is None or not _window_id_of(window):
        raise NeedsOwner("window_not_found", "Açık bir VS Code penceresi göremedim efendim.")
    return Decision(
        "ide_open_file", plans.ide_open_file(str(window["window_id"]), file_name), LEVEL_KEYBOARD
    )


def _decide_office_type(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    text = str(step.args.get("text") or "")
    application = str(step.args.get("application") or "word")
    image = "winword.exe" if application == "word" else "excel.exe"
    if not text:
        raise NeedsOwner("validation_error", "Ne yazacağımı anlayamadım efendim.")
    window = obs.window_of_image(image)
    if window is None or not _window_id_of(window):
        name_tr = adapter_for(image).name_tr
        raise NeedsOwner("window_not_found", f"Açık bir {name_tr} penceresi göremedim efendim.")
    return Decision(
        "office_type",
        plans.office_type(str(window["window_id"]), image, text),
        LEVEL_KEYBOARD,
    )


DECIDERS: Final[dict[str, Callable[[MissionStep, Observation, uuid.UUID], Decision]]] = {
    KIND_APP_OPEN: _decide_app_open,
    KIND_NAVIGATE: _decide_navigate,
    KIND_TYPE_TEXT: _decide_type_text,
    KIND_UI_INVOKE: _decide_ui_invoke,
    KIND_WINDOW_CLOSE: _decide_window_close,
    KIND_SETTINGS_OPEN: _decide_settings_open,
    KIND_EXPLORER_OPEN: _decide_explorer_open,
    KIND_IDE_OPEN_FILE: _decide_ide_open_file,
    KIND_OFFICE_TYPE: _decide_office_type,
}


def decide(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    decider = DECIDERS.get(step.kind)
    if decider is None:
        raise NeedsOwner("validation_error", f"'{step.kind}' türünde bir adımı bilmiyorum efendim.")
    return decider(step, obs, mission_id)


# ------------------------------------------------------------ the visual rung (106)


def _locate_on_screen(
    ports: MissionPorts, mission_id: uuid.UUID, step: MissionStep, window_id: str, name: str
) -> tuple[int, int]:
    """Capture the window, ask the vision provider WHERE ``name`` is, and return the screen
    point to click - or stop for the owner, saying which of those could not be done."""
    if ports.vision is None or not hasattr(ports.vision, "locate"):
        raise NeedsOwner(
            "vision_unavailable",
            "Ekrandan bakarak bulmam gerekiyordu ama görsel sağlayıcı tanımlı değil efendim.",
        )
    capture = ports.device.run(
        capability="screen.capture",
        payload={"window_id": window_id},
        idempotency_key=f"mission:{mission_id}:{step.id}:capture:{step.rounds}",
        timeout_s=15.0,
    )
    body = capture.result if capture.ok and isinstance(capture.result, dict) else {}
    encoded = str(body.get("png_base64") or "")
    if not capture.ok or not encoded:
        raise NeedsOwner(capture.error_class or "device_error", "Ekranı yakalayamadım efendim.")
    try:
        png = base64.b64decode(encoded)
        location = ports.vision.locate(png, target=name)
    except (VisionError, ValueError) as exc:
        raise NeedsOwner("vision_unavailable", "Görsel sağlayıcı cevap veremedi efendim.") from exc
    if location is None:
        raise NeedsOwner("ui_target_not_found", f"Ekranda '{name}' diye bir şey göremedim efendim.")
    scale = float(body.get("scale") or 1)
    return int(round(location.x * scale)), int(round(location.y * scale))


def _decide_visual(
    step: MissionStep, obs: Observation, ports: MissionPorts, mission_id: uuid.UUID
) -> Decision:
    """Req 106: the fifth rung. The tree could not find the element; capture the window,
    ask the vision provider WHERE the named thing is, and click there - then read the
    tree again so the click is judged by something the click did not write."""
    if step.kind not in VISUAL_FALLBACK_KINDS:
        raise NeedsOwner("vision_unavailable", "Bu adım için görsel bir yedek yolum yok efendim.")
    window_id = _require_foreground(obs, "ekrana bakmak")
    name = str(step.args.get("name") or "")
    x, y = _locate_on_screen(ports, mission_id, step, window_id, name)
    return Decision(
        "pointer_click",
        plans.visual_click(window_id, x, y, absent_name=name),
        LEVEL_VISUAL,
        note=f"görsel yedek: '{name}' ({x},{y})",
    )


def _decide_click_text(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    """Something the owner sees on the screen - a video's title, a link, a label - clicked
    the way the owner would: the pointer moved there, a left click.

    Why the picture and not the tree first: the thing is inside a WEB PAGE, and the tree the
    device can read stops at five levels / 200 nodes, far above a page's links; the rung that
    can actually see it is the screen. And the proof is not the click: the window's title
    must CHANGE afterwards (a video opened, a page moved on), read by a separate look - the
    tree-absence check the button rung uses would pass before the click, too, on a page whose
    links the tree never reaches."""
    ports: MissionPorts | None = _PORTS.get(mission_id)
    if ports is None:
        raise NeedsOwner("dependency_unavailable", "Ekrana ulaşamadım efendim.")
    name = str(step.args.get("name") or "")
    if not name:
        raise NeedsOwner("validation_error", "Neye tıklayacağımı anlayamadım efendim.")
    window_id = _require_foreground(obs, "tıklamak")
    title_before = str((obs.foreground or {}).get("title") or "")
    try:
        x, y = _locate_on_screen(ports, mission_id, step, window_id, name)
    except NeedsOwner as exc:
        if exc.error_class != "ui_target_not_found" or not step.args.get("search_if_missing"):
            raise
        if step.args.get("searched") or "youtube" not in title_before.lower():
            raise
        # Owner scenario 2026-09-18: the video is not on this tab - look for it the way
        # the owner would, in the site's own search, then look at the screen again.
        step.args["searched"] = True
        url = SEARCH_URLS["youtube"].format(q=_readable_query(name))
        return Decision(
            "search_first",
            plans.keyboard_navigate(window_id, url),
            LEVEL_KEYBOARD,
            note=f"bu sekmede yok; YouTube'da '{name}' aradım",
            finishes_step=False,
        )
    return Decision(
        "screen_click",
        plans.click_and_expect_change(window_id, x, y, title_before=title_before),
        LEVEL_VISUAL,
        note=f"ekranda '{name}' ({x},{y})",
    )


def _owner_browser_window(obs: Observation, what: str, *, image: str = CHROME_IMAGE) -> str:
    window = _owner_chrome_window(obs, image)
    if window is None:
        raise NeedsOwner(
            "dependency_unavailable",
            f"{what} için açık bir {BROWSER_NAMES_TR[image]} penceresi bulamadım efendim.",
        )
    return str(window["window_id"])


def _decide_tab_switch(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    window_id = _owner_browser_window(obs, "sekme değiştirmek", image=_browser_image_of(step))
    title_before = str((obs.foreground or {}).get("title") or "")
    index = step.args.get("index")
    direction = str(step.args.get("direction") or "next")
    return Decision(
        "tab_switch",
        plans.tab_switch(
            window_id,
            direction=direction,
            index=int(index) if isinstance(index, int) else None,
            title_before=title_before,
        ),
        LEVEL_KEYBOARD,
    )


def _decide_tab_close(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    window_id = _owner_browser_window(obs, "sekmeyi kapatmak", image=_browser_image_of(step))
    title_before = str((obs.foreground or {}).get("title") or "")
    return Decision(
        "tab_close", plans.tab_close(window_id, title_before=title_before), LEVEL_KEYBOARD
    )


def _decide_tab_new(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    del mission_id
    window_id = _owner_browser_window(obs, "yeni sekme açmak", image=_browser_image_of(step))
    return Decision("tab_new", plans.tab_new(window_id), LEVEL_KEYBOARD)


def _decide_video_play(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    """The video on the screen that is not playing (a paused player, the replay circle in the
    owner's screenshot): find the player, click it, and prove it PLAYS - two captures a
    moment apart must differ where the picture is. A title cannot tell playing from paused."""
    ports: MissionPorts | None = _PORTS.get(mission_id)
    if ports is None:
        raise NeedsOwner("dependency_unavailable", "Ekrana ulaşamadım efendim.")
    window_id = _require_foreground(obs, "videoyu oynatmak")
    x, y = _locate_on_screen(ports, mission_id, step, window_id, VIDEO_PLAYER_TARGET_TR)
    return Decision(
        "video_play",
        plans.click_and_expect_motion(window_id, x, y),
        LEVEL_VISUAL,
        note=f"oynatıcı ({x},{y})",
    )


#: What the picture is asked for when the owner says "videoyu oynat" and names nothing.
VIDEO_PLAYER_TARGET_TR: Final = "sayfadaki büyük video oynatıcısının ortası (oynat düğmesi)"


def _decide_site_search(step: MissionStep, obs: Observation, mission_id: uuid.UUID) -> Decision:
    """The site's search, the way the owner does it: the results page of the site whose tab
    is in front (YouTube when the title says so, else Google), reached through the address
    bar of the owner's own browser window and verified by the title that comes back."""
    del mission_id
    query = str(step.args.get("query") or "").strip()
    if not query:
        raise NeedsOwner("validation_error", "Neyi arayacağımı anlayamadım efendim.")
    site = str(step.args.get("site") or "")
    title = str((obs.foreground or {}).get("title") or "").lower()
    if site not in SEARCH_URLS:
        site = "youtube" if "youtube" in title else "google"
    browser = _browser_image_of(step)
    window = _owner_chrome_window(obs, browser)
    if window is None:
        raise NeedsOwner(
            "dependency_unavailable",
            f"Aramak için açık bir {BROWSER_NAMES_TR[browser]} penceresi bulamadım efendim.",
        )
    url = SEARCH_URLS[site].format(q=_readable_query(query))
    return Decision(
        "site_search",
        plans.keyboard_navigate(str(window["window_id"]), url),
        LEVEL_KEYBOARD,
        note=f"{SITE_NAMES_TR[site]}'da '{query}' aradım",
    )


DECIDERS[KIND_CLICK_TEXT] = _decide_click_text
DECIDERS[KIND_SITE_SEARCH] = _decide_site_search
DECIDERS[KIND_TAB_SWITCH] = _decide_tab_switch
DECIDERS[KIND_TAB_CLOSE] = _decide_tab_close
DECIDERS[KIND_TAB_NEW] = _decide_tab_new
DECIDERS[KIND_VIDEO_PLAY] = _decide_video_play


# -------------------------------------------------------------------- the loop


def _strategy_for(task: OperatorTask) -> str:
    return STRATEGY_BY_ERROR_CLASS.get(task.error_class or "", STRATEGY_DEFAULT)


def _next_rung(level: str) -> str | None:
    """The rung below ``level`` that this loop can actually climb to: only visual today."""
    if level in (LEVEL_UI_AUTOMATION, LEVEL_KEYBOARD):
        return LEVEL_VISUAL
    return None


def _trail(mission: Mission, step: MissionStep, **fields: Any) -> None:
    mission.trail.append(
        {
            "step": step.id,
            "round": step.rounds,
            "at": _iso(datetime.now(UTC)),
            **fields,
        }
    )


def run_mission_step(
    mission: Mission,
    index: int,
    ports: MissionPorts,
    *,
    on_task: Callable[[Mission, MissionStep, OperatorTask], None] | None = None,
) -> Mission:
    """One step's closed loop (module docstring). Returns the mission with the step
    ``done``, or the mission stopped: ``paused`` with an ``escalation`` for the owner,
    ``cancelled``, or ``failed`` when the bounds ran out."""
    step = mission.steps[index]
    mission.current_step = index
    mission.status = MISSION_RUNNING
    if mission.started_at is None:
        mission.started_at = datetime.now(UTC)
    step.status = STEP_RUNNING
    level_override: str | None = None
    _PORTS[mission.id] = ports

    try:
        return _run_rounds(mission, step, ports, level_override, on_task)
    finally:
        _PORTS.pop(mission.id, None)


def _run_rounds(
    mission: Mission,
    step: MissionStep,
    ports: MissionPorts,
    level_override: str | None,
    on_task: Callable[[Mission, MissionStep, OperatorTask], None] | None,
) -> Mission:
    while step.rounds < MAX_ROUNDS_PER_STEP:
        if mission.cancel_requested:
            return _stop(mission, step, MISSION_CANCELLED, ERROR_CANCELLED, "iptal edildi")
        step.rounds += 1
        obs = observe(ports, mission.id, f"{step.id}:{step.rounds}")
        try:
            if level_override == LEVEL_VISUAL:
                decision = _decide_visual(step, obs, ports, mission.id)
            else:
                decision = decide(step, obs, mission.id)
        except NeedsOwner as exc:
            step.error_class = exc.error_class
            step.message = exc.speech
            _trail(mission, step, observed=obs.as_dict(), decided=None, owner=exc.error_class)
            return _escalate(mission, step, exc.speech)
        step.level = decision.level
        task = new_task(goal=step.label_tr, plan_name=decision.plan_name, steps=decision.steps)
        run_task(task, ports.device)
        if on_task is not None:
            on_task(mission, step, task)
        step.observed = dict(task.last_observed)
        if task.status == STATUS_SUCCEEDED and not decision.finishes_step:
            _trail(
                mission,
                step,
                observed=obs.as_dict(),
                decided=decision.plan_name,
                level=decision.level,
                note=decision.note,
                outcome="prepared",
            )
            continue
        if task.status == STATUS_SUCCEEDED:
            step.status = STEP_DONE
            step.error_class = ""
            step.message = decision.note
            _trail(
                mission,
                step,
                observed=obs.as_dict(),
                decided=decision.plan_name,
                level=decision.level,
                note=decision.note,
                outcome="verified",
            )
            return mission
        strategy = _strategy_for(task)
        step.error_class = task.error_class or ""
        step.message = task.error_message
        _trail(
            mission,
            step,
            observed=obs.as_dict(),
            decided=decision.plan_name,
            level=decision.level,
            outcome=task.status,
            error_class=step.error_class,
            strategy=strategy,
        )
        if strategy == STRATEGY_STOP or mission.cancel_requested:
            return _stop(mission, step, MISSION_CANCELLED, ERROR_CANCELLED, "iptal edildi")
        if strategy == STRATEGY_RETRY and step.retries < MAX_RETRIES_PER_STEP:
            step.retries += 1
            continue
        if strategy == STRATEGY_REOBSERVE and step.replans < MAX_REPLANS_PER_STEP:
            step.replans += 1
            continue
        if strategy in (STRATEGY_REOBSERVE, STRATEGY_ESCALATE) and level_override is None:
            rung = _next_rung(decision.level)
            if rung is not None and step.kind in VISUAL_FALLBACK_KINDS:
                level_override = rung
                continue
        return _escalate(mission, step, _owner_speech(step))
    return _stop(
        mission,
        step,
        MISSION_FAILED,
        step.error_class or "bounds_exhausted",
        f"{step.label_tr}: {MAX_ROUNDS_PER_STEP} turda sonuç alamadım",
    )


def _owner_speech(step: MissionStep) -> str:
    reason = {
        ERROR_MODAL_OPEN: "bir iletişim kutusu açık",
        "permission_denied": "izin verilmemiş",
        "validation_error": "cihaz isteği geçersiz buldu",
        "capability_missing": "cihaz bu yeteneğe sahip değil",
        "no_capable_device": "bunu yapabilecek bir cihaz çevrimiçi değil",
        ERROR_POSTCONDITION_FAILED: "sonucu doğrulayamadım",
        "ui_target_not_found": "aradığım öğeyi bulamadım",
    }.get(step.error_class, step.error_class or "bilinmeyen bir nedenle")
    return f"'{step.label_tr}' adımında durdum efendim: {reason}. Nasıl devam edeyim?"


def _escalate(mission: Mission, step: MissionStep, speech: str) -> Mission:
    step.status = STEP_FAILED
    mission.status = MISSION_PAUSED
    mission.escalation = {"step": step.id, "speech": speech, "error_class": step.error_class}
    mission.message = speech
    return mission


def _stop(
    mission: Mission, step: MissionStep, status: str, error_class: str, message: str
) -> Mission:
    step.status = STEP_FAILED if status == MISSION_FAILED else STEP_SKIPPED
    mission.status = status
    mission.error_class = error_class
    mission.message = message
    mission.completed_at = datetime.now(UTC)
    return mission


def run_mission(
    mission: Mission,
    ports: MissionPorts,
    *,
    on_task: Callable[[Mission, MissionStep, OperatorTask], None] | None = None,
) -> Mission:
    """Every remaining step in order (the synchronous form the tests and the inline
    service use; the Temporal workflow drives ``run_mission_step`` one at a time so a
    pause or a cancel lands between rounds)."""
    if mission.preview and not mission.approved:
        mission.status = MISSION_AWAITING_APPROVAL
        return mission
    for index in range(mission.current_step, len(mission.steps)):
        if mission.steps[index].status == STEP_DONE:
            continue
        if mission.pause_requested:
            mission.status = MISSION_PAUSED
            mission.pause_requested = False
            return mission
        run_mission_step(mission, index, ports, on_task=on_task)
        if mission.status != MISSION_RUNNING:
            return mission
    mission.status = MISSION_SUCCEEDED
    mission.completed_at = datetime.now(UTC)
    return mission


def resume(mission: Mission) -> Mission:
    """Req 129: continue from the step that stopped; an escalated step gets a fresh set of
    rounds because the owner has (presumably) changed what the loop will see."""
    if mission.status not in (MISSION_PAUSED, MISSION_AWAITING_APPROVAL):
        return mission
    if mission.escalation is not None:
        step = mission.steps[mission.current_step]
        step.rounds = 0
        step.retries = 0
        step.replans = 0
        step.status = STEP_PENDING
        mission.escalation = None
    mission.approved = True
    mission.status = MISSION_RUNNING
    mission.pause_requested = False
    return mission


# ------------------------------------------------------------ the rule planner (127)

_CONNECTOR_RE: Final = re.compile(
    r"\s*(?:,|\bve\b|\bsonra\b|\bardından\b|\bardindan\b|\bdaha sonra\b)\s*"
)
PREVIEW_MARKERS: Final[tuple[str, ...]] = (
    "önce göster",
    "once goster",
    "yapmadan önce",
    "yapmadan once",
    "planı göster",
    "plani goster",
    "önizle",
    "onizle",
)
_OPEN_VERBS: Final = ("aç", "ac", "başlat", "baslat")
_GO_VERBS: Final = ("gir", "git", "gidelim", "girelim", "aç", "ac")
_WRITE_VERBS: Final = ("yaz", "yazsana", "yazar")


def _tokens(text: str) -> tuple[str, ...]:
    from app.voice.intents import normalize_transcript

    return normalize_transcript(text)[1]


def _strip_suffix(token: str) -> str:
    return token.split("'", 1)[0]


def _segment_settings(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    # The NOUN (Ayarlar / Ayarlarda / Ayarları / settings), never the verb "ayarla"
    # (adjust) - "Işığı ayarla" is the scene's, "şehrimi ... ayarla" the location's.
    if not any(_strip_suffix(t).startswith(("ayarlar", "settings")) for t in tokens):
        return None
    # A file named "ayarlar.json" copied, moved or read is the document family's; the
    # Settings app is OPENED, and never together with a file or folder noun.
    if any(
        t.startswith(("dosya", "klasör", "klasor", "kopyala", "taşı", "tasi", "json", "sil", "oku"))
        for t in tokens
    ):
        return None
    if not any(
        _strip_suffix(t) in _OPEN_VERBS or _strip_suffix(t) in ("git", "gir") for t in tokens
    ):
        return None
    page = ""
    for tok in tokens:
        bare = _strip_suffix(tok)
        for stem, name in SETTINGS_PAGES.items():
            if bare.startswith(stem):
                page = name
                break
        if page:
            break
    label = f"Ayarlar'da {page} sayfasını aç" if page else "Ayarlar'ı aç"
    return MissionStep(id="", kind=KIND_SETTINGS_OPEN, args={"page": page}, label_tr=label)


def _segment_explorer(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    joined = " ".join(_strip_suffix(t) for t in tokens)
    # A search or a read INSIDE a folder is the document family's ("İndirilenler
    # klasöründe bütçe dosyasını ara"); only an OPEN of the folder itself is a mission.
    if any(t.startswith(("ara", "bul", "oku", "listele", "göster", "goster")) for t in tokens):
        return None
    if not any(
        _strip_suffix(t) in _OPEN_VERBS or _strip_suffix(t) in ("git", "gir") for t in tokens
    ):
        return None
    names_explorer = "gezgin" in joined or "explorer" in joined
    folder = next((k for k in EXPLORER_FOLDERS if k in {_strip_suffix(t) for t in tokens}), None)
    if folder is None or not (
        names_explorer or any(t.startswith("klasör") or t.startswith("klasor") for t in tokens)
    ):
        return None
    _target, titles = EXPLORER_FOLDERS[folder]
    return MissionStep(
        id="",
        kind=KIND_EXPLORER_OPEN,
        args={"folder": folder},
        label_tr=f"Dosya Gezgini'nde {titles[0]} klasörünü aç",
    )


def _segment_ide(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    joined = " ".join(_strip_suffix(t) for t in tokens)
    if not (
        "vs code" in joined
        or "visual studio code" in joined
        or "kod editör" in joined
        or "vscode" in joined
    ):
        return None
    if not any(t.startswith("dosya") for t in tokens):
        return None
    match = re.search(r"([\w.\-]+\.[A-Za-z0-9]{1,8})", raw)
    if match is None:
        return None
    file_name = match.group(1)
    return MissionStep(
        id="",
        kind=KIND_IDE_OPEN_FILE,
        args={"file": file_name},
        label_tr=f"VS Code'da {file_name} dosyasını aç",
    )


def _segment_office(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    bare = [_strip_suffix(t) for t in tokens]
    application = "word" if "word" in bare else "excel" if "excel" in bare else None
    if application is None or not any(t.startswith(v) for t in tokens for v in _WRITE_VERBS):
        return None
    text = _quoted_or_before_verb(raw, tokens, skip=("word", "excel", "e", "a"))
    if not text:
        return None
    name_tr = "Word" if application == "word" else "Excel"
    return MissionStep(
        id="",
        kind=KIND_OFFICE_TYPE,
        args={"application": application, "text": text},
        label_tr=f"{name_tr}'e '{text}' yaz",
    )


#: Brand words that are part of a BROWSER's name when the next word is that browser - never
#: a destination. "Google Chrome'dan direkt YouTube ana sayfasını aç" was planned as
#: "google.com adresine git" (production, 2026-09-18): the first known site in the sentence
#: won, and it was the browser's own first name.
_BROWSER_BRANDS: Final[dict[str, tuple[str, ...]]] = {
    "google": ("chrome",),
    "microsoft": ("edge",),
    "mozilla": ("firefox",),
}


def _browser_name_positions(tokens: tuple[str, ...]) -> set[int]:
    """Indices of tokens that are a browser's brand word ("google" in "Google Chrome")."""
    out: set[int] = set()
    for i, tok in enumerate(tokens[:-1]):
        follow = _BROWSER_BRANDS.get(_strip_suffix(tok))
        if follow and any(tokens[i + 1].startswith(name) for name in follow):
            out.add(i)
    return out


#: Where a site's own search lives. "YouTube'da X", "Google'da X ara": the locative is
#: Turkish for "search on", and the owner's prototype did exactly this - the words typed
#: into the browser and Enter pressed (owner, 2026-09-18).
SEARCH_URLS: Final[dict[str, str]] = {
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "google": "https://www.google.com/search?q={q}",
}
_LOCATIVE_SUFFIXES: Final[frozenset[str]] = frozenset({"da", "de", "ta", "te"})
#: Words in a search sentence that are the request, not the thing searched for.
_SEARCH_STOP: Final[frozenset[str]] = frozenset(
    {
        "ara",
        "arat",
        "bul",
        "aç",
        "ac",
        "oynat",
        "gir",
        "git",
        "göster",
        "goster",
        "chrome",
        "chromedan",
        "tarayıcıdan",
        "tarayicidan",
        "edge",
        "edgeden",
        "edgede",
        "lütfen",
        "lutfen",
        "bana",
        "benim",
        "için",
        "icin",
    }
)


#: Characters that would change what a search address MEANS; they are dropped from the words.
_URL_SIGNIFICANT: Final = "&#?%/=+"


def _readable_query(query: str) -> str:
    """The words as the owner would see them typed into the address bar - "Barış+Manço",
    not "Bar%C4%B1%C5%9F+Man%C3%A7o". Chrome takes the Unicode as it is; only the
    characters that would change the address's meaning are dropped."""
    # a separator, never glued: "5+3" must not become "53"
    cleaned = "".join(" " if ch in _URL_SIGNIFICANT else ch for ch in query)
    return "+".join(cleaned.split())


def _says_search(tokens: tuple[str, ...]) -> bool:
    return any(
        t in ("ara", "arat", "aratır", "bul", "bulur") or t.startswith("arat") for t in tokens
    )


SITE_NAMES_TR: Final[dict[str, str]] = {"youtube": "YouTube", "google": "Google"}


def _site_in_locative(tokens: tuple[str, ...], index: int) -> str | None:
    """The site of "YouTube'da" at ``index``, whichever apostrophe the transcriber used:
    the curly one the normaliser cuts into "youtube" + "da", or the straight one it keeps
    glued as "youtube'da" (what the production ASR writes, 2026-09-19 - every "YouTube'da X
    ara" had been "none" to the router while the curly test sentences passed)."""
    tok = tokens[index]
    stem, _, suffix = tok.partition("'")
    if stem not in SEARCH_URLS:
        return None
    if suffix:
        return stem if suffix in _LOCATIVE_SUFFIXES else None
    if index + 1 < len(tokens) and tokens[index + 1] in _LOCATIVE_SUFFIXES:
        return stem
    return None


def _segment_search(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    """ "YouTube'da Barış Manço aç" -> the site's own search results for "Barış Manço"."""
    brand_words = _browser_name_positions(tokens)
    for index, tok in enumerate(tokens):
        if index in brand_words:
            continue
        site = _site_in_locative(tokens, index)
        if site is None:
            continue
        tok = site
        # The words searched for come from the owner's own sentence, each cut at its
        # apostrophe: the normaliser splits "Chrome'dan" into "chrome" + "dan", and a token
        # list would carry that "dan" into the search ("dan sezen aksu", 2026-09-18).
        rest: list[str] = []
        for word in raw.split():
            base = word
            for mark in _APOSTROPHES:
                base = base.split(mark, 1)[0]
            base = base.strip(_WORD_EDGE_PUNCTUATION)
            head = _head(base)
            if not head or head in SEARCH_URLS or head in _SEARCH_STOP:
                continue
            if head.startswith("video") or head in _BROWSER_BRANDS:
                continue
            rest.append(base)
        query = " ".join(rest)
        url = SEARCH_URLS[tok].format(q=_readable_query(query))
        site = "YouTube" if tok == "youtube" else "Google"
        return MissionStep(
            id="",
            kind=KIND_NAVIGATE,
            # Flagged only when the owner said SEARCH ("ara", "arat", "bul"): "YouTube'da X
            # aç" keeps its existing media-player route; the flag is what the router reads.
            args={"url": url, **({"search": True} if _says_search(tokens) else {})},
            label_tr=f"{site}'da '{query}' ara",
        )
    return None


#: Words that may keep a site's name company in "open the site" without naming anything
#: else: the browser, the request itself, and the page/address noun in its forms.
_NAVIGATE_FILLER_STEMS: Final[tuple[str, ...]] = (
    "sayfa",
    "adres",
    "site",
    "direkt",
    "ana",
    "lütfen",
    "lutfen",
    "bana",
    "hemen",
    "şimdi",
    "simdi",
    "tarayıcı",
    "tarayici",
    "chrome",
    "edge",
    "firefox",
)


def _names_more_than_the_site(tokens: tuple[str, ...], site_index: int) -> bool:
    """True when the sentence carries a content word besides the site, the browser, the
    verb and the page noun - "YouTube'dan Sezen Aksu Gülümse aç" names a SONG, and opening
    youtube.com would lose it (that sentence is the media player's, corpus m.play)."""
    brand_words = _browser_name_positions(tokens)
    for index, tok in enumerate(tokens):
        if index == site_index or index in brand_words:
            continue
        bare = _strip_suffix(tok)
        if len(bare) <= 3:  # apostrophe suffixes split off by the normaliser: dan, da, u, ı
            continue
        if bare in _GO_VERBS or tok in _GO_VERBS:
            continue
        if bare.startswith(_NAVIGATE_FILLER_STEMS):
            continue
        if _DOMAIN_RE.match(bare):
            continue
        return True
    return False


def _segment_navigate(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    if not any(_strip_suffix(t) in _GO_VERBS or t in _GO_VERBS for t in tokens):
        return None
    brand_words = _browser_name_positions(tokens)
    for index, tok in enumerate(tokens):
        if index in brand_words:
            continue
        bare = _strip_suffix(tok)
        if bare in KNOWN_SITES:
            if _names_more_than_the_site(tokens, index):
                return None
            url = KNOWN_SITES[bare]
            return MissionStep(
                id="", kind=KIND_NAVIGATE, args={"url": url}, label_tr=f"{url} adresine git"
            )
        if _DOMAIN_RE.match(bare) and not bare.endswith(".exe"):
            if _names_more_than_the_site(tokens, index):
                return None
            url = f"https://{bare}/"
            return MissionStep(
                id="", kind=KIND_NAVIGATE, args={"url": url}, label_tr=f"{url} adresine git"
            )
    return None


def _segment_app_open(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    from app.operator.plans import resolve_app_alias

    if not any(_strip_suffix(t) in _OPEN_VERBS for t in tokens):
        return None
    application = resolve_app_alias(tokens)
    if application is None:
        return None
    name_tr = APP_NAMES_TR.get(application, application)
    return MissionStep(
        id="",
        kind=KIND_APP_OPEN,
        args={"application": application},
        label_tr=f"{name_tr} uygulamasını aç",
    )


def _segment_ui_invoke(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    if not any(
        t.startswith("düğme") or t.startswith("dugme") or t.startswith("tuş") for t in tokens
    ):
        return None
    if not any(t.startswith(("tıkla", "tikla", "bas")) for t in tokens):
        return None
    match = re.search(r"([\wÇĞİÖŞÜçğıöşü]+)\s+d[üu][ğg]mesine", raw)
    if match is None:
        return None
    name = match.group(1)
    return MissionStep(
        id="", kind=KIND_UI_INVOKE, args={"name": name}, label_tr=f"{name} düğmesine bas"
    )


#: "arama kısmına", "arama kutusuna", "aramaya": the site's search box, named as a place.
_SEARCH_BOX_STEMS: Final[tuple[str, ...]] = ("arama", "aramaya", "aramada")
_SEARCH_BOX_PLACES: Final[frozenset[str]] = frozenset(
    {
        "kısmına",
        "kismina",
        "kısmında",
        "kisminda",
        "kutusuna",
        "kutusunda",
        "çubuğuna",
        "cubuguna",
        "bölümüne",
        "bolumune",
        "yerine",
        "alanına",
        "alanina",
        "barına",
        "barina",
    }
)
#: Words that place the search box (this tab, the site) and are never part of the query.
_SEARCH_CONTEXT_WORDS: Final[frozenset[str]] = frozenset(
    {
        "şu",
        "su",
        "anki",
        "şimdiki",
        "simdiki",
        "bu",
        "geçerli",
        "gecerli",
        "açık",
        "acik",
        "olan",
        "mevcut",
        "sekmede",
        "sekmedeki",
        "sekmesinde",
        "sekmesindeki",
        "sekmeye",
        "sayfada",
        "sayfadaki",
        "sayfasında",
        "sitesinde",
        "lütfen",
        "lutfen",
        "bana",
        "hemen",
    }
)
_SEARCH_BOX_VERBS: Final[tuple[str, ...]] = ("yaz", "ara", "arat", "gir", "bul")


def _segment_site_search(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    """ "Arama kısmına Tosun Paşa yaz", "Şu anki YouTube sekmesinde arama kısmına Tosun Paşa
    yaz", "Aramaya Barış Manço yaz": the site's search box, named as a place - so the words
    are a QUERY on the open tab, never text typed wherever the caret happens to be
    (production 2026-09-19 14:46: the whole sentence went into the address bar)."""
    box_at = next(
        (i for i, t in enumerate(tokens) if _strip_suffix(t).startswith(_SEARCH_BOX_STEMS)),
        None,
    )
    if box_at is None:
        return None
    head = _strip_suffix(tokens[box_at])
    place_at = (
        box_at + 1
        if box_at + 1 < len(tokens) and tokens[box_at + 1] in _SEARCH_BOX_PLACES
        else None
    )
    if head == "arama" and place_at is None:
        return None  # "arama" alone is a noun in many sentences; the PLACE makes it the box
    if not any(t.startswith(_SEARCH_BOX_VERBS) for t in tokens):
        return None
    site = next((s for s in SEARCH_URLS if any(_strip_suffix(t) == s for t in tokens)), None)
    words: list[str] = []
    for word in raw.split():
        base = word.strip(_WORD_EDGE_PUNCTUATION)
        for mark in _APOSTROPHES:
            base = base.split(mark, 1)[0]
        h = _head(base)
        if not h or h in _SEARCH_CONTEXT_WORDS or h in SEARCH_URLS or h in _BROWSER_BRANDS:
            continue
        if h.startswith(_SEARCH_BOX_STEMS) or h in _SEARCH_BOX_PLACES:
            continue
        if h.startswith(_SEARCH_BOX_VERBS) or h in _SEARCH_STOP:
            continue
        words.append(base)
    query = " ".join(words).strip()
    if not query:
        return None
    args: dict[str, Any] = {"query": query}
    if site:
        args["site"] = site
    where = f"{SITE_NAMES_TR[site]}'da" if site else "açık sekmede"
    return MissionStep(id="", kind=KIND_SITE_SEARCH, args=args, label_tr=f"{where} '{query}' ara")


def _segment_type_text(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    if not any(_strip_suffix(t) in _WRITE_VERBS or t.startswith("yaz") for t in tokens):
        return None
    text = _quoted_or_before_verb(raw, tokens, skip=("buraya", "oraya", "içine", "icine"))
    if not text:
        return None
    return MissionStep(id="", kind=KIND_TYPE_TEXT, args={"text": text}, label_tr=f"'{text}' yaz")


def _segment_window_close(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    if not any(t.startswith("kapat") for t in tokens):
        return None
    return MissionStep(id="", kind=KIND_WINDOW_CLOSE, args={}, label_tr="öndeki pencereyi kapat")


def _quoted_or_before_verb(raw: str, tokens: tuple[str, ...], *, skip: tuple[str, ...]) -> str:
    quoted = re.search(r"[\"'“”‘’]([^\"'“”‘’]{1,200})[\"'“”‘’]", raw)
    if quoted:
        return quoted.group(1).strip()
    words = list(tokens)
    verb_at = next((i for i, t in enumerate(words) if t.startswith("yaz")), None)
    if verb_at is None:
        return ""
    before = [
        w
        for w in words[:verb_at]
        if _strip_suffix(w) not in skip
        and not _strip_suffix(w).startswith("word")
        and not _strip_suffix(w).startswith("excel")
    ]
    return " ".join(before).strip()


#: The words that make a sentence about something ON THE SCREEN rather than an app or a site.
_CLICK_VERB_STEMS: Final[tuple[str, ...]] = ("tıkla", "tikla", "oynat", "başlat", "baslat")
_OPEN_OR_CLICK_WORDS: Final[frozenset[str]] = frozenset({"aç", "ac", "gir", "bas"})
_ON_SCREEN_WORDS: Final[frozenset[str]] = frozenset(
    {"ekranda", "ekrandaki", "sayfada", "sayfadaki", "sekmede", "sekmedeki", "sekmesinde"}
)
_BUTTON_WORDS: Final[tuple[str, ...]] = ("düğme", "dugme", "buton", "button", "tuş", "tus")
#: Words around a target that are not part of it ("ekranda ŞU ... YAZANA tıkla").
_TARGET_FILLERS: Final[frozenset[str]] = frozenset(
    {
        "ekranda",
        "ekrandaki",
        "sayfada",
        "sayfadaki",
        "şu",
        "su",
        "şuna",
        "bu",
        "buna",
        "yazana",
        "yazan",
        "yere",
        "yazıya",
        "yaziya",
        "lütfen",
        "lutfen",
        # "şu anki YouTube sekmesindeki Tosun Paşa videosunu aç" (owner, 2026-09-19): the tab
        # and the site place the video; the picture is asked for "Tosun Paşa", nothing else.
        "anki",
        "şimdiki",
        "simdiki",
        "geçerli",
        "gecerli",
        "açık",
        "acik",
        "olan",
        "görünen",
        "gorunen",
        "mevcut",
        "sekmede",
        "sekmedeki",
        "sekmesinde",
        "sekmesindeki",
        "youtube",
        "google",
    }
)
_POSITION_WORDS: Final[frozenset[str]] = frozenset(
    {
        "sağdan",
        "sagdan",
        "soldan",
        "üstten",
        "ustten",
        "alttan",
        "sağdaki",
        "sagdaki",
        "soldaki",
        "üstteki",
        "ustteki",
        "alttaki",
        "ortadaki",
    }
)
_ORDINAL_WORDS: Final[frozenset[str]] = frozenset(
    {"ilk", "birinci", "ikinci", "üçüncü", "ucuncu", "dördüncü", "dorduncu", "son", "sonuncu"}
)
_APOSTROPHES: Final[tuple[str, ...]] = ("'", "’")
_WORD_EDGE_PUNCTUATION: Final = ".,!?;:" + chr(34) + "“”"


def _head(word: str) -> str:
    normal = _tokens(word)
    return normal[0] if normal else ""


def _is_trigger(head: str) -> bool:
    return (
        head.startswith("video")
        or head in _OPEN_OR_CLICK_WORDS
        or head.startswith(_CLICK_VERB_STEMS)
    )


def _segment_click_text(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    """ "Atatürk belgeseli videosunu aç", "Tarkan'a tıkla", "Ekranda Abone ol yazana tıkla",
    "İlk videoyu oynat" - something visible, named by the owner. Last in the matcher order
    so a button ("... düğmesine tıkla"), an application or a site keeps its own reading."""
    # A BUTTON is the tree's ("İptal butonuna tıkla" -> ui.invoke by name, a rung above the
    # picture); claiming it here moved it off that rung (caught by test_operator_ui).
    if any(t.startswith(_BUTTON_WORDS) for t in tokens):
        return None
    # News is the news tool's ("Bugünün Show Ana Haber videosunu aç" -> news.open, with
    # the source it names), not a picture search on whatever page is open.
    if any(t.startswith("haber") for t in tokens):
        return None
    has_video = any(t.startswith("video") for t in tokens)
    has_click = any(t.startswith(("tıkla", "tikla")) for t in tokens)
    on_screen = any(t in _ON_SCREEN_WORDS for t in tokens)
    has_verb = any(t in _OPEN_OR_CLICK_WORDS or t.startswith(_CLICK_VERB_STEMS) for t in tokens)
    if not has_verb or not (has_video or has_click or on_screen):
        return None
    words: list[str] = []
    for word in raw.split():
        bare = word
        for mark in _APOSTROPHES:
            bare = bare.split(mark, 1)[0]
        head = _head(bare)  # "YouTube'da" is the site + a suffix, never a name (2026-09-19)
        if _is_trigger(head):
            break
        if head in _TARGET_FILLERS:
            continue
        words.append(word.strip(_WORD_EDGE_PUNCTUATION))
    if words:
        last = words[-1]
        for mark in _APOSTROPHES:
            last = last.split(mark, 1)[0]
        words[-1] = last
    words = [w for w in words if w]
    if not words:
        return None
    target = " ".join(words)
    positional = any(_head(w) in _ORDINAL_WORDS or _head(w) in _POSITION_WORDS for w in words)
    if has_video and positional:
        target = f"{target} video"
    args: dict[str, Any] = {"name": target}
    if has_video and not positional:
        # A video named by the owner may not be on this tab (owner scenario 2026-09-18);
        # "sağdan üçüncü video" is a place on THIS screen and is never searched for, and
        # a button or a link ("Abone ol", "Tarkan") is not a video.
        args["search_if_missing"] = True
    return MissionStep(
        id="",
        kind=KIND_CLICK_TEXT,
        args=args,
        label_tr=f"ekranda '{target}' yazan yere tıkla",
    )


_TAB_STEMS: Final[tuple[str, ...]] = ("sekme",)
_NEXT_WORDS: Final[frozenset[str]] = frozenset(
    {"yan", "yandaki", "sonraki", "sağdaki", "sagdaki", "diğer", "diger", "öteki", "oteki"}
)
_PREV_WORDS: Final[frozenset[str]] = frozenset(
    {"önceki", "onceki", "soldaki", "geri", "bir önceki", "bir onceki"}
)
_ORDINALS: Final[dict[str, int]] = {
    "ilk": 1,
    "birinci": 1,
    "ikinci": 2,
    "üçüncü": 3,
    "ucuncu": 3,
    "dördüncü": 4,
    "dorduncu": 4,
    "beşinci": 5,
    "besinci": 5,
    "altıncı": 6,
    "altinci": 6,
    "yedinci": 7,
    "sekizinci": 8,
    "son": 9,
    "sonuncu": 9,
    # "3. sekmeye geç" reaches the planner as the cardinal ("üç"), so those count too.
    "bir": 1,
    "iki": 2,
    "üç": 3,
    "uc": 3,
    "dört": 4,
    "dort": 4,
    "beş": 5,
    "bes": 5,
    "altı": 6,
    "alti": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
}
_DIGIT_ORDINAL_RE: Final = re.compile(r"^(\d)(?:\.|inci|nci|üncü|uncu|ıncı|inci)?$")


def _ordinal_of(token: str) -> int | None:
    if token in _ORDINALS:
        return _ORDINALS[token]
    match = _DIGIT_ORDINAL_RE.match(token)
    return int(match.group(1)) if match else None


def _segment_tab(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    """ "Yan sekmeye geç", "Önceki sekmeye geç", "Üçüncü sekmeye geç", "Sekmeyi kapat" - the
    owner's Chrome, by its own keyboard shortcuts. "Yeni sekmede X aç" is _segment_tab_new's."""
    if not any(t.startswith(_TAB_STEMS) for t in tokens):
        return None
    if any(t.startswith("yeni") for t in tokens):
        return None
    if any(t.startswith("kapat") for t in tokens):
        return MissionStep(id="", kind=KIND_TAB_CLOSE, args={}, label_tr="sekmeyi kapat")
    if not any(t.startswith(("geç", "gec", "git", "atla")) for t in tokens):
        return None
    for t in tokens:
        if t in _PREV_WORDS:
            return MissionStep(
                id="",
                kind=KIND_TAB_SWITCH,
                args={"direction": "prev"},
                label_tr="önceki sekmeye geç",
            )
    for t in tokens:
        n = _ordinal_of(t)
        if n is not None:
            return MissionStep(
                id="", kind=KIND_TAB_SWITCH, args={"index": n}, label_tr=f"{n}. sekmeye geç"
            )
    return MissionStep(
        id="", kind=KIND_TAB_SWITCH, args={"direction": "next"}, label_tr="yan sekmeye geç"
    )


def _segment_tab_new(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    """ "Yeni sekmede YouTube aç" - a new tab, then whatever the rest of the sentence opens
    (the planner appends the navigate step from the same words)."""
    if not (
        any(t.startswith("yeni") for t in tokens) and any(t.startswith(_TAB_STEMS) for t in tokens)
    ):
        return None
    return MissionStep(id="", kind=KIND_TAB_NEW, args={}, label_tr="yeni sekme aç")


_VIDEO_PLAY_VERBS: Final[tuple[str, ...]] = ("aç", "ac", "oynat", "başlat", "baslat", "devam")
_VIDEO_NOUNS: Final[frozenset[str]] = frozenset(
    {"video", "videoyu", "videoya", "videosunu", "film", "filmi"}
)


def _segment_video_play(tokens: tuple[str, ...], raw: str) -> MissionStep | None:
    """ "Videoyu aç", "Videoyu oynat", "Oynat" - the video already on the screen, no name."""
    content = [
        t
        for t in tokens
        if t not in _VIDEO_NOUNS
        and t not in _TARGET_FILLERS
        and not t.startswith(_VIDEO_PLAY_VERBS)
        and t not in ("et", "ettir")
    ]
    if content:
        return None
    if not any(t in _VIDEO_NOUNS for t in tokens) and not any(
        t.startswith(("oynat",)) for t in tokens
    ):
        return None
    if not any(t.startswith(_VIDEO_PLAY_VERBS) for t in tokens):
        return None
    return MissionStep(id="", kind=KIND_VIDEO_PLAY, args={}, label_tr="ekrandaki videoyu oynat")


SEGMENT_MATCHERS: Final[tuple[Callable[[tuple[str, ...], str], MissionStep | None], ...]] = (
    _segment_tab_new,
    _segment_tab,
    _segment_video_play,
    _segment_settings,
    _segment_explorer,
    _segment_ide,
    _segment_office,
    _segment_site_search,
    _segment_search,
    _segment_navigate,
    _segment_app_open,
    _segment_ui_invoke,
    # Before type_text: "Ekranda Abone ol YAZANA tıkla" names a thing to click, and
    # type_text's "yaz" read it as text to TYPE into the window (2026-09-18).
    _segment_click_text,
    _segment_type_text,
    _segment_window_close,
)


def plan_mission(text: str) -> Mission:
    """The owner's sentence -> a mission, or :class:`MissionClarificationNeeded`."""
    raw = (text or "").strip()
    if not raw:
        raise MissionClarificationNeeded("Ne yapmamı istediğinizi anlayamadım efendim.")
    lowered = raw.lower()
    preview = any(marker in lowered for marker in PREVIEW_MARKERS)
    cleaned = raw
    for marker in PREVIEW_MARKERS:
        cleaned = re.sub(re.escape(marker), " ", cleaned, flags=re.IGNORECASE)
    segments = [s for s in _CONNECTOR_RE.split(cleaned) if s and s.strip()]
    if len(segments) > MAX_MISSION_STEPS:
        raise MissionClarificationNeeded(
            f"Tek seferde en çok {MAX_MISSION_STEPS} adım alabiliyorum efendim."
        )
    steps: list[MissionStep] = []
    for segment in segments:
        tokens = _tokens(segment)
        if not tokens:
            continue
        step = next(
            (m(tokens, segment) for m in SEGMENT_MATCHERS if m(tokens, segment) is not None), None
        )
        if step is not None and step.kind == KIND_TAB_NEW:
            steps.append(step)
            rest = re.sub(r"(?i)yeni\s+sekmede?", " ", segment).strip()
            rest_tokens = _tokens(rest)
            step = None
            if rest_tokens:
                step = next(
                    (
                        m(rest_tokens, rest)
                        for m in SEGMENT_MATCHERS
                        if m(rest_tokens, rest) is not None
                    ),
                    None,
                )
                if step is None:
                    raise MissionClarificationNeeded(
                        f"'{rest}' kısmını nasıl yapacağımı bilmiyorum efendim."
                    )
            if step is None:
                continue
        if step is None:
            raise MissionClarificationNeeded(
                f"'{segment.strip()}' kısmını nasıl yapacağımı bilmiyorum efendim."
            )
        steps.append(step)
    if not steps:
        raise MissionClarificationNeeded("Ne yapmamı istediğinizi anlayamadım efendim.")
    steps = _with_browser_in_front(steps, named=_names_a_browser(_tokens(cleaned)))
    for index, step in enumerate(steps):
        step.id = f"m{index + 1}"
    return Mission(id=uuid.uuid4(), goal=raw[:300], steps=steps, preview=preview)


def _names_a_browser(tokens: tuple[str, ...]) -> str | None:
    """The app id of the browser the owner asked for in so many words - "Chrome'dan
    YouTube'u aç", "tarayıcıdan", "Edge'de YouTube'u aç" - or ``None`` when the sentence
    names no browser and the owner's Chrome is meant (decision 2026-09-18). Production
    2026-09-19 09:39: "Edge'de" was not a browser to this function, and Edge sentences got
    an implicit Chrome."""
    for t in tokens:
        if t.startswith(("chrome", "tarayıcı", "tarayici")):
            return "chrome"
        if t.startswith("edge"):
            return "msedge"
    return None


def _with_browser_in_front(
    steps: list[MissionStep], *, named: str | None = None
) -> list[MissionStep]:
    """A navigate step drives the owner's browser WINDOW (keyboard rung), so the plan must
    put that window in front first. "Chrome'dan YouTube'u aç" names no separate opening
    step; it gets one here - for the browser the owner NAMED, else Chrome, marked implicit.
    It never launches a second browser: the app-open decision activates the window that is
    already there (req 113). Every step after a browser other than Chrome is stamped with
    that browser's image, so the deciders drive Edge's window when Edge was asked for
    (production 2026-09-19 09:39:58: Edge, then an implicit Chrome, then a navigate that
    only knew Chrome)."""
    out: list[MissionStep] = []
    browser_up = False
    browser_image = ""
    for step in steps:
        if step.kind == KIND_APP_OPEN and step.args.get("application") in BROWSER_APP_IMAGES:
            browser_up = True
            browser_image = BROWSER_APP_IMAGES[str(step.args.get("application"))]
        if step.kind in (KIND_TAB_NEW, KIND_TAB_SWITCH, KIND_TAB_CLOSE):
            browser_up = True
        if step.kind in (KIND_NAVIGATE, KIND_SITE_SEARCH) and not browser_up:
            application = named or "chrome"
            out.append(
                MissionStep(
                    id="",
                    kind=KIND_APP_OPEN,
                    # IMPLICIT: the plan's own preparation, not something the owner asked
                    # for - the router counts only what the owner asked for when it decides
                    # whether a sentence is a multi-step mission (else "Haberleri YouTube'dan
                    # aç" stopped being the news tool's, CI 2026-09-18).
                    args={"application": application, **({} if named else {"implicit": True})},
                    label_tr=f"{APP_NAMES_TR.get(application, 'Chrome')} uygulamasını aç",
                )
            )
            browser_up = True
            browser_image = BROWSER_APP_IMAGES[application]
        if step.kind != KIND_APP_OPEN and browser_image and browser_image != CHROME_IMAGE:
            step.args["browser"] = browser_image
        out.append(step)
    return out


__all__ = [
    "BROWSER_KINDS",
    "DECIDERS",
    "EXPLORER_FOLDERS",
    "KIND_APP_OPEN",
    "KIND_CLICK_TEXT",
    "KIND_TAB_CLOSE",
    "KIND_TAB_NEW",
    "KIND_SITE_SEARCH",
    "KIND_TAB_SWITCH",
    "KIND_VIDEO_PLAY",
    "KIND_EXPLORER_OPEN",
    "KIND_IDE_OPEN_FILE",
    "KIND_NAVIGATE",
    "KIND_OFFICE_TYPE",
    "KIND_SETTINGS_OPEN",
    "KIND_TYPE_TEXT",
    "KIND_UI_INVOKE",
    "KIND_WINDOW_CLOSE",
    "KNOWN_SITES",
    "LADDER",
    "MAX_MISSION_STEPS",
    "MAX_REPLANS_PER_STEP",
    "MAX_RETRIES_PER_STEP",
    "MAX_ROUNDS_PER_STEP",
    "MISSION_AWAITING_APPROVAL",
    "MISSION_CANCELLED",
    "MISSION_FAILED",
    "MISSION_PAUSED",
    "MISSION_PLANNED",
    "MISSION_RUNNING",
    "MISSION_STATUSES",
    "MISSION_SUCCEEDED",
    "PREVIEW_MARKERS",
    "SETTINGS_PAGES",
    "STEP_DONE",
    "STEP_FAILED",
    "STEP_KINDS",
    "STEP_PENDING",
    "STEP_RUNNING",
    "STEP_SKIPPED",
    "STRATEGY_BY_ERROR_CLASS",
    "STRATEGY_DEFAULT",
    "STRATEGY_ESCALATE",
    "STRATEGY_OWNER",
    "STRATEGY_REOBSERVE",
    "STRATEGY_RETRY",
    "STRATEGY_STOP",
    "TERMINAL_MISSION_STATUSES",
    "VISUAL_FALLBACK_KINDS",
    "Decision",
    "Mission",
    "MissionClarificationNeeded",
    "MissionPorts",
    "MissionStep",
    "NeedsOwner",
    "Observation",
    "decide",
    "observe",
    "plan_mission",
    "resume",
    "run_mission",
    "run_mission_step",
]
