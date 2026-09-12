"""B03 req 5: a fake device answers what the device answers.

Three times now, a fake has been kinder than the machine:

* 2026-09-11 - the C# manifest test scaffolded its OWN fixture instead of the one the Cloud
  Core sends, so a bare string where an object belonged reached production;
* 2026-09-12 (B01) - the fake docker could not fail a migration, so no test could see that a
  failed migration did not stop a release;
* 2026-09-12 (this batch) - every fake ``project.test`` result omitted ``counts_parsed``,
  the field the device uses to say it could NOT read its runner's output. The Cloud Core
  ignored the field and stamped ``verified`` with ``passed: null``; no test could see that
  either, because no fake ever sent the field to ignore.

The last one is the shape this test closes: the keys a fake answers are read from the
DEVICE's own source, so a field the device starts sending cannot stay invisible to the
Cloud Core's tests.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
DEVICE_PROJECTS = (
    REPO_ROOT
    / "devices"
    / "windows-agent"
    / "src"
    / "PagentOS.SessionCompanion"
    / "Projects"
    / "ProjectCapabilities.cs"
)
SUPPORT = Path(__file__).resolve().parents[1] / "appfactory_support.py"

#: The device method whose result the fakes stand in for, and the fake functions that do it.
_CAPABILITY_METHOD = "TestAsync"
_FAKE_FUNCTIONS = ("project_test_ok", "project_test_failing")

#: Keys the device returns that a fake may legitimately leave out: values a caller never reads
#: and that carry no verdict. Each one must be named here, so "the fake is smaller" is a
#: decision somebody made rather than a field somebody forgot.
_OPTIONAL_FOR_FAKES = frozenset({"project_id", "slug", "command_key", "truncated", "log_path"})


def _device_result_keys() -> set[str]:
    """The keys the device's ``project.test`` really answers, from its own source.

    Anchored on the METHOD DECLARATION rather than the first mention of the name: the name
    appears first in the dispatch table and again as a call into the runner, and slicing from
    either found no result literal at all - a reader that silently finds nothing is the same
    failure this file exists to prevent, which is why the guard below checks it.
    """
    source = DEVICE_PROJECTS.read_text("utf-8")
    start = source.index(f"private async Task<JsonObject> {_CAPABILITY_METHOD}(")
    end = source.index("// =====", start)
    return set(re.findall(r'\["([a-z_]+)"\]\s*=', source[start:end]))


def _fake_result_keys(function: str) -> set[str]:
    tree = ast.parse(SUPPORT.read_text("utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            for inner in ast.walk(node):
                if isinstance(inner, ast.keyword) and inner.arg == "result":
                    assert isinstance(inner.value, ast.Dict)
                    return {
                        key.value
                        for key in inner.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }
    raise AssertionError(f"{function} has no result= mapping in {SUPPORT}")


def test_the_device_source_is_readable_and_says_something() -> None:
    """A guard on the guard: an unreadable device source would make every assertion vacuous."""
    keys = _device_result_keys()
    assert {"exit_code", "passed", "failed", "counts_parsed"} <= keys, sorted(keys)


@pytest.mark.parametrize("function", _FAKE_FUNCTIONS)
def test_a_fake_project_test_answers_what_the_device_answers(function: str) -> None:
    device = _device_result_keys() - _OPTIONAL_FOR_FAKES
    fake = _fake_result_keys(function)
    missing = sorted(device - fake)
    assert not missing, (
        f"{function} omits {missing}, which the real device always sends. A fake that is "
        "smaller than the machine cannot show that the Cloud Core ignores a field - which is "
        "exactly how `counts_parsed` was ignored while builds were stamped verified."
    )


@pytest.mark.parametrize("function", _FAKE_FUNCTIONS)
def test_a_fake_invents_no_field_the_device_never_sends(function: str) -> None:
    """The other direction: a fake that answers a key the device does not send lets the Cloud
    Core be written against a shape that will never arrive."""
    invented = sorted(_fake_result_keys(function) - _device_result_keys())
    assert not invented, f"{function} answers {invented}, which the device never sends"
