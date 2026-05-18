"""Application configuration. All values come from environment variables.

Cloud-agnostic: no AWS or Azure SDK references. Secrets management is the
deployer's responsibility — they inject env vars from whichever secret store.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Server
    env: str = Field(default="dev", description="dev | staging | prod")
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Database
    # Defaults match infra/docker/docker-compose.yml host port mappings.
    # Production overrides via env vars hit cloud-internal addresses on
    # the standard ports (5432/6379).
    database_url: str = "postgresql+asyncpg://poker:poker@localhost:15432/poker"
    redis_url: str = "redis://localhost:16379/0"

    # Auth
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 24 * 3600

    # X OAuth
    x_client_id: str = ""
    x_client_secret: str = ""
    x_redirect_uri: str = "http://localhost:5173/auth/callback"

    # ElevenLabs
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_flash_v2_5"

    # Game economy
    play_chip_grant: int = 10000  # Daily chip grant for active players
    default_min_buyin_bb: int = 50
    default_max_buyin_bb: int = 200
    action_timer_seconds: int = 25
    timebank_seconds: int = 30
    disconnect_grace_seconds: int = 30

    # Limits
    max_tables_per_host: int = 5
    max_concurrent_tables: int = 100

    # Feature flags
    persistence_enabled: bool = False
    # Auth mode: "fake" (uses ?as=<handle> for dev/test), "x_oauth" (production
    # OAuth via X), or "both" (tries JWT cookie first, falls back to ?as).
    # Default "both" — tests keep working, production sets to "x_oauth" to
    # disable the fake path.
    auth_mode: str = "both"

    # Moderation: comma-separated list of banned client IPs and/or CIDR
    # ranges (e.g. "1.2.3.4,5.6.7.0/24,2001:db8::/32"). Matching clients
    # receive 403 on HTTP and 1008 on WebSocket before any route logic
    # runs. IPs are resolved from the connection's `client.host`, which
    # only reflects the real client when uvicorn is launched with
    # `--proxy-headers --forwarded-allow-ips "*"` (set in the Dockerfile).
    # Without those flags the app sees only the ALB's address. To ban at
    # runtime, update the BANNED_IPS env var in the ECS task definition
    # and force a new deployment.
    banned_ips: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
