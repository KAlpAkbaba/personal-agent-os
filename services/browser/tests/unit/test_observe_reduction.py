"""Contract v1.6 item 1: the reduction of a page to a numbered list (ADR-0207).

``reduce_elements`` is a pure function over the raw records the page-side collector
returns, so everything here runs on plain dictionaries: no browser, no clock.

What each test would catch if it went missing:

* the caps - an observation that grows with the page is not an observation a planner can
  be handed, and it would cross the 48 KiB result cap on the first real shop;
* the order - cutting the list anywhere but at the far end of the page would hide what
  the owner is looking at;
* stable numbering - the same page observed twice must number its elements the same way,
  or a plan made from one observation means nothing in the next;
* values - the value of a field is never in an observation. Not a password, not a card
  number, not an ordinary text box.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from browser_agent import observe
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.observe import (
    ELEMENT_KEYS,
    MAX_NAME_CHARS,
    clamp,
    is_sensitive,
    reduce_elements,
    reduce_text,
)


def _raw(order: int, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "order": order,
        "tag": "button",
        "type": "",
        "role": "button",
        "name": f"Düğme {order}",
        "visible": True,
        "in_viewport": True,
        "disabled": False,
        "readonly": False,
        "required": False,
        "checked": None,
        "expanded": None,
        "selected": None,
        "has_value": None,
        "in_form": False,
        "submits": False,
        "href_host": None,
        "autocomplete": "",
        "field_id": "",
        "has_onclick": False,
        "has_href": False,
        "in_shadow": False,
        "path": [f"html:nth-of-type(1) > body:nth-of-type(1) > button:nth-of-type({order})"],
    }
    record.update(overrides)
    return record


# ------------------------------------------------------------------ numbering and order


def test_the_same_records_are_numbered_the_same_way_every_time() -> None:
    records = [_raw(i) for i in range(1, 9)]
    first = reduce_elements(records, observation_id="a")
    second = reduce_elements(list(records), observation_id="b")
    assert [e.as_dict() for e in first.elements] == [e.as_dict() for e in second.elements]
    assert [e.ref for e in first.elements] == [f"e{i}" for i in range(1, 9)]


def test_the_viewport_comes_first_and_the_rest_follows_in_document_order() -> None:
    records = [
        _raw(1, in_viewport=False, name="yukarıda, ekran dışı"),
        _raw(2, name="ekranda ilk"),
        _raw(3, in_viewport=False, name="aşağıda"),
        _raw(4, name="ekranda ikinci"),
    ]
    names = [e.name for e in reduce_elements(records).elements]
    assert names == ["ekranda ilk", "ekranda ikinci", "yukarıda, ekran dışı", "aşağıda"]


def test_the_viewport_scope_lists_only_what_is_on_the_screen() -> None:
    records = [_raw(1), _raw(2, in_viewport=False), _raw(3)]
    observed = reduce_elements(records, scope="viewport")
    assert [e.name for e in observed.elements] == ["Düğme 1", "Düğme 3"]
    assert all(e.in_viewport for e in observed.elements)
    assert observed.truncated is False


def test_an_unknown_scope_is_refused() -> None:
    with pytest.raises(BrowserError) as caught:
        reduce_elements([_raw(1)], scope="everything")
    assert caught.value.error_class == ErrorClass.VALIDATION_ERROR


# ------------------------------------------------------------------ the caps


def test_the_cap_holds_at_120_elements_and_says_truncated() -> None:
    records = [_raw(i) for i in range(1, 401)]
    observed = reduce_elements(records)
    assert len(observed.elements) == 120
    assert observed.truncated is True
    assert observed.seen == 400
    assert observed.elements[-1].ref == "e120"


def test_a_caller_may_lower_the_cap_and_never_raise_it() -> None:
    records = [_raw(i) for i in range(1, 301)]
    assert len(reduce_elements(records, max_elements=10).elements) == 10
    assert len(reduce_elements(records, max_elements=5000).elements) == 120
    assert clamp(5000, default=120, ceiling=120, name="max_elements") == 120
    assert clamp(None, default=120, ceiling=120, name="max_elements") == 120
    for bad in (0, -3, "12", 1.5, True):
        with pytest.raises(BrowserError):
            clamp(bad, default=120, ceiling=120, name="max_elements")


def test_what_is_cut_is_the_far_end_of_the_page() -> None:
    records = [_raw(i, in_viewport=i <= 3) for i in range(1, 201)]
    observed = reduce_elements(records, max_elements=50)
    assert [e.name for e in observed.elements[:3]] == ["Düğme 1", "Düğme 2", "Düğme 3"]
    assert observed.elements[-1].name == "Düğme 50"


def test_a_truncated_collection_is_a_truncated_observation() -> None:
    observed = reduce_elements([_raw(1)], collector_truncated=True)
    assert observed.truncated is True


def test_a_name_is_capped_at_80_characters() -> None:
    observed = reduce_elements([_raw(1, name="uzun " * 100)])
    name = observed.elements[0].name
    assert len(name) == MAX_NAME_CHARS
    assert name.endswith("…")


def test_the_text_is_capped_at_6000_characters_and_says_so() -> None:
    text, truncated, _ = reduce_text("kelime " * 5000)
    assert len(text) <= 6000 and truncated is True
    short, truncated, _ = reduce_text("kısa metin")
    assert short == "kısa metin" and truncated is False
    lowered, _, _ = reduce_text("a" * 9000, max_chars=100)
    assert len(lowered) == 100
    raised, _, _ = reduce_text("a" * 9000, max_chars=90_000)
    assert len(raised) == 6000


def _worst_case() -> tuple[observe.Observation, str]:
    records = [
        _raw(i, name="ğüşıöç " * 40, href_host="cok-uzun-bir-alan-adi.example.org", has_href=True)
        for i in range(1, 500)
    ]
    text, _, _ = reduce_text("ğüşıöç " * 5000)
    return reduce_elements(records), text


def _bytes(observed: observe.Observation, text: str) -> int:
    payload = {"elements": [e.as_dict() for e in observed.elements], "text": text}
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def test_the_character_caps_alone_do_not_fit_the_result_cap() -> None:
    """The reason ``fit_to_budget`` exists, kept as a test so nobody removes it as
    redundant: 120 names and 6 000 characters of TURKISH text are 56 KB of UTF-8, and
    every browser result is held to 48 KiB."""
    observed, text = _worst_case()
    assert len(observed.elements) == 120 and len(text) == 6000
    assert _bytes(observed, text) > 48 * 1024


def test_an_observation_fits_its_byte_budget_text_first_then_the_far_end() -> None:
    observed, text = _worst_case()
    fitted, fitted_text, elements_cut, text_cut = observe.fit_to_budget(observed, text)

    assert _bytes(fitted, fitted_text) <= observe.MAX_OBSERVATION_BYTES < 48 * 1024
    assert text_cut is True and len(fitted_text) >= observe.MIN_TEXT_CHARS
    # The text gave way before any element did, and what went is the END of the list.
    assert [e.ref for e in fitted.elements] == [f"e{i}" for i in range(1, len(fitted.elements) + 1)]
    assert elements_cut is (len(fitted.elements) < 120)
    assert fitted.truncated is True
    assert len(fitted.elements) >= 100
    # A reference that was cut from the result does not exist in what the worker holds.
    assert fitted.by_ref(f"e{len(fitted.elements)}") is not None
    assert fitted.by_ref(f"e{len(fitted.elements) + 1}") is None


def test_an_observation_that_already_fits_is_returned_untouched() -> None:
    observed = reduce_elements([_raw(i) for i in range(1, 30)])
    fitted, text, elements_cut, text_cut = observe.fit_to_budget(observed, "kısa metin")
    assert fitted is observed and text == "kısa metin"
    assert elements_cut is False and text_cut is False


def test_a_tiny_budget_still_returns_an_element_and_never_raises() -> None:
    observed, text = _worst_case()
    fitted, fitted_text, elements_cut, text_cut = observe.fit_to_budget(
        observed, text, max_bytes=600
    )
    assert len(fitted.elements) == 1 and elements_cut is True and text_cut is True
    assert _bytes(fitted, fitted_text) <= 600 or fitted_text == ""


# ------------------------------------------------------------------ what is listed


def test_hidden_elements_are_not_listed_and_disabled_ones_say_so() -> None:
    records = [
        _raw(1, visible=False, name="görünmez"),
        _raw(2, disabled=True, name="Stokta yok"),
        _raw(3, name="görünür"),
    ]
    observed = reduce_elements(records)
    assert [e.name for e in observed.elements] == ["Stokta yok", "görünür"]
    assert "disabled" in observed.elements[0].state
    assert "disabled" not in observed.elements[1].state


def test_two_elements_with_one_name_get_two_references() -> None:
    records = [_raw(1, name="Sepete ekle"), _raw(2, name="Sepete ekle")]
    observed = reduce_elements(records)
    assert [e.name for e in observed.elements] == ["Sepete ekle", "Sepete ekle"]
    assert observed.elements[0].ref != observed.elements[1].ref
    assert observed.elements[0].path != observed.elements[1].path


def test_state_is_a_closed_vocabulary() -> None:
    record = _raw(
        1,
        role="checkbox",
        tag="input",
        type="checkbox",
        checked=True,
        required=True,
        expanded=False,
        in_shadow=True,
    )
    assert reduce_elements([record]).elements[0].state == (
        "required",
        "checked",
        "not_expanded",
        "in_shadow",
    )
    empty_box = _raw(2, role="textbox", tag="input", type="text", has_value=False)
    assert "empty" in reduce_elements([empty_box]).elements[0].state


def test_an_element_carries_exactly_the_documented_keys() -> None:
    observed = reduce_elements([_raw(1)])
    assert tuple(observed.elements[0].as_dict()) == ELEMENT_KEYS
    # The path and the fingerprint stay in the worker.
    assert "path" not in observed.elements[0].as_dict()
    assert "fingerprint" not in observed.elements[0].as_dict()


def test_records_that_are_not_records_are_ignored() -> None:
    observed = reduce_elements([None, "düğme", 7, _raw(1)])  # type: ignore[list-item]
    assert [e.ref for e in observed.elements] == ["e1"]


# ------------------------------------------------------------------ values never leave


SECRET = "cok-gizli-parola-9731"
CARD = "4111111111111111"


@pytest.mark.parametrize(
    "record",
    [
        _raw(1, role="textbox", tag="input", type="password", name="Parola", has_value=True),
        _raw(2, role="textbox", tag="input", type="text", name="Kart", autocomplete="cc-number"),
        _raw(3, role="textbox", tag="input", type="text", name="E-posta", has_value=True),
    ],
)
def test_the_value_of_a_field_is_never_in_an_observation(record: dict[str, Any]) -> None:
    """Even when a raw record CARRIES a value - the collector never sends one - the
    reducer copies a closed set of keys and the value has nowhere to go."""
    smuggled = {**record, "value": SECRET, "text": CARD, "defaultValue": SECRET}
    observed = reduce_elements([smuggled])
    dumped = json.dumps([e.as_dict() for e in observed.elements], ensure_ascii=False)
    assert SECRET not in dumped and CARD not in dumped
    assert "value" not in observed.elements[0].as_dict()
    assert repr(observed).count(SECRET) == 0


def test_the_collector_reads_a_boolean_and_never_the_value() -> None:
    """The structural half of the promise: the page-side script has no expression that
    returns a field's content. ``el.value`` appears only inside the boolean test."""
    script = observe.COLLECT_JS
    assert "has_value: hasValue" in script
    for line in script.splitlines():
        if "el.value" in line:
            assert "!!(el.value && String(el.value).length)" in line, line
    assert "getAttribute('value')" in script  # the LABEL of a button-like input only
    for line in script.splitlines():
        if "getAttribute('value')" in line:
            assert "return el.getAttribute('value') || type;" in line, line


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"type": "password"}, True),
        ({"autocomplete": "cc-number"}, True),
        ({"autocomplete": "section-pay cc-csc"}, True),
        ({"autocomplete": "one-time-code"}, True),
        ({"name": "Kart numarası"}, True),
        ({"name": "Güvenlik kodu"}, True),
        ({"field_id": "user_password login-pw"}, True),
        ({"field_id": "cvv"}, True),
        ({"name": "TC Kimlik No"}, True),
        ({"name": "IBAN"}, True),
        ({"name": "Doğrulama kodu"}, True),
        ({"name": "Şifre"}, True),
        ({"name": "E-posta"}, False),
        ({"name": "Adres"}, False),
        ({"name": "Pinterest kullanıcı adı"}, False),
        ({"name": "Şifreleme hakkında not"}, False),
        ({"field_id": "shipping_address"}, False),
    ],
)
def test_a_credential_a_card_or_an_identity_field_is_sensitive(
    overrides: dict[str, Any], expected: bool
) -> None:
    record = _raw(1, role="textbox", tag="input", type="text", name="alan", **{})
    record.update(overrides)
    assert is_sensitive(record) is expected
    assert reduce_elements([record]).elements[0].sensitive is expected


def test_only_a_field_can_be_sensitive() -> None:
    assert is_sensitive(_raw(1, role="button", name="PIN gönder")) is False
    assert is_sensitive(_raw(1, role="link", tag="a", name="Parolamı unuttum")) is False


# ------------------------------------------------------------------ the risk hint


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"name": "Sepete ekle"}, "REVERSIBLE_WRITE"),
        ({"name": "Siparişi tamamla", "submits": True, "in_form": True}, "HIGH_IMPACT"),
        ({"name": "Kaydet", "submits": True, "in_form": True}, "EXTERNAL_COMMUNICATION"),
        ({"name": "Onayla"}, "EXTERNAL_COMMUNICATION"),
        ({"name": "Paylaş"}, "EXTERNAL_COMMUNICATION"),
        ({"name": "Silver plan"}, "REVERSIBLE_WRITE"),
        ({"name": "Dark mode"}, "REVERSIBLE_WRITE"),
        ({"role": "link", "tag": "a", "has_href": True, "name": "Kargo bilgisi"}, "NAVIGATE"),
        (
            {"role": "link", "tag": "a", "has_href": True, "has_onclick": True, "name": "Aç"},
            "REVERSIBLE_WRITE",
        ),
        ({"role": "link", "tag": "a", "has_href": True, "name": "Satın al"}, "HIGH_IMPACT"),
        (
            {"role": "textbox", "tag": "input", "type": "text", "name": "Silinecek hesap"},
            "REVERSIBLE_WRITE",
        ),
        (
            {"role": "checkbox", "tag": "input", "type": "checkbox", "name": "Abone ol"},
            "REVERSIBLE_WRITE",
        ),
    ],
)
def test_the_risk_hint_follows_the_contracts_own_rule(
    overrides: dict[str, Any], expected: str
) -> None:
    record = _raw(1)
    record.update(overrides)
    assert reduce_elements([record]).elements[0].risk_hint == expected
