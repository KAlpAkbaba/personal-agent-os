"""The Anthropic EngineeringModel's seam, over a transport that records instead of dialling.

What is pinned: every answer is a FORCED tool call and is read from the tool input only;
usage is accumulated for the budget; the key travels only in the header and never in an
error; a non-200 says its status and error type and nothing of the body; a model that does
not answer through the tool is an error, never a guess.

And, since the first real run (Phase 10) died on ``TypeError: string indices must be
integers`` with no record written: an answer is READ, never trusted to have its schema's
shape. An array sent as a JSON string is decoded, not iterated a character at a time; an
answer of any other wrong shape is a ModelError naming the field; an answer cut off at
``max_tokens`` is never used; a review's ``"false"`` is not an approval. Changes to existing
files come back as exact replacements, resolved here against the text the model was shown -
and a replacement that does not apply is carried as ``rejected``, never guessed at.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.selfdev.anthropic_model import AnthropicEngineeringModel, ModelError
from app.selfdev.model import ChangePlan, DefectSpec, FailureDiagnosis, FileEdit, Patch

KEY = "test-anthropic-key-not-a-real-one"
CALC = "services/api/app/calc.py"
TEST = "services/api/tests/unit/test_calc_regression.py"
DEFECT = DefectSpec("d1", "add subtracts", "add(2,3) == -1", (CALC,))
PLAN = ChangePlan("add adds", (CALC,), TEST, "r")
BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"
ADD_MINUS = "def add(a, b):\n    return a - b"
ADD_PLUS = "def add(a, b):\n    return a + b"


def _transport(
    answer: dict,
    *,
    status: int = 200,
    seen: list | None = None,
    stop_reason: str = "tool_use",
) -> httpx.MockTransport:
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
                "stop_reason": stop_reason,
                "usage": {"input_tokens": 120, "output_tokens": 30},
            },
        )

    return httpx.MockTransport(handle)


def _model(answer: dict, **kwargs) -> AnthropicEngineeringModel:
    return AnthropicEngineeringModel(api_key=KEY, transport=_transport(answer, **kwargs))


def test_every_answer_is_a_forced_tool_call_read_from_its_input() -> None:
    seen: list = []
    model = _model(
        {
            "replacements": [{"path": CALC, "old_text": ADD_MINUS, "new_text": ADD_PLUS}],
            "new_files": [{"path": TEST, "text": "t"}],
            "notes": "n",
        },
        seen=seen,
    )

    patch = model.generate_patch(DEFECT, PLAN, {CALC: BUGGY})

    assert isinstance(patch, Patch) and patch.paths == (CALC, TEST)
    headers, body = seen[0]
    assert body["tool_choice"] == {"type": "tool", "name": "patch"}
    assert body["model"] == "claude-opus-5"
    assert headers["x-api-key"] == KEY
    # The files reach the model as data inside tags, never as instructions.
    assert f'<file path="{CALC}">' in body["messages"][0]["content"]
    assert "DATA" in body["system"]


def test_usage_accumulates_for_the_budget() -> None:
    model = _model({"cause": "c", "next_step": "n"})
    for _ in range(3):
        assert isinstance(model.review_failure(DEFECT, Patch(edits=()), "boom"), FailureDiagnosis)
    assert model.usage.calls == 3
    assert model.usage.total == 3 * 150


def test_an_error_names_the_status_and_type_and_never_echoes_the_body() -> None:
    model = _model({}, status=529)
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


# ------------------------------------------------------------ reading, not trusting, an answer


def test_an_array_sent_as_a_json_string_is_decoded_not_iterated_a_character_at_a_time() -> None:
    """The Phase 10 crash: the patch's array arrived as a JSON string."""
    model = _model(
        {
            "replacements": json.dumps(
                [{"path": CALC, "old_text": ADD_MINUS, "new_text": ADD_PLUS}]
            ),
            "new_files": json.dumps([{"path": TEST, "text": "def test_add(): ...\n"}]),
        }
    )

    patch = model.generate_patch(DEFECT, PLAN, {CALC: BUGGY})

    assert patch.rejected == ()
    assert {e.path: e.new_text for e in patch.edits} == {
        CALC: BUGGY.replace(ADD_MINUS, ADD_PLUS, 1),
        TEST: "def test_add(): ...\n",
    }


@pytest.mark.parametrize(
    ("answer", "field"),
    [
        ({"replacements": ["just a string"], "new_files": []}, "replacements"),
        ({"replacements": [], "new_files": [{"path": TEST}]}, "text"),
        ({"replacements": "not json at all", "new_files": []}, "replacements"),
        ({"replacements": [{"path": 7, "old_text": "a", "new_text": "b"}]}, "path"),
    ],
)
def test_an_answer_of_the_wrong_shape_is_a_model_error_naming_the_field(
    answer: dict, field: str
) -> None:
    with pytest.raises(ModelError, match=field):
        _model(answer).generate_patch(DEFECT, PLAN, {CALC: BUGGY})


def test_an_answer_cut_off_at_max_tokens_is_never_used() -> None:
    model = _model(
        {"summary": "half an ans", "relevant_paths": [], "root_cause": "?"},
        stop_reason="max_tokens",
    )
    with pytest.raises(ModelError, match="max_tokens"):
        model.analyze_codebase(DEFECT, {CALC: BUGGY})


def test_a_review_that_says_false_as_a_string_is_not_an_approval() -> None:
    refused = _model({"approved": "false", "findings": ["no test"]})
    assert refused.review_code(DEFECT, PLAN, "diff").approved is False
    approved = _model({"approved": "true", "findings": []})
    assert approved.review_code(DEFECT, PLAN, "diff").approved is True
    with pytest.raises(ModelError, match="approved"):
        _model({"approved": "maybe", "findings": []}).review_code(DEFECT, PLAN, "diff")


# ------------------------------------------------------------------ replacements


def test_replacements_resolve_in_order_against_the_text_the_model_was_shown() -> None:
    model = _model(
        {
            "replacements": [
                {"path": CALC, "old_text": ADD_MINUS, "new_text": ADD_PLUS},
                {"path": CALC, "old_text": "def sub(", "new_text": "def subtract("},
            ],
            "new_files": [{"path": TEST, "text": "t\r\n"}],
        }
    )

    patch = model.generate_patch(DEFECT, PLAN, {CALC: BUGGY})

    assert patch.rejected == ()
    fixed = BUGGY.replace(ADD_MINUS, ADD_PLUS, 1).replace("def sub(", "def subtract(")
    assert patch.edits == (FileEdit(CALC, fixed), FileEdit(TEST, "t\n"))


@pytest.mark.parametrize(
    ("replacement", "why"),
    [
        ({"path": CALC, "old_text": "return a * b", "new_text": "x"}, "occurs 0 times"),
        ({"path": CALC, "old_text": "return a - b", "new_text": "x"}, "occurs 2 times"),
        ({"path": CALC, "old_text": "", "new_text": "x"}, "empty"),
        ({"path": "services/api/app/other.py", "old_text": "a", "new_text": "b"}, "not shown"),
    ],
)
def test_a_replacement_that_does_not_apply_is_rejected_with_its_reason_never_guessed(
    replacement: dict, why: str
) -> None:
    model = _model({"replacements": [replacement], "new_files": [{"path": TEST, "text": "t"}]})

    patch = model.generate_patch(DEFECT, PLAN, {CALC: BUGGY})

    assert len(patch.rejected) == 1 and why in patch.rejected[0]
    assert replacement["path"] not in patch.paths


def test_the_fix_request_shows_the_previous_proposal_as_a_diff_against_the_base() -> None:
    seen: list = []
    model = _model({"replacements": [], "new_files": [{"path": TEST, "text": "t"}]}, seen=seen)
    wrong = BUGGY.replace("a - b", "a * b", 1)
    previous = Patch(edits=(FileEdit(CALC, wrong), FileEdit(TEST, "old")))

    model.fix_patch(DEFECT, previous, FailureDiagnosis("wrong op", "use +"), {CALC: BUGGY})

    content = seen[0][1]["messages"][0]["content"]
    assert f"--- a/{CALC}" in content and "-    return a - b" in content
    assert "+    return a * b" in content
    assert f'<proposed path="{TEST}">\nold\n</proposed>' in content


def test_every_exchange_is_kept_for_the_record_and_the_key_is_in_none_of_them() -> None:
    model = _model({"cause": "c", "next_step": "n"})

    model.review_failure(DEFECT, Patch(edits=()), "boom")

    assert model.exchanges == [
        {
            "tool": "failure_diagnosis",
            "stop_reason": "tool_use",
            "input_tokens": 120,
            "output_tokens": 30,
            "answer": {"cause": "c", "next_step": "n"},
        }
    ]
    assert KEY not in json.dumps(model.exchanges)
