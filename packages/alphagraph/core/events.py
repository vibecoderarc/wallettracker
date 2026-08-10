"""Canonical event schemas (spec §4).

Every provider adapter maps into these types. No business logic anywhere in the
system may read a provider-shaped payload directly.

Version this module's SCHEMA_VERSION on any breaking field change; stored events
retain the version they were written with.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from alphagraph.core.addresses import Network
from alphagraph.core.timeutil import ensure_utc

SCHEMA_VERSION = 1


class EventType(StrEnum):
    # Spot
    TRANSFER = "transfer"
    SWAP = "swap"
    TOKEN_CREATED = "token_created"
    POOL_CREATED = "pool_created"
    LIQUIDITY_ADDED = "liquidity_added"
    LIQUIDITY_REMOVED = "liquidity_removed"
    AUTHORITY_CHANGED = "authority_changed"

    # Perpetuals (spec §4.3) — the signal types v1 had no concept of.
    POSITION_OPENED = "position_opened"
    POSITION_INCREASED = "position_increased"
    POSITION_REDUCED = "position_reduced"
    POSITION_CLOSED = "position_closed"
    ORDER_PLACED = "order_placed"
    ORDER_CANCELLED = "order_cancelled"
    LIQUIDATION = "liquidation"

    # Meta
    UNKNOWN_INTERACTION = "unknown_interaction"


class Finality(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FINALIZED = "finalized"
    REVERTED = "reverted"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"
    LONG = "long"
    SHORT = "short"


class QualityFlag(StrEnum):
    UNPARSED_INSTRUCTION = "unparsed_instruction"
    MISSING_PRICE = "missing_price"
    STALE_PRICE = "stale_price"
    PROVIDER_CONFLICT = "provider_conflict"
    DECIMALS_ASSUMED = "decimals_assumed"
    PARTIAL_PARSE = "partial_parse"


class AssetRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    network: Network
    address: str
    symbol: str | None = None
    decimals: int = 9


class CanonicalEvent(BaseModel):
    """One normalized observation. Immutable once written.

    Carries both `chain_time` (when it happened) and `observed_at` (when this
    system first saw it). Backtests may only ever read `observed_at` — see
    `alphagraph.backtest.clock`.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    schema_version: int = SCHEMA_VERSION
    event_type: EventType

    network: Network
    block_height: int
    transaction_id: str
    event_index: int = 0

    chain_time: datetime
    observed_at: datetime
    finality: Finality = Finality.CONFIRMED

    # Who acted, and on what.
    actor: str
    asset: AssetRef | None = None
    counterparty: str | None = None

    side: Side | None = None
    amount_raw: int | None = None
    amount_decimals: int | None = None
    usd_value: Decimal | None = None
    price_usd: Decimal | None = None

    # Perp-specific. `filled` is False for a resting order that never executed —
    # spec §4.3 treats those as first-class signals.
    leverage: Decimal | None = None
    limit_price_usd: Decimal | None = None
    filled: bool = True

    provider: str = "unknown"
    parser_version: str = "v1"
    raw_hash: str | None = None
    quality_flags: list[QualityFlag] = Field(default_factory=list)
    correlation_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("chain_time", "observed_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @property
    def is_acquisition(self) -> bool:
        """Did the actor take on exposure?

        Deliberately excludes plain transfers: a token arriving in a wallet is
        not a purchase, and counting it as one would let anyone manufacture a
        fake track record by airdropping tokens to a wallet (spec v1 §6
        acceptance criteria).
        """
        if self.event_type is EventType.SWAP:
            return self.side is Side.BUY
        if self.event_type in {EventType.POSITION_OPENED, EventType.POSITION_INCREASED}:
            return self.side is Side.LONG
        return False

    @property
    def is_intent_only(self) -> bool:
        """A statement of intent that moved no funds (resting/cancelled order)."""
        return self.event_type in {EventType.ORDER_PLACED, EventType.ORDER_CANCELLED} or (
            not self.filled and self.event_type is EventType.ORDER_PLACED
        )
