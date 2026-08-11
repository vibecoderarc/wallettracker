"""Contract tests for the Birdeye provider.

Recorded response shapes again — no key here, and the sandbox blocks egress
regardless. These verify the traction floor, the paging stop, the compute-unit
accounting, and that absent prices stay absent.

The floor is the point of this provider. Everything that went wrong with the
previous universe — tokenised equities, pools holding fractions of a cent, one
day of history — came from being unable to ask "which launches did real
volume?" So that question is what these tests are about.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from alphagraph.core.addresses import Network
from alphagraph.core.events import AssetRef
from alphagraph.providers.birdeye import BirdeyeProvider
from alphagraph.providers.http import HttpClient

MEME = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


def _token(symbol: str, volume: float, address: str | None = None, liquidity: float = 200_000):
    return {
        "address": address or f"{symbol}1111111111111111111111111111111111",
        "symbol": symbol,
        "name": f"{symbol} Token",
        "liquidity": liquidity,
        "v24hUSD": volume,
        "mc": volume * 3,
    }


def _list_payload(tokens: list[dict]) -> dict:
    return {"success": True, "data": {"tokens": tokens}}


def _ohlcv_payload(rows: list[dict]) -> dict:
    return {"success": True, "data": {"items": rows}}


class FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(200, json=_list_payload([]))
        return self._responses.pop(0)


def _provider(responses: list[httpx.Response]) -> tuple[BirdeyeProvider, FakeTransport]:
    transport = FakeTransport(responses)
    client = HttpClient(
        provider="birdeye",
        base_url="https://public-api.birdeye.so",
        requests_per_second=0,
        client=httpx.AsyncClient(transport=transport),
    )
    return BirdeyeProvider("test-key", client=client), transport


class TestTractionFloor:
    def test_tokens_below_the_volume_floor_are_excluded(self):
        """A token nobody traded cannot carry a footprint worth finding."""
        payload = _list_payload(
            [
                _token("BIG", 5_000_000),
                _token("SMALL", 12_000),
                _token("DUST", 3.5),
            ]
        )
        provider, _ = _provider([httpx.Response(200, json=payload)])
        tokens = asyncio.run(provider.token_universe(min_volume_24h_usd=2_000_000))
        assert [t.symbol for t in tokens] == ["BIG"]

    def test_the_floor_is_configurable_not_hardcoded(self):
        payload = _list_payload([_token("MID", 500_000)])
        provider, _ = _provider([httpx.Response(200, json=payload)])
        tokens = asyncio.run(provider.token_universe(min_volume_24h_usd=100_000))
        assert [t.symbol for t in tokens] == ["MID"]

    def test_floor_is_rechecked_client_side(self):
        """Trusting a server-side filter that silently does nothing is how the
        universe filled with dust last time."""
        payload = _list_payload([_token("IGNORED_FILTER", 1.0)])
        provider, _ = _provider([httpx.Response(200, json=payload)])
        assert asyncio.run(provider.token_universe(min_volume_24h_usd=2_000_000)) == []

    def test_paging_stops_once_a_page_is_entirely_below_the_floor(self):
        """Results are volume-descending, so paging on only spends units."""
        good = _list_payload([_token("BIG", 5_000_000)])
        below = _list_payload([_token("TINY", 10)])
        provider, transport = _provider(
            [
                httpx.Response(200, json=good),
                httpx.Response(200, json=below),
                httpx.Response(200, json=good),
            ]
        )
        asyncio.run(provider.token_universe(min_volume_24h_usd=1_000_000))
        assert len(transport.requests) == 2


class TestComputeUnits:
    def test_spend_is_estimated_not_just_request_counted(self):
        """Billing is per compute unit, so a request count understates spend."""
        provider, _ = _provider([httpx.Response(200, json=_list_payload([_token("A", 5_000_000)]))])
        asyncio.run(provider.token_universe())
        usage = provider.usage
        assert usage["requests"] >= 1
        assert usage["estimated_compute_units"] > 0

    def test_ohlcv_and_list_have_different_costs(self):
        from alphagraph.providers.birdeye import ESTIMATED_CU

        assert ESTIMATED_CU["token_list"] != ESTIMATED_CU["price"]


class TestAuthAndShape:
    def test_api_key_is_sent_as_a_header(self):
        provider, transport = _provider([httpx.Response(200, json=_list_payload([]))])
        asyncio.run(provider.token_universe())
        assert transport.requests[0].headers["X-API-KEY"] == "test-key"
        assert transport.requests[0].headers["x-chain"] == "solana"

    def test_missing_key_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="API key"):
            BirdeyeProvider("")

    @pytest.mark.parametrize(
        "payload",
        [{}, {"data": None}, {"data": {}}, {"data": "nope"}, {"data": {"tokens": "bad"}}],
    )
    def test_malformed_payloads_do_not_raise(self, payload):
        provider, _ = _provider([httpx.Response(200, json=payload)])
        assert asyncio.run(provider.token_universe()) == []


class TestCandles:
    def _rows(self) -> list[dict]:
        base = int(datetime(2026, 3, 1, tzinfo=UTC).timestamp())
        return [
            {"unixTime": base + i * 86400, "o": 1.0, "h": 1.2, "l": 0.9, "c": 1.0 + i, "v": 900_000}
            for i in range(5)
        ]

    def test_candles_are_parsed_and_sorted(self):
        provider, _ = _provider([httpx.Response(200, json=_ohlcv_payload(self._rows()))])
        candles = asyncio.run(
            provider.token_candles(
                MEME, start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC)
            )
        )
        assert len(candles) == 5
        assert candles == sorted(candles, key=lambda c: c.start)

    def test_incomplete_rows_are_dropped(self):
        rows = [*self._rows(), {"unixTime": 1, "o": None, "h": 1, "l": 1, "c": 1}, {}]
        provider, _ = _provider([httpx.Response(200, json=_ohlcv_payload(rows))])
        candles = asyncio.run(
            provider.token_candles(
                MEME, start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC)
            )
        )
        assert len(candles) == 5

    def test_repeat_reads_are_cached(self):
        provider, transport = _provider([httpx.Response(200, json=_ohlcv_payload(self._rows()))])

        async def run():
            for _ in range(3):
                await provider.token_candles(
                    MEME,
                    start=datetime(2026, 1, 1, tzinfo=UTC),
                    end=datetime(2026, 6, 1, tzinfo=UTC),
                )

        asyncio.run(run())
        assert len(transport.requests) == 1

    def test_missing_price_is_none_not_zero(self):
        provider, _ = _provider([])
        asset = AssetRef(network=Network.SOLANA, address=MEME)
        snapshot = asyncio.run(provider.snapshot(asset, datetime(2026, 6, 1, tzinfo=UTC)))
        assert snapshot.price_usd is None
        assert snapshot.has_price is False

    def test_price_reads_the_candle_at_or_before_the_time(self):
        provider, _ = _provider([httpx.Response(200, json=_ohlcv_payload(self._rows()))])
        asset = AssetRef(network=Network.SOLANA, address=MEME)

        async def run():
            await provider.token_candles(
                MEME, start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC)
            )
            return await provider.snapshot(asset, datetime(2026, 3, 3, 12, tzinfo=UTC))

        assert asyncio.run(run()).price_usd == Decimal("3.0")


class TestUniverseIsNotOnlySurvivors:
    """The volume-sorted listing can only ever show what is alive today.

    This is the same trap the pool-scraping universe fell into, and switching
    provider does not fix it: a token that ran in March and is a corpse now does
    no volume, so it cannot appear in a list sorted by current volume. Building
    the universe from that listing alone would delete every collapse — and the
    wallets that were early to one — before discovery ever ran.
    """

    def test_new_listings_are_not_subject_to_the_volume_floor(self):
        dead = _token("RUGGED", 0.0, liquidity=0.0)
        provider, _ = _provider([httpx.Response(200, json=_list_payload([dead]))])
        tokens = asyncio.run(provider.new_listings(max_tokens=10))
        assert [t.symbol for t in tokens] == ["RUGGED"], (
            "a launch that already died must still be reachable, or every "
            "rug_or_collapse outcome disappears from the record"
        )

    def test_universe_merges_both_listings_without_duplicates(self):
        alive = _token("ALIVE", 9_000_000)
        overlap = _token("ALIVE", 9_000_000)
        dead = _token("DEAD", 0.0, liquidity=0.0)
        provider, _ = _provider(
            [
                httpx.Response(200, json=_list_payload([alive])),
                httpx.Response(200, json=_list_payload([])),
                httpx.Response(200, json=_list_payload([overlap, dead])),
                httpx.Response(200, json=_list_payload([])),
            ]
        )
        pools = asyncio.run(provider.universe(pages=1))
        symbols = [p.symbol for p in pools]
        assert symbols == ["ALIVE", "DEAD"]
        assert len({p.token_address for p in pools}) == 2

    def test_universe_hits_both_endpoints(self):
        provider, transport = _provider([])
        asyncio.run(provider.universe(pages=1))
        paths = {r.url.path for r in transport.requests}
        assert "/defi/tokenlist" in paths
        assert "/defi/v2/tokens/new_listing" in paths


class TestSweepContract:
    """Birdeye has to be substitutable for GeckoTerminal in the sweep."""

    def test_satisfies_the_universe_source_protocol(self):
        from alphagraph.providers.universe import UniverseSource

        provider, _ = _provider([])
        assert isinstance(provider, UniverseSource)

    def test_series_key_is_the_mint_so_no_registration_is_needed(self):
        provider, _ = _provider([])
        asset = AssetRef(network=Network.SOLANA, address=MEME, symbol="BONK")
        assert provider.series_key_for(asset) == MEME

    def test_pool_candles_requests_a_six_month_window(self):
        provider, transport = _provider([httpx.Response(200, json=_ohlcv_payload([]))])
        asyncio.run(provider.pool_candles(MEME))
        params = transport.requests[0].url.params
        span_days = (int(params["time_to"]) - int(params["time_from"])) / 86400
        assert 179 <= span_days <= 181, (
            "the sweep looks back six months; a shorter window makes older "
            "outcomes invisible without failing"
        )


class TestCreationTime:
    def test_seconds_and_milliseconds_both_parse(self):
        from alphagraph.providers.birdeye import _timestamp

        expected = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        seconds = int(expected.timestamp())
        assert _timestamp(seconds) == expected
        assert _timestamp(seconds * 1000) == expected

    def test_unusable_values_are_absent_rather_than_guessed(self):
        from alphagraph.providers.birdeye import _timestamp

        # A fabricated origin would make the bot detector compute an entry
        # latency against a time that never happened.
        for bad in (None, 0, -1, "", "not-a-date", True, {}):
            assert _timestamp(bad) is None

    def test_creation_time_is_carried_onto_the_universe_entry(self):
        listed = _token("NEW", 4_000_000)
        listed["liquidityAddedAt"] = int(datetime(2026, 3, 1, tzinfo=UTC).timestamp())
        provider, _ = _provider([httpx.Response(200, json=_list_payload([listed]))])
        tokens = asyncio.run(provider.token_universe())
        assert tokens[0].created_at == datetime(2026, 3, 1, tzinfo=UTC)
