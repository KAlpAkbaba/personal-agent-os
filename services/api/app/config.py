"""Application settings.

All values have dev-only defaults matching infra/docker/docker-compose.dev.yml
(loopback-bound local containers). Real deployments override via environment
variables or a .env file. No secrets live in this file.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: services/api — the identity root lives beside the service, outside the repo's
#: tracked tree (see .gitignore), never inside a database.
_API_ROOT = Path(__file__).resolve().parents[1]

#: PRODUCT DECISION (owner, 2026-09-04): DuckDuckGo is the default production
#: search provider for Research; Google stays fully implemented (including
#: the CAPTCHA/owner-handoff machinery, which must not be weakened) but is no
#: longer attempted first automatically. "auto" lets the device worker's own
#: provider order decide.
_RESEARCH_SEARCH_PROVIDERS = ("duckduckgo", "google", "auto")

#: ADR-0177 (owner, 2026-09-19): "Her şeyi kendi Chrome'umda, gözümün önünde yapsın" —
#: page FETCHING (not discovery/search, which is unchanged) reads through the owner's own,
#: already-open Chrome window by default ("owner"); "worker" is the pre-ADR-0177 background
#: browser worker (app.research.browser_gateway.DeviceBrowserGateway), kept fully selectable
#: as a fallback path and for environments with no interactive owner desktop.
_RESEARCH_BROWSERS = ("owner", "worker")

#: ADR-0091. Mirrors app.weather.providers.WEATHER_PROVIDERS as a literal (not an
#: import) — the same reason _RESEARCH_SEARCH_PROVIDERS above is a literal rather than
#: importing app.research.*: this settings module must not gain a heavier import chain
#: just to validate one string.
_WEATHER_PROVIDERS = ("open_meteo", "none")


def _default_identity_root_dir() -> str:
    return str(_API_ROOT / "var" / "identity")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PAGENTOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "pagentos-api"
    environment: str = "dev"
    #: M18.4 (spec §2): the git sha the release script exported for THIS process, and the
    #: host's last-known-good pointer when it exported one. Empty means "unknown" and is
    #: reported as unknown - never guessed from the tree.
    release: str = ""
    last_known_good: str = ""

    # PostgreSQL (compose service "postgres", host port 15432)
    database_url: str = "postgresql+psycopg://pagentos:pagentos-dev@127.0.0.1:15432/pagentos"

    # Redis (compose service "redis", host port 16379)
    redis_url: str = "redis://127.0.0.1:16379/0"

    # MinIO / S3 (compose service "minio", host port 19000)
    s3_endpoint_url: str = "http://127.0.0.1:19000"
    s3_access_key: str = "minioadmin"  # dev-only default for local MinIO
    s3_secret_key: str = "minioadmin"  # dev-only default for local MinIO
    s3_bucket: str = "pagentos-artifacts"
    s3_region: str = "us-east-1"

    # Temporal (compose service "temporal", host port 17233)
    temporal_address: str = "127.0.0.1:17233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "pagentos-core"

    # Health check budget per dependency, seconds.
    health_check_timeout_s: float = 2.0

    # CORS: explicit allowlist of web origins permitted to call the API from a
    # browser. Loopback dev origins only by default; NEVER "*". Production sets
    # PAGENTOS_WEB_ORIGINS to the real (Tailscale/private) web origin.
    web_origins: tuple[str, ...] = (
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:3100",
        "http://localhost:3100",
    )

    # Voice providers (M4). Keys are an OWNER ACTION: empty by default so the
    # real adapters stay inert (PROVIDER_AUTH_MISSING) and never call out in
    # tests. Set e.g. PAGENTOS_VOICE_ELEVENLABS_API_KEY in the environment to
    # activate a real provider. NEVER commit real keys.
    voice_elevenlabs_api_key: str = ""
    voice_azure_speech_key: str = ""
    voice_azure_speech_region: str = "westeurope"
    voice_openai_api_key: str = ""
    # Secret used to derive the Fernet key that encrypts stored speaker profiles
    # (derived embeddings, never raw audio). Dev-only default; production sets
    # PAGENTOS_VOICE_PROFILE_SECRET to a real secret. Matches the minio dev-default
    # convention (loopback-only, not a production credential).
    voice_profile_secret: str = "pagentos-dev-voice-profile-secret"
    voice_speaker_object_prefix: str = "voice/speaker"
    voice_benchmark_object_prefix: str = "voice/benchmark"

    # Realtime voice (M12, ADR-0034/0035). ConversationRealtime providers are
    # chosen by declared capability; this list only orders otherwise-equal
    # candidates. The simulator needs no key and is always registered. The
    # per-session provider credential is short-lived (seconds) and the session
    # record itself expires after its TTL.
    voice_realtime_provider_preference: tuple[str, ...] = ("openai-realtime", "simulator")
    voice_realtime_credential_ttl_s: int = 600
    #: 0 (the default) means a voice session NEVER expires on a timer -- it ends when the
    #: owner ends it. Any positive value restores a fixed horizon measured from creation,
    #: which is what closed the owner's conversations at exactly one hour while they were
    #: still talking (ADR-0105). It is not a keepalive interval and never was: nothing
    #: renewed it, so a larger number would only move the surprise further out.
    voice_realtime_session_ttl_s: int = 0
    # The deterministic simulator is a GATE, not a product path (ADR-0036 §2). It
    # is registered as a ConversationRealtime candidate only in environment=dev,
    # or when this flag is set explicitly, so a production session can never be
    # answered by the simulator when the real adapter is missing its key.
    voice_realtime_simulator_enabled: bool = False
    # OpenAI Realtime adapter (M12 track B, ADR-0038). The ONLY place a realtime
    # model name may appear outside the adapter module. Voice + eagerness are
    # defaults the owner tunes after real Turkish A/B runs; nothing here is a
    # quality claim. Turkish quality on this provider is UNVERIFIED by the vendor
    # docs (docs/research/realtime-providers-2026-09.md §1.5) and is measured on
    # the owner's machine, never assumed.
    voice_realtime_openai_base_url: str = "https://api.openai.com/v1"
    voice_realtime_openai_model: str = "gpt-realtime-2.1"
    # The owner's A/B verdict (2026-09-03): cedar is closer to the Arbor target.
    voice_realtime_openai_voice: str = "cedar"
    # ADR-0043: the owner's perceptual target is ChatGPT's "Arbor", which the Realtime
    # API does not expose (live discovery 2026-09-02). The profile is realised through
    # the closest supported voice + the style block in the persona + output pacing;
    # "none" disables the style block. If the vendor ever exposes it, set the voice.
    voice_realtime_owner_target_voice_profile: str = "arbor"
    voice_realtime_voice_candidates: tuple[str, ...] = ("marin", "cedar")
    voice_realtime_openai_speed: float = 1.0  # audio.output.speed; moderate pace = 1.0
    voice_realtime_openai_eagerness: str = "low"  # low | medium | high | auto
    voice_realtime_openai_transcription_model: str = "gpt-4o-transcribe"
    voice_realtime_openai_timeout_s: float = 15.0

    # Device broker (M1). Heartbeat interval is sent to agents in the welcome
    # frame; liveness timeout is heartbeat_interval * liveness_factor.
    broker_heartbeat_interval_s: float = 10.0
    broker_liveness_factor: float = 2.5
    broker_enrollment_token_ttl_s: int = 900
    broker_sweep_interval_s: float = 5.0
    broker_default_command_timeout_s: float = 300.0
    #: B05 req 663/246: whether the server-side device-command gate BLOCKS or only counts.
    #: Ships as "shadow" on purpose - the roadmap's rollback plan is that a gate's first
    #: appearance in production must not be an outage. Flipping to "enforce" is an owner
    #: decision, made after a day of shadow counting says what it would have refused.
    device_command_gate_mode: str = "shadow"
    #: B05 req 245/247/248/666: whether the voice step-up policy BLOCKS or only counts.
    #: Shadow, and for a second reason beyond caution: the only thing that produces a
    #: speaker verdict today is a REST endpoint nothing calls automatically, so enforcing
    #: now would refuse every sensitive voice action. Enforcement needs the realtime probe
    #: flow first, and then the owner's decision.
    voice_step_up_mode: str = "shadow"
    #: B07 req 679: the audit-retention sweep counts what it would remove and removes
    #: nothing until this is false. A retention sweep is the one kind of housekeeping whose
    #: bug is unrecoverable, and the roadmap's rollback plan for this batch is a dry run
    #: first - "it would have removed 9,000 rows" is a sentence the owner gets to read
    #: before it becomes true.
    audit_retention_dry_run: bool = True
    #: B08 req 646/648: where the host keeps LAST_BACKUP.json and the drill reports. The API
    #: runs in a container and this lives on the host, so where it is not mounted the check
    #: reports "skipped, not visible" rather than inventing a failure it cannot observe.
    backup_root: str = "/var/lib/pagentos-backup"
    broker_handshake_timeout_s: float = 10.0

    #: B15 req 281: whether the wake alarm reads the morning briefing aloud after its
    #: greeting. On by default, because a morning briefing nobody hears without opening a
    #: browser is the gap this batch exists to close - and off by one setting, because the
    #: roadmap's own rollback plan for this batch is "turn the briefing extension off and
    #: keep the existing greeting", which has to be one flag and not a revert.
    alarm_briefing_enabled: bool = True

    #: B17 req 39: whether what this system knows about its owner reaches the model's
    #: instructions. On by default, because a memory nothing reads is a write-only
    #: diary - and off by one setting, because the roadmap's rollback plan for B17 is
    #: "turn the injection off and the persona goes back to what it was", which has to
    #: be a flag and not a revert.
    memory_injection_enabled: bool = True
    #: B37 req 51/53: which embedding provider serves memory retrieval -
    #: "deterministic" (the seeded n-gram hash, offline, NOT semantic), "openai"
    #: (text-embedding-3-small at the index width; needs a key) or "auto" (OpenAI when a
    #: key is configured, otherwise deterministic). Every fallback is reported with its
    #: reason by /v1/system/health and /v1/memory/embedding.
    memory_embedding_provider: str = "auto"
    memory_embedding_model: str = "text-embedding-3-small"

    # M18.3 (spec §3.3): the routine clock — the ONE named, owner-visible component that
    # asks "is anything due?". The routines package still has no timer of its own and
    # `evaluate_due` is still its only entry point; this is the thing that calls it, on a
    # cadence the owner can see on the health manifest and turn off here. Disabled in every
    # unit test by never running the lifespan; set PAGENTOS_ROUTINE_CLOCK_ENABLED=false to
    # turn it off in a real process (an alarm will then never fire on its own — which is
    # exactly why it is a setting and not a hidden constant).
    routine_clock_enabled: bool = True
    routine_clock_interval_s: float = 10.0
    #: M18.4 (spec §3.4): the Evolution Supervisor's scan rides the routine clock and runs
    #: at most every ``evolution_supervisor_interval_s``; disabled means no scan at all.
    evolution_supervisor_enabled: bool = True
    evolution_supervisor_interval_s: float = 300.0
    #: B18 req 71: the Experience Engine's pass, riding the same clock with its own
    #: interval. `ingest` has been complete since it was written and the only caller was a
    #: manual POST - 1441 activity events and 0 memories, measured. Fifteen minutes,
    #: because the ledger is written by things that take minutes and a derivation pass
    #: faster than its input is just re-reading.
    experience_ingest_enabled: bool = True
    experience_ingest_interval_s: float = 900.0
    #: Phase 8 (2026-09-11): the memory / owner-session / authorised-asset expiry sweeps
    #: that existed and nothing ran (app.maintenance). 0 disables; the first pass waits
    #: the initial delay so a booting process is not doing housekeeping.
    retention_sweep_interval_s: float = 3600.0
    retention_sweep_initial_delay_s: float = 300.0
    # M18.3 §3.7: the origin the COMPANION uses to fetch a greeting WAV. Empty means "the
    # same origin the device already talks to"; the Device Service validates the URL's
    # origin against its own configured broker REST origin before forwarding, so a wrong
    # value here fails closed on the device rather than fetching from somewhere else.
    alarm_audio_origin: str = ""
    # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §4, ADR-0085 decision 4): the origin a
    # VOICE-triggered ``artifact.open`` builds its render download URL from — there is
    # no live HTTP request to read a base_url from the way the REST
    # ``POST /v1/artifacts/{id}/open`` route can. Empty means "not configured yet"; a
    # real deployment sets it to the same origin devices already dial (the same rule
    # ``alarm_audio_origin`` follows for the greeting WAV).
    artifact_download_origin: str = ""
    # B42 (req 412): the owner's delete policy for artifacts - confirm (the default:
    # an explicit yes in the same call), deny, or free. Checkpoint 14 is the owner's.
    # Owner decision 2026-09-19 (row 412): no second word before a delete - "free".
    artifact_delete_policy: str = "free"
    # B43 (req 492): the image provider - local (Pillow, deterministic; generation
    # refused by name) or openai (gpt-image-1 over the owner's voice_openai_api_key).
    creative_image_provider: str = "local"
    creative_openai_image_model: str = "gpt-image-1"
    creative_image_timeout_s: float = 60.0
    # B45 (req 360): the inbox checked on the routine clock - a no-op without an account.
    mail_poll_enabled: bool = True
    mail_poll_interval_s: float = 300.0

    # Devices layer (M13 track C, PROJECT_CONSTITUTION.md §11a). Presence is
    # "online" (live WS) vs "stale" (recently seen but disconnected, within
    # this gap) vs "offline"; selection only ever picks an "online" device.
    device_presence_stale_after_s: float = 30.0

    # M13 research pipeline (ADR-0050). Temporal worker mode: "off" (tests,
    # default — nothing starts in-process), "embedded" (the API process runs
    # the worker in-process; production compose, no separate worker
    # container), "external" (the standalone `python -m app.worker`, unchanged).
    worker_mode: str = "off"

    # Synthesis providers (app.research.synthesis, M13_RESEARCH_SPEC.md §6).
    # Deterministic is always available offline; OpenAI/Anthropic are inert
    # (SynthesisNotConfiguredError, no I/O) without a key. `openai_api_key` is
    # checked first, falling back to the already-installed
    # `voice_openai_api_key` (same owner key, nothing new to install) — env
    # vars PAGENTOS_OPENAI_API_KEY / PAGENTOS_ANTHROPIC_API_KEY per spec.
    openai_api_key: str = ""
    # B29 req 105 (app.operator.vision): screenshot understanding behind a provider
    # interface. "auto" = OpenAI when an OpenAI key is configured (the same owner key the
    # synthesis and the realtime voice use), otherwise no provider and the tool answers
    # honestly that none is configured; "none" turns it off explicitly.
    vision_provider: str = "auto"
    vision_openai_model: str = "gpt-4o-mini"
    vision_request_timeout_s: float = 30.0
    research_openai_model: str = "gpt-4o-mini"
    research_openai_base_url: str = "https://api.openai.com/v1"
    research_openai_timeout_s: float = 30.0
    anthropic_api_key: str = ""
    # 2026-09-19: "claude-3-5-haiku-20241022" was retired - the Messages API answered 404 and
    # a run with 14 fetched pages and 3 verified sources failed at its last step.
    research_anthropic_model: str = "claude-haiku-4-5-20251001"
    research_anthropic_base_url: str = "https://api.anthropic.com"
    # ADR-0173 addendum (owner 2026-09-19, "Claude Haiku (çok ucuz ama ücretli)"): the model
    # that answers what the local mode's router does not understand. The alias, never a
    # dated id - a dated one was retired under this system the same day.
    assistant_chat_model: str = "claude-haiku-4-5"
    assistant_chat_timeout_s: float = 20.0
    research_anthropic_timeout_s: float = 30.0
    research_default_synthesis: str = "auto"
    research_default_max_sources: int = 12
    #: B33 req 472/473: the native factory's signing policy (app.nativefactory.signing).
    #: Owner decision 2026-09-16 ("win uygulamada da kendinden imzalı olsun"): the default is
    #: "test_certificate" - the device signs an MSIX with its own self-signed identity.
    #: "unsigned" is an explicit opt-out; "owner_certificate" is refused by name.
    native_signing_mode: str = "test_certificate"
    research_max_sources_ceiling: int = 30
    # PRODUCT DECISION (owner, 2026-09-04): DuckDuckGo is the DEFAULT production
    # search provider for Research; Google is not attempted first automatically
    # any more but stays fully implemented and selectable, including its
    # CAPTCHA/owner-handoff machinery (see ADR history in docs/DECISIONS.md).
    research_search_provider: str = "duckduckgo"

    @field_validator("research_search_provider")
    @classmethod
    def _validate_research_search_provider(cls, v: str) -> str:
        if v not in _RESEARCH_SEARCH_PROVIDERS:
            raise ValueError(
                "PAGENTOS_RESEARCH_SEARCH_PROVIDER must be one of "
                f"{_RESEARCH_SEARCH_PROVIDERS}, got {v!r}"
            )
        return v

    #: ADR-0177: which browser reads a research page. "owner" (default) drives the owner's
    #: own Chrome window (app.research.owner_browser_gateway.OwnerBrowserGateway), serially;
    #: "worker" is the pre-ADR-0177 background browser worker.
    research_browser: str = "owner"

    @field_validator("research_browser")
    @classmethod
    def _validate_research_browser(cls, v: str) -> str:
        if v not in _RESEARCH_BROWSERS:
            raise ValueError(
                f"PAGENTOS_RESEARCH_BROWSER must be one of {_RESEARCH_BROWSERS}, got {v!r}"
            )
        return v

    # Owner identity / API authentication (M9, ADR-0027).
    #
    # There is NO default credential. `identity_root_dir` holds the SHA-256 hash
    # of the one owner credential, minted by POST /v1/identity/bootstrap (a
    # one-time owner action) or by `python -m app.identity.recover --rotate`.
    # Until that file exists every protected endpoint refuses: fail closed.
    identity_root_dir: str = Field(default_factory=_default_identity_root_dir)
    # Bootstrap creates authority from nothing, so it additionally requires a
    # loopback peer: the owner is on the machine. Disable only for a deployment
    # whose network path is already owner-only (e.g. behind Tailscale).
    identity_bootstrap_loopback_only: bool = True
    # Absolute session lifetime and inactivity window, seconds. A session dies
    # at whichever comes first; refresh rotates the token and restarts both.
    session_ttl_s: int = 30 * 24 * 3600  # 30 days
    session_idle_timeout_s: int = 7 * 24 * 3600  # 7 days
    #: B05 req 658: the ceiling a refresh cannot lift. `session_ttl_s` is renewed on every
    #: refresh, so before this a token that leaked into a log or a backup lived for ever as
    #: long as something kept refreshing it. This one is measured from `created_at` and is
    #: the age at which the session must be established again rather than extended.
    #: 0 disables it (and the health surface says so rather than implying a ceiling).
    session_absolute_lifetime_s: int = 90 * 24 * 3600  # 90 days
    # Failed-attempt budget per window: throttles credential exchange (429) and
    # bounds how much a scanner can append to session_events.
    identity_auth_max_failures: int = 10
    identity_auth_failure_window_s: float = 60.0

    # M21 Mail & Calendar (docs/M21_MAIL_CALENDAR_SPEC.md §2, ADR-0084). Empty host means
    # "no account configured" - the honest `account_missing` answer is production's own
    # until the owner puts real credentials on the host (owner item); nothing here is a
    # default credential. The two "enabled" flags are host settings the autonomous system
    # NEVER writes (ADR-0084 decision 1) - a real send/write happens only when the owner
    # has explicitly turned the flag on out of band, on top of the per-call read-back gate.
    mail_imap_host: str = ""
    mail_imap_port: int = 993
    mail_imap_user: str = ""
    mail_imap_password: str = ""
    mail_imap_use_ssl: bool = True
    mail_smtp_host: str = ""
    mail_smtp_port: int = 587
    mail_smtp_user: str = ""
    mail_smtp_password: str = ""
    mail_smtp_use_tls: bool = True
    mail_from: str = ""
    mail_send_enabled: bool = False
    caldav_url: str = ""
    caldav_user: str = ""
    caldav_password: str = ""
    calendar_ics_url: str = ""
    calendar_write_enabled: bool = False
    # B46 (req 354): 'refuse' (default) or 'confirm' - the owner's decision, never inferred.
    calendar_cancel_policy: str = "refuse"
    # B46 (req 358, 361): the calendar mirror + reminders on the routine clock.
    calendar_sync_enabled: bool = True
    calendar_sync_interval_s: float = 120.0

    # B34 req 674: the managed file mutation surface (write/append/edit/rename/move/copy/
    # delete-to-Recycle-Bin, each journaled and undoable). ONE host flag closes the whole
    # surface (the roadmap's rollback plan); the device's authorised roots and the
    # per-mutation proposal/confirmation gate stand on top of it. Permanent deletion is
    # not offered by any setting - the shape of that policy is the owner's checkpoint.
    documents_mutation_enabled: bool = True

    # B35 (req 581-623, 680): the self-development queue and its bounds. ONE host flag
    # closes the whole surface (the roadmap's rollback plan: the runner stops, the
    # worktrees stay). The bounds are the owner's numbers, read beside what is spent.
    selfdev_enabled: bool = True
    #: Req 620: candidates in flight at once. One, because two candidates on one
    #: machine race for the same worktree slots and the same model budget.
    selfdev_max_parallel: int = 1
    #: Req 618: tokens the queue may spend per calendar day (UTC), all runs together.
    selfdev_daily_token_budget: int = 1_000_000
    #: Req 619: the free-space floor under the worktrees root; below it nothing starts.
    selfdev_min_free_bytes: int = 5 * 1024**3
    #: Req 603: how many CI-red -> fix follow-ups one candidate may spawn.
    selfdev_max_ci_fix_rounds: int = 2
    #: Req 601: push selfdev/* branches so CI runs on them. Off: the owner pushes.
    selfdev_ci_push: bool = False
    #: A claim older than this with no result is queued again (a worker died).
    selfdev_claim_ttl_s: float = 7200.0
    #: Where the worker keeps worktrees; None = beside the repository (the CLI default).
    selfdev_worktrees_root: str | None = None

    # B36 (req 565, 577): Capability Genesis - the front door. Model-backed adapter
    # generation is OFF until the owner turns it on; every model-written adapter still
    # passes the security gate and waits for the owner. A non-loopback interface may be
    # researched only when the owner enrolled its host as an authorized asset.
    genesis_model_generation_enabled: bool = False
    # B38 (req 550/551): model-backed executive planning for a directive the deterministic
    # shapes cannot plan. OFF by default (the owner's model budget); every proposal still
    # passes the same graph validator as the rule-based plans.
    executive_model_planner_enabled: bool = False
    executive_planner_model: str = "claude-sonnet-5"
    # B51 (req 740): a model for what the deterministic router leaves unrouted - OFF by
    # default; never an acting, stop or eye-disable intent (app.voice.intent_router).
    voice_model_router_enabled: bool = False
    # B51 (req 744): speak the clarification question (sideband say) - OFF until a live
    # session shows it does not overlap the model's own answer.
    voice_clarify_aloud_enabled: bool = False
    voice_router_model: str = "claude-sonnet-5"
    # B40 (req 425, 435-437): the App Factory's code model - the composed generator's
    # model slots and the fix loop. Off by default: the budget is the owner's.
    appfactory_model_generation_enabled: bool = False
    appfactory_code_model: str = "claude-sonnet-5"
    appfactory_max_fix_attempts: int = 3
    genesis_authorized_hosts_enabled: bool = True

    # Owner Location Context / Live Weather (docs/DECISIONS.md ADR-0091). Open-Meteo needs
    # no signup and no API key for non-commercial use (verified against the vendor's own
    # docs, 2026-09-08) — unlike a credential, defaulting to it is not "inventing a key"
    # (app.weather.providers module docstring); set PAGENTOS_WEATHER_PROVIDER=none to turn
    # live weather off (the honest dependency_unavailable path) without removing the code.
    weather_provider: str = "open_meteo"
    weather_open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    weather_open_meteo_geocoding_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    weather_request_timeout_s: float = 10.0
    # Coarse IP geolocation (tier 5 of app.location.service.LocationService.resolve).
    # Empty means "not configured" (dependency_unavailable at that tier only — the
    # resolver still falls through to "unresolved" honestly): unlike the weather provider,
    # generic IP-geolocation vendors commonly gate anything beyond trivial use behind
    # registration/ToS, so this stays an explicit owner action rather than a hardcoded
    # default vendor (app.location.providers module docstring).
    location_ip_geo_url: str = ""
    location_ip_geo_timeout_s: float = 5.0

    # B11 req 372: Web Push (app.webpush). The private key is a SECRET — read from the
    # environment like every other provider credential (voice_openai_api_key etc.),
    # never a default here, never logged. Empty means "not configured yet": the push
    # rung is skipped honestly (app.notifications.ladder.PushRung.available) and every
    # other rung is unaffected — the same shape voice_openai_api_key already
    # establishes for an owner-provisioned credential. Generated by
    # scripts/cloud/new-vapid-key.ps1 (a local P-256 keypair; no account, no signup —
    # RFC 8292's whole point) and shipped with scripts/cloud/set-cloud-secret.ps1.
    webpush_vapid_private_key: str = ""
    # RFC 8292 §2: the JWT "sub" claim, a contact URI a push service can reach about
    # abuse — "mailto:" or "https:" only. NOT hardcoded to the owner's own address
    # (CLAUDE.md: never invent/commit personal data); empty means push stays
    # unconfigured even with a key present, the same as a missing key.
    webpush_vapid_subject: str = ""
    webpush_request_timeout_s: float = 10.0

    @field_validator("weather_provider")
    @classmethod
    def _validate_weather_provider(cls, v: str) -> str:
        if v not in _WEATHER_PROVIDERS:
            raise ValueError(
                f"PAGENTOS_WEATHER_PROVIDER must be one of {_WEATHER_PROVIDERS}, got {v!r}"
            )
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
