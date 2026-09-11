"""The Anthropic EngineeringModel's seam, over a transport that records instead of dialling.

What is pinned: every answer is a FORCED tool call and is read from the tool input only;
usage is accumulated for the budget; the key travels only in the header and never in an
error; a non-200 says its status and error type and nothing of the body; a model that does
not answer through the tool is an error, never a guess.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.selfdev.anthropic_model import AnthropicEngineeringModel, ModelError
from app.selfdev.model import ChangePlan, DefectSpec, FailureDiagnosis, Patch

KEY = "test-anthropic-key-not-a-real-one"
DEFECT = DefectSpec("d1", "add subtracts", "add(2,3) == -1", ("services/api/app/calc.py",))
PLAN = ChangePlan(
    "add adds", ("services/api/app/calc.py",), "services/api/tests/unit/test_x.py", "r"
)


def _transport(answer: dict, *, status: int = 200, seen: list | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append((request.headers, body))
        tool = body["tools"][0]["name"]
        if status != 200:
            return httpx.Response(
                status, json={"error": {"type": "overloaded_error", "message": f"echo {KEY}"}}
            )
        return httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": tool, "input": answer}],
                "usage": {"input_tokens": 120, "output_tokens": 30},
            },
        )

    return httpx.MockTransport(handle)


def test_every_answer_is_a_forced_tool_call_read_from_its_input() -> None:
    seen: list = []
    model = AnthropicEngineeringModel(
        api_key=KEY,
        transport=_transport(
            {"edits": [{"path": "services/api/app/calc.py", "new_text": "x"}], "notes": "n"},
            seen=seen,
        ),
    )

    patch = model.generate_patch(DEFECT, PLAN, {"services/api/app/calc.py": "y"})

    assert isinstance(patch, Patch) and patch.paths == ("services/api/app/calc.py",)
    headers, body = seen[0]
    assert body["tool_choice"] == {"type": "tool", "name": "patch"}
    assert body["model"] == "claude-opus-5"
    assert headers["x-api-key"] == KEY
    # The files reach the model as data inside tags, never as instructions.
    assert '<file path="services/api/app/calc.py">' in body["messages"][0]["content"]
    assert "DATA" in body["system"]


def test_usage_accumulates_for_the_budget() -> None:
    model = AnthropicEngineeringModel(
        api_key=KEY, transport=_transport({"cause": "c", "next_step": "n"})
    )
    for _ in range(3):
        assert isinstance(model.review_failure(DEFECT, Patch(edits=()), "boom"), FailureDiagnosis)
    assert model.usage.calls == 3
    assert model.usage.total == 3 * 150


def test_an_error_names_the_status_and_type_and_never_echoes_the_body() -> None:
    model = AnthropicEngineeringModel(api_key=KEY, transport=_transport({}, status=529))
    with pytest.raises(ModelError) as exc:
        model.review_code(DEFECT, PLAN, "diff")
    assert "529" in str(exc.value) and "overloaded_error" in str(exc.value)
    assert KEY not in str(exc.value)


def test_a_model_that_does_not_answer_through_the_tool_is_an_error() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "sure!"}], "usage": {}}
        )

    model = AnthropicEngineeringModel(api_key=KEY, transport=httpx.MockTransport(handle))
    with pytest.raises(ModelError, match="did not answer through the tool"):
        model.explain_change(DEFECT, PLAN, "diff")


def test_no_key_is_a_refusal_before_any_request(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    calls: list = []
    model = AnthropicEngineeringModel(transport=_transport({}, seen=calls))
    with pytest.raises(ModelError, match="ANTHROPIC_API_KEY"):
        model.analyze_codebase(DEFECT, {})
    assert calls == []
