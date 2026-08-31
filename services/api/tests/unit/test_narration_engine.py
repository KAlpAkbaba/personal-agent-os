"""Unit tests: segmentation with stable semantic IDs, chunk planning,
ahead-of-playback synthesis, chunk cache, and cancellation of unused chunks."""

import hashlib

from app.narration.engine import (
    ChunkCache,
    Cursor,
    NarrationEngine,
    VoiceSettings,
    build_plan,
)

BODY = """# Giriş

Birinci cümle. İkinci cümle. Üçüncü cümle.

# İkinci Bölüm

Dördüncü cümle. Beşinci cümle.
"""


class FakeSynthesizer:
    """Deterministic offline TTS seam: same text+settings -> same bytes.

    Stands in for the real provider owned by app/voice; records call order so
    tests can assert what was (and was not) synthesized."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, text: str, settings: VoiceSettings) -> bytes:
        self.calls.append(text)
        return hashlib.sha256((text + "|" + settings.cache_token()).encode("utf-8")).digest()


def test_segmentation_stable_ids() -> None:
    plan = build_plan(BODY, artifact_id="a1", version=1)
    # Sections: implicit empty s1 dropped; s2 "Giriş", s3 "İkinci Bölüm".
    titles = {s.id: s.title for s in plan.sections}
    assert titles.get("s2") == "Giriş"
    assert titles.get("s3") == "İkinci Bölüm"

    # Chunk ids are "section:paragraph:sentence" and stable/deterministic.
    ids = [c.chunk_id for c in plan.chunks]
    assert "s2:p1:0" in ids  # heading "Giriş"
    assert "s2:p2:0" in ids and "s2:p2:1" in ids and "s2:p2:2" in ids  # 3 sentences
    assert "s3:p3:0" in ids  # heading "İkinci Bölüm"
    assert "s3:p4:0" in ids and "s3:p4:1" in ids

    # Rebuilding yields identical ids (stability across regeneration).
    plan2 = build_plan(BODY, artifact_id="a1", version=1)
    assert [c.chunk_id for c in plan2.chunks] == ids


def test_cursor_lookup_and_navigation() -> None:
    plan = build_plan(BODY, artifact_id="a1", version=1)
    first = plan.chunks[0].cursor
    assert plan.index_of(None) == 0
    assert plan.chunk_at(first).chunk_id == "s2:p1:0"

    # next section from within s2 lands on the first chunk of s3.
    c = Cursor("s2", "p2", 1)
    nxt = plan.next_section_cursor(c)
    assert nxt is not None
    assert nxt.section_id == "s3"

    # paragraph start cursor for repeat.
    start = plan.paragraph_start_cursor("p2")
    assert start == Cursor("s2", "p2", 0)


def test_ahead_of_play_planning_and_cache_hits() -> None:
    plan = build_plan(BODY, artifact_id="a1", version=1)
    synth = FakeSynthesizer()
    engine = NarrationEngine(synth, lookahead=2)
    settings = VoiceSettings(voice="tr-1", speed=1.0)

    first = plan.chunks[0].cursor
    results = engine.ensure_ahead(plan, first, settings)
    # window = current + 2 following = 3 chunks
    assert len(results) == 3
    assert all(not r.from_cache for r in results)
    assert len(synth.calls) == 3

    # Re-running at the same cursor is fully served from cache (no new synth).
    calls_before = len(synth.calls)
    results2 = engine.ensure_ahead(plan, first, settings)
    assert all(r.from_cache for r in results2)
    assert len(synth.calls) == calls_before


def test_cancellation_of_unused_future_chunks() -> None:
    plan = build_plan(BODY, artifact_id="a1", version=1)
    synth = FakeSynthesizer()
    engine = NarrationEngine(synth, lookahead=1)
    settings = VoiceSettings()

    start = plan.chunks[0].cursor
    engine.ensure_ahead(plan, start, settings)
    planned_first = set(engine._planned)  # noqa: SLF001 - white-box check of plan window
    assert len(planned_first) == 2  # current + 1 lookahead

    # Jump far ahead: the previously-planned early chunks are cancelled/evicted.
    far = plan.chunks[-1].cursor
    engine.ensure_ahead(plan, far, settings)
    planned_after = set(engine._planned)  # noqa: SLF001
    assert planned_first.isdisjoint(planned_after)
    # Evicted keys are gone from the cache.
    for chunk_id in planned_first - planned_after:
        key = ChunkCache.key(plan.artifact_id, plan.version, chunk_id, settings)
        assert engine.cache.get(key) is None


def test_cache_key_depends_on_voice_settings() -> None:
    k1 = ChunkCache.key("a1", 1, "s2:p2:0", VoiceSettings(voice="x", speed=1.0))
    k2 = ChunkCache.key("a1", 1, "s2:p2:0", VoiceSettings(voice="y", speed=1.0))
    k3 = ChunkCache.key("a1", 1, "s2:p2:0", VoiceSettings(voice="x", speed=1.5))
    k4 = ChunkCache.key("a1", 2, "s2:p2:0", VoiceSettings(voice="x", speed=1.0))
    assert len({k1, k2, k3, k4}) == 4  # voice, speed and version all matter


def test_table_and_code_blocks_become_single_chunks() -> None:
    body = """# Rapor

| Servis | Durum |
|---|---|
| API | Uyarı |

```
ERROR boom
```
"""
    plan = build_plan(body, artifact_id="a1", version=1)
    kinds = {c.kind for c in plan.chunks}
    assert "table" in kinds
    assert "code" in kinds
    table_chunks = [c for c in plan.chunks if c.kind == "table"]
    assert len(table_chunks) == 1
    assert "uyarı durumunda" in table_chunks[0].text
