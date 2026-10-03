"""Contract v1.8 end to end: the ceiling on a WRITE (ADR-0207 PR-C).

A real worker driving real headless Chromium. ``fill``, ``select_option`` and
``set_checked`` carry the ceiling the Cloud Core's gate allowed; the worker classifies the
element it resolved and refuses above it BEFORE anything is typed, chosen or ticked. What
this file proves that a unit test cannot: that the page really is unchanged after a
refusal - read back from the DOM, not inferred from the exception.

Evidence class: ``PROVEN_PROXY`` - a real browser, a page written by the test, not the
owner's Chrome.
"""

from __future__ import annotations

import pytest

from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.worker import Worker

pytestmark = pytest.mark.browser

ALL = ("READ", "NAVIGATE", "REVERSIBLE_WRITE", "EXTERNAL_COMMUNICATION", "HIGH_IMPACT")

PAGE = """
<h1>Ayarlar</h1>
<div id="loose">
  <input id="del-text" type="text" aria-label="Hesabı sil">
  <select id="del-select" aria-label="Hesabı sil">
    <option value="hayir">Hayır</option><option value="evet">Evet</option>
  </select>
  <input id="del-check" type="checkbox" aria-label="Hesabı sil">
  <input id="note" type="text" aria-label="Not">
  <span id="w1"><input id="bare-a" type="checkbox" data-testid="bare-a"></span>
  <span id="w2"><input id="bare-b" type="checkbox" data-testid="bare-b"></span>
</div>
<form id="f" onsubmit="return false"></form>
"""

#: (operation, role, payload field and value, how the DOM says what it holds)
WRITES = (
    ("browser.fill", "textbox", {"value": "evet"}, "document.getElementById('del-text').value"),
    (
        "browser.select_option",
        "combobox",
        {"value": "evet"},
        "document.getElementById('del-select').value",
    ),
    (
        "browser.set_checked",
        "checkbox",
        {"checked": True},
        "document.getElementById('del-check').checked",
    ),
)


async def _page(worker: Worker, site_url: str):
    await worker._execute(
        "browser.session_open",
        {
            "session_id": "s1",
            "profile": "isolated",
            "policy": {"allowed_risk_classes": list(ALL), "visible": False},
        },
    )
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{site_url}/index.html"})
    page = worker._sessions["s1"].browser_session.backend.current_page
    await page.evaluate("(html) => { document.body.innerHTML = html; }", PAGE)
    return page


async def _target(worker: Worker, role: str, name: str) -> dict:
    seen = await worker._execute("browser.observe", {"session_id": "s1"})
    found = [e for e in seen["elements"] if e["role"] == role and e["name"] == name]
    assert len(found) == 1, seen["elements"]
    return {"ref": found[0]["ref"], "observation_id": seen["observation_id"]}


def _body(result: dict) -> dict:
    """The operation's own result: ``_execute`` adds the worker's ``lifecycle`` block to
    every answer, v1.7 and v1.8 alike."""
    return {k: v for k, v in result.items() if k != "lifecycle"}


async def _refused(worker: Worker, capability: str, payload: dict) -> BrowserError:
    with pytest.raises(BrowserError) as refused:
        await worker._execute(capability, {"session_id": "s1", **payload})
    return refused.value


@pytest.mark.parametrize(("capability", "role", "body", "read"), WRITES)
async def test_a_write_above_its_ceiling_is_refused_and_the_page_is_unchanged(
    worker, site_url, capability, role, body, read
) -> None:
    page = await _page(worker, site_url)
    before = await page.evaluate(read)
    target = await _target(worker, role, "Hesabı sil")

    error = await _refused(
        worker, capability, {"target": target, "risk_ceiling": "REVERSIBLE_WRITE", **body}
    )
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert error.retryable is False
    assert error.evidence["reason"] == "above_ceiling"
    assert error.evidence["risk_class"] == "HIGH_IMPACT"
    assert error.evidence["risk_ceiling"] == "REVERSIBLE_WRITE"
    assert await page.evaluate(read) == before

    # The same control under the ceiling it really needs is written, and says its class.
    done = await worker._execute(
        capability, {"session_id": "s1", "target": target, "risk_ceiling": "HIGH_IMPACT", **body}
    )
    assert _body(done) == {"ok": True, "risk_class": "HIGH_IMPACT"}
    assert await page.evaluate(read) != before
    await worker._execute("browser.session_close", {"session_id": "s1"})


@pytest.mark.parametrize(("capability", "role", "body", "read"), WRITES)
async def test_without_a_ceiling_a_write_is_served_as_in_v1_7(
    worker, site_url, capability, role, body, read
) -> None:
    page = await _page(worker, site_url)
    before = await page.evaluate(read)
    target = await _target(worker, role, "Hesabı sil")
    done = await worker._execute(capability, {"session_id": "s1", "target": target, **body})
    assert _body(done) == {"ok": True}
    assert await page.evaluate(read) != before
    await worker._execute("browser.session_close", {"session_id": "s1"})


@pytest.mark.parametrize(("capability", "role", "body", "read"), WRITES)
async def test_a_ceiling_that_is_not_a_class_is_a_validation_error(
    worker, site_url, capability, role, body, read
) -> None:
    page = await _page(worker, site_url)
    before = await page.evaluate(read)
    target = await _target(worker, role, "Hesabı sil")
    error = await _refused(worker, capability, {"target": target, "risk_ceiling": "SAFE", **body})
    assert error.error_class == ErrorClass.VALIDATION_ERROR
    assert await page.evaluate(read) == before
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_plain_field_under_the_write_ceiling_is_written(worker, site_url) -> None:
    page = await _page(worker, site_url)
    target = await _target(worker, "textbox", "Not")
    done = await worker._execute(
        "browser.fill",
        {
            "session_id": "s1",
            "target": target,
            "value": "yarın",
            "risk_ceiling": "REVERSIBLE_WRITE",
        },
    )
    assert _body(done) == {"ok": True, "risk_class": "REVERSIBLE_WRITE"}
    assert await page.evaluate("document.getElementById('note').value") == "yarın"
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_control_rewired_into_a_form_after_it_was_seen_is_refused(worker, site_url) -> None:
    """The name stays the same (there is none); only the WIRING changes - the gap the
    fingerprint of a reference cannot see. A semantic target reaches the moved control
    and the ceiling refuses it; the twin that stayed outside the form is ticked."""
    page = await _page(worker, site_url)
    await page.evaluate(
        "document.getElementById('f').appendChild(document.getElementById('bare-a'))"
    )

    error = await _refused(
        worker,
        "browser.set_checked",
        {"target": {"test_id": "bare-a"}, "checked": True, "risk_ceiling": "REVERSIBLE_WRITE"},
    )
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert error.evidence["reason"] == "above_ceiling"
    assert error.evidence["risk_class"] == "EXTERNAL_COMMUNICATION"
    assert await page.evaluate("document.getElementById('bare-a').checked") is False

    done = await worker._execute(
        "browser.set_checked",
        {
            "session_id": "s1",
            "target": {"test_id": "bare-b"},
            "checked": True,
            "risk_ceiling": "REVERSIBLE_WRITE",
        },
    )
    assert _body(done) == {"ok": True, "risk_class": "REVERSIBLE_WRITE"}
    assert await page.evaluate("document.getElementById('bare-b').checked") is True
    await worker._execute("browser.session_close", {"session_id": "s1"})


async def test_a_referenced_control_moved_into_a_form_is_not_ticked(worker, site_url) -> None:
    """By reference the move is already refused before the ceiling is reached (the path
    the observation recorded no longer leads to it). Either way: not ticked."""
    page = await _page(worker, site_url)
    seen = await worker._execute("browser.observe", {"session_id": "s1"})
    bare = [e for e in seen["elements"] if e["role"] == "checkbox" and not e["name"]]
    assert len(bare) == 2 and not any(e["in_form"] for e in bare)
    await page.evaluate(
        "document.getElementById('f').appendChild(document.getElementById('bare-a'))"
    )
    with pytest.raises(BrowserError):
        for element in bare:
            await worker._execute(
                "browser.set_checked",
                {
                    "session_id": "s1",
                    "target": {"ref": element["ref"], "observation_id": seen["observation_id"]},
                    "checked": True,
                    "risk_ceiling": "REVERSIBLE_WRITE",
                },
            )
    assert await page.evaluate("document.getElementById('bare-a').checked") is False
    await worker._execute("browser.session_close", {"session_id": "s1"})
