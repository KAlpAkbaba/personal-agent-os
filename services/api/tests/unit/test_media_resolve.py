"""Which video gets played (ADR-0112) — the pure half, no browser, no database.

What the owner hears is decided here, so this is where the decision is pinned.
"""

from __future__ import annotations

import pytest

from app.media.resolve import (
    canonical_url,
    pick_video,
    search_query,
    video_id_from_url,
)

VIDEO = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO}",
        f"https://youtube.com/watch?v={VIDEO}",
        f"https://m.youtube.com/watch?v={VIDEO}",
        f"https://music.youtube.com/watch?v={VIDEO}",
        f"https://www.youtube.com/watch?v={VIDEO}&list=PL123&index=4",
        f"https://youtu.be/{VIDEO}",
        f"https://youtu.be/{VIDEO}?t=30",
    ],
)
def test_a_real_watch_url_yields_its_video_id(url: str) -> None:
    assert video_id_from_url(url) == VIDEO


@pytest.mark.parametrize(
    "url",
    [
        # A lookalike host. ``"youtube.com" in host`` is TRUE for this one, which is the
        # substring-host defect the M26 news review already found in this repository;
        # the check is against the parsed hostname, so it is refused.
        f"https://youtube.com.evil.tld/watch?v={VIDEO}",
        f"https://notyoutube.com/watch?v={VIDEO}",
        # Not a watch page: media_play proves a <video> element advanced, and none of
        # these has one, so playing them would produce an honest failure the owner
        # hears as "I could not verify it" — a worse answer than "I could not find it".
        f"https://www.youtube.com/shorts/{VIDEO}",
        "https://www.youtube.com/@somechannel",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/results?search_query=tarkan",
        "https://www.youtube.com/watch",
        # Wrong id shape.
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=" + "x" * 40,
        # Not a URL at all.
        "javascript:alert(1)",
        "file:///C:/Windows/System32/calc.exe",
        "",
        None,
        123,
    ],
)
def test_anything_that_is_not_a_watch_page_is_refused(url: object) -> None:
    assert video_id_from_url(url) is None


def test_the_url_played_is_rebuilt_from_the_id_not_taken_from_the_engine() -> None:
    """A SERP link carries tracking parameters and redirect wrappers, and the device
    validates whatever it is handed against its own destination policy. The id is the
    only part worth keeping."""
    tracked = f"https://www.youtube.com/watch?v={VIDEO}&utm_source=serp&pp=ygU"
    chosen = pick_video([{"rank": 1, "url": tracked, "title": "Şarkı"}])
    assert chosen is not None
    assert chosen.url == canonical_url(VIDEO) == f"https://www.youtube.com/watch?v={VIDEO}"
    assert "utm_source" not in chosen.url


def test_the_first_real_video_wins_in_the_engines_own_order() -> None:
    """No re-ranking. The engine ranked them; a second opinion here would be this
    module inventing a judgement it cannot defend, and the owner can say "diğeri"."""
    chosen = pick_video(
        [
            {"rank": 1, "url": "https://www.youtube.com/@channel", "title": "kanal"},
            {"rank": 2, "url": "https://example.com/lyrics", "title": "sözler"},
            {"rank": 3, "url": f"https://youtu.be/{VIDEO}", "title": "İkinci"},
            {"rank": 4, "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "Sonra"},
        ]
    )
    assert chosen is not None
    assert chosen.video_id == VIDEO
    assert chosen.rank == 3
    assert chosen.title == "İkinci"


def test_no_video_at_all_is_none_rather_than_the_nearest_link() -> None:
    assert (
        pick_video(
            [
                {"rank": 1, "url": "https://example.com/lyrics", "title": "sözler"},
                {"rank": 2, "url": "https://www.youtube.com/@channel", "title": "kanal"},
            ]
        )
        is None
    )
    assert pick_video([]) is None
    assert pick_video(None) is None
    assert pick_video("results") is None
    assert pick_video([None, 5, {"url": None}]) is None


def test_the_search_query_adds_youtube_once() -> None:
    assert search_query("Doğum günün kutlu olsun Kadir") == "Doğum günün kutlu olsun Kadir youtube"
    # ...and not twice, when the owner already said it.
    assert search_query("youtube'dan tarkan") == "youtube'dan tarkan"
    assert search_query("  boşluklu   söz  ") == "boşluklu söz youtube"
    assert search_query("") == "youtube"
