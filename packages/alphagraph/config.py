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

    @field_validator("fixture_dir")
    @classmethod
    def _fixture_dir_exists(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"fixture_dir does not exist: {v}")
        return v

    @model_validator(mode="after")
    def _live_mode_requires_credentials(self) -> Settings:
        if self.provider_mode is ProviderMode.LIVE and not self.solana_rpc_url:
            raise ValueError(
                "provider_mode=live requires ALPHAGRAPH_SOLANA_RPC_URL. "
                "Refusing to start in live mode without a configured chain provider."
            )
        return self

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
