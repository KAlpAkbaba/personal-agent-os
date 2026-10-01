"""PLAN, by a model (ADR-0207 PR-C): ONE Messages API request, ONE forced ``step`` call.

``ModelPlanner`` stands behind the same ``TaskPlanner`` interface as the rule table and
the scripted fake, so the loop does not know it is there. Everything it is given and
everything accepted back was built and tested in ``app.webtask.planner`` before any model
existed: ``build_prompt`` writes the blocks, ``STEP_TOOL`` is the one flat tool the model
is forced to call, ``parse_step`` is what its arguments must survive. This module adds
the HTTP request and nothing else - no budget, no retry policy of its own beyond one
breath on "busy", no judgement about whether the step may run (that is the gate's).

Boundaries, on purpose:

* The user message is GOAL, ELEMENTS, HISTORY, PAGE, in that order. The goal is the
  first thing the model reads and the only instruction; the page excerpt is the LAST
  block and exists only inside the untrusted-content wrapper.
* The model is chosen by what the round needs, not by the task: the cheap model, and the
  capable one for the round after a step that did not hold (``request.capable`` - the
  loop sets it; it is the re-plan rule and there is no other).
* Whatever goes wrong - a transport failure, a timeout, a status, an answer in words, a
  step that does not parse - is a ``PlannerError`` whose message is built from fixed
  words and numbers. It never carries the model's text, a vendor's error message or a
  key the model invented: any of those can be a sentence a page wrote.
* Raw HTTP through an injectable ``send`` (the shape of
  ``app.assistant_chat.AnthropicChatProvider``): no SDK, and a test never touches the
  network.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Final

import httpx

from app.logging import get_logger
from app.webtask.planner import STEP_TOOL, PlannerError, PlanRequest, build_prompt, parse_step
from app.webtask.types import Step

logger = get_logger("app.webtask.model_planner")

ANTHROPIC_VERSION: Final = "2023-06-01"
#: ONE flat tool call: an action, a reference, a short why. The cap is a cost bound.
MAX_TOKENS: Final = 600
#: Well inside the round activity's 150 s heartbeat timeout, twice over with the retry.
REQUEST_TIMEOUT_S: Final = 30.0
#: 429 (rate limit) and 529 (overloaded) are asked once more after a breath; nothing else is.
RETRYABLE_STATUS: Final[frozenset[int]] = frozenset({429, 529})
RETRY_DELAY_S: Final = 1.5

#: The user message's blocks, in the order the model reads them. PAGE is last.
BLOCKS: Final[tuple[tuple[str, str], ...]] = (
    ("GOAL", "goal"),
    ("ELEMENTS", "elements"),
    ("HISTORY", "history"),
    ("PAGE", "page"),
)

_TOOL_KEYS: Final = frozenset(STEP_TOOL["input_schema"]["properties"])

SendFn = Callable[[str, dict[str, str], dict[str, Any], float], tuple[int, dict[str, Any]]]


def _http_send(url: str, headers: dict[str, str], body: dict[str, Any], timeout_s: float):
    response = httpx.post(url, headers=headers, json=body, timeout=timeout_s)
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload if isinstance(payload, dict) else {}


class ModelPlanner:
    """One request per plan; the answer is a ``Step`` or a ``PlannerError``."""

    name = "model"

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        capable_model: str,
        base_url: str = "https://api.anthropic.com",
        timeout_s: float = REQUEST_TIMEOUT_S,
        send: SendFn | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key or ""
        self._model = model
        self._capable_model = capable_model or model
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._send = send or _http_send
        self._sleep = sleep

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def request(self, request: PlanRequest) -> tuple[str, dict[str, str], dict[str, Any]]:
        prompt = build_prompt(request)
        body = {
            "model": self._capable_model if request.capable else self._model,
            "max_tokens": MAX_TOKENS,
            "temperature": 0,
            "system": prompt["system"],
            "messages": [
                {
                    "role": "user",
                    "content": "\n\n".join(f"{label}\n{prompt[key]}" for label, key in BLOCKS),
                }
            ],
            "tools": [STEP_TOOL],
            "tool_choice": {"type": "tool", "name": STEP_TOOL["name"]},
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        return f"{self._base_url}/v1/messages", headers, body

    def plan(self, request: PlanRequest) -> Step | None:
        if not self.configured:
            raise PlannerError("no model key is configured")
        url, headers, body = self.request(request)
        model = str(body["model"])
        status, payload = 0, {}
        for attempt in (1, 2):
            try:
                status, payload = self._send(url, headers, body, self._timeout_s)
            except httpx.HTTPError as exc:
                # The class only: a transport error's text can quote what was sent.
                logger.warning("webtask_planner_transport_failed", error=type(exc).__name__)
                raise PlannerError(
                    f"the model could not be reached ({type(exc).__name__})"
                ) from None
            if status in RETRYABLE_STATUS and attempt == 1:
                self._sleep(RETRY_DELAY_S)
                continue
            break
        if status != 200:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            logger.warning(
                "webtask_planner_vendor_error",
                status=status,
                model=model,
                error_type=str(error.get("type"))[:60],
            )
            if status in RETRYABLE_STATUS:
                raise PlannerError(f"the model is busy (status {status}, asked twice)")
            raise PlannerError(f"the model answered status {status}")

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        logger.info(
            "webtask_planner_answered",
            model=model,
            capable=request.capable,
            stop_reason=str(payload.get("stop_reason"))[:32],
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
        )
        if payload.get("stop_reason") == "max_tokens":
            raise PlannerError("the model's answer was cut off before the step was whole")
        content = payload.get("content")
        calls = [
            block
            for block in (content if isinstance(content, list) else ())
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if len(calls) != 1 or calls[0].get("name") != STEP_TOOL["name"]:
            # Words, a refusal, another tool, two steps: none of it is repeated here.
            raise PlannerError(f"the model did not answer with one step ({len(calls)} tool calls)")
        arguments = calls[0].get("input")
        try:
            return parse_step(arguments)
        except PlannerError as exc:
            # parse_step names a key the MODEL wrote when it is one the tool does not
            # have. A model that read a hostile page can write anything there.
            if isinstance(arguments, dict) and set(arguments) - _TOOL_KEYS:
                raise PlannerError("the model's step carries keys the tool does not have") from None
            raise PlannerError(f"the model's step was not accepted: {exc}") from None


__all__ = [
    "BLOCKS",
    "MAX_TOKENS",
    "REQUEST_TIMEOUT_S",
    "ModelPlanner",
    "SendFn",
]
