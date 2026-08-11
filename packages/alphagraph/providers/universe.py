"""What the discovery sweep needs from a market-data source, and nothing else.

The sweep does three things with market data: ask for a universe of assets, ask
for each one's price history, and remember which key that history is addressed
by. That is the whole contract, and stating it explicitly is what lets the
universe source be swapped without touching the sweep.

The key matters because providers disagree about what a price series belongs to.
GeckoTerminal addresses OHLCV by *pool*, so a token with three pools has three
series and the mapping has to be kept. Birdeye addresses it by *token*, so the
key is the mint itself. `PoolRef.pool_address` is that key in both cases — for
Birdeye it equals `token_address`, which is not a lie so much as a degenerate
case: there is exactly one series per token.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from alphagraph.core.addresses import Network
from alphagraph.core.events import AssetRef
from alphagraph.providers.base import Candle


@dataclass(frozen=True, slots=True)
class PoolRef:
    """An asset in the universe, and the key its price series is addressed by."""

    pool_address: str
    token_address: str
    symbol: str | None
    reserve_usd: Decimal | None
    volume_24h_usd: Decimal | None
    created_at: datetime | None

    @property
    def asset(self) -> AssetRef:
        return AssetRef(network=Network.SOLANA, address=self.token_address, symbol=self.symbol)


@runtime_checkable
class UniverseSource(Protocol):
    """A market-data provider the bootstrap sweep can run against."""

    name: str

    @property
    def usage(self) -> dict[str, int | str]:
        """Requests spent so far, reported in the sweep summary."""

    async def universe(self, pages: int = 3) -> list[PoolRef]:
        """The assets to study.

        Must not be selected purely on being alive today. A token that ran and
        then died has no current volume and no current liquidity, so a source
        that only lists what is trading now silently removes every collapse —
        and with it the outcome class that separates a wallet early into things
        that last from one early into things that die.
        """

    async def pool_candles(self, pool_address: str) -> list[Candle]:
        """Daily OHLCV for one series key. Cached; sweeps re-read the same keys."""

    def register_pool(self, asset: AssetRef, pool_address: str) -> None:
        """Record which series key prices this asset, for later snapshots."""

    def series_key_for(self, asset: AssetRef) -> str | None:
        """The key `pool_candles` wants for this asset, or None if unknown.

        None means "this asset is not in the universe", and the sweep skips it
        rather than guessing a key and fetching an empty series.
        """
