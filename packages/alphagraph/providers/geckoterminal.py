"""Market data and the token universe, from GeckoTerminal's public API.

Chosen because it needs no API key and returns per-pool OHLCV, which makes the
first real sweep cost nothing. It supplies two different things:

  * **The universe.** Which tokens exist and which ones actually moved. Discovery
    works backwards from outcomes, so this is what defines the outcome set.
  * **Prices.** OHLCV history for detecting runs and revivals, and spot prices
    for valuing a wallet's entries.

Known limitation, and it bounds the whole first sweep: history goes back about
six months. Discovery therefore sees roughly two quarters of outcomes, not
years. That is enough to surface candidates but not enough to confirm one — a
wallet needs independent events spread over time, and six months caps how many
it can have. Treat early results as a shortlist to shadow-watch, not a verdict.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from alphagraph.core.addresses import Network
from alphagraph.core.events import AssetRef
from alphagraph.providers.base import (
    Candle,
    Capability,
    MarketDataProvider,
    MarketSnapshot,
    Unsupported,
)
from alphagraph.providers.http import HttpClient

log = logging.getLogger(__name__)

GECKOTERMINAL_BASE = "https://api.geckoterminal.com/api/v2"

#: Observed: the public tier refuses persistently at 0.5 req/s, exhausting
#: retries. It behaves like a per-minute window, so pace well under it — being
#: refused costs a request just as a successful call does, and a 429 storm
#: wastes the whole run.
DEFAULT_RPS = 0.25

#: Daily candles, so one request covers months. Hourly at limit=1000 reaches
#: only ~41 days, and in practice returned 16 hours for a young pool — nowhere
#: near enough to detect a run that happened months ago.
DEFAULT_TIMEFRAME = "day"
DEFAULT_CANDLE_LIMIT = 180

#: Pools below this are dust. The default pool listing returned reserves of
#: fractions of a cent, and a "10x" on a pool holding $0.000003 is noise, not an
#: outcome anyone could have traded.
MIN_POOL_LIQUIDITY_USD = Decimal("15000")

#: A quote token's price history says nothing about a memecoin's fate, and pools
#: are named for both sides. These are filtered out of the universe.
QUOTE_SYMBOLS = frozenset({"SOL", "WSOL", "USDC", "USDT", "USDH", "UXD"})


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class PoolRef:
    """A tradable pool and the non-quote token it prices."""

    pool_address: str
    token_address: str
    symbol: str | None
    reserve_usd: Decimal | None
    volume_24h_usd: Decimal | None
    created_at: datetime | None

    @property
    def asset(self) -> AssetRef:
        return AssetRef(network=Network.SOLANA, address=self.token_address, symbol=self.symbol)


class GeckoTerminalProvider(MarketDataProvider):
    name = "geckoterminal"

    def __init__(
        self,
        *,
        requests_per_second: float = DEFAULT_RPS,
        client: HttpClient | None = None,
    ) -> None:
        self._http = client or HttpClient(
            provider=self.name,
            base_url=GECKOTERMINAL_BASE,
            requests_per_second=requests_per_second,
        )
        self._ohlcv_cache: dict[str, list[Candle]] = {}

    @property
    def usage(self) -> dict[str, int | str]:
        return self._http.meter.as_dict()

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.OHLCV})

    # -------------------------------------------------------------- universe

    async def top_pools(
        self,
        pages: int = 5,
        trending: bool = False,
        min_liquidity_usd: Decimal | None = None,
    ) -> list[PoolRef]:
        """Pools worth considering as the discovery universe.

        Sorted by 24h volume rather than taking the endpoint's default order.
        The default returned tokenised equities with reserves of fractions of a
        cent — an unusable universe, both because dust pools cannot produce a
        tradable outcome and because they are not the market being studied.

        Still deliberately includes ordinary pools, not only trending ones. A
        universe built from what is hot today is a survivorship trap: every token
        in it already moved, the base rate approaches 100%, and no wallet can
        show an edge over it.
        """
        path = "/networks/solana/trending_pools" if trending else "/networks/solana/pools"
        floor = MIN_POOL_LIQUIDITY_USD if min_liquidity_usd is None else min_liquidity_usd
        pools: list[PoolRef] = []
        seen: set[str] = set()

        for page in range(1, pages + 1):
            payload = await self._http.get_json(path, {"page": page, "sort": "h24_volume_usd_desc"})
            for item in self._items(payload):
                pool = self._parse_pool(item)
                if pool is None or pool.pool_address in seen:
                    continue
                if floor > 0 and (pool.reserve_usd or Decimal(0)) < floor:
                    continue
                seen.add(pool.pool_address)
                pools.append(pool)
        return pools

    async def universe(self, pages: int = 3) -> list[PoolRef]:
        """Combine listings so the universe is not only what is hot right now.

        Top-by-volume supplies established tokens; trending supplies the ones
        currently moving. Both are needed: only-trending is survivorship, and
        only-established misses the launches this system exists to catch.
        """
        established = await self.top_pools(pages=pages, trending=False)
        hot = await self.top_pools(pages=1, trending=True)
        merged: dict[str, PoolRef] = {p.pool_address: p for p in established}
        for pool in hot:
            merged.setdefault(pool.pool_address, pool)
        return list(merged.values())

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, dict):
            return [data]
        return [item for item in data or [] if isinstance(item, dict)]

    def _parse_pool(self, item: dict[str, Any]) -> PoolRef | None:
        attributes = item.get("attributes")
        if not isinstance(attributes, dict):
            return None
        pool_address = attributes.get("address") or item.get("id")
        if not pool_address:
            return None

        relationships = item.get("relationships") or {}
        base = (relationships.get("base_token") or {}).get("data") or {}
        # Ids arrive namespaced as "solana_<mint>"; the mint is what we index by.
        raw_id = str(base.get("id") or "")
        token_address = raw_id.split("_", 1)[1] if "_" in raw_id else raw_id
        if not token_address:
            return None

        name = str(attributes.get("name") or "")
        symbol = name.split("/")[0].strip() or None
        if symbol and symbol.upper() in QUOTE_SYMBOLS:
            return None

        return PoolRef(
            pool_address=str(pool_address).split("_", 1)[-1],
            token_address=token_address,
            symbol=symbol,
            reserve_usd=_decimal(attributes.get("reserve_in_usd")),
            volume_24h_usd=_decimal((attributes.get("volume_usd") or {}).get("h24")),
            created_at=self._parse_time(attributes.get("pool_created_at")),
        )

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            text = str(value).replace("Z", "+00:00")
            return datetime.fromisoformat(text).astimezone(UTC)
        except ValueError:
            return None

    # ---------------------------------------------------------------- prices

    async def pool_candles(
        self,
        pool_address: str,
        timeframe: str = DEFAULT_TIMEFRAME,
        limit: int = DEFAULT_CANDLE_LIMIT,
    ) -> list[Candle]:
        """Hourly OHLCV for a pool, cached because sweeps re-read the same pools.

        Keyed by pool address alone. An earlier version keyed on
        pool+timeframe+limit, which meant `snapshot()` — which looks up by pool —
        never found anything and silently reported every price as missing.
        """
        if pool_address in self._ohlcv_cache:
            return self._ohlcv_cache[pool_address]

        payload = await self._http.get_json(
            f"/networks/solana/pools/{pool_address}/ohlcv/{timeframe}",
            {"aggregate": 1, "limit": limit},
        )
        rows = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []

        candles: list[Candle] = []
        for row in rows:
            # [timestamp, open, high, low, close, volume]
            if not isinstance(row, list | tuple) or len(row) < 6:
                continue
            timestamp = row[0]
            if not isinstance(timestamp, int | float):
                continue
            values = [_decimal(v) for v in row[1:6]]
            if any(v is None for v in values):
                continue
            open_, high, low, close, volume = values  # type: ignore[misc]
            candles.append(
                Candle(
                    start=datetime.fromtimestamp(timestamp, tz=UTC),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume_usd=volume,
                )
            )

        candles.sort(key=lambda c: c.start)
        self._ohlcv_cache[pool_address] = candles
        return candles

    # ------------------------------------------------------------- interface

    async def snapshot(self, asset: AssetRef, at: datetime) -> MarketSnapshot:
        """Price at a point in time, from cached OHLCV if we have it.

        Returns an explicitly empty snapshot rather than a zero when there is no
        data. A zero price reads as "worthless" three steps downstream and would
        silently turn an unpriceable asset into a total loss.
        """
        pool = self._pool_for_asset(asset)
        candles = self._ohlcv_cache.get(pool) if pool else None
        if not candles:
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

        prior = [c for c in candles if c.start <= at]
        if not prior:
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

        candle = prior[-1]
        window = prior[-24:]
        age_hours = (at - candle.start).total_seconds() / 3600
        return MarketSnapshot(
            asset=asset,
            as_of=at,
            observed_at=candle.start,
            price_usd=candle.close,
            liquidity_usd=None,
            volume_24h_usd=sum((c.volume_usd for c in window), Decimal(0)),
            market_cap_usd=None,
            holder_count=None,
            provider=self.name,
            # Daily candles: anything older than ~2 days is stale. Judging
            # daily data by an hourly threshold marks everything stale.
            is_stale=age_hours > 48,
        )

    async def candles(
        self, asset: AssetRef, start: datetime, end: datetime, resolution: str = "1h"
    ) -> Sequence[Candle] | Unsupported:
        pool = self._pool_for_asset(asset)
        if pool is None:
            return Unsupported(
                capability=Capability.OHLCV,
                provider=self.name,
                detail="no pool registered for this asset; call register_pool first",
            )
        series = await self.pool_candles(pool)
        return [c for c in series if start <= c.start <= end]

    # OHLCV is addressed by pool, not by token, so the mapping has to be kept.
    _pool_by_asset: dict[str, str]

    def register_pool(self, asset: AssetRef, pool_address: str) -> None:
        if not hasattr(self, "_pool_by_asset"):
            self._pool_by_asset = {}
        self._pool_by_asset[asset.address] = pool_address

    def _pool_for_asset(self, asset: AssetRef) -> str | None:
        return getattr(self, "_pool_by_asset", {}).get(asset.address)
