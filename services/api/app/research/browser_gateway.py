"""M13 browser gateway seam: how the API dispatches research fetch work.

Production dispatch is NOT a Python import of ``services/browser`` — that
package runs as a separate process on the owner's enrolled Windows machine,
reached over Tailscale via the SAME proven device/broker command path M1
already qualified (``desktop.open_application``, ``desktop.open_artifact``),
extended with ``browser.*`` capabilities. See docs/DECISIONS.md ADR-0035 for
exactly which pieces that requires and do not yet exist: a
``browser.fetch_evidence`` capability entry in the Windows agent's capability
manifest/allowlist, a handler that constructs a
``browser_agent.BrowserSession`` (via ``ManagedBackend`` by default, or
``ExistingSessionBackend`` behind
``browser_agent.enrollment.get_research_authorized_enrollment``) and calls
``browser_agent.research.gather_evidence``, and the async command-dispatch
plumbing to await the (potentially slow, multi-page) result over the device
protocol's command-envelope pattern. ``BrowserGateway`` is the interface that
hides that dispatch from the rest of the M13 pipeline so the pipeline is
testable today without any of it existing yet.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from app.devices.commands import (
    CommandExpired,
    CommandFailed,
    CommandOutcome,
    CommandSucceeded,
    DeviceCommandClientProtocol,
)
from app.research.destination import DestinationPolicyError, validate_fetch_target
from app.research.evidence import EvidenceRecord
from app.research.forbidden_keys import find_forbidden_keys

# Fixed fallback "now" so FakeBrowserGateway is deterministic even when the
# caller does not pass `now` explicitly (mirrors DeterministicResearchProvider's
# no-wall-clock discipline, M3).
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FetchQuery:
    """One (query, source class) the gateway should search and gather from.

    Deliberately query-shaped, not URL-shaped: discovering which URLs answer
    a query (running a search, or visiting a known directory/index page and
    reading its result links) is the browser agent's job — it has the live
    Chrome session and the semantic DOM/accessibility surface
    (``browser_agent.session.BrowserSession``) needed to do that safely.
    Handing the API layer a fixed URL list would defeat the point of a
    *browser* research provider.
    """

    query: str
    source_class: str = "unknown"
    max_results: int = 3


class BrowserGatewayNotConfiguredError(RuntimeError):
    """Raised by an inert (unwired) BrowserGateway before any dispatch."""


@runtime_checkable
class BrowserGateway(Protocol):
    """Dispatch a batch of queries to the Windows Browser Agent, get evidence back."""

    name: str

    def fetch_evidence(
        self,
        queries: list[FetchQuery],
        *,
        enrollment_id: str | None = None,
        now: datetime | None = None,
    ) -> list[EvidenceRecord]: ...


class UnwiredBrowserGateway:
    """The real seam. INERT: raises before any I/O, exactly like
    ``WebResearchProvider`` (M3) / ``ClaudeCodingBackend`` (M6). The dispatch
    path (``browser.fetch_evidence`` over the device/broker) does not exist
    yet (ADR-0035); wiring it is explicitly out of scope for M13's design
    pass. Registering this as the default gateway keeps
    ``BrowserResearchProvider`` honest: calling it for real today fails
    loudly instead of silently returning nothing.
    """

    name = "unwired"

    def fetch_evidence(self, queries, *, enrollment_id=None, now=None):
        raise BrowserGatewayNotConfiguredError(
            "no BrowserGateway is wired; browser.fetch_evidence dispatch over the "
            "device/broker command path is not implemented yet (see "
            "docs/DECISIONS.md ADR-0035). Inject a real gateway, or use "
            "FakeBrowserGateway for offline tests."
        )


class FakeBrowserGateway:
    """Deterministic, offline, seeded gateway for tests and demos.

    Mirrors ``DeterministicResearchProvider``'s role for the M3 provider
    seam: same inputs -> same outputs, no network, no browser, no wall
    clock unless the caller supplies ``now``.
    """

    name = "fake"

    def fetch_evidence(
        self,
        queries: list[FetchQuery],
        *,
        enrollment_id: str | None = None,
        now: datetime | None = None,
    ) -> list[EvidenceRecord]:
        moment = now or _EPOCH
        records: list[EvidenceRecord] = []
        for q in queries:
            for i in range(max(1, q.max_results)):
                url = f"https://{q.source_class}.example.com/{quote(q.query)}/{i}"
                records.append(
                    EvidenceRecord(
                        url=url,
                        title=f"{q.query} — {q.source_class} kaynağı {i + 1}",
                        excerpt=(
                            f"{q.query} ile ilgili {q.source_class} sınıfından bir bulgu "
                            f"(seri no {i + 1})."
                        ),
                        fetched_at=moment,
                        extraction_method="dom_text",
                        source_class=q.source_class,
                        query=q.query,
                    )
                )
        return records


class BrowserDispatchError(RuntimeError):
    """A device command in the browser.* family failed or expired.

    Carries the device taxonomy error class (DEVICE_PROTOCOL.md §8 /
    BROWSER_CAPABILITIES.md §5) so callers (the Temporal fetch activity) can
    decide retry vs. terminal-failure the same way any other device command
    outcome is handled.
    """

    def __init__(self, error_class: str, message: str, retryable: bool) -> None:
        super().__init__(f"{error_class}: {message}")
        self.error_class = error_class
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class SearchHit:
    url: str
    title: str
    snippet: str
    published_hint: str | None = None
    rank: int = 0


@dataclass(frozen=True, slots=True)
class SearchEvidence:
    """Provider evidence returned with every device search (BROWSER_CAPABILITIES.md §3):
    which provider was requested, which one answered, whether a fallback happened and why."""

    requested_provider: str
    provider: str
    fallback: bool
    fallback_reason: str | None
    query: str
    result_count: int
    attempts: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "requested_provider": self.requested_provider,
            "provider": self.provider,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "query": self.query,
            "result_count": self.result_count,
            "attempts": list(self.attempts),
        }

    @classmethod
    def from_result(cls, query: str, result: dict[str, Any]) -> SearchEvidence:
        provider = str(result.get("provider") or result.get("engine") or "unknown")
        requested = str(result.get("requested_provider") or provider)
        hits = result.get("results") or []
        return cls(
            requested_provider=requested,
            provider=provider,
            fallback=bool(result.get("fallback", provider != requested)),
            fallback_reason=(
                str(result["fallback_reason"]) if result.get("fallback_reason") else None
            ),
            query=str(result.get("query") or query),
            result_count=int(result.get("result_count", len(hits))),
            attempts=tuple(a for a in (result.get("attempts") or []) if isinstance(a, dict)),
        )


def _outcome_or_raise(outcome: CommandOutcome) -> dict[str, Any]:
    if isinstance(outcome, CommandSucceeded):
        return outcome.result
    if isinstance(outcome, CommandFailed):
        raise BrowserDispatchError(outcome.error_class, outcome.message, outcome.retryable)
    if isinstance(outcome, CommandExpired):
        raise BrowserDispatchError("timeout", "command expired before a terminal ack", True)
    raise BrowserDispatchError(  # pragma: no cover - defensive, outcome is a closed union
        "internal_bug", f"unknown command outcome {outcome!r}", False
    )


def _reject_forbidden_keys(result: dict[str, Any]) -> None:
    """Cloud-Core-side half of the forbidden-key scan (finding HIGH-3): any
    device command result that becomes evidence is scanned again here, even
    though the worker already scrubs it on the device side — a hit refuses
    the result outright rather than storing a partially-redacted evidence
    item."""
    forbidden = find_forbidden_keys(result)
    if forbidden:
        raise BrowserDispatchError(
            "security_scope_error",
            f"device result contained forbidden key(s): {sorted(set(forbidden))}",
            False,
        )


def fetch_idempotency_key(task_id: str, url: str, *, attempt: int = 1) -> str:
    """``{task_id}:fetch:{sha256(url)[:16]}:{attempt}`` (spec §5) — a NEW key
    per attempt so a retry after ``expired``/``dependency_unavailable`` never
    replays a stale terminal ack for a different logical attempt."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{task_id}:fetch:{digest}:{attempt}"


class DeviceBrowserGateway:
    last_search_evidence: SearchEvidence | None = None
    """Real gateway: dispatches ``browser.*`` commands to one selected device
    over :class:`~app.devices.commands.DeviceCommandClientProtocol`, using
    exactly the payload/result shapes of ``packages/protocol/
    BROWSER_CAPABILITIES.md`` §2-§3. A research session is opened READ+NAVIGATE
    only (§4) and reused for every command in the run — ``session_id`` is the
    research task id, per the contract's own suggestion (§2).
    """

    name = "device"

    def __init__(
        self,
        command_client: DeviceCommandClientProtocol,
        *,
        device_id: uuid.UUID,
        task_id: str,
        trace_id: str = "",
        timeout_s: float = 60.0,
        excerpt_chars: int = 1200,
    ) -> None:
        self._client = command_client
        self._device_id = device_id
        self._session_id = task_id
        self._trace_id = trace_id or task_id
        self._timeout_s = timeout_s
        self._excerpt_chars = excerpt_chars
        self._session_opened = False

    def ensure_session(self) -> dict[str, Any]:
        if self._session_opened:
            return {"created": False}
        outcome = self._client.run(
            device_id=self._device_id,
            capability="browser.session_open",
            payload={
                "session_id": self._session_id,
                "profile": "research",
                "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
                "channel": "chrome",
            },
            idempotency_key=f"{self._session_id}:session_open",
            timeout_s=self._timeout_s,
            trace_id=self._trace_id,
        )
        result = _outcome_or_raise(outcome)
        self._session_opened = True
        return result

    def search(
        self, query: str, *, source_class: str = "unknown", max_results: int = 10
    ) -> list[SearchHit]:
        self.ensure_session()
        digest = hashlib.sha256(f"{query}:{source_class}".encode()).hexdigest()[:16]
        outcome = self._client.run(
            device_id=self._device_id,
            capability="browser.search",
            payload={
                "session_id": self._session_id,
                "query": query,
                "engine": "auto",
                "max_results": max_results,
                "recency_days": 3,
            },
            idempotency_key=f"{self._session_id}:search:{digest}",
            timeout_s=self._timeout_s,
            trace_id=self._trace_id,
        )
        result = _outcome_or_raise(outcome)
        _reject_forbidden_keys(result)
        self.last_search_evidence = SearchEvidence.from_result(query, result)
        return [
            SearchHit(
                url=str(r["url"]),
                title=str(r.get("title", "")),
                snippet=str(r.get("snippet", "")),
                published_hint=r.get("published_hint"),
                rank=int(r.get("rank", i + 1)),
            )
            for i, r in enumerate(result.get("results", []))
        ]

    def fetch_url(
        self,
        url: str,
        *,
        query: str = "",
        source_class: str = "unknown",
        attempt: int = 1,
    ) -> EvidenceRecord:
        """One ``browser.fetch_evidence`` command for a single URL."""
        try:
            validate_fetch_target(url)
        except DestinationPolicyError as exc:
            raise BrowserDispatchError("security_scope_error", str(exc), False) from exc

        self.ensure_session()
        outcome = self._client.run(
            device_id=self._device_id,
            capability="browser.fetch_evidence",
            payload={
                "session_id": self._session_id,
                "url": url,
                "query": query,
                "source_class": source_class,
                "excerpt_chars": self._excerpt_chars,
                "timeout_ms": int(self._timeout_s * 1000),
            },
            idempotency_key=fetch_idempotency_key(self._session_id, url, attempt=attempt),
            timeout_s=self._timeout_s,
            trace_id=self._trace_id,
        )
        result = _outcome_or_raise(outcome)
        _reject_forbidden_keys(result)
        command_id = outcome.command_id if isinstance(outcome, CommandSucceeded) else None
        fetched_at_raw = result.get("fetched_at")
        fetched_at = (
            datetime.fromisoformat(str(fetched_at_raw)) if fetched_at_raw else datetime.now(UTC)
        )
        metadata = result.get("metadata") or {}
        return EvidenceRecord(
            url=url,
            title=str(result.get("title") or ""),
            excerpt=str(result.get("excerpt") or ""),
            fetched_at=fetched_at,
            extraction_method=str(result.get("extraction_method") or "dom_text"),
            source_class=source_class,
            query=query,
            final_url=str(result.get("final_url") or url),
            publisher=str(metadata.get("publisher") or ""),
            published_at=_parse_optional_dt(metadata.get("published_at")),
            modified_at=_parse_optional_dt(metadata.get("modified_at")),
            retrieved_at=fetched_at,
            page_kind=str(result.get("page_kind") or "ok"),
            http_status=result.get("http_status"),
            injection_suspected=bool(result.get("injection_markers", 0)),
            device_id=str(self._device_id),
            command_id=str(command_id) if command_id else None,
        )

    def close_session(self) -> None:
        if not self._session_opened:
            return
        try:
            self._client.run(
                device_id=self._device_id,
                capability="browser.session_close",
                payload={"session_id": self._session_id},
                idempotency_key=f"{self._session_id}:session_close",
                timeout_s=self._timeout_s,
                trace_id=self._trace_id,
            )
        except BrowserDispatchError:
            pass  # best-effort (spec §5: "ignored when the device is gone")
        finally:
            self._session_opened = False

    # ------------------------------------------------- BrowserGateway Protocol

    def fetch_evidence(
        self,
        queries: list[FetchQuery],
        *,
        enrollment_id: str | None = None,
        now: datetime | None = None,
    ) -> list[EvidenceRecord]:
        del enrollment_id, now  # real dispatch: timestamps come from the device
        records: list[EvidenceRecord] = []
        try:
            for q in queries:
                hits = self.search(q.query, source_class=q.source_class, max_results=q.max_results)
                for hit in hits[: q.max_results]:
                    records.append(
                        self.fetch_url(hit.url, query=q.query, source_class=q.source_class)
                    )
        finally:
            self.close_session()
        return records


def _parse_optional_dt(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


__all__ = [
    "SearchEvidence",
    "BrowserDispatchError",
    "BrowserGateway",
    "BrowserGatewayNotConfiguredError",
    "DeviceBrowserGateway",
    "FakeBrowserGateway",
    "FetchQuery",
    "SearchHit",
    "UnwiredBrowserGateway",
    "fetch_idempotency_key",
]
