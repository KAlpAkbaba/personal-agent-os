"""Application settings.

All values have dev-only defaults matching infra/docker/docker-compose.dev.yml
(loopback-bound local containers). Real deployments override via environment
variables or a .env file. No secrets live in this file.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: services/api — the identity root lives beside the service, outside the repo's
#: tracked tree (see .gitignore), never inside a database.
_API_ROOT = Path(__file__).resolve().parents[1]


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
    voice_realtime_session_ttl_s: int = 3600
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
    broker_handshake_timeout_s: float = 10.0

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
    # Failed-attempt budget per window: throttles credential exchange (429) and
    # bounds how much a scanner can append to session_events.
    identity_auth_max_failures: int = 10
    identity_auth_failure_window_s: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
