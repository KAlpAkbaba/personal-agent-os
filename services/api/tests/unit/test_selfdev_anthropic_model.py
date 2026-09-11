"""The Anthropic EngineeringModel's seam, over a transport that records instead of dialling.

What is pinned: every answer is a FORCED tool call and is read from the tool input only;
usage is accumulated for the budget; the key travels only in the header and never in an
error; a non-200 says its status and error type and nothing of the body; a model that does
not answer through the tool is an error, never a guess.

And what three real runs (Phase 10) taught. An answer is READ, never trusted to have its
schema's shape: an array sent as a JSON string is decoded, not iterated a character at a
time; any other wrong shape is a ModelError naming the field; an answer cut off at
``max_tokens`` is never used; a review's ``"false"`` is not an approval. And a patch is ONE
top-level string of SEARCH/REPLACE blocks: asked for an array of objects holding long code,
the real model twice returned it garbled - its own parameter markup inside the value, a field
escaped to the top level. The blocks are resolved here against the text the model was shown;
a block that does not apply is carried as ``rejected``, never guessed at.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.selfdev.anthropic_model import AnthropicEngineeringModel, ModelError, parse_edit_blocks
from app.selfdev.model import (
    ChangePlan,
    CodebaseAnalysis,
    DefectSpec,
    FailureDiagnosis,
    FileEdit,
    Patch,
)

KEY = "test-anthropic-key-not-a-real-one"
CALC = "services/api/app/calc.py"
TEST = "services/api/tests/unit/test_calc_regression.py"
DEFECT = DefectSpec("d1", "add subtracts", "add(2,3) == -1", (CALC,))
PLAN = ChangePlan("add adds", (CALC,), TEST, "r")
BUGGY = "def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n"


def _replace(path: str, *pairs: tuple[str, str]) -> str:
    blocks = [f"=== FILE {path}"]
    for old, new in pairs:
        blocks += ["<<<<<<< SEARCH", old, "=======", new, ">>>>>>> REPLACE"]
    return "\n".join(blocks) + "\n"


def _new_file(path: str, text: str) -> str:
    return f"=== NEW FILE {path}\n{text}=== END FILE\n"


FIX_ADD = _replace(CALC, ("def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b"))
REGRESSION = "def test_add() -> None:\n    assert add(2, 3) == 5\n"


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
    model = _model({"edits": FIX_ADD + _new_file(TEST, REGRESSION), "notes": "n"}, seen=seen)

    patch = model.generate_patch(DEFECT, PLAN, {CALC: BUGGY})

    assert isinstance(patch, Patch) and patch.paths == (CALC, TEST)
    headers, body = seen[0]
    assert body["tool_choice"] == {"type": "tool", "name": "patch"}
    assert body["model"] == "claude-opus-5"
    assert headers["x-api-key"] == KEY
    # The files reach the model as data inside tags, never as instructions.
    assert f'<file path="{CALC}">' in body["messages"][0]["content"]
    assert "DATA" in body["system"]
    # And the patch is asked for as ONE top-level string: no nested structure to garble.
    schema = body["tools"][0]["input_schema"]
    assert schema["properties"]["edits"]["type"] == "string"
    assert all(p["type"] == "string" for p in schema["properties"].values())


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
    model = _model(
        {
            "summary": "s",
            "paths_to_change": json.dumps([CALC]),
            "regression_test_path": TEST,
            "regression_test_rationale": "r",
        }
    )

    plan = model.plan_change(DEFECT, CodebaseAnalysis("s", (CALC,), "c"), {CALC: BUGGY})

    assert plan.paths_to_change == (CALC,)


@pytest.mark.parametrize(
    ("answer", "field"),
    [
        # The third real run, verbatim in shape: the model's parameter markup inside the
        # value of a nested array, and one field escaped to the top level.
        (
            {"replacements": '\n<parameter name="old_text">x', "new_text": "y", "notes": "n"},
            "edits",
        ),
        ({"edits": ["a list of blocks"]}, "edits"),
        ({"edits": 7}, "edits"),
    ],
)
def test_a_patch_of_the_wrong_shape_is_a_model_error_naming_the_field(
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


# ------------------------------------------------------------------ blocks


def test_blocks_resolve_in_order_against_the_text_the_model_was_shown() -> None:
    edits = _replace(
        CALC,
        ("def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b"),
        ("def sub(", "def subtract("),
    ) + _new_file(TEST, REGRESSION)
    model = _model({"edits": edits.replace("\n", "\r\n")})

    patch = model.generate_patch(DEFECT, PLAN, {CALC: BUGGY})

    assert patch.rejected == ()
    fixed = BUGGY.replace("a - b", "a + b", 1).replace("def sub(", "def subtract(")
    assert patch.edits == (FileEdit(CALC, fixed), FileEdit(TEST, REGRESSION))


def test_a_new_file_keeps_its_blank_lines_and_ends_in_one_newline() -> None:
    text = "import x\n\n\ndef test_a() -> None:\n    assert x\n"
    replacements, new_files, problems = parse_edit_blocks(_new_file(TEST, text))
    assert (replacements, new_files, problems) == ([], [(TEST, text)], [])


@pytest.mark.parametrize(
    ("edits", "why"),
    [
        (_replace(CALC, ("return a * b", "x")), "occurs 0 times"),
        (_replace(CALC, ("return a - b", "x")), "occurs 2 times"),
        ("=== FILE " + CALC + "\n<<<<<<< SEARCH\n=======\nx\n>>>>>>> REPLACE\n", "empty"),
        (_replace("services/api/app/other.py", ("a", "b")), "not shown"),
        ("=== FILE " + CALC + "\n<<<<<<< SEARCH\nreturn a - b\n=======\nx\n", "unterminated"),
        ("<<<<<<< SEARCH\nreturn a - b\n=======\nx\n>>>>>>> REPLACE\n", "no === FILE"),
        (f"=== NEW FILE {TEST}\nabc\n", "no '=== END FILE'"),
        ("Here is the patch you asked for:\n" + FIX_ADD, "outside any block"),
        (_replace(CALC, ("x", "y")).replace("=======", "====="), "unterminated"),
    ],
    ids=[
        "absent",
        "ambiguous",
        "empty-search",
        "not-shown",
        "no-replace-marker",
        "no-file-header",
        "no-end-of-new-file",
        "prose-outside-blocks",
        "broken-divider",
    ],
)
def test_a_block_that_does_not_apply_is_rejected_with_its_reason_never_guessed(
    edits: str, why: str
) -> None:
    model = _model({"edits": edits + _new_file(TEST, REGRESSION)})

    patch = model.generate_patch(DEFECT, PLAN, {CALC: BUGGY})

    assert patch.rejected and any(why in reason for reason in patch.rejected), patch.rejected


def test_the_fix_request_shows_the_previous_proposal_as_a_diff_against_the_base() -> None:
    seen: list = []
    model = _model({"edits": _new_file(TEST, "t\n")}, seen=seen)
    wrong = BUGGY.replace("a - b", "a * b", 1)
    previous = Patch(edits=(FileEdit(CALC, wrong), FileEdit(TEST, "old")), rejected=("r0",))

    model.fix_patch(DEFECT, previous, FailureDiagnosis("wrong op", "use +"), {CALC: BUGGY})

    content = seen[0][1]["messages"][0]["content"]
    assert f"--- a/{CALC}" in content and "-    return a - b" in content
    assert "+    return a * b" in content
    assert f'<proposed path="{TEST}">\nold\n</proposed>' in content
    assert "did not apply:\nr0" in content


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
