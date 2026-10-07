"""Unit tests: the memory's word leg (card memory-lexical-turkish-rrf) on SQLite.

- ``fold`` makes İzmir / IZMIR / izmir one key, in Python and (integration suite) in PostgreSQL;
- stop words never reach the candidate search;
- ``PAGENTOS_MEMORY_LEXICAL`` defaults to ``trgm`` (card memory-lexical-turkish-measure);
  ``like`` is the old ranking byte for byte, kept as the way back;
- in ``trgm`` mode a memory found only by its words gets a rank of its own (RRF) instead of a
  semantic score of 0.0, so a name said once is not buried under 59 look-alikes.

The PostgreSQL half (pg_trgm, the ``turkish`` text search, the migration) is
``tests/integration/test_memory_lexical_pg.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.memory import lexical, service
from app.memory.embedding import DeterministicEmbedder, cosine_similarity
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.memory.retrieval import (
    W_SEMANTIC,
    RetrievalFilters,
    hybrid_search,
    keyword_candidates,
)
from app.memory.service import MemoryLinks
from app.memory.types import MemoryClass

MEMORY_TABLES = [
    Entity.__table__,
    EntityEdge.__table__,
    Memory.__table__,
    MemoryVersion.__table__,
    MemoryEvidence.__table__,
    MemoryEmbedding.__table__,
    MemoryAuditEvent.__table__,
]

EMBEDDER = DeterministicEmbedder()

AHMET_QUERY = "Ahmet'e ne söz vermiştim?"
#: Only the stop word "ne" and the lone letter "e" of the query: with the deterministic
#: embedder each one is nearer the query than the memory that names Ahmet (0.21-0.42 against
#: 0.14), none holds a query word, and no two are near enough (< 0.80) to be merged as one.
DISTRACTOR_WORDS = (
    "kahve toplantı bahçe fatura araba tatil kira market doktor spor kitap film kombi perde "
    "halı balkon çanta gözlük şemsiye telefon priz lamba masa sandalye dolap bisiklet kedi "
    "köpek çiçek fırın ütü çaydanlık tencere tabak bardak kaşık çatal bıçak havlu sabun yastık "
    "yorgan battaniye ayakkabı mont kazak gömlek pantolon etek şapka eldiven atkı çorap kemer "
    "saat yüzük kolye küpe cüzdan"
).split()


def ahmet_memory_text(token: str) -> str:
    return (
        "Ahmet kitabını cumartesi sabahı getirecek; bahçe kapısı tamir edilecek, faturalar "
        f"ödenecek, kombi bakımı yapılacak ({token})."
    )


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    for table in MEMORY_TABLES:
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def trgm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lexical.MODE_ENV, "trgm")


@pytest.fixture()
def like(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(lexical.MODE_ENV, "like")


def _remember(db: Session, text: str, conversation_id: uuid.UUID) -> uuid.UUID:
    return service.remember_explicit(
        db,
        EMBEDDER,
        text=text,
        memory_class=MemoryClass.SEMANTIC,
        links=MemoryLinks(conversation_id=conversation_id),
    ).memory_id


def _ahmet_corpus(db: Session) -> tuple[uuid.UUID, uuid.UUID]:
    conversation = uuid.uuid4()
    target = _remember(db, ahmet_memory_text("a1b2c3d4e5"), conversation)
    for word in DISTRACTOR_WORDS:
        _remember(db, f"ne e {word}", conversation)
    return conversation, target


# --------------------------------------------------------------------------- fold


@pytest.mark.parametrize("spelling", ["İzmir", "IZMIR", "izmir", "İZMİR", "ızmır"])
def test_fold_makes_every_spelling_of_izmir_one_key(spelling: str) -> None:
    assert lexical.fold(spelling) == "izmir"


def test_fold_never_leaves_a_combining_dot() -> None:
    # str.lower() turns İ into "i" + U+0307: a key that matches nothing the owner typed.
    assert "̇" not in lexical.fold("İstanbul'a İnci ile gittim")
    assert lexical.fold("İstanbul") == "istanbul"


def test_turkish_lower_keeps_the_dotless_i() -> None:
    assert lexical.turkish_lower("IŞIK") == "ışık"
    assert lexical.turkish_lower("İNCİ") == "inci"


# --------------------------------------------------------------------------- terms


def test_stop_words_do_not_become_query_terms() -> None:
    assert lexical.query_terms("Bir şey için ne demiştim") == ["demiştim"]


def test_query_terms_fold_dedupe_and_keep_order() -> None:
    assert lexical.query_terms("İzmir'e IZMIR ile Ahmet, izmir") == ["izmir", "ahmet"]


def test_query_terms_are_capped() -> None:
    words = " ".join(f"kelime{i}" for i in range(20))
    assert len(lexical.query_terms(words)) == 8


def test_stem_root_strips_the_tense_and_person() -> None:
    assert lexical.stem_root("vermiştim") == "ver"
    assert lexical.stem_root("verdim") == "ver"
    assert lexical.stem_root("söylemiştim") == "söyle"
    # a root shorter than three letters is no root: the word stays whole
    assert lexical.stem_root("kedi") == "kedi"
    assert lexical.stem_root("ahmet") == "ahmet"


def test_tsquery_is_or_of_prefixes_of_roots() -> None:
    assert lexical.tsquery_text("Ahmet'e ne söz vermiştim?") == "ahmet:* | söz:* | ver:*"
    assert lexical.tsquery_text("ne için bir") == ""


# --------------------------------------------------------------------------- mode


def test_mode_defaults_to_trgm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(lexical.MODE_ENV, raising=False)
    assert lexical.lexical_mode() == "trgm"


@pytest.mark.parametrize(
    ("raw", "mode"), [("trgm", "trgm"), (" TRGM ", "trgm"), ("like", "like"), ("bm25", "trgm")]
)
def test_mode_reads_the_environment(monkeypatch: pytest.MonkeyPatch, raw: str, mode: str) -> None:
    monkeypatch.setenv(lexical.MODE_ENV, raw)
    assert lexical.lexical_mode() == mode


# --------------------------------------------------------------------------- word leg on SQLite


@pytest.mark.parametrize("query", ["İzmir", "IZMIR", "izmir", "İZMİR'e"])
def test_every_spelling_finds_the_izmir_memory(db: Session, trgm: None, query: str) -> None:
    conversation = uuid.uuid4()
    target = _remember(db, "Annemle İzmir'e cumartesi gideceğiz.", conversation)
    _remember(db, "Ankara'daki toplantı ertelendi.", conversation)
    found = keyword_candidates(db, query, RetrievalFilters(conversation_id=conversation))
    assert [m.id for m in found] == [target]


def test_stop_words_do_not_reach_the_candidate_search(db: Session, trgm: None) -> None:
    conversation = uuid.uuid4()
    only_stop_words = _remember(db, "Bir şey için ne zaman gideceğiz bilmiyorum.", conversation)
    said = _remember(db, "Dün komşuya bahçeyi sulayacağımı demiştim.", conversation)
    found = keyword_candidates(
        db, "Bir şey için ne demiştim", RetrievalFilters(conversation_id=conversation)
    )
    ids = [m.id for m in found]
    assert said in ids
    assert only_stop_words not in ids


def test_keyword_candidates_come_back_in_word_rank_order(db: Session, trgm: None) -> None:
    conversation = uuid.uuid4()
    one = _remember(db, "Ahmet geldi.", conversation)
    both = _remember(db, "Ahmet'e söz verdim, kitabı getireceğim.", conversation)
    found = keyword_candidates(db, AHMET_QUERY, RetrievalFilters(conversation_id=conversation))
    assert [m.id for m in found] == [both, one]


def test_like_mode_keeps_todays_terms(db: Session, like: None) -> None:
    # Today's leg: every word of three letters or more, stop words included.
    conversation = uuid.uuid4()
    only_stop_words = _remember(db, "Bir şey için ne zaman gideceğiz bilmiyorum.", conversation)
    found = keyword_candidates(
        db, "Bir şey için ne demiştim", RetrievalFilters(conversation_id=conversation)
    )
    assert [m.id for m in found] == [only_stop_words]


# --------------------------------------------------------------------------- RRF


def test_like_mode_buries_a_name_said_once(db: Session, like: None) -> None:
    """Today's defect, kept as the 'like' mode's pinned behaviour: found only by its word, the
    memory gets 0.0 for meaning and 59 nearer look-alikes go above it."""
    conversation, target = _ahmet_corpus(db)
    hits = hybrid_search(
        db, EMBEDDER, AHMET_QUERY, RetrievalFilters(conversation_id=conversation), k=60
    )
    assert target not in [h.memory.id for h in hits[:3]]
    by_id = {h.memory.id: h for h in hits}
    assert by_id[target].components["semantic"] == 0.0


def test_trgm_mode_ranks_a_name_said_once_first(db: Session, trgm: None) -> None:
    conversation, target = _ahmet_corpus(db)
    hits = hybrid_search(
        db, EMBEDDER, AHMET_QUERY, RetrievalFilters(conversation_id=conversation), k=10
    )
    assert target in [h.memory.id for h in hits[:3]]
    assert hits[0].memory.id == target


def test_like_mode_semantic_component_is_the_cosine(db: Session, like: None) -> None:
    conversation = uuid.uuid4()
    memory_id = _remember(db, "Kombi bakımı cumartesi yapılacak.", conversation)
    hits = hybrid_search(
        db, EMBEDDER, "kombi bakımı", RetrievalFilters(conversation_id=conversation)
    )
    expected = cosine_similarity(
        EMBEDDER.embed("kombi bakımı"), EMBEDDER.embed("Kombi bakımı cumartesi yapılacak.")
    )
    assert hits[0].memory.id == memory_id
    assert hits[0].components["semantic"] == pytest.approx(W_SEMANTIC * expected)


def test_rrf_is_normalised_to_one_for_first_in_both_lists(db: Session, trgm: None) -> None:
    conversation = uuid.uuid4()
    memory_id = _remember(db, "Kombi bakımı cumartesi yapılacak.", conversation)
    hits = hybrid_search(
        db, EMBEDDER, "kombi bakımı", RetrievalFilters(conversation_id=conversation)
    )
    assert hits[0].memory.id == memory_id
    assert hits[0].components["semantic"] == pytest.approx(W_SEMANTIC)


def test_rrf_scores() -> None:
    assert lexical.rrf_score(1, 1) == pytest.approx(1.0)
    assert lexical.rrf_score(1, None) == pytest.approx(0.5)
    assert lexical.rrf_score(60, 1) == pytest.approx((1 / 120 + 1 / 61) / (2 / 61))
