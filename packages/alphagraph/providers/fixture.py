"""Fixture-backed provider adapters.

These implement the same interfaces a paid provider will, so swapping in Helius
or a market-data vendor later is a constructor change, not a rewrite. They are
also what makes `pytest` and the local demo run at zero cost.
"""

from __future__ import annotations

import bisect
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import datetime
from decimal import Decimal

from alphagraph.core.addresses import Network
from alphagraph.core.events import AssetRef, CanonicalEvent
from alphagraph.core.outcomes import Outcome, OutcomeClass
from alphagraph.core.timeutil import parse_utc, utcnow
from alphagraph.providers.base import (
    Candle,
    Capability,
    ChainProvider,
    ListingProvider,
    MarketDataProvider,
    MarketSnapshot,
    NotificationProvider,
    ProviderHealth,
    Unsupported,
)
from alphagraph.providers.world import World, build_world

_STALE_AFTER_HOURS = 6


class FixtureChainProvider(ChainProvider):
    name = "fixture-chain"

    def __init__(self, world: World, network: Network = Network.SOLANA) -> None:
        self._world = world
        self._network = network
        self._events = sorted(world.events, key=lambda e: e.observed_at)
        self._observed_keys = [e.observed_at for e in self._events]

    def network(self) -> Network:
        return self._network

    def capabilities(self) -> frozenset[Capability]:
        return frozenset(
            {
                Capability.STREAM_ADDRESSES,
                Capability.BACKFILL_RANGE,
                Capability.HISTORICAL_ARCHIVE,
                Capability.PERP_POSITIONS,
                Capability.RESTING_ORDERS,
            }
        )

    async def backfill(
        self, start: datetime, end: datetime, addresses: Sequence[str] | None = None
    ) -> AsyncIterator[CanonicalEvent]:
        """Yield events by OBSERVED time, not chain time.

        A backfill can only surface what the system could have seen. Slicing on
        chain_time here would hand future knowledge to any caller that replays
        a historical range.
        """
        wanted = set(addresses) if addresses else None
        lo = bisect.bisect_left(self._observed_keys, start)
        hi = bisect.bisect_right(self._observed_keys, end)
        for event in self._events[lo:hi]:
            if wanted is None or event.actor in wanted:
                yield event

    async def stream_addresses(self, addresses: Sequence[str]) -> AsyncIterator[CanonicalEvent]:
        wanted = set(addresses)
        for event in self._events:
            if event.actor in wanted:
                yield event

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name, healthy=True, checked_at=utcnow(), lag_seconds=0.0
        )


class FixtureMarketDataProvider(MarketDataProvider):
    name = "fixture-market"

    def __init__(self, world: World) -> None:
        self._candles: dict[str, list[Candle]] = {}
        for address, rows in world.candles.items():
            parsed = [
                Candle(
                    start=parse_utc(r["start"]),
                    open=Decimal(r["open"]),
                    high=Decimal(r["high"]),
                    low=Decimal(r["low"]),
                    close=Decimal(r["close"]),
                    volume_usd=Decimal(r["volume_usd"]),
                )
                for r in rows
            ]
            parsed.sort(key=lambda c: c.start)
            self._candles[address] = parsed

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.OHLCV})

    def _candles_for(self, asset: AssetRef) -> list[Candle]:
        return self._candles.get(asset.address, [])

    async def snapshot(self, asset: AssetRef, at: datetime) -> MarketSnapshot:
        series = self._candles_for(asset)
        if not series:
            # Explicit "no data" rather than a zero. Spec §5: a missing price is
            # never rendered as 0, because 0 reads as "worthless" downstream.
            return MarketSnapshot(
                asset=asset,
                as_of=at,
                observed_at=at,
                price_usd=None,
                liquidity_usd=None,
                volume_24h_usd=None,
                market_cap_usd=None,
                holder_count=None,
                provider=self.name,
                is_stale=True,
            )
        idx = bisect.bisect_right([c.start for c in series], at) - 1
        if idx < 0:
            return MarketSnapshot(
                asset=asset,
                as_of=at,
                observed_at=at,
                price_usd=None,
                liquidity_usd=None,
                volume_24h_usd=None,
                market_cap_usd=None,
                holder_count=None,
                provider=self.name,
                is_stale=True,
            )
        candle = series[idx]
        age_hours = (at - candle.start).total_seconds() / 3600
        window = series[max(0, idx - 24) : idx + 1]
        volume = sum((c.volume_usd for c in window), Decimal(0))
        return MarketSnapshot(
            asset=asset,
            as_of=at,
            observed_at=candle.start,
            price_usd=candle.close,
            liquidity_usd=volume * Decimal("0.35"),
            volume_24h_usd=volume,
            market_cap_usd=candle.close * Decimal(1_000_000_000),
            holder_count=None,
            provider=self.name,
            is_stale=age_hours > _STALE_AFTER_HOURS,
        )

    async def candles(
        self, asset: AssetRef, start: datetime, end: datetime, resolution: str = "1h"
    ) -> Sequence[Candle] | Unsupported:
        return [c for c in self._candles_for(asset) if start <= c.start <= end]


class FixtureListingProvider(ListingProvider):
    name = "fixture-listings"

    def __init__(self, world: World) -> None:
        self._listings = [o for o in world.outcomes if o.outcome_class is OutcomeClass.CEX_LISTING]

    async def listings(self, since: datetime) -> Iterable[Outcome]:
        return [o for o in self._listings if o.first_observed_at >= since]


class RecordingNotificationProvider(NotificationProvider):
    """Captures notifications instead of sending them.

    This is the default in shadow mode. Nothing reaches an external service
    unless the operator explicitly enables live alerts (spec §15.1).
    """

    name = "recording"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str | None]] = []

    async def send(self, title: str, body: str, url: str | None = None) -> bool:
        self.sent.append((title, body, url))
        return True


def default_world() -> World:
    return build_world()
