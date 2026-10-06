"""Conversations as text: start/stop, lines with a speaker, named voices with consent.

The owner, 2026-10-05: "keep my conversations as TEXT like Wispr Flow, never the audio; tell
the voices apart; when you ask 'bu kim?' or I name a voice, know it from then on."

- **No audio.** A line arrives as text plus, at most, a DERIVED speaker embedding. Where a
  caller still holds the microphone's buffer, :func:`transcribe_and_drop` runs the
  recogniser and the embedder on it and zeroes it, also when the recogniser fails. No table
  has an audio column (``app.conversations.models``).
- **Voices apart.** Inside one conversation the voices are grouped in MEMORY
  (:class:`LiveConversations` -> ``VoiceClusterer``): 'Konuşmacı 1', 'Konuşmacı 2'. The
  groups are dropped when the conversation stops. A new voice asks 'bu kim?' once
  (``ask_who``) - never twice for one voice in one conversation.
- **Names need consent.** 'bu Ahmet' (:func:`name_speaker`) without Ahmet's consent records
  the name only: no profile, the lines stay 'Konuşmacı N'. With consent
  (:func:`record_consent`, 'Ahmet izin verdi') the group's centroid is sealed as his profile,
  his lines in that conversation show 'Ahmet', and later conversations recognise him at
  ``PERSON_MATCH_THRESHOLD`` on the voice group's centroid; a recognised group is pinned to
  him (earlier lines relabelled, no 'bu kim?'). KVKK treats a voiceprint as biometric data.
- **The owner's lines** are always 'Sen': by the caller's flag (his own device's channel) or
  by the owner verifier on his enrolled profile (the segments route reads it).
- **Deleting a person** deletes the profile and the name; every line of theirs shows
  'Konuşmacı N' again (the label is read, never stored).
"""

from __future__ import annotations

import math
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.conversations.models import (
    MODES,
    NAME_WIDTH,
    NOTE_WIDTH,
    TEXT_WIDTH,
    TITLE_WIDTH,
    ConversationRow,
    ConversationSettingRow,
    PersonRow,
    SegmentRow,
)
from app.voice.crypto import ProfileCipher
from app.voice.errors import VoiceError
from app.voice.speaker import OwnerProfile, SpeakerDecision, verify_speaker
from app.voice.speaker_profiles import (
    PERSON_MATCH_THRESHOLD,
    PersonProfile,
    VoiceClusterer,
    best_match,
    open_profile,
    seal_profile,
)

OWNER_LABEL = "Sen"
PROFILE_MODEL = "conversation-embedding"
PREVIEW_WIDTH = 120
LIST_LIMIT = 200


class ConversationRefused(Exception):
    """A request the rules refuse; ``message`` is the Turkish sentence for the owner."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ------------------------------------------------------------------------ live state


@dataclass(slots=True)
class _Live:
    clusterer: VoiceClusterer = field(default_factory=VoiceClusterer)
    #: Numbers used before this process saw the conversation (a restart): new groups follow.
    base: int = 0
    asked: set[int] = field(default_factory=set)
    #: group number -> person named for it whose consent is not recorded yet.
    pending: dict[int, uuid.UUID] = field(default_factory=dict)
    #: group number -> the consenting person it was named as in this conversation.
    named: dict[int, uuid.UUID] = field(default_factory=dict)


class LiveConversations:
    """The voice groups of the open conversations. Memory only, never persisted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._live: dict[uuid.UUID, _Live] = {}

    def get(self, cid: uuid.UUID, *, base: int = 0) -> _Live:
        with self._lock:
            state = self._live.get(cid)
            if state is None:
                state = self._live[cid] = _Live(base=base)
            return state

    def peek(self, cid: uuid.UUID) -> _Live | None:
        with self._lock:
            return self._live.get(cid)

    def items(self) -> list[tuple[uuid.UUID, _Live]]:
        with self._lock:
            return list(self._live.items())

    def drop(self, cid: uuid.UUID) -> None:
        with self._lock:
            self._live.pop(cid, None)

    def clear(self) -> None:
        with self._lock:
            self._live.clear()

    def forget_person(self, person_id: uuid.UUID) -> None:
        with self._lock:
            for state in self._live.values():
                for table in (state.pending, state.named):
                    for number in [n for n, p in table.items() if p == person_id]:
                        del table[number]


# --------------------------------------------------------------------------- views


@dataclass(frozen=True, slots=True)
class SegmentView:
    id: str
    seq: int
    spoken_at: datetime
    text: str
    speaker: str
    is_owner: bool
    speaker_no: int | None
    person_id: str | None


@dataclass(frozen=True, slots=True)
class ConversationView:
    id: uuid.UUID
    mode: str
    title: str | None
    started_at: datetime
    ended_at: datetime | None
    segment_count: int
    preview: str
    segments: list[SegmentView] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SegmentResult:
    segment: SegmentView
    #: True once per new unnamed voice per conversation: JARVIS may ask 'bu kim?'.
    ask_who: bool


@dataclass(frozen=True, slots=True)
class PersonView:
    id: uuid.UUID
    name: str
    created_at: datetime
    consent_at: datetime | None
    consent_note: str | None
    has_profile: bool


@dataclass(frozen=True, slots=True)
class NameResult:
    person: PersonView
    applied: bool
    message: str


def _label(row: SegmentRow, names: dict[uuid.UUID, str]) -> str:
    if row.is_owner:
        return OWNER_LABEL
    if row.person_id is not None and row.person_id in names:
        return names[row.person_id]
    if row.speaker_no is not None:
        return f"Konuşmacı {row.speaker_no}"
    return "Konuşmacı"


def _segment_view(row: SegmentRow, names: dict[uuid.UUID, str]) -> SegmentView:
    return SegmentView(
        id=str(row.id),
        seq=row.seq,
        spoken_at=row.spoken_at,
        text=row.text,
        speaker=_label(row, names),
        is_owner=row.is_owner,
        speaker_no=row.speaker_no,
        person_id=str(row.person_id) if row.person_id else None,
    )


def _person_view(row: PersonRow) -> PersonView:
    return PersonView(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        consent_at=row.consent_at,
        consent_note=row.consent_note,
        has_profile=row.profile_sealed is not None,
    )


def _names(db: Session) -> dict[uuid.UUID, str]:
    return {pid: name for pid, name in db.execute(select(PersonRow.id, PersonRow.name))}


def name_key(name: str) -> str:
    """Turkish case-folding: 'AHMET', 'Ahmet' and 'ahmet' are one person; 'Işık' is 'ışık'."""
    folded = name.strip().replace("I", "ı").replace("İ", "i").lower()
    return " ".join(folded.split())


#: Search folding, the same in Python and in SQL (SQLite's lower() is ASCII-only and
#: PostgreSQL's depends on the database's locale, so the Turkish capitals are spelled out).
#: All four i's are one letter: a transcript writes 'ışık' where the owner types 'isik', and
#: 'IŞIK' must find 'Işıkları' as well as 'İSTANBUL' finds 'istanbul'.
SEARCH_FOLD = (
    ("I", "i"),
    ("İ", "i"),
    ("ı", "i"),
    ("Ç", "ç"),
    ("Ğ", "ğ"),
    ("Ö", "ö"),
    ("Ş", "ş"),
    ("Ü", "ü"),
)


def search_fold(text: str) -> str:
    for upper, lower in SEARCH_FOLD:
        text = text.replace(upper, lower)
    return text.lower()


def _search_fold_sql(column):  # noqa: ANN001, ANN202
    for upper, lower in SEARCH_FOLD:
        column = func.replace(column, upper, lower)
    return func.lower(column)


#: PostgreSQL's ``integer``: a voice number beyond it is no voice of any conversation.
SPEAKER_NO_MAX = 2**31 - 1
EMBEDDING_MAX = 4096


def _clean_text(value: object, *, code: str, message: str) -> str:
    """A string without NUL (PostgreSQL refuses it in text; it is never speech)."""
    if not isinstance(value, str) or "\x00" in value:
        raise ConversationRefused(code, message)
    return value


def _optional_text(value: object, *, code: str, message: str) -> str | None:
    if value is None:
        return None
    return _clean_text(value, code=code, message=message)


def _vector(embedding: object) -> list[float] | None:
    """A derived embedding: 1..4096 finite numbers, not all zero (a zero vector has no
    direction and a NaN poisons a group's centroid or a person's profile for ever)."""
    if embedding is None:
        return None
    refused = ConversationRefused(
        "embedding_invalid", "Ses izi sıfır olmayan, sonlu sayılardan oluşan bir liste olmalı."
    )
    if not isinstance(embedding, (list, tuple)) or not 1 <= len(embedding) <= EMBEDDING_MAX:
        raise refused
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in embedding):
        raise refused
    vector = [float(x) for x in embedding]
    if not all(math.isfinite(x) for x in vector) or not any(vector):
        raise refused
    return vector


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _conversation(db: Session, cid: uuid.UUID) -> ConversationRow:
    row = db.get(ConversationRow, cid)
    if row is None:
        raise ConversationRefused("not_found", "Bu konuşma yok; silinmiş olabilir.")
    return row


def _live_for(db: Session, live: LiveConversations, cid: uuid.UUID) -> _Live:
    state = live.peek(cid)
    if state is not None:
        return state
    used = db.execute(
        select(func.max(SegmentRow.speaker_no)).where(SegmentRow.conversation_id == cid)
    ).scalar_one()
    return live.get(cid, base=int(used or 0))


# ---------------------------------------------------------------------- start / stop


def start_conversation(
    db: Session,
    live: LiveConversations,
    *,
    mode: str = "manual",
    title: object = None,
    now: datetime | None = None,
) -> ConversationView:
    title = _optional_text(title, code="title_invalid", message="Başlık bir yazı olmalı.")
    if mode not in MODES:
        raise ConversationRefused("mode_invalid", "Konuşma ya elle ya da 'evde dinle' ile başlar.")
    open_one = db.execute(
        select(ConversationRow.id).where(ConversationRow.ended_at.is_(None)).limit(1)
    ).scalar_one_or_none()
    if open_one is not None:
        raise ConversationRefused("already_open", "Zaten yazdığım bir konuşma var; önce onu bitir.")
    row = ConversationRow(
        mode=mode,
        title=(title or "").strip()[:TITLE_WIDTH] or None,
        started_at=_now(now),
    )
    db.add(row)
    db.flush()
    live.get(row.id)
    return _view(db, row, with_segments=False)


def stop_conversation(
    db: Session, live: LiveConversations, cid: uuid.UUID, *, now: datetime | None = None
) -> ConversationView:
    row = _conversation(db, cid)
    if row.ended_at is None:
        row.ended_at = _now(now)
        db.flush()
    # The voice groups of a finished conversation are not kept.
    live.drop(cid)
    return _view(db, row, with_segments=False)


# ----------------------------------------------------------------------------- lines


def transcribe_and_drop(
    buffer: bytearray,
    *,
    stt: Callable[[bytearray], str],
    embedder: Callable[[bytearray], list[float]] | None,
) -> tuple[str, list[float] | None]:
    """Text and a derived embedding out of a microphone buffer; the buffer is zeroed after,
    whatever happens. The audio goes no further than this function."""
    try:
        text = stt(buffer)
        embedding = embedder(buffer) if embedder is not None else None
        return text, embedding
    finally:
        buffer[:] = bytes(len(buffer))


def _consenting_profiles(db: Session, cipher: ProfileCipher) -> list[PersonProfile]:
    rows = db.execute(
        select(PersonRow).where(
            PersonRow.profile_sealed.is_not(None), PersonRow.consent_at.is_not(None)
        )
    ).scalars()
    profiles = []
    for row in rows:
        try:
            vector = open_profile(cipher, row.profile_sealed)  # type: ignore[arg-type]
        except VoiceError:
            continue  # sealed under another secret: unusable, never fatal
        profiles.append(PersonProfile(str(row.id), row.name, vector))
    return profiles


def _is_owner_voice(embedding: list[float], owner_profile: OwnerProfile | None) -> bool:
    if owner_profile is None or len(embedding) != owner_profile.dim:
        return False
    verdict = verify_speaker(embedding, owner_profile, device_trusted=True)
    return verdict.decision == SpeakerDecision.OWNER


def add_segment(
    db: Session,
    live: LiveConversations,
    cipher: ProfileCipher,
    cid: uuid.UUID,
    *,
    text: object,
    embedding: object = None,
    is_owner: bool | None = None,
    owner_profile: OwnerProfile | None = None,
    now: datetime | None = None,
) -> SegmentResult:
    content = _clean_text(
        "" if text is None else text, code="text_invalid", message="Satır bir yazı olmalı."
    ).strip()
    vector = _vector(embedding)
    row = _conversation(db, cid)
    if row.ended_at is not None:
        raise ConversationRefused("closed", "Bu konuşma bitti; yeni satır eklenmez.")
    if not content:
        raise ConversationRefused("text_empty", "Boş satır yazılmaz.")
    if len(content) > TEXT_WIDTH:
        raise ConversationRefused("text_too_long", f"Bir satır en çok {TEXT_WIDTH} karakter.")

    owner = bool(is_owner) or (vector is not None and _is_owner_voice(vector, owner_profile))
    speaker_no: int | None = None
    person_id: uuid.UUID | None = None
    ask_who = False
    if not owner and vector is not None:
        state = _live_for(db, live, cid)
        number, new_voice = state.clusterer.assign(vector)
        speaker_no = state.base + number
        person_id = state.named.get(speaker_no)
        if person_id is None:
            # The GROUP is matched, not the line alone: one voice is one person for the
            # whole conversation, and a recognised group is pinned (no mixed labels).
            centroid = state.clusterer.centroid(number) or vector
            found, _score = best_match(
                centroid, _consenting_profiles(db, cipher), threshold=PERSON_MATCH_THRESHOLD
            )
            if found is not None:
                person_id = uuid.UUID(found.person_id)
                state.named[speaker_no] = person_id
                db.execute(
                    update(SegmentRow)
                    .where(
                        SegmentRow.conversation_id == cid,
                        SegmentRow.speaker_no == speaker_no,
                        SegmentRow.is_owner.is_(False),
                    )
                    .values(person_id=person_id)
                )
        if new_voice and person_id is None and speaker_no not in state.asked:
            state.asked.add(speaker_no)
            ask_who = True

    seq = db.execute(
        select(func.coalesce(func.max(SegmentRow.seq), 0)).where(SegmentRow.conversation_id == cid)
    ).scalar_one()
    segment = SegmentRow(
        conversation_id=cid,
        seq=int(seq) + 1,
        spoken_at=_now(now),
        text=content,
        is_owner=owner,
        speaker_no=speaker_no,
        person_id=person_id,
    )
    db.add(segment)
    db.flush()
    return SegmentResult(_segment_view(segment, _names(db)), ask_who)


# ---------------------------------------------------------------- people and consent


def _person_by_name(db: Session, name: object, *, now: datetime) -> PersonRow:
    message = f"İsim 1-{NAME_WIDTH} karakter olmalı."
    clean = " ".join(_clean_text(name, code="name_invalid", message=message).split())
    if not clean or len(clean) > NAME_WIDTH:
        raise ConversationRefused("name_invalid", message)
    key = name_key(clean)
    row = db.execute(select(PersonRow).where(PersonRow.name_key == key)).scalar_one_or_none()
    if row is None:
        row = PersonRow(name=clean, name_key=key, created_at=now)
        db.add(row)
        db.flush()
    return row


def _apply_name(
    db: Session,
    cipher: ProfileCipher,
    state: _Live | None,
    cid: uuid.UUID,
    number: int,
    person: PersonRow,
) -> bool:
    """Consent is recorded: seal the group's centroid as the profile and relabel its lines.
    Returns whether a profile could be made (the group is still in memory)."""
    assert person.consent_at is not None
    sealed = False
    if state is not None:
        centroid = state.clusterer.centroid(number - state.base)
        if centroid is not None:
            person.profile_sealed = seal_profile(cipher, centroid, model_id=PROFILE_MODEL)
            person.profile_model = PROFILE_MODEL
            sealed = True
        state.named[number] = person.id
        state.pending.pop(number, None)
    db.execute(
        update(SegmentRow)
        .where(
            SegmentRow.conversation_id == cid,
            SegmentRow.speaker_no == number,
            SegmentRow.is_owner.is_(False),
        )
        .values(person_id=person.id)
    )
    db.flush()
    return sealed


def name_speaker(
    db: Session,
    live: LiveConversations,
    cipher: ProfileCipher,
    cid: uuid.UUID,
    speaker_no: int,
    name: object,
    *,
    now: datetime | None = None,
) -> NameResult:
    """'bu Ahmet' for voice ``speaker_no`` of conversation ``cid``."""
    if isinstance(speaker_no, bool) or not 1 <= speaker_no <= SPEAKER_NO_MAX:
        raise ConversationRefused("speaker_invalid", "Konuşmacı numarası 1 ya da daha büyük olur.")
    _conversation(db, cid)
    has_lines = db.execute(
        select(SegmentRow.id)
        .where(SegmentRow.conversation_id == cid, SegmentRow.speaker_no == speaker_no)
        .limit(1)
    ).scalar_one_or_none()
    if has_lines is None:
        raise ConversationRefused("no_speaker", f"Bu konuşmada Konuşmacı {speaker_no} yok.")
    person = _person_by_name(db, name, now=_now(now))
    state = live.peek(cid)
    if person.consent_at is None:
        if state is not None:
            state.pending[speaker_no] = person.id
        return NameResult(
            _person_view(person),
            False,
            (
                f"{person.name} adını not ettim ama izni kayıtlı değil; sesini saklamıyorum, "
                f"satırları 'Konuşmacı {speaker_no}' kalır. '{person.name} izin verdi' "
                "dersen tanırım."
            ),
        )
    sealed = _apply_name(db, cipher, state, cid, speaker_no, person)
    message = (
        f"Tamam, bu {person.name}. Bundan sonra sesini tanırım."
        if sealed
        else f"Tamam, bu {person.name}; bu konuşmanın ses izi artık yok, sesini sonra tanırım."
    )
    return NameResult(_person_view(person), True, message)


def record_consent(
    db: Session,
    live: LiveConversations,
    cipher: ProfileCipher,
    *,
    name: object = None,
    person_id: uuid.UUID | None = None,
    note: object = None,
    now: datetime | None = None,
) -> PersonView:
    """'Ahmet izin verdi': the consent and its date; a waiting naming is applied now."""
    note = _optional_text(note, code="note_invalid", message="İzin notu bir yazı olmalı.")
    if person_id is not None:
        person = db.get(PersonRow, person_id)
        if person is None:
            raise ConversationRefused("not_found", "Bu kişi yok; silinmiş olabilir.")
    else:
        person = _person_by_name(db, name or "", now=_now(now))
    if person.consent_at is None:
        person.consent_at = _now(now)
    if note:
        person.consent_note = note.strip()[:NOTE_WIDTH]
    db.flush()
    for cid, state in live.items():
        for number in [n for n, p in state.pending.items() if p == person.id]:
            _apply_name(db, cipher, state, cid, number, person)
    return _person_view(person)


def list_people(db: Session) -> list[PersonView]:
    rows = db.execute(select(PersonRow).order_by(PersonRow.name_key)).scalars()
    return [_person_view(r) for r in rows]


def delete_person(db: Session, live: LiveConversations, person_id: uuid.UUID) -> bool:
    """The profile and the name go; every line of theirs is 'Konuşmacı N' again."""
    row = db.get(PersonRow, person_id)
    if row is None:
        return False
    db.execute(update(SegmentRow).where(SegmentRow.person_id == person_id).values(person_id=None))
    db.delete(row)
    db.flush()
    live.forget_person(person_id)
    return True


# ------------------------------------------------------------- reading and forgetting


def _view(db: Session, row: ConversationRow, *, with_segments: bool) -> ConversationView:
    segments: list[SegmentView] = []
    if with_segments:
        names = _names(db)
        rows = db.execute(
            select(SegmentRow).where(SegmentRow.conversation_id == row.id).order_by(SegmentRow.seq)
        ).scalars()
        segments = [_segment_view(s, names) for s in rows]
    count, first = db.execute(
        select(func.count(SegmentRow.id), func.min(SegmentRow.seq)).where(
            SegmentRow.conversation_id == row.id
        )
    ).one()
    preview = ""
    if first is not None:
        preview = db.execute(
            select(SegmentRow.text).where(
                SegmentRow.conversation_id == row.id, SegmentRow.seq == first
            )
        ).scalar_one()[:PREVIEW_WIDTH]
    return ConversationView(
        id=row.id,
        mode=row.mode,
        title=row.title,
        started_at=row.started_at,
        ended_at=row.ended_at,
        segment_count=int(count),
        preview=preview,
        segments=segments,
    )


def list_conversations(db: Session, *, q: str | None = None) -> list[ConversationView]:
    query = select(ConversationRow).order_by(ConversationRow.started_at.desc())
    needle = search_fold((q or "").replace("\x00", "").strip())
    if needle:
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        matching = select(SegmentRow.conversation_id).where(
            _search_fold_sql(SegmentRow.text).like(f"%{escaped}%", escape="\\")
        )
        query = query.where(ConversationRow.id.in_(matching))
    rows = db.execute(query.limit(LIST_LIMIT)).scalars()
    return [_view(db, r, with_segments=False) for r in rows]


def get_conversation(db: Session, cid: uuid.UUID) -> ConversationView:
    return _view(db, _conversation(db, cid), with_segments=True)


def delete_conversation(db: Session, live: LiveConversations, cid: uuid.UUID) -> bool:
    if db.get(ConversationRow, cid) is None:
        return False
    db.execute(delete(SegmentRow).where(SegmentRow.conversation_id == cid))
    db.execute(delete(ConversationRow).where(ConversationRow.id == cid))
    db.flush()
    live.drop(cid)
    return True


def forget_all(db: Session, live: LiveConversations) -> int:
    """'unut': every conversation and every line. People and their consent stay."""
    count = db.execute(select(func.count(ConversationRow.id))).scalar_one()
    db.execute(delete(SegmentRow))
    db.execute(delete(ConversationRow))
    db.flush()
    live.clear()
    return int(count)


# ------------------------------------------------------------------ 'evde dinle'


def get_home_listen(db: Session) -> bool:
    row = db.get(ConversationSettingRow, 1)
    return bool(row and row.home_listen)


def set_home_listen(db: Session, on: bool, *, now: datetime | None = None) -> bool:
    row = db.get(ConversationSettingRow, 1)
    if row is None:
        row = ConversationSettingRow(id=1, home_listen=bool(on), updated_at=_now(now))
        db.add(row)
    else:
        row.home_listen = bool(on)
        row.updated_at = _now(now)
    db.flush()
    return row.home_listen
