"""Unit tests for semantic target-spec validation (no browser needed)."""

import pytest

from browser_agent import BrowserError, ErrorClass, TargetSpec, coerce_target


def _assert_validation_error(excinfo: pytest.ExceptionInfo[BrowserError]) -> None:
    assert excinfo.value.error_class is ErrorClass.VALIDATION_ERROR
    assert excinfo.value.retryable is False


@pytest.mark.parametrize(
    "spec",
    [
        TargetSpec(role="button", name="Greet"),
        TargetSpec(role="navigation"),
        TargetSpec(text="All systems nominal"),
        TargetSpec(label="Your name"),
        TargetSpec(placeholder="Search query"),
        TargetSpec(test_id="status-box"),
    ],
)
def test_valid_specs_pass_validation(spec: TargetSpec) -> None:
    spec.validate()


def test_empty_spec_is_validation_error() -> None:
    with pytest.raises(BrowserError) as excinfo:
        TargetSpec().validate()
    _assert_validation_error(excinfo)


def test_multiple_primary_strategies_is_validation_error() -> None:
    with pytest.raises(BrowserError) as excinfo:
        TargetSpec(role="button", text="Greet").validate()
    _assert_validation_error(excinfo)


def test_name_without_role_is_validation_error() -> None:
    with pytest.raises(BrowserError) as excinfo:
        TargetSpec(text="Greet", name="Greet").validate()
    _assert_validation_error(excinfo)


@pytest.mark.parametrize(
    "spec",
    [TargetSpec(role=""), TargetSpec(text="   "), TargetSpec(role="button", name="")],
)
def test_blank_values_are_validation_errors(spec: TargetSpec) -> None:
    with pytest.raises(BrowserError) as excinfo:
        spec.validate()
    _assert_validation_error(excinfo)


def test_coerce_accepts_dict() -> None:
    spec = coerce_target({"role": "button", "name": "Greet", "exact": True})
    assert spec == TargetSpec(role="button", name="Greet", exact=True)


def test_coerce_rejects_unknown_fields() -> None:
    with pytest.raises(BrowserError) as excinfo:
        coerce_target({"xpath": "//button"})
    _assert_validation_error(excinfo)


def test_coerce_rejects_raw_coordinates() -> None:
    """Raw coordinates must not be expressible as a semantic target."""
    with pytest.raises(BrowserError) as excinfo:
        coerce_target({"x": 100, "y": 200})
    _assert_validation_error(excinfo)


def test_coerce_rejects_non_dict() -> None:
    with pytest.raises(BrowserError) as excinfo:
        coerce_target("button")  # type: ignore[arg-type]
    _assert_validation_error(excinfo)


def test_as_dict_drops_unset_fields() -> None:
    assert TargetSpec(label="Your name").as_dict() == {"label": "Your name"}
    assert TargetSpec(role="button", name="Greet", exact=True).as_dict() == {
        "role": "button",
        "name": "Greet",
        "exact": True,
    }
