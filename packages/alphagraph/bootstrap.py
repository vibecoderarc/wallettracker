"""Phase A: the first discovery sweep against real Solana data.

Ties the two live providers to the discovery engine that already exists and is
tested. Produces a ranked candidate list with real hit rates and a real base
rate, starting from no wallets at all.

## The trap this is built to avoid

The obvious implementation fetches buyers of tokens that ran, feeds them to the
engine, and reports the results. It produces garbage, and flattering garbage:
every wallet appears to have a ~100% hit rate.

The reason is the denominator. A wallet's hit rate is
`outcomes it was early to / assets it touched`. If the only transactions ever
ingested come from windows before tokens that ran, then every asset a wallet
touched *is* a winner by construction. The denominator equals the numerator, and
the engine's guards — which compare against a population base rate — have
nothing honest to compare against.

So the sweep runs in two passes:

  **Pass 1 — surface.** Fetch buyers from the windows before confirmed outcomes.
  This produces suspects, and nothing more. No rate computed here is meaningful.

  **Pass 2 — qualify.** For each suspect, fetch their *full* trading history
  across the window, including everything that went nowhere. This is what makes
  the denominator honest, and it is the only reason any number the engine
  produces afterwards means anything.

Pass 2 is what costs the requests, and it is not optional. A sweep that skips it
has not measured anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from alphagraph.core.events import CanonicalEvent, EventType, Side
from alphagraph.core.outcomes import PREDICTIVE_CLASSES
from alphagraph.core.timeutil import utcnow
from alphagraph.discovery.engine import DiscoveryEngine
from alphagraph.ingestion.pipeline import Ingestor
from alphagraph.outcomes.registry import (
    OutcomeRegistry,
    daily_configs,
    detect_collapse,
    detect_price_run,
    detect_pump_event,
    detect_revival,
)
from alphagraph.providers.helius import HeliusChainProvider
from alphagraph.providers.universe import PoolRef, UniverseSource

log = logging.getLogger(__name__)

#: How far before an outcome to look for buyers. Matches the discovery engine's
#: widest lookback window; fetching more would cost requests the engine ignores.
LOOKBACK = timedelta(days=30)

#: Was this token ever tradable? Judged on the busiest day in its history, not
#: on what it holds now.
#:
#: Present-day liquidity is the wrong test for a universe: a token that ran and
#: then rugged holds nothing today and would be excluded, taking every
#: `rug_or_collapse` outcome with it — the class that distinguishes a wallet
#: early into things that last from one early into things that die. Peak
#: historical volume admits the ones that mattered and were later abandoned,
#: while still rejecting tokens nobody could ever have traded.
MIN_PEAK_DAILY_VOLUME_USD = 50_000


@dataclass
class SweepBudget:
    """Hard caps on spend. Reached limits are reported, never silently hit."""

    max_helius_requests: int = 3_000
    max_pools: int = 300
    max_pages_per_address: int = 8
    max_candidates_to_qualify: int = 150

    def as_dict(self) -> dict[str, int]:
        return self.__dict__.copy()


@dataclass
class SweepReport:
    started_at: datetime = field(default_factory=utcnow)
    pools_examined: int = 0
    pools_with_prices: int = 0
    pools_never_tradable: int = 0
    pools_dead_but_counted: int = 0
    outcomes_detected: dict[str, int] = field(default_factory=dict)
    outcome_windows_fetched: int = 0
    suspects_found: int = 0
    suspects_qualified: int = 0
    events_written: int = 0
    unknown_interactions: int = 0
    candidates_passed: list[str] = field(default_factory=list)
    base_rate: float = 0.0
    truncated: list[str] = field(default_factory=list)
    provider_usage: dict[str, Any] = field(default_factory=dict)

    @property
    def parse_coverage(self) -> float:
        """Share of ingested events we could interpret.

        A low number means the candidate list is built on partial history, so
        it belongs in the report rather than buried in a log.
        """
        total = self.events_written
        if total == 0:
            return 0.0
        return 1.0 - (self.unknown_interactions / total)

    def as_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "pools_examined": self.pools_examined,
            "pools_with_prices": self.pools_with_prices,
            "pools_never_tradable": self.pools_never_tradable,
            "pools_dead_but_counted": self.pools_dead_but_counted,
            "outcomes_detected": self.outcomes_detected,
            "outcome_windows_fetched": self.outcome_windows_fetched,
            "suspects_found": self.suspects_found,
            "suspects_qualified": self.suspects_qualified,
            "events_written": self.events_written,
            "unknown_interactions": self.unknown_interactions,
            "parse_coverage": round(self.parse_coverage, 4),
            "candidates_passed": self.candidates_passed,
            "base_rate": round(self.base_rate, 5),
            "truncated": self.truncated,
            "provider_usage": self.provider_usage,
        }


class BootstrapSweep:
    def __init__(
        self,
        session: Session,
        chain: HeliusChainProvider,
        market: UniverseSource,
        budget: SweepBudget | None = None,
    ) -> None:
        self.session = session
        self.chain = chain
        self.market = market
        self.budget = budget or SweepBudget()
        self.registry = OutcomeRegistry(session)
        self.ingestor = Ingestor(session)
        self.report = SweepReport()

    # ------------------------------------------------------------ estimation

    async def estimate(self, pages: int = 5) -> dict[str, Any]:
        """What a sweep would cost, without spending the Helius allowance.

        Only the keyless provider is touched. Run this before the real thing so
        the request count is a decision rather than a discovery.
        """
        pools = (await self.market.universe(pages=pages))[: self.budget.max_pools]

        outcomes = 0
        priced = 0
        for pool in pools:
            candles = await self.market.pool_candles(pool.pool_address)
            if not candles:
                continue
            priced += 1
            if self._detect(pool, candles):
                outcomes += 1

        per_address = self.budget.max_pages_per_address
        pass1 = outcomes * per_address
        pass2 = self.budget.max_candidates_to_qualify * per_address
        return {
            "pools_examined": len(pools),
            "pools_with_prices": priced,
            "outcomes_detected": outcomes,
            "estimated_helius_requests": pass1 + pass2,
            "budget_cap": self.budget.max_helius_requests,
            "within_budget": (pass1 + pass2) <= self.budget.max_helius_requests,
            "market_data_usage": self.market.usage,
        }

    # ---------------------------------------------------------------- passes

    def _detect(self, pool: PoolRef, candles: list) -> list:
        # Live sweeps read daily candles, so the detectors need the scaled
        # volume floors. Using the hourly defaults here would admit dust.
        run_cfg, revival_cfg, collapse_cfg = daily_configs()
        run = detect_price_run(pool.asset, candles, run_cfg)
        revival = detect_revival(pool.asset, candles, revival_cfg)
        collapse = detect_collapse(pool.asset, candles, collapse_cfg)
        pump = detect_pump_event(pool.asset, candles, run, collapse)
        return [o for o in (run, revival, collapse, pump) if o is not None]

    async def build_universe(self, pages: int = 5) -> list[PoolRef]:
        """Establish the asset universe and record what happened to each.

        Every pool examined is registered, including the ones where nothing
        happened. Those are the denominator of the population base rate; drop
        them and every hit rate in the system becomes meaningless.
        """
        pools = (await self.market.universe(pages=pages))[: self.budget.max_pools]
        self.report.pools_examined = len(pools)

        for pool in pools:
            self.market.register_pool(pool.asset, pool.pool_address)
            candles = await self.market.pool_candles(pool.pool_address)
            if not candles:
                continue

            peak_volume = max((float(c.volume_usd) for c in candles), default=0.0)
            if peak_volume < MIN_PEAK_DAILY_VOLUME_USD:
                # Never tradable at any point, so it cannot have produced an
                # outcome anyone could have acted on.
                self.report.pools_never_tradable += 1
                continue

            recent_volume = float(candles[-1].volume_usd)
            if recent_volume < MIN_PEAK_DAILY_VOLUME_USD * 0.05:
                # Was tradable once, is dead now. Exactly the case a
                # present-day liquidity filter would have silently dropped.
                self.report.pools_dead_but_counted += 1

            self.report.pools_with_prices += 1
            self._register_asset(pool, candles[0].start)

            for outcome in self._detect(pool, candles):
                if self.registry.upsert(outcome):
                    key = outcome.outcome_class.value
                    self.report.outcomes_detected[key] = (
                        self.report.outcomes_detected.get(key, 0) + 1
                    )
        self.session.flush()
        return pools

    def _register_asset(self, pool: PoolRef, first_candle: datetime) -> None:
        from alphagraph.db.models import Asset

        asset_id = f"solana:{pool.token_address}"
        if self.session.get(Asset, asset_id) is not None:
            return
        self.session.add(
            Asset(
                id=asset_id,
                network="solana",
                address=pool.token_address,
                symbol=pool.symbol,
                decimals=9,
                first_seen_at=pool.created_at or first_candle,
            )
        )

    async def _ingest_address(
        self, address: str, start: datetime, end: datetime
    ) -> list[CanonicalEvent]:
        """Fetch and store one address's activity, respecting the request cap."""
        if self._helius_requests() >= self.budget.max_helius_requests:
            if "helius_request_cap" not in self.report.truncated:
                self.report.truncated.append("helius_request_cap")
            return []

        events: list[CanonicalEvent] = []
        async for event in self.chain.fetch_address_history(
            address, start=start, end=end, max_pages=self.budget.max_pages_per_address
        ):
            events.append(event)

        from alphagraph.ingestion.pipeline import IngestReport

        report = IngestReport()
        for event in events:
            self.ingestor.write(event, report)
            if event.event_type is EventType.UNKNOWN_INTERACTION:
                self.report.unknown_interactions += 1
        self.report.events_written += report.written
        self.session.flush()
        return events

    def _helius_requests(self) -> int:
        return int(self.chain.usage.get("requests", 0))

    async def pass_one_surface(self, as_of: datetime) -> set[str]:
        """Buyers active before confirmed outcomes. Suspects only.

        No rate computed from this pass alone means anything — every asset seen
        here is a winner by construction. Pass two supplies the denominator.
        """
        outcomes = self.registry.observable(as_of, list(PREDICTIVE_CLASSES))
        suspects: set[str] = set()

        for outcome in outcomes:
            pool = self._pool_for_outcome(outcome.asset_id)
            if pool is None:
                continue
            events = await self._ingest_address(pool, outcome.t0 - LOOKBACK, outcome.t0)
            self.report.outcome_windows_fetched += 1
            for event in events:
                if event.event_type is EventType.SWAP and event.side is Side.BUY:
                    suspects.add(event.actor)

        self.report.suspects_found = len(suspects)
        return suspects

    def _pool_for_outcome(self, asset_id: str) -> str | None:
        _, _, address = asset_id.partition(":")
        from alphagraph.core.addresses import Network
        from alphagraph.core.events import AssetRef

        return self.market.series_key_for(AssetRef(network=Network.SOLANA, address=address))

    async def pass_two_qualify(self, suspects: set[str], start: datetime, end: datetime) -> None:
        """Fetch each suspect's full history, so the denominator is honest.

        This is the expensive pass and the one that makes the numbers real. A
        wallet that bought one winner and forty losers looks identical to a
        genuine find until this runs.
        """
        ordered = sorted(suspects)[: self.budget.max_candidates_to_qualify]
        if len(suspects) > len(ordered):
            self.report.truncated.append(f"qualified {len(ordered)} of {len(suspects)} suspects")

        for wallet in ordered:
            await self._ingest_address(wallet, start, end)
            self.report.suspects_qualified += 1

    # ------------------------------------------------------------------- run

    async def run(self, pages: int = 5, window_days: int = 180) -> SweepReport:
        end = utcnow()
        start = end - timedelta(days=window_days)

        log.info("sweep: building universe")
        await self.build_universe(pages=pages)

        log.info("sweep: pass one, surfacing suspects")
        suspects = await self.pass_one_surface(end)

        log.info("sweep: pass two, qualifying %d suspects", len(suspects))
        await self.pass_two_qualify(suspects, start, end)

        log.info("sweep: scoring")
        engine = DiscoveryEngine(self.session)
        scores = engine.run_sweep(end)
        engine.persist(scores, end)
        self.report.candidates_passed = [s.wallet for s in scores if s.passed]

        from alphagraph.core.outcomes import OutcomeClass

        self.report.base_rate = self.registry.base_rate(OutcomeClass.PRICE_RUN, end)[0]
        self.report.provider_usage = {
            "helius": self.chain.usage,
            self.market.name: self.market.usage,
        }
        self.session.flush()
        return self.report
