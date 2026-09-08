"""FastAPI application entrypoint.

Run locally:
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.alarms.audio_store import get_audio_store
from app.alarms.greeting_audio import build_greeting_tts
from app.alarms.routes import audio_router as alarms_audio_router
from app.alarms.routes import router as alarms_router
from app.alarms.routine_port import WakeAlarmRunner
from app.alarms.sequence import WakeSequence
from app.ambient.routes import router as ambient_router
from app.appfactory.routes import router as apps_router
from app.appfactory.service import AppFactoryService
from app.artifacts.render_fetch_store import get_render_fetch_store
from app.artifacts.routes import device_router as artifacts_device_router
from app.artifacts.routes import router as artifacts_router
from app.artifacts.runtime import ArtifactRuntime
from app.broker.routes import router as broker_router
from app.broker.runtime import BrokerRuntime
from app.broker.ws import router as broker_ws_router
from app.calendar.providers import build_calendar_provider, build_calendar_writer
from app.calendar.routes import router as calendar_router
from app.calendar.service import CalendarService
from app.config import Settings, get_settings
from app.creative3d.routes import router as scenes_router
from app.creative3d.service import SceneService
from app.db import build_engine, build_session_factory
from app.devices.commands import DeviceCommandClient, register_broker_runtime
from app.devices.routes import router as devices_router
from app.devices.status import get_status_registry
from app.documents.service import DocumentService
from app.evolution.routes import router as evolution_router
from app.evolution.runtime import EvolutionRuntime
from app.experience.routes import router as experience_router
from app.genesis.routes import router as genesis_router
from app.genesis.runtime import GenesisRuntime
from app.genesis.service import register_genesis_service
from app.goals.routes import router as goals_router
from app.health import run_health_checks
from app.identity.routes import router as identity_router
from app.identity.runtime import IdentityRuntime
from app.ledger import service as ledger_service
from app.ledger.routes import router as ledger_router
from app.logging import configure_logging, get_logger
from app.mail.providers import build_mail_provider, build_mail_sender
from app.mail.routes import router as mail_router
from app.mail.service import MailService
from app.memory.routes import router as memory_router
from app.memory.runtime import MemoryRuntime
from app.middleware import TraceIdMiddleware
from app.mobile.routes import router as mobile_router
from app.mobile.runtime import MobileRuntime
from app.narration.routes import router as narration_router
from app.operator.service import OperatorService, register_operator_service
from app.presence.routes import router as presence_router
from app.release.routes import router as release_router
from app.release.version import release_model
from app.research.browser_gateway import UnwiredBrowserGateway
from app.research.embedded_worker import EmbeddedWorkerRuntime
from app.research.health import research_health
from app.research.routes import router as research_router
from app.routines.clock import RoutineClock, register_routine_clock, routine_clock_health
from app.routines.dispatch import (
    ActionDispatcher,
    BrokerDeviceAction,
    RealtimeSayBriefing,
    register_routine_dispatcher,
)
from app.routines.models import Routine
from app.routines.routes import router as routines_router
from app.security.routes import router as security_router
from app.security.runtime import SecurityRuntime
from app.selfhealing.routes import router as selfhealing_router
from app.selfhealing.runtime import SelfHealingRuntime
from app.selfmodel.routes import router as selfmodel_router
from app.state.routes import router as state_router
from app.uistate import UiState
from app.uistate import publish as publish_ui_state
from app.uistate.routes import router as ui_state_router
from app.voice.qualification.routes import router as voice_qualification_router
from app.voice.realtime_sessions.research_announcer import ResearchToolCallAnnouncer
from app.voice.realtime_sessions.routes import router as voice_realtime_router
from app.voice.realtime_sessions.runtime import RealtimeVoiceRuntime
from app.voice.routes import router as voice_router
from app.voice.runtime import VoiceRuntime
from app.worldmodel.routes import router as world_router

configure_logging()
logger = get_logger("app.main")


class UTF8JSONResponse(JSONResponse):
    """``application/json; charset=utf-8`` on every JSON response.

    The body was always UTF-8 (the database holds ``ş``/``ğ`` correctly); without
    the charset a Windows PowerShell 5.1 client decoded it as Latin-1 and showed
    the owner ``Ã``/``Å`` mojibake in a real qualification record. Declaring it
    fixes every RFC-conformant client; the repository's own scripts decode bytes as
    UTF-8 regardless.
    """

    media_type = "application/json; charset=utf-8"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    broker = BrokerRuntime(settings)
    artifacts = ArtifactRuntime(settings)
    voice = VoiceRuntime(settings)
    memory = MemoryRuntime(settings)
    selfhealing = SelfHealingRuntime(settings)
    evolution = EvolutionRuntime(settings)
    genesis = GenesisRuntime(evolution)
    # M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6): the module-wide registry the ONE
    # router's turn handler reads to decide what a bare "Onaylıyorum."/"Vazgeç." means —
    # the same discipline app.operator.service.register_operator_service follows for the
    # ringing-aware Cancel/Status pair.
    register_genesis_service(genesis.service)
    security = SecurityRuntime(settings)
    identity = IdentityRuntime(settings)
    mobile = MobileRuntime(settings)
    # M12: realtime voice sessions push sideband messages over the broker's
    # device WebSocket, so the runtime is handed the broker (never a socket).
    # M18.2 follow-up to ADR-0067: also handed the ArtifactRuntime, so a spoken
    # "araştır" (research.start) can create the same real task/run REST does.
    voice_realtime = RealtimeVoiceRuntime(settings, broker=broker, artifacts=artifacts)
    # M13/ADR-0050 §9: the research pipeline's fetch activities dispatch
    # device commands through THIS process's BrokerRuntime for immediate
    # delivery (app.devices.commands); embedded_worker optionally runs the
    # Temporal worker in-process too (PAGENTOS_WORKER_MODE=embedded).
    embedded_worker = EmbeddedWorkerRuntime(settings)
    # M18.2 DEFECT 2 / ADR-0067: the research Temporal worker holds no sideband
    # registrations (same reason mobile.announcer runs here, not in the worker) — this
    # sweeper watches ResearchRunRow/ResearchReportRow for a run started by a
    # research.start tool call and completes that call once the run is terminal, so
    # the model receives spoken_result instead of the conversation stalling forever.
    research_tool_call_announcer = ResearchToolCallAnnouncer(
        voice_realtime.session, voice_realtime.sideband
    )

    def _routine_label(routine_id: Any) -> str | None:
        """Best-effort alarm label lookup (ADR-0060) — never raises: a routine name is a
        nicety on the alarm payload, not a precondition for ringing it."""
        try:
            with artifacts.session() as session:
                routine = session.get(Routine, routine_id)
                return routine.name if routine is not None else None
        except Exception:  # noqa: BLE001 - a label lookup must never break dispatch
            return None

    # A dedicated engine/session factory (mirrors app.research.browser_activities' own
    # `_session_factory()`): app.devices.commands.DeviceCommandClient wants a plain
    # `sessionmaker`, not ArtifactRuntime's context-manager `.session()`. Shared by the
    # routine dispatcher, the wake sequence and the routine clock — one connection pool for
    # everything that runs off the event loop.
    dispatch_engine = build_engine(settings.database_url)
    dispatch_session_factory = build_session_factory(dispatch_engine)

    device_action = BrokerDeviceAction(
        session_factory=dispatch_session_factory,
        command_client=DeviceCommandClient(dispatch_session_factory),
    )
    # M18.3 (spec §3.5, §3.7): the wake sequence, and the TTS provider for its greeting.
    # The provider is resolved through the voice runtime's own registry, so a process with
    # no OpenAI key gets the offline fake and the greeting still has a truthful path —
    # never a silent alarm because a key is missing.
    wake_sequence = WakeSequence(
        device_action=device_action,
        tts=build_greeting_tts(settings),
        audio_store=get_audio_store(),
        broker_audio_origin=settings.alarm_audio_origin,
    )
    # docs/DECISIONS.md ADR-0078: the alarm/display voice tools read the wake sequence
    # and the device-status registry from ToolContext.live (tools_ambient._sequence,
    # display_status). They are registered HERE, where they are built, on the same
    # objects app.state exposes below - a tool call and a route must never see two.
    # M19 (docs/M19_DIGITAL_OPERATOR_SPEC.md §4): the Digital Operator's runtime, on the
    # SAME BrokerDeviceAction object the wake sequence already holds - one device port,
    # never a second desktop-control path. Registered on the module-wide registry too
    # (app.operator.service), so the ONE router's ringing-aware Cancel/Status pair can
    # ask "is a task running?" from record_client_events, which builds no ToolContext.
    operator_service = OperatorService()
    register_operator_service(operator_service)
    # M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3): File & Document Intelligence's
    # own service, reading the SAME device port every other family holds — one desktop/
    # file authority, never a second path. No process-wide registry of its own (unlike
    # OperatorService): nothing outside a tool call needs to ask "is a read running?".
    document_service = DocumentService()
    # M21 (docs/M21_MAIL_CALENDAR_SPEC.md §2, §3, ADR-0084): Mail & Calendar's own
    # providers, built from settings — never a fake in production (module docstrings of
    # app.mail.providers / app.calendar.providers). With nothing configured the provider
    # (and therefore the service) is honest about `account_missing`.
    mail_service = MailService(build_mail_provider(settings), build_mail_sender(settings))
    calendar_service = CalendarService(
        build_calendar_provider(settings), build_calendar_writer(settings)
    )
    # M23 (docs/M23_APP_FACTORY_SPEC.md §1-§4, ADR-0086): the App Factory's own service,
    # reading the SAME device port every other family holds — one desktop authority,
    # never a second path. The browser gateway is the M13 seam
    # (``app.research.browser_gateway.BrowserGateway``); the real dispatch path over
    # ``browser.*`` device commands is not wired for a synchronous voice tool call yet
    # (ADR-0035 — the research pipeline dispatches it through Temporal instead), so
    # ``UnwiredBrowserGateway`` is registered here: inert, raises before any I/O, exactly
    # like ``document_service``'s own honest ``capability_missing`` when no device runtime
    # exists at all. ``app.appfactory.service`` catches that failure and returns a
    # truthful refused receipt rather than crashing the tool call.
    app_factory_service = AppFactoryService()
    browser_gateway = UnwiredBrowserGateway()
    # M25 (docs/M25_CREATIVE_3D_SPEC.md §2-§4, ADR-0088): 3D Creation's own service,
    # reading the SAME device port every other family holds (project.scaffold/
    # project.run, extended by the windows-engineer track with the two 3D runtimes and
    # scene.inspect) — one desktop authority, never a second path. Renders share the
    # SAME object store the Artifact Factory already uses (``artifacts.store``): one
    # bucket, one provider interface, never a second one for the same kind of file.
    creative3d_service = SceneService(object_store=artifacts.store)
    voice_realtime.register_live(
        wake_sequence=wake_sequence,
        device_statuses=get_status_registry(),
        # M18.4 (spec §4): the owner's voice over self-evolution reads the supervisor and
        # the engine through the same live-source path as every other tool.
        evolution_runtime=evolution,
        evolution_service=evolution.evolution_service,
        settings=settings,
        device_action=device_action,
        operator=operator_service,
        document_service=document_service,
        mail_service=mail_service,
        calendar_service=calendar_service,
        app_factory_service=app_factory_service,
        browser_gateway=browser_gateway,
        # M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6): capability.* reads the SAME
        # GenesisService the REST surface (app/genesis/routes.py) drives.
        genesis_service=genesis.service,
        # M25 (docs/M25_CREATIVE_3D_SPEC.md §5): scene.* reads the SAME SceneService
        # the REST surface (app/creative3d/routes.py) drives.
        creative3d_service=creative3d_service,
    )

    def _build_routine_dispatcher() -> ActionDispatcher:
        return ActionDispatcher(
            briefing=RealtimeSayBriefing(
                session_factory=dispatch_session_factory, sideband=voice_realtime.sideband
            ),
            device_action=device_action,
            routine_label=_routine_label,
            wake_alarm=WakeAlarmRunner(
                session_factory=dispatch_session_factory, sequence=wake_sequence
            ),
        )

    routine_dispatcher = _build_routine_dispatcher()

    def _build_routine_clock() -> RoutineClock:
        """M18.3 spec §3.3. The three ticks, in order, each in the same worker thread and
        the same session: routines decide, alarms advance, ambient acts."""
        from app.alarms import service as alarms_service
        from app.ambient import service as ambient_service
        from app.routines import service as routines_service
        from app.routines.presence_link import greeting_verdict, resolve_greeting_decision

        def _evaluate_due(session: Any, now: Any) -> Any:
            decision = resolve_greeting_decision(session)
            allowed, reason = greeting_verdict(decision)
            from app.routines.conditions import RoutineConditionContext

            context = RoutineConditionContext(
                greeting_allowed=allowed,
                greeting_reason=reason,
                greeting_decision=decision,
            )
            return routines_service.evaluate_due(session, now=now, context=context)

        return RoutineClock(
            session_factory=dispatch_session_factory,
            evaluate_due=_evaluate_due,
            alarm_tick=lambda session, now: alarms_service.tick(
                session, sequence=wake_sequence, now=now
            ),
            ambient_tick=lambda session, now: ambient_service.tick(
                session, sequence=wake_sequence, now=now
            ),
            evolution_tick=lambda session, now: evolution.supervisor.scan(
                session, now=now, evolution_service=evolution.evolution_service
            ),
            interval_s=settings.routine_clock_interval_s,
            enabled=settings.routine_clock_enabled,
        )

    # M18.4 (spec §3): the Evolution Supervisor reads incidents and gaps through the
    # runtimes that own them, and scans on the routine clock after the owner-facing ticks.
    evolution.supervisor.bind(
        incidents=lambda status=None, limit=200: selfhealing.service.list_incidents(
            status=status, limit=limit
        ),
        gaps=lambda status="open", limit=100: evolution.gaps.list(status=status, limit=limit),
    )
    routine_clock = _build_routine_clock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await broker.start()
        register_broker_runtime(broker)
        # M18 (ADR-0060): the real RoutineDispatcher, alongside the broker runtime it
        # depends on. app.routines.service falls back to NoopDispatcher when nothing is
        # registered, so every unit test (none of which run this lifespan) is unaffected.
        register_routine_dispatcher(routine_dispatcher)
        # M18.3 (spec §3.3): the ONE named, owner-visible component that asks. Started
        # after the dispatcher it drives and stopped before it, so a tick can never find a
        # half-wired process.
        register_routine_clock(routine_clock)
        await routine_clock.start()
        await artifacts.start()
        # M9: the artifact-ready announcer runs HERE, in the process that holds
        # the push registrations. The Temporal worker only makes a task READY;
        # this drains READY-but-unannounced tasks (app/mobile/announcer.py).
        await mobile.announcer.start()
        await research_tool_call_announcer.start()
        await embedded_worker.start()
        # M16 track A: re-derive activity_events from canonical tables on every
        # start (spec §1.4, safe to call twice). Never blocks startup — an older
        # DB without the ledger tables yet, or any other backfill failure, is
        # logged and swallowed so a broken ledger can never take Cloud Core down.
        try:
            with artifacts.session() as ledger_session:
                report = await asyncio.to_thread(ledger_service.backfill, ledger_session)
            logger.info("ledger_backfill_at_startup", created=report.total_created)
        except Exception as exc:  # noqa: BLE001 - best-effort, never fatal
            logger.warning(
                "ledger_backfill_at_startup_failed", error=f"{type(exc).__name__}: {exc}"
            )
        logger.info("broker_started")
        # The bus's first word after a (re)start is a truthful idle, not silence: the
        # owner's M18 eye run (2026-09-06) read an EMPTY current state right after a
        # release, which the harness could not tell from "nothing ever happened". An
        # idle stamped by the system with a startup timestamp is a real fact; it is
        # never a voice state (no realtime session exists yet) and the first live
        # session's event replaces it.
        publish_ui_state(
            UiState.IDLE,
            subsystem="system",
            intensity=0.0,
            status="startup",
            label="api_started",
            metadata={"reason": "api_started"},
        )
        try:
            yield
        finally:
            await embedded_worker.stop()
            await research_tool_call_announcer.stop()
            await mobile.announcer.stop()
            await routine_clock.stop()
            register_routine_clock(None)
            register_routine_dispatcher(None)
            register_broker_runtime(None)
            await broker.stop()
            logger.info("broker_stopped")

    # M9: the interactive docs and the OpenAPI schema enumerate every endpoint
    # and request shape to an unauthenticated caller. Useful locally, gratuitous
    # once this is reachable over a network, so they follow the environment.
    docs_enabled = settings.environment == "dev"
    app = FastAPI(
        default_response_class=UTF8JSONResponse,
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    app.state.broker = broker
    app.state.artifacts = artifacts
    app.state.voice = voice
    app.state.memory = memory
    app.state.settings = settings
    app.state.selfhealing = selfhealing
    app.state.evolution = evolution
    app.state.genesis = genesis
    app.state.security = security
    app.state.identity = identity
    app.state.mobile = mobile
    app.state.voice_realtime = voice_realtime
    app.state.embedded_worker = embedded_worker
    # M18.3: the routes and the voice tools reach the device through these, injected
    # rather than imported as singletons (docs/M18_ACTION_CONTRACT.md §4).
    app.state.wake_sequence = wake_sequence
    app.state.routine_clock = routine_clock
    app.state.device_statuses = get_status_registry()
    app.state.alarm_audio_store = get_audio_store()
    # M22 (ADR-0085 addendum 5): the device render-fetch token store, injected the same
    # way as the alarm audio store above — the device-facing route reads it from
    # app.state so a test can swap it, and open_service mints into it either way.
    app.state.artifact_render_fetch_store = get_render_fetch_store()
    app.state.operator_service = operator_service
    app.state.document_service = document_service
    app.state.mail_service = mail_service
    app.state.calendar_service = calendar_service
    app.state.app_factory_service = app_factory_service
    app.state.creative3d_service = creative3d_service
    # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §4): POST /v1/artifacts/{id}/open reaches
    # the device through the SAME BrokerDeviceAction object the wake sequence, the
    # operator and the documents/mail/calendar families already hold above — one device
    # port, never a second desktop-control path.
    app.state.device_action = device_action
    # Scoped CORS: the web shell is a separate origin from the API. Allow only
    # the configured loopback/private web origins (never "*"); M0 review #3.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.web_origins),
        # PATCH/PUT/DELETE: the owner web UI must be able to correct/forget
        # memory and edit narration/pronunciation (constitution §9).
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        # M9: every non-health endpoint now requires an owner bearer session,
        # so the browser must be allowed to send Authorization.
        allow_headers=["Authorization", "Content-Type", "X-Trace-Id"],
        max_age=600,
    )
    app.add_middleware(TraceIdMiddleware)
    # M9/ADR-0027: the identity router is included first so the authentication
    # surface is registered before everything it protects.
    app.include_router(identity_router)
    app.include_router(broker_router)
    app.include_router(broker_ws_router)
    app.include_router(artifacts_router)
    # M23 (docs/M23_APP_FACTORY_SPEC.md §6, ADR-0086 addendum 2): the Cockpit's
    # Uygulamalar panel — owner-gated at the router level, the same service and device
    # port the voice tools call.
    app.include_router(apps_router)
    # M22 (ADR-0085 addendum 5): the device-facing render-fetch route is separate and
    # deliberately not owner-gated — the device holds a one-time token instead of a
    # session (app/artifacts/routes.py's device_router docstring).
    app.include_router(artifacts_device_router)
    app.include_router(voice_router)
    app.include_router(voice_realtime_router)
    app.include_router(narration_router)
    app.include_router(memory_router)
    app.include_router(selfhealing_router)
    app.include_router(evolution_router)
    app.include_router(genesis_router)
    app.include_router(security_router)
    app.include_router(mobile_router)
    app.include_router(research_router)
    app.include_router(ui_state_router)
    # M17 cognitive foundations: what was learned, what is being pursued, what is true now
    app.include_router(experience_router)
    app.include_router(goals_router)
    app.include_router(world_router)
    app.include_router(state_router)
    app.include_router(selfmodel_router)
    app.include_router(ledger_router)
    app.include_router(presence_router)
    # M18: durable TRIGGER -> CONDITIONS -> ACTIONS routines; evaluation is an explicit
    # call only (POST /v1/routines/evaluate), never a background timer at startup.
    app.include_router(routines_router)
    # M18.3: wake alarms, the ambient display policy, and one device-status read. The
    # alarms AUDIO router is separate and deliberately not owner-gated — the companion
    # holds a one-time token instead of a session (app/alarms/routes.py's docstring).
    app.include_router(alarms_router)
    app.include_router(alarms_audio_router)
    app.include_router(ambient_router)
    app.include_router(voice_qualification_router)
    app.include_router(release_router)
    app.include_router(devices_router)
    # M21 (docs/M21_MAIL_CALENDAR_SPEC.md §3): the Cockpit's approval pair for a pending
    # draft/proposal — owner-gated, the same require_owner_session dependency every other
    # router applies.
    app.include_router(mail_router)
    app.include_router(calendar_router)
    # M25 (docs/M25_CREATIVE_3D_SPEC.md §6): the Cockpit's "3B Sahne" panel — owner-
    # gated, the same require_owner_session dependency every other router applies.
    app.include_router(scenes_router)

    @app.get("/v1/system/health")
    async def system_health() -> dict[str, Any]:
        checks = await run_health_checks(settings)
        checks["broker"] = broker.health_check()
        # M3: object-store reachability for the artifact subsystem.
        checks["artifacts"] = await asyncio.to_thread(artifacts.health_check)
        # M4: voice-provider activation status (offline fakes vs key-gated reals).
        checks["voice"] = await asyncio.to_thread(voice.health_check)
        # M5: memory backend + embedder identity (native, deterministic by default).
        checks["memory"] = await asyncio.to_thread(memory.health_check)
        # M6: coding-backend identity + recovery-supervisor script availability.
        checks["selfhealing"] = await asyncio.to_thread(selfhealing.health_check)
        # M7: skill-generator identity + evolution sandbox posture.
        checks["evolution"] = await asyncio.to_thread(evolution.health_check)
        # M8: authorized-asset scope authority + defensive-collector posture.
        checks["security"] = await asyncio.to_thread(security.health_check)
        # M9: authentication posture (scheme, credential-root kind, windows).
        # Deliberately does not say whether an owner credential exists — this
        # is the one unauthenticated endpoint.
        checks["identity"] = await asyncio.to_thread(identity.health_check)
        # M9: push-transport posture (which real provider a credential would
        # activate) + the share/export bound. No I/O, no secrets.
        checks["mobile"] = await asyncio.to_thread(mobile.health_check)
        # M12: which ConversationRealtime provider a session would select, by
        # capability, and why (no I/O, no secrets).
        checks["voice_realtime"] = await asyncio.to_thread(voice_realtime.health_check)
        # M13: the embedded Temporal worker's own run state (ok when running,
        # "skipped" — not degraded — when PAGENTOS_WORKER_MODE is not
        # "embedded", e.g. every test and the "off"/"external" defaults).
        checks["temporal_worker"] = await asyncio.to_thread(embedded_worker.health_check)
        # M13: synthesis-provider configuration posture (no I/O, no secrets).
        checks["research"] = await asyncio.to_thread(research_health, settings)
        # M18.3 (spec §3.3): the routine clock is owner-visible BECAUSE it is the thing
        # that asks. "skipped" when no clock is registered (every test process); "fail"
        # when one was configured and is not running, which is exactly the state in which
        # no alarm would ever fire.
        checks["routine_clock"] = routine_clock_health()
        degraded = any(check["status"] not in ("ok", "skipped") for check in checks.values())
        status = "degraded" if degraded else "ok"
        logger.info("health_checked", status=status, checks=checks)
        # M18.4 (spec §2): WHAT is running - the release sha (or "unknown"), the app
        # version and every contract this process serves - so "hangi sürüm çalışıyor?"
        # and a release qualification read the same facts.
        return {
            "status": status,
            "version": __version__,
            "release": release_model(settings),
            "checks": checks,
        }

    return app


app = create_app()
