"""Provider interfaces and capability negotiation (spec §12).

Adapters declare what they can do. Callers check before calling. An unsupported
operation returns an explicit `Unsupported` result rather than raising deep in a
pipeline or — worse — returning empty data that reads as "nothing happened".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from alphagraph.core.addresses import Network
from alphagraph.core.events import AssetRef, CanonicalEvent
from alphagraph.core.outcomes import Outcome


class Capability(StrEnum):
    STREAM_ADDRESSES = "stream_addresses"
    BACKFILL_RANGE = "backfill_range"
    HISTORICAL_ARCHIVE = "historical_archive"
    PERP_POSITIONS = "perp_positions"
    RESTING_ORDERS = "resting_orders"
    HOLDER_SNAPSHOT = "holder_snapshot"
    OHLCV = "ohlcv"
    SOCIAL_STREAM = "social_stream"


@dataclass(frozen=True, slots=True)
class Unsupported:
    """Returned when an operation is outside a provider's declared capabilities."""

    capability: Capability
    provider: str
    detail: str = ""

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    healthy: bool
    checked_at: datetime
    lag_seconds: float | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Candle:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_usd: Decimal


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """A point-in-time market reading with explicit provenance and staleness.

    `observed_at` is separate from `as_of` so a caller can tell a fresh reading
    from a carried-forward one. Spec §5 forbids presenting stale data as current.
    """

    asset: AssetRef
    as_of: datetime
    observed_at: datetime
    price_usd: Decimal | None
    liquidity_usd: Decimal | None
    volume_24h_usd: Decimal | None
    market_cap_usd: Decimal | None
    holder_count: int | None
    provider: str
    is_stale: bool = False

    @property
    def has_price(self) -> bool:
        return self.price_usd is not None


@dataclass(frozen=True, slots=True)
class SocialPost:
    post_id: str
    account_id: str
    account_handle: str
    is_authenticated_official: bool
    published_at: datetime
    first_observed_at: datetime
    text: str
    extracted_addresses: tuple[str, ...] = field(default=())
    deleted: bool = False


class ChainProvider(ABC):
    """Source of canonical on-chain events."""

    name: str = "abstract"

    @abstractmethod
    def network(self) -> Network: ...

    @abstractmethod
    def capabilities(self) -> frozenset[Capability]: ...

    @abstractmethod
    async def backfill(
        self, start: datetime, end: datetime, addresses: Sequence[str] | None = None
    ) -> AsyncIterator[CanonicalEvent]: ...

    @abstractmethod
    async def stream_addresses(self, addresses: Sequence[str]) -> AsyncIterator[CanonicalEvent]: ...

    @abstractmethod
    async def health(self) -> ProviderHealth: ...

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities()

    def require(self, capability: Capability) -> Unsupported | None:
        if self.supports(capability):
            return None
        return Unsupported(capability=capability, provider=self.name)


class MarketDataProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def capabilities(self) -> frozenset[Capability]: ...

    @abstractmethod
    async def snapshot(self, asset: AssetRef, at: datetime) -> MarketSnapshot: ...

    @abstractmethod
    async def candles(
        self, asset: AssetRef, start: datetime, end: datetime, resolution: str = "1h"
    ) -> Sequence[Candle] | Unsupported: ...


class ListingProvider(ABC):
    """CEX listing announcements — the outcome registry's most important input."""

    name: str = "abstract"

    @abstractmethod
    async def listings(self, since: datetime) -> Iterable[Outcome]: ...


class SocialProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def posts(self, since: datetime) -> Iterable[SocialPost]: ...


class NotificationProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def send(self, title: str, body: str, url: str | None = None) -> bool: ...
