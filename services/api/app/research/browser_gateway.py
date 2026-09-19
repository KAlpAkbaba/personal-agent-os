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
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

from app.devices.commands import (
    CommandExpired,
    CommandFailed,
    CommandOutcome,
    CommandSucceeded,
    DeviceCommandClientProtocol,
)
from app.logging import get_logger
from app.research.contracts import (
    ENTITY_DISCOVERED_RESULT,
    ContractViolation,
    require_number,
    require_text,
)
from app.research.destination import DestinationPolicyError, validate_fetch_target
from app.research.evidence import EvidenceRecord
from app.research.forbidden_keys import find_forbidden_keys

# Fixed fallback "now" so FakeBrowserGateway is deterministic even when the
# caller does not pass `now` explicitly (mirrors DeterministicResearchProvider's
# no-wall-clock discipline, M3).
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

#: How many characters of page text ``browser.fetch_evidence`` is asked for (2026-09-19
#: incident, docs/DECISIONS.md ADR addendum after ADR-0173: production run 817c558a's
#: excerpts were all exactly 1200 chars and started with page chrome — the old default
#: — so the whole budget was routinely spent before the article's own text began).
#: Bounded by the device result envelope's own cap, ``MAX_RESULT_BYTES = 48 KiB``
#: (services/browser/browser_agent/worker.py, a device-side constant this module does
#: not import — the two processes agree on the wire shape, not on Python objects, same
#: as the rest of this module's boundary discipline). Worst-case UTF-8 is 4 bytes/char,
#: so 48 KiB could in principle hold as few as ~12 KB of characters once other envelope
#: fields (title/url/metadata/etc.) are accounted for; 8000 chars leaves comfortable
#: headroom under that worst case while still being ~6.7x the old 1200-char default.
DEFAULT_EXCERPT_CHARS = 8000


logger = get_logger("app.research.browser_gateway")


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


#: Distinct paragraphs per result index. The fake has to produce pages the quality gate
#: accepts - on topic, long enough to judge, and different enough from each other to
#: survive deduplication - because a stub of one sentence is neither realistic nor usable.
_FAKE_ANGLES = (
    (
        "duyuruldu ve ilk kullanıcılara açıldı. Yayınlanan notlarda araç kullanımı, "
        "kalıcı hafıza ve çok adımlı görev planlaması yeteneklerinin genişletildiği "
        "belirtiliyor. Kurumsal erişimin bu hafta başlayacağı ve fiyatlandırmanın "
        "kullanım başına belirleneceği açıklandı."
    ),
    (
        "için yeni bir yapay zeka ajanı sürümü yayınlandı. Sürüm, araç çağırma "
        "protokolü desteği, daha "
        "iyi hafıza yönetimi ve çok ajanlı iş akışları için bir planlayıcı içeriyor. "
        "Geliştiriciler, otonom ajanların üretim ortamında çalıştırılmasının belirgin "
        "şekilde kolaylaştığını söylüyor."
    ),
    (
        "üzerine yapılan yapay zeka ajanı araştırması yayımlandı. Rapor, otonom ajan "
        "iş akışlarının görev tamamlama oranlarını, "
        "insan onayı gereken adımları ve araç entegrasyonlarının maliyetini ölçüyor. En "
        "yaygın kullanım alanı müşteri desteği olarak öne çıkıyor ve kurumların "
        "ölçeklendirme planları aktarılıyor."
    ),
    (
        "kapsamında yapay zeka ajanlarının güvenlik değerlendirmesi paylaşıldı. "
        "Değerlendirme, ajanların yetki sınırlarını, araç çağrılarının denetlenmesini ve "
        "istem enjeksiyonuna karşı alınan önlemleri ele alıyor. Ekipler, insan onayı "
        "gerektiren adımların açıkça tanımlanmasını öneriyor."
    ),
    (
        "ile ilgili ajan tabanlı otomasyon girişimi yeni bir yatırım turu duyurdu. "
        "Şirket, yapay zeka ajanlarının kurumsal iş akışlarını uçtan uca yürütmesini "
        "hedefliyor ve kaynağın ürün ekibi ile araç entegrasyonlarına ayrılacağını "
        "belirtiyor."
    ),
)


#: Headlines that describe genuinely different events. Same-story detection collapses
#: pages whose titles say the same thing, so a fake that reused one headline would leave a
#: single finding no matter how many pages it produced.
_FAKE_TITLES = (
    "Yapay zeka ajanı platformu duyuruldu",
    "Açık kaynak yapay zeka ajanı çerçevesi 2.0 yayınlandı",
    "Kurumsal yapay zeka ajanı kullanımı araştırması yayımlandı",
    "Yapay zeka ajanları için güvenlik değerlendirmesi paylaşıldı",
    "Ajan tabanlı otomasyon girişimi yeni yatırım aldı",
)


def _fake_page_title(index: int) -> str:
    return _FAKE_TITLES[index % len(_FAKE_TITLES)]


def _fake_page_body(query: str, source_class: str, index: int) -> str:
    angle = _FAKE_ANGLES[index % len(_FAKE_ANGLES)]
    return f"{query} konusunda {source_class} kaynağında yer alan gelişme {angle}"


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
                        title=_fake_page_title(i),
                        excerpt=_fake_page_body(q.query, q.source_class, i),
                        fetched_at=moment,
                        # A page a person would accept as evidence says when it was
                        # published; the pre-synthesis quality gate refuses undated pages,
                        # so a fake that omitted this would exercise a pipeline no real
                        # run can reach.
                        published_at=moment - timedelta(hours=6 + i),
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
    which provider was requested, which one answered, whether a fallback happened and why.

    §3a extends the contract with an owner-handoff outcome: ``state`` ("ok" or
    "waiting_for_owner_verification"), ``path`` (which route the worker took —
    google_ui/google_url/fallback/handoff_pending/handoff_cleared/
    handoff_timeout_fallback) and ``verification_url`` (set while waiting)."""

    requested_provider: str
    provider: str
    fallback: bool
    fallback_reason: str | None
    query: str
    result_count: int
    attempts: tuple[dict[str, Any], ...] = ()
    schema_version: int = 0
    locale: str | None = None
    state: str = "ok"
    path: str | None = None
    verification_url: str | None = None
    page_kind: str | None = None
    #: Schema 3 (contract §3a, 2026-09-04): the worker's single verification block -
    #: {handoffs, outcome: pending|cleared|timeout|repeat|None, interstitial, verification_url}.
    #: The interstitial kind is evidence exactly once, here; older schema-2 workers send none.
    verification: dict[str, Any] | None = None

    #: The search response schema that carries provider evidence (BROWSER_CAPABILITIES §3).
    #: Schema 3 adds the verification block; 2 is still accepted (its fields stay None).
    REQUIRED_SCHEMA_VERSION = 2

    @property
    def contract_ok(self) -> bool:
        return self.schema_version >= self.REQUIRED_SCHEMA_VERSION

    @property
    def waiting_for_owner_verification(self) -> bool:
        return self.state == "waiting_for_owner_verification"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_ok": self.contract_ok,
            "locale": self.locale,
            "requested_provider": self.requested_provider,
            "provider": self.provider,
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "query": self.query,
            "result_count": self.result_count,
            "attempts": list(self.attempts),
            "state": self.state,
            "path": self.path,
            "verification_url": self.verification_url,
            "page_kind": self.page_kind,
            "verification": self.verification,
        }

    @classmethod
    def from_result(cls, query: str, result: dict[str, Any]) -> SearchEvidence:
        schema_version = result.get("schema_version")
        schema_version = int(schema_version) if isinstance(schema_version, int) else 0
        provider = str(result.get("provider") or "unknown")
        requested = str(result.get("requested_provider") or "unknown")
        hits = result.get("results") or []
        verification = result.get("verification")
        verification = dict(verification) if isinstance(verification, dict) else None
        # Schema 3 keeps the interstitial kind ONLY in the verification block and reports
        # page_kind="waiting" while a handoff is pending; older workers put it at the top level.
        page_kind = str(result.get("page_kind")) if result.get("page_kind") else None
        if verification and verification.get("interstitial") and page_kind in (None, "waiting"):
            page_kind = str(verification["interstitial"])
        verification_url = result.get("verification_url") or (
            verification.get("verification_url") if verification else None
        )
        return cls(
            schema_version=schema_version,
            locale=(str(result.get("locale")) if result.get("locale") else None),
            requested_provider=requested,
            provider=provider,
            fallback=bool(result.get("fallback", provider != requested)),
            fallback_reason=(
                str(result.get("fallback_reason")) if result.get("fallback_reason") else None
            ),
            query=str(result.get("query") or query),
            result_count=int(result.get("result_count", len(hits))),
            attempts=tuple(a for a in (result.get("attempts") or []) if isinstance(a, dict)),
            state=str(result.get("state") or "ok"),
            path=(str(result.get("path")) if result.get("path") else None),
            verification_url=(str(verification_url) if verification_url else None),
            page_kind=page_kind,
            verification=verification,
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


def _is_unknown_session_error(exc: BrowserDispatchError) -> bool:
    """BROWSER_CAPABILITIES.md §2: "An operation on an unknown session fails
    with ``validation_error`` (``message`` starts with ``unknown session``) —
    Cloud Core re-opens and retries"."""
    return exc.error_class == "validation_error" and exc.message.lower().startswith(
        "unknown session"
    )


# --------------------------------------------------------- process-wide session registry
#
# Spec §5a: "DeviceBrowserGateway opens it [the session] once per process (a
# known-open cache keyed by device+session, invalidated by an "unknown
# session" answer, then re-opened once)". Every activity constructs its own
# DeviceBrowserGateway instance (Temporal activities are plain functions with
# no shared state across calls), so the cache has to live at module level,
# keyed by (device_id, session_id), rather than on the instance — otherwise
# every discover/fetch/await_verification activity in a job would re-issue
# ``browser.session_open`` even though the device's own idempotency store
# would just no-op it. It is an optimisation only: on a fresh process (e.g.
# after a worker restart) ``ensure_session`` simply reopens once more, which
# is safe because ``browser.session_open`` is itself idempotent/reuse-safe.
_registry_lock = threading.Lock()
_KNOWN_OPEN_SESSIONS: set[tuple[str, str]] = set()


def reset_known_open_sessions() -> None:
    """Test-only: clear the process-wide registry so tests that reuse the
    same device/task id fixtures do not leak "already open" state between
    each other. Harmless to call in production (just forces one extra
    ``browser.session_open`` the next time a gateway is used)."""
    with _registry_lock:
        _KNOWN_OPEN_SESSIONS.clear()


class DeviceBrowserGateway:
    last_search_evidence: SearchEvidence | None = None
    """Real gateway: dispatches ``browser.*`` commands to one selected device
    over :class:`~app.devices.commands.DeviceCommandClientProtocol`, using
    exactly the payload/result shapes of ``packages/protocol/
    BROWSER_CAPABILITIES.md`` §2-§3/§3a. A research session is opened
    READ+NAVIGATE only (§4) and reused for every command in the run —
    ``session_id`` is the research task id, per the contract's own suggestion
    (§2). ``ensure_session`` consults the process-wide known-open registry
    (spec §5a) so ``browser.session_open`` is dispatched at most once per job
    per process, and any command that comes back with an "unknown session"
    ``validation_error`` invalidates the registry entry, reopens once, and
    retries that one command (never more than once).
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
        excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
        search_provider: str = "duckduckgo",
    ) -> None:
        self._client = command_client
        self._device_id = device_id
        self._session_id = task_id
        self._trace_id = trace_id or task_id
        self._timeout_s = timeout_s
        self._excerpt_chars = excerpt_chars
        #: PRODUCT DECISION (owner, 2026-09-04): DuckDuckGo is the default
        #: engine sent on every ``browser.search`` this gateway issues; Google
        #: stays fully selectable (via this arg or the per-call `engine`
        #: override on `search()`), including its CAPTCHA/owner-handoff path.
        self._search_provider = search_provider
        self._session_opened = False
        self._session_open_attempt = 0

    def _registry_key(self) -> tuple[str, str]:
        return (str(self._device_id), self._session_id)

    def _open_session(self) -> dict[str, Any]:
        self._session_open_attempt += 1
        outcome = self._client.run(
            device_id=self._device_id,
            capability="browser.session_open",
            payload={
                "session_id": self._session_id,
                "profile": "research",
                "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": True},
                "channel": "chrome",
            },
            idempotency_key=f"{self._session_id}:session_open:{self._session_open_attempt}",
            timeout_s=self._timeout_s,
            trace_id=self._trace_id,
        )
        result = _outcome_or_raise(outcome)
        with _registry_lock:
            _KNOWN_OPEN_SESSIONS.add(self._registry_key())
        self._session_opened = True
        return result

    def ensure_session(self) -> dict[str, Any]:
        with _registry_lock:
            already_open = self._registry_key() in _KNOWN_OPEN_SESSIONS
        if already_open:
            self._session_opened = True
            return {"created": False}
        return self._open_session()

    def _invalidate_session(self) -> None:
        with _registry_lock:
            _KNOWN_OPEN_SESSIONS.discard(self._registry_key())
        self._session_opened = False

    def _run(
        self,
        capability: str,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> tuple[dict[str, Any], uuid.UUID | None]:
        """Dispatch one ``browser.*`` command, opening the session first
        (no-op after the first call in this process, spec §5a). A single
        "unknown session" answer invalidates the registry, reopens once with
        a fresh idempotency key (so the reopen is a real dispatch, not a
        replay of the stale terminal ack), and retries this SAME command
        exactly once with a distinct idempotency key of its own — never more
        than one retry."""
        self.ensure_session()
        outcome = self._client.run(
            device_id=self._device_id,
            capability=capability,
            payload=payload,
            idempotency_key=idempotency_key,
            timeout_s=self._timeout_s,
            trace_id=self._trace_id,
            heartbeat=heartbeat,
        )
        try:
            result = _outcome_or_raise(outcome)
        except BrowserDispatchError as exc:
            if not _is_unknown_session_error(exc):
                raise
            self._invalidate_session()
            self._open_session()
            outcome = self._client.run(
                device_id=self._device_id,
                capability=capability,
                payload=payload,
                idempotency_key=f"{idempotency_key}:session-retry",
                timeout_s=self._timeout_s,
                trace_id=self._trace_id,
                heartbeat=heartbeat,
            )
            result = _outcome_or_raise(outcome)
        _reject_forbidden_keys(result)
        command_id = outcome.command_id if isinstance(outcome, CommandSucceeded) else None
        return result, command_id

    def search(
        self,
        query: str,
        *,
        source_class: str = "unknown",
        max_results: int = 10,
        interstitial: str = "fallback",
        engine: str | None = None,
    ) -> list[SearchHit]:
        """``browser.search`` (BROWSER_CAPABILITIES.md §3/§3a). ``interstitial``
        selects the owner-handoff behaviour: "fallback" (unattended — the
        worker tries the next provider) or "handoff" (owner present — the
        worker brings Chrome forward and returns
        ``state=waiting_for_owner_verification`` instead of solving/retrying
        anything). Provider evidence (including the §3a additions) is
        recorded on ``self.last_search_evidence`` after every call.
        ``engine`` overrides the gateway's configured ``search_provider`` for
        this one call when given; otherwise the configured provider (default
        "duckduckgo", PRODUCT DECISION 2026-09-04) is sent."""
        digest = hashlib.sha256(f"{query}:{source_class}".encode()).hexdigest()[:16]
        result, _command_id = self._run(
            "browser.search",
            {
                "session_id": self._session_id,
                "query": query,
                "engine": engine if engine is not None else self._search_provider,
                "max_results": max_results,
                "recency_days": 3,
                "interstitial": interstitial,
            },
            f"{self._session_id}:search:{digest}",
        )
        self.last_search_evidence = SearchEvidence.from_result(query, result)
        # A malformed hit is skipped with its reason, never fatal: one bad row in a provider's
        # result list must not lose the other nine (owner incident, 2026-09-04).
        hits: list[SearchHit] = []
        for i, r in enumerate(result.get("results", [])):
            if not isinstance(r, dict):
                logger.warning("research_search_hit_skipped", reason="not_an_object", position=i)
                continue
            try:
                url = require_text(r.get("url"), ENTITY_DISCOVERED_RESULT, "url")
                rank = require_number(
                    r.get("rank", i + 1), ENTITY_DISCOVERED_RESULT, "rank", entity_id=url
                )
                hits.append(
                    SearchHit(
                        url=url,
                        title=require_text(
                            r.get("title", ""), ENTITY_DISCOVERED_RESULT, "title", entity_id=url
                        ),
                        snippet=require_text(
                            r.get("snippet", ""), ENTITY_DISCOVERED_RESULT, "snippet", entity_id=url
                        ),
                        published_hint=r.get("published_hint"),
                        rank=int(rank) if rank is not None else i + 1,
                    )
                )
            except ContractViolation as violation:
                logger.warning("research_search_hit_skipped", **violation.as_dict())
        return hits

    def await_verification(
        self,
        *,
        timeout_s: float = 60.0,
        iteration: int = 0,
        heartbeat: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """``browser.wait for=verification_cleared`` (BROWSER_CAPABILITIES.md
        §3a): polls the owner's Chrome window until the interstitial is
        cleared or ``timeout_ms`` elapses. The contract caps a single
        ``browser.wait`` command at 60s (browser-family commands run up to
        120s, but the workflow re-issues this call in a loop so it can
        heartbeat and re-check its overall budget between calls); ``iteration``
        makes each call in that loop its own idempotency key (this is NOT a
        retry of the same wait — every call is a genuinely new poll)."""
        capped = min(timeout_s, 60.0)
        result, _command_id = self._run(
            "browser.wait",
            {
                "session_id": self._session_id,
                "for": "verification_cleared",
                "timeout_ms": int(capped * 1000),
            },
            f"{self._session_id}:wait_verification:{iteration}",
            heartbeat=heartbeat,
        )
        return {
            "satisfied": bool(result.get("satisfied", False)),
            "url": result.get("url"),
            "elapsed_ms": result.get("elapsed_ms"),
        }

    def fetch_url(
        self,
        url: str,
        *,
        query: str = "",
        source_class: str = "unknown",
        attempt: int = 1,
        tab: str = "same",
    ) -> EvidenceRecord:
        """One ``browser.fetch_evidence`` command for a single URL.
        ``tab="new"`` (BROWSER_CAPABILITIES.md §3a) opens the URL in a new tab
        and reselects the previous one afterwards, so the research pipeline's
        persistent Google results tab stays loaded between searches."""
        try:
            validate_fetch_target(url)
        except DestinationPolicyError as exc:
            raise BrowserDispatchError("security_scope_error", str(exc), False) from exc

        result, command_id = self._run(
            "browser.fetch_evidence",
            {
                "session_id": self._session_id,
                "url": url,
                "query": query,
                "source_class": source_class,
                "excerpt_chars": self._excerpt_chars,
                "timeout_ms": int(self._timeout_s * 1000),
                "tab": tab,
            },
            fetch_idempotency_key(self._session_id, url, attempt=attempt),
        )
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
            self._invalidate_session()

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
    "DEFAULT_EXCERPT_CHARS",
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
    "reset_known_open_sessions",
]
