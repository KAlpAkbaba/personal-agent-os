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
import atexit
import base64
import json
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from playwright.async_api import Frame, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from . import launch_guard, lifecycle, policy, release, search_engines
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
from .search_engines import detect_google_interstitial
from .session import BrowserSession
from .targets import TargetSpec, coerce_target

logger = get_logger(__name__)

WORKER_VERSION = "0.3.0"
# Per-capability response schema versions (BROWSER_CAPABILITIES.md §3). A consumer that
# needs the search-provider evidence checks `contracts["browser.search"] >= 2` on the hello
# or worker_status BEFORE searching, so an old installed worker yields a clear contract/
# version mismatch instead of a missing-property error (owner run, 2026-09-03).
CONTRACTS: dict[str, int] = {"browser.search": 2}
PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_NAV_TIMEOUT_MS = 15_000
DEFAULT_IDLE_TIMEOUT_S = 600
MAX_RESULT_BYTES = 48 * 1024
_REAP_INTERVAL_S = 15.0
# M13 lifecycle guard (owner-machine incident, 2026-09-03): "deliberate and
# bounded" tabs. DEFAULT_MAX_TABS is the per-session ceiling unless a
# session_open payload raises it (never above MAX_TABS_CEILING) for a plan
# that genuinely needs more open tabs.
DEFAULT_MAX_TABS = 6
MAX_TABS_CEILING = 12
BROWSER_PID_EXIT_TIMEOUT_S = 10.0
# fetch_evidence's post-load settle (contract §3a): a bounded, DOM-driven
# "networkidle" wait (exceptions swallowed) replaces the old fixed sleep.
_FETCH_EVIDENCE_NETWORKIDLE_TIMEOUT_MS = 3_000
_SCREENSHOT_MAX_BYTES = 300 * 1024
_SCREENSHOT_QUALITIES: tuple[int, ...] = (80, 60, 40, 20)
# Google-through-the-UI (contract §3a): readiness/verification polling never
# uses a single fixed sleep — both loops poll a real DOM/navigation condition
# on a short interval, bounded by an overall timeout.
_GOOGLE_READY_TIMEOUT_MS = 10_000
_GOOGLE_READY_POLL_S = 0.25
_VERIFICATION_CLEARED_POLL_S = 0.5

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


def _err_result(
    request_id: str,
    error_class: str,
    message: str,
    retryable: bool,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {
        "class": error_class,
        "message": message[:2000],
        "retryable": retryable,
    }
    if evidence:
        # Structured, bounded context (lifecycle guard name, pids, profile
        # path, fault file). Rendered through the same JSON-safe path as
        # results; never page content.
        serialized = json.dumps(evidence, default=str)
        error["evidence"] = (
            json.loads(serialized) if len(serialized) <= 8000 else {"truncated": True}
        )
    return {
        "type": "result",
        "request_id": request_id,
        "ok": False,
        "error": error,
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
    # M13 lifecycle (owner-machine incident, 2026-09-03): identity/proof
    # fields carried on every session-scoped result (see
    # Worker._lifecycle_info). ``session_uid`` is a fresh uuid4 per actual
    # browser launch — unchanged across a mere reopen (narrow_reopen) of the
    # SAME live browser, regenerated only when the browser itself is
    # recreated (fresh session_open, or after a dead-browser discard).
    session_uid: str = ""
    profile_dir: Path | None = None
    max_tabs: int = DEFAULT_MAX_TABS
    popups_closed: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Set when a browser.search (interstitial="handoff") returns
    # state=waiting_for_owner_verification, holding the query that was
    # pending; consumed (and cleared) by the next browser.search for this
    # session (contract §3a "resume after clearance").
    awaiting_verification_query: str | None = None
    # Owner handoff bookkeeping (contract §3a, "retry once, never loop"): how many
    # interstitials this session has already handed to the owner and whether one
    # clearance has already been consumed. After one cleared verification a
    # further interstitial is NOT handed off again; it is recorded and the
    # provider fallback applies.
    verification_handoffs: int = 0
    verification_cleared_once: bool = False


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
_WAIT_FOR_VALUES = frozenset({"navigation", "text", "target", "load", "verification_cleared"})
_FETCH_EVIDENCE_TAB_VALUES = frozenset({"same", "new"})
_INTERSTITIAL_MODES = frozenset({"fallback", "handoff"})
_SEARCH_MODES = frozenset({"interactive", "unattended"})
#: Semantic target for Google's real search box (contract §3a): a single
#: role=combobox on the page, resolved with ``.first`` like every other
#: locator in this module — never CSS/XPath, never coordinates.
_GOOGLE_SEARCH_BOX_TARGET = TargetSpec(role="combobox")


def detect_user_locale() -> str | None:
    """The machine's user locale as BCP-47 (``tr-TR``) without assuming one: Windows'
    GetUserDefaultLocaleName first, then the CRT locale, else ``None``."""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):  # type: ignore[attr-defined]
            value = buf.value.strip()
            if value:
                return value
    except Exception:  # noqa: BLE001 - not Windows, or no kernel32
        pass
    try:
        import locale as _locale

        name = _locale.getlocale()[0]
        if name and "_" in name:
            return name.replace("_", "-")
    except Exception:  # noqa: BLE001
        pass
    return None


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
        self._locale = getattr(args, "locale", None) or detect_user_locale()
        # Loopback/private destinations are refused unless the worker was started with
        # --allow-private-destinations (test fixture sites only; the companion never
        # passes it). Cloud Core applies the same policy before dispatching.
        self._allow_private_destinations = bool(getattr(args, "allow_private_destinations", False))
        # Base URL for Google's home page (contract §3a). Defaults to the real
        # Google; the browser e2e suite points this at the fixture site so
        # the Google-through-the-UI flow is deterministic and offline.
        self._google_base_url: str = (
            getattr(args, "google_base_url", None) or "https://www.google.com"
        )
        self._sessions: dict[str, SessionState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._shutting_down = False
        self._start_time = time.monotonic()
        self._browser_info: BrowserInfo | None = None
        # M13 lifecycle (owner-machine incident 2026-09-03): the single
        # session_id currently allowed to hold the persistent "research"
        # profile/browser. A second session_open for a *different*
        # session_id with profile="research" while this is set (and that
        # session is still tracked) is refused with
        # browser_lifecycle_violation instead of ever attempting a second
        # launch on the same profile.
        self._research_owner_session_id: str | None = None
        # Cross-process guards (launch_guard): durable launch-rate breaker and
        # the ownership record, both under the worker data directory so they
        # survive this process.
        self._breaker = launch_guard.LaunchBreaker(self._data_dir)
        self._ownership = launch_guard.OwnershipRecord(self._data_dir)

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
                "contracts": dict(CONTRACTS),
                "capabilities": list(policy.CAPABILITIES),
                "browser": self._browser_info.as_dict(),
                "lifecycle_fault": self._breaker.current_fault(),
                # Which copy of this package is executing (release.py): the
                # verifier compares it with the release it staged.
                "module": release.module_info(),
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

    def _atexit_cleanup(self) -> None:
        """Last-resort synchronous safety net registered by :func:`main` on
        the real CLI process (never on an in-process ``Worker`` used by
        tests): the worker must never exit leaving a Chrome it launched. This
        runs after the asyncio loop is gone — e.g. an unhandled exception
        escaped ``run()``'s own ``finally`` above — so it is a synchronous
        OS-level reap of ``--profile-dir``, not a graceful
        ``BrowserSession.close()``. Best effort: swallows every error, since
        this is the process's last chance to clean up, not a place to raise."""
        try:
            pids = lifecycle.find_profile_chrome_pids(self._profile_dir)
        except Exception:
            return
        if not pids:
            return
        logger.warning("browser.atexit_orphan_reap", profile_dir=str(self._profile_dir), pids=pids)
        for pid in pids:
            with suppress(Exception):
                lifecycle.terminate_pid(pid)
        with suppress(Exception):
            lifecycle.wait_for_pids_exit(pids, timeout_s=BROWSER_PID_EXIT_TIMEOUT_S)

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
                exited = await self._close_session_state(session_id, state, op="idle_close")
                logger.info(
                    "browser.session_idle_closed", session_id=session_id, browser_pid_exited=exited
                )

    async def _close_all_sessions(self) -> None:
        # M13 lifecycle (owner-machine incident 2026-09-03): shutdown closes
        # every session the SAME way session_close/idle-reap do — a
        # BrowserSession.close() plus a bounded wait for the OS process to be
        # gone — never just "close and hope" (the worker must never exit
        # leaving a Chrome it launched).
        for session_id in list(self._sessions):
            state = self._sessions.pop(session_id, None)
            if state is None:
                continue
            await self._close_session_state(session_id, state, op="shutdown_close")

    async def _close_session_state(self, session_id: str, state: SessionState, *, op: str) -> bool:
        """Close one session's browser and wait (bounded) for its OS
        process(es) to actually exit. Shared by session_close, the idle
        reaper and worker shutdown so all three answer ``browser_pid_exited``
        the same honest way. Never raises — cleanup must not crash the
        caller."""
        if self._research_owner_session_id == session_id:
            self._research_owner_session_id = None
        try:
            await state.browser_session.close()
        except Exception as exc:
            logger.warning(f"browser.{op}_error", session_id=session_id, error=str(exc)[:200])
        exited = await self._await_browser_pid_exit(state)
        if state.profile == "research":
            with suppress(Exception):
                self._ownership.update(
                    closed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    closed_by=op,
                    browser_pid_exited=exited,
                )
        return exited

    async def _await_browser_pid_exit(self, state: SessionState) -> bool:
        """Bounded (10s) wait for the browser process(es) this session
        launched to actually be gone from the OS process list — answers
        ``browser_pid_exited`` truthfully rather than assuming a
        Playwright-level ``close()`` call tore down the OS process."""
        if state.profile_dir is not None:
            return await asyncio.to_thread(
                lifecycle.wait_for_profile_clear,
                state.profile_dir,
                timeout_s=BROWSER_PID_EXIT_TIMEOUT_S,
            )
        pid = state.backend.main_pid
        if pid is None:
            return True
        still_alive = await asyncio.to_thread(
            lifecycle.wait_for_pids_exit, [pid], timeout_s=BROWSER_PID_EXIT_TIMEOUT_S
        )
        return not still_alive

    async def _current_tab_count(self, state: SessionState) -> int:
        try:
            return len(await state.browser_session.list_tabs())
        except Exception:
            return 0

    async def _close_popup(self, session_id: str, page: Page) -> None:
        """``ManagedBackend.on_popup`` callback (M13 lifecycle, owner-machine
        incident 2026-09-03): a page the loaded page opened on its own is
        closed immediately and counted, never left as a silent extra tab.
        Fire-and-forget (scheduled via ``asyncio.ensure_future``, never
        blocks the event that triggered it); best effort — never raises."""
        state = self._sessions.get(session_id)
        if state is not None:
            state.popups_closed += 1
        logger.warning(
            "browser.popup_opened",
            session_id=session_id,
            url=redact_url(page.url) if page.url else None,
        )
        with suppress(Exception):
            if not page.is_closed():
                await page.close()
        logger.info(
            "browser.popup_closed",
            session_id=session_id,
            popups_closed=state.popups_closed if state is not None else None,
        )

    def _lifecycle_info(
        self, state: SessionState, *, tab_count: int, reused: bool
    ) -> dict[str, Any]:
        """The ``lifecycle`` block carried on every session-scoped result
        (M13 lifecycle contract, owner-machine incident 2026-09-03) — lets a
        consumer PROVE it is talking to the same one owned browser across a
        sequence of commands rather than a silently-relaunched second one."""
        return {
            "session_uid": state.session_uid,
            "browser_pid": state.backend.main_pid,
            "worker_pid": os.getpid(),
            "profile_dir": str(state.profile_dir) if state.profile_dir is not None else None,
            "tab_count": tab_count,
            "max_tabs": state.max_tabs,
            "max_windows": 1,
            "reused": reused,
            "launch_kind": state.backend.last_launch_kind,
            "launch_lock": state.backend.launch_lock_name,
            "job_object_assigned": state.backend.job_object_assigned,
        }

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
            _write_line(
                _err_result(
                    request_id, str(err.error_class), err.message, err.retryable, err.evidence
                )
            )
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
            result = await handler(self, state, payload)
            # M13 lifecycle backstop (owner-machine incident 2026-09-03):
            # tab_new/fetch_evidence already refuse BEFORE opening a tab that
            # would cross max_tabs; this is defense in depth for anything
            # that slipped past that pre-check (e.g. a popup racing its
            # auto-close) — discovered here, on ANY op, as a violation.
            tab_count = await self._current_tab_count(state)
            lifecycle.check_tab_count_within_budget(tab_count, state.max_tabs, op=capability)
            result["lifecycle"] = self._lifecycle_info(state, tab_count=tab_count, reused=True)
            if state.profile == "research" and capability in (
                "browser.tab_new",
                "browser.tab_close",
            ):
                with suppress(Exception):
                    self._ownership.update(tab_ids=list(range(tab_count)))
            return result

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
        max_tabs = payload.get("max_tabs", DEFAULT_MAX_TABS)
        if (
            not isinstance(max_tabs, int)
            or isinstance(max_tabs, bool)
            or not (1 <= max_tabs <= MAX_TABS_CEILING)
        ):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"session_open: max_tabs must be an integer in 1..{MAX_TABS_CEILING}",
                retryable=False,
            )

        existing = self._sessions.get(session_id)
        if existing is not None and not await existing.backend.is_alive():
            # The browser process behind this session died (crash/killed).
            # Recovery matrix (M13_RESEARCH_SPEC §5): "browser process killed
            # -> dependency_unavailable [on the op that discovered it] -> the
            # next session_open recreates the session" — a stale entry must
            # never be silently reused as if it were still live, so this
            # falls through to the normal creation path below instead of the
            # narrow-reopen path. This is also the ONE place a
            # dependency_unavailable browser is allowed to be relaunched —
            # exactly once, by the normal creation path's single _launch()
            # attempt below, never a retry loop.
            del self._sessions[session_id]
            if self._research_owner_session_id == session_id:
                self._research_owner_session_id = None
            with suppress(Exception):
                await existing.browser_session.close()
            logger.info("browser.session_recreated_after_crash", session_id=session_id)
            existing = None
        if existing is not None:
            new_allowed = policy.narrow_reopen(existing.policy_allowed, requested_classes)
            existing.policy_allowed = new_allowed
            existing.max_tabs = max_tabs
            existing.last_used = time.monotonic()
            tab_count = await self._current_tab_count(existing)
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
                "lifecycle": self._lifecycle_info(existing, tab_count=tab_count, reused=True),
            }

        # M13 lifecycle guard (owner-machine incident 2026-09-03): one owned
        # research browser. The persistent "research" profile is a single
        # shared directory (self._profile_dir) across every session_id this
        # worker tracks — only ONE session_id may hold it at a time. A
        # DIFFERENT session_id requesting it while the current owner is still
        # tracked is refused outright; the worker never attempts a second
        # launch on the same profile (that second launch is exactly what
        # opens a new window in the existing Chrome on the owner's machine).
        if (
            profile == "research"
            and self._research_owner_session_id is not None
            and self._research_owner_session_id != session_id
            and self._research_owner_session_id in self._sessions
        ):
            owner = self._research_owner_session_id
            raise BrowserError(
                ErrorClass.BROWSER_LIFECYCLE_VIOLATION,
                f"session '{owner}' already owns the research browser; close it first",
                retryable=False,
                evidence={"owner_session_id": owner, "requested_session_id": session_id},
            )

        allowed = (
            requested_classes if requested_classes is not None else policy.RESEARCH_SESSION_CLASSES
        )
        profile_dir = self._profile_dir if profile == "research" else None
        backend = ManagedBackend(headless=not visible, profile_dir=profile_dir, channel=channel)
        if profile_dir is not None:
            backend.breaker = self._breaker
        # A single, non-retried launch attempt (ManagedBackend._launch reaps
        # any orphan on profile_dir first); any BrowserError here — including
        # browser_lifecycle_violation if the profile is still locked after
        # reaping — propagates to the caller as-is. Never retried in a loop.
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
            session_uid=str(uuid.uuid4()),
            profile_dir=profile_dir,
            max_tabs=max_tabs,
            last_used=time.monotonic(),
        )
        self._sessions[session_id] = state
        if profile == "research":
            self._research_owner_session_id = session_id
            self._ownership.write(
                {
                    "research_job_id": payload.get("research_job_id") or session_id,
                    "session_id": session_id,
                    "browser_session_id": state.session_uid,
                    "worker_pid": os.getpid(),
                    "chrome_root_pid": backend.main_pid,
                    "chrome_start_time": (
                        launch_guard.process_start_time_iso(backend.main_pid)
                        if backend.main_pid is not None
                        else None
                    ),
                    "launched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "launch_kind": backend.last_launch_kind,
                    "transport": "playwright-pipe",
                    "cdp_endpoint": None,
                    "profile_path": str(profile_dir),
                    "launch_lock": backend.launch_lock_name,
                    "job_object_assigned": backend.job_object_assigned,
                    "channel": state.channel,
                    "visible": visible,
                    "max_tabs": max_tabs,
                    "tab_ids": [0],
                    "closed_at": None,
                    "browser_pid_exited": None,
                }
            )
        # M13 lifecycle (owner-machine incident 2026-09-03): "deliberate and
        # bounded" tabs — a page the loaded page opens on its own
        # (window.open / target=_blank), as opposed to a tab_new/
        # fetch_evidence(tab=new) WE requested, is closed immediately rather
        # than left to silently inflate the session's tab count. Opt-in on
        # the backend (see ManagedBackend.on_popup) — direct
        # BrowserSession/ManagedBackend callers outside the worker are
        # unaffected (M2 semantics unchanged there).
        backend.on_popup(
            lambda page, sid=session_id: asyncio.ensure_future(self._close_popup(sid, page))
        )
        logger.info(
            "browser.session_opened",
            session_id=session_id,
            session_uid=state.session_uid,
            browser_pid=backend.main_pid,
            profile=profile,
            channel=state.channel,
            visible=visible,
        )
        tab_count = await self._current_tab_count(state)
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
            "lifecycle": self._lifecycle_info(state, tab_count=tab_count, reused=False),
        }

    async def _op_session_close(self, session_id: str) -> dict[str, Any]:
        state = self._sessions.pop(session_id, None)
        if state is None:
            return {"closed": True, "session_id": session_id, "browser_pid_exited": True}
        tab_count = await self._current_tab_count(state)
        lifecycle_info = self._lifecycle_info(state, tab_count=tab_count, reused=True)
        # M13 lifecycle (owner-machine incident 2026-09-03): close the
        # context AND wait (bounded 10s) for the profile's chrome process(es)
        # to actually be gone from the OS — the worker must never report a
        # session closed while its Chrome is still alive.
        exited = await self._close_session_state(session_id, state, op="session_close")
        return {
            "closed": True,
            "session_id": session_id,
            "browser_pid_exited": exited,
            "lifecycle": lifecycle_info,
        }

    async def _op_worker_status(self) -> dict[str, Any]:
        now = time.monotonic()
        sessions_info = []
        for sid, state in self._sessions.items():
            tab_count = await self._current_tab_count(state)
            try:
                current_url = redact_url(state.browser_session.backend.current_page.url)
            except Exception:
                current_url = None
            sessions_info.append(
                {
                    "session_id": sid,
                    "session_uid": state.session_uid,
                    "browser_pid": state.backend.main_pid,
                    "tabs": tab_count,
                    "tab_count": tab_count,
                    "idle_s": round(now - state.last_used, 1),
                    "current_url": current_url,
                }
            )
        browser_dict: dict[str, Any] = (
            self._browser_info.as_dict()
            if self._browser_info
            else {"channel": self._default_channel, "available": False, "version": None}
        )
        browser_dict = {**browser_dict, "alive": bool(browser_dict.get("available"))}
        return {
            "worker_version": WORKER_VERSION,
            "contracts": dict(CONTRACTS),
            "module": release.module_info(),
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
        # M13 lifecycle (owner-machine incident 2026-09-03): refuse BEFORE
        # opening anything when the new tab would cross max_tabs — the tab
        # count is therefore unchanged by a refused call.
        current = await self._current_tab_count(state)
        lifecycle.check_tab_budget(current, state.max_tabs, op="tab_new")
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
        if wait_for == "verification_cleared":
            return await self._wait_verification_cleared(page, timeout_ms=timeout_ms)
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

    async def _wait_verification_cleared(self, page: Page, *, timeout_ms: float) -> dict[str, Any]:
        """``browser.wait for=verification_cleared`` (contract §3a, READ).

        Polls the page every ~500 ms — a bounded DOM/navigation condition,
        never a fixed sleep — until :func:`search_engines.is_verification_cleared`
        is true or ``timeout_ms`` elapses. ``satisfied: false`` on timeout is
        the normal "still waiting" answer, not an error.
        """
        start = time.perf_counter()
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            try:
                html = await page.content()
            except Exception:
                html = ""
            cleared = search_engines.is_verification_cleared(page.url, html)
            if cleared or time.monotonic() >= deadline:
                return {"satisfied": cleared, "url": page.url, "elapsed_ms": _elapsed_ms(start)}
            await asyncio.sleep(_VERIFICATION_CLEARED_POLL_S)

    # ------------------------------------------------------------------ #
    # Google through the real UI (contract §3a)
    # ------------------------------------------------------------------ #

    def _google_home_url(self, locale: str | None) -> str:
        """Google's home page URL, with ``hl``/``gl`` when a locale is known.

        Built from ``--google-base-url`` (default the real Google; the
        browser e2e suite points it at the fixture site), not hardcoded, so
        the query string appends onto whatever base is configured — a plain
        host (``https://www.google.com``) or a fixture path that already
        carries its own query string.
        """
        params = search_engines.locale_params(locale)
        if not params:
            return self._google_base_url
        sep = "&" if "?" in self._google_base_url else "?"
        return f"{self._google_base_url}{sep}{urlencode(params)}"

    async def _is_google_results_page(self, page: Page) -> bool:
        """Whether the current page is already on the Google host with a
        visible search box — i.e. a second search can type into it directly
        instead of navigating home again (contract §3a)."""
        try:
            if urlsplit(page.url).netloc != urlsplit(self._google_base_url).netloc:
                return False
        except Exception:
            return False
        try:
            return (await _GOOGLE_SEARCH_BOX_TARGET.to_locator(page).count()) > 0
        except Exception:
            return False

    async def _google_box_value(self, page: Page) -> str | None:
        """The current value typed into Google's search box, or ``None`` when
        it cannot be read (no box, detached page, …)."""
        try:
            return await _GOOGLE_SEARCH_BOX_TARGET.to_locator(page).first.input_value()
        except Exception:
            return None

    async def _wait_google_ready(self, page: Page, *, timeout_ms: float) -> None:
        """Poll until the results region (``#search``/``#rso``) or an
        interstitial appears, or ``timeout_ms`` elapses — a DOM/navigation
        condition, never a fixed sleep (contract §3a)."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            try:
                has_results = (await page.locator("#search, #rso").count()) > 0
            except Exception:
                has_results = False
            if has_results:
                return
            try:
                html = await page.content()
            except Exception:
                html = ""
            if detect_google_interstitial(html, page.url) is not None:
                return
            if time.monotonic() >= deadline:
                return
            await asyncio.sleep(_GOOGLE_READY_POLL_S)

    async def _google_ui_fetch(
        self,
        state: SessionState,
        query: str,
        *,
        locale: str | None,
        recency_days: int | None,
        interstitial_mode: str,
    ) -> tuple[str, str, int | None, str, str | None]:
        """One Google attempt, driven through the real page (contract §3a).

        Returns a 5-tuple compatible with :func:`search_engines.run_search`'s
        ``fetch`` seam: ``(html, page_kind, http_status, final_url,
        path_hint)``. Raises :class:`search_engines.GoogleHandoffPending`
        instead of returning when ``interstitial_mode == "handoff"`` and an
        interstitial is hit — ``run_search`` stops immediately on that, never
        falling back (never looping).
        """
        browser_session = state.browser_session
        page = browser_session.backend.current_page

        # Resume after an owner-handoff clearance (contract §3a): consumed
        # regardless of outcome — a stale/mismatched query falls through to
        # a normal (typed) attempt below.
        awaiting_query = state.awaiting_verification_query
        state.awaiting_verification_query = None
        if awaiting_query is not None and awaiting_query == query:
            html = await page.content()
            still_blocked = detect_google_interstitial(html, page.url)
            if still_blocked is None and await self._google_box_value(page) == query:
                state.verification_cleared_once = True
                return html, "ok", None, page.url, "handoff_cleared"
            if still_blocked is not None:
                # The owner did not (or could not) complete the page. Google is NOT
                # attempted again: the interstitial is recorded as this attempt's
                # outcome and ``run_search`` moves to the next provider (unattended
                # policy after a handoff timeout), or - in handoff mode - the same
                # pending state is reported once more without a new navigation.
                if interstitial_mode == "handoff" and not state.verification_cleared_once:
                    state.awaiting_verification_query = query
                    raise search_engines.GoogleHandoffPending(
                        page_kind=still_blocked,
                        verification_url=page.url,
                        detail="handoff: owner verification still pending; not retried",
                    )
                return html, still_blocked, None, page.url, "handoff_timeout_fallback"

        if not await self._is_google_results_page(page):
            home_url = self._google_home_url(locale)
            self._check_destination(home_url, op="search")
            await browser_session.navigate(home_url, timeout_ms=DEFAULT_NAV_TIMEOUT_MS)
            page = browser_session.backend.current_page

        box = _GOOGLE_SEARCH_BOX_TARGET.to_locator(page).first
        try:
            await box.wait_for(state="visible", timeout=5_000)
            await box.fill(query)
            await box.press("Enter")
        except Exception as exc:
            raise map_playwright_error(
                exc, phase=Phase.ACT, op="search", evidence={"url": redact_url(page.url)}
            ) from exc

        await self._wait_google_ready(page, timeout_ms=_GOOGLE_READY_TIMEOUT_MS)
        raw = await read_raw_page_data(page)
        body_text = await browser_session.page_text()
        kind_result = classify_page(
            title=raw.title,
            heading_text=raw.heading_text,
            body_text=body_text,
            has_password_field=raw.has_password_field,
            http_status=None,
        )
        html = await page.content()
        final_url = page.url
        interstitial = detect_google_interstitial(html, final_url)

        if interstitial is not None and interstitial_mode == "handoff":
            if state.verification_cleared_once or state.verification_handoffs >= 1:
                # Retry once, never loop: one verification has already been handed
                # to the owner in this session. A further interstitial is recorded
                # and the provider fallback applies.
                logger.warning(
                    "browser.google_interstitial_after_verification",
                    session_id=state.session_id,
                    page_kind=interstitial,
                    handoffs=state.verification_handoffs,
                )
                return html, interstitial, None, final_url, "handoff_repeat_fallback"
            state.verification_handoffs += 1
            await page.bring_to_front()
            await asyncio.to_thread(lifecycle.bring_process_window_to_front, state.backend.main_pid)
            state.awaiting_verification_query = query
            raise search_engines.GoogleHandoffPending(
                page_kind=interstitial, verification_url=final_url
            )

        if interstitial is None and recency_days is not None:
            filtered_url = search_engines.append_recency_param(final_url, recency_days)
            self._check_destination(filtered_url, op="search")
            await browser_session.navigate(filtered_url, timeout_ms=DEFAULT_NAV_TIMEOUT_MS)
            page = browser_session.backend.current_page
            await self._wait_google_ready(page, timeout_ms=_GOOGLE_READY_TIMEOUT_MS)
            html = await page.content()
            final_url = page.url
            return html, kind_result.page_kind, None, final_url, "google_url"

        return html, kind_result.page_kind, None, final_url, "google_ui"

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
        # ``mode``: "interactive" (Google -> owner handoff if needed -> fallback only
        # afterwards) or "unattended" (Google -> deterministic fallback if blocked).
        # It is the owner-facing name of ``interstitial`` (handoff / fallback); an
        # explicit ``interstitial`` wins when both are given.
        mode = payload.get("mode")
        if mode is not None and mode not in _SEARCH_MODES:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"search: mode must be one of {sorted(_SEARCH_MODES)}",
                retryable=False,
            )
        interstitial_mode = payload.get(
            "interstitial", "handoff" if mode == "interactive" else "fallback"
        )
        if interstitial_mode not in _INTERSTITIAL_MODES:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"search: interstitial must be one of {sorted(_INTERSTITIAL_MODES)}",
                retryable=False,
            )
        effective_mode = "interactive" if interstitial_mode == "handoff" else "unattended"

        locale = payload.get("locale") or self._locale
        if locale is not None and not isinstance(locale, str):
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                "search: locale must be a string or null",
                retryable=False,
            )
        browser_session = state.browser_session

        async def fetch(_engine: str, url: str) -> tuple[str, str, int | None, str, str | None]:
            if _engine == "google":
                # Google is driven through the real page, not fetched by URL
                # (contract §3a) — ``url`` (Google's results URL) is unused
                # here; the attempt navigates/types instead.
                return await self._google_ui_fetch(
                    state,
                    query,
                    locale=locale,
                    recency_days=recency_days,
                    interstitial_mode=interstitial_mode,
                )
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
            return html, kind_result.page_kind, http_status, page.url, None

        outcome = await search_engines.run_search(
            query,
            engine,
            fetch=fetch,
            max_results=max_results,
            recency_days=recency_days,
            locale=locale,
        )
        logger.info(
            "browser.search_provider",
            requested_provider=outcome.requested_provider,
            provider=outcome.provider,
            fallback=outcome.fallback,
            fallback_reason=outcome.fallback_reason,
            result_count=len(outcome.results),
            query_chars=len(query),
            attempts=[a.as_dict() for a in outcome.attempts],
            path=outcome.path,
            state=outcome.state,
            mode=effective_mode,
        )
        result = outcome.as_dict()
        result["mode"] = effective_mode
        result["verification_handoffs"] = state.verification_handoffs
        return result

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
        tab = payload.get("tab", "same")
        if tab not in _FETCH_EVIDENCE_TAB_VALUES:
            raise BrowserError(
                ErrorClass.VALIDATION_ERROR,
                f"fetch_evidence: tab must be one of {sorted(_FETCH_EVIDENCE_TAB_VALUES)}",
                retryable=False,
            )

        browser_session = state.browser_session
        new_tab_index: int | None = None
        previous_tab_index: int | None = None
        try:
            if tab == "new":
                # Open a blank tab first (no direct URL nav here — that
                # wouldn't hand back a Response for http_status), select it,
                # then navigate normally through the session so the Google
                # results tab (contract §3a) is never disturbed.
                tabs_before = await browser_session.list_tabs()
                # M13 lifecycle (owner-machine incident 2026-09-03): refuse
                # BEFORE opening anything when this would cross max_tabs —
                # the tab count is therefore unchanged by a refused call.
                lifecycle.check_tab_budget(len(tabs_before), state.max_tabs, op="fetch_evidence")
                previous_tab_index = next((t.index for t in tabs_before if t.is_current), 0)
                new_tab_index = await browser_session.new_tab(None)

            response = await browser_session.navigate(url, timeout_ms=timeout_ms)
            page = browser_session.backend.current_page
            # DOM/navigation-driven settle (bounded, exceptions swallowed) —
            # replaces the old fixed sleep (contract §3a).
            with suppress(Exception):
                await page.wait_for_load_state(
                    "networkidle", timeout=_FETCH_EVIDENCE_NETWORKIDLE_TIMEOUT_MS
                )

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

            result: dict[str, Any] = {
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
                "tab_used": tab,
            }
        finally:
            if new_tab_index is not None:
                with suppress(Exception):
                    await browser_session.close_tab(new_tab_index)
                if previous_tab_index is not None:
                    with suppress(Exception):
                        await browser_session.select_tab(previous_tab_index)

        # Audit/log line after the result is known (never blocking the
        # interactive path above).
        logger.info(
            "browser.fetch_evidence",
            url=redact_url(url),
            page_kind=result["page_kind"],
            tab=tab,
            text_chars=result["text_chars"],
            injection_markers=result["injection_markers"],
        )
        return result


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
        "--locale",
        default=None,
        help="BCP-47 locale for search providers (default: the machine's user locale)",
    )
    parser.add_argument(
        "--allow-private-destinations",
        action="store_true",
        help="Permit loopback/private/tailnet destinations (fixture tests only)",
    )
    parser.add_argument(
        "--google-base-url",
        default=None,
        help=(
            "Base URL for Google's home page used by the Google-through-the-UI "
            "browser.search flow (contract §3a; default: https://www.google.com). "
            "The browser e2e suite points this at the fixture site."
        ),
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
    # M13 lifecycle (owner-machine incident 2026-09-03): the worker must
    # never exit leaving a Chrome it launched. run()'s own finally already
    # closes every session gracefully; this is the best-effort synchronous
    # backstop for the case where even that does not run (an unhandled
    # exception escaping asyncio.run, a plain sys.exit, ...). Registered only
    # here — the real CLI process — never for an in-process Worker created
    # directly by tests (tests/conftest.py's `worker` fixture bypasses main()
    # and closes sessions itself in fixture teardown).
    atexit.register(worker._atexit_cleanup)
    return asyncio.run(worker.run())


if __name__ == "__main__":
    raise SystemExit(main())
