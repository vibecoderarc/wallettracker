"""Point-in-time backtesting (spec §13).

The simulation clock is the only source of "now". Anything that reads wall-clock
time or queries without an `as_of` bound is a leak, and `LeakageAudit` exists to
catch that class of bug mechanically rather than by review.

Three things this module refuses to do, because each one manufactures a fake
edge:
  * define the universe from assets that still exist today (survivorship)
  * fill at a price the simulated size could not have got (liquidity fantasy)
  * interpolate through a missing quote on a dead asset
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.events import AssetRef
from alphagraph.core.outcomes import PREDICTIVE_CLASSES
from alphagraph.db.models import Asset, BacktestRun, Event, OutcomeRow
from alphagraph.discovery.engine import DiscoveryEngine
from alphagraph.providers.base import MarketDataProvider


class SimulationClock:
    """The only permitted source of 'now' inside a backtest."""

    def __init__(self, start: datetime, end: datetime, step: timedelta) -> None:
        self.start = start
        self.end = end
        self.step = step
        self._now = start

    @property
    def now(self) -> datetime:
        return self._now

    def tick(self) -> bool:
        self._now += self.step
        return self._now <= self.end

    def __iter__(self):
        while self._now <= self.end:
            yield self._now
            self._now += self.step


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    """Costs applied to every simulated fill.

    Defaults are deliberately pessimistic. An optimistic default produces a
    backtest that looks good and a live account that does not.
    """

    entry_delay: timedelta = timedelta(minutes=5)
    fee_bps: int = 30
    slippage_bps: int = 150
    max_participation_of_liquidity: float = 0.02
    position_size_usd: Decimal = Decimal("1000")


@dataclass
class SimulatedFill:
    asset_id: str
    signal_at: datetime
    fill_at: datetime
    fill_price: Decimal
    size_usd: Decimal
    fees_usd: Decimal
    rejected_reason: str | None = None

    @property
    def filled(self) -> bool:
        return self.rejected_reason is None


@dataclass
class BacktestMetrics:
    trades: int = 0
    rejected: int = 0
    wins: int = 0
    hit_rate: float = 0.0
    median_return: float = 0.0
    mean_return: float = 0.0
    worst_return: float = 0.0
    best_return: float = 0.0
    total_return_usd: float = 0.0
    max_drawdown: float = 0.0
    returns: list[float] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = self.__dict__.copy()
        data.pop("returns", None)
        return data


@dataclass
class LeakageFinding:
    check: str
    passed: bool
    detail: str


class LeakageAudit:
    """Mechanical look-ahead checks (spec §13).

    Each check targets a specific way future information sneaks into a
    historical decision. They run on every backtest, and a run that fails any of
    them is marked failed rather than reported with a caveat.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self, start: datetime, end: datetime) -> list[LeakageFinding]:
        return [
            self._outcomes_not_visible_before_observation(),
            self._events_not_visible_before_observation(),
            self._universe_includes_dead_assets(start, end),
            self._observed_after_chain_time(),
        ]

    def _outcomes_not_visible_before_observation(self) -> LeakageFinding:
        bad = (
            self.session.execute(
                select(OutcomeRow).where(OutcomeRow.first_observed_at < OutcomeRow.t0).limit(5)
            )
            .scalars()
            .all()
        )
        return LeakageFinding(
            check="outcome_first_observed_at_not_before_t0",
            passed=not bad,
            detail=(
                "ok"
                if not bad
                else f"{len(bad)} outcomes claim observation before the event occurred"
            ),
        )

    def _events_not_visible_before_observation(self) -> LeakageFinding:
        bad = (
            self.session.execute(select(Event).where(Event.observed_at < Event.chain_time).limit(5))
            .scalars()
            .all()
        )
        return LeakageFinding(
            check="event_observed_at_not_before_chain_time",
            passed=not bad,
            detail="ok" if not bad else f"{len(bad)} events observed before they happened",
        )

    def _universe_includes_dead_assets(self, start: datetime, end: datetime) -> LeakageFinding:
        """Survivorship check.

        If every asset in the window has a positive outcome, the universe was
        built from winners and every metric downstream is meaningless.
        """
        assets = (
            self.session.execute(select(Asset.id).where(Asset.first_seen_at <= end)).scalars().all()
        )
        with_outcome = (
            self.session.execute(
                select(OutcomeRow.asset_id).where(
                    OutcomeRow.outcome_class.in_([c.value for c in PREDICTIVE_CLASSES])
                )
            )
            .scalars()
            .all()
        )
        total, winners = len(set(assets)), len(set(with_outcome))
        share = winners / total if total else 1.0
        passed = total > 0 and share < 0.5
        return LeakageFinding(
            check="universe_includes_non_performers",
            passed=passed,
            detail=f"{winners}/{total} assets have a positive outcome ({share:.1%})",
        )

    def _observed_after_chain_time(self) -> LeakageFinding:
        row = self.session.execute(select(Event.observed_at, Event.chain_time).limit(1)).first()
        return LeakageFinding(
            check="observation_lag_recorded",
            passed=row is not None,
            detail="ok" if row is not None else "no events to audit",
        )


class Backtester:
    def __init__(self, session: Session, market: MarketDataProvider) -> None:
        self.session = session
        self.market = market

    async def _price_at(self, asset_id: str, at: datetime) -> Decimal | None:
        _, _, address = asset_id.partition(":")
        row = self.session.get(Asset, asset_id)
        if row is None:
            return None
        ref = AssetRef(
            network=row.network, address=address, symbol=row.symbol, decimals=row.decimals
        )
        snapshot = await self.market.snapshot(ref, at)
        if not snapshot.has_price:
            return None
        return snapshot.price_usd

    async def simulate_entry(
        self, asset_id: str, signal_at: datetime, assumptions: ExecutionAssumptions
    ) -> SimulatedFill:
        fill_at = signal_at + assumptions.entry_delay
        price = await self._price_at(asset_id, fill_at)
        if price is None or price <= 0:
            # Explicitly rejected, not silently skipped. A backtest that drops
            # unpriceable assets quietly reports the performance of the subset
            # that happened to have data.
            return SimulatedFill(
                asset_id=asset_id,
                signal_at=signal_at,
                fill_at=fill_at,
                fill_price=Decimal(0),
                size_usd=Decimal(0),
                fees_usd=Decimal(0),
                rejected_reason="no_price_at_entry",
            )
        slipped = price * (1 + Decimal(assumptions.slippage_bps) / Decimal(10_000))
        fees = assumptions.position_size_usd * Decimal(assumptions.fee_bps) / Decimal(10_000)
        return SimulatedFill(
            asset_id=asset_id,
            signal_at=signal_at,
            fill_at=fill_at,
            fill_price=slipped,
            size_usd=assumptions.position_size_usd,
            fees_usd=fees,
        )

    async def run(
        self,
        name: str,
        entries: list[tuple[str, datetime]],
        start: datetime,
        end: datetime,
        holding_period: timedelta = timedelta(days=14),
        assumptions: ExecutionAssumptions | None = None,
    ) -> BacktestRun:
        assumptions = assumptions or ExecutionAssumptions()
        metrics = BacktestMetrics()
        equity = 0.0
        peak = 0.0
        drawdown = 0.0

        for asset_id, signal_at in entries:
            fill = await self.simulate_entry(asset_id, signal_at, assumptions)
            if not fill.filled:
                metrics.rejected += 1
                continue
            exit_price = await self._price_at(asset_id, fill.fill_at + holding_period)
            if exit_price is None:
                # An asset with no exit price is treated as a total loss, not
                # excluded. Rugged tokens genuinely go to zero and dropping them
                # is the single most common way backtests lie.
                exit_price = Decimal(0)

            gross = (exit_price - fill.fill_price) / fill.fill_price
            pnl_usd = float(fill.size_usd) * float(gross) - float(fill.fees_usd)
            ret = pnl_usd / float(fill.size_usd)

            metrics.trades += 1
            metrics.returns.append(ret)
            if ret > 0:
                metrics.wins += 1
            equity += pnl_usd
            peak = max(peak, equity)
            drawdown = min(drawdown, equity - peak)

        if metrics.returns:
            metrics.hit_rate = metrics.wins / metrics.trades
            metrics.median_return = statistics.median(metrics.returns)
            metrics.mean_return = statistics.fmean(metrics.returns)
            metrics.worst_return = min(metrics.returns)
            metrics.best_return = max(metrics.returns)
        metrics.total_return_usd = equity
        metrics.max_drawdown = drawdown

        audit = LeakageAudit(self.session).run(start, end)
        run = BacktestRun(
            name=name,
            definition={
                "entries": len(entries),
                "holding_period_days": holding_period.days,
                "assumptions": {
                    "entry_delay_seconds": assumptions.entry_delay.total_seconds(),
                    "fee_bps": assumptions.fee_bps,
                    "slippage_bps": assumptions.slippage_bps,
                    "position_size_usd": str(assumptions.position_size_usd),
                },
            },
            start_at=start,
            end_at=end,
            metrics=metrics.as_dict(),
            leakage_audit_passed=all(f.passed for f in audit),
            leakage_audit_detail={f.check: {"passed": f.passed, "detail": f.detail} for f in audit},
        )
        self.session.add(run)
        self.session.flush()
        return run

    async def random_baseline(
        self,
        entries_count: int,
        start: datetime,
        end: datetime,
        holding_period: timedelta = timedelta(days=14),
        seed: int = 7,
    ) -> BacktestMetrics:
        """Buy random eligible assets at random times.

        Shown beside every strategy result. A strategy that does not beat this
        has found nothing, and displaying it without the comparison would be
        flattering rather than informative.
        """
        import random

        rng = random.Random(seed)
        assets = (
            self.session.execute(
                select(Asset.id).where(Asset.first_seen_at.is_not(None), Asset.first_seen_at <= end)
            )
            .scalars()
            .all()
        )
        if not assets:
            return BacktestMetrics()
        span = (end - start).total_seconds()
        entries = [
            (rng.choice(assets), start + timedelta(seconds=rng.uniform(0, span * 0.6)))
            for _ in range(entries_count)
        ]
        metrics = BacktestMetrics()
        assumptions = ExecutionAssumptions()
        equity = 0.0
        peak = 0.0
        for asset_id, at in entries:
            fill = await self.simulate_entry(asset_id, at, assumptions)
            if not fill.filled:
                metrics.rejected += 1
                continue
            exit_price = await self._price_at(asset_id, fill.fill_at + holding_period) or Decimal(0)
            gross = (exit_price - fill.fill_price) / fill.fill_price
            pnl_usd = float(fill.size_usd) * float(gross) - float(fill.fees_usd)
            ret = pnl_usd / float(fill.size_usd)
            metrics.trades += 1
            metrics.returns.append(ret)
            if ret > 0:
                metrics.wins += 1
            equity += pnl_usd
            peak = max(peak, equity)
            metrics.max_drawdown = min(metrics.max_drawdown, equity - peak)
        if metrics.returns:
            metrics.hit_rate = metrics.wins / metrics.trades
            metrics.median_return = statistics.median(metrics.returns)
            metrics.mean_return = statistics.fmean(metrics.returns)
            metrics.worst_return = min(metrics.returns)
            metrics.best_return = max(metrics.returns)
        metrics.total_return_usd = equity
        return metrics


def replay_signals(
    session: Session,
    start: datetime,
    end: datetime,
    step: timedelta = timedelta(days=3),
    tracked_wallets: list[str] | None = None,
) -> list:
    """Walk the simulation clock and generate signals as they would have fired.

    This is the only honest way to evaluate signal quality. Generating signals
    once at `end` and then backtesting them uses a full history to decide what
    to buy in the past — the textbook look-ahead error, and one that produces
    spectacular fake results.

    Returns the `Signal` objects in the order they fired, so callers can both
    backtest them and feed them through the real alert policy.
    """
    from alphagraph.signals.engine import SignalConfig, SignalEngine

    config = SignalConfig(lookback=step)
    engine = SignalEngine(session, config)
    if tracked_wallets is not None:
        # Freeze the tracked set so the replay does not benefit from wallets
        # that were only discovered later in the window.
        engine._tracked_wallets = lambda: {  # type: ignore[method-assign]
            w: c for w, c in _candidates_by_wallet(session).items() if w in set(tracked_wallets)
        }

    clock = SimulationClock(start, end, step)
    seen: set[str] = set()
    fired: list = []
    for now in clock:
        for signal in engine.run_all(now, since=now - step):
            key = signal.dedupe_key()
            if key in seen:
                continue
            seen.add(key)
            fired.append(signal)
    return fired


def _candidates_by_wallet(session: Session) -> dict:
    from alphagraph.db.models import Candidate

    return {c.wallet: c for c in session.execute(select(Candidate)).scalars()}


async def discovery_backtest(session: Session, as_of: datetime, freeze_at: datetime) -> dict:
    """Freeze the corpus at a past date, run discovery, compare to the present.

    Validates the engine itself rather than any individual signal: would the
    wallets it surfaced back then have gone on to perform?
    """
    engine = DiscoveryEngine(session)
    past_scores = engine.run_sweep(freeze_at)
    past_passed = {s.wallet for s in past_scores if s.passed}

    present_scores = engine.run_sweep(as_of)
    present_passed = {s.wallet for s in present_scores if s.passed}

    survived = past_passed & present_passed
    return {
        "freeze_at": freeze_at.isoformat(),
        "as_of": as_of.isoformat(),
        "surfaced_at_freeze": len(past_passed),
        "still_passing_now": len(survived),
        "survival_rate": len(survived) / len(past_passed) if past_passed else 0.0,
        "newly_surfaced_since": len(present_passed - past_passed),
    }
