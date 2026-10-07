"""Contract v1.9: a cloud task WRITES, but only on the owner's allow-listed sites.

The owner's rule (ADR-0213 addendum, 2026-09-30, option 4): the cloud reads everywhere and
acts only on the sites the owner listed. Two keepers hold it, each on its own side:

* the cloud device's clamp (``browser_agent.cloud.policy.clamp_command``) lets a
  ``session_open`` that says ``cloud_task: true`` and carries ``owner_allow_list`` reach
  ``REVERSIBLE_WRITE`` - never ``EXTERNAL_COMMUNICATION`` or ``HIGH_IMPACT``;
* the worker (``browser_agent.worker``) runs every write of such a session only when the
  page's CURRENT address is on that list (a subdomain counts) and not on the deny-list.

The last test drives a real headless Chromium (``-m browser``): a fill on a listed page is
made and read back, the same fill on an unlisted page is refused and leaves it empty.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from browser_agent import cloud_allowlist, worker
from browser_agent.cloud import policy as cloud_policy
from browser_agent.errors import BrowserError, ErrorClass
from browser_agent.policy import RiskClass
from browser_agent.worker import Worker

SESSION_ID = "0b6f3c0e-5f0a-4d57-9a41-3f2f3d1f8a10"
THREE = ["READ", "NAVIGATE", "REVERSIBLE_WRITE"]


def _cloud_open(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": SESSION_ID,
        "profile": "research",
        "channel": "chromium",
        "policy": {"allowed_risk_classes": list(THREE), "visible": False},
        "cloud_task": True,
        "owner_allow_list": ["magaza.com.tr"],
    }
    payload.update(overrides)
    return payload


def _refusal(call: Any, *args: Any) -> BrowserError:
    with pytest.raises(BrowserError) as caught:
        call(*args)
    return caught.value


# ------------------------------------------------------------------ S2: the clamp


def test_a_cloud_task_may_reach_reversible_write() -> None:
    out = cloud_policy.clamp_command("browser.session_open", _cloud_open())
    assert out["policy"]["allowed_risk_classes"] == sorted(THREE)
    assert out["policy"]["visible"] is False and out["channel"] == "chromium"
    # Both new fields reach the worker as they were sent.
    assert out["cloud_task"] is True and out["owner_allow_list"] == ["magaza.com.tr"]


def test_an_empty_owner_list_is_still_a_cloud_task() -> None:
    out = cloud_policy.clamp_command("browser.session_open", _cloud_open(owner_allow_list=[]))
    assert out["owner_allow_list"] == []


@pytest.mark.parametrize("wider", ["EXTERNAL_COMMUNICATION", "HIGH_IMPACT"])
def test_a_cloud_task_never_sends_or_deletes(wider: str) -> None:
    payload = _cloud_open(policy={"allowed_risk_classes": [*THREE, wider], "visible": False})
    error = _refusal(cloud_policy.clamp_command, "browser.session_open", payload)
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR and error.retryable is False
    assert error.evidence["refused"] == [wider]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"cloud_task": False}, id="cloud_task-false"),
        pytest.param({}, id="v1.8-payload"),
    ],
)
def test_without_cloud_task_the_session_stays_read_and_navigate(payload: dict) -> None:
    sent = {
        "session_id": SESSION_ID,
        "profile": "research",
        "policy": {"allowed_risk_classes": list(THREE)},
        **payload,
    }
    error = _refusal(cloud_policy.clamp_command, "browser.session_open", sent)
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR
    assert error.evidence["refused"] == ["REVERSIBLE_WRITE"]


def test_a_v1_8_payload_is_clamped_exactly_as_before() -> None:
    sent = {"session_id": SESSION_ID, "profile": "research", "policy": {"visible": True}}
    out = cloud_policy.clamp_command("browser.session_open", sent)
    assert out["policy"] == {"allowed_risk_classes": ["NAVIGATE", "READ"], "visible": False}
    assert "cloud_task" not in out and "owner_allow_list" not in out


def test_a_cloud_task_without_a_list_is_refused() -> None:
    payload = _cloud_open()
    del payload["owner_allow_list"]
    error = _refusal(cloud_policy.clamp_command, "browser.session_open", payload)
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR


@pytest.mark.parametrize(
    "flag",
    [pytest.param({}, id="no-cloud_task"), pytest.param({"cloud_task": False}, id="false")],
)
def test_a_list_without_cloud_task_is_refused(flag: dict[str, Any]) -> None:
    # READ + NAVIGATE only: the refusal must come from the stray list itself, not from a
    # class the session may not hold (the inspector's m6 survived the three-class version).
    payload = _cloud_open(policy={"allowed_risk_classes": ["READ", "NAVIGATE"]}, **flag)
    if not flag:
        del payload["cloud_task"]
    error = _refusal(cloud_policy.clamp_command, "browser.session_open", payload)
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR and error.retryable is False
    assert "refused" not in (error.evidence or {})
    assert "owner_allow_list belongs to a cloud task only" in str(error)


def test_the_owners_profile_is_still_not_reachable_from_the_cloud() -> None:
    error = _refusal(
        cloud_policy.clamp_command, "browser.session_open", _cloud_open(profile="owner")
    )
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR


@pytest.mark.parametrize(
    "bad",
    [
        "Bank.com",
        "co.example",
        "",
        "com",
        "magaza.com.tr/x",
        " magaza.com.tr",
        "www.magaza.com.tr",
        7,
    ],
)
def test_a_malformed_site_in_the_list_is_refused_not_skipped(bad: Any) -> None:
    payload = _cloud_open(owner_allow_list=["ok.example", bad])
    error = _refusal(cloud_policy.clamp_command, "browser.session_open", payload)
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR and error.retryable is False


@pytest.mark.parametrize("not_a_list", ["magaza.com.tr", {"magaza.com.tr": 1}, None])
def test_the_list_must_be_a_list(not_a_list: Any) -> None:
    error = _refusal(
        cloud_policy.clamp_command, "browser.session_open", _cloud_open(owner_allow_list=not_a_list)
    )
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR


@pytest.mark.parametrize("capability", ["browser.media_status", "browser.media_play"])
def test_the_media_operations_pass_the_clamp_untouched(capability: str) -> None:
    """T4 on the cloud: reading a video's clock is READ, starting it NAVIGATE - the clamp
    has nothing to cut from them."""
    payload = {"session_id": SESSION_ID, "url": "https://www.youtube.com/watch?v=x"}
    assert cloud_policy.clamp_command(capability, payload) is payload


# ------------------------------------------------------------------ S3: the worker


def _state(url: str, owner_allow_list: tuple[str, ...] | None) -> Any:
    page = SimpleNamespace(url=url)
    written: list[tuple[str, Any]] = []

    async def fill(spec: Any, value: str, frame: Any = None) -> None:
        written.append(("fill", value))

    async def select_option(spec: Any, value: str, frame: Any = None) -> None:
        written.append(("select_option", value))

    async def set_checked(spec: Any, checked: bool, frame: Any = None) -> None:
        written.append(("set_checked", checked))

    return SimpleNamespace(
        owner_allow_list=owner_allow_list,
        cloud_task=owner_allow_list is not None,
        policy_allowed=frozenset(RiskClass),
        written=written,
        browser_session=SimpleNamespace(
            backend=SimpleNamespace(current_page=page),
            fill=fill,
            select_option=select_option,
            set_checked=set_checked,
        ),
    )


WRITES = (
    ("fill", {"value": "Ada"}),
    ("select_option", {"value": "mavi"}),
    ("set_checked", {"checked": True}),
)
TARGET = {"role": "textbox", "name": "Ad"}


def _write(state: Any, name: str, extra: dict) -> dict:
    op = getattr(Worker, f"_op_{name}")
    return asyncio.run(op(object.__new__(Worker), state, {"target": TARGET, **extra}))


#: (current url, the session's list, error reason or None when the write is made)
TABLE = (
    ("https://magaza.com.tr/sepet", ("magaza.com.tr",), None),
    ("https://odeme.magaza.com.tr/adres", ("magaza.com.tr",), None),
    ("https://www.trendyol.com/", ("magaza.com.tr",), "not_on_owner_allow_list"),
    ("https://magaza.com.tr.evil.example/", ("magaza.com.tr",), "not_on_owner_allow_list"),
    ("https://notmagaza.com.tr/", ("magaza.com.tr",), "not_on_owner_allow_list"),
    ("https://magaza.com.tr/", (), "not_on_owner_allow_list"),
    ("about:blank", ("magaza.com.tr",), "not_on_owner_allow_list"),
)


@pytest.mark.parametrize(("name", "extra"), WRITES)
@pytest.mark.parametrize(("url", "listed", "reason"), TABLE)
def test_a_cloud_write_runs_only_on_a_listed_site(
    name: str, extra: dict, url: str, listed: tuple[str, ...], reason: str | None
) -> None:
    state = _state(url, listed)
    if reason is None:
        assert _write(state, name, extra) == {"ok": True}
        assert len(state.written) == 1
        return
    with pytest.raises(BrowserError) as caught:
        _write(state, name, extra)
    error = caught.value
    assert error.error_class == ErrorClass.SECURITY_SCOPE_ERROR and error.retryable is False
    assert error.evidence == {"reason": reason, "site": cloud_allowlist.site_of(url)}
    assert state.written == []


def test_the_site_in_the_evidence_is_the_registrable_one() -> None:
    error = _refusal(
        worker._refuse_unless_owner_allow_listed,
        _state("https://www.hepsiburada.com/x?kart=4111", ("magaza.com.tr",)),
        "browser.fill",
    )
    assert error.evidence == {"reason": "not_on_owner_allow_list", "site": "hepsiburada.com"}
    assert "4111" not in error.message


@pytest.mark.parametrize(("name", "extra"), WRITES)
def test_a_bank_on_the_owners_list_is_still_refused_by_the_deny_list_first(
    name: str, extra: dict
) -> None:
    state = _state("https://sube.isbank.com.tr/hesap", ("isbank.com.tr",))
    with pytest.raises(BrowserError) as caught:
        _write(state, name, extra)
    # The v1.7 deny-list refusal comes first, unchanged.
    assert caught.value.evidence["reason"] == "denied_site"
    assert state.written == []


def test_the_keeper_itself_asks_the_deny_list_before_the_owners_list() -> None:
    error = _refusal(
        worker._refuse_unless_owner_allow_listed,
        _state("https://sube.isbank.com.tr/hesap", ("isbank.com.tr",)),
        "browser.fill",
    )
    assert error.evidence == {"reason": "deny_listed_site", "site": "isbank.com.tr"}


@pytest.mark.parametrize(("name", "extra"), WRITES)
def test_a_session_without_a_list_writes_as_today(name: str, extra: dict) -> None:
    """The owner's Chrome and every device session: no list, no new check."""
    state = _state("https://www.trendyol.com/", None)
    assert _write(state, name, extra) == {"ok": True}
    assert worker._refuse_unless_owner_allow_listed(state, "browser.fill") is None


def test_the_seed_list_and_the_sessions_list_are_one_union(monkeypatch) -> None:
    monkeypatch.setattr(cloud_allowlist, "SITES", ("tohum.example",))
    state = _state("https://tohum.example/form", ("magaza.com.tr",))
    assert _write(state, "fill", {"value": "x"}) == {"ok": True}
    assert cloud_allowlist.acting_allowed(
        "https://magaza.com.tr/", extra_sites=("magaza.com.tr",)
    ) == (
        True,
        "",
    )
    assert cloud_allowlist.acting_allowed("https://magaza.com.tr/") == (
        False,
        "not_on_owner_allow_list",
    )


def test_an_external_communication_fill_is_above_a_cloud_ceiling() -> None:
    """The v1.8 ceiling stays: a field that submits is EXTERNAL_COMMUNICATION, and the
    cloud's step is gated at REVERSIBLE_WRITE at most."""
    from browser_agent.policy import classify_write, enforce_ceiling

    risk = classify_write("Yorumu yayınla", True, True, "fill")
    assert risk == RiskClass.EXTERNAL_COMMUNICATION
    error = _refusal(lambda: enforce_ceiling(risk, "REVERSIBLE_WRITE", capability="browser.fill"))
    assert error.evidence["reason"] == "above_ceiling"


# ------------------------------------------------------------------ the click


class _Locator:
    def __init__(self, described: dict[str, Any]) -> None:
        self.described = described
        self.first = self
        self.clicks = 0

    async def wait_for(self, **_: Any) -> None:
        return None

    async def evaluate(self, _js: str) -> dict[str, Any]:
        return self.described

    async def click(self, **_: Any) -> None:
        self.clicks += 1


class _Spec:
    def __init__(self, locator: _Locator) -> None:
        self.locator = locator
        self.ref = None

    def to_locator(self, _root: Any) -> _Locator:
        return self.locator

    def as_dict(self) -> dict[str, Any]:
        return {"role": "button"}


def _click(url: str, described: dict[str, Any], listed: tuple[str, ...] | None) -> _Locator:
    locator = _Locator(described)
    state = _state(url, listed)
    instance = object.__new__(Worker)

    async def bound(_state: Any, _target: Any) -> _Spec:
        return _Spec(locator)

    instance._bound_target = bound  # type: ignore[method-assign]
    asyncio.run(Worker._op_click(instance, state, {"target": {"role": "button", "name": "x"}}))
    return locator


PLAIN_BUTTON = {"tag": "button", "role": "button", "name": "Renk: mavi"}
LINK = {"tag": "a", "role": "link", "name": "Kampanyalar", "has_href": True}


def test_a_reversible_click_off_the_list_is_refused() -> None:
    with pytest.raises(BrowserError) as caught:
        _click("https://www.trendyol.com/", PLAIN_BUTTON, ("magaza.com.tr",))
    assert caught.value.evidence == {"reason": "not_on_owner_allow_list", "site": "trendyol.com"}


def test_a_reversible_click_on_the_list_is_made() -> None:
    assert _click("https://magaza.com.tr/", PLAIN_BUTTON, ("magaza.com.tr",)).clicks == 1


def test_a_navigating_click_does_not_ask_the_list() -> None:
    """Following a link is NAVIGATE: the cloud does that everywhere."""
    assert _click("https://www.trendyol.com/", LINK, ("magaza.com.tr",)).clicks == 1
    assert _click("https://www.trendyol.com/", LINK, ()).clicks == 1


# ------------------------------------------------------------------ the session carries it


def test_session_state_carries_the_list_and_the_flag() -> None:
    fields = worker.SessionState.__dataclass_fields__
    assert fields["owner_allow_list"].default is None
    assert fields["cloud_task"].default is False


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"owner_allow_list": "magaza.com.tr"}, id="list-is-a-string"),
        pytest.param({"owner_allow_list": ["magaza.com.tr", 3]}, id="item-not-a-string"),
        pytest.param({"owner_allow_list": ["Magaza.com.tr"]}, id="item-not-lower-case"),
        pytest.param({"owner_allow_list": [""]}, id="item-empty"),
        pytest.param({"cloud_task": "yes"}, id="flag-not-a-bool"),
    ],
)
def test_the_worker_refuses_a_malformed_list_before_it_launches(tmp_path, bad: dict) -> None:
    from browser_agent.worker import build_arg_parser

    instance = Worker(build_arg_parser().parse_args(["--data-dir", str(tmp_path)]))
    payload = {"session_id": "s1", "profile": "isolated", "policy": {}, **bad}
    with pytest.raises(BrowserError) as caught:
        asyncio.run(instance._execute("browser.session_open", payload))
    assert caught.value.error_class == ErrorClass.VALIDATION_ERROR


# ------------------------------------------------------------------ real headless Chromium

ALL = ("READ", "NAVIGATE", "REVERSIBLE_WRITE", "EXTERNAL_COMMUNICATION", "HIGH_IMPACT")


@pytest.mark.browser
async def test_real_chromium_writes_on_the_listed_page_and_refuses_off_it(worker, site_url) -> None:
    """The fixture site is served on 127.0.0.1; the same page under ``localhost`` is
    another site. The session lists ``127.0.0.1`` only."""
    await worker._execute(
        "browser.session_open",
        {
            "session_id": "s1",
            "profile": "isolated",
            "policy": {"allowed_risk_classes": list(ALL), "visible": False},
            "cloud_task": True,
            "owner_allow_list": ["127.0.0.1"],
        },
    )
    target = {"role": "textbox", "name": "Full name"}
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{site_url}/form.html"})
    done = await worker._execute(
        "browser.fill", {"session_id": "s1", "target": target, "value": "Ada Lovelace"}
    )
    assert done["ok"] is True
    page = worker._sessions["s1"].browser_session.backend.current_page
    assert await page.evaluate("document.getElementById('fname').value") == "Ada Lovelace"

    other = site_url.replace("127.0.0.1", "localhost")
    await worker._execute("browser.navigate", {"session_id": "s1", "url": f"{other}/form.html"})
    with pytest.raises(BrowserError) as refused:
        await worker._execute(
            "browser.fill", {"session_id": "s1", "target": target, "value": "Ada Lovelace"}
        )
    assert refused.value.evidence == {"reason": "not_on_owner_allow_list", "site": "localhost"}
    page = worker._sessions["s1"].browser_session.backend.current_page
    assert await page.evaluate("document.getElementById('fname').value") == ""


VIDEO_PAGE = """
<video id="v" muted autoplay playsinline width="64" height="48"></video>
<canvas id="c" width="64" height="48"></canvas>
<script>
  const c = document.getElementById('c'); const g = c.getContext('2d'); let n = 0;
  setInterval(() => { g.fillStyle = n++ % 2 ? '#123' : '#abc'; g.fillRect(0, 0, 64, 48); }, 40);
  const v = document.getElementById('v'); v.srcObject = c.captureStream(25); v.play();
</script>
"""


@pytest.mark.browser
async def test_real_headless_chromium_reports_a_moving_media_clock(worker, site_url) -> None:
    """T4's proof without a sound device: ``media_status`` reads ``currentTime`` and it
    moves. A canvas stream stands in for the video file the fixture site does not have."""
    await worker._execute(
        "browser.session_open",
        {
            "session_id": "m1",
            "profile": "isolated",
            "session_kind": "media",
            "policy": {"allowed_risk_classes": ["READ", "NAVIGATE"], "visible": False},
        },
    )
    await worker._execute("browser.navigate", {"session_id": "m1", "url": f"{site_url}/index.html"})
    page = worker._sessions["m1"].browser_session.backend.current_page
    await page.evaluate(
        "(html) => { document.body.innerHTML = html;"
        " for (const s of document.body.querySelectorAll('script')) {"
        " const t = document.createElement('script'); t.textContent = s.textContent;"
        " s.replaceWith(t); } }",
        VIDEO_PAGE,
    )
    first = await worker._execute("browser.media_status", {"session_id": "m1"})
    await asyncio.sleep(1.5)
    second = await worker._execute("browser.media_status", {"session_id": "m1"})
    assert first["present"] is True and second["playing"] is True
    assert second["current_time_s"] > first["current_time_s"] + 0.5, (first, second)
