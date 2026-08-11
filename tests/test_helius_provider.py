"""Contract tests for the live Solana provider.

No API key here, so these run against recorded response shapes. That verifies
our parsing and our failure handling — not that Helius still returns this shape,
which only a live smoke test can confirm.

The cases that matter are the ugly ones: failed transactions, swaps we cannot
interpret, missing fields, and rate limits. A provider that handles the happy
path and mangles the rest produces a corrupted track record rather than an
obvious error.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from alphagraph.core.events import EventType, QualityFlag, Side
from alphagraph.providers.helius import HeliusChainProvider
from alphagraph.providers.http import HttpClient, ProviderRequestError

WALLET = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
MEME_MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
WSOL = "So11111111111111111111111111111111111111112"


def _swap_tx(*, buy: bool = True, signature: str = "sig-buy-1") -> dict:
    """A swap as the Enhanced Transactions API reports it."""
    meme_leg = {
        "mint": MEME_MINT,
        "tokenAmount": 1250.5,
        "fromUserAccount": "poolAccount" if buy else WALLET,
        "toUserAccount": WALLET if buy else "poolAccount",
    }
    sol_leg = {
        "mint": WSOL,
        "tokenAmount": 3.2,
        "fromUserAccount": WALLET if buy else "poolAccount",
        "toUserAccount": "poolAccount" if buy else WALLET,
    }
    return {
        "signature": signature,
        "timestamp": int(datetime(2026, 6, 1, 12, tzinfo=UTC).timestamp()),
        "slot": 285_000_000,
        "type": "SWAP",
        "source": "RAYDIUM",
        "feePayer": WALLET,
        "description": "swapped",
        "tokenTransfers": [meme_leg, sol_leg],
    }


class FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = responses
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(200, json=[])
        return self._responses.pop(0)


def _provider(responses: list[httpx.Response]) -> tuple[HeliusChainProvider, FakeTransport]:
    transport = FakeTransport(responses)
    client = HttpClient(
        provider="helius",
        base_url="https://api.helius.xyz",
        requests_per_second=0,  # no pacing in tests
        client=httpx.AsyncClient(transport=transport),
    )
    return HeliusChainProvider("test-key", client=client), transport


class TestSwapParsing:
    def test_buy_is_attributed_to_the_non_quote_token(self):
        provider, _ = _provider([])
        events = provider.parse_transaction(_swap_tx(buy=True), subject=WALLET)
        assert len(events) == 1
        event = events[0]
        assert event.event_type is EventType.SWAP
        assert event.side is Side.BUY
        assert event.asset is not None
        # The memecoin, not wrapped SOL — otherwise every buy looks like a SOL buy.
        assert event.asset.address == MEME_MINT
        assert event.actor == WALLET

    def test_sell_direction_is_detected(self):
        provider, _ = _provider([])
        event = provider.parse_transaction(_swap_tx(buy=False), subject=WALLET)[0]
        assert event.side is Side.SELL
        assert event.asset.address == MEME_MINT

    def test_price_is_absent_not_invented(self):
        """A missing USD value must stay missing and be flagged."""
        provider, _ = _provider([])
        event = provider.parse_transaction(_swap_tx(), subject=WALLET)[0]
        assert event.usd_value is None
        assert QualityFlag.MISSING_PRICE in event.quality_flags

    def test_subject_wins_over_fee_payer(self):
        """Aggregator routes make the fee payer a program, not the trader."""
        provider, _ = _provider([])
        raw = _swap_tx()
        raw["feePayer"] = "SomeAggregatorProgram1111111111111111111111"
        event = provider.parse_transaction(raw, subject=WALLET)[0]
        assert event.actor == WALLET


class TestDefensiveParsing:
    def test_failed_transaction_produces_nothing(self):
        """A reverted transaction moved no funds and must not count as a trade."""
        provider, _ = _provider([])
        raw = _swap_tx()
        raw["transactionError"] = {"InstructionError": [3, {"Custom": 6001}]}
        assert provider.parse_transaction(raw, subject=WALLET) == []

    def test_uninterpretable_transaction_is_explicit_not_dropped(self):
        """Coverage gaps must be visible, never silently skipped."""
        provider, _ = _provider([])
        raw = {
            "signature": "sig-weird",
            "timestamp": int(datetime(2026, 6, 1, tzinfo=UTC).timestamp()),
            "slot": 1,
            "type": "COMPRESSED_NFT_MINT",
            "feePayer": WALLET,
        }
        events = provider.parse_transaction(raw, subject=WALLET)
        assert len(events) == 1
        assert events[0].event_type is EventType.UNKNOWN_INTERACTION
        assert QualityFlag.UNPARSED_INSTRUCTION in events[0].quality_flags

    @pytest.mark.parametrize(
        "raw",
        [
            {},
            {"signature": "s"},
            {"timestamp": 123},
            {"signature": "s", "timestamp": "not-a-number"},
        ],
    )
    def test_malformed_payloads_do_not_raise(self, raw):
        """A KeyError mid-backfill aborts a sweep that already spent its budget."""
        provider, _ = _provider([])
        assert provider.parse_transaction(raw, subject=WALLET) == []

    def test_missing_token_amount_is_flagged_not_guessed(self):
        provider, _ = _provider([])
        raw = _swap_tx()
        raw["tokenTransfers"][0]["tokenAmount"] = None
        event = provider.parse_transaction(raw, subject=WALLET)[0]
        assert QualityFlag.PARTIAL_PARSE in event.quality_flags


class TestPaginationAndBudget:
    def test_walks_back_until_the_window_start(self):
        old = _swap_tx(signature="sig-old")
        old["timestamp"] = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())
        provider, transport = _provider(
            [
                httpx.Response(200, json=[_swap_tx(signature="sig-1")]),
                httpx.Response(200, json=[old]),
            ]
        )

        async def run():
            return [
                e
                async for e in provider.fetch_address_history(
                    WALLET,
                    start=datetime(2026, 1, 1, tzinfo=UTC),
                    end=datetime(2026, 12, 1, tzinfo=UTC),
                )
            ]

        events = asyncio.run(run())
        assert [e.transaction_id for e in events] == ["sig-1"]
        # Stopped after seeing something older than the window rather than
        # paging through the wallet's entire history.
        assert len(transport.requests) == 2

    def test_max_pages_caps_spend(self):
        """One very active address must not consume a whole monthly allowance."""
        provider, transport = _provider(
            [httpx.Response(200, json=[_swap_tx(signature=f"s{i}")]) for i in range(20)]
        )

        async def run():
            return [
                e
                async for e in provider.fetch_address_history(
                    WALLET,
                    start=datetime(2026, 1, 1, tzinfo=UTC),
                    end=datetime(2026, 12, 1, tzinfo=UTC),
                    max_pages=3,
                )
            ]

        asyncio.run(run())
        assert len(transport.requests) == 3

    def test_usage_is_metered(self):
        """Free-tier allowance has to be a number, not a surprise."""
        provider, _ = _provider([httpx.Response(200, json=[])])

        async def run():
            return [
                e
                async for e in provider.fetch_address_history(
                    WALLET,
                    start=datetime(2026, 1, 1, tzinfo=UTC),
                    end=datetime(2026, 12, 1, tzinfo=UTC),
                )
            ]

        asyncio.run(run())
        assert provider.usage["requests"] == 1


class TestFailureHandling:
    def test_rate_limit_is_retried_then_succeeds(self):
        provider, transport = _provider(
            [
                httpx.Response(429, headers={"retry-after": "0"}, json={}),
                httpx.Response(200, json=[_swap_tx()]),
            ]
        )

        async def run():
            return [
                e
                async for e in provider.fetch_address_history(
                    WALLET,
                    start=datetime(2026, 1, 1, tzinfo=UTC),
                    end=datetime(2026, 12, 1, tzinfo=UTC),
                )
            ]

        events = asyncio.run(run())
        assert len(events) == 1
        # Refused, retried, got the page, then asked for the next one and stopped
        # on the empty response.
        assert len(transport.requests) == 3
        assert provider.usage["rate_limited"] == 1

    def test_persistent_failure_raises_rather_than_returning_empty(self):
        """Empty and failed must never look the same to the caller."""
        # One more than max_retries, so the retries are genuinely exhausted.
        provider, _ = _provider([httpx.Response(500, json={}) for _ in range(10)])

        async def run():
            return [
                e
                async for e in provider.fetch_address_history(
                    WALLET,
                    start=datetime(2026, 1, 1, tzinfo=UTC),
                    end=datetime(2026, 12, 1, tzinfo=UTC),
                )
            ]

        with pytest.raises(ProviderRequestError):
            asyncio.run(run())

    def test_unexpected_payload_shape_raises(self):
        provider, _ = _provider([httpx.Response(200, json={"error": "nope"})])

        async def run():
            return [
                e
                async for e in provider.fetch_address_history(
                    WALLET,
                    start=datetime(2026, 1, 1, tzinfo=UTC),
                    end=datetime(2026, 12, 1, tzinfo=UTC),
                )
            ]

        with pytest.raises(ProviderRequestError):
            asyncio.run(run())

    def test_backfill_without_addresses_is_refused(self):
        """Helius indexes by address; a chain-wide sweep would return silence."""
        provider, _ = _provider([])

        async def run():
            return [
                e
                async for e in provider.backfill(
                    datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
                )
            ]

        with pytest.raises(ValueError, match="explicit addresses"):
            asyncio.run(run())

    def test_missing_api_key_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="API key"):
            HeliusChainProvider("")


class TestIdempotency:
    def test_same_transaction_yields_the_same_event_id(self):
        """Re-running a sweep must not duplicate rows."""
        provider, _ = _provider([])
        first = provider.parse_transaction(_swap_tx(), subject=WALLET)[0]
        second = provider.parse_transaction(_swap_tx(), subject=WALLET)[0]
        assert first.event_id == second.event_id

    def test_observed_at_is_never_before_chain_time(self):
        """The leakage audit rejects events observed before they happened."""
        provider, _ = _provider([])
        raw = _swap_tx()
        raw["timestamp"] = int((datetime.now(tz=UTC) + timedelta(days=1)).timestamp())
        event = provider.parse_transaction(raw, subject=WALLET)[0]
        assert event.observed_at >= event.chain_time
