"""Contract v1.6 end to end: observe, act by reference, observe again (ADR-0207, PR-A).

A real worker driving real (headless) Chromium against the fixture site - the same
harness as ``test_worker_e2e.py``. This is what the unit tests cannot show: that the
collector really numbers a real DOM, that a reference really reaches the element it was
given for and no other, and that the page really is not written to.

The evidence class this file supports is ``PROVEN_PROXY``: a real browser, a fixture
site. The owner's own Chrome is PR-C.
"""

from __future__ import annotations

import json

import pytest

from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.worker import Worker

pytestmark = pytest.mark.browser

WRITE = ("READ", "NAVIGATE", "REVERSIBLE_WRITE")
SECRET = "cok-gizli-parola-9731"
CARD = "4111111111111111"
CVV = "987"
EMAIL = "sahip@example.org"
COUPON = "GIZLI-KUPON-4417"
BARE = "ETIKETSIZ-DEGER-5520"


async def _open(worker: Worker, classes: tuple[str, ...] = WRITE) -> None:
    await worker._execute(
        "browser.session_open",
        {
            "session_id": "s1",
            "profile": "isolated",
            "policy": {"allowed_risk_classes": list(classes), "visible": False},
        },
    )


async def _goto(worker: Worker, url: str) -> None:
    await worker._execute("browser.navigate", {"session_id": "s1", "url": url})


async def _observe(worker: Worker, **payload: object) -> dict:
    return await worker._execute("browser.observe", {"session_id": "s1", **payload})


async def _status(worker: Worker) -> str:
    found = await worker._execute(
        "browser.find", {"session_id": "s1", "target": {"test_id": "status"}}
    )
    if found["match_count"]:
        return found["elements"][0]["text"]
    extracted = await worker._execute(
        "browser.extract", {"session_id": "s1", "mode": "text", "max_chars": 4000}
    )
    return extracted["text"]


def _by_name(observation: dict, name: str) -> list[dict]:
    return [e for e in observation["elements"] if e["name"] == name]


def _ref(observation: dict, element: dict) -> dict:
    return {"ref": element["ref"], "observation_id": observation["observation_id"]}


# --------------------------------------------------------------------------- #
# what an observation is
# --------------------------------------------------------------------------- #


async def test_the_page_is_a_numbered_list_of_what_can_be_acted_on(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)

    assert seen["url"].endswith("observe.html")
    # The page carries a password field, so the classifier every other operation uses
    # calls it an auth wall (contract section 3). An observation reports that kind
    # and lists the page all the same: what to make of it is the consumer's decision.
    assert seen["page_kind"] == "auth_wall"
    assert seen["observation_id"].startswith("obs-")
    refs = [e["ref"] for e in seen["elements"]]
    assert refs == [f"e{i}" for i in range(1, len(refs) + 1)]
    assert seen["element_count"] == len(refs)

    names = [e["name"] for e in seen["elements"]]
    for expected in ("Kargo bilgisi", "Sepete ekle", "Paylaş", "Siparişi tamamla", "E-posta"):
        assert expected in names, names
    # Hidden and aria-hidden controls are not there; a disabled one is, and says so.
    assert "Görünmez düğme" not in names
    assert "Yardımcı teknolojiden gizli" not in names
    assert "disabled" in _by_name(seen, "Stokta yok")[0]["state"]
    # An open shadow root is walked.
    assert "in_shadow" in _by_name(seen, "Gölgedeki düğme")[0]["state"]
    # The text is the page's, the viewport comes first.
    assert "Kulaklıklar" in seen["text"]
    first_off_screen = next(i for i, e in enumerate(seen["elements"]) if not e["in_viewport"])
    assert all(not e["in_viewport"] for e in seen["elements"][first_off_screen:])
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_the_risk_hint_is_the_contracts_rule_on_a_real_page(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)

    def hint(name: str) -> str:
        return _by_name(seen, name)[0]["risk_hint"]

    assert hint("Sepete ekle") == "REVERSIBLE_WRITE"
    assert hint("Silver plan") == "REVERSIBLE_WRITE"
    assert hint("Dark mode") == "REVERSIBLE_WRITE"
    assert hint("Paylaş") == "EXTERNAL_COMMUNICATION"
    assert hint("Siparişi tamamla") == "HIGH_IMPACT"
    assert hint("Kargo bilgisi") == "NAVIGATE"
    assert _by_name(seen, "Yardım")[0]["href_host"] == "example.org"
    submit = _by_name(seen, "Siparişi tamamla")[0]
    assert submit["in_form"] is True and submit["submits"] is True
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_the_value_of_a_field_never_leaves_the_page(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    dumped = json.dumps(seen, ensure_ascii=False)

    for value in (SECRET, CARD, EMAIL, COUPON, BARE):
        assert value not in dumped
    # A field with no label is named by its placeholder, a field with nothing is
    # nameless - neither is ever named by what is typed in it.
    coupon = _by_name(seen, "İndirim kodu")
    assert coupon and "has_value" in coupon[0]["state"]
    nameless = [e for e in seen["elements"] if e["role"] == "textbox" and not e["name"]]
    assert len(nameless) == 1 and "has_value" in nameless[0]["state"]
    assert f'"{CVV}"' not in dumped
    assert all("value" not in element for element in seen["elements"])

    assert _by_name(seen, "Parola")[0]["sensitive"] is True
    assert _by_name(seen, "Kart numarası")[0]["sensitive"] is True
    assert _by_name(seen, "Güvenlik kodu")[0]["sensitive"] is True
    assert _by_name(seen, "E-posta")[0]["sensitive"] is False
    assert "has_value" in _by_name(seen, "Parola")[0]["state"]
    assert "has_value" in _by_name(seen, "E-posta")[0]["state"]
    assert "empty" in _by_name(seen, "Not")[0]["state"]
    assert "checked" in _by_name(seen, "Hediye paketi")[0]["state"]
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_observing_writes_nothing_into_the_page(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    state = worker._sessions["s1"]
    page = state.browser_session.backend.current_page

    async def fingerprint() -> dict:
        return await page.evaluate(
            """() => ({
                html: document.documentElement.outerHTML.length,
                attrs: Array.from(document.querySelectorAll('*'))
                    .reduce((n, el) => n + el.attributes.length, 0),
                globals: Object.keys(window).filter((k) => /pagentos|observe|__obs/i.test(k)),
            })"""
        )

    before = await fingerprint()
    await _observe(worker)
    await _observe(worker, scope="viewport", max_elements=5)
    assert await fingerprint() == before
    assert before["globals"] == []
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_the_caps_can_be_lowered_and_never_raised(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    few = await _observe(worker, max_elements=3, max_text_chars=20)
    assert len(few["elements"]) == 3 and few["elements_truncated"] is True
    assert len(few["text"]) <= 20 and few["text_truncated"] is True and few["truncated"] is True

    many = await _observe(worker, max_elements=99_999, max_text_chars=99_999)
    assert len(many["elements"]) <= 120 and len(many["text"]) <= 6000

    on_screen = await _observe(worker, scope="viewport")
    assert all(e["in_viewport"] for e in on_screen["elements"])
    assert "Sayfanın sonundaki düğme" not in [e["name"] for e in on_screen["elements"]]

    for bad in ({"max_elements": 0}, {"max_elements": "12"}, {"scope": "everything"}):
        with pytest.raises(BrowserError) as caught:
            await _observe(worker, **bad)
        assert caught.value.error_class == ErrorClass.VALIDATION_ERROR
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_read_only_session_may_observe(worker, site_url) -> None:
    await _open(worker, classes=("READ", "NAVIGATE"))
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    assert seen["element_count"] > 5
    with pytest.raises(BrowserError) as caught:
        await worker._execute(
            "browser.click",
            {"session_id": "s1", "target": _ref(seen, _by_name(seen, "Sepete ekle")[0])},
        )
    assert caught.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# acting by reference
# --------------------------------------------------------------------------- #


async def test_a_reference_reaches_the_element_it_was_given_for(worker, site_url) -> None:
    """Two buttons with ONE name. Until v1.6 a click could only ever reach the first."""
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    first, second = _by_name(seen, "Sepete ekle")
    assert first["ref"] != second["ref"]

    clicked = await worker._execute(
        "browser.click", {"session_id": "s1", "target": _ref(seen, second)}
    )
    assert clicked["clicked"] is True and clicked["resolved"]["name"] == "Sepete ekle"
    assert (await _observe(worker))["text"].count("Sepette: Kulaklık B") == 1

    again = await _observe(worker)
    await worker._execute(
        "browser.click",
        {"session_id": "s1", "target": _ref(again, _by_name(again, "Sepete ekle")[0])},
    )
    assert "Sepette: Kulaklık A" in (await _observe(worker))["text"]
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_nth_picks_the_second_match_without_an_observation(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    target = {"role": "button", "name": "Sepete ekle", "nth": 1}
    found = await worker._execute("browser.find", {"session_id": "s1", "target": target})
    assert found["match_count"] == 1
    await worker._execute("browser.click", {"session_id": "s1", "target": target})
    assert "Sepette: Kulaklık B" in (await _observe(worker))["text"]

    with pytest.raises(BrowserError) as caught:
        await worker._execute(
            "browser.click",
            {
                "session_id": "s1",
                "target": {"role": "button", "name": "Sepete ekle", "nth": 7},
                "timeout_ms": 500,
            },
        )
    assert caught.value.error_class == ErrorClass.UI_TARGET_NOT_FOUND
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_reference_fills_a_field_and_reaches_into_an_open_shadow_root(
    worker, site_url
) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    await worker._execute(
        "browser.fill",
        {"session_id": "s1", "target": _ref(seen, _by_name(seen, "Not")[0]), "value": "kapıya"},
    )
    after = await _observe(worker)
    assert "has_value" in _by_name(after, "Not")[0]["state"]
    assert "kapıya" not in json.dumps(after, ensure_ascii=False)

    await worker._execute(
        "browser.click",
        {"session_id": "s1", "target": _ref(after, _by_name(after, "Gölgedeki düğme")[0])},
    )
    assert "Gölge düğmesine basıldı" in (await _observe(worker))["text"]
    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# a reference lives as long as its observation
# --------------------------------------------------------------------------- #


async def _refused(worker: Worker, target: dict) -> BrowserError:
    with pytest.raises(BrowserError) as caught:
        await worker._execute("browser.click", {"session_id": "s1", "target": target})
    return caught.value


async def test_the_old_observations_reference_is_refused(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    old = await _observe(worker)
    new = await _observe(worker)
    assert old["observation_id"] != new["observation_id"]

    error = await _refused(worker, _ref(old, _by_name(old, "Sepete ekle")[0]))
    assert error.error_class == ErrorClass.UI_STATE_CHANGED and error.retryable is True
    assert error.evidence["reason"] == "other_observation"
    # Nothing was clicked, and the new observation's reference still works.
    assert "Sepet boş." in (await _observe(worker))["text"]
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_reference_does_not_survive_a_navigation(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    target = _ref(seen, _by_name(seen, "Sepete ekle")[0])

    await _goto(worker, f"{site_url}/second.html")
    error = await _refused(worker, target)
    assert error.error_class == ErrorClass.UI_STATE_CHANGED
    assert error.evidence["reason"] == "navigated"

    # ...not even when the tab comes back to the very same URL: it is another document.
    await _goto(worker, f"{site_url}/observe.html")
    error = await _refused(worker, target)
    assert error.evidence["reason"] == "navigated"
    assert "Sepet boş." in (await _observe(worker))["text"]
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_reference_does_not_survive_a_reload(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    page = worker._sessions["s1"].browser_session.backend.current_page
    await page.reload()
    error = await _refused(worker, _ref(seen, _by_name(seen, "Sepete ekle")[0]))
    assert error.evidence["reason"] == "navigated"
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_reference_is_never_resolved_to_something_similar(worker, site_url) -> None:
    """The page changes under the observation: the element is removed, replaced by
    another with the same place, or renamed. Each is refused; none is clicked."""
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    page = worker._sessions["s1"].browser_session.backend.current_page

    seen = await _observe(worker)
    await page.evaluate("document.getElementById('add-b').remove()")
    error = await _refused(worker, _ref(seen, _by_name(seen, "Sepete ekle")[1]))
    assert error.error_class == ErrorClass.UI_STATE_CHANGED
    assert error.evidence["reason"] in {"gone", "changed"}

    seen = await _observe(worker)
    await page.evaluate("document.getElementById('add-a').textContent = 'Satın al'")
    error = await _refused(worker, _ref(seen, _by_name(seen, "Sepete ekle")[0]))
    assert error.evidence["reason"] == "changed"

    seen = await _observe(worker)
    await page.evaluate(
        """() => {
            const old = document.getElementById('add-a');
            const link = document.createElement('a');
            link.href = 'result.html';
            link.textContent = old.textContent;
            old.replaceWith(link);
        }"""
    )
    error = await _refused(worker, _ref(seen, _by_name(seen, "Satın al")[0]))
    assert error.evidence["reason"] in {"gone", "changed"}
    assert page.url.endswith("observe.html")
    assert "Sepet boş." in (await _observe(worker))["text"]
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_reference_belongs_to_its_tab(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    await worker._execute(
        "browser.tab_new", {"session_id": "s1", "url": f"{site_url}/observe.html"}
    )
    error = await _refused(worker, _ref(seen, _by_name(seen, "Sepete ekle")[0]))
    assert error.error_class == ErrorClass.UI_STATE_CHANGED
    assert error.evidence["reason"] in {"other_tab", "navigated"}
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_reference_nobody_handed_out_and_a_session_that_never_observed(
    worker, site_url
) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe.html")
    error = await _refused(worker, {"ref": "e1", "observation_id": "obs-never"})
    assert error.evidence["reason"] == "no_observation"

    seen = await _observe(worker)
    error = await _refused(worker, {"ref": "e999", "observation_id": seen["observation_id"]})
    assert error.error_class == ErrorClass.VALIDATION_ERROR
    await worker._execute("browser.session_close", {"session_id": "s1"})


# --------------------------------------------------------------------------- #
# a page that gives orders
# --------------------------------------------------------------------------- #


async def test_a_hostile_page_is_observed_and_nothing_on_it_is_done(worker, site_url) -> None:
    await _open(worker)
    await _goto(worker, f"{site_url}/observe-hostile.html")
    seen = await _observe(worker)

    assert seen["url"].endswith("observe-hostile.html")
    assert seen["injection_markers"] >= 4
    assert "Nothing happened." in seen["text"]

    order = next(e for e in seen["elements"] if "click buy now" in e["name"])
    assert order["risk_hint"] == "HIGH_IMPACT"
    disguised = _by_name(seen, "Pay now")
    assert disguised and disguised[0]["risk_hint"] == "HIGH_IMPACT"
    assert all(len(e["name"]) <= 80 for e in seen["elements"])

    # The session may write reversibly - and still cannot press either button, because
    # the WORKER classifies the click from the element, whatever the page calls it.
    for element in (order, disguised[0]):
        error = await _refused(worker, _ref(seen, element))
        assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert "Nothing happened." in (await _observe(worker))["text"]
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_the_hello_advertises_the_observe_contract(worker) -> None:
    status = await worker._execute("browser.worker_status", {})
    assert status["contracts"]["browser.observe"] == 1


# --------------------------------------------------------------------------- #
# contract v1.7: the risk ceiling
# --------------------------------------------------------------------------- #


async def test_a_click_above_the_class_it_was_gated_at_is_refused_and_nothing_happens(
    worker, site_url
) -> None:
    """The session MAY send; the step was gated as a reversible write. The button turns
    out to be one that sends, so the worker refuses it - the step was not what it looked
    like."""
    await _open(worker, classes=(*WRITE, "EXTERNAL_COMMUNICATION", "HIGH_IMPACT"))
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    share = _by_name(seen, "Paylaş")[0]

    with pytest.raises(BrowserError) as caught:
        await worker._execute(
            "browser.click",
            {
                "session_id": "s1",
                "target": _ref(seen, share),
                "risk_ceiling": "REVERSIBLE_WRITE",
            },
        )
    assert caught.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert caught.value.evidence["reason"] == "above_ceiling"
    assert caught.value.evidence["risk_class"] == "EXTERNAL_COMMUNICATION"

    # At its own class, and with no ceiling at all (a caller from before v1.7), it runs.
    for extra in ({"risk_ceiling": "EXTERNAL_COMMUNICATION"}, {}):
        again = await _observe(worker)
        clicked = await worker._execute(
            "browser.click",
            {"session_id": "s1", "target": _ref(again, _by_name(again, "Paylaş")[0]), **extra},
        )
        assert clicked["clicked"] is True
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_ceiling_does_not_widen_what_the_session_allows(worker, site_url) -> None:
    await _open(worker)  # READ, NAVIGATE, REVERSIBLE_WRITE
    await _goto(worker, f"{site_url}/observe.html")
    seen = await _observe(worker)
    with pytest.raises(BrowserError) as caught:
        await worker._execute(
            "browser.click",
            {
                "session_id": "s1",
                "target": _ref(seen, _by_name(seen, "Siparişi tamamla")[0]),
                "risk_ceiling": "HIGH_IMPACT",
            },
        )
    assert caught.value.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert caught.value.evidence.get("reason") != "above_ceiling"
    assert (await _observe(worker))["url"].endswith("observe.html")
    await worker._execute("browser.session_close", {"session_id": "s1"})
