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
    from app.research.contracts import TARGET_REPORT_FINDINGS
    from app.research.models import STAGE_FAILED, STAGE_READY

DEFAULT_MAX_SOURCES = 12
#: How many extra fetch rounds a run may spend when the quality gate leaves it short of
#: TARGET_REPORT_FINDINGS. Bounded on purpose: a run that cannot find enough usable coverage
#: should say so, not keep browsing.
MAX_TOPUP_ROUNDS = 3
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
_FETCH_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=4,
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


class OwnerVerificationTimeout(Exception):
    """Interactive run with on_verification_timeout="fail": the owner did not clear
    Google's page in time and asked not to continue on the fallback provider."""


@workflow.defn
class BrowserResearchWorkflow:
    @workflow.run
    async def run(self, request: BrowserResearchRequest) -> dict:
        plan = await workflow.execute_activity(
            plan_activity,
            args=[request.task_id, request.topic, request.recency_days, request.max_sources],
            start_to_close_timeout=_SHORT,
            retry_policy=_STANDARD_RETRY,
        )

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

        try:
            for source_class in plan["source_classes"]:
                for i, query_text in enumerate(plan["queries"]):
                    query_id = f"{source_class}:{i}"
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
            for activity, args, policy in (
                (
                    fail_run_activity,
                    [request.task_id, "owner_verification_timeout", detail],
                    _STANDARD_RETRY,
                ),
                (close_session_activity, [request.task_id, device_id], _NO_RETRY),
            ):
                try:
                    await workflow.execute_activity(
                        activity, args=args, start_to_close_timeout=_SHORT, retry_policy=policy
                    )
                except ActivityError:
                    pass
            return self._failed(request.task_id, plan, detail, "owner_verification_timeout")

        targets = await workflow.execute_activity(
            fetch_targets_activity,
            args=[request.task_id, request.max_sources, request.topic],
            start_to_close_timeout=_SHORT,
            retry_policy=_STANDARD_RETRY,
        )

        await self._fetch_all(request.task_id, device_id, targets)

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

            # Top-up rounds when the quality gate left too little to answer with. Most
            # fetched pages are refused in practice (2026-09-04: nine of twelve, as off topic,
            # out of window or interstitials), so a fixed budget spent once decides the size of
            # the report by luck. Each round is sized to the shortfall and the number of
            # rounds is fixed, so this is never a loop that keeps fetching until it likes the
            # answer; it stops early when discovery has nothing left, and it only ever fetches
            # candidates that were never fetched, so nothing is duplicated on replay.
            for _round in range(MAX_TOPUP_ROUNDS):
                shortfall = TARGET_REPORT_FINDINGS - int(ranked.get("evidence", 0))
                if shortfall <= 0:
                    break
                extra = await workflow.execute_activity(
                    fetch_targets_activity,
                    args=[
                        request.task_id,
                        min(request.max_sources, max(3, shortfall * 3)),
                        request.topic,
                    ],
                    start_to_close_timeout=_SHORT,
                    retry_policy=_STANDARD_RETRY,
                )
                if not extra:
                    break  # discovery has nothing left to offer
                await self._fetch_all(request.task_id, device_id, extra)
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

            report = await workflow.execute_activity(
                synthesize_activity,
                args=[request.task_id, plan["topic"], plan["recency"], request.synthesis],
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

    async def _fetch_all(self, task_id: str, device_id: str, targets: list) -> None:
        """Fetch every target in order. One bad URL never fails the run."""
        for target in targets:
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
                continue  # recorded as a fetch failure

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
