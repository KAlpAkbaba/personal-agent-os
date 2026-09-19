"""Finding a named video by the PC's own OCR - the pure matcher and the mission rung around
it (ADR-0176; owner 2026-09-19: "local OCR yapsak sorun daha hızlı düzelir mi ve maddiyatı
düşer mi?", and "birden fazla pencere varsa 2 pencereye de baksın").

The lines below are shaped like the owner's real YouTube home page of 2026-09-19 17:15 (a
2576x1416 window): the site's search box and the address bar in the top band, the titles
under their thumbnails, one of them wrapped over two lines.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from app.operator import mission as mission_module
from app.operator import task as task_module
from app.operator.mission import (
    MISSION_PAUSED,
    MISSION_SUCCEEDED,
    MissionPorts,
    plan_mission,
    run_mission,
)
from app.operator.ocr_locate import find_text, fold
from app.operator.vision import FakeVisionProvider, image_mime
from app.routines.dispatch import DeviceRunResult
from tests.alarms_support import ONE_PIXEL_PNG_B64, FakeDeviceAction, ok, window_id

HEIGHT = 1416


def _line(text: str, x: int, y: int, *, word_w: int = 60, h: int = 22) -> dict[str, Any]:
    words = []
    cursor = x
    for word in text.split():
        width = max(12, len(word) * 11)
        words.append({"text": word, "x": cursor, "y": y, "width": width, "height": h})
        cursor += width + 8
    return {"text": text, "x": x, "y": y, "width": cursor - x - 8, "height": h, "words": words}


HOME = [
    _line("youtube.com", 160, 52),
    _line("Ara", 990, 140),
    _line("I Asked Every Drive Thru for their Worst Item", 310, 585),
    _line("Üç Kağıtçı Türk Filmi | 4K ULTRA HD | KEMAL SUNAL", 880, 585),
    _line("25 Quality Items You Should Own Before 25", 1450, 585),
    _line("Midnight Chicago Blues - Slow Chicago Blues & Smooth Jazz for", 2020, 585),
    _line("Midnight Blue Hours#3", 2020, 610),
    _line("EN DÜŞÜK PUANLI YAZ TATİLİ TURU!", 1120, 1330),
]


# ------------------------------------------------------------------------ the pure matcher


def test_folding_makes_the_spoken_and_the_printed_the_same_letters() -> None:
    assert fold("ÜÇ KAĞITÇI") == "uc kagitci"
    assert fold("İstanbul'da  Işık!") == "istanbul da isik"


def test_a_name_the_speech_recogniser_glued_finds_the_title_printed_in_two_words() -> None:
    """Production 2026-09-19 17:15: Chrome wrote "üçkağıtçı"; the page prints "Üç Kağıtçı"."""
    hit = find_text(HOME, "üçkağıtçı", image_height=HEIGHT)
    assert hit is not None and hit.text.startswith("Üç Kağıtçı")
    # The click lands on the NAME's own words, not in the middle of the long line.
    first_two = HOME[3]["words"][:2]
    left = first_two[0]["x"]
    right = first_two[1]["x"] + first_two[1]["width"]
    assert left <= hit.x <= right and 585 <= hit.y <= 585 + 22


def test_a_spoken_suffix_and_upper_case_do_not_hide_a_title() -> None:
    hit = find_text(HOME, "en düşük puanlı yaz tatili turu", image_height=HEIGHT)
    assert hit is not None and "TATİLİ" in hit.text


def test_a_title_wrapped_over_two_lines_is_one_title() -> None:
    hit = find_text(HOME, "Chicago Blues Midnight Blue Hours", image_height=HEIGHT)
    assert hit is not None
    assert "Midnight Chicago Blues" in hit.text and "Blue Hours" in hit.text


def test_the_owners_words_in_the_search_box_never_win_over_the_result_below() -> None:
    """After "search first" the query is printed in the address bar and the site's search
    box; clicking either opens nothing. The title lower on the page is the one meant."""
    results = [
        _line("youtube.com/results?search_query=tosun+paşa", 160, 52),
        _line("tosun paşa", 990, 140),
        _line("Tosun Paşa - RESTORASYONLU 4K FULL", 900, 420),
    ]
    hit = find_text(results, "Tosun Paşa", image_height=HEIGHT)
    assert hit is not None and hit.y >= 420 and not hit.in_top_band
    # ...and when the band is the ONLY place it is printed, that is still reported, marked.
    only_band = find_text(results[:2], "Tosun Paşa", image_height=HEIGHT)
    assert only_band is not None and only_band.in_top_band


def test_words_that_are_not_on_the_page_are_not_found() -> None:
    assert find_text(HOME, "Barış Manço Dönence", image_height=HEIGHT) is None
    assert find_text(HOME, "a", image_height=HEIGHT) is None
    assert find_text([], "Üç Kağıtçı", image_height=HEIGHT) is None


# ----------------------------------------------------------------- the rung in the mission


def _chrome(index: int, title: str, *, x: int, y: int, foreground: bool) -> dict[str, Any]:
    return {
        "window_id": window_id(index),
        "pid": 21392,
        "image": "chrome.exe",
        "title": title,
        "rect": {"x": x, "y": y, "width": 2576, "height": 1416},
        "state": "maximized",
        "foreground": foreground,
        "class_name": "Chrome_WidgetWin_1",
    }


class _Desk:
    """Two Chrome windows and the Claude window in front, as on 2026-09-19 17:15. ``texts``
    maps a window id to the lines its OCR returns; a click changes that window's title."""

    def __init__(self, texts: dict[str, list[dict[str, Any]]], *, ocr: bool = True) -> None:
        self.front = {
            "window_id": window_id(9),
            "pid": 11840,
            "image": "claude.exe",
            "title": "Claude",
            "rect": {"x": -8, "y": -8, "width": 1456, "height": 2536},
            "state": "maximized",
            "foreground": True,
            "class_name": "Chrome_WidgetWin_1",
        }
        self.first = _chrome(
            2, "Personal Agent OS - Google Chrome", x=1432, y=1129, foreground=False
        )
        self.second = _chrome(3, "(954) YouTube - Google Chrome", x=-2600, y=40, foreground=False)
        self.texts = texts
        self.ocr = ocr
        self.clicked: dict[str, Any] | None = None
        self.looks = 0
        self.formats: list[str] = []

    def device(self) -> FakeDeviceAction:
        def current(_p: dict[str, Any]) -> DeviceRunResult:
            if self.clicked is None:
                return ok(window=dict(self.front))
            self.looks += 1
            target = self._by_id(self.clicked["window_id"])
            title = "Üç Kağıtçı - YouTube - Google Chrome" if self.looks >= 2 else target["title"]
            return ok(window={**target, "title": title, "foreground": True})

        def read(p: dict[str, Any]) -> DeviceRunResult:
            if not self.ocr:
                return DeviceRunResult(False, "capability_missing", "screen.ocr is not offered")
            wid = str(p.get("window_id"))
            return ok(
                width=2576,
                height=1416,
                scale=1,
                lines=self.texts.get(wid, []),
                observed={"window": dict(self._by_id(wid))},
            )

        def capture(p: dict[str, Any]) -> DeviceRunResult:
            self.formats.append(str(p.get("format")))
            if p.get("format") == "jpeg":
                return DeviceRunResult(False, "validation_error", 'payload.format must be "png"')
            return ok(
                width=1288,
                height=708,
                scale=2,
                png_base64=ONE_PIXEL_PNG_B64,
                observed={"window": dict(self._by_id(str(p.get("window_id"))))},
            )

        def click(p: dict[str, Any]) -> DeviceRunResult:
            self.clicked = dict(p)
            x, y = int(p["x"]), int(p["y"])
            return ok(x=x, y=y, space="screen", observed={"cursor": {"x": x, "y": y}})

        return FakeDeviceAction(
            results={
                "window.current": current,
                "window.list": lambda _p: ok(
                    windows=[dict(self.front), dict(self.first), dict(self.second)]
                ),
                "window.activate": lambda p: ok(
                    window={**self._by_id(str(p.get("window_id"))), "foreground": True}
                ),
                "screen.ocr": read,
                "screen.capture": capture,
                "pointer.click": click,
            }
        )

    def _by_id(self, wid: str) -> dict[str, Any]:
        for window in (self.front, self.first, self.second):
            if window["window_id"] == wid:
                return window
        raise AssertionError(wid)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_module, "_sleep", lambda _s: None)


def test_the_video_is_found_by_ocr_on_the_owners_other_chrome_window_and_clicked_there() -> None:
    """The Claude window was in front and the video was on the SECOND Chrome window. Every
    browser window is read; the click is a SCREEN point - the window's own position added
    (that second window sits at x=-2600 on another monitor) - and no picture is paid for."""
    desk = _Desk({window_id(3): HOME})
    device = desk.device()
    vision = FakeVisionProvider(location=(5, 5))
    m = plan_mission("üçkağıtçı videosunu aç")
    run_mission(m, MissionPorts(device=device, vision=vision))

    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert desk.clicked is not None and desk.clicked["window_id"] == window_id(3)
    hit = find_text(HOME, "üçkağıtçı", image_height=HEIGHT)
    assert hit is not None
    assert (desk.clicked["x"], desk.clicked["y"]) == (-2600 + hit.x, 40 + hit.y)
    assert vision.image_sizes == [], "the paid picture rung was used although OCR answered"
    assert "screen.capture" not in device.capabilities_called()
    assert m.steps[0].args.get("located_by") == "ocr"


def test_a_named_video_ocr_read_and_did_not_find_is_searched_for_without_a_paid_look() -> None:
    desk = _Desk({window_id(3): HOME})
    device = desk.device()
    vision = FakeVisionProvider(location=(5, 5))
    m = plan_mission("Barış Manço Dönence videosunu aç")
    run_mission(m, MissionPorts(device=device, vision=vision))
    # It is searched for on the window that IS on YouTube (the second one), through the
    # address bar - and never by paying for a picture of a page OCR already read.
    # (This desk scripts no keyboard, so the search stops at its first keystroke; what is
    # pinned here is WHICH window it went to and that nothing was paid for.)
    activated = [c["payload"] for c in device.calls if c["capability"] == "window.activate"]
    assert activated and activated[0]["window_id"] == window_id(3), device.capabilities_called()
    assert m.steps[0].args.get("searched") is True
    assert vision.image_sizes == []
    assert m.status == MISSION_PAUSED


def test_a_device_without_ocr_falls_back_to_the_picture_asking_for_jpeg_then_png() -> None:
    """An agent that predates ADR-0176: no screen.ocr, and "format": "jpeg" is a validation
    error. The PNG it knows is asked for next, and the picture's point becomes a screen point
    with the capture's scale AND the window's position."""
    desk = _Desk({}, ocr=False)
    device = desk.device()
    vision = FakeVisionProvider(location=(100, 50))
    m = plan_mission("üçkağıtçı videosunu aç")
    run_mission(m, MissionPorts(device=device, vision=vision))

    assert m.status == MISSION_SUCCEEDED, m.as_dict()
    assert desk.formats[:2] == ["jpeg", "png"]
    # first browser window read is the one at (1432, 1129); scale 2.
    assert (desk.clicked["x"], desk.clicked["y"]) == (1432 + 200, 1129 + 100)


def test_a_position_on_the_screen_is_the_pictures_question_never_ocrs() -> None:
    desk = _Desk({window_id(2): HOME, window_id(3): HOME})
    device = desk.device()
    m = plan_mission("sağdan üçüncü videoyu aç")
    assert m.steps[0].args.get("positional") is True
    run_mission(m, MissionPorts(device=device, vision=FakeVisionProvider(location=(10, 10))))
    assert "screen.ocr" not in device.capabilities_called()


def test_the_provider_is_told_the_pictures_own_type() -> None:
    assert image_mime(bytes((0xFF, 0xD8, 0xFF, 0xE0))) == "image/jpeg"
    assert image_mime(base64.b64decode(ONE_PIXEL_PNG_B64)) == "image/png"
    assert mission_module.MAX_WINDOWS_PER_LOOK >= 2


# ------------------------------------------- production 2026-09-19 17:38: the neighbour opened


def test_the_page_that_opened_must_carry_the_name() -> None:
    from app.operator.ocr_locate import title_names

    assert title_names(
        "Üç Kağıtçı Türk Filmi | 4K ULTRA HD | KEMAL SUNAL - YouTube", "üçkağıtçı Türk filmi"
    )
    assert title_names("(954) Tosun Paşa - RESTORASYONLU 4K FULL - YouTube", "tosun paşa")
    assert not title_names(
        "(954) 25 Quality Items You Should Own Before 25 - YouTube", "üçkağıtçı Türk filmi"
    )
    assert not title_names("", "üç kağıtçı")


def test_a_click_that_opens_the_neighbours_video_is_gone_back_from_and_never_called_done() -> None:
    """Asked for "üçkağıtçı Türk filmi", the click opened "25 Quality Items You Should Own
    Before 25" and the mission said succeeded - the title had merely CHANGED. Now: the proof
    fails, the mission goes back once, looks again, and a second wrong page stops for the
    owner as wrong_target."""
    home = dict(_chrome(2, "(954) YouTube - Google Chrome", x=1432, y=1129, foreground=True))
    wrong_title = "(954) 25 Quality Items You Should Own Before 25 - YouTube - Google Chrome"
    state = {"title": home["title"], "clicks": 0, "backs": 0}

    def window() -> dict[str, Any]:
        return {**home, "title": state["title"]}

    def click(p: dict[str, Any]) -> DeviceRunResult:
        state["clicks"] += 1
        state["title"] = wrong_title
        x, y = int(p["x"]), int(p["y"])
        return ok(x=x, y=y, space="screen", observed={"cursor": {"x": x, "y": y}})

    def chord(p: dict[str, Any]) -> DeviceRunResult:
        if p.get("keys") == ["alt", "left"]:
            state["backs"] += 1
            state["title"] = home["title"]
        return ok(keys=p.get("keys"), window_id=p.get("window_id"), observed={"window": window()})

    device = FakeDeviceAction(
        results={
            "window.current": lambda _p: ok(window=window()),
            "window.list": lambda _p: ok(windows=[window()]),
            "window.activate": lambda _p: ok(window=window()),
            "screen.ocr": lambda _p: ok(
                width=2576, height=1416, scale=1, lines=HOME, observed={"window": window()}
            ),
            "pointer.click": click,
            "keyboard.shortcut": chord,
        }
    )
    m = plan_mission("şu an sekmedeki üçkağıtçı Türk filmini aç")
    run_mission(m, MissionPorts(device=device, vision=FakeVisionProvider(location=(1, 1))))

    assert m.status == MISSION_PAUSED, m.as_dict()
    assert m.steps[0].error_class == "wrong_target"
    assert state["backs"] == 1 and state["clicks"] == 2
    assert state["title"] == wrong_title  # said as it is; never reported as opened


def test_a_film_is_the_screens_only_when_the_sentence_places_it_there() -> None:
    """ "şu an sekmedeki üçkağıtçı Türk filmini aç" is a thing on this tab; "Esaretin Bedeli
    filmini aç" alone is the media player's (corpus m.play.film) and is not planned here."""
    from app.operator.mission import MissionClarificationNeeded

    placed = plan_mission("şu an sekmedeki üçkağıtçı Türk filmini aç")
    assert placed.steps[0].kind == "click_text"
    assert placed.steps[0].args == {"name": "üçkağıtçı Türk", "search_if_missing": True}
    with pytest.raises(MissionClarificationNeeded):
        plan_mission("Esaretin Bedeli filmini aç")
