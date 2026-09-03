"""M13 stdio worker: ``python -m browser_agent.worker`` (contract §7).

Companion <-> worker protocol: newline-delimited UTF-8 JSON on stdin/stdout,
one object per line. **stdout carries protocol lines only** — every log goes
to stderr (``configure_logging(stream=sys.stderr)`` below). The worker never
crashes on a bad line: parsing/dispatch failures answer a typed error (or, if
even ``request_id`` could not be recovered, are logged and dropped) and the
loop continues.

Session model: one :class:`SessionState` per ``session_id`` holds a
:class:`~browser_agent.session.BrowserSession`/:class:`~browser_agent.backends.ManagedBackend`
pair, its (narrow-only) risk-class policy, and an ``asyncio.Lock`` — commands
against the SAME session run serially (the lock is held for the command's
duration); commands against DIFFERENT sessions run concurrently as separate
asyncio tasks. An idle-timeout reaper closes sessions that saw no command for
``idle_timeout_s``.

Every result is passed through two last-line guards before it is written
(:func:`_finalize_result`): a forbidden-key scan (contract §6) and a 48 KiB
size cap with truncation (contract §3).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import Frame, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import policy, search_engines
from .backends import ManagedBackend
from .destination import require_public_destination
from .detect import BrowserInfo, detect_browser
from .errors import BrowserError, ErrorClass, Phase, map_playwright_error, redact_url
from .extraction import (
    build_links,
    build_metadata,
    parse_json_ld,
    read_primary_text,
    read_raw_page_data,
    truncate_text,
)
from .injection import count_injection_markers
from .obs_logging import configure_logging, get_logger
from .page_kind import classify_page
from .session import BrowserSession
from .targets import coerce_target

logger = get_logger(__name__)

WORKER_VERSION = "0.1.0"
PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_NAV_TIMEOUT_MS = 15_000
DEFAULT_IDLE_TIMEOUT_S = 600
MAX_RESULT_BYTES = 48 * 1024
_REAP_INTERVAL_S = 15.0
_FETCH_EVIDENCE_SETTLE_S = 0.3
_SCREENSHOT_MAX_BYTES = 300 * 1024
_SCREENSHOT_QUALITIES: tuple[int, ...] = (80, 60, 40, 20)

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_CAPABILITY_NAME_RE = re.compile(policy.CAPABILITY_NAME_RE_SOURCE)

# Capabilities that own their own risk-class enforcement instead of the
# generic static-lookup path in ``Worker._execute`` (browser.click resolves
# its class dynamically from the clicked element; browser.download adds an
# authorization_ref requirement on top of the HIGH_IMPACT check).
_CUSTOM_ENFORCEMENT_CAPS = frozenset({"browser.click", "browser.download"})

# ----------------------------------------------------------------------- #
# forbidden-key scan + result size cap (contract §3, §6)
# ----------------------------------------------------------------------- #

#: Substrings a *normalized* (lowercased, non-alphanumerics stripped) key
#: must not contain. Normalizing before matching — not matching a raw
#: substring list — is deliberate: a prior project defect (see
#: services/api's realtime-session audit scrubber) showed that a raw
#: substring check against "api_key" misses "apiKey"/"api-key"/"APIKEY";
#: stripping separators and casing before comparison catches all of them.
_FORBIDDEN_KEY_TOKENS: tuple[str, ...] = (
    "cookie",
    "authorization",
    "setcookie",
    "localstorage",
    "sessionstorage",
    "password",
    "token",
    "secret",
    "apikey",
)


def _normalize_key(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


def _is_forbidden_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return any(token in normalized for token in _FORBIDDEN_KEY_TOKENS)


def redact_forbidden_keys(value: Any) -> tuple[Any, list[str]]:
    """Recursively replace any forbidden-matching dict key's value.

    Returns ``(redacted_value, matched_keys)``. This is a last-line guard —
    no operation in this package is expected to ever produce one of these
    keys — so a non-empty ``matched_keys`` is itself logged as a warning by
    the caller (it indicates a bug upstream, not normal operation).
    """
    found: list[str] = []

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            result: dict[str, Any] = {}
            for k, v in node.items():
                if isinstance(k, str) and _is_forbidden_key(k):
                    found.append(k)
                    result[k] = "<redacted:forbidden-key>"
                else:
                    result[k] = walk(v)
            return result
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(value), found


def cap_result_size(result: dict[str, Any], *, max_bytes: int = MAX_RESULT_BYTES) -> dict[str, Any]:
    """Shrink the largest string field(s) until the JSON encoding fits.

    Sets ``result["truncated"] = True`` when anything was cut. A
    pathological result that still cannot fit after emptying every string
    field collapses to a minimal marker object rather than being emitted
    oversized.
    """
    payload = dict(result)
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= max_bytes:
        return payload

    string_fields = sorted(
        (k for k, v in payload.items() if isinstance(v, str)),
        key=lambda k: len(payload[k]),
        reverse=True,
    )
    for key in string_fields:
        while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > max_bytes:
            current = payload[key]
            if len(current) <= 64:
                break
            payload[key] = current[: max(64, len(current) // 2)]
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) <= max_bytes:
            payload["truncated"] = True
            return payload

    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > max_bytes:
        payload = {"truncated": True, "note": "result exceeded max size and was minimized"}
    return payload


def _finalize_result(result: dict[str, Any]) -> dict[str, Any]:
    redacted, found = redact_forbidden_keys(result)
    if found:
        logger.warning("browser.forbidden_key_redacted", keys=found)
    return cap_result_size(redacted)


# ----------------------------------------------------------------------- #
# envelope helpers
# ----------------------------------------------------------------------- #


def _write_line(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _ok_result(request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "result",
        "request_id": request_id,
        "ok": True,
        "result": _finalize_result(result),
    }


def _err_result(request_id: str, error_class: str, message: str, retryable: bool) -> dict[str, Any]:
    return {
        "type": "result",
        "request_id": request_id,
        "ok": False,
        "error": {"class": error_class, "message": message[:2000], "retryable": retryable},
    }


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _require_session_id(payload: dict[str, Any]) -> str:
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not _SESSION_ID_RE.match(session_id):
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR,
            "session_id must be a 1-128 char string matching ^[A-Za-z0-9_.:-]+$",
            retryable=False,
        )
    return session_id


def _frame_root(page: Page, frame: str | None) -> Any:
    if frame is None:
        return page
    if not isinstance(frame, str) or not frame.strip():
        raise BrowserError(
            ErrorClass.VALIDATION_ERROR, "frame must be a non-empty iframe name", retryable=False
        )
    return page.frame_locator(f'iframe[name="{frame}"]')


async def _stdin_lines(loop: asyncio.AbstractEventLoop) -> AsyncIterator[str]:
    """Yield stdin lines as they arrive, without blocking the event loop.

    A background thread does the blocking ``readline()`` (portable across
    platforms, notably Windows, where wiring ``asyncio`` directly to a piped
    stdin is fragile) and hands lines to the loop via
    ``call_soon_threadsafe``.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _reader() -> None:
        try:
            for raw in sys.stdin:
                loop.call_soon_threadsafe(queue.put_nowait, raw)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    thread = threading.Thread(target=_reader, name="browser-worker-stdin", daemon=True)
    thread.start()
    while True:
        line = await queue.get()
        if line is None:
            return
        yield line


# ----------------------------------------------------------------------- #
# session state
# ----------------------------------------------------------------------- #


@dataclass(slots=True)
class SessionState:
    session_id: str
    browser_session: BrowserSession
    backend: ManagedBackend
    policy_allowed: frozenset[policy.RiskClass]
    visible: bool
    channel: str
    browser_version: str | None
    profile: str
    last_used: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# ----------------------------------------------------------------------- #
# shared DOM-read snippets for click/find target description
# ----------------------------------------------------------------------- #

_DESCRIBE_ELEMENT_JS = r"""
(el) => {
  const tag = el.tagName.toLowerCase();
  const explicitRole = el.getAttribute('role');
  const hasHref = tag === 'a' && el.hasAttribute('href');
  const inputType = (el.getAttribute('type') || '').toLowerCase();
  const isSubmitControl =
    (tag === 'button' && (inputType === 'submit' || inputType === '')) ||
    (tag === 'input' && inputType === 'submit');
  const form = el.closest('form');
  const inFormSubmitPath = !!form && isSubmitControl;
  const name = (el.getAttribute('aria-label') || el.innerText || el.value || '').trim();
  const hasOnclick = !!el.onclick || el.hasAttribute('onclick');
  let role = explicitRole;
  if (!role) {
    if (hasHref) role = 'link';
    else if (tag === 'button') role = 'button';
    else role = null;
  }
  return {
    tag: tag, role: role, name: name,
    has_href: hasHref, is_submit: inFormSubmitPath, has_onclick: hasOnclick,
  };
}
"""

_SCROLL_JS = r"""
(args) => {
  const doc = document.scrollingElement || document.documentElement;
  if (args.direction === 'down') window.scrollBy(0, args.amount);
  else if (args.direction === 'up') window.scrollBy(0, -args.amount);
  else if (args.direction === 'to_end') window.scrollTo(0, doc.scrollHeight);
  else if (args.direction === 'to_top') window.scrollTo(0, 0);
  const scrollY = doc.scrollTop;
  const scrollHeight = doc.scrollHeight;
  const viewport = window.innerHeight;
  const atEnd = (scrollY + viewport) >= (scrollHeight - 2);
  return { scroll_y: scrollY, scroll_height: scrollHeight, at_end: atEnd };
}
"""

_SCROLL_DIRECTIONS = frozenset({"down", "up", "to_end", "to_top"})
_EXTRACT_MODES = frozenset({"text", "links", "metadata", "structured", "all"})
_WAIT_FOR_VALUES = frozenset({"navigation", "text", "target", "load"})


class Worker:
    """One worker process: session registry, dispatch, idle reaper."""

    def __init__(self, args: argparse.Namespace) -> None:
        self._args = args
        self._data_dir = Path(args.data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._profile_dir = (
            Path(args.profile_dir) if args.profile_dir else self._data_dir / "profile"
        )
        self._default_channel: str | None = args.channel
        self._default_visible = bool(args.visible) and not args.headless
        self._idle_timeout_s = args.idle_timeout_s
        # Loopback/private destinations are refused unless the worker was started with
        # --allow-private-destinations (test fixture sites only; the companion never
        # passes it). Cloud Core applies the same policy before dispatching.
        self._allow_private_destinations = bool(
            getattr(args, "allow_private_destinations", False)
        )
        self._sessions: dict[str, SessionState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._shutting_down = False
        self._start_time = time.monotonic()
        self._browser_info: BrowserInfo | None = None

    # ------------------------------------------------------------------ #
    # top-level lifecycle
    # ------------------------------------------------------------------ #

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def _print_hello(self) -> None:
        self._browser_info = await detect_browser(self._default_channel)
        _write_line(
            {
                "type": "hello",
                "worker_version": WORKER_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "capabilities": list(policy.CAPABILITIES),
                "browser": self._browser_info.as_dict(),
            }
        )

    async def run(self) -> int:
        await self._print_hello()
        if self._args.self_check:
            if self._browser_info is not None and self._browser_info.available:
                return 0
            sys.stderr.write(
                f"browser channel {self._default_channel!r} not found or not runnable\n"
            )
            sys.stderr.flush()
            return 1

        reaper = asyncio.create_task(self._reap_idle_sessions())
        try:
            async for raw_line in _stdin_lines(asyncio.get_running_loop()):
                await self._handle_line(raw_line)
                if self._shutting_down:
                    break
        finally:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
            await self._close_all_sessions()
        return 0

    async def _reap_idle_sessions(self) -> None:
        while True:
            await asyncio.sleep(_REAP_INTERVAL_S)
            now = time.monotonic()
            for session_id in list(self._sessions):
                lock = self._lock_for(session_id)
                if lock.locked():
                    continue  # a command is in flight; try again next sweep
                state = self._sessions.get(session_id)
                if state is None or now - state.last_used < self._idle_timeout_s:
                    continue
                async with lock:
                    current = self._sessions.get(session_id)
                    if current is None or now - current.last_used < self._idle_timeout_s:
                        continue
                    del self._sessions[session_id]
                try:
                    await state.browser_session.close()
                except Exception as exc:  # cleanup must not crash the reaper
                    logger.warning(
                        "browser.idle_close_error", session_id=session_id, error=str(exc)[:200]
                    )
                else:
                    logger.info("browser.session_idle_closed", session_id=session_id)

    async def _close_all_sessions(self) -> None:
        for session_id in list(self._sessions):
            state = self._sessions.pop(session_id, None)
            if state is None:
                continue
            try:
                await state.browser_session.close()
            except Exception as exc:
                logger.warning(
                    "browser.shutdown_close_error", session_id=session_id, error=str(exc)[:200]
                )

    # ------------------------------------------------------------------ #
    # protocol loop
    # ------------------------------------------------------------------ #

    async def _handle_line(self, raw_line: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            logger.error("browser.bad_line", error=str(exc), line_preview=line[:200])
            return
        if not isinstance(envelope, dict):
            logger.error("browser.bad_line", error="line is not a JSON object")
            return

        msg_type = envelope.get("type")
        if msg_type == "exec":
            await self._handle_exec(envelope)
        elif msg_type == "cancel":
            self._handle_cancel(envelope)
        elif msg_type == "ping":
            _write_line({"type": "pong", "sessions": len(self._sessions)})
        elif msg_type == "shutdown":
            self._shutting_down = True
        else:
            request_id = envelope.get("request_id")
            if isinstance(request_id, str) and request_id:
                _write_line(
                    _err_result(
                        request_id, "validation_error", f"unknown message type {msg_type!r}", False
                    )
                )
            else:
                logger.error("browser.bad_line", error=f"unknown/missing type {msg_type!r}")

    async def _handle_exec(self, envelope: dict[str, Any]) -> None:
        request_id = envelope.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            logger.error("browser.bad_line", error="exec envelope missing request_id")
            return
        capability = envelope.get("capability")
        payload = envelope.get("payload")
        timeout_ms = envelope.get("timeout_ms", DEFAULT_TIMEOUT_MS)
        if not isinstance(capability, str):
            _write_line(
                _err_result(
                    request_id, "validation_error", "exec missing/invalid capability", False
                )
            )
            return
        if not isinstance(payload, dict):
            payload = {}
        if not isinstance(timeout_ms, int | float) or timeout_ms <= 0:
            timeout_ms = DEFAULT_TIMEOUT_MS

        task = asyncio.create_task(self._run_exec(request_id, capability, payload, timeout_ms))
        self._inflight[request_id] = task
        task.add_done_callback(lambda _t, rid=request_id: self._inflight.pop(rid, None))

    def _handle_cancel(self, envelope: dict[str, Any]) -> None:
        request_id = envelope.get("request_id")
        if not isinstance(request_id, str):
            return
        task = self._inflight.get(request_id)
        if task is not None and not task.done():
            task.cancel()

    async def _run_exec(
        self, request_id: str, capability: str, payload: dict[str, Any], timeout_ms: float
    ) -> None:
        try:
            result = await asyncio.wait_for(
                self._execute(capability, payload), timeout=timeout_ms / 1000
            )
        except TimeoutError:
            _write_line(
                _err_result(
                    request_id,
                    "timeout",
                    f"{capability}: operation exceeded timeout_ms={timeout_ms}",
                    True,
                )
            )
            return
        except asyncio.CancelledError:
            _write_line(
                _err_result(request_id, "cancelled", f"command {request_id} was cancelled", False)
            )
            return
        except BrowserError as err:
            _write_line(_err_result(request_id, str(err.error_class), err.message, err.retryable))
            return
        except Exception as exc:  # the worker must never crash on a bad op
            logger.error(
                "browser.internal_bug", request_id=request_id, capability=capability, error=str(exc)
            )
            _write_line(
                _err_result(
                    request_id,
                    "internal_bug",
                    f"{capability}: unexpected {type(exc).__name__}: {exc}",
                    False,
                )
            )
            return
        _write_line(_ok_result(request_id, result))

    # ------------------------------------------------------------------ #
    # dispatch
    # ------------------------------------------------------------------ #

    async def _execute(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(capability, str) or not _CAPABILITY_NAME_RE.match(capability):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"malformed capability name: {capability!r}",
                retryable=False,
            )
        if capability not in policy.CAPABILITIES:
            raise BrowserError(
                ErrorClass.CAPABILITY_MISSING,
                f"capability not implemented: {capability!r}",
                retryable=False,
            )

        if capability == "browser.worker_status":
            return await self._op_worker_status()

        if capability == "browser.session_open":
            session_id = _require_session_id(payload)
            async with self._lock_for(session_id):
                return await self._op_session_open(session_id, payload)

        if capability == "browser.session_close":
            session_id = _require_session_id(payload)
            async with self._lock_for(session_id):
                return await self._op_session_close(session_id)

        session_id = _require_session_id(payload)
        async with self._lock_for(session_id):
            state = self._sessions.get(session_id)
            if state is None:
                raise BrowserError(
                    ErrorClass.VALIDATION_ERROR, f"unknown session: {session_id!r}", retryable=False
                )
            state.last_used = time.monotonic()
            if capability not in _CUSTOM_ENFORCEMENT_CAPS:
                risk_class = policy.CAPABILITY_RISK_CLASS[capability]
                policy.enforce(state.policy_allowed, risk_class, capability=capability)
            handler = _HANDLERS[capability]
            return await handler(self, state, payload)

    # ------------------------------------------------------------------ #
    # session_open / session_close / worker_status
    # ------------------------------------------------------------------ #

    async def _op_session_open(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        profile = payload.get("profile", "research")
        if profile not in ("research", "isolated"):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "session_open: profile must be 'research' or 'isolated'",
                retryable=False,
            )
        channel = payload.get("channel", self._default_channel)
        if channel is not None and channel not in ("chrome", "chromium"):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "session_open: channel must be 'chrome' or 'chromium'",
                retryable=False,
            )
        policy_payload = payload.get("policy") or {}
        if not isinstance(policy_payload, dict):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "session_open: policy must be an object",
                retryable=False,
            )
        visible = bool(policy_payload.get("visible", self._default_visible))
        requested_classes: frozenset[policy.RiskClass] | None = None
        if "allowed_risk_classes" in policy_payload:
            requested_classes = policy.parse_risk_classes(policy_payload["allowed_risk_classes"])

        existing = self._sessions.get(session_id)
        if existing is not None and not await existing.backend.is_alive():
            # The browser process behind this session died (crash/killed).
            # Recovery matrix (M13_RESEARCH_SPEC §5): "browser process killed
            # -> dependency_unavailable [on the op that discovered it] -> the
            # next session_open recreates the session" — a stale entry must
            # never be silently reused as if it were still live, so this
            # falls through to the normal creation path below instead of the
            # narrow-reopen path.
            del self._sessions[session_id]
            with suppress(Exception):
                await existing.browser_session.close()
            logger.info("browser.session_recreated_after_crash", session_id=session_id)
            existing = None
        if existing is not None:
            new_allowed = policy.narrow_reopen(existing.policy_allowed, requested_classes)
            existing.policy_allowed = new_allowed
            existing.last_used = time.monotonic()
            return {
                "session_id": session_id,
                "created": False,
                "channel": existing.channel,
                "browser_version": existing.browser_version,
                "idle_timeout_s": self._idle_timeout_s,
                "policy": {
                    "allowed_risk_classes": sorted(c.value for c in new_allowed),
                    "visible": existing.visible,
                },
            }

        allowed = (
            requested_classes if requested_classes is not None else policy.RESEARCH_SESSION_CLASSES
        )
        profile_dir = self._profile_dir if profile == "research" else None
        backend = ManagedBackend(headless=not visible, profile_dir=profile_dir, channel=channel)
        await backend.connect()
        browser_session = BrowserSession(backend, file_io_root=self._data_dir)
        browser_version = (
            backend.native_browser.version
            if backend.native_browser is not None
            else (self._browser_info.version if self._browser_info else None)
        )
        state = SessionState(
            session_id=session_id,
            browser_session=browser_session,
            backend=backend,
            policy_allowed=allowed,
            visible=visible,
            channel=channel or "chromium",
            browser_version=browser_version,
            profile=profile,
            last_used=time.monotonic(),
        )
        self._sessions[session_id] = state
        logger.info(
            "browser.session_opened",
            session_id=session_id,
            profile=profile,
            channel=state.channel,
            visible=visible,
        )
        return {
            "session_id": session_id,
            "created": True,
            "channel": state.channel,
            "browser_version": browser_version,
            "idle_timeout_s": self._idle_timeout_s,
            "policy": {
                "allowed_risk_classes": sorted(c.value for c in allowed),
                "visible": visible,
            },
        }

    async def _op_session_close(self, session_id: str) -> dict[str, Any]:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return {"closed": True, "session_id": session_id}
        try:
            await state.browser_session.close()
        except Exception as exc:
            logger.warning(
                "browser.session_close_error", session_id=session_id, error=str(exc)[:200]
            )
        return {"closed": True, "session_id": session_id}

    async def _op_worker_status(self) -> dict[str, Any]:
        now = time.monotonic()
        sessions_info = []
        for sid, state in self._sessions.items():
            try:
                tabs = await state.browser_session.list_tabs()
                tab_count = len(tabs)
            except Exception:
                tab_count = 0
            sessions_info.append(
                {"session_id": sid, "tabs": tab_count, "idle_s": round(now - state.last_used, 1)}
            )
        browser_dict: dict[str, Any] = (
            self._browser_info.as_dict()
            if self._browser_info
            else {"channel": self._default_channel, "available": False, "version": None}
        )
        browser_dict = {**browser_dict, "alive": bool(browser_dict.get("available"))}
        return {
            "worker_version": WORKER_VERSION,
            "browser": browser_dict,
            "sessions": sessions_info,
            "uptime_s": round(now - self._start_time, 1),
        }

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #

    async def _navigation_result(
        self, browser_session: BrowserSession, response: Any, *, elapsed_ms: float
    ) -> dict[str, Any]:
        page = browser_session.backend.current_page
        http_status = response.status if response is not None else None
        raw = await read_raw_page_data(page)
        body_text = await browser_session.page_text()
        kind_result = classify_page(
            title=raw.title,
            heading_text=raw.heading_text,
            body_text=body_text,
            has_password_field=raw.has_password_field,
            http_status=http_status,
        )
        return {
            "url": page.url,
            "title": raw.title,
            "http_status": http_status,
            "page_kind": kind_result.page_kind,
            "site_error": kind_result.site_error.as_dict() if kind_result.site_error else None,
            "elapsed_ms": elapsed_ms,
        }

    def _check_destination(self, url: str, *, op: str) -> None:
        if self._allow_private_destinations:
            return
        require_public_destination(url, op=op)

    async def _op_navigate(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR, "navigate: 'url' is required", retryable=False
            )
        self._check_destination(url, op="navigate")
        timeout_ms = payload.get("timeout_ms", DEFAULT_NAV_TIMEOUT_MS)
        start = time.perf_counter()
        response = await state.browser_session.navigate(url, timeout_ms=timeout_ms)
        return await self._navigation_result(
            state.browser_session, response, elapsed_ms=_elapsed_ms(start)
        )

    async def _op_back(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        timeout_ms = payload.get("timeout_ms", DEFAULT_NAV_TIMEOUT_MS)
        start = time.perf_counter()
        await state.browser_session.back(timeout_ms=timeout_ms)
        return await self._navigation_result(
            state.browser_session, None, elapsed_ms=_elapsed_ms(start)
        )

    async def _op_forward(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        timeout_ms = payload.get("timeout_ms", DEFAULT_NAV_TIMEOUT_MS)
        start = time.perf_counter()
        await state.browser_session.forward(timeout_ms=timeout_ms)
        return await self._navigation_result(
            state.browser_session, None, elapsed_ms=_elapsed_ms(start)
        )

    # ------------------------------------------------------------------ #
    # tabs
    # ------------------------------------------------------------------ #

    async def _op_tab_list(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        tabs = await state.browser_session.list_tabs()
        return {
            "tabs": [
                {"index": t.index, "url": t.url, "title": t.title, "active": t.is_current}
                for t in tabs
            ]
        }

    async def _op_tab_new(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        url = payload.get("url")
        if url is not None and not isinstance(url, str):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "tab_new: 'url' must be a string or null",
                retryable=False,
            )
        if url:
            self._check_destination(url, op="tab_new")
        index = await state.browser_session.new_tab(url)
        return {"index": index}

    async def _op_tab_close(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        index = payload.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "tab_close: 'index' must be an integer",
                retryable=False,
            )
        await state.browser_session.close_tab(index)
        return {"closed": True}

    async def _op_tab_select(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        index = payload.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "tab_select: 'index' must be an integer",
                retryable=False,
            )
        start = time.perf_counter()
        await state.browser_session.select_tab(index)
        return await self._navigation_result(
            state.browser_session, None, elapsed_ms=_elapsed_ms(start)
        )

    # ------------------------------------------------------------------ #
    # inspect / find / click / fill / select_option / set_checked / scroll / wait
    # ------------------------------------------------------------------ #

    async def _op_inspect(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        browser_session = state.browser_session
        page = browser_session.backend.current_page
        tabs = await browser_session.list_tabs()
        tab_index = next((t.index for t in tabs if t.is_current), 0)
        raw = await read_raw_page_data(page)
        body_text = await browser_session.page_text()
        kind_result = classify_page(
            title=raw.title,
            heading_text=raw.heading_text,
            body_text=body_text,
            has_password_field=raw.has_password_field,
            http_status=None,
        )
        return {
            "url": page.url,
            "title": raw.title,
            "tab_index": tab_index,
            "tab_count": len(tabs),
            "page_kind": kind_result.page_kind,
        }

    async def _op_find(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        target = payload.get("target")
        if not target:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR, "find: 'target' is required", retryable=False
            )
        spec = coerce_target(target)
        page = state.browser_session.backend.current_page
        root = _frame_root(page, payload.get("frame"))
        locator = spec.to_locator(root)
        try:
            count = await locator.count()
        except Exception as exc:
            raise map_playwright_error(exc, phase=Phase.OTHER, op="find") from exc
        elements: list[dict[str, Any]] = []
        for i in range(min(count, 20)):
            nth = locator.nth(i)
            try:
                described = await nth.evaluate(_DESCRIBE_ELEMENT_JS)
                visible = await nth.is_visible()
                text = await nth.inner_text()
            except Exception:  # a matched-but-detached element must not abort find
                continue
            elements.append(
                {
                    "tag": described.get("tag", ""),
                    "role": described.get("role"),
                    "name": described.get("name") or "",
                    "text": text.strip() if text else "",
                    "visible": visible,
                }
            )
        return {"match_count": count, "elements": elements}

    async def _op_click(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        target = payload.get("target")
        if not target:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR, "click: 'target' is required", retryable=False
            )
        spec = coerce_target(target)
        frame = payload.get("frame")
        timeout_ms = payload.get("timeout_ms", 5_000)
        page = state.browser_session.backend.current_page
        root = _frame_root(page, frame)
        locator = spec.to_locator(root).first
        try:
            await locator.wait_for(state="attached", timeout=timeout_ms)
        except Exception as exc:
            raise map_playwright_error(
                exc,
                phase=Phase.RESOLVE,
                op="click",
                evidence={"target": spec.as_dict(), "url": redact_url(page.url)},
            ) from exc
        try:
            described = await locator.evaluate(_DESCRIBE_ELEMENT_JS)
        except Exception as exc:
            raise map_playwright_error(
                exc, phase=Phase.ACT, op="click", evidence={"target": spec.as_dict()}
            ) from exc
        resolved = policy.ResolvedElement(
            tag=described.get("tag", ""),
            role=described.get("role"),
            name=described.get("name") or "",
            has_href=bool(described.get("has_href")),
            is_submit=bool(described.get("is_submit")),
            has_onclick=bool(described.get("has_onclick")),
        )
        risk_class = policy.classify_click(resolved)
        policy.enforce(state.policy_allowed, risk_class, capability="browser.click")

        url_before = page.url
        try:
            await locator.click(timeout=timeout_ms)
        except Exception as exc:
            raise map_playwright_error(
                exc,
                phase=Phase.ACT,
                op="click",
                evidence={"target": spec.as_dict(), "url": redact_url(page.url)},
            ) from exc
        return {
            "clicked": True,
            "navigated": page.url != url_before,
            "url": page.url,
            "resolved": {"tag": resolved.tag, "role": resolved.role, "name": resolved.name},
        }

    async def _op_fill(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        value = payload.get("value")
        if not isinstance(value, str):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR, "fill: 'value' must be a string", retryable=False
            )
        await state.browser_session.fill(
            coerce_target(payload.get("target")), value, frame=payload.get("frame")
        )
        return {"ok": True}

    async def _op_select_option(
        self, state: SessionState, payload: dict[str, Any]
    ) -> dict[str, Any]:
        value = payload.get("value")
        if not isinstance(value, str):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "select_option: 'value' must be a string",
                retryable=False,
            )
        await state.browser_session.select_option(
            coerce_target(payload.get("target")), value=value, frame=payload.get("frame")
        )
        return {"ok": True}

    async def _op_set_checked(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        checked = payload.get("checked")
        if not isinstance(checked, bool):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "set_checked: 'checked' must be a boolean",
                retryable=False,
            )
        await state.browser_session.set_checked(
            coerce_target(payload.get("target")), checked, frame=payload.get("frame")
        )
        return {"ok": True}

    async def _op_scroll(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        direction = payload.get("direction", "down")
        if direction not in _SCROLL_DIRECTIONS:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"scroll: direction must be one of {sorted(_SCROLL_DIRECTIONS)}",
                retryable=False,
            )
        amount_px = payload.get("amount_px", 800)
        if not isinstance(amount_px, int | float) or isinstance(amount_px, bool) or amount_px < 0:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "scroll: amount_px must be a non-negative number",
                retryable=False,
            )
        page = state.browser_session.backend.current_page
        try:
            result = await page.evaluate(_SCROLL_JS, {"direction": direction, "amount": amount_px})
        except Exception as exc:
            raise map_playwright_error(exc, phase=Phase.ACT, op="scroll") from exc
        return result

    async def _op_wait(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        wait_for = payload.get("for")
        if wait_for not in _WAIT_FOR_VALUES:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"wait: 'for' must be one of {sorted(_WAIT_FOR_VALUES)}",
                retryable=False,
            )
        timeout_ms = payload.get("timeout_ms", 10_000)
        page = state.browser_session.backend.current_page
        start = time.perf_counter()
        try:
            if wait_for in ("navigation", "load"):
                await page.wait_for_load_state("load", timeout=timeout_ms)
            elif wait_for == "text":
                text = payload.get("text")
                if not isinstance(text, str) or not text:
                    raise BrowserError(
                        ErrorClass.VALIDATION_ERROR,
                        "wait: 'text' is required for for='text'",
                        retryable=False,
                    )
                await page.get_by_text(text).first.wait_for(state="visible", timeout=timeout_ms)
            else:  # target
                target = payload.get("target")
                if not target:
                    raise BrowserError(
                        ErrorClass.VALIDATION_ERROR,
                        "wait: 'target' is required for for='target'",
                        retryable=False,
                    )
                spec = coerce_target(target)
                root = _frame_root(page, payload.get("frame"))
                await spec.to_locator(root).first.wait_for(state="attached", timeout=timeout_ms)
        except BrowserError:
            raise
        except PlaywrightTimeoutError:
            return {"satisfied": False, "elapsed_ms": _elapsed_ms(start)}
        except Exception as exc:
            raise map_playwright_error(exc, phase=Phase.ACT, op="wait") from exc
        return {"satisfied": True, "elapsed_ms": _elapsed_ms(start)}

    # ------------------------------------------------------------------ #
    # extract / snapshot / screenshot / download
    # ------------------------------------------------------------------ #

    async def _op_extract(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        mode = payload.get("mode", "all")
        if mode not in _EXTRACT_MODES:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"extract: mode must be one of {sorted(_EXTRACT_MODES)}",
                retryable=False,
            )
        max_chars = payload.get("max_chars", 24_000)
        frame_name = payload.get("frame")
        page = state.browser_session.backend.current_page
        target: Page | Frame = page
        if frame_name:
            resolved_frame = page.frame(name=frame_name)
            if resolved_frame is None:
                raise BrowserError(
                    ErrorClass.VALIDATION_ERROR,
                    f"extract: unknown frame {frame_name!r}",
                    retryable=False,
                )
            target = resolved_frame

        raw = await read_raw_page_data(target)
        try:
            body_text = await target.locator("body").inner_text()
        except Exception:
            body_text = ""
        kind_result = classify_page(
            title=raw.title,
            heading_text=raw.heading_text,
            body_text=body_text,
            has_password_field=raw.has_password_field,
            http_status=None,
        )
        result: dict[str, Any] = {
            "url": target.url,
            "title": raw.title,
            "page_kind": kind_result.page_kind,
        }
        if mode in ("text", "all"):
            primary = await read_primary_text(target)
            text, truncated = truncate_text(primary, max_chars=max_chars)
            result["text"] = text
            result["truncated"] = truncated
        if mode in ("metadata", "all"):
            result["metadata"] = build_metadata(raw).as_dict()
        if mode in ("links", "all"):
            result["links"] = build_links(raw.links, base_url=target.url)
        if mode in ("structured", "all"):
            result["structured"] = {"json_ld": parse_json_ld(raw.json_ld_raw)}
        return result

    async def _op_snapshot(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        max_chars = payload.get("max_chars", 24_000)
        snapshot = await state.browser_session.accessibility_snapshot()
        text, truncated = truncate_text(snapshot, max_chars=max_chars)
        return {"aria_snapshot": text, "truncated": truncated}

    async def _op_screenshot(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        page = state.browser_session.backend.current_page
        data = b""
        for quality in _SCREENSHOT_QUALITIES:
            try:
                data = await page.screenshot(type="jpeg", quality=quality, full_page=False)
            except Exception as exc:
                raise map_playwright_error(exc, phase=Phase.OTHER, op="screenshot") from exc
            if len(data) <= _SCREENSHOT_MAX_BYTES:
                break
        return {
            "format": "jpeg",
            "base64": base64.b64encode(data).decode("ascii"),
            "bytes": len(data),
        }

    async def _op_download(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        target = payload.get("target")
        authorization_ref = payload.get("authorization_ref")
        if policy.RiskClass.HIGH_IMPACT not in state.policy_allowed or not authorization_ref:
            raise BrowserError(
                ErrorClass.SECURITY_SCOPE_ERROR,
                "browser.download: requires HIGH_IMPACT in the session policy and a non-empty "
                "authorization_ref",
                retryable=False,
                evidence={
                    "authorization_ref_present": bool(authorization_ref),
                    "high_impact_allowed": policy.RiskClass.HIGH_IMPACT in state.policy_allowed,
                },
            )
        if not target:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR, "download: 'target' is required", retryable=False
            )
        downloads_dir = self._data_dir / "downloads"
        result = await state.browser_session.download(coerce_target(target), save_dir=downloads_dir)
        return {
            "path": str(result.path),
            "bytes": result.path.stat().st_size,
            "sha256": result.sha256,
        }

    # ------------------------------------------------------------------ #
    # search / fetch_evidence
    # ------------------------------------------------------------------ #

    async def _op_search(self, state: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR, "search: 'query' is required", retryable=False
            )
        engine = payload.get("engine", "auto")
        if engine != "auto" and engine not in search_engines.ENGINES:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"search: engine must be one of auto, {', '.join(search_engines.ENGINES)}",
                retryable=False,
            )
        max_results = payload.get("max_results", 10)
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "search: max_results must be an integer",
                retryable=False,
            )
        recency_days = payload.get("recency_days")
        if recency_days is not None and (
            not isinstance(recency_days, int) or isinstance(recency_days, bool)
        ):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "search: recency_days must be an integer or null",
                retryable=False,
            )

        browser_session = state.browser_session

        async def fetch(_engine: str, url: str) -> tuple[str, str, int | None]:
            response = await browser_session.navigate(url, timeout_ms=15_000)
            page = browser_session.backend.current_page
            http_status = response.status if response is not None else None
            raw = await read_raw_page_data(page)
            body_text = await browser_session.page_text()
            kind_result = classify_page(
                title=raw.title,
                heading_text=raw.heading_text,
                body_text=body_text,
                has_password_field=raw.has_password_field,
                http_status=http_status,
            )
            html = await page.content()
            return html, kind_result.page_kind, http_status

        outcome = await search_engines.run_search(
            query, engine, fetch=fetch, max_results=max_results, recency_days=recency_days
        )
        return outcome.as_dict()

    async def _op_fetch_evidence(
        self, state: SessionState, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR, "fetch_evidence: 'url' is required", retryable=False
            )
        self._check_destination(url, op="fetch_evidence")
        query = payload.get("query") or ""
        source_class = payload.get("source_class") or "unknown"
        excerpt_chars = payload.get("excerpt_chars", 1_200)
        timeout_ms = payload.get("timeout_ms", 30_000)

        browser_session = state.browser_session
        page = browser_session.backend.current_page
        response = await browser_session.navigate(url, timeout_ms=timeout_ms)
        await asyncio.sleep(
            _FETCH_EVIDENCE_SETTLE_S
        )  # domcontentloaded/load already awaited; short settle

        raw = await read_raw_page_data(page)
        body_text = await browser_session.page_text()
        primary_text = await read_primary_text(page)
        http_status = response.status if response is not None else None
        kind_result = classify_page(
            title=raw.title,
            heading_text=raw.heading_text,
            body_text=body_text,
            has_password_field=raw.has_password_field,
            http_status=http_status,
        )
        excerpt, _truncated = truncate_text(primary_text.strip(), max_chars=excerpt_chars)
        links = build_links(raw.links, base_url=page.url)

        return {
            "url": url,
            "final_url": page.url,
            "title": raw.title,
            "excerpt": excerpt,
            "text_chars": len(body_text),
            "fetched_at": _utc_now_iso(),
            "extraction_method": "dom_text",
            "page_kind": kind_result.page_kind,
            "http_status": http_status,
            "metadata": build_metadata(raw).as_dict(),
            "source_class": source_class,
            "query": query,
            "injection_markers": count_injection_markers(body_text),
            "links_count": len(links),
        }


# ----------------------------------------------------------------------- #
# capability -> handler table (built after every handler is defined above)
# ----------------------------------------------------------------------- #

_HANDLERS: dict[str, Callable[[Worker, SessionState, dict[str, Any]], Any]] = {
    "browser.navigate": Worker._op_navigate,
    "browser.back": Worker._op_back,
    "browser.forward": Worker._op_forward,
    "browser.tab_list": Worker._op_tab_list,
    "browser.tab_new": Worker._op_tab_new,
    "browser.tab_close": Worker._op_tab_close,
    "browser.tab_select": Worker._op_tab_select,
    "browser.inspect": Worker._op_inspect,
    "browser.find": Worker._op_find,
    "browser.click": Worker._op_click,
    "browser.fill": Worker._op_fill,
    "browser.select_option": Worker._op_select_option,
    "browser.set_checked": Worker._op_set_checked,
    "browser.scroll": Worker._op_scroll,
    "browser.wait": Worker._op_wait,
    "browser.extract": Worker._op_extract,
    "browser.snapshot": Worker._op_snapshot,
    "browser.screenshot": Worker._op_screenshot,
    "browser.download": Worker._op_download,
    "browser.search": Worker._op_search,
    "browser.fetch_evidence": Worker._op_fetch_evidence,
}


# ----------------------------------------------------------------------- #
# CLI
# ----------------------------------------------------------------------- #


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m browser_agent.worker",
        description="Personal Agent OS Browser Worker (M13) — stdio protocol, see "
        "packages/protocol/BROWSER_CAPABILITIES.md §7.",
    )
    parser.add_argument("--data-dir", required=True, help="Directory for profile/downloads state")
    parser.add_argument(
        "--profile-dir",
        default=None,
        help="Persistent 'research' profile directory (default: <data-dir>/profile)",
    )
    parser.add_argument(
        "--channel",
        choices=["chrome", "chromium"],
        default="chrome",
        help="Default browser channel",
    )
    visibility = parser.add_mutually_exclusive_group()
    visibility.add_argument(
        "--visible", action="store_true", help="Default session_open to headful (owner sees Chrome)"
    )
    visibility.add_argument(
        "--headless", action="store_true", help="Default session_open to headless (default)"
    )
    parser.add_argument("--idle-timeout-s", type=int, default=DEFAULT_IDLE_TIMEOUT_S)
    parser.add_argument(
        "--allow-private-destinations",
        action="store_true",
        help="Permit loopback/private/tailnet destinations (fixture tests only; never in production)",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="Print hello, then exit 0 if the browser is available, non-zero otherwise",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging(stream=sys.stderr)
    args = build_arg_parser().parse_args(argv)
    worker = Worker(args)
    return asyncio.run(worker.run())


if __name__ == "__main__":
    raise SystemExit(main())
