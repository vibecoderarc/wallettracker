"""Contract tests for the market-data provider.

Runs against recorded response shapes, so it verifies our parsing rather than
that GeckoTerminal still returns this format. The API is keyless, so a live
smoke test is possible later without any credential.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from alphagraph.core.addresses import Network
from alphagraph.core.events import AssetRef
from alphagraph.providers.geckoterminal import GeckoTerminalProvider
from alphagraph.providers.http import HttpClient

MEME = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
POOL = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"


def _pool_payload(symbol: str = "BONK", token: str = MEME) -> dict:
    return {
        "data": [
            {
                "id": f"solana_{POOL}",
                "attributes": {
                    "address": POOL,
                    "name": f"{symbol} / SOL",
                    "reserve_in_usd": "1250000.55",
                    "volume_usd": {"h24": "890000.10"},
                    "pool_created_at": "2026-01-15T08:30:00Z",
                },
                "relationships": {"base_token": {"data": {"id": f"solana_{token}"}}},
            }
        ]
    }


def _ohlcv_payload(rows: list[list]) -> dict:
    return {"data": {"attributes": {"ohlcv_list": rows}}}


class FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(200, json={"data": []})
        return self._responses.pop(0)


def _provider(responses: list[httpx.Response]) -> tuple[GeckoTerminalProvider, FakeTransport]:
    transport = FakeTransport(responses)
    client = HttpClient(
        provider="geckoterminal",
        base_url="https://api.geckoterminal.com/api/v2",
        requests_per_second=0,
        client=httpx.AsyncClient(transport=transport),
    )
    return GeckoTerminalProvider(client=client), transport


class TestUniverse:
    def test_parses_a_pool_into_its_non_quote_token(self):
        provider, _ = _provider([httpx.Response(200, json=_pool_payload())])
        pools = asyncio.run(provider.top_pools(pages=1))
        assert len(pools) == 1
        pool = pools[0]
        assert pool.token_address == MEME
        assert pool.pool_address == POOL
        assert pool.symbol == "BONK"
        assert pool.reserve_usd == __import__("decimal").Decimal("1250000.55")

    def test_quote_only_pools_are_excluded(self):
        """A stablecoin's price history says nothing about a memecoin's fate."""
        provider, _ = _provider([httpx.Response(200, json=_pool_payload(symbol="USDC"))])
        assert asyncio.run(provider.top_pools(pages=1)) == []

    def test_malformed_entries_are_skipped_not_fatal(self):
        payload = {"data": [{"id": "x"}, {"attributes": {}}, "junk", None]}
        provider, _ = _provider([httpx.Response(200, json=payload)])
        assert asyncio.run(provider.top_pools(pages=1)) == []

    def test_pagination_requests_each_page(self):
        provider, transport = _provider(
            [httpx.Response(200, json=_pool_payload()) for _ in range(3)]
        )
        asyncio.run(provider.top_pools(pages=3))
        assert len(transport.requests) == 3
        assert "page=3" in str(transport.requests[-1].url)


class TestCandles:
    def _rows(self) -> list[list]:
        base = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp())
        return [[base + i * 3600, 1.0, 1.1, 0.9, 1.05, 5000.0] for i in range(5)]

    def test_ohlcv_is_parsed_and_sorted(self):
        provider, _ = _provider([httpx.Response(200, json=_ohlcv_payload(self._rows()))])
        candles = asyncio.run(provider.pool_candles(POOL))
        assert len(candles) == 5
        assert candles == sorted(candles, key=lambda c: c.start)
        assert candles[0].start.tzinfo is not None

    def test_malformed_rows_are_dropped(self):
        rows = [*self._rows(), [], [1], ["bad", 1, 2, 3, 4, 5], [123, None, 1, 1, 1, 1]]
        provider, _ = _provider([httpx.Response(200, json=_ohlcv_payload(rows))])
        assert len(asyncio.run(provider.pool_candles(POOL))) == 5

    def test_repeat_reads_are_cached(self):
        """Sweeps re-read the same pools; the rate limit is the scarce resource."""
        provider, transport = _provider([httpx.Response(200, json=_ohlcv_payload(self._rows()))])

        async def run():
            await provider.pool_candles(POOL)
            await provider.pool_candles(POOL)

        asyncio.run(run())
        assert len(transport.requests) == 1


class TestSnapshots:
    def test_missing_data_is_none_not_zero(self):
        """A zero price reads as 'worthless' downstream and fakes a total loss."""
        provider, _ = _provider([])
        asset = AssetRef(network=Network.SOLANA, address=MEME)
        snapshot = asyncio.run(provider.snapshot(asset, datetime(2026, 6, 1, tzinfo=UTC)))
        assert snapshot.price_usd is None
        assert snapshot.has_price is False
        assert snapshot.is_stale is True

    def test_price_before_any_history_is_none(self):
        base = int(datetime(2026, 6, 1, tzinfo=UTC).timestamp())
        rows = [[base + i * 3600, 1.0, 1.1, 0.9, 1.05, 100.0] for i in range(3)]
        provider, _ = _provider([httpx.Response(200, json=_ohlcv_payload(rows))])
        asset = AssetRef(network=Network.SOLANA, address=MEME)
        provider.register_pool(asset, POOL)

        async def run():
            await provider.pool_candles(POOL)
            return await provider.snapshot(asset, datetime(2020, 1, 1, tzinfo=UTC))

        assert asyncio.run(run()).price_usd is None

    def test_price_is_read_from_the_candle_at_or_before_the_time(self):
        base = datetime(2026, 6, 1, tzinfo=UTC)
        rows = [[int(base.timestamp()) + i * 3600, 1.0, 1.1, 0.9, 2.0 + i, 100.0] for i in range(3)]
        provider, _ = _provider([httpx.Response(200, json=_ohlcv_payload(rows))])
        asset = AssetRef(network=Network.SOLANA, address=MEME)
        provider.register_pool(asset, POOL)

        async def run():
            await provider.pool_candles(POOL)
            from datetime import timedelta

            return await provider.snapshot(asset, base + timedelta(hours=1, minutes=30))

        snapshot = asyncio.run(run())
        assert snapshot.price_usd == __import__("decimal").Decimal("3.0")

    def test_unregistered_asset_reports_unsupported_rather_than_empty(self):
        provider, _ = _provider([])
        asset = AssetRef(network=Network.SOLANA, address="unregistered")
        result = asyncio.run(
            provider.candles(
                asset, datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
            )
        )
        assert bool(result) is False
