"""Reverse discovery engine (spec §10).

Works backwards from outcomes to find wallets, because you cannot track a wallet
you have not found and the wallets worth finding do not announce themselves.

Given enough historical assets, some wallets look extraordinary purely by
chance. Most of this module is the guards against that, not the search itself:
base-rate comparison, independence testing, minimum samples, negative controls,
and a shadow period before anything is promoted. If those guards ever get
relaxed to "surface more candidates", the engine stops being useful and starts
being a random number generator with a good UI.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.outcomes import PREDICTIVE_CLASSES, OutcomeClass
from alphagraph.core.timeutil import days
from alphagraph.db.models import Candidate, Event, OutcomeRow
from alphagraph.outcomes.registry import OutcomeRegistry
from alphagraph.wallets.metrics import (
    EXCLUDED_ARCHETYPES,
    Archetype,
    WalletMetricsCalculator,
    shrink,
    wilson_interval,
)

#: Lookback windows before t0. A wallet appearing in the 30-day window is doing
#: something different from one appearing in the last hour.
LOOKBACK_WINDOWS = (days(1), days(7), days(30))


class CandidateStatus:
    SURFACED = "surfaced"
    CANDIDATE = "candidate"
    SHADOW = "shadow_watched"
    TRACKED = "tracked"
    DECAYING = "decaying"
    RETIRED = "retired"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class DiscoveryConfig:
    min_events: int = 4
    min_independent_events: int = 3
    min_edge_ratio: float = 1.8
    min_shrunk_hit_rate: float = 0.20
    #: Two appearances inside this window on correlated assets count as one.
    independence_window: timedelta = timedelta(days=21)
    shadow_period: timedelta = timedelta(days=30)
    max_candidates_per_sweep: int = 50


@dataclass
class Appearance:
    wallet: str
    outcome_id: str
    asset_id: str
    outcome_class: str
    t0: datetime
    entered_at: datetime
    lead_time_days: float
    window: str
    usd_value: float | None


@dataclass
class CandidateScore:
    wallet: str
    appearances: list[Appearance] = field(default_factory=list)
    total_touched: int = 0
    hits: int = 0
    hit_rate: float = 0.0
    shrunk_hit_rate: float = 0.0
    base_rate: float = 0.0
    edge_ratio: float = 0.0
    independent_events: int = 0
    confidence_interval: tuple[float, float] = (0.0, 1.0)
    archetype: str = Archetype.UNCLASSIFIED
    median_lead_days: float | None = None
    passed: bool = False
    rejection_reason: str | None = None

    def provenance(self) -> list[dict]:
        return [
            {
                "outcome_id": a.outcome_id,
                "asset_id": a.asset_id,
                "outcome_class": a.outcome_class,
                "t0": a.t0.isoformat(),
                "entered_at": a.entered_at.isoformat(),
                "lead_time_days": round(a.lead_time_days, 2),
                "window": a.window,
            }
            for a in self.appearances
        ]


class DiscoveryEngine:
    def __init__(
        self,
        session: Session,
        config: DiscoveryConfig | None = None,
        calculator: WalletMetricsCalculator | None = None,
    ) -> None:
        self.session = session
        self.config = config or DiscoveryConfig()
        self.calculator = calculator or WalletMetricsCalculator(session)
        self.registry = OutcomeRegistry(session)

    # ------------------------------------------------------------- step 1-5

    def sweep_outcome(self, outcome: OutcomeRow, as_of: datetime) -> list[Appearance]:
        """Enumerate everyone who acquired exposure before this outcome's t0."""
        appearances: list[Appearance] = []
        widest = max(LOOKBACK_WINDOWS)
        rows = self.session.execute(
            select(Event)
            .where(
                Event.asset_id == outcome.asset_id,
                Event.observed_at < outcome.t0,
                Event.observed_at >= outcome.t0 - widest,
                Event.side.in_(["buy", "long"]),
            )
            .order_by(Event.observed_at)
        ).scalars()

        first_by_wallet: dict[str, Event] = {}
        for event in rows:
            first_by_wallet.setdefault(event.actor, event)

        for wallet, event in first_by_wallet.items():
            delta = outcome.t0 - event.observed_at
            window = next(
                (f"{int(w.days)}d" for w in LOOKBACK_WINDOWS if delta <= w),
                f"{int(widest.days)}d",
            )
            appearances.append(
                Appearance(
                    wallet=wallet,
                    outcome_id=outcome.outcome_id,
                    asset_id=outcome.asset_id,
                    outcome_class=outcome.outcome_class,
                    t0=outcome.t0,
                    entered_at=event.observed_at,
                    lead_time_days=delta.total_seconds() / 86400,
                    window=window,
                    usd_value=float(event.usd_value) if event.usd_value is not None else None,
                )
            )
        return appearances

    # --------------------------------------------------------------- step 8

    def count_independent(self, appearances: list[Appearance]) -> int:
        """Collapse correlated appearances into independent observations.

        Ten entries during one frothy fortnight is one market condition observed
        ten times, not ten pieces of evidence. Without this a wallet that
        happened to be active in a single hot week looks like a serial predictor.
        """
        if not appearances:
            return 0
        ordered = sorted(appearances, key=lambda a: a.t0)
        clusters = 1
        anchor = ordered[0].t0
        seen_assets = {ordered[0].asset_id}
        for appearance in ordered[1:]:
            if appearance.asset_id in seen_assets:
                continue
            seen_assets.add(appearance.asset_id)
            if appearance.t0 - anchor > self.config.independence_window:
                clusters += 1
                anchor = appearance.t0
        return clusters

    # ---------------------------------------------------------------- step 6-11

    def score_wallet(
        self, wallet: str, appearances: list[Appearance], as_of: datetime
    ) -> CandidateScore:
        score = CandidateScore(wallet=wallet, appearances=appearances)

        metrics = self.calculator.compute(wallet, as_of)
        score.archetype = metrics.archetype

        touched = metrics.distinct_tokens
        score.total_touched = touched
        hit_assets = {a.asset_id for a in appearances}
        score.hits = len(hit_assets)
        score.hit_rate = score.hits / touched if touched else 0.0
        score.independent_events = self.count_independent(appearances)
        score.confidence_interval = wilson_interval(score.hits, max(touched, score.hits))

        leads = [a.lead_time_days for a in appearances]
        score.median_lead_days = statistics.median(leads) if leads else None

        # Blended base rate across the outcome classes this wallet actually hit.
        class_counts: dict[str, int] = defaultdict(int)
        for a in appearances:
            class_counts[a.outcome_class] += 1
        weighted = 0.0
        total = sum(class_counts.values()) or 1
        for cls_value, count in class_counts.items():
            rate, _, _ = self.registry.base_rate(OutcomeClass(cls_value), as_of)
            weighted += rate * (count / total)
        score.base_rate = weighted
        score.shrunk_hit_rate = shrink(score.hits, max(touched, score.hits), weighted)
        score.edge_ratio = score.shrunk_hit_rate / weighted if weighted > 0 else 0.0

        score.rejection_reason = self._reject_reason(score, metrics)
        score.passed = score.rejection_reason is None
        return score

    def _reject_reason(self, score: CandidateScore, metrics) -> str | None:
        cfg = self.config
        if metrics.archetype in EXCLUDED_ARCHETYPES:
            return f"excluded_archetype:{metrics.archetype}:{metrics.excluded_reason}"
        if score.hits < cfg.min_events:
            return f"too_few_events:{score.hits}<{cfg.min_events}"
        if score.independent_events < cfg.min_independent_events:
            return (
                f"insufficient_independence:{score.independent_events}<{cfg.min_independent_events}"
            )
        if score.edge_ratio < cfg.min_edge_ratio:
            return f"edge_vs_base_rate:{score.edge_ratio:.2f}<{cfg.min_edge_ratio}"
        if score.shrunk_hit_rate < cfg.min_shrunk_hit_rate:
            return f"shrunk_hit_rate:{score.shrunk_hit_rate:.3f}<{cfg.min_shrunk_hit_rate}"
        return None

    # ------------------------------------------------------------------ sweep

    def run_sweep(self, as_of: datetime, since: datetime | None = None) -> list[CandidateScore]:
        """Full backwards sweep over every observable outcome."""
        outcomes = self.registry.observable(as_of, list(PREDICTIVE_CLASSES))
        if since is not None:
            outcomes = [o for o in outcomes if o.first_observed_at >= since]

        by_wallet: dict[str, list[Appearance]] = defaultdict(list)
        for outcome in outcomes:
            for appearance in self.sweep_outcome(outcome, as_of):
                by_wallet[appearance.wallet].append(appearance)

        scores = [
            self.score_wallet(wallet, appearances, as_of)
            for wallet, appearances in by_wallet.items()
        ]
        scores.sort(key=lambda s: (s.passed, s.edge_ratio, s.independent_events), reverse=True)
        return scores

    def persist(self, scores: list[CandidateScore], as_of: datetime) -> dict[str, int]:
        counts = {"surfaced": 0, "updated": 0, "rejected": 0}
        # Rejected scores are still walked so an existing candidate that has
        # decayed gets its rejection reason recorded rather than silently
        # keeping yesterday's flattering numbers.
        for score in scores:
            existing = self.session.execute(
                select(Candidate).where(Candidate.wallet == score.wallet)
            ).scalar_one_or_none()

            status = CandidateStatus.SURFACED if score.passed else CandidateStatus.REJECTED
            if existing is None:
                if not score.passed:
                    counts["rejected"] += 1
                    continue
                self.session.add(
                    Candidate(
                        wallet=score.wallet,
                        network="solana",
                        status=status,
                        surfaced_at=as_of,
                        archetype=score.archetype,
                        hit_rate=score.hit_rate,
                        shrunk_hit_rate=score.shrunk_hit_rate,
                        base_rate=score.base_rate,
                        sample_size=score.total_touched,
                        independent_events=score.independent_events,
                        edge_vs_base=score.edge_ratio,
                        discovery_provenance=score.provenance(),
                        rejection_reason=score.rejection_reason,
                    )
                )
                counts["surfaced"] += 1
            else:
                existing.hit_rate = score.hit_rate
                existing.shrunk_hit_rate = score.shrunk_hit_rate
                existing.base_rate = score.base_rate
                existing.sample_size = score.total_touched
                existing.independent_events = score.independent_events
                existing.edge_vs_base = score.edge_ratio
                existing.archetype = score.archetype
                existing.discovery_provenance = score.provenance()
                existing.rejection_reason = score.rejection_reason
                counts["updated"] += 1
        self.session.flush()
        return counts

    # ------------------------------------------------------------- lifecycle

    def advance_lifecycle(self, now: datetime) -> dict[str, int]:
        """Move candidates through surfaced → shadow → tracked.

        The shadow period is the only real test. Everything before it is
        retrospective, and retrospective evidence is exactly what selection bias
        is made of.
        """
        moved = {"to_shadow": 0, "to_tracked": 0, "to_decaying": 0}
        candidates = list(self.session.execute(select(Candidate)).scalars())
        for candidate in candidates:
            if candidate.status == CandidateStatus.SURFACED:
                candidate.status = CandidateStatus.SHADOW
                candidate.shadow_started_at = now
                moved["to_shadow"] += 1
            elif candidate.status == CandidateStatus.SHADOW:
                started = candidate.shadow_started_at
                if started and now - started >= self.config.shadow_period:
                    if candidate.edge_vs_base >= self.config.min_edge_ratio:
                        candidate.status = CandidateStatus.TRACKED
                        candidate.promoted_at = now
                        moved["to_tracked"] += 1
                    else:
                        candidate.status = CandidateStatus.DECAYING
                        moved["to_decaying"] += 1
        self.session.flush()
        return moved


# ---------------------------------------------------------------- baselines


@dataclass
class BaselineResult:
    name: str
    surfaced: int
    median_edge_ratio: float
    note: str


def negative_controls(
    session: Session, engine: DiscoveryEngine, as_of: datetime, sample: int = 40
) -> list[BaselineResult]:
    """Run the same pipeline against wallets with no claim to skill.

    If the random baseline surfaces candidates at a similar rate to the real
    sweep, the pipeline is broken and its output means nothing. This is a
    correctness check on the engine, not a product feature.
    """
    all_wallets = list(session.execute(select(Event.actor).distinct().limit(500)).scalars())
    if not all_wallets:
        return []
    step = max(1, len(all_wallets) // sample)
    chosen = all_wallets[::step][:sample]

    edges: list[float] = []
    surfaced = 0
    for wallet in chosen:
        score = engine.score_wallet(wallet, [], as_of)
        edges.append(score.edge_ratio)
        if score.passed:
            surfaced += 1
    return [
        BaselineResult(
            name="random_wallet",
            surfaced=surfaced,
            median_edge_ratio=statistics.median(edges) if edges else 0.0,
            note=f"{len(chosen)} wallets sampled with no outcome appearances attributed",
        )
    ]
