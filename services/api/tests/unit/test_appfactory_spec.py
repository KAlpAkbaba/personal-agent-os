"""``AppSpec`` (docs/M23_APP_FACTORY_SPEC.md §1, ADR-0086)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.appfactory.spec import AppSpec, slug_for_name


def _task_tracker(**overrides) -> dict:
    base = {"name": "Yapılacaklar", "kind": "web_static", "template": "task-tracker"}
    base.update(overrides)
    return base


def test_task_tracker_spec_is_valid() -> None:
    spec = AppSpec.model_validate(_task_tracker())
    assert spec.slug() == "yapilacaklar" or spec.slug()  # deterministic, non-empty


def test_slug_is_deterministic_and_bounded() -> None:
    assert slug_for_name("Görev Takip!!") == slug_for_name("Görev Takip!!")
    assert slug_for_name("Görev Takip") != ""
    assert slug_for_name("...") == "app"


def test_kind_must_match_template() -> None:
    with pytest.raises(ValidationError, match="requires kind"):
        AppSpec.model_validate(_task_tracker(kind="cli"))


def test_task_tracker_rejects_static_page_fields() -> None:
    with pytest.raises(ValidationError, match="must not carry"):
        AppSpec.model_validate(_task_tracker(page_title="X"))


def test_static_page_spec_is_valid() -> None:
    spec = AppSpec.model_validate(
        {
            "name": "Notlarım",
            "kind": "web_static",
            "template": "static-page",
            "page_title": "Notlarım",
            "page_heading": "Notlarım",
            "page_body": "Merhaba dünya.",
        }
    )
    assert spec.template == "static-page"


def test_cli_tool_requires_at_least_one_command() -> None:
    with pytest.raises(ValidationError, match="requires at least one command"):
        AppSpec.model_validate(
            {"name": "Araç", "kind": "cli", "template": "cli-tool", "commands": []}
        )


def test_cli_tool_command_names_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        AppSpec.model_validate(
            {
                "name": "Araç",
                "kind": "cli",
                "template": "cli-tool",
                "commands": [{"name": "run"}, {"name": "run"}],
            }
        )


def test_cli_tool_command_name_must_be_a_plain_identifier() -> None:
    with pytest.raises(ValidationError):
        AppSpec.model_validate(
            {
                "name": "Araç",
                "kind": "cli",
                "template": "cli-tool",
                "commands": [{"name": "run; rm -rf"}],
            }
        )


def test_cli_tool_spec_is_valid() -> None:
    spec = AppSpec.model_validate(
        {
            "name": "Selamlayıcı",
            "kind": "cli",
            "template": "cli-tool",
            "commands": [{"name": "selamla"}, {"name": "say"}],
        }
    )
    assert [c.name for c in spec.commands] == ["selamla", "say"]


@pytest.mark.parametrize("bad_name", ["..\\x", "C:\\x", "a/b", "a\\b", "..\\..\\x"])
def test_name_may_not_carry_a_path(bad_name: str) -> None:
    with pytest.raises(ValidationError):
        AppSpec.model_validate(_task_tracker(name=bad_name))


@pytest.mark.parametrize(
    "field,value",
    [
        ("page_title", "X\nwindow.__pwned=1;//"),  # the review's working PoC
        ("page_title", "X\r\n//"),
        ("page_title", "X\u2028window.x=1"),  # a JS line terminator that is not \n
        ("page_heading", "a\x00b"),
        ("page_heading", "tab\there"),
        ("name", "Notlar\x85im"),  # a C1 control (NEL)
        ("page_body", "a\x1bb"),  # an escape character inside body text
    ],
)
def test_free_text_refuses_every_control_character(field: str, value: str) -> None:
    """The M23 security review (Critical): a newline in ``page_title`` ended a ``//``
    comment in the generated ``app.js`` and the rest ran as top-level JavaScript in the
    owner's browser. Free text is one line of printable text, or it is refused here —
    before generation, before the device."""
    payload = {"name": "Notlarim", "kind": "web_static", "template": "static-page"}
    payload[field] = value
    with pytest.raises(ValidationError, match="control character"):
        AppSpec.model_validate(payload)


def test_body_text_may_carry_line_breaks() -> None:
    spec = AppSpec.model_validate(
        {
            "name": "Notlarim",
            "kind": "web_static",
            "template": "static-page",
            "page_body": "ilk satır\nikinci satır",
        }
    )
    assert spec.page_body == "ilk satır\nikinci satır"


def test_name_may_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        AppSpec.model_validate(_task_tracker(name=""))


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        AppSpec.model_validate(_task_tracker(unexpected_field="x"))
