"""The daily collector: build the history nobody will sell us.

Everything else in this system depends on knowing what happened to a token
*before* it was obvious. That history is the one thing no affordable API
provides. Listings answer "what is trading now"; a token that ran in March and
is a corpse today does no volume, so it is invisible to every current-state
query. The sweep can partly work around this with new-listing feeds, but only
back to that feed's horizon, and never with a record of what a token looked like
on the specific day a wallet was buying it.

So we build it forward. Every day the collector records the market state of
everything it is watching, stamped with our own clock. In three months that is
three months of point-in-time truth that cost nothing to acquire and cannot be
revoked. In a year it is the asset the rest of the product stands on.

## The watchlist is sticky, and that is the whole design

The obvious implementation records today's universe each day. It would be
worthless, and worse, quietly worthless: it would hold a record of every token
on the days it was big, and no record of any collapse. That is survivorship
bias — the same failure that emptied the universe of `rug_or_collapse` outcomes
— rebuilt inside our own archive where no provider could be blamed for it.

So once an asset clears the traction floor, it is watched permanently. It is
observed on the days it is trading and on the days it is dead, and each
observation records which of those it was. Assets that never qualified do get
retired after a stretch of silence, because the cost has to be bounded
somewhere, and a token that never traded carries no footprint worth finding.

## Cost is bounded and the truncation is reported

A run has a hard observation cap. When it binds, today's universe is observed
first and the rest are taken oldest-observation-first, so no watched asset can
starve indefinitely. What was skipped goes in the report rather than a log,
because a collector that silently observes half its watchlist produces gaps that
look like real quiet periods months later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from alphagraph.core.timeutil import utcnow
from alphagraph.db.models import Asset, AssetObservation, WatchedAsset
from alphagraph.outcomes.registry import (
    OutcomeRegistry,
    daily_configs,
    detect_collapse,
    detect_price_run,
    detect_pump_event,
    detect_revival,
)
from alphagraph.providers.base import Candle
from alphagraph.providers.universe import PoolRef, UniverseSource

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    """What to watch, how long, and how much to spend doing it."""

    #: Daily volume that makes an asset worth watching forever. Deliberately
    #: lower than the sweep's $2M listing floor: by the time something is
    #: unambiguously big it is too late to have been early to it, and the whole
    #: point of collecting forward is to hold history from before that.
    qualify_volume_24h_usd: Decimal = Decimal(250_000)

    #: Below this, nobody could meaningfully trade it that day.
    quiet_volume_24h_usd: Decimal = Decimal(5_000)

    #: Consecutive quiet days before an asset that *never* qualified is retired.
    #: Qualified assets are never retired — see the module docstring.
    retire_after_quiet_days: int = 21

    #: Hard cap on price-series fetches per run.
    max_observations: int = 400

    #: Pages of the provider's universe listing to pull.
    universe_pages: int = 3


@dataclass
class CollectionReport:
    ran_at: datetime = field(default_factory=utcnow)
    universe_size: int = 0
    newly_watched: int = 0
    newly_qualified: int = 0
    observed: int = 0
    observed_outside_universe: int = 0
    price_missing: int = 0
    retired: int = 0
    watchlist_size: int = 0
    outcomes_detected: dict[str, int] = field(default_factory=dict)
    skipped_for_budget: int = 0
    provider_usage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at.isoformat(),
            "universe_size": self.universe_size,
            "newly_watched": self.newly_watched,
            "newly_qualified": self.newly_qualified,
            "observed": self.observed,
            "observed_outside_universe": self.observed_outside_universe,
            "price_missing": self.price_missing,
            "retired": self.retired,
            "watchlist_size": self.watchlist_size,
            "outcomes_detected": self.outcomes_detected,
            "skipped_for_budget": self.skipped_for_budget,
            "provider_usage": self.provider_usage,
        }


class DailyCollector:
    def __init__(
        self,
        session: Session,
        market: UniverseSource,
        config: CollectorConfig | None = None,
    ) -> None:
        self.session = session
        self.market = market
        self.config = config or CollectorConfig()
        self.registry = OutcomeRegistry(session)
        self.report = CollectionReport()

    async def run(self, now: datetime | None = None) -> CollectionReport:
        now = now or utcnow()
        self.report.ran_at = now

        universe = await self._listing()
        in_universe = self._admit(universe, now)
        targets = self._select_targets(in_universe, now)

        for watched, listed in targets:
            await self._observe(watched, listed, now)

        self._retire(now)
        self.session.flush()

        self.report.watchlist_size = int(
            self.session.scalar(
                select(func.count())
                .select_from(WatchedAsset)
                .where(WatchedAsset.retired_at.is_(None))
            )
            or 0
        )
        self.report.provider_usage = {self.market.name: self.market.usage}
        return self.report

    # -------------------------------------------------------------- listing

    async def _listing(self) -> list[PoolRef]:
        entries = await self.market.universe(pages=self.config.universe_pages)
        self.report.universe_size = len(entries)
        return entries

    def _admit(self, entries: list[PoolRef], now: datetime) -> dict[str, PoolRef]:
        """Add today's listing to the watchlist, and record what qualified.

        Qualification is a one-way door. An asset that cleared the floor once is
        evidence forever, regardless of what it does afterwards — especially if
        what it does afterwards is die.
        """
        listed: dict[str, PoolRef] = {}
        for entry in entries:
            asset_id = f"solana:{entry.token_address}"
            listed[asset_id] = entry
            self._ensure_asset(asset_id, entry, now)

            watched = self.session.get(WatchedAsset, asset_id)
            if watched is None:
                watched = WatchedAsset(
                    asset_id=asset_id,
                    series_key=entry.pool_address,
                    symbol=entry.symbol,
                    source=self.market.name,
                    first_watched_at=now,
                    # Set explicitly rather than left to the column default,
                    # which does not apply until flush — and this row is read
                    # back within the same run.
                    peak_volume_24h_usd=Decimal(0),
                    quiet_days=0,
                )
                self.session.add(watched)
                self.report.newly_watched += 1
            else:
                # A provider swap changes how the series is addressed.
                watched.series_key = entry.pool_address
                watched.retired_at = None

            volume = entry.volume_24h_usd or Decimal(0)
            if volume > watched.peak_volume_24h_usd:
                watched.peak_volume_24h_usd = volume
            if watched.qualified_at is None and volume >= self.config.qualify_volume_24h_usd:
                watched.qualified_at = now
                self.report.newly_qualified += 1
        return listed

    def _ensure_asset(self, asset_id: str, entry: PoolRef, now: datetime) -> None:
        if self.session.get(Asset, asset_id) is not None:
            return
        self.session.add(
            Asset(
                id=asset_id,
                network="solana",
                address=entry.token_address,
                symbol=entry.symbol,
                decimals=9,
                # None when the provider did not report a creation time. Left
                # absent rather than defaulted to now, because the bot detector
                # measures entry latency from this and a fabricated origin would
                # make every early buyer look like a sniper.
                first_seen_at=entry.created_at,
            )
        )

    # ------------------------------------------------------------- selection

    def _select_targets(
        self, listed: dict[str, PoolRef], now: datetime
    ) -> list[tuple[WatchedAsset, PoolRef | None]]:
        """Today's listing first, then the watchlist by staleness.

        The second group is the reason this class exists. Those are assets that
        have fallen out of the listing — the ones a survivor-selected universe
        drops — and observing them is how a collapse ends up on the record.
        """
        active = list(
            self.session.execute(
                select(WatchedAsset).where(WatchedAsset.retired_at.is_(None))
            ).scalars()
        )

        in_listing = [w for w in active if w.asset_id in listed]
        absent = [w for w in active if w.asset_id not in listed]
        # Never observed sorts first: an asset with no history at all is the
        # most expensive gap to leave open.
        absent.sort(key=lambda w: w.last_observed_at or datetime.min.replace(tzinfo=UTC))

        ordered = in_listing + absent
        cap = self.config.max_observations
        if len(ordered) > cap:
            self.report.skipped_for_budget = len(ordered) - cap
            ordered = ordered[:cap]

        return [(w, listed.get(w.asset_id)) for w in ordered]

    # ----------------------------------------------------------- observation

    async def _observe(self, watched: WatchedAsset, listed: PoolRef | None, now: datetime) -> None:
        candles = await self._candles(watched.series_key)
        latest = candles[-1] if candles else None

        volume = self._volume(listed, latest)
        price = latest.close if latest is not None else None
        if price is None:
            self.report.price_missing += 1

        self._write_observation(
            watched=watched,
            now=now,
            price=price,
            volume=volume,
            liquidity=listed.reserve_usd if listed else None,
            in_universe=listed is not None,
        )

        if volume is not None and volume > watched.peak_volume_24h_usd:
            watched.peak_volume_24h_usd = volume
        self._update_quiet_streak(watched, listed, volume)
        watched.last_observed_at = now

        self.report.observed += 1
        if listed is None:
            self.report.observed_outside_universe += 1

        if candles:
            self._detect(watched, candles)

    def _update_quiet_streak(
        self, watched: WatchedAsset, listed: PoolRef | None, volume: Decimal | None
    ) -> None:
        """Count consecutive days with no sign of life. Only unqualified assets
        are ever retired on this, so the cost of being wrong is bounded.

        Unknown volume is treated differently depending on why it is unknown.
        On an asset that appeared in today's listing, a missing figure is a
        provider gap — the asset is demonstrably still being quoted, so the
        streak is left alone rather than counted against it. On an asset that is
        absent from the listing *and* returns no price series, there is no
        evidence of life anywhere, and that is what silence looks like.

        The distinction matters because the alternative — treating every gap as
        silence — would retire assets during a provider outage and leave holes
        in the archive that read as real quiet periods months later.
        """
        if volume is None:
            if listed is not None:
                return
            watched.quiet_days += 1
            return
        if volume < self.config.quiet_volume_24h_usd:
            watched.quiet_days += 1
        else:
            watched.quiet_days = 0

    async def _candles(self, series_key: str) -> list[Candle]:
        try:
            return await self.market.pool_candles(series_key)
        except Exception as exc:
            # Counted as a missing price rather than swallowed silently. A run
            # that fails on one token should still archive the other 399.
            log.warning("candles failed for %s: %s", series_key, exc)
            return []

    @staticmethod
    def _volume(listed: PoolRef | None, latest: Candle | None) -> Decimal | None:
        """Prefer the listing's 24h figure; fall back to the last candle.

        Returns None when neither is available, never zero. Zero is a claim that
        nothing traded, and inventing it would age into a fake quiet period that
        the revival detector would later read as dormancy.
        """
        if listed is not None and listed.volume_24h_usd is not None:
            return listed.volume_24h_usd
        if latest is not None:
            return latest.volume_usd
        return None

    def _write_observation(
        self,
        *,
        watched: WatchedAsset,
        now: datetime,
        price: Decimal | None,
        volume: Decimal | None,
        liquidity: Decimal | None,
        in_universe: bool,
    ) -> None:
        day = now.date().isoformat()
        existing = self.session.execute(
            select(AssetObservation).where(
                AssetObservation.asset_id == watched.asset_id,
                AssetObservation.observed_on == day,
            )
        ).scalar_one_or_none()

        row = existing or AssetObservation(
            asset_id=watched.asset_id,
            observed_on=day,
        )
        row.observed_at = now
        row.price_usd = price
        row.volume_24h_usd = volume
        row.liquidity_usd = liquidity
        row.market_cap_usd = None
        row.in_universe = in_universe
        row.source = self.market.name
        if existing is None:
            self.session.add(row)

    def _detect(self, watched: WatchedAsset, candles: list[Candle]) -> None:
        from alphagraph.core.addresses import Network
        from alphagraph.core.events import AssetRef

        _, _, address = watched.asset_id.partition(":")
        asset = AssetRef(network=Network.SOLANA, address=address, symbol=watched.symbol)

        run_cfg, revival_cfg, collapse_cfg = daily_configs()
        run = detect_price_run(asset, candles, run_cfg)
        revival = detect_revival(asset, candles, revival_cfg)
        collapse = detect_collapse(asset, candles, collapse_cfg)
        pump = detect_pump_event(asset, candles, run, collapse)

        for outcome in (run, revival, collapse, pump):
            if outcome is None:
                continue
            if self.registry.upsert(outcome):
                key = outcome.outcome_class.value
                self.report.outcomes_detected[key] = self.report.outcomes_detected.get(key, 0) + 1

    # -------------------------------------------------------------- retiring

    def _retire(self, now: datetime) -> None:
        """Drop only assets that never mattered.

        A qualified asset is never retired no matter how long it has been dead.
        Its dead days are the record, and the day it comes back — the "died and
        then randomly pumped" case — is only visible if we kept watching through
        the silence.
        """
        stale = self.session.execute(
            select(WatchedAsset).where(
                WatchedAsset.retired_at.is_(None),
                WatchedAsset.qualified_at.is_(None),
                WatchedAsset.quiet_days >= self.config.retire_after_quiet_days,
            )
        ).scalars()
        for watched in stale:
            watched.retired_at = now
            self.report.retired += 1


def observation_series(
    session: Session, asset_id: str, *, as_of: datetime | None = None
) -> list[AssetObservation]:
    """Our own recorded history for an asset, up to a point in time.

    Filtered on `observed_at`, not on any provider timestamp, so a backtest run
    against a past date sees exactly what the collector knew on that date.
    """
    stmt = select(AssetObservation).where(AssetObservation.asset_id == asset_id)
    if as_of is not None:
        stmt = stmt.where(AssetObservation.observed_at <= as_of)
    return list(session.execute(stmt.order_by(AssetObservation.observed_at)).scalars())


def archive_span(session: Session) -> timedelta:
    """How much point-in-time history the collector has accumulated so far.

    Worth surfacing: for the first months this number is the honest answer to
    "why is discovery finding so little", and it grows without any further work.
    """
    first = session.scalar(
        select(AssetObservation.observed_at).order_by(AssetObservation.observed_at).limit(1)
    )
    last = session.scalar(
        select(AssetObservation.observed_at).order_by(AssetObservation.observed_at.desc()).limit(1)
    )
    if first is None or last is None:
        return timedelta(0)
    return last - first
