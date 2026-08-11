"""Tests for the daily collector.

The collector's entire value is that its archive is *not* survivor-selected.
Everything else it does — paging, budgets, idempotency — is plumbing that
several other modules already demonstrate. So most of these tests attack the
one property that would be silently worthless if it broke: that an asset which
falls out of the listing keeps being recorded, and that its dead days end up on
the record rather than as a gap.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from alphagraph.collector import (
    CollectorConfig,
    DailyCollector,
    archive_span,
    observation_series,
)
from alphagraph.db.models import Asset, AssetObservation, WatchedAsset
from alphagraph.db.session import create_all, reset_engine
from alphagraph.providers.base import Candle
from alphagraph.providers.universe import PoolRef

DAY = timedelta(days=1)
START = datetime(2026, 4, 1, tzinfo=UTC)


@pytest.fixture
def session(tmp_path):
    from sqlalchemy.orm import Session

    reset_engine()
    engine = create_all(f"sqlite+pysqlite:///{tmp_path / 'collector.db'}")
    db = Session(bind=engine, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        reset_engine()


def _entry(symbol: str, volume: float, liquidity: float = 100_000) -> PoolRef:
    address = f"{symbol}1111111111111111111111111111111111"
    return PoolRef(
        pool_address=address,
        token_address=address,
        symbol=symbol,
        reserve_usd=Decimal(str(liquidity)),
        volume_24h_usd=Decimal(str(volume)),
        created_at=None,
    )


def _candles(closes: list[float], volumes: list[float], start: datetime = START) -> list[Candle]:
    return [
        Candle(
            start=start + i * DAY,
            open=Decimal(str(c)),
            high=Decimal(str(c)),
            low=Decimal(str(c)),
            close=Decimal(str(c)),
            volume_usd=Decimal(str(v)),
        )
        for i, (c, v) in enumerate(zip(closes, volumes, strict=True))
    ]


class FakeMarket:
    """A universe source whose listing can change between days."""

    name = "fake"

    def __init__(
        self, listings: list[list[PoolRef]], candles: dict[str, list[Candle]] | None = None
    ):
        self._listings = listings
        self._candles = candles or {}
        self.calls = 0
        self.candle_calls: list[str] = []

    @property
    def usage(self) -> dict[str, int | str]:
        return {"requests": self.calls}

    async def universe(self, pages: int = 3) -> list[PoolRef]:
        index = min(self.calls, len(self._listings) - 1)
        self.calls += 1
        return self._listings[index]

    async def pool_candles(self, pool_address: str) -> list[Candle]:
        self.candle_calls.append(pool_address)
        return self._candles.get(pool_address, [])

    def register_pool(self, asset, pool_address: str) -> None:
        return None

    def series_key_for(self, asset) -> str | None:
        return asset.address


def _run(session, market, config=None, now=START):
    collector = DailyCollector(session, market, config or CollectorConfig())
    return asyncio.run(collector.run(now=now))


class TestTheArchiveIsNotSurvivorSelected:
    """The one property that makes this worth running every day."""

    def test_an_asset_that_falls_out_of_the_listing_is_still_observed(self, session):
        winner = _entry("RUNUP", 900_000)
        market = FakeMarket([[winner], []])

        _run(session, market, now=START)
        report = _run(session, market, now=START + DAY)

        assert report.universe_size == 0, "the listing no longer contains it"
        assert report.observed == 1, (
            "an asset that stops trading must keep being recorded — its dead "
            "days are the collapse, and dropping it deletes the evidence"
        )
        assert report.observed_outside_universe == 1

    def test_dead_days_are_recorded_as_dead_rather_than_missing(self, session):
        winner = _entry("RUNUP", 900_000)
        market = FakeMarket([[winner], []])
        _run(session, market, now=START)
        _run(session, market, now=START + DAY)

        rows = observation_series(session, f"solana:{winner.token_address}")
        assert [r.in_universe for r in rows] == [True, False], (
            "the record has to distinguish 'was trading' from 'we kept watching'"
        )

    def test_a_qualified_asset_is_never_retired_however_long_it_stays_dead(self, session):
        winner = _entry("RUNUP", 900_000)
        market = FakeMarket([[winner], []])
        config = CollectorConfig(retire_after_quiet_days=2, max_observations=50)

        _run(session, market, config, now=START)
        for day in range(1, 12):
            _run(session, market, config, now=START + day * DAY)

        watched = session.get(WatchedAsset, f"solana:{winner.token_address}")
        assert watched.qualified_at is not None
        assert watched.retired_at is None, (
            "the 'died then randomly pumped' case is only visible if we watched through the silence"
        )
        assert watched.quiet_days >= 2

    def test_an_asset_that_never_qualified_is_retired_to_bound_cost(self, session):
        dust = _entry("DUST", 400, liquidity=50)
        market = FakeMarket([[dust], []])
        config = CollectorConfig(
            qualify_volume_24h_usd=Decimal(250_000),
            quiet_volume_24h_usd=Decimal(5_000),
            retire_after_quiet_days=2,
        )

        # Day one: listed but below the tradable line. Day two: gone from the
        # listing with no series either, which is what silence looks like.
        first = _run(session, market, config, now=START)
        second = _run(session, market, config, now=START + DAY)

        assert first.retired == 0
        assert second.retired == 1
        watched = session.get(WatchedAsset, f"solana:{dust.token_address}")
        assert watched.qualified_at is None
        assert watched.retired_at == START + DAY

    def test_a_retired_asset_rejoins_when_it_comes_back_to_life(self, session):
        """The "died and then randomly pumped out of the blue" case."""
        dust = _entry("DUST", 400, liquidity=50)
        revived = _entry("DUST", 900_000)
        config = CollectorConfig(retire_after_quiet_days=1)
        market = FakeMarket([[dust], [], [revived]])
        asset_id = f"solana:{dust.token_address}"

        _run(session, market, config, now=START)
        _run(session, market, config, now=START + DAY)
        assert session.get(WatchedAsset, asset_id).retired_at is not None

        _run(session, market, config, now=START + 2 * DAY)
        watched = session.get(WatchedAsset, asset_id)
        assert watched.retired_at is None
        assert watched.qualified_at == START + 2 * DAY, (
            "coming back from the dead is exactly what qualifies it to be "
            "watched permanently from then on"
        )

    def test_a_provider_gap_on_a_listed_asset_does_not_count_as_silence(self, session):
        """A missing figure is not evidence of death."""
        gap = PoolRef(
            pool_address="G1",
            token_address="G1",
            symbol="GAP",
            reserve_usd=None,
            volume_24h_usd=None,
            created_at=None,
        )
        config = CollectorConfig(retire_after_quiet_days=2)
        market = FakeMarket([[gap], [gap], [gap]])
        for day in range(3):
            _run(session, market, config, now=START + day * DAY)

        watched = session.get(WatchedAsset, "solana:G1")
        assert watched.quiet_days == 0
        assert watched.retired_at is None, (
            "retiring during a provider outage would leave holes that read as "
            "real quiet periods months later"
        )


class TestQualification:
    def test_clearing_the_floor_once_qualifies_permanently(self, session):
        big = _entry("BIG", 900_000)
        small = _entry("BIG", 1_000)
        market = FakeMarket([[big], [small]])

        first = _run(session, market, now=START)
        second = _run(session, market, now=START + DAY)

        assert first.newly_qualified == 1
        assert second.newly_qualified == 0, "qualification is not re-earned each day"
        watched = session.get(WatchedAsset, f"solana:{big.token_address}")
        assert watched.qualified_at == START

    def test_peak_volume_survives_the_collapse(self, session):
        big = _entry("BIG", 900_000)
        dead = _entry("BIG", 12)
        market = FakeMarket([[big], [dead]])

        _run(session, market, now=START)
        _run(session, market, now=START + DAY)

        watched = session.get(WatchedAsset, f"solana:{big.token_address}")
        assert watched.peak_volume_24h_usd == Decimal("900000")


class TestPointInTime:
    def test_observations_are_stamped_with_our_clock_not_the_providers(self, session):
        # Candles are dated a month before the run; the observation must be
        # stamped when *we* recorded it, or a backtest would see today's data
        # while replaying April.
        entry = _entry("TOK", 500_000)
        market = FakeMarket(
            [[entry]],
            {entry.pool_address: _candles([1.0, 1.1], [100_000, 120_000], start=START - 30 * DAY)},
        )
        run_at = START + 5 * DAY
        _run(session, market, now=run_at)

        row = session.execute(select(AssetObservation)).scalar_one()
        assert row.observed_at == run_at

    def test_the_series_can_be_read_as_of_a_past_date(self, session):
        entry = _entry("TOK", 500_000)
        market = FakeMarket([[entry], [entry], [entry]])
        for day in range(3):
            _run(session, market, now=START + day * DAY)

        asset_id = f"solana:{entry.token_address}"
        assert len(observation_series(session, asset_id)) == 3
        assert len(observation_series(session, asset_id, as_of=START + DAY)) == 2

    def test_archive_span_reports_how_much_history_exists(self, session):
        entry = _entry("TOK", 500_000)
        market = FakeMarket([[entry], [entry]])
        _run(session, market, now=START)
        _run(session, market, now=START + 9 * DAY)
        assert archive_span(session) == timedelta(days=9)


class TestIdempotency:
    def test_running_twice_in_a_day_overwrites_rather_than_duplicates(self, session):
        entry = _entry("TOK", 500_000)
        market = FakeMarket([[entry], [entry]])

        _run(session, market, now=START)
        _run(session, market, now=START + timedelta(hours=6))

        rows = session.execute(select(AssetObservation)).scalars().all()
        assert len(rows) == 1
        assert rows[0].observed_at == START + timedelta(hours=6)


class TestBudget:
    def test_the_cap_is_enforced_and_the_shortfall_reported(self, session):
        entries = [_entry(f"T{i:03d}", 500_000) for i in range(10)]
        market = FakeMarket([entries])
        report = _run(session, market, CollectorConfig(max_observations=4))

        assert report.observed == 4
        assert report.skipped_for_budget == 6, (
            "silent truncation ages into gaps that look like real quiet periods"
        )

    def test_the_listing_is_observed_before_the_rest_of_the_watchlist(self, session):
        old = _entry("OLD", 500_000)
        fresh = _entry("NEW", 500_000)
        market = FakeMarket([[old], [fresh]])

        _run(session, market, now=START)
        report = _run(session, market, CollectorConfig(max_observations=1), now=START + DAY)

        assert report.observed == 1
        assert report.observed_outside_universe == 0, "today's listing comes first"

    def test_never_observed_assets_are_not_starved_by_the_cap(self, session):
        a, b = _entry("AAA", 500_000), _entry("BBB", 500_000)
        market = FakeMarket([[a, b], []])
        # Day one only reaches AAA; day two the listing is empty, so both are
        # off-listing and BBB — never observed — must come first.
        _run(session, market, CollectorConfig(max_observations=1), now=START)
        _run(session, market, CollectorConfig(max_observations=1), now=START + DAY)

        assert observation_series(session, f"solana:{b.token_address}"), (
            "an asset with no history at all is the most expensive gap to leave open"
        )


class TestFailureHandling:
    def test_one_failing_asset_does_not_end_the_run(self, session):
        good, bad = _entry("GOOD", 500_000), _entry("BAD", 500_000)

        class Exploding(FakeMarket):
            async def pool_candles(self, pool_address: str):
                if pool_address == bad.pool_address:
                    raise RuntimeError("provider blew up")
                return []

        report = _run(session, Exploding([[good, bad]]))
        assert report.observed == 2, "the other asset still has to be archived"

    def test_absent_volume_is_absent_not_zero(self, session):
        entry = PoolRef(
            pool_address="X1",
            token_address="X1",
            symbol="X",
            reserve_usd=None,
            volume_24h_usd=None,
            created_at=None,
        )
        _run(session, FakeMarket([[entry]]))

        row = session.execute(select(AssetObservation)).scalar_one()
        assert row.volume_24h_usd is None, (
            "a fabricated zero ages into a fake quiet period the revival "
            "detector would later read as dormancy"
        )

    def test_unknown_creation_time_is_left_unset(self, session):
        entry = _entry("TOK", 500_000)
        _run(session, FakeMarket([[entry]]))

        asset = session.get(Asset, f"solana:{entry.token_address}")
        assert asset.first_seen_at is None, (
            "defaulting creation time to now would make every early buyer look "
            "like a sniper to the bot detector"
        )
