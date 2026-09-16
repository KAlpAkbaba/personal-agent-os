"""BrowserResearchWorkflow (Temporal) — the durable M13 pipeline (spec §5).

Workflow id: ``research-browser-{task_id}``. Every activity is idempotent, so
a Cloud Core restart resumes the workflow from Temporal history and a
DeviceService/Chrome restart is absorbed by the fetch activity's retry +
next-attempt idempotency key (``app.research.browser_gateway.fetch_idempotency_key``).

The workflow itself holds no DB/device state — every fact it needs to resume
correctly (the plan, the selected device, discovered candidates, fetched
evidence, the synthesized report) lives in Postgres, written by the
activities. The workflow only sequences activities and returns the final
summary — never the full report body inline beyond what
``GET /v1/research/{task_id}`` already returns (the REST route reads the
persisted rows directly; this return value is what the caller/route uses to
know the run finished and where to look).
"""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.research.browser_activities import (
        await_verification_activity,
        close_session_activity,
        discover_activity,
        fail_run_activity,
        fetch_activity,
        fetch_targets_activity,
        persist_artifact_activity,
        plan_activity,
        rank_activity,
        remember_activity,
        select_device_activity,
        synthesize_activity,
    )
    from app.research.contracts import MIN_REPORT_FINDINGS
    from app.research.models import STAGE_FAILED, STAGE_READY
    from app.research.plan import diversify_queries
    from app.research.policy import MODE_QUICK, ResearchPolicy, decide_next_wave

DEFAULT_MAX_SOURCES = 12
DEFAULT_SYNTHESIS = "auto"
#: spec §5a: the owner-handoff wait budget, per waiting occurrence.
DEFAULT_INTERACTIVE_WAIT_S = 600
#: BROWSER_CAPABILITIES.md §3a: a single browser.wait command is capped at 60s.
_VERIFICATION_POLL_S = 60

_SHORT = timedelta(seconds=30)
_MEDIUM = timedelta(seconds=90)
_FETCH_TIMEOUT = timedelta(seconds=120)  # browser.* commands may run up to 120s

_STANDARD_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=20),
    maximum_attempts=3,
)
#: M18.2 owner rule 3 ("ZERO retries for a confirmed challenge ... at most one
#: retry only with clear evidence of a transient navigation failure"): a challenge
#: page is never an exception here at all (app.research.browser_activities.
#: fetch_activity records it as data and returns normally, per ADR-0050's
#: "website error != browser error"), so this policy governs only genuine
#: transport-level dispatch failures (timeout, dependency_unavailable, ...) — capped
#: at one retry (two attempts total), down from three, so even a transient-looking
#: failure never quietly re-spends a QUICK run's tight time budget.
_FETCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=2,
)
_NO_RETRY = RetryPolicy(maximum_attempts=1)


@dataclass
class BrowserResearchRequest:
    task_id: str
    topic: str
    target_device: str | None = None
    recency_days: int | None = None
    max_sources: int = DEFAULT_MAX_SOURCES
    synthesis: str = DEFAULT_SYNTHESIS
    discovery_source_classes: list[str] = field(
        default_factory=lambda: ["official", "technical", "academic", "news", "community"]
    )
    #: spec §5a: owner-handoff mode. False (default) = unattended, the
    #: workflow never waits on a Google interstitial (interstitial="fallback"
    #: immediately). True = the owner is present; a "news"/"community" search
    #: uses interstitial="handoff" and, if the device hands back a
    #: waiting_for_owner_verification result, the workflow waits up to
    #: `interactive_wait_s` for the owner to clear it before falling back.
    interactive: bool = False
    interactive_wait_s: int = DEFAULT_INTERACTIVE_WAIT_S
    #: spec §5a (2026-09-04): what an INTERACTIVE run does when the owner does not
    #: complete Google's page within `interactive_wait_s`. "fallback" (default) =
    #: the unattended policy, one attempt on the next provider; "fail" = stop the
    #: run with a Turkish explanation instead of silently continuing without the
    #: primary provider, so the owner decides (rerun interactively, or unattended).
    #: Unattended runs never wait, so this never applies to them.
    on_verification_timeout: str = "fallback"
    #: PRODUCT DECISION (owner, 2026-09-04): DuckDuckGo is the default
    #: production search provider for every discovery search this run
    #: issues; Google stays fully selectable ("google") — including its
    #: CAPTCHA/owner-handoff machinery, unchanged — or "auto" to let the
    #: device worker's own provider order decide.
    search_provider: str = "duckduckgo"
    #: M18.2 (ADR-0068): "quick" (default, the conversational research fast path) |
    #: "standard" | "deep". Never chosen silently — a caller must ask for anything
    #: past QUICK (app.research.policy.derive_mode_from_utterance /
    #: the REST route's own `research_mode` field).
    mode: str = MODE_QUICK


class OwnerVerificationTimeout(Exception):
    """Interactive run with on_verification_timeout="fail": the owner did not clear
    Google's page in time and asked not to continue on the fallback provider."""


@workflow.defn
class BrowserResearchWorkflow:
    def __init__(self) -> None:
        # B31 req 203/204: the owner's pause. A signal flips the flag; the run holds at
        # its NEXT stage boundary (never mid-activity - an activity is a device command
        # already in flight), and the seconds spent holding are kept out of the budget so
        # "elapsed" stays the run's own time. Cancellation (handle.cancel) still lands
        # while paused: workflow.wait_condition is itself cancellable.
        self._paused = False
        self._paused_seconds = 0.0

    @workflow.signal
    def pause(self) -> None:
        self._paused = True

    @workflow.signal
    def resume(self) -> None:
        self._paused = False

    @workflow.query
    def paused(self) -> bool:
        return self._paused

    @workflow.query
    def paused_seconds(self) -> float:
        return self._paused_seconds

    async def _gate(self) -> None:
        """Hold here while the owner has the run paused (B31 req 203)."""
        if not self._paused:
            return
        held_from = workflow.now()
        await workflow.wait_condition(lambda: not self._paused)
        self._paused_seconds += (workflow.now() - held_from).total_seconds()

    def _elapsed_s(self, run_start) -> float:
        return (workflow.now() - run_start).total_seconds() - self._paused_seconds

    @workflow.run
    async def run(self, request: BrowserResearchRequest) -> dict:
        # ADR-0074: the hard budget is the OWNER's budget, so the clock starts when
        # the run does — before planning and discovery, not (as before) only when the
        # first fetch wave begins. The old start point made `elapsed_s` under-report
        # by the whole discovery stage, so a "120 s" QUICK run could spend 120 s of
        # fetching on top of however long discovery had already taken, and the number
        # the owner saw in diagnostics was not the time the run took.
        # workflow.now() is Temporal's deterministic clock (never datetime.now()).
        run_start = workflow.now()
        plan = await workflow.execute_activity(
            plan_activity,
            args=[
                request.task_id,
                request.topic,
                request.recency_days,
                request.max_sources,
                request.mode,
            ],
            start_to_close_timeout=_SHORT,
            retry_policy=_STANDARD_RETRY,
        )
        # The policy plan_activity resolved and stored ONCE (ADR-0068): every
        # activity that needs it re-reads this SAME stored dict from the run row,
        # so a replay/resume never re-resolves a policy that could have since
        # changed defaults out from under an in-flight run.
        policy = ResearchPolicy.from_dict(plan["policy"])

        try:
            device = await workflow.execute_activity(
                select_device_activity,
                args=[request.task_id, request.target_device],
                start_to_close_timeout=_SHORT,
                retry_policy=_NO_RETRY,
            )
        except ActivityError as exc:
            return self._failed(request.task_id, plan, self._error_detail(exc), "no_capable_device")

        device_id = device["device_id"]

        # M18.2 owner rule 1 ("discovery queries <= 2" for QUICK): cap how many of
        # the plan's expanded queries are actually issued PER source class. This is
        # what keeps a conversational request from repeating the real run's blow-up
        # (254 candidates from every query-expansion template x every source
        # class) — official/technical/academic discovery stays cheap either way (an
        # API call, never a browser), but capping them too keeps the candidate pool
        # itself small enough for the wave loop below to matter.
        # ADR-0074 decision 4: WHICH queries, not just how many. The expansion's own
        # first entries are Turkish near-duplicates of each other ("X", "X haberleri"),
        # so a QUICK run's cap of 2 used to buy one language's view of one engine's
        # coverage; diversify_queries picks the two most DIFFERENT phrasings instead
        # (typically the owner's Turkish and the English core query), which is what
        # gives the domain-diverse shortlist below something diverse to draw on.
        discovery_queries = list(diversify_queries(plan["queries"], policy.discovery_queries_max))

        try:
            for source_class in plan["source_classes"]:
                for i, query_text in enumerate(discovery_queries):
                    query_id = f"{source_class}:{i}"
                    await self._gate()
                    await self._discover_with_handoff(
                        request,
                        device_id,
                        query_id,
                        query_text,
                        source_class,
                        plan["recency"]["start"],
                    )
        except OwnerVerificationTimeout as exc:
            detail = str(exc)
            for activity, args, retry_policy in (
                (
                    fail_run_activity,
                    [request.task_id, "owner_verification_timeout", detail],
                    _STANDARD_RETRY,
                ),
                (close_session_activity, [request.task_id, device_id], _NO_RETRY),
            ):
                try:
                    await workflow.execute_activity(
                        activity,
                        args=args,
                        start_to_close_timeout=_SHORT,
                        retry_policy=retry_policy,
                    )
                except ActivityError:
                    pass
            return self._failed(request.task_id, plan, detail, "owner_verification_timeout")

        # Owner rule 6: fetch in WAVES and stop early, never fetch everything up
        # front. Wave 1 is always fetched (rank_activity must run at least once so
        # its own InsufficientValidEvidence gate still applies exactly as before);
        # every wave after that is a deliberate, bounded decision
        # (app.research.policy.decide_next_wave — pure and unit-tested on its own)
        # weighing evidence-so-far against the mode's wave/time/source budgets.
        #
        # ADR-0074: `max_waves` is a FLOOR of attempts and the hard budget is the
        # ceiling — the loop keeps going while the budget has room for another wave
        # and there is anything fetchable left. `fetchable_remaining` is what a wave
        # itself reports: a wave that comes back with fewer targets than it asked for
        # has exhausted the shortlist (cooled domains, spent quotas, destination
        # policy), which is a different fact from "the budget is gone" and stops the
        # loop on its own reason.
        await self._gate()
        targets = await workflow.execute_activity(
            fetch_targets_activity,
            args=[request.task_id, policy.wave_size, request.topic],
            start_to_close_timeout=_SHORT,
            retry_policy=_STANDARD_RETRY,
        )

        await self._fetch_all(request.task_id, device_id, targets, policy)
        sources_fetched = len(targets)
        waves_used = 1
        # A wave that returns fewer targets than it asked for has drained the
        # shortlist; 0 is then the honest "nothing fetchable left". None means the
        # question is still open, which is never itself a reason to stop.
        fetchable_remaining: int | None = (
            0 if len(targets) < policy.wave_size else None
        )

        try:
            ranked = await workflow.execute_activity(
                rank_activity,
                args=[
                    request.task_id,
                    plan["topic"],
                    plan["recency"]["start"],
                    plan["recency"]["end"],
                ],
                start_to_close_timeout=_SHORT,
                retry_policy=_STANDARD_RETRY,
            )
            evidence_count = int(ranked.get("evidence", 0))

            while True:
                await self._gate()
                elapsed_s = self._elapsed_s(run_start)
                decision = decide_next_wave(
                    policy=policy,
                    evidence_count=evidence_count,
                    waves_used=waves_used,
                    elapsed_s=elapsed_s,
                    sources_fetched=sources_fetched,
                    fetchable_remaining=fetchable_remaining,
                    # A run that could already publish a FULL report spends only the
                    # soft budget looking for one more finding; the whole hard budget
                    # belongs to a run that would otherwise have nothing to say.
                    publishable=evidence_count >= MIN_REPORT_FINDINGS,
                )
                if not decision.should_fetch:
                    break
                extra = await workflow.execute_activity(
                    fetch_targets_activity,
                    args=[request.task_id, decision.fetch_count, request.topic],
                    start_to_close_timeout=_SHORT,
                    retry_policy=_STANDARD_RETRY,
                )
                if not extra:
                    break  # discovery has nothing left to offer
                if len(extra) < decision.fetch_count:
                    fetchable_remaining = 0
                await self._fetch_all(request.task_id, device_id, extra, policy)
                sources_fetched += len(extra)
                waves_used += 1
                ranked = await workflow.execute_activity(
                    rank_activity,
                    args=[
                        request.task_id,
                        plan["topic"],
                        plan["recency"]["start"],
                        plan["recency"]["end"],
                    ],
                    start_to_close_timeout=_SHORT,
                    retry_policy=_STANDARD_RETRY,
                )
                evidence_count = int(ranked.get("evidence", 0))

            await self._gate()
            elapsed_s = self._elapsed_s(run_start)
            run_stats = {
                "mode": policy.mode,
                "budget_s": policy.hard_budget_s,
                "elapsed_s": elapsed_s,
                "waves": waves_used,
                "paused_s": round(self._paused_seconds, 3),
            }
            report = await workflow.execute_activity(
                synthesize_activity,
                args=[
                    request.task_id,
                    plan["topic"],
                    plan["recency"],
                    request.synthesis,
                    run_stats,
                ],
                start_to_close_timeout=_MEDIUM,
                retry_policy=_STANDARD_RETRY,
            )

            persisted = await workflow.execute_activity(
                persist_artifact_activity,
                args=[request.task_id, plan["topic"]],
                start_to_close_timeout=_MEDIUM,
                retry_policy=_STANDARD_RETRY,
            )

            memory_id = await workflow.execute_activity(
                remember_activity,
                args=[request.task_id, plan["topic"]],
                start_to_close_timeout=_SHORT,
                retry_policy=_STANDARD_RETRY,
            )

        except ActivityError as exc:
            # The run must end in a visible terminal state even when an activity
            # exhausts its retries (seen live: a synthesis failure left the status
            # endpoint on 'ranking' until the harness gave up).
            detail = self._error_detail(exc)
            error_class = self._error_class(exc)
            try:
                await workflow.execute_activity(
                    fail_run_activity,
                    args=[request.task_id, error_class, detail],
                    start_to_close_timeout=_SHORT,
                    retry_policy=_STANDARD_RETRY,
                )
            except ActivityError:
                pass
            try:
                await workflow.execute_activity(
                    close_session_activity,
                    args=[request.task_id, device_id],
                    start_to_close_timeout=_SHORT,
                    retry_policy=_NO_RETRY,
                )
            except ActivityError:
                pass
            return self._failed(request.task_id, plan, detail, error_class)

        try:
            await workflow.execute_activity(
                close_session_activity,
                args=[request.task_id, device_id],
                start_to_close_timeout=_SHORT,
                retry_policy=_NO_RETRY,
            )
        except ActivityError:
            pass  # best-effort (spec §5)

        return {
            "task_id": request.task_id,
            "stage": STAGE_READY,
            "device": device,
            "artifact_id": persisted["artifact_id"],
            "memory_id": memory_id,
            "synthesis_provider": report.get("synthesis_provider"),
            "findings_count": len(report.get("findings", [])),
            "sources_count": len(report.get("sources", [])),
        }

    async def _discover_with_handoff(
        self,
        request: "BrowserResearchRequest",
        device_id: str,
        query_id: str,
        query_text: str,
        source_class: str,
        window_start_iso: str,
    ) -> None:
        """One query's discovery, absorbing the owner-handoff loop (spec §5a).

        Unattended (``request.interactive`` False): a single ``discover_activity``
        call with ``interstitial="fallback"`` — the device never hands back a
        waiting result under fallback, so this is exactly the old behaviour.

        Interactive: the first call uses ``interstitial="handoff"``. If the
        device answers ``status="waiting"`` (a Google interstitial was shown
        and Chrome was brought to the front), this loops
        ``await_verification_activity`` in <=60s slices — each one heartbeats
        and is a fresh idempotency key, never a retry of the same wait —
        until the owner clears it or ``interactive_wait_s`` is spent. Cleared
        -> re-issue the SAME search (still interstitial="handoff"; the worker
        resumes on the already-loaded page, path=handoff_cleared) and, if
        THAT also comes back waiting (a second interstitial), the loop
        continues with whatever budget is left. Budget spent -> one final
        attempt with interstitial="fallback" (path=handoff_timeout_fallback),
        whose outcome is accepted either way — one query/class failing (or
        never getting past its interstitial) must not fail the whole run.
        """
        interstitial = "handoff" if request.interactive else "fallback"
        budget_s = request.interactive_wait_s
        iteration = 0
        clearances = 0
        while True:
            try:
                outcome = await workflow.execute_activity(
                    discover_activity,
                    args=[
                        request.task_id,
                        device_id,
                        query_id,
                        query_text,
                        source_class,
                        window_start_iso,
                        interstitial,
                        request.search_provider,
                    ],
                    start_to_close_timeout=_MEDIUM,
                    retry_policy=_STANDARD_RETRY,
                )
            except ActivityError:
                return  # one query/class failing must not fail the whole run

            if not request.interactive or outcome.get("status") != "waiting":
                return

            if clearances >= 1:
                # Retry once, never loop (contract §3a): the owner already cleared one
                # verification for this query and Google asked again. Recorded by the
                # worker's attempt evidence; one final attempt on the fallback provider.
                await self._fallback_after_handoff(
                    request, device_id, query_id, query_text, source_class, window_start_iso
                )
                return

            cleared = False
            while budget_s > 0:
                wait_s = min(_VERIFICATION_POLL_S, budget_s)
                try:
                    wait_result = await workflow.execute_activity(
                        await_verification_activity,
                        args=[request.task_id, device_id, wait_s, iteration],
                        start_to_close_timeout=timedelta(seconds=wait_s + 15),
                        heartbeat_timeout=timedelta(seconds=20),
                        retry_policy=_NO_RETRY,
                    )
                except ActivityError:
                    wait_result = {"satisfied": False}
                iteration += 1
                budget_s -= wait_s
                if wait_result.get("satisfied"):
                    cleared = True
                    break

            if not cleared:
                if request.on_verification_timeout == "fail":
                    raise OwnerVerificationTimeout(
                        "Google'ın doğrulama sayfası "
                        f"{request.interactive_wait_s} saniye içinde tamamlanmadı; bu araştırma "
                        "isteğin gereği yedek sağlayıcıya geçmeden durduruldu. Hazır olduğunuzda "
                        "etkileşimli olarak yeniden başlatın veya gözetimsiz modda çalıştırın."
                    )
                # Budget spent: one last attempt on the fallback provider,
                # then stop regardless of its outcome (the worker never hands
                # off again under interstitial="fallback").
                await self._fallback_after_handoff(
                    request, device_id, query_id, query_text, source_class, window_start_iso
                )
                return

            clearances += 1
            interstitial = "handoff"
            # loop back once and re-discover on the resumed page (path=handoff_cleared)

    async def _fallback_after_handoff(
        self,
        request: "BrowserResearchRequest",
        device_id: str,
        query_id: str,
        query_text: str,
        source_class: str,
        window_start_iso: str,
    ) -> None:
        """One attempt with interstitial="fallback" on the still-pending query: the
        worker records the interstitial and goes to the next provider WITHOUT
        attempting Google again (path=handoff_timeout_fallback); its outcome is
        accepted either way."""
        try:
            await workflow.execute_activity(
                discover_activity,
                args=[
                    request.task_id,
                    device_id,
                    query_id,
                    query_text,
                    source_class,
                    window_start_iso,
                    "fallback",
                    request.search_provider,
                ],
                start_to_close_timeout=_MEDIUM,
                retry_policy=_STANDARD_RETRY,
            )
        except ActivityError:
            pass

    def _error_detail(self, exc: ActivityError) -> str:
        cause = exc.cause
        if isinstance(cause, ApplicationError):
            return str(cause.message)
        return str(exc)

    def _error_class(self, exc: ActivityError) -> str:
        cause = exc.cause
        if isinstance(cause, ApplicationError) and cause.type:
            return str(cause.type)
        return "research_failed"

    async def _fetch_one(self, task_id: str, device_id: str, target: dict) -> None:
        try:
            await workflow.execute_activity(
                fetch_activity,
                args=[
                    task_id,
                    device_id,
                    target["url"],
                    target["query"],
                    target["source_class"],
                ],
                start_to_close_timeout=_FETCH_TIMEOUT,
                heartbeat_timeout=timedelta(seconds=20),
                retry_policy=_FETCH_RETRY,
            )
        except ActivityError:
            pass  # recorded as a fetch failure; one bad URL never fails the run

    async def _fetch_all(
        self, task_id: str, device_id: str, targets: list, policy: ResearchPolicy | None = None
    ) -> None:
        """Fetch this wave's targets with up to ``policy.concurrent_fetches`` (owner
        rule 2) in flight at once — chunked rather than a single unbounded
        ``asyncio.gather`` so the device is never asked for more concurrent
        sessions/tabs than the mode's own policy allows. One bad URL never fails
        the run (each fetch's own ActivityError is swallowed in ``_fetch_one``, so
        one slow/failed activity in a chunk never cancels its siblings)."""
        concurrency = max(1, (policy or ResearchPolicy.from_dict({})).concurrent_fetches)
        for start in range(0, len(targets), concurrency):
            chunk = targets[start : start + concurrency]
            await asyncio.gather(*(self._fetch_one(task_id, device_id, t) for t in chunk))

    def _failed(self, task_id: str, plan: dict, detail: str, error_class: str) -> dict:
        return {
            "task_id": task_id,
            "stage": STAGE_FAILED,
            "device": None,
            "error": {"error_class": error_class, "detail": detail},
            "artifact_id": None,
            "memory_id": None,
        }


__all__ = ["BrowserResearchRequest", "BrowserResearchWorkflow"]
