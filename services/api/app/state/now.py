"""The live-state composer behind ``state.now`` (docs/M18_ACTION_CONTRACT.md §3, §4).

On 2026-09-06 the owner asked "Kendi sisteminde şu anda ne görüyorsun?" and heard how
many facts of each truth kind the world model held. That is bookkeeping, not an answer.
A CURRENT-STATE question is answered from the live runtime -> the World Model snapshot ->
the most recent verified state, and the ledger is never the source for "now" (contract §3).

This module does not duplicate the World Model: it calls
:func:`app.worldmodel.state.assemble_snapshot` (the same collectors the REST surface uses)
and SHAPES the result into the handful of facts an owner asks about, each carrying where it
came from, when it was observed, how old it is, how confident the source is and whether it
is past its TTL. A fact that cannot be established becomes an uncertainty with a reason -
never a guess. Then it speaks: result first, one to three Turkish sentences, no ids, no
counts of truth kinds, no "kayıtlara bakıyorum".

Read-only. The one write around a live answer (the ``voice.state_answered`` ledger row)
is :func:`record_state_answered`, called by the tool handler, not by the composer.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.explain.classify import QUERY_EYE_STATE, QUERY_WORLD_STATE
from app.ledger import service as ledger_service
from app.ledger.vocabulary import EVENT_TYPE_VOICE_STATE_ANSWERED, SUBSYSTEM_VOICE
from app.logging import get_logger
from app.narration.numbers import cardinal
from app.worldmodel.state import Fact, WorldSnapshot, assemble_snapshot

logger = get_logger("app.state.now")

SUBSYSTEM: Final = "worldmodel"

SCOPE_ALL: Final = "all"
SCOPE_EYE: Final = "eye"
SCOPE_VOICE: Final = "voice"
SCOPE_PRESENCE: Final = "presence"
SCOPE_DEVICES: Final = "devices"
SCOPE_RELEASE: Final = "release"
SCOPES: Final[tuple[str, ...]] = (
    SCOPE_ALL,
    SCOPE_EYE,
    SCOPE_VOICE,
    SCOPE_PRESENCE,
    SCOPE_DEVICES,
    SCOPE_RELEASE,
)

#: The fact keys (contract §4), and which scope each belongs to.
KEY_CORE_HEALTH: Final = "cloud_core.health"
KEY_VOICE_SESSION: Final = "voice.session"
KEY_EYE_ENABLED: Final = "eye.enabled"
KEY_EYE_LAST_OBSERVATION: Final = "eye.last_observation_age_s"
KEY_OWNER_PRESENCE: Final = "owner.presence"
KEY_DEVICES_ONLINE: Final = "devices.online"
KEY_RELEASE_SHADOW_READY: Final = "release.shadow_ready"
KEY_TASKS_RUNNING: Final = "tasks.running"

_KEYS_BY_SCOPE: Final[dict[str, tuple[str, ...]]] = {
    SCOPE_EYE: (KEY_EYE_ENABLED, KEY_EYE_LAST_OBSERVATION),
    SCOPE_VOICE: (KEY_VOICE_SESSION,),
    SCOPE_PRESENCE: (KEY_EYE_ENABLED, KEY_OWNER_PRESENCE),
    SCOPE_DEVICES: (KEY_DEVICES_ONLINE,),
    SCOPE_RELEASE: (KEY_RELEASE_SHADOW_READY,),
}

#: Statuses of an evolution opportunity that mean "built, waiting for the owner".
_AWAITING_OWNER_STATUSES: Final[tuple[str, ...]] = ("shadow_ready", "owner_approval_required")

#: A camera observation older than this is not evidence about now (mirrors
#: app.presence.engine.DEFAULT_TTL_S["camera"]; the live engine's policy wins when given).
_DEFAULT_CAMERA_TTL_S: Final = 90.0


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class LiveFact:
    key: str
    value: Any
    source: str
    observed_at: datetime
    age_s: float
    confidence: float
    stale: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "observed_at": _iso(self.observed_at),
            "age_s": round(self.age_s, 1),
            "confidence": round(float(self.confidence), 3),
            "stale": self.stale,
        }


@dataclass(frozen=True, slots=True)
class LiveUncertainty:
    subject: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "reason": self.reason}


class _Composer:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.facts: dict[str, LiveFact] = {}
        self.uncertainties: dict[str, LiveUncertainty] = {}

    def fact(
        self,
        key: str,
        value: Any,
        *,
        source: str,
        observed_at: datetime | None = None,
        confidence: float = 1.0,
        stale: bool = False,
    ) -> None:
        seen = _aware(observed_at) if observed_at is not None else self.now
        age = max(0.0, (self.now - seen).total_seconds())
        self.facts[key] = LiveFact(
            key=key,
            value=value,
            source=source,
            observed_at=seen,
            age_s=age,
            confidence=confidence,
            stale=stale,
        )

    def uncertain(self, subject: str, reason: str) -> None:
        self.uncertainties[subject] = LiveUncertainty(subject=subject, reason=reason)


# ------------------------------------------------------------------ the facts


def _health_value(health: dict[str, Any]) -> str:
    """The same verdict /v1/system/health gives (app.health.is_degraded): until 2026-09-11
    this counted a `skipped` check and an advisory Redis as degraded, so `state.now` could
    say "kismen saglikli" about a Cloud Core its own health endpoint reported ok."""
    from app.health import is_degraded

    if not any(v is not None for v in health.values()):
        return "unknown"
    return "degraded" if is_degraded(health) else "ok"


def _collect_core(c: _Composer, db: Session, health: dict[str, Any] | None) -> None:
    """``cloud_core.health``: the caller's health probe when it ran one, otherwise the one
    probe this process can make honestly right now - its own database answers. The
    process composing the answer IS the Cloud Core; that it is answering with a working
    session is runtime truth, and it is stated as exactly that, no more."""
    if health:
        c.fact(KEY_CORE_HEALTH, _health_value(health), source="health_probe")
        return
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - the probe IS the answer
        c.fact(KEY_CORE_HEALTH, "fail", source="health_probe", confidence=1.0)
        return
    c.fact(KEY_CORE_HEALTH, "ok", source="health_probe")


def _collect_voice(c: _Composer, db: Session, session_id: uuid.UUID | None) -> None:
    """``voice.session``: THIS realtime session, from its row - the one the question came
    through. Without a session there is nothing to be connected."""
    if session_id is None:
        c.uncertain(KEY_VOICE_SESSION, "no_session")
        return
    from app.voice.realtime_sessions.models import (
        REALTIME_STATE_CLOSED,
        REALTIME_STATE_EXPIRED,
        RealtimeSessionRow,
    )

    row = db.get(RealtimeSessionRow, session_id)
    if row is None:
        c.uncertain(KEY_VOICE_SESSION, "session_not_found")
        return
    connected = row.state not in (REALTIME_STATE_CLOSED, REALTIME_STATE_EXPIRED)
    c.fact(
        KEY_VOICE_SESSION,
        "connected" if connected else "closed",
        source="realtime_session",
        observed_at=row.updated_at or c.now,
    )


def _collect_eye(c: _Composer, db: Session, snapshot: WorldSnapshot, presence_runtime: Any) -> None:
    """``eye.enabled`` (the durable flag, from the World Model's camera fact and dated by
    the ledger row that set it) and ``eye.last_observation_age_s`` (the most recent
    accepted camera observation the live engine still holds)."""
    from app.presence.eye import latest_eye_event

    camera = _snapshot_fact(snapshot, "device.camera_state")
    if camera is None:
        c.uncertain(KEY_EYE_ENABLED, _snapshot_reason(snapshot, "presence", "section_unavailable"))
        return
    enabled = camera.value == "enabled"
    set_by = latest_eye_event(db)
    c.fact(
        KEY_EYE_ENABLED,
        enabled,
        source="ledger",
        observed_at=getattr(set_by, "occurred_at", None) or c.now,
    )

    last_at: datetime | None = None
    reader = getattr(presence_runtime, "last_observation_at", None)
    if callable(reader):
        try:
            last_at = reader(source="camera")
        except Exception:  # noqa: BLE001 - one probe failing must not blank the answer
            last_at = None
    if last_at is None:
        if enabled:
            # The eye is on and no frame has been accepted: say so, do not guess.
            c.uncertain(KEY_EYE_LAST_OBSERVATION, "no_camera_observations")
        return
    ttl = _DEFAULT_CAMERA_TTL_S
    policy = getattr(presence_runtime, "policy", None)
    ttl_table = getattr(policy, "ttl_s", None)
    if isinstance(ttl_table, dict):
        ttl = float(ttl_table.get("camera", ttl))
    age = max(0.0, (c.now - _aware(last_at)).total_seconds())
    c.fact(
        KEY_EYE_LAST_OBSERVATION,
        round(age, 1),
        source="presence_engine",
        observed_at=last_at,
        stale=age > ttl,
    )


def _collect_presence(c: _Composer, snapshot: WorldSnapshot) -> None:
    fact = _snapshot_fact(snapshot, "owner.presence")
    if fact is None:
        c.uncertain(
            KEY_OWNER_PRESENCE, _snapshot_reason(snapshot, "owner.presence", "no_observations_yet")
        )
        return
    c.fact(
        KEY_OWNER_PRESENCE,
        fact.value,
        source="presence_engine",
        observed_at=fact.observed_at,
        confidence=fact.confidence,
        stale=fact.is_stale(now=c.now),
    )


def _collect_devices(
    c: _Composer, db: Session, snapshot: WorldSnapshot, broker_runtime: Any
) -> None:
    enrolled = _snapshot_fact(snapshot, "devices.enrolled_count")
    if enrolled is None:
        reason = _snapshot_reason(snapshot, "devices", "section_unavailable")
        c.uncertain(KEY_DEVICES_ONLINE, reason)
        return
    if int(enrolled.value or 0) == 0:
        c.fact(KEY_DEVICES_ONLINE, {"count": 0, "names": []}, source="device_registry")
        return
    if broker_runtime is None:
        c.uncertain(KEY_DEVICES_ONLINE, "no_live_broker_runtime_supplied")
        return
    online_ids: list[str] = []
    for f in snapshot.facts:
        if f.key.startswith("devices.presence.") and f.value == "online":
            online_ids.append(f.key.rsplit(".", 1)[-1])
    names: list[str] = []
    if online_ids:
        try:
            from app.broker.models import Device

            rows = db.execute(
                select(Device).where(Device.id.in_([uuid.UUID(i) for i in online_ids]))
            ).scalars()
            names = sorted(str(r.name) for r in rows)
        except Exception:  # noqa: BLE001 - the count is still true without the names
            names = []
    c.fact(
        KEY_DEVICES_ONLINE,
        {"count": len(online_ids), "names": names},
        source="broker",
    )


def _collect_release(c: _Composer, db: Session) -> None:
    """``release.shadow_ready``: built modules waiting for the owner's decision."""
    try:
        from app.evolution.models import EvolutionOpportunity
    except ImportError:
        c.uncertain(KEY_RELEASE_SHADOW_READY, "section_unavailable")
        return
    try:
        rows = db.execute(
            select(EvolutionOpportunity).where(
                EvolutionOpportunity.status.in_(list(_AWAITING_OWNER_STATUSES))
            )
        ).scalars()
        count = sum(1 for _ in rows)
    except Exception:  # noqa: BLE001 - the table is absent on this schema: an honest gap
        c.uncertain(KEY_RELEASE_SHADOW_READY, "section_unavailable")
        return
    c.fact(KEY_RELEASE_SHADOW_READY, count, source="evolution_registry")


def _collect_tasks(c: _Composer, snapshot: WorldSnapshot) -> None:
    fact = _snapshot_fact(snapshot, "tasks.running_count")
    if fact is None:
        c.uncertain(KEY_TASKS_RUNNING, _snapshot_reason(snapshot, "tasks", "section_unavailable"))
        return
    c.fact(KEY_TASKS_RUNNING, int(fact.value or 0), source="task_registry")


def _snapshot_fact(snapshot: WorldSnapshot, key: str) -> Fact | None:
    for f in snapshot.facts:
        if f.key == key:
            return f
    return None


def _snapshot_reason(snapshot: WorldSnapshot, subject: str, default: str) -> str:
    for u in snapshot.uncertainties:
        if u.subject == subject:
            return u.reason
    return default


# ------------------------------------------------------------------ speech


_PRESENCE_SENTENCE: Final[dict[str, str]] = {
    "present": "Şu an sizi karşımda görüyorum.",
    "returned": "Az önce döndüğünüzü gördüm; şu an karşımdasınız.",
    "away": "Şu an sizi yerinizde görmüyorum.",
    "awake": "Şu an sizi uyanık ve hareketli görüyorum.",
    "resting": "Şu an sizi dinlenir hâlde görüyorum.",
    "likely_asleep": "Büyük olasılıkla dinleniyorsunuz; kesin değil.",
    "unknown": "Şu an geçerli bir varlık gözlemim yok.",
}
_NO_PRESENCE: Final = "Şu an geçerli bir varlık gözlemim yok."


def _seconds(value: float) -> str:
    return str(int(round(value)))


def _presence_sentence(facts: dict[str, LiveFact]) -> str:
    """Stale is said as stale (contract §4): the last verified observation is dated and
    the present is declared unverifiable, never rounded up to "still present"."""
    fact = facts.get(KEY_OWNER_PRESENCE)
    if fact is None:
        return _NO_PRESENCE
    if fact.stale:
        return (
            f"Son doğrulanmış varlık gözlemi {_seconds(fact.age_s)} saniye önceydi; "
            "şu an kesin doğrulayamıyorum."
        )
    return _PRESENCE_SENTENCE.get(str(fact.value), _NO_PRESENCE)


def _eye_sentence(facts: dict[str, LiveFact]) -> str:
    enabled = facts.get(KEY_EYE_ENABLED)
    if enabled is None:
        return "Gözün durumunu şu an doğrulayamıyorum."
    if not enabled.value:
        return "Göz kapalı efendim."
    last = facts.get(KEY_EYE_LAST_OBSERVATION)
    if last is None:
        return "Göz açık görünüyor ama hiç gözlem gelmedi; kamerayı doğrulayamıyorum."
    if last.stale:
        return (
            f"Göz açık görünüyor ama {_seconds(last.age_s)} saniyedir gözlem gelmiyor; "
            "kamerayı doğrulayamıyorum."
        )
    return f"Göz açık efendim; son gözlem {_seconds(last.age_s)} saniye önce."


def _voice_sentence(facts: dict[str, LiveFact]) -> str:
    fact = facts.get(KEY_VOICE_SESSION)
    if fact is None:
        return "Ses oturumunu şu an doğrulayamıyorum."
    return "Ses bağlı efendim." if fact.value == "connected" else "Ses bağlı değil efendim."


def _devices_sentence(facts: dict[str, LiveFact]) -> str:
    fact = facts.get(KEY_DEVICES_ONLINE)
    if fact is None:
        return "Cihazların durumunu şu an doğrulayamıyorum."
    count = int((fact.value or {}).get("count", 0))
    names = list((fact.value or {}).get("names") or [])
    if count == 0:
        return "Çevrimiçi cihaz yok efendim."
    listed = f": {', '.join(names)}" if names else ""
    return f"{cardinal(count).capitalize()} cihaz çevrimiçi{listed}."


def _release_sentence(facts: dict[str, LiveFact]) -> str:
    fact = facts.get(KEY_RELEASE_SHADOW_READY)
    if fact is None:
        return "Dağıtım durumunu şu an doğrulayamıyorum."
    count = int(fact.value or 0)
    if count == 0:
        return "Canlıya alınmayı bekleyen modül yok efendim."
    return f"{cardinal(count).capitalize()} modül canlıya alınmayı bekliyor efendim."


def _all_speech(facts: dict[str, LiveFact]) -> str:
    """Result first, at most three sentences (contract §4): the flags in one breath,
    then the presence claim, then what is waiting or running - only when something is."""
    core = facts.get(KEY_CORE_HEALTH)
    flags: list[str] = []
    if core is not None:
        flags.append(
            {
                "ok": "Cloud Core sağlıklı",
                "degraded": "Cloud Core kısmen sağlıklı",
                "fail": "Cloud Core veritabanına ulaşamıyor",
            }.get(str(core.value), "Cloud Core durumunu doğrulayamıyorum")
        )
    else:
        flags.append("Cloud Core durumunu doğrulayamıyorum")
    voice = facts.get(KEY_VOICE_SESSION)
    if voice is not None:
        flags.append("ses bağlı" if voice.value == "connected" else "ses bağlı değil")
    eye = facts.get(KEY_EYE_ENABLED)
    if eye is not None:
        flags.append("göz açık" if eye.value else "göz kapalı")
    sentences = [", ".join(flags) + "."]

    if eye is None or eye.value:
        sentences.append(_presence_sentence(facts))

    pending: list[str] = []
    shadow = facts.get(KEY_RELEASE_SHADOW_READY)
    if shadow is not None and int(shadow.value or 0) > 0:
        pending.append(f"{cardinal(int(shadow.value))} modül canlıya alınmayı bekliyor")
    tasks = facts.get(KEY_TASKS_RUNNING)
    if tasks is not None and int(tasks.value or 0) > 0:
        pending.append(f"{cardinal(int(tasks.value))} iş çalışıyor")
    if pending:
        joined = "; ".join(pending)
        sentences.append(joined[0].upper() + joined[1:] + ".")
    return " ".join(sentences)


def _speech(scope: str, facts: dict[str, LiveFact]) -> str:
    if scope == SCOPE_EYE:
        return _eye_sentence(facts)
    if scope == SCOPE_VOICE:
        return _voice_sentence(facts)
    if scope == SCOPE_PRESENCE:
        eye = facts.get(KEY_EYE_ENABLED)
        if eye is not None and not eye.value:
            return "Göz kapalı; varlık gözlemi yapmıyorum efendim."
        return _presence_sentence(facts)
    if scope == SCOPE_DEVICES:
        return _devices_sentence(facts)
    if scope == SCOPE_RELEASE:
        return _release_sentence(facts)
    return _all_speech(facts)


# ------------------------------------------------------------------ composer


def compose_live_state(
    db: Session,
    *,
    scope: str = SCOPE_ALL,
    session_id: uuid.UUID | None = None,
    now: datetime | None = None,
    presence_runtime: Any | None = None,
    broker_runtime: Any | None = None,
    health: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The ``state.now`` result (contract §4): facts, uncertainties and the sentence.

    ``presence_runtime`` / ``broker_runtime`` are the live in-process engines, injected
    the way the World Model routes inject them (this module never imports a singleton at
    module scope). ``health`` is a health-check result, or a callable that produces one;
    without it the Cloud Core reports the one probe it can make itself (its database).
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope!r}")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    health_results: dict[str, Any] | None
    if callable(health):
        try:
            health_results = dict(health() or {})
        except Exception:  # noqa: BLE001 - a broken probe is "unknown", not an exception
            health_results = None
    else:
        health_results = dict(health) if health else None

    snapshot = assemble_snapshot(
        db,
        broker_runtime=broker_runtime,
        presence_runtime=presence_runtime,
        health_results=health_results,
        now=moment,
    )
    c = _Composer(moment)
    for name, fn in (
        ("core", lambda: _collect_core(c, db, health_results)),
        ("voice", lambda: _collect_voice(c, db, session_id)),
        ("eye", lambda: _collect_eye(c, db, snapshot, presence_runtime)),
        ("presence", lambda: _collect_presence(c, snapshot)),
        ("devices", lambda: _collect_devices(c, db, snapshot, broker_runtime)),
        ("release", lambda: _collect_release(c, db)),
        ("tasks", lambda: _collect_tasks(c, snapshot)),
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - one source down must not blank the answer
            logger.warning(
                "live_state_section_failed", section=name, error=f"{type(exc).__name__}: {exc}"
            )
            c.uncertain(name, "section_unavailable")

    keys = _KEYS_BY_SCOPE.get(scope)
    facts = dict(c.facts) if keys is None else {k: v for k, v in c.facts.items() if k in keys}
    uncertainties = (
        dict(c.uncertainties)
        if keys is None
        else {k: v for k, v in c.uncertainties.items() if k in keys}
    )
    return {
        "query_kind": QUERY_EYE_STATE if scope == SCOPE_EYE else QUERY_WORLD_STATE,
        "subsystem": SUBSYSTEM,
        "scope": scope,
        "observed_at": _iso(moment),
        "facts": [f.as_dict() for f in facts.values()],
        "uncertainties": [u.as_dict() for u in uncertainties.values()],
        "speech": _speech(scope, facts),
    }


def record_state_answered(
    db: Session,
    result: dict[str, Any],
    *,
    session_id: uuid.UUID | None,
    now: datetime | None = None,
) -> None:
    """Ledger ``voice.state_answered`` (contract §4): fact keys, uncertainty subjects and
    the stale keys - never the sentence. Best-effort, like every ledger note."""
    moment = now or datetime.now(UTC)
    facts = [str(f.get("key")) for f in result.get("facts") or [] if isinstance(f, dict)]
    stale = [
        str(f.get("key"))
        for f in result.get("facts") or []
        if isinstance(f, dict) and bool(f.get("stale"))
    ]
    uncertainties = [
        str(u.get("subject")) for u in result.get("uncertainties") or [] if isinstance(u, dict)
    ]
    try:
        ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_VOICE_STATE_ANSWERED,
                subsystem=SUBSYSTEM_VOICE,
                action="state.now",
                factual_summary=(
                    f"Sahibe canlı durum söylendi ({result.get('scope')}): "
                    f"{len(facts)} olgu, {len(uncertainties)} belirsizlik, {len(stale)} bayat."
                ),
                occurred_at=moment,
                detail_json={
                    "query_kind": result.get("query_kind"),
                    "scope": result.get("scope"),
                    "facts": facts,
                    "uncertainties": uncertainties,
                    "stale": stale,
                },
                source="live",
                source_ref=f"voice_state_answered:{session_id}:{moment.isoformat()}",
                evidence_refs=(
                    [{"kind": "realtime_session", "ref": str(session_id)}] if session_id else []
                ),
            ),
        )
    except Exception:  # noqa: BLE001 - evidence, not a dependency
        logger.warning("voice_state_answered_ledger_failed")


__all__ = [
    "KEY_CORE_HEALTH",
    "KEY_DEVICES_ONLINE",
    "KEY_EYE_ENABLED",
    "KEY_EYE_LAST_OBSERVATION",
    "KEY_OWNER_PRESENCE",
    "KEY_RELEASE_SHADOW_READY",
    "KEY_TASKS_RUNNING",
    "KEY_VOICE_SESSION",
    "SCOPES",
    "SCOPE_ALL",
    "SCOPE_DEVICES",
    "SCOPE_EYE",
    "SCOPE_PRESENCE",
    "SCOPE_RELEASE",
    "SCOPE_VOICE",
    "SUBSYSTEM",
    "LiveFact",
    "LiveUncertainty",
    "compose_live_state",
    "record_state_answered",
]
