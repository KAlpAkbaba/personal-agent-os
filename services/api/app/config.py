"""Application settings.

All values have dev-only defaults matching infra/docker/docker-compose.dev.yml
(loopback-bound local containers). Real deployments override via environment
variables or a .env file. No secrets live in this file.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Device broker (M1). Heartbeat interval is sent to agents in the welcome
    # frame; liveness timeout is heartbeat_interval * liveness_factor.
    broker_heartbeat_interval_s: float = 10.0
    broker_liveness_factor: float = 2.5
    broker_enrollment_token_ttl_s: int = 900
    broker_sweep_interval_s: float = 5.0
    broker_default_command_timeout_s: float = 300.0
    broker_handshake_timeout_s: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
