"""Birdeye: the token universe, queried rather than scraped.

This exists because the universe was the bottleneck. Scraping pool listings and
filtering client-side produced tokenised equities and pools holding fractions of
a cent — not because the filtering was wrong, but because you cannot ask those
endpoints the question that matters. Birdeye can be asked directly: *Solana
tokens that did over $2M of volume*.

It also returns far deeper OHLCV, which decides whether a run from months ago is
visible at all. The previous source returned sixteen hours of history for the
first pool it was asked about.

## Billing is in compute units, not requests

Every call costs a variable number of units against a monthly allowance, so the
usual "requests made" counter understates spend on the expensive endpoints. This
module tracks estimated units separately and exposes them, because running out
mid-sweep is a worse failure than being slow.

The per-endpoint costs below are estimates, not contract. Reconcile them against
the real figures on the Birdeye dashboard after the first sweep, and correct
them here — an estimate that is quietly wrong is how an allowance disappears.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from alphagraph.core.addresses import Network
from alphagraph.core.events import AssetRef
from alphagraph.core.timeutil import utcnow
from alphagraph.providers.base import (
    Candle,
    Capability,
    MarketDataProvider,
    MarketSnapshot,
    Unsupported,
)
from alphagraph.providers.http import HttpClient
from alphagraph.providers.universe import PoolRef

log = logging.getLogger(__name__)

BIRDEYE_BASE = "https://public-api.birdeye.so"

#: Lite tier is 15 req/s. Pace under it — a refusal costs the same as a call.
DEFAULT_RPS = 8.0

#: Rough compute-unit costs, used only to keep a running estimate of spend.
#: Verify against the dashboard and correct; a wrong estimate is worse than none
#: because it invites confidence.
ESTIMATED_CU = {
    "token_list": 30,
    "new_listing": 30,
    "ohlcv": 15,
    "price": 3,
}

#: The traction floor, expressed as the question actually being asked: which
#: launches did enough volume that being early to them could have mattered?
#: A token nobody traded cannot carry a footprint worth finding, and including
#: it only pollutes the base rate.
DEFAULT_MIN_VOLUME_24H_USD = 2_000_000
DEFAULT_MIN_LIQUIDITY_USD = 50_000

#: How much history to pull per token when the sweep asks for a price series.
#: Six months, because that is the window the sweep studies.
DEFAULT_HISTORY_DAYS = 180


def _timestamp(value: Any) -> datetime | None:
    """Creation time, from whichever of several shapes the endpoint used.

    Returns None rather than a guess when the value is unusable. Downstream, a
    missing creation time falls back to the first candle, and — more importantly
    — the bot detector treats it as "unknown" instead of computing an entry
    latency against a fabricated origin.
    """
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    seconds = float(value)
    if seconds > 1e11:  # milliseconds
        seconds /= 1000.0
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class TokenRef:
    """A token that cleared the traction floor."""

    address: str
    symbol: str | None
    name: str | None
    liquidity_usd: Decimal | None
    volume_24h_usd: Decimal | None
    market_cap_usd: Decimal | None
    created_at: datetime | None = None

    @property
    def asset(self) -> AssetRef:
        return AssetRef(network=Network.SOLANA, address=self.address, symbol=self.symbol)


class BirdeyeProvider(MarketDataProvider):
    name = "birdeye"

    def __init__(
        self,
        api_key: str,
        *,
        requests_per_second: float = DEFAULT_RPS,
        client: HttpClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("BirdeyeProvider requires an API key")
        self._api_key = api_key
        self._http = client or HttpClient(
            provider=self.name,
            base_url=BIRDEYE_BASE,
            requests_per_second=requests_per_second,
        )
        self._candles: dict[str, list[Candle]] = {}
        self._estimated_cu = 0

    @property
    def usage(self) -> dict[str, int | str]:
        return {**self._http.meter.as_dict(), "estimated_compute_units": self._estimated_cu}

    def capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.OHLCV, Capability.HISTORICAL_ARCHIVE})

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self._api_key, "x-chain": "solana"}

    async def _get(self, path: str, params: dict[str, Any], cost_key: str) -> Any:
        self._estimated_cu += ESTIMATED_CU.get(cost_key, 1)
        return await self._http.get_json(path, params, headers=self._headers())

    # -------------------------------------------------------------- universe

    async def token_universe(
        self,
        *,
        min_volume_24h_usd: float = DEFAULT_MIN_VOLUME_24H_USD,
        min_liquidity_usd: float = DEFAULT_MIN_LIQUIDITY_USD,
        max_tokens: int = 300,
    ) -> list[TokenRef]:
        """Tokens that cleared the traction floor, sorted by 24h volume.

        The floor is applied server-side where the API supports it and rechecked
        here regardless. Trusting a filter parameter that silently does nothing
        is how the universe filled with dust last time.
        """
        tokens: list[TokenRef] = []
        seen: set[str] = set()
        offset = 0
        page_size = 50

        while len(tokens) < max_tokens:
            payload = await self._get(
                "/defi/tokenlist",
                {
                    "sort_by": "v24hUSD",
                    "sort_type": "desc",
                    "offset": offset,
                    "limit": page_size,
                    "min_liquidity": min_liquidity_usd,
                },
                "token_list",
            )
            items = self._items(payload)
            if not items:
                break

            below_floor = 0
            for item in items:
                token = self._parse_token(item)
                if token is None or token.address in seen:
                    continue
                volume = float(token.volume_24h_usd or 0)
                if volume < min_volume_24h_usd:
                    below_floor += 1
                    continue
                seen.add(token.address)
                tokens.append(token)

            # Results are volume-descending, so once a whole page falls below the
            # floor everything after it does too. Paging on would spend units to
            # fetch rows already known to be excluded.
            if below_floor == len(items):
                break
            offset += page_size

        return tokens[:max_tokens]

    async def new_listings(self, *, max_tokens: int = 200) -> list[TokenRef]:
        """Recently listed tokens, deliberately exempt from the volume floor.

        `token_universe` sorts by *current* 24h volume, which means it can only
        ever return tokens that are alive right now. A token that ran in March
        and is a corpse today does nothing today, so it is invisible to that
        listing — and if the universe were built from it alone, every
        `rug_or_collapse` outcome would vanish along with the wallets that were
        early to one. That is the same survivorship trap the pool-scraping
        universe fell into, and a better provider does not fix it by itself.

        So this listing is fetched without the traction floor on purpose. The
        floor still gets applied, but downstream and on the right quantity: the
        sweep judges each token on its *peak* historical daily volume, which
        admits the ones that mattered and were later abandoned while still
        rejecting the ones nobody could ever have traded.
        """
        tokens: list[TokenRef] = []
        seen: set[str] = set()
        offset = 0
        page_size = 50

        while len(tokens) < max_tokens:
            payload = await self._get(
                "/defi/v2/tokens/new_listing",
                {"offset": offset, "limit": page_size},
                "new_listing",
            )
            items = self._items(payload)
            if not items:
                break
            added = 0
            for item in items:
                token = self._parse_token(item)
                if token is None or token.address in seen:
                    continue
                seen.add(token.address)
                tokens.append(token)
                added += 1
            if added == 0:
                break
            offset += page_size

        return tokens[:max_tokens]

    async def universe(self, pages: int = 3) -> list[PoolRef]:
        """The sweep's universe: what is trading now, plus what launched recently.

        Two listings for two reasons. Volume-sorted gives established tokens
        with real traction — the user's "multi-million dollar" floor, asked as a
        query rather than filtered out of a scrape. New listings reach the ones
        that have since died, which the first listing structurally cannot see.

        Neither reaches a token that ran and died *before* the new-listing feed's
        horizon. No provider sells that history cheaply, and pretending
        otherwise would put a survivorship bias into the base rate while looking
        like coverage. The honest fix is the daily collector, which accumulates
        launches from today forward and does not depend on anyone's archive.
        """
        per_listing = max(1, pages) * 50
        ranked = await self.token_universe(max_tokens=per_listing)
        fresh = await self.new_listings(max_tokens=per_listing)

        merged: list[PoolRef] = []
        seen: set[str] = set()
        for token in [*ranked, *fresh]:
            if token.address in seen:
                continue
            seen.add(token.address)
            merged.append(
                PoolRef(
                    # Birdeye prices tokens, not pools, so the series key is the
                    # mint. One series per token, no mapping to maintain.
                    pool_address=token.address,
                    token_address=token.address,
                    symbol=token.symbol,
                    reserve_usd=token.liquidity_usd,
                    volume_24h_usd=token.volume_24h_usd,
                    created_at=token.created_at,
                )
            )
        return merged

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("tokens", "items", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    return [i for i in value if isinstance(i, dict)]
            return []
        if isinstance(data, list):
            return [i for i in data if isinstance(i, dict)]
        return []

    def _parse_token(self, item: dict[str, Any]) -> TokenRef | None:
        address = item.get("address") or item.get("mint")
        if not address:
            return None
        return TokenRef(
            address=str(address),
            symbol=item.get("symbol"),
            name=item.get("name"),
            liquidity_usd=_decimal(item.get("liquidity")),
            volume_24h_usd=_decimal(item.get("v24hUSD") or item.get("volume24hUSD")),
            market_cap_usd=_decimal(item.get("mc") or item.get("marketCap")),
            created_at=_timestamp(
                item.get("liquidityAddedAt") or item.get("blockUnixTime") or item.get("createdAt")
            ),
        )

    # ---------------------------------------------------------------- prices

    async def token_candles(
        self,
        address: str,
        *,
        start: datetime,
        end: datetime,
        interval: str = "1D",
    ) -> list[Candle]:
        """OHLCV for a token, cached because a sweep re-reads the same tokens."""
        if address in self._candles:
            return self._candles[address]

        payload = await self._get(
            "/defi/ohlcv",
            {
                "address": address,
                "type": interval,
                "time_from": int(start.timestamp()),
                "time_to": int(end.timestamp()),
            },
            "ohlcv",
        )

        rows = self._items(payload)
        candles: list[Candle] = []
        for row in rows:
            timestamp = row.get("unixTime") or row.get("time")
            if not isinstance(timestamp, int | float):
                continue
            values = [_decimal(row.get(k)) for k in ("o", "h", "l", "c")]
            if any(v is None for v in values):
                continue
            open_, high, low, close = values  # type: ignore[misc]
            candles.append(
                Candle(
                    start=datetime.fromtimestamp(timestamp, tz=UTC),
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume_usd=_decimal(row.get("v") or row.get("vUsd")) or Decimal(0),
                )
            )

        candles.sort(key=lambda c: c.start)
        self._candles[address] = candles
        return candles

    async def pool_candles(
        self, pool_address: str, history_days: int = DEFAULT_HISTORY_DAYS
    ) -> list[Candle]:
        """Daily OHLCV for one universe entry, in the shape the sweep expects.

        The key is a token mint here, not a pool. Named for the contract in
        `providers.universe` so the sweep does not need to know which it has.
        """
        end = utcnow()
        return await self.token_candles(
            pool_address, start=end - timedelta(days=history_days), end=end
        )

    def register_pool(self, asset: AssetRef, pool_address: str) -> None:
        """No-op: prices are addressed by mint, so there is no mapping to keep.

        Present because the sweep calls it. Doing nothing is correct here, and
        is why `snapshot` can look up by `asset.address` directly.
        """
        return None

    def series_key_for(self, asset: AssetRef) -> str:
        """The mint itself — Birdeye needs no registration step."""
        return asset.address

    # ------------------------------------------------------------- interface

    async def snapshot(self, asset: AssetRef, at: datetime) -> MarketSnapshot:
        candles = self._candles.get(asset.address)
        prior = [c for c in candles or [] if c.start <= at]
        if not prior:
            # Explicitly absent, never zero. A zero price reads as "worthless"
            # downstream and would fabricate a total loss.
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
        window = prior[-7:]
        age_days = (at - candle.start).total_seconds() / 86400
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
            is_stale=age_days > 2,
        )

    async def candles(
        self, asset: AssetRef, start: datetime, end: datetime, resolution: str = "1D"
    ) -> Sequence[Candle] | Unsupported:
        series = await self.token_candles(asset.address, start=start, end=end, interval=resolution)
        return [c for c in series if start <= c.start <= end]
