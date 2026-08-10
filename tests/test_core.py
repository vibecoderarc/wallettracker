"""Core primitives: addresses, units, time, idempotency, ingestion."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from alphagraph.core.addresses import Network, normalize_address
from alphagraph.core.events import EventType, Side
from alphagraph.core.idempotency import event_key
from alphagraph.core.timeutil import ensure_utc, parse_utc
from alphagraph.core.units import TokenAmount


class TestAddresses:
    def test_evm_lowercased(self):
        mixed = "0xAbCdEf0123456789AbCdEf0123456789AbCdEf01"
        assert normalize_address(mixed, Network.BSC) == mixed.lower()

    def test_solana_case_preserved(self):
        """Base58 is case-sensitive; lowercasing would corrupt the address."""
        address = "So11111111111111111111111111111111111111112"
        assert normalize_address(address, Network.SOLANA) == address

    def test_invalid_rejected(self):
        with pytest.raises(ValueError):
            normalize_address("0xnope", Network.BSC)
        with pytest.raises(ValueError):
            normalize_address("has0OIl-invalid-base58-chars!!", Network.SOLANA)


class TestUnits:
    def test_raw_integer_preserved(self):
        amount = TokenAmount(raw=123456789, decimals=6)
        assert amount.raw == 123456789
        assert amount.value == Decimal("123.456789")

    def test_float_does_not_leak_binary_error(self):
        assert TokenAmount.from_decimal(0.1, 9).raw == 100_000_000

    def test_mismatched_decimals_rejected(self):
        with pytest.raises(ValueError):
            TokenAmount(1, 6) + TokenAmount(1, 9)

    def test_implausible_decimals_rejected(self):
        with pytest.raises(ValueError):
            TokenAmount(1, 99)


class TestTime:
    def test_naive_rejected(self):
        with pytest.raises(ValueError):
            ensure_utc(datetime(2026, 1, 1))

    def test_parse_requires_timezone(self):
        assert parse_utc("2026-01-01T00:00:00Z").tzinfo is not None
        with pytest.raises(ValueError):
            parse_utc("2026-01-01T00:00:00")


class TestIdempotency:
    def test_stable_across_calls(self):
        args = dict(network="solana", transaction_id="abc", event_type="swap", event_index=1)
        assert event_key(**args) == event_key(**args)

    def test_semantic_index_disambiguates(self):
        base = dict(network="solana", transaction_id="abc", event_type="swap", event_index=1)
        assert event_key(**base, semantic_index=0) != event_key(**base, semantic_index=1)

    def test_independent_of_provider_and_time(self):
        """Replaying via a different provider must not create a duplicate."""
        a = event_key(network="solana", transaction_id="t", event_type="swap", event_index=0)
        b = event_key(network="solana", transaction_id="t", event_type="swap", event_index=0)
        assert a == b


class TestIngestionIdempotency:
    def test_replay_writes_no_duplicates(self, world):
        from alphagraph.db.session import create_all, get_session_factory, reset_engine
        from alphagraph.ingestion.pipeline import Ingestor
        from alphagraph.providers.fixture import FixtureChainProvider
        from alphagraph.providers.world import WORLD_END, WORLD_START

        reset_engine()
        create_all("sqlite+pysqlite:///:memory:")
        session = get_session_factory()()

        async def run():
            provider = FixtureChainProvider(world)
            ingestor = Ingestor(session)
            first = await ingestor.backfill(provider, WORLD_START, WORLD_END)
            second = await ingestor.backfill(provider, WORLD_START, WORLD_END)
            return first, second

        first, second = asyncio.run(run())
        assert first.written > 1000
        assert first.dead_lettered == 0
        assert second.written == 0, "replay created duplicate rows"
        assert second.duplicates == first.written
        session.close()
        reset_engine()


class TestEventSemantics:
    def _event(self, **kwargs):
        from alphagraph.core.events import AssetRef, CanonicalEvent

        defaults = dict(
            event_id="e1",
            event_type=EventType.TRANSFER,
            network=Network.SOLANA,
            block_height=1,
            transaction_id="t1",
            chain_time=datetime(2026, 1, 1, tzinfo=UTC),
            observed_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=5),
            actor="A" * 40,
            asset=AssetRef(network=Network.SOLANA, address="B" * 40),
        )
        return CanonicalEvent(**{**defaults, **kwargs})

    def test_transfer_is_not_an_acquisition(self):
        """Airdropping tokens to a wallet must not forge a track record."""
        assert self._event(event_type=EventType.TRANSFER).is_acquisition is False

    def test_buy_is_an_acquisition(self):
        assert self._event(event_type=EventType.SWAP, side=Side.BUY).is_acquisition is True

    def test_sell_is_not(self):
        assert self._event(event_type=EventType.SWAP, side=Side.SELL).is_acquisition is False

    def test_unfilled_order_is_intent_only(self):
        event = self._event(event_type=EventType.ORDER_PLACED, side=Side.LONG, filled=False)
        assert event.is_intent_only is True
