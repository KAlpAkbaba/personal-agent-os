"""Owner-requested media playback (ADR-0112).

"YouTube'dan 'Doğum günün kutlu olsun Kadir' aç."

The device has been able to do this all along. ``app/alarms/sequence.py`` opens
YouTube every morning for the wake song and ``app/news/playback_service.py``
plays the latest news video, both through ``browser.session_open`` +
``browser.media_play``. What did not exist was a way for the OWNER to ask: none
of the 115 voice tools opened a web page or played a video that the owner named,
and ``tools_operator`` even instructs the model never to call ``browser.*``
itself. So the request fell through to ``capability.propose``, which wrote it
down -- "geliştirme listeme aldım" -- and nothing else happened. On 2026-09-10
the owner asked twice and watched nothing happen twice.

This package is the missing route, not a new capability:

``resolve``  turns what the owner said into ONE candidate video, from the
             device's own ``browser.search`` results. A pure function over a
             result list, so the choice is testable without a browser.
``playback`` the ONE place this reaches the device, the same discipline
             ``app.news.playback_service`` states: ``browser.session_open``
             succeeding is not proof anything is playing, and a weaker result is
             never reported as a stronger one.

The session is ``profile: "isolated"`` with ``session_kind: "media"`` -- the
worker's own refusal message names it ("use profile 'alarm', 'news' (or
'isolated')"), and it is the honest choice for an ad-hoc request: it may not
touch the ``research`` profile a live research run could be using, it must not
share the ``alarm`` profile (a song the owner asked for is not the wake song),
and ``news`` is enforced as Latest News Mode's alone. Isolated is
non-persistent, so nothing the owner plays here is kept.
"""

from __future__ import annotations

MEDIA_VERSION = 1

__all__ = ["MEDIA_VERSION"]
