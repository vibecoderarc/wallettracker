"""Outcome registry and detectors (spec §3).

These are the labels. Every wallet metric, discovery claim, and backtest result
is measured against them, so their definitions are versioned and their
observation timestamps are kept honest.

The universe deliberately includes assets where nothing happened. Computing hit
rates against survivors only is the fastest way to manufacture a fake edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.events import AssetRef
from alphagraph.core.outcomes import Outcome, OutcomeClass
from alphagraph.core.timeutil import days, ensure_utc
from alphagraph.db.models import Asset, OutcomeRow
from alphagraph.providers.base import Candle, MarketDataProvider

DEFINITION_VERSION = "v1"

#: Volume thresholds are PER CANDLE, so they depend on the candle size. The
#: defaults below are tuned for hourly data. Live sweeps use daily candles —
#: one request covers months instead of days — and a day's volume is orders of
#: magnitude larger than an hour's, so the hourly floors would wave through
#: illiquid noise. `daily_configs()` supplies the scaled versions.
HOURS_PER_DAY = 24

#: How long after t0 the system is assumed to have confirmed the outcome. A
#: price run is only knowable after the window closes, so the registry must not
#: pretend it was observable at t0 itself.
CONFIRMATION_LAG = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class PriceRunConfig:
    multiple: Decimal = Decimal("3")
    horizon: timedelta = timedelta(days=30)
    baseline_window: timedelta = timedelta(days=7)
    min_baseline_volume_usd: Decimal = Decimal("500")


@dataclass(frozen=True, slots=True)
class RevivalConfig:
    dormant_days: int = 45
    dormant_max_avg_volume_usd: Decimal = Decimal("500")
    wake_multiple: Decimal = Decimal("4")
    wake_window: timedelta = timedelta(days=14)
    #: The dormancy window ends this far BEFORE the wake, leaving room for a
    #: quiet accumulation phase. Measuring dormancy right up to the move would
    #: disqualify exactly the assets being accumulated in silence — the case
    #: this detector exists to catch.
    accumulation_gap_days: int = 32


@dataclass(frozen=True, slots=True)
class CollapseConfig:
    drawdown_from_peak: Decimal = Decimal("0.85")
    window: timedelta = timedelta(days=14)


def _outcome_id(cls: OutcomeClass, asset: AssetRef, t0: datetime) -> str:
    return f"out_{cls.value}_{asset.address[:10]}_{int(t0.timestamp())}"


def detect_price_run(
    asset: AssetRef, candles: list[Candle], config: PriceRunConfig | None = None
) -> Outcome | None:
    """First point at which price reached `multiple` times its trailing baseline."""
    cfg = config or PriceRunConfig()
    if len(candles) < 10:
        return None

    for i, candle in enumerate(candles):
        window_start = candle.start - cfg.baseline_window
        baseline_slice = [c for c in candles[:i] if c.start >= window_start]
        if len(baseline_slice) < 4:
            continue
        baseline = min(c.close for c in baseline_slice)
        if baseline <= 0:
            continue
        avg_vol = sum((c.volume_usd for c in baseline_slice), Decimal(0)) / len(baseline_slice)
        if avg_vol < cfg.min_baseline_volume_usd:
            # Illiquid noise can print a 10x on a $12 trade. Requiring a real
            # baseline stops the registry filling with untradeable artifacts.
            continue
        if candle.close / baseline >= cfg.multiple:
            t0 = ensure_utc(candle.start)
            return Outcome(
                outcome_id=_outcome_id(OutcomeClass.PRICE_RUN, asset, t0),
                outcome_class=OutcomeClass.PRICE_RUN,
                definition_version=DEFINITION_VERSION,
                asset=asset,
                event_time=t0,
                first_observed_at=t0 + CONFIRMATION_LAG,
                t0=t0,
                magnitude=(candle.close / baseline).quantize(Decimal("0.001")),
                evidence={"baseline": str(baseline), "peak": str(candle.close)},
            )
    return None


def detect_revival(
    asset: AssetRef, candles: list[Candle], config: RevivalConfig | None = None
) -> Outcome | None:
    """A dead asset waking up — the "died then randomly pumped" case."""
    cfg = config or RevivalConfig()
    if len(candles) < 20:
        return None

    for i, candle in enumerate(candles):
        dormant_end = candle.start - days(cfg.accumulation_gap_days)
        dormant_start = dormant_end - days(cfg.dormant_days)
        dormant = [c for c in candles[:i] if dormant_start <= c.start < dormant_end]
        if len(dormant) < 8:
            continue
        avg_vol = sum((c.volume_usd for c in dormant), Decimal(0)) / len(dormant)
        if avg_vol > cfg.dormant_max_avg_volume_usd:
            continue
        dormant_price = max(c.close for c in dormant)
        if dormant_price <= 0:
            continue
        forward = [c for c in candles[i:] if c.start <= candle.start + cfg.wake_window]
        if not forward:
            continue
        peak = max(c.close for c in forward)
        if peak / dormant_price >= cfg.wake_multiple:
            t0 = ensure_utc(candle.start)
            return Outcome(
                outcome_id=_outcome_id(OutcomeClass.REVIVAL, asset, t0),
                outcome_class=OutcomeClass.REVIVAL,
                definition_version=DEFINITION_VERSION,
                asset=asset,
                event_time=t0,
                first_observed_at=t0 + CONFIRMATION_LAG,
                t0=t0,
                magnitude=(peak / dormant_price).quantize(Decimal("0.001")),
                evidence={"dormant_days": str(cfg.dormant_days), "dormant_avg_vol": str(avg_vol)},
            )
    return None


def detect_collapse(
    asset: AssetRef, candles: list[Candle], config: CollapseConfig | None = None
) -> Outcome | None:
    """Terminal drawdown from peak.

    Required, not optional. A wallet that is early into things that pump and
    then die is a different animal from one whose picks survive, and without
    this class the two are indistinguishable.
    """
    cfg = config or CollapseConfig()
    if len(candles) < 10:
        return None
    peak = Decimal(0)
    peak_at: datetime | None = None
    for candle in candles:
        if candle.close > peak:
            peak, peak_at = candle.close, candle.start
        if peak > 0 and peak_at is not None and candle.start > peak_at:
            drop = (peak - candle.close) / peak
            if drop >= cfg.drawdown_from_peak:
                t0 = ensure_utc(candle.start)
                return Outcome(
                    outcome_id=_outcome_id(OutcomeClass.RUG_OR_COLLAPSE, asset, t0),
                    outcome_class=OutcomeClass.RUG_OR_COLLAPSE,
                    definition_version=DEFINITION_VERSION,
                    asset=asset,
                    event_time=t0,
                    first_observed_at=t0 + CONFIRMATION_LAG,
                    t0=t0,
                    magnitude=drop.quantize(Decimal("0.001")),
                    evidence={"peak": str(peak)},
                )
    return None


def detect_pump_event(
    asset: AssetRef,
    candles: list[Candle],
    run: Outcome | None,
    collapse: Outcome | None,
    max_gap: timedelta = timedelta(days=14),
) -> Outcome | None:
    """A run that collapsed shortly after — the coordinated-promotion shape.

    Composed from the run and collapse detectors rather than detected
    independently, because "went up then went to zero quickly" is exactly what
    distinguishes a pump from a durable move, and reusing the two primitives
    keeps the definitions consistent.
    """
    if run is None or collapse is None:
        return None
    gap = collapse.t0 - run.t0
    if gap <= timedelta(0) or gap > max_gap:
        return None
    return Outcome(
        outcome_id=_outcome_id(OutcomeClass.PUMP_EVENT, asset, run.t0),
        outcome_class=OutcomeClass.PUMP_EVENT,
        definition_version=DEFINITION_VERSION,
        asset=asset,
        event_time=run.t0,
        first_observed_at=collapse.first_observed_at,
        t0=run.t0,
        magnitude=run.magnitude,
        evidence={
            "run_outcome_id": run.outcome_id,
            "collapse_outcome_id": collapse.outcome_id,
            "hours_to_collapse": str(round(gap.total_seconds() / 3600, 1)),
        },
    )


def daily_configs() -> tuple[PriceRunConfig, RevivalConfig, CollapseConfig]:
    """Detector settings for daily candles.

    Volume floors are raised roughly in proportion to the candle size, so a
    "10x" on a pool doing a few hundred dollars a day is rejected the same way
    it would be on hourly data. Without this the live universe fills with dust
    that technically multiplied but that nobody could have traded.
    """
    return (
        PriceRunConfig(
            multiple=Decimal("3"),
            horizon=timedelta(days=30),
            baseline_window=timedelta(days=7),
            min_baseline_volume_usd=Decimal("25000"),
        ),
        RevivalConfig(
            dormant_days=45,
            dormant_max_avg_volume_usd=Decimal("10000"),
            wake_multiple=Decimal("4"),
            wake_window=timedelta(days=14),
            accumulation_gap_days=32,
        ),
        CollapseConfig(drawdown_from_peak=Decimal("0.85"), window=timedelta(days=14)),
    )


class OutcomeRegistry:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, outcome: Outcome) -> bool:
        """Insert if new. Returns True when written.

        Outcomes are immutable once recorded — re-detecting the same event must
        not move its `first_observed_at` forward, or point-in-time queries would
        drift every time the detector runs.
        """
        existing = self.session.get(OutcomeRow, outcome.outcome_id)
        if existing is not None:
            return False
        self.session.add(
            OutcomeRow(
                outcome_id=outcome.outcome_id,
                outcome_class=outcome.outcome_class.value,
                definition_version=outcome.definition_version,
                asset_id=f"{outcome.asset.network.value}:{outcome.asset.address}",
                event_time=outcome.event_time,
                first_observed_at=outcome.first_observed_at,
                t0=outcome.t0,
                magnitude=outcome.magnitude,
                venue=outcome.venue,
                evidence=outcome.evidence,
            )
        )
        return True

    async def detect_all(
        self, market: MarketDataProvider, assets: list[AssetRef]
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        wide_start = datetime(2000, 1, 1, tzinfo=ensure_utc(datetime.now().astimezone()).tzinfo)
        wide_end = datetime(2100, 1, 1, tzinfo=wide_start.tzinfo)
        for asset in assets:
            result = await market.candles(asset, wide_start, wide_end)
            if not isinstance(result, list) or not result:
                continue
            run = detect_price_run(asset, result)
            revival = detect_revival(asset, result)
            collapse = detect_collapse(asset, result)
            pump = detect_pump_event(asset, result, run, collapse)
            for outcome in (run, revival, collapse, pump):
                if outcome and self.upsert(outcome):
                    counts[outcome.outcome_class.value] = (
                        counts.get(outcome.outcome_class.value, 0) + 1
                    )
        self.session.flush()
        return counts

    def observable(
        self, as_of: datetime, classes: list[OutcomeClass] | None = None
    ) -> list[OutcomeRow]:
        """Outcomes the system could have known about at `as_of`."""
        stmt = select(OutcomeRow).where(OutcomeRow.first_observed_at <= as_of)
        if classes:
            stmt = stmt.where(OutcomeRow.outcome_class.in_([c.value for c in classes]))
        return list(self.session.execute(stmt.order_by(OutcomeRow.t0)).scalars())

    def base_rate(self, outcome_class: OutcomeClass, as_of: datetime) -> tuple[float, int, int]:
        """Population base rate: what fraction of all known assets hit this class.

        Displayed next to every wallet hit rate. If 5% of assets 3x and a wallet
        hits 8% over 12 events, that wallet has found nothing, and the UI must
        make that obvious rather than celebrating an 8% hit rate.
        """
        total_assets = self.session.execute(
            select(Asset.id).where(Asset.first_seen_at <= as_of)
        ).scalars()
        total = len(set(total_assets))
        if total == 0:
            return 0.0, 0, 0
        hit_assets = self.session.execute(
            select(OutcomeRow.asset_id).where(
                OutcomeRow.outcome_class == outcome_class.value,
                OutcomeRow.first_observed_at <= as_of,
            )
        ).scalars()
        hits = len(set(hit_assets))
        return hits / total, hits, total
