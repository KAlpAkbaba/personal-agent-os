"""The memory's word leg on the REAL PostgreSQL (card memory-lexical-turkish-rrf).

What SQLite cannot show: pg_trgm's ``<%`` and ``word_similarity`` on the folded text, the
``turkish`` text search, PostgreSQL's own ``lower`` against Python's Turkish fold, the GIN
indexes the migration builds, and that the migration goes up, down and up again on the
``pgvector/pgvector:pg16`` image.

Every test's memories carry one conversation id of their own and are filtered on it, so the
rest of the dev database never takes part; every memory is forgotten when its test ends.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.config import Settings
from app.memory import lexical, service
from app.memory.embedding import DeterministicEmbedder
from app.memory.retrieval import RetrievalFilters, hybrid_search, keyword_candidates
from app.memory.runtime import MemoryRuntime
from app.memory.service import MemoryLinks
from app.memory.types import Actor, MemoryClass
from tests.integration.conftest import API_ROOT
from tests.integration.migration_ids import head, parent_of, revision_named

pytestmark = pytest.mark.integration

EMBEDDER = DeterministicEmbedder()
AHMET_QUERY = "Ahmet'e ne söz vermiştim?"
#: The same 59 look-alikes as the unit twin (tests/unit/test_memory_lexical.py): each nearer
#: the query by meaning than the memory naming Ahmet, none holding a query word.
DISTRACTOR_WORDS = (
    "kahve toplantı bahçe fatura araba tatil kira market doktor spor kitap film kombi perde "
    "halı balkon çanta gözlük şemsiye telefon priz lamba masa sandalye dolap bisiklet kedi "
    "köpek çiçek fırın ütü çaydanlık tencere tabak bardak kaşık çatal bıçak havlu sabun yastık "
    "yorgan battaniye ayakkabı mont kazak gömlek pantolon etek şapka eldiven atkı çorap kemer "
    "saat yüzük kolye küpe cüzdan"
).split()
FOLD_INDEX = "ix_memories_text_fold_trgm"
TSV_INDEX = "ix_memories_text_tsv_tr"


@pytest.fixture(scope="module")
def runtime() -> MemoryRuntime:
    return MemoryRuntime(Settings())


class Corpus:
    """Memories of one conversation; forgets them all on ``close``."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.conversation = uuid.uuid4()
        self.ids: list[uuid.UUID] = []
        self.token = uuid.uuid4().hex[:10]

    def remember(self, text: str) -> uuid.UUID:
        memory_id = service.remember_explicit(
            self.session,
            EMBEDDER,
            text=text,
            memory_class=MemoryClass.SEMANTIC,
            links=MemoryLinks(conversation_id=self.conversation),
        ).memory_id
        self.ids.append(memory_id)
        return memory_id

    @property
    def filters(self) -> RetrievalFilters:
        return RetrievalFilters(conversation_id=self.conversation)

    def forget(self, memory_id: uuid.UUID) -> None:
        service.forget_memory(self.session, memory_id, actor=Actor.OWNER)
        self.ids.remove(memory_id)

    def close(self) -> None:
        for memory_id in list(self.ids):
            self.forget(memory_id)


@pytest.fixture()
def corpus(runtime: MemoryRuntime) -> Iterator[Corpus]:
    with runtime.session() as session:
        made = Corpus(session)
        try:
            yield made
        finally:
            session.rollback()
            made.close()


def _mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv(lexical.MODE_ENV, mode)


# --------------------------------------------------------------------------- acceptance 1


def test_a_name_said_once_comes_back_in_trgm_mode_only(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = corpus.remember(
        "Ahmet kitabını cumartesi sabahı getirecek; bahçe kapısı tamir edilecek, faturalar "
        f"ödenecek, kombi bakımı yapılacak ({corpus.token})."
    )
    for word in DISTRACTOR_WORDS:
        corpus.remember(f"ne e {word}")
    assert len(corpus.ids) == 60

    _mode(monkeypatch, "like")
    like = hybrid_search(corpus.session, EMBEDDER, AHMET_QUERY, corpus.filters, k=60)
    assert target not in [h.memory.id for h in like[:3]], "today's defect is gone in 'like'?"

    _mode(monkeypatch, "trgm")
    fused = hybrid_search(corpus.session, EMBEDDER, AHMET_QUERY, corpus.filters, k=10)
    assert target in [h.memory.id for h in fused[:3]]
    assert fused[0].memory.id == target


# --------------------------------------------------------------------------- acceptance 2


def test_postgres_lower_folds_like_python(runtime: MemoryRuntime) -> None:
    spellings = ["İzmir", "IZMIR", "izmir", "İZMİR", "ızmır", "Işık", "ışık"]
    with runtime.session() as session:
        for spelling in spellings:
            folded = session.execute(
                sql_text(f"SELECT {lexical.FOLD_SQL} FROM (SELECT CAST(:t AS text) AS text) s"),
                {"t": spelling},
            ).scalar_one()
            assert folded == lexical.fold(spelling), spelling


@pytest.mark.parametrize("query", ["İzmir", "IZMIR", "izmir", "İZMİR'e"])
def test_every_spelling_finds_the_izmir_memory(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    _mode(monkeypatch, "trgm")
    target = corpus.remember(f"Annemle İzmir'e cumartesi gideceğiz ({corpus.token}).")
    corpus.remember(f"Ankara'daki toplantı ertelendi ({corpus.token}).")
    found = keyword_candidates(corpus.session, query, corpus.filters)
    assert [m.id for m in found] == [target]


# --------------------------------------------------------------------------- acceptance 4


@pytest.mark.parametrize(
    ("memory_text", "query"),
    [
        ("Ahmet'e kitabı geri vereceğim", "Ahmet ne istedi"),
        ("Ahmet kitabı geri istedi", "Ahmet'e ne demiştim"),
        ("Ahmet kitabı geri istedi", "Ahmete ne demiştim"),
        ("Komşuya bahçeyi sulama sözü verdim", "komşuya ne vermiştim"),
        ("Dün bahçeyi sulayacağımı sana verdim", "neyi vermiştim"),
    ],
)
def test_suffixed_words_meet_in_trgm_mode(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch, memory_text: str, query: str
) -> None:
    _mode(monkeypatch, "trgm")
    target = corpus.remember(f"{memory_text} ({corpus.token}).")
    found = keyword_candidates(corpus.session, query, corpus.filters)
    assert target in [m.id for m in found]


def test_vermistim_needs_the_root_not_snowball(runtime: MemoryRuntime) -> None:
    """Why ``stem_root`` exists: Snowball keeps 'vermiş' and 'ver' apart."""
    with runtime.session() as session:
        stemmed_only = session.execute(
            sql_text(
                "SELECT to_tsvector('turkish', 'söz verdim') "
                "@@ to_tsquery('turkish', 'vermiştim:*')"
            )
        ).scalar_one()
        by_root = session.execute(
            sql_text("SELECT to_tsvector('turkish', 'söz verdim') @@ to_tsquery('turkish', :q)"),
            {"q": lexical.tsquery_text("vermiştim")},
        ).scalar_one()
    assert stemmed_only is False
    assert by_root is True


# --------------------------------------------------------------------------- acceptance 5


def test_a_forgotten_memory_is_not_found_by_words(
    corpus: Corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mode(monkeypatch, "trgm")
    target = corpus.remember(f"Ahmet'e kitabı cumartesi getireceğime söz verdim ({corpus.token}).")
    assert target in [m.id for m in keyword_candidates(corpus.session, AHMET_QUERY, corpus.filters)]
    corpus.forget(target)
    assert keyword_candidates(corpus.session, AHMET_QUERY, corpus.filters) == []
    hits = hybrid_search(corpus.session, EMBEDDER, AHMET_QUERY, corpus.filters, k=50)
    assert target not in [h.memory.id for h in hits]


# --------------------------------------------------------------------------- acceptance 6


def _indexes(session: Session) -> set[str]:
    rows = session.execute(
        sql_text("SELECT indexname FROM pg_indexes WHERE tablename = 'memories'")
    ).scalars()
    return set(rows) & {FOLD_INDEX, TSV_INDEX}


def test_pg_trgm_ships_with_the_image(runtime: MemoryRuntime) -> None:
    with runtime.session() as session:
        available = session.execute(
            sql_text("SELECT default_version FROM pg_available_extensions WHERE name = 'pg_trgm'")
        ).scalar()
        installed = session.execute(
            sql_text("SELECT extversion FROM pg_extension WHERE extname = 'pg_trgm'")
        ).scalar()
    assert available, "pg_trgm is not in this PostgreSQL image"
    assert installed, "the migration did not install pg_trgm"


def test_migration_goes_up_down_and_up(runtime: MemoryRuntime) -> None:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    mine = revision_named("text_trgm")
    assert head() == mine, "the trgm migration is not the head; downgrading would undo others"
    try:
        command.downgrade(cfg, parent_of(mine))
        with runtime.session() as session:
            assert _indexes(session) == set()
        command.upgrade(cfg, "head")
        with runtime.session() as session:
            assert _indexes(session) == {FOLD_INDEX, TSV_INDEX}
    finally:
        command.upgrade(cfg, "head")


def test_the_word_leg_can_use_both_indexes(runtime: MemoryRuntime) -> None:
    """The query's expressions are the indexes' expressions: with sequential scans off the
    planner picks them (on a small table it would rightly not)."""
    with runtime.session() as session:
        session.execute(sql_text("SET LOCAL enable_seqscan = off"))
        trgm_plan = "\n".join(
            session.execute(
                sql_text(
                    f"EXPLAIN SELECT id FROM memories WHERE CAST(:t AS text) <% {lexical.FOLD_SQL}"
                ),
                {"t": "ahmet"},
            ).scalars()
        )
        tsv_plan = "\n".join(
            session.execute(
                sql_text(
                    "EXPLAIN SELECT id FROM memories WHERE to_tsvector('turkish'::regconfig, text)"
                    " @@ to_tsquery('turkish'::regconfig, :q)"
                ),
                {"q": "ahmet:*"},
            ).scalars()
        )
        session.rollback()
    assert FOLD_INDEX in trgm_plan, trgm_plan
    assert TSV_INDEX in tsv_plan, tsv_plan
