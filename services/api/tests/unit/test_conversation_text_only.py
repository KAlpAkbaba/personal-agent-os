"""A conversation line is TEXT - the sound must not ride inside it (card
conversation-text-refuses-audio).

Test team, round t-manual-20261006e (staging 72884b71): POST ``/segments`` with ``content``
holding ``data:audio/wav;base64,...`` answered 201 and stored it. The owner, 2026-10-05: his
conversations are kept as text only, never the audio (KVKK: a voice is biometric data). The
route refuses, 422 ``audio_refused`` in Turkish, a line carrying a ``data:`` URL, a long base64
run, a base64 block wrapped into lines (76 / 64 a line) or a base64 run carrying an audio
file's magic (RIFF / ID3 / OggS / fLaC / EBML / #!AMR / an m4a ``ftyp`` box).
Ordinary Turkish text - digits, punctuation, a long sentence - is still written; the length
limit is the service's and stays.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.artifacts.runtime import ArtifactRuntime
from app.config import Settings
from app.conversations.models import (
    TEXT_WIDTH,
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.main import create_app
from tests.identity_support import authenticate, install_identity

TABLES = (ConversationRow, SegmentRow, PersonRow, ConversationSettingRow)
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + bytes(range(256)) * 2


@pytest.fixture()
def api():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.__table__.create(engine)
    settings = Settings(_env_file=None)
    app = create_app(settings)
    install_identity(app, settings=settings)
    artifacts = ArtifactRuntime(settings)
    artifacts._engine = engine
    artifacts._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    app.state.artifacts = artifacts
    client = TestClient(app)
    authenticate(app, client, settings=settings)
    cid = client.post("/v1/conversations", json={}).json()["id"]
    yield client, cid, engine
    engine.dispose()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _wrapped(data: bytes, *, width: int = 76, eol: str = "\n") -> str:
    text = _b64(data)
    return eol.join(text[i : i + width] for i in range(0, len(text), width))


#: A phone's recording (m4a: an ``ftyp`` box) and a raw AAC/ADTS stream - neither opens with
#: a magic the first pass knew, so only their wrapped shape or the ``ftyp`` box betrays them.
M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom" + bytes(range(7, 250, 3)) * 4
AAC = b"\xff\xf1\x50\x80\x2e\x7f\xfc" + bytes(range(3, 256, 5)) * 6


def _rows(engine) -> int:
    with engine.connect() as connection:
        return len(connection.execute(select(SegmentRow.id)).all())


REFUSED = {
    "data_audio_url": "data:audio/wav;base64," + _b64(WAV),
    "data_url_inside_a_sentence": "Bunu dinle: data:audio/ogg;base64,T2dnUwACAAAAAAAA ne dersin?",
    "data_url_other_mime": "data:application/octet-stream;base64,AAAAAAAA",
    "base64_run_400": "A" * 100 + "b9+/" * 75,
    "base64_run_inside_text": "Kayıt şu: " + _b64(bytes(range(256)) * 2) + " tamam.",
    "riff_prefix": "ses " + _b64(WAV[:24]),
    "id3_prefix": _b64(b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x00"),
    "oggs_prefix": "işte " + _b64(b"OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00"),
    "flac_prefix": _b64(b"fLaC\x00\x00\x00\x22\x10\x00\x10\x00"),
    "webm_prefix": _b64(b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81"),
    # Inspector, first pass: the `base64` CLI / MIME shape - 76-character lines - kept every
    # line under the 200 run, and a phone's m4a (`ftyp`) had no magic in the list.
    "m4a_76_line_block": _wrapped(M4A),
    "aac_76_line_block": _wrapped(AAC),
    "aac_64_line_block_crlf": _wrapped(AAC, width=64, eol="\r\n"),
    "m4a_short_unsplit": "kayıt " + _b64(M4A[:90]),
    "m4a_box_size_24": _b64(b"\x00\x00\x00\x18ftyp3gp4\x00\x00\x00\x00isom3gp4"),
}


@pytest.mark.parametrize("content", REFUSED.values(), ids=REFUSED.keys())
def test_a_line_carrying_audio_is_refused_in_turkish(api, content) -> None:
    client, cid, engine = api
    refused = client.post(f"/v1/conversations/{cid}/segments", json={"content": content})
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "audio_refused"
    assert detail["message"] == "Ses kaydı alınmaz; yalnızca yazıya dökülmüş metin."
    assert _rows(engine) == 0


LONG_SENTENCE = (
    "Yarın sabah saat 09:30'da Ahmet'le Kadıköy'deki ofiste buluşup 2027 bütçesini, "
    "üç yeni müşteri sözleşmesini ve %18 KDV'li faturaları konuşacağız; sonra öğle yemeğinde "
    "annemi arayıp hafta sonu planını - Bursa'ya gidiş, 14.500 TL'lik araba bakımı ve "
    "kardeşimin doğum günü hediyesi - netleştireceğim, unutmayalım. "
) * 6

ACCEPTED = {
    "plain": "Selam, nasılsın?",
    "digits_and_punctuation": "Toplantı 14:30'da; oda 3B, tel. 0532 123 45 67 (dahili #12).",
    "word_data_with_colon": "data: dediğin şey bence önemli değil, veri: yeterli.",
    "url": "Şu linke bak: https://www.example.com/haber/2026/10/06/ekonomi?id=42&ref=ana",
    "long_sentence": LONG_SENTENCE[:TEXT_WIDTH],
    "magic_like_short_word": "SUQz ve UklGR diye iki kısaltma gördüm.",
    "multi_line_turkish": (
        "Bugünkü notlar:\n"
        "1) Ahmet'i 15:00'te ara, sözleşmeyi konuş.\n"
        "2) Market: süt, yumurta, ekmek, 2 kg domates.\n"
        "3) Akşam 20:30 annemle görüntülü konuşma.\n"
        "Unutma: kargo takip no 1234567890123 yarın gelecek."
    ),
    "multi_line_links": (
        "Linkler:\n"
        "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/view\n"
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890abcdef\n"
        "https://example.com/belgeler/2026/sozlesme-taslagi-son-hali.pdf"
    ),
}


@pytest.mark.parametrize("content", ACCEPTED.values(), ids=ACCEPTED.keys())
def test_ordinary_turkish_text_is_still_written(api, content) -> None:
    client, cid, engine = api
    written = client.post(f"/v1/conversations/{cid}/segments", json={"content": content})
    assert written.status_code == 201, written.text
    assert written.json()["text"] == content.strip()
    assert _rows(engine) == 1


def test_the_length_limit_stays(api) -> None:
    client, cid, _engine = api
    too_long = client.post(
        f"/v1/conversations/{cid}/segments", json={"content": "Merhaba dünya. " * 400}
    )
    assert too_long.status_code == 422
    assert too_long.json()["detail"]["code"] == "text_too_long"
