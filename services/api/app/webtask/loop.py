"""The loop: ONE round, over ports (ADR-0207 a).

``run_round(state, ports)`` observes, plans, gates, acts and verifies once, and returns
the state as it is afterwards. It is a function of its arguments: the browser, the
planner and the clock are ports, so the acceptance tasks run here against a fake browser
and a scripted planner exactly as they will run against Chrome and a model.

A round is the unit of durability: the workflow runs one activity per round and the
state is written to the row after each. A process that dies between two rounds resumes
by OBSERVING AGAIN - never by replaying an action.

Budgets and stops, all constants, all in the trail:

* ``MAX_ROUNDS`` rounds; ``MAX_ACTIVE_SECONDS`` of time spent working - waiting for the
  owner is not counted, and a wait does not advance the round counter;
* ``MAX_FAILED_STREAK`` rounds in a row that did not hold;
* the same state twice with the same step is a loop. The loop never clicks its way
  around a wall.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from app.webtask import gate, verify
from app.webtask.planner import PlannerError, PlanRequest, TaskPlanner
from app.webtask.sites import site_of
from app.webtask.types import (
    ACTION_FILL,
    ACTION_SELECT,
    ASK_CANNOT_SEE,
    ASK_CHALLENGE,
    ASK_CONFIRM,
    ASK_LOGIN,
    ASK_QUESTION,
    FAIL_BROWSER,
    FAIL_LOOP,
    FAIL_PLANNER,
    FAIL_ROUND_BUDGET,
    FAIL_STREAK,
    FAIL_TIME_BUDGET,
    NEEDS_REF,
    ROUND_ACT_FAILED,
    ROUND_ACTED,
    ROUND_ASKED,
    ROUND_DONE,
    ROUND_REFUSED,
    ROUND_VERIFY_FAILED,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WAITING_OWNER,
    TERMINAL,
    Element,
    Observation,
    Pending,
    Round,
    Step,
)

MAX_ROUNDS: Final = 25
MAX_ACTIVE_SECONDS: Final = 8 * 60
MAX_FAILED_STREAK: Final = 3
#: The same state with the same step this many times is a loop.
LOOP_SAME_STEP: Final = 2
#: The same state this many times, whatever the step, is a loop too.
LOOP_SAME_STATE: Final = 3
MAX_TRAIL: Final = 60
#: ``app.webtask.model_planner.ModelPlanner.name``: a round it answered is a paid call.
PLANNER_MODEL: Final = "model"

PAGE_AUTH_WALL: Final = "auth_wall"
PAGE_CAPTCHA: Final = "captcha"

ERROR_UI_STATE_CHANGED: Final = "ui_state_changed"
ERROR_SECURITY_SCOPE: Final = "security_scope_error"
ERROR_TARGET_NOT_FOUND: Final = "ui_target_not_found"
#: ``ui_state_changed`` reasons that mean "I cannot tell which element" rather than "the
#: page moved on": the first is what an ambiguous path inside a shadow root looks like.
_CANNOT_SEE_REASONS: Final = frozenset({"not_unique"})


class BrowserPortError(Exception):
    """A typed browser failure, as the device reports it. Never carries page text."""

    def __init__(
        self, error_class: str, message: str = "", *, retryable: bool = False, reason: str = ""
    ) -> None:
        super().__init__(message or error_class)
        self.error_class = error_class
        self.retryable = retryable
        self.reason = reason


class BrowserPort(Protocol):
    def observe(self, *, task_id: str, key: str) -> Observation: ...

    def act(
        self, *, task_id: str, key: str, step: Step, observation_id: str, risk_ceiling: str
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Ports:
    browser: BrowserPort
    planner: TaskPlanner
    #: Monotonic seconds. The loop measures the time IT spends, never the wall clock.
    clock: Callable[[], float]


@dataclass(slots=True)
class TaskState:
    task_id: str
    goal: str
    status: str = STATUS_RUNNING
    round_index: int = 0
    rounds: list[Round] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=list)
    pending: Pending | None = None
    #: The owner's confirmation of ``pending.step``, already judged by the gate of
    #: ``app.actions.confirmation_gate`` in the service layer: ``{"source": ...}``.
    grant: dict[str, Any] | None = None
    failure: str = ""
    message: str = ""
    active_seconds: float = 0.0
    failed_streak: int = 0
    hint: str = ""
    #: state key -> how often it was seen; "<state key>|<step digest>" -> how often acted.
    seen: dict[str, int] = field(default_factory=dict)
    #: The observation the last act ended with. It is the next round's first - unless the
    #: loop waited for the owner, who may have changed the page: then it is observed anew.
    observation: Observation | None = None
    observation_fresh: bool = False
    #: How many device commands were issued, for idempotency keys that never repeat.
    commands: int = 0
    #: Where the task runs (``app.webtask.types.TARGET_*``); "" = the owner's Chrome, as
    #: before targets. The gate reads it: in the cloud a write needs the owner's list.
    target: str = ""
    #: Rounds a planner was asked for a step, and how many of them the MODEL answered -
    #: the measure of what a task costs (the rules cost nothing).
    planner_calls: int = 0
    planner_model_calls: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status,
            "round_index": self.round_index,
            "rounds": [r.as_dict() for r in self.rounds],
            "answers": list(self.answers),
            "allowed_hosts": list(self.allowed_hosts),
            "pending": self.pending.as_dict() if self.pending else None,
            "grant": dict(self.grant) if self.grant else None,
            "failure": self.failure,
            "message": self.message,
            "active_seconds": round(self.active_seconds, 3),
            "failed_streak": self.failed_streak,
            "hint": self.hint,
            "seen": dict(self.seen),
            "observation": self.observation.as_dict() if self.observation else None,
            "observation_fresh": self.observation_fresh,
            "commands": self.commands,
            "target": self.target,
            "planner_calls": self.planner_calls,
            "planner_model_calls": self.planner_model_calls,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> TaskState:
        observation = raw.get("observation")
        return cls(
            task_id=str(raw.get("task_id") or ""),
            goal=str(raw.get("goal") or ""),
            status=str(raw.get("status") or STATUS_RUNNING),
            round_index=int(raw.get("round_index") or 0),
            rounds=[Round.from_dict(r) for r in raw.get("rounds") or []],
            answers=[str(a) for a in raw.get("answers") or []],
            allowed_hosts=[str(h) for h in raw.get("allowed_hosts") or []],
            pending=Pending.from_dict(raw.get("pending")),
            grant=dict(raw["grant"]) if raw.get("grant") else None,
            failure=str(raw.get("failure") or ""),
            message=str(raw.get("message") or ""),
            active_seconds=float(raw.get("active_seconds") or 0.0),
            failed_streak=int(raw.get("failed_streak") or 0),
            hint=str(raw.get("hint") or ""),
            seen={str(k): int(v) for k, v in (raw.get("seen") or {}).items()},
            observation=Observation.from_result(observation) if observation else None,
            observation_fresh=bool(raw.get("observation_fresh")),
            commands=int(raw.get("commands") or 0),
            target=str(raw.get("target") or ""),
            planner_calls=int(raw.get("planner_calls") or 0),
            planner_model_calls=int(raw.get("planner_model_calls") or 0),
        )


# ------------------------------------------------------------------ helpers


def url_shape(url: str) -> str:
    """The address without the VALUES of its query: ``?q=kulaklik&page=2`` and
    ``?q=hoparlor&page=2`` are the same place for "have I been here", and a session id
    in the query must not make every visit look new."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    keys = sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)})
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, "&".join(keys), ""))


def state_key(observation: Observation) -> str:
    return f"{url_shape(observation.url)}#{observation.digest()}"


def _key(state: TaskState, what: str) -> str:
    state.commands += 1
    return f"webtask:{state.task_id}:{state.round_index}:{state.commands}:{what}"


def _record(state: TaskState, entry: Round) -> None:
    state.rounds.append(entry)
    if len(state.rounds) > MAX_TRAIL:
        del state.rounds[: len(state.rounds) - MAX_TRAIL]


def _fail(state: TaskState, reason: str, message: str) -> TaskState:
    state.status = STATUS_FAILED
    state.failure = reason
    state.message = message
    state.pending = None
    state.grant = None
    return state


def _ask(state: TaskState, observation: Observation, pending: Pending) -> TaskState:
    pending.asked_round = state.round_index
    state.status = STATUS_WAITING_OWNER
    state.pending = pending
    state.grant = None
    state.message = pending.message
    # The owner may change the page while the loop waits: what it holds is stale.
    state.observation_fresh = False
    _record(
        state,
        Round(
            index=state.round_index,
            site=site_of(observation.url),
            action="ask_owner",
            element=(pending.step and pending.facts.get("element")) or "",
            risk=pending.risk,
            outcome=ROUND_ASKED,
            detail=pending.kind,
            flagged=observation.flagged,
        ),
    )
    return state


def _rebind(step: Step, facts: dict[str, Any], observation: Observation) -> Step | None:
    """The confirmed step in a NEW observation: the element that is what the read-back
    said it was. None, or two, is not the element - the loop does not pick one."""
    if step.action not in NEEDS_REF:
        return step
    wanted = (str(facts.get("role") or ""), str(facts.get("element") or ""))
    matches = [e for e in observation.elements if (e.role, e.name) == wanted]
    if len(matches) != 1:
        return None
    return Step.from_dict({**step.as_dict(), "ref": matches[0].ref})


def _value_chars(step: Step) -> int | None:
    if step.action in (ACTION_FILL, ACTION_SELECT) and step.value is not None:
        return len(step.value)
    return None


_WHITESPACE = re.compile(r"\s+")


def _short(text: str, limit: int = 160) -> str:
    return _WHITESPACE.sub(" ", text or "").strip()[:limit]


# ------------------------------------------------------------------ the round


def run_round(state: TaskState, ports: Ports) -> TaskState:
    """One round. Returns ``state``, changed. Never raises for what a browser, a planner
    or a page can do: those are outcomes, written on the state."""
    if state.status != STATUS_RUNNING:
        return state
    if state.round_index >= MAX_ROUNDS:
        return _fail(state, FAIL_ROUND_BUDGET, f"{MAX_ROUNDS} turda bitiremedim.")
    if state.active_seconds >= MAX_ACTIVE_SECONDS:
        return _fail(state, FAIL_TIME_BUDGET, "Ayrılan sürede bitiremedim.")

    started = ports.clock()
    try:
        return _round(state, ports)
    finally:
        state.active_seconds += max(0.0, ports.clock() - started)


def _observe(state: TaskState, ports: Ports) -> Observation | None:
    if state.observation is not None and state.observation_fresh:
        return state.observation
    try:
        observation = ports.browser.observe(task_id=state.task_id, key=_key(state, "observe"))
    except BrowserPortError as exc:
        _fail(state, FAIL_BROWSER, f"Tarayıcıya ulaşamadım ({exc.error_class}).")
        return None
    state.observation = observation
    state.observation_fresh = True
    return observation


def _round(state: TaskState, ports: Ports) -> TaskState:
    observation = _observe(state, ports)
    if observation is None:
        return state

    # Decision 6: an auth wall is the owner's to pass. Nothing is planned on it.
    if observation.page_kind == PAGE_AUTH_WALL:
        return _ask(
            state,
            observation,
            Pending(
                kind=ASK_LOGIN,
                message=(
                    f"{site_of(observation.url)} giriş istiyor. Giriş yapın, "
                    "sonra 'devam' deyin; kaldığım turdan sürdürürüm."
                ),
            ),
        )
    if observation.page_kind == PAGE_CAPTCHA:
        return _ask(
            state,
            observation,
            Pending(
                kind=ASK_CHALLENGE,
                message=(
                    f"{site_of(observation.url)} bir doğrulama gösteriyor. Onu siz geçin, "
                    "sonra 'devam' deyin."
                ),
            ),
        )

    here = state_key(observation)
    context = gate.TaskContext(
        goal=state.goal,
        answers=tuple(state.answers),
        allowed_hosts=tuple(state.allowed_hosts),
        grant=None,
        target=state.target,
    )

    planner_name = ""
    confirmed = state.pending if state.pending and state.pending.kind == ASK_CONFIRM else None
    if confirmed is not None and state.grant is not None and confirmed.step is not None:
        step = _rebind(confirmed.step, confirmed.facts, observation)
        if step is None:
            state.pending = None
            return _ask(
                state,
                observation,
                Pending(
                    kind=ASK_CANNOT_SEE,
                    message=(
                        "Onayladığınız öğeyi artık bu sayfada göremiyorum; sayfa değişmiş. "
                        "Hiçbir şeye basmadım."
                    ),
                ),
            )
        context = gate.TaskContext(
            goal=state.goal,
            answers=tuple(state.answers),
            allowed_hosts=tuple(state.allowed_hosts),
            grant=gate.Grant(
                step_digest=confirmed.step_digest,
                source=str(state.grant.get("source") or ""),
                facts=dict(confirmed.facts),
            ),
            target=state.target,
        )
        planner_name = "confirmed"
    else:
        state.planner_calls += 1
        try:
            planned = ports.planner.plan(
                PlanRequest(
                    goal=state.goal,
                    observation=observation,
                    history=tuple(state.rounds),
                    answers=tuple(state.answers),
                    hint=state.hint,
                    capable=bool(state.hint),
                )
            )
        except PlannerError as exc:
            return _fail(state, FAIL_PLANNER, f"Planlayıcı yanıt vermedi ({_short(str(exc))}).")
        if planned is None:
            return _fail(state, FAIL_PLANNER, "Bu tur için bir adım planlanamadı.")
        step = planned
        planner_name = str(
            getattr(ports.planner, "last_used", "") or getattr(ports.planner, "name", "")
        )
        if planner_name == PLANNER_MODEL:
            state.planner_model_calls += 1

    element = observation.by_ref(step.ref) if step.action in NEEDS_REF else None
    decision = gate.decide(step, observation, context)
    # A grant opens ONE step ONCE, whatever the gate made of it.
    state.grant = None
    state.pending = None

    def entry(outcome: str, **kwargs: Any) -> Round:
        return Round(
            index=state.round_index,
            site=site_of(observation.url),
            action=step.action,
            element=element.name if element else "",
            role=element.role if element else "",
            risk=decision.risk,
            outcome=outcome,
            value_chars=_value_chars(step),
            planner=planner_name,
            flagged=observation.flagged,
            state_key=here,
            **kwargs,
        )

    if decision.kind == gate.DECISION_DONE:
        state.status = STATUS_DONE
        state.message = _short(decision.message, 1_200)
        _record(state, entry(ROUND_DONE))
        return state

    if decision.kind == gate.DECISION_ASK:
        pending = Pending(kind=decision.ask_kind, message=decision.message, risk=decision.risk)
        if decision.ask_kind == ASK_CONFIRM:
            pending.step = step
            pending.step_digest = step.digest(element)
            pending.facts = dict(decision.facts)
        return _ask(state, observation, pending)

    if decision.kind == gate.DECISION_REFUSE:
        state.failed_streak += 1
        state.hint = f"the step was refused: {decision.reason}"
        if decision.message:
            # A refusal the owner can lift himself (a site off his cloud allow-list) is
            # said to him; the planner hears the reason and plans again or ends honestly.
            state.message = decision.message
        _record(state, entry(ROUND_REFUSED, detail=decision.reason))
        state.round_index += 1
        return _streak(state)

    # ---- ALLOW. Have I been here, about to do this?
    acted_key = f"{here}|{step.digest(element)}"
    state.seen[here] = state.seen.get(here, 0) + 1
    state.seen[acted_key] = state.seen.get(acted_key, 0) + 1
    if state.seen[acted_key] >= LOOP_SAME_STEP or state.seen[here] >= LOOP_SAME_STATE:
        _record(state, entry(ROUND_REFUSED, detail=FAIL_LOOP))
        return _fail(
            state, FAIL_LOOP, "Aynı sayfada aynı adımı yineliyorum; ilerleyemiyorum, durdum."
        )

    try:
        ports.browser.act(
            task_id=state.task_id,
            key=_key(state, step.action),
            step=step,
            observation_id=observation.observation_id,
            risk_ceiling=decision.risk_ceiling,
        )
    except BrowserPortError as exc:
        state.observation_fresh = False
        if exc.error_class == ERROR_UI_STATE_CHANGED and exc.reason in _CANNOT_SEE_REASONS:
            return _ask(
                state,
                observation,
                Pending(
                    kind=ASK_CANNOT_SEE,
                    message=(
                        "Hangi öğe olduğunu ayırt edemiyorum; gölge DOM ya da çerçeve içinde "
                        "olabilir. Tahmin etmiyorum."
                    ),
                ),
            )
        if exc.error_class == ERROR_SECURITY_SCOPE:
            # The device classified the step HIGHER than it was gated at. Never retried.
            return _ask(
                state,
                observation,
                Pending(
                    kind=ASK_QUESTION,
                    risk=decision.risk,
                    message=(
                        "Cihaz bu adımı benim değerlendirdiğimden daha riskli buldu ve "
                        "yapmadı. Nasıl devam edeyim?"
                    ),
                ),
            )
        state.failed_streak += 1
        state.hint = f"the act failed: {exc.error_class}"
        _record(
            state,
            entry(
                ROUND_ACT_FAILED,
                detail=exc.error_class + (f":{exc.reason}" if exc.reason else ""),
                confirmed_by=decision.confirmed_by,
            ),
        )
        state.round_index += 1
        return _streak(state)

    # ---- VERIFY: a second, independent read. It is also the next round's first.
    try:
        after = ports.browser.observe(task_id=state.task_id, key=_key(state, "verify"))
    except BrowserPortError as exc:
        state.observation_fresh = False
        _record(state, entry(ROUND_ACTED, verified=None, detail=f"unverified:{exc.error_class}"))
        return _fail(state, FAIL_BROWSER, "Adımı attım ama sonucunu okuyamadım.")
    state.observation = after
    state.observation_fresh = True
    assert step.expect is not None  # the gate refuses an acting step without one
    verdict = verify.check(step.expect, observation, after)
    if verdict.ok:
        state.failed_streak = 0
        state.hint = ""
        outcome = ROUND_ACTED
    else:
        state.failed_streak += 1
        state.hint = f"expected {step.expect.kind}: {verdict.detail}"
        outcome = ROUND_VERIFY_FAILED
    _record(
        state,
        entry(
            outcome,
            verified=verdict.ok,
            detail=_short(verdict.detail),
            confirmed_by=decision.confirmed_by,
        ),
    )
    state.round_index += 1
    return _streak(state)


def _streak(state: TaskState) -> TaskState:
    if state.failed_streak >= MAX_FAILED_STREAK:
        return _fail(state, FAIL_STREAK, f"Art arda {MAX_FAILED_STREAK} adım tutmadı; durdum.")
    return state


# ------------------------------------------------------------------ the owner's words


class OwnerWordError(Exception):
    """The owner's word does not apply to the state the task is in."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def continue_(state: TaskState, answer: str = "") -> TaskState:
    """ "devam": the owner did what was asked (signed in, passed a challenge, typed a
    field, answered a question). The loop takes up THE SAME ROUND and observes again."""
    if state.status != STATUS_WAITING_OWNER or state.pending is None:
        raise OwnerWordError("not_waiting")
    if state.pending.kind == ASK_CONFIRM:
        # "devam" is not a confirmation: an irreversible step needs the owner's word
        # FOR THAT STEP, through confirm().
        raise OwnerWordError("confirmation_required")
    if answer.strip():
        state.answers.append(answer.strip())
    state.pending = None
    state.grant = None
    state.status = STATUS_RUNNING
    state.message = ""
    state.observation_fresh = False
    return state


def confirm(state: TaskState, *, source: str) -> TaskState:
    """The owner's word for the step that was read back. The caller has ALREADY judged
    the claim (same session, a later turn, the router's own resolution)."""
    if state.status != STATUS_WAITING_OWNER or state.pending is None:
        raise OwnerWordError("not_waiting")
    if state.pending.kind != ASK_CONFIRM or state.pending.step is None:
        # A payment, a login, a denied site: nothing here is opened by a word.
        raise OwnerWordError("nothing_to_confirm")
    state.grant = {"source": source, "step_digest": state.pending.step_digest}
    state.status = STATUS_RUNNING
    state.message = ""
    state.observation_fresh = False
    return state


def decline(state: TaskState) -> TaskState:
    """ "hayır": the read-back was refused. The step is dropped and the planner is told."""
    if state.status != STATUS_WAITING_OWNER or state.pending is None:
        raise OwnerWordError("not_waiting")
    state.answers.append("Sahip bu adımı onaylamadı; o adımı atma.")
    state.pending = None
    state.grant = None
    state.status = STATUS_RUNNING
    state.message = ""
    state.observation_fresh = False
    return state


def cancel(state: TaskState) -> TaskState:
    if state.status in TERMINAL:
        return state
    state.status = STATUS_CANCELLED
    state.pending = None
    state.grant = None
    state.message = "İptal edildi."
    return state


def element_of(state: TaskState, step: Step) -> Element | None:
    return state.observation.by_ref(step.ref) if state.observation else None


__all__ = [
    "LOOP_SAME_STATE",
    "LOOP_SAME_STEP",
    "MAX_ACTIVE_SECONDS",
    "MAX_FAILED_STREAK",
    "MAX_ROUNDS",
    "BrowserPort",
    "BrowserPortError",
    "OwnerWordError",
    "Ports",
    "TaskState",
    "cancel",
    "confirm",
    "continue_",
    "decline",
    "run_round",
    "state_key",
    "url_shape",
]
