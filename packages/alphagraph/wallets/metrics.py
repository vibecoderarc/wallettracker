"""Wallet behavioural metrics and archetype classification (spec §5).

This module is the discriminator between a real find and noise. It is
deliberately all arithmetic — no judgement, no model, no LLM — because every
number here has to be reproducible for a given (wallet, as_of, version).

The central insight it encodes: predictive skill and profitability are
different measurements. A wallet can be reliably early into assets that later
run while still losing money on every one of them. Ranking on P&L would discard
exactly the wallets worth watching.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.events import EventType, Side
from alphagraph.core.outcomes import PREDICTIVE_CLASSES, OutcomeClass
from alphagraph.db.models import Event, OutcomeRow

CALC_VERSION = "v1"

#: Prior strength for shrinkage. A wallet needs roughly this many observations
#: before its own rate outweighs the population base rate. Tuned so that
#: 3-for-3 does not outrank 9-for-12.
SHRINKAGE_PRIOR = 8.0


class Archetype:
    INFORMED_ACCUMULATOR = "informed_accumulator"
    LISTING_PREDICTOR = "listing_predictor"
    REVIVAL_SPECIALIST = "revival_specialist"
    PUMP_OPERATOR = "pump_operator"
    HIGH_FREQUENCY = "high_frequency"
    BOT = "bot"
    INFRASTRUCTURE = "infrastructure"
    UNCLASSIFIED = "unclassified"


#: Archetypes excluded from insider cohorts regardless of measured performance.
#: Their P&L often looks excellent; their predictive value to a human researcher
#: is zero, because "reacted in 40ms" is not information.
EXCLUDED_ARCHETYPES = frozenset({Archetype.BOT, Archetype.HIGH_FREQUENCY, Archetype.INFRASTRUCTURE})


@dataclass(frozen=True, slots=True)
class ExclusionRules:
    max_trades_per_month: float = 45.0
    max_distinct_tokens_per_month: float = 25.0
    #: Minimum plausible gap between a token existing and a human buying it.
    #: Anything faster is a sniper bot reacting programmatically to the launch.
    min_entry_latency_seconds: float = 30.0
    min_sniper_entries: int = 3
    max_active_hour_spread: int = 20  # distinct UTC hours seen; 24/7 → automation
    min_median_hold_hours: float = 2.0


@dataclass(frozen=True, slots=True)
class ArchetypeThresholds:
    quiet_max_trades_per_month: float = 6.0
    quiet_min_shrunk_hit_rate: float = 0.30
    quiet_min_edge_ratio: float = 2.0
    min_sample: int = 5
    listing_min_hit_rate: float = 0.5
    revival_min_hit_rate: float = 0.5
    pump_min_collapse_share: float = 0.6


@dataclass
class WalletMetricSet:
    wallet: str
    as_of: datetime
    calc_version: str = CALC_VERSION

    # Activity
    total_actions: int = 0
    acquisitions: int = 0
    distinct_tokens: int = 0
    active_days: int = 0
    trades_per_month: float = 0.0
    distinct_tokens_per_month: float = 0.0
    distinct_active_hours: int = 0
    #: Shortest observed gap between a token's creation and this wallet buying it.
    min_entry_latency_seconds: float | None = None
    sniper_entries: int = 0
    median_hold_hours: float | None = None

    # Precision, per predictive outcome class
    hits: dict[str, int] = field(default_factory=dict)
    hit_rate: dict[str, float] = field(default_factory=dict)
    shrunk_hit_rate: dict[str, float] = field(default_factory=dict)
    base_rate: dict[str, float] = field(default_factory=dict)
    edge_ratio: dict[str, float] = field(default_factory=dict)

    # Earliness / quietness
    median_lead_time_days: float | None = None
    median_buyer_rank_percentile: float | None = None

    # Conviction
    median_position_usd: float = 0.0
    max_position_usd: float = 0.0
    position_sizes_usd: list[float] = field(default_factory=list)

    # Separate from skill, on purpose.
    realized_pnl_usd: float = 0.0
    collapse_share: float = 0.0

    # Perp behaviour
    resting_orders: int = 0
    liquidations: int = 0

    archetype: str = Archetype.UNCLASSIFIED
    archetype_confidence: float = 0.0
    excluded_reason: str | None = None

    def as_dict(self) -> dict:
        data = asdict(self)
        data["as_of"] = self.as_of.isoformat()
        return data


def shrink(hits: int, trials: int, base_rate: float, prior: float = SHRINKAGE_PRIOR) -> float:
    """Empirical-Bayes shrinkage toward the population base rate.

    Without this, a wallet that is 3-for-3 shows a 100% hit rate and outranks
    everything. With it, three observations barely move off the base rate,
    which is the honest answer: three events tell you almost nothing.
    """
    if trials <= 0:
        return base_rate
    return (hits + prior * base_rate) / (trials + prior)


def wilson_interval(hits: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — the uncertainty shown next to every hit rate."""
    if trials == 0:
        return (0.0, 1.0)
    phat = hits / trials
    denom = 1 + z**2 / trials
    centre = (phat + z**2 / (2 * trials)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / trials + z**2 / (4 * trials**2)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _month_span(first: datetime, last: datetime) -> float:
    span_days = max((last - first).total_seconds() / 86400, 1.0)
    return max(span_days / 30.44, 1.0 / 30.44)


class WalletMetricsCalculator:
    """Computes point-in-time metrics for a wallet.

    Every read is bounded by `as_of` on `observed_at`, so the same call with the
    same arguments returns the same answer forever — the property backtests rely
    on and `tests/test_leakage.py` asserts.
    """

    def __init__(
        self,
        session: Session,
        *,
        exclusions: ExclusionRules | None = None,
        thresholds: ArchetypeThresholds | None = None,
    ) -> None:
        self.session = session
        self.exclusions = exclusions or ExclusionRules()
        self.thresholds = thresholds or ArchetypeThresholds()

    # -------------------------------------------------------------- internals

    def _events(self, wallet: str, as_of: datetime, lookback: timedelta | None) -> list[Event]:
        stmt = select(Event).where(Event.actor == wallet, Event.observed_at <= as_of)
        if lookback is not None:
            stmt = stmt.where(Event.observed_at >= as_of - lookback)
        return list(self.session.execute(stmt.order_by(Event.observed_at)).scalars())

    def _outcomes_by_asset(self, as_of: datetime) -> dict[str, list[OutcomeRow]]:
        rows = self.session.execute(
            select(OutcomeRow).where(OutcomeRow.first_observed_at <= as_of)
        ).scalars()
        grouped: dict[str, list[OutcomeRow]] = defaultdict(list)
        for row in rows:
            grouped[row.asset_id].append(row)
        return grouped

    def _base_rates(self, as_of: datetime) -> dict[str, float]:
        from alphagraph.outcomes.registry import OutcomeRegistry

        registry = OutcomeRegistry(self.session)
        return {
            cls.value: registry.base_rate(cls, as_of)[0]
            for cls in (*PREDICTIVE_CLASSES, OutcomeClass.RUG_OR_COLLAPSE)
        }

    # ------------------------------------------------------------------- main

    def compute(
        self, wallet: str, as_of: datetime, lookback: timedelta | None = None
    ) -> WalletMetricSet:
        events = self._events(wallet, as_of, lookback)
        metrics = WalletMetricSet(wallet=wallet, as_of=as_of)
        if not events:
            metrics.excluded_reason = "no_activity"
            return metrics

        outcomes = self._outcomes_by_asset(as_of)
        base_rates = self._base_rates(as_of)

        acquisitions = [
            e
            for e in events
            if (e.event_type == EventType.SWAP.value and e.side == Side.BUY.value)
            or e.event_type in {EventType.POSITION_OPENED.value, EventType.POSITION_INCREASED.value}
        ]
        metrics.total_actions = len(events)
        metrics.acquisitions = len(acquisitions)

        touched = {e.asset_id for e in acquisitions if e.asset_id}
        metrics.distinct_tokens = len(touched)

        first, last = events[0].observed_at, events[-1].observed_at
        months = _month_span(first, last)
        metrics.trades_per_month = len(events) / months
        metrics.distinct_tokens_per_month = len(touched) / months
        metrics.active_days = len({e.observed_at.date() for e in events})
        metrics.distinct_active_hours = len({e.observed_at.hour for e in events})

        self._score_entry_latency(metrics, acquisitions)

        sizes = [float(e.usd_value) for e in events if e.usd_value is not None]
        if sizes:
            metrics.position_sizes_usd = sizes
            metrics.median_position_usd = statistics.median(sizes)
            metrics.max_position_usd = max(sizes)

        metrics.resting_orders = sum(
            1 for e in events if e.event_type == EventType.ORDER_PLACED.value and not e.filled
        )
        metrics.liquidations = sum(1 for e in events if e.event_type == EventType.LIQUIDATION.value)

        metrics.median_hold_hours = self._median_hold_hours(events)
        metrics.realized_pnl_usd = self._realized_pnl(events)

        self._score_precision(metrics, acquisitions, touched, outcomes, base_rates, as_of)
        self._score_earliness(metrics, acquisitions, outcomes, as_of)
        self._classify(metrics)
        return metrics

    def _score_entry_latency(self, metrics: WalletMetricSet, acquisitions: list[Event]) -> None:
        """How soon after a token existed did this wallet buy it?

        Measured from the asset's first-seen chain time, NOT from our own
        observation lag. Observation lag describes our infrastructure; entry
        latency describes their behaviour, and only the second one says anything
        about whether a wallet is automated.
        """
        latencies: list[float] = []
        threshold = self.exclusions.min_entry_latency_seconds
        for event in acquisitions:
            if not event.asset_id:
                continue
            created = self._creation_time(event.asset_id)
            if created is None:
                # We never ingested this token's creation — it predates the
                # backfill window. Measuring latency against the earliest event
                # we happen to hold would understate it and manufacture false
                # sniper counts at every backfill boundary.
                continue
            latency = (event.chain_time - created).total_seconds()
            if latency < 0:
                continue
            latencies.append(latency)
            if latency <= threshold:
                metrics.sniper_entries += 1
        if latencies:
            metrics.min_entry_latency_seconds = min(latencies)

    def _creation_time(self, asset_id: str) -> datetime | None:
        """Chain time of the asset's observed creation event, if we have one."""
        return self.session.execute(
            select(Event.chain_time)
            .where(
                Event.asset_id == asset_id,
                Event.event_type == EventType.TOKEN_CREATED.value,
            )
            .order_by(Event.chain_time)
            .limit(1)
        ).scalar_one_or_none()

    def _median_hold_hours(self, events: list[Event]) -> float | None:
        opened: dict[str, datetime] = {}
        holds: list[float] = []
        for e in events:
            if not e.asset_id:
                continue
            if e.side == Side.BUY.value and e.asset_id not in opened:
                opened[e.asset_id] = e.observed_at
            elif e.side == Side.SELL.value and e.asset_id in opened:
                holds.append((e.observed_at - opened.pop(e.asset_id)).total_seconds() / 3600)
        return statistics.median(holds) if holds else None

    def _realized_pnl(self, events: list[Event]) -> float:
        """Crude realized P&L: sells minus buys on closed round trips.

        Deliberately crude, and deliberately kept away from the ranking. It is
        displayed for context, never used to promote or demote a wallet.
        """
        spent: dict[str, float] = defaultdict(float)
        pnl = 0.0
        for e in events:
            if not e.asset_id or e.usd_value is None:
                continue
            value = float(e.usd_value)
            if e.side == Side.BUY.value:
                spent[e.asset_id] += value
            elif e.side == Side.SELL.value and spent[e.asset_id] > 0:
                pnl += value - spent[e.asset_id]
                spent[e.asset_id] = 0.0
            elif e.event_type == EventType.LIQUIDATION.value:
                pnl -= value
        return pnl

    def _score_precision(
        self,
        metrics: WalletMetricSet,
        acquisitions: list[Event],
        touched: set[str],
        outcomes: dict[str, list[OutcomeRow]],
        base_rates: dict[str, float],
        as_of: datetime,
    ) -> None:
        """Hit rate per outcome class, counted per ASSET, not per trade.

        Per-trade counting would let a wallet that bought the same winner ten
        times look ten times as skilled.
        """
        first_touch: dict[str, datetime] = {}
        for e in acquisitions:
            if e.asset_id and e.asset_id not in first_touch:
                first_touch[e.asset_id] = e.observed_at

        for cls in (*PREDICTIVE_CLASSES, OutcomeClass.RUG_OR_COLLAPSE):
            hits = 0
            for asset_id in touched:
                entry = first_touch.get(asset_id)
                if entry is None:
                    continue
                for row in outcomes.get(asset_id, []):
                    if row.outcome_class != cls.value:
                        continue
                    # The wallet must have acted BEFORE the move became public.
                    # Buying after the pump is not prediction.
                    if entry < row.t0:
                        hits += 1
                        break
            trials = len(touched)
            base = base_rates.get(cls.value, 0.0)
            metrics.hits[cls.value] = hits
            metrics.hit_rate[cls.value] = hits / trials if trials else 0.0
            metrics.shrunk_hit_rate[cls.value] = shrink(hits, trials, base)
            metrics.base_rate[cls.value] = base
            metrics.edge_ratio[cls.value] = (
                metrics.shrunk_hit_rate[cls.value] / base if base > 0 else 0.0
            )

        collapse_hits = metrics.hits.get(OutcomeClass.RUG_OR_COLLAPSE.value, 0)
        metrics.collapse_share = collapse_hits / len(touched) if touched else 0.0

    def _score_earliness(
        self,
        metrics: WalletMetricSet,
        acquisitions: list[Event],
        outcomes: dict[str, list[OutcomeRow]],
        as_of: datetime,
    ) -> None:
        leads: list[float] = []
        ranks: list[float] = []
        for e in acquisitions:
            if not e.asset_id:
                continue
            relevant = [
                row
                for row in outcomes.get(e.asset_id, [])
                if row.outcome_class in {c.value for c in PREDICTIVE_CLASSES}
                and e.observed_at < row.t0
            ]
            if not relevant:
                continue
            t0 = min(row.t0 for row in relevant)
            leads.append((t0 - e.observed_at).total_seconds() / 86400)
            ranks.append(self._buyer_rank_percentile(e.asset_id, e.observed_at, t0))
        if leads:
            metrics.median_lead_time_days = statistics.median(leads)
        if ranks:
            metrics.median_buyer_rank_percentile = statistics.median(ranks)

    def _buyer_rank_percentile(self, asset_id: str, entry: datetime, t0: datetime) -> float:
        """Where this wallet sat among all pre-move buyers. 0 = first in."""
        rows = self.session.execute(
            select(Event.actor, Event.observed_at)
            .where(
                Event.asset_id == asset_id,
                Event.observed_at < t0,
                Event.side == Side.BUY.value,
            )
            .order_by(Event.observed_at)
        ).all()
        if not rows:
            return 1.0
        seen: dict[str, datetime] = {}
        for actor, observed in rows:
            seen.setdefault(actor, observed)
        ordered = sorted(seen.values())
        position = sum(1 for t in ordered if t < entry)
        return position / len(ordered)

    # --------------------------------------------------------------- classify

    def _classify(self, metrics: WalletMetricSet) -> None:
        ex = self.exclusions
        th = self.thresholds

        # Hard exclusions first. These win over any measured performance.
        # A sniper's hit rate can be excellent and is still worthless here:
        # "bought 1.2 seconds after the mint existed" is not information, it is
        # infrastructure, and no human researcher can act on it.
        if metrics.sniper_entries >= ex.min_sniper_entries:
            metrics.archetype = Archetype.BOT
            metrics.archetype_confidence = 0.95
            metrics.excluded_reason = (
                f"entered_within_{ex.min_entry_latency_seconds:.0f}s_of_launch_"
                f"{metrics.sniper_entries}_times"
            )
            return
        if (
            metrics.distinct_active_hours >= ex.max_active_hour_spread
            and metrics.total_actions > 50
        ):
            metrics.archetype = Archetype.BOT
            metrics.archetype_confidence = 0.8
            metrics.excluded_reason = "active_around_the_clock"
            return
        if (
            metrics.trades_per_month > ex.max_trades_per_month
            or metrics.distinct_tokens_per_month > ex.max_distinct_tokens_per_month
        ):
            metrics.archetype = Archetype.HIGH_FREQUENCY
            metrics.archetype_confidence = 0.85
            metrics.excluded_reason = f"churn_{metrics.trades_per_month:.1f}_trades_per_month"
            return

        listing = metrics.shrunk_hit_rate.get(OutcomeClass.CEX_LISTING.value, 0.0)
        listing_raw = metrics.hit_rate.get(OutcomeClass.CEX_LISTING.value, 0.0)
        revival_raw = metrics.hit_rate.get(OutcomeClass.REVIVAL.value, 0.0)
        run_edge = metrics.edge_ratio.get(OutcomeClass.PRICE_RUN.value, 0.0)
        run_shrunk = metrics.shrunk_hit_rate.get(OutcomeClass.PRICE_RUN.value, 0.0)

        if metrics.distinct_tokens < th.min_sample:
            metrics.archetype = Archetype.UNCLASSIFIED
            metrics.archetype_confidence = 0.0
            metrics.excluded_reason = f"sample_size_{metrics.distinct_tokens}_below_minimum"
            return

        # Pump operator: repeatedly present at pumps that then collapse.
        if metrics.collapse_share >= th.pump_min_collapse_share and metrics.distinct_tokens >= 3:
            metrics.archetype = Archetype.PUMP_OPERATOR
            metrics.archetype_confidence = min(0.95, metrics.collapse_share)
            return

        if (
            listing_raw >= th.listing_min_hit_rate
            and metrics.hits.get(OutcomeClass.CEX_LISTING.value, 0) >= 3
        ):
            metrics.archetype = Archetype.LISTING_PREDICTOR
            metrics.archetype_confidence = min(0.95, listing)
            return

        if (
            revival_raw >= th.revival_min_hit_rate
            and metrics.hits.get(OutcomeClass.REVIVAL.value, 0) >= 3
        ):
            metrics.archetype = Archetype.REVIVAL_SPECIALIST
            metrics.archetype_confidence = min(0.95, revival_raw)
            return

        if (
            metrics.trades_per_month <= th.quiet_max_trades_per_month
            and run_shrunk >= th.quiet_min_shrunk_hit_rate
            and run_edge >= th.quiet_min_edge_ratio
        ):
            metrics.archetype = Archetype.INFORMED_ACCUMULATOR
            metrics.archetype_confidence = min(0.9, run_shrunk)
            return

        metrics.archetype = Archetype.UNCLASSIFIED
        metrics.archetype_confidence = 0.0


def entity_relative_significance(
    metrics: WalletMetricSet,
    *,
    usd_value: float | None,
    event_type: str,
    days_since_last_action: float | None,
) -> tuple[float, list[str]]:
    """How unusual is this action FOR THIS WALLET (spec §5.4).

    The reason a $500 probe from a studied entity outranks a $1M buy from a
    stranger. v1 used absolute USD floors and would have discarded exactly the
    trade that opened the CASHCAT sequence.
    """
    reasons: list[str] = []
    score = 0.0

    if usd_value is not None and metrics.position_sizes_usd:
        below = sum(1 for s in metrics.position_sizes_usd if s <= usd_value)
        pct = below / len(metrics.position_sizes_usd)
        score += 0.35 * pct
        if pct >= 0.8:
            reasons.append(f"size in top {int((1 - pct) * 100) + 1}% of this wallet's history")

    if days_since_last_action is not None:
        # Action after a long silence is the informative case. A wallet that
        # trades constantly tells you nothing when it trades again.
        dormancy = min(days_since_last_action / 60.0, 1.0)
        score += 0.30 * dormancy
        if days_since_last_action >= 21:
            reasons.append(f"first action in {days_since_last_action:.0f} days")

    rare_types = {
        EventType.ORDER_PLACED.value: "placed a resting order below market",
        EventType.POSITION_OPENED.value: "opened a leveraged position",
        EventType.LIQUIDATION.value: "was liquidated",
    }
    if event_type in rare_types:
        score += 0.20
        reasons.append(rare_types[event_type])

    if metrics.trades_per_month <= 6:
        score += 0.15
        reasons.append(f"wallet acts rarely ({metrics.trades_per_month:.1f}/month)")

    return min(score, 1.0), reasons


def action_counter(events: list[Event]) -> Counter[str]:
    return Counter(e.event_type for e in events)
