"""Application configuration with validation.

Fails loudly on misconfiguration rather than silently degrading. No secrets are
stored here; only references to them. See `.env.example`.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class RunMode(StrEnum):
    """How the system is permitted to act on what it finds.

    SHADOW is the default and the safe state: everything computes, alerts are
    persisted, but nothing leaves the machine. Spec §15.1 requires shadow review
    before notifications are ever enabled.
    """

    SHADOW = "shadow"
    LIVE_ALERTS = "live_alerts"


class ProviderMode(StrEnum):
    FIXTURE = "fixture"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ALPHAGRAPH_",
        extra="ignore",
        frozen=True,
    )

    environment: str = "local"
    run_mode: RunMode = RunMode.SHADOW
    provider_mode: ProviderMode = ProviderMode.FIXTURE

    database_url: str = "sqlite+pysqlite:///./alphagraph.db"
    fixture_dir: Path = REPO_ROOT / "fixtures"

    # Budget guard rails (spec §12). Zero disables the cap.
    daily_budget_usd: float = Field(default=25.0, ge=0)
    monthly_budget_usd: float = Field(default=600.0, ge=0)

    # Secret *references*, never secrets. Empty means the provider is unavailable
    # and the system must degrade visibly rather than fabricate coverage.
    solana_rpc_url: str = ""
    solana_indexer_key: str = ""
    market_data_key: str = ""
    perps_api_url: str = ""
    bsc_rpc_url: str = ""
    ai_provider_key: str = ""
    notification_webhook: str = ""

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    #: Bearer token required on every endpoint except /v1/health. Empty is only
    #: tolerated when `environment` is local — see the validator below.
    api_token: str = ""
    #: Comma-separated browser origins permitted to call the API.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    #: Populate an empty database from the fixture world on first boot, so a
    #: fresh deploy shows a working dashboard without anyone running a command
    #: by hand. Only ever acts in fixture mode and only when there are no events.
    auto_seed: bool = True

    @field_validator("fixture_dir")
    @classmethod
    def _fixture_dir_exists(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"fixture_dir does not exist: {v}")
        return v

    @property
    def is_local(self) -> bool:
        return self.environment.lower() in {"local", "test", "dev", "development"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def _live_mode_requires_credentials(self) -> Settings:
        if self.provider_mode is ProviderMode.LIVE and not self.solana_rpc_url:
            raise ValueError(
                "provider_mode=live requires ALPHAGRAPH_SOLANA_RPC_URL. "
                "Refusing to start in live mode without a configured chain provider."
            )
        return self

    @model_validator(mode="after")
    def _deployed_requires_api_token(self) -> Settings:
        """A deployed instance must not be readable by whoever finds the URL.

        Everything this system produces — tracked wallets, dossier notes, the
        hypotheses behind them — is the product. An unauthenticated public
        hostname gives it all away, so refuse to boot rather than start quietly
        wide open.
        """
        if not self.is_local and not self.api_token:
            raise ValueError(
                f"ALPHAGRAPH_API_TOKEN is required when environment={self.environment!r}. "
                "Refusing to start an unauthenticated API outside local development."
            )
        return self

    @property
    def auth_required(self) -> bool:
        return bool(self.api_token)

    @property
    def notifications_enabled(self) -> bool:
        """Notifications require BOTH live mode and a configured destination.

        Spec §15.1: notifications are enabled only by explicit approval after
        shadow review.
        """
        return self.run_mode is RunMode.LIVE_ALERTS and bool(self.notification_webhook)


@lru_cache
def get_settings() -> Settings:
    return Settings()
