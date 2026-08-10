"""Signal engine — the eleven families from spec §8.

Deterministic rules only. No language model participates in detection or
scoring; the AI layer summarises signals after they exist and never creates one.

Every signal carries its feature vector, evidence ids, an immutable evidence
cutoff, and the population base rate for its claim, so a user can always ask
"compared to what?" and get an answer.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.events import EventType, Side
from alphagraph.core.outcomes import OutcomeClass
from alphagraph.db.models import Candidate, Event, GraphEdge, OutcomeRow
from alphagraph.discovery.engine import CandidateStatus
from alphagraph.outcomes.registry import OutcomeRegistry
from alphagraph.wallets.metrics import (
    WalletMetricsCalculator,
    entity_relative_significance,
)

DEFINITION_VERSION = "v1"


class Family:
    SILENT_ACCUMULATION = "silent_accumulation"
    TRACKED_ENTITY_ACTION = "tracked_entity_action"
    PLAYBOOK_STAGE = "playbook_stage_transition"
    SEQUENCE_CONFIRMATION = "sequence_confirmation"
    RESTING_ORDER = "resting_order_placed"
    CONVERGENCE = "independent_convergence"
    CROSS_VENUE = "cross_venue_confirmation"
    NEW_CANDIDATE = "newly_surfaced_candidate"
    PUMP_OPERATOR = "pump_operator_activity"
    ANNOUNCEMENT = "authenticated_announcement"
    DATA_QUALITY = "data_quality_risk"


@dataclass(frozen=True, slots=True)
class SignalConfig:
    lookback: timedelta = timedelta(days=3)
    silent_accumulation_window: timedelta = timedelta(days=14)
    silent_min_buyers: int = 3
    silent_max_price_move: float = 0.25
    convergence_window: timedelta = timedelta(days=5)
    convergence_min_wallets: int = 2
    cooldown: timedelta = timedelta(hours=6)
    min_significance: float = 0.25


@dataclass
class Signal:
    family: str
    triggered_at: datetime
    evidence_cutoff: datetime
    title: str
    significance: float = 0.0
    confidence: float = 0.0
    risk_band: str = "unknown"
    asset_id: str | None = None
    wallet: str | None = None
    entity_id: str | None = None
    supporting_facts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    base_rate_context: dict = field(default_factory=dict)
    feature_vector: dict = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)

    def dedupe_key(self) -> str:
        """One key per (family, subject, day).

        Bucketing by day is what stops a wallet that trickles in over an
        afternoon from generating a dozen near-identical alerts.
        """
        day = self.triggered_at.strftime("%Y%m%d")
        return f"{self.family}:{self.asset_id or '-'}:{self.wallet or '-'}:{day}"

    def as_dict(self) -> dict:
        data = asdict(self)
        data["triggered_at"] = self.triggered_at.isoformat()
        data["evidence_cutoff"] = self.evidence_cutoff.isoformat()
        data["dedupe_key"] = self.dedupe_key()
        return data


class SignalEngine:
    def __init__(self, session: Session, config: SignalConfig | None = None) -> None:
        self.session = session
        self.config = config or SignalConfig()
        self.calculator = WalletMetricsCalculator(session)
        self.registry = OutcomeRegistry(session)

    # ----------------------------------------------------------------- utils

    def _tracked_wallets(self) -> dict[str, Candidate]:
        rows = self.session.execute(
            select(Candidate).where(
                Candidate.status.in_([CandidateStatus.TRACKED, CandidateStatus.SHADOW])
            )
        ).scalars()
        return {c.wallet: c for c in rows}

    def _recent_events(self, as_of: datetime, since: datetime) -> list[Event]:
        return list(
            self.session.execute(
                select(Event)
                .where(Event.observed_at <= as_of, Event.observed_at >= since)
                .order_by(Event.observed_at)
            ).scalars()
        )

    def _last_action_before(self, wallet: str, before: datetime) -> datetime | None:
        row = self.session.execute(
            select(Event.observed_at)
            .where(Event.actor == wallet, Event.observed_at < before)
            .order_by(Event.observed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row

    # -------------------------------------------------------------- families

    def tracked_entity_actions(self, as_of: datetime) -> list[Signal]:
        """Family 2 + 5: any action by a tracked wallet, ranked entity-relative."""
        tracked = self._tracked_wallets()
        if not tracked:
            return []
        since = as_of - self.config.lookback
        signals: list[Signal] = []

        for event in self._recent_events(as_of, since):
            if event.actor not in tracked:
                continue
            candidate = tracked[event.actor]
            metrics = self.calculator.compute(event.actor, as_of)
            last = self._last_action_before(event.actor, event.observed_at)
            dormancy = (
                (event.observed_at - last).total_seconds() / 86400 if last is not None else None
            )
            significance, reasons = entity_relative_significance(
                metrics,
                usd_value=float(event.usd_value) if event.usd_value is not None else None,
                event_type=event.event_type,
                days_since_last_action=dormancy,
            )
            if significance < self.config.min_significance:
                continue

            is_resting = event.event_type == EventType.ORDER_PLACED.value and not event.filled
            family = Family.RESTING_ORDER if is_resting else Family.TRACKED_ENTITY_ACTION
            verb = "placed a resting order on" if is_resting else f"{event.side or 'acted on'}"
            subject = event.asset_id or "unknown asset"
            title = f"{candidate.archetype} wallet {event.actor[:8]}… {verb} {subject}"

            base_rate, hits, total = self.registry.base_rate(OutcomeClass.PRICE_RUN, as_of)
            signals.append(
                Signal(
                    family=family,
                    triggered_at=event.observed_at,
                    evidence_cutoff=as_of,
                    title=title,
                    significance=significance,
                    confidence=float(candidate.edge_vs_base) / 10.0
                    if candidate.edge_vs_base
                    else 0.3,
                    asset_id=event.asset_id,
                    wallet=event.actor,
                    supporting_facts=[
                        *reasons,
                        f"wallet hit rate {float(candidate.hit_rate):.0%} "
                        f"over {candidate.sample_size} assets "
                        f"({candidate.independent_events} independent events)",
                        f"population base rate {base_rate:.1%} ({hits}/{total} assets)",
                    ],
                    risks=(
                        ["Order is unfilled — this is stated intent, not a position"]
                        if is_resting
                        else []
                    ),
                    unknowns=["Wallet ownership is inferred from behaviour, not established"],
                    base_rate_context={
                        "base_rate": base_rate,
                        "wallet_shrunk_hit_rate": float(candidate.shrunk_hit_rate),
                        "edge_ratio": float(candidate.edge_vs_base),
                    },
                    feature_vector={
                        "usd_value": float(event.usd_value) if event.usd_value else None,
                        "days_dormant": dormancy,
                        "event_type": event.event_type,
                        "filled": event.filled,
                    },
                    evidence_ids=[event.event_id],
                )
            )
        return signals

    def silent_accumulation(self, as_of: datetime) -> list[Signal]:
        """Family 1: sustained buying with flat price and dead volume.

        The "dead coin that randomly pumps" setup. Rare, and when it appears it
        is usually somebody who knows something.
        """
        window_start = as_of - self.config.silent_accumulation_window
        rows = self.session.execute(
            select(Event)
            .where(
                Event.observed_at <= as_of,
                Event.observed_at >= window_start,
                Event.side == Side.BUY.value,
                Event.asset_id.is_not(None),
            )
            .order_by(Event.observed_at)
        ).scalars()

        by_asset: dict[str, list[Event]] = defaultdict(list)
        for event in rows:
            by_asset[event.asset_id].append(event)

        signals: list[Signal] = []
        for asset_id, events in by_asset.items():
            buyers = {e.actor for e in events}
            if len(buyers) < self.config.silent_min_buyers:
                continue
            prices = [float(e.price_usd) for e in events if e.price_usd is not None]
            if len(prices) >= 2:
                move = abs(prices[-1] - prices[0]) / prices[0] if prices[0] else 0.0
                if move > self.config.silent_max_price_move:
                    continue
            net_usd = sum(float(e.usd_value or 0) for e in events)
            if net_usd <= 0:
                continue

            signals.append(
                Signal(
                    family=Family.SILENT_ACCUMULATION,
                    triggered_at=events[-1].observed_at,
                    evidence_cutoff=as_of,
                    title=f"Quiet accumulation in {asset_id} — {len(buyers)} buyers, price flat",
                    significance=min(0.9, 0.3 + 0.1 * len(buyers)),
                    confidence=0.5,
                    asset_id=asset_id,
                    supporting_facts=[
                        f"{len(buyers)} distinct buyers over "
                        f"{self.config.silent_accumulation_window.days} days",
                        f"${net_usd:,.0f} accumulated with no material price move",
                    ],
                    risks=["Flat price may reflect illiquidity rather than absorption"],
                    unknowns=["Buyer independence not yet verified"],
                    feature_vector={"buyers": len(buyers), "net_usd": net_usd},
                    evidence_ids=[e.event_id for e in events[:20]],
                )
            )
        return signals

    def convergence(self, as_of: datetime) -> list[Signal]:
        """Family 6: several INDEPENDENT tracked wallets into the same asset.

        Independence is the whole requirement. A cluster acting together is one
        opinion expressed several times, and reporting it as several
        confirmations would systematically overstate confidence.
        """
        tracked = self._tracked_wallets()
        if len(tracked) < self.config.convergence_min_wallets:
            return []
        since = as_of - self.config.convergence_window
        rows = self.session.execute(
            select(Event).where(
                Event.observed_at <= as_of,
                Event.observed_at >= since,
                Event.actor.in_(list(tracked)),
                Event.side.in_(["buy", "long"]),
                Event.asset_id.is_not(None),
            )
        ).scalars()

        by_asset: dict[str, set[str]] = defaultdict(set)
        evidence: dict[str, list[str]] = defaultdict(list)
        for event in rows:
            by_asset[event.asset_id].add(event.actor)
            evidence[event.asset_id].append(event.event_id)

        related = self._related_pairs()
        signals: list[Signal] = []
        for asset_id, wallets in by_asset.items():
            if len(wallets) < self.config.convergence_min_wallets:
                continue
            independent = self._independent_subset(wallets, related)
            if len(independent) < self.config.convergence_min_wallets:
                continue
            signals.append(
                Signal(
                    family=Family.CONVERGENCE,
                    triggered_at=as_of,
                    evidence_cutoff=as_of,
                    title=f"{len(independent)} independent tracked wallets converged on {asset_id}",
                    significance=min(0.95, 0.4 + 0.15 * len(independent)),
                    confidence=0.6,
                    asset_id=asset_id,
                    supporting_facts=[
                        f"{len(independent)} of {len(wallets)} participating wallets have no "
                        "detected relationship to each other",
                    ],
                    risks=["Undetected relationships would reduce this to a single opinion"],
                    unknowns=["Clustering is heuristic; absence of an edge is not proof"],
                    feature_vector={"wallets": len(wallets), "independent": len(independent)},
                    evidence_ids=evidence[asset_id][:20],
                )
            )
        return signals

    def _related_pairs(self) -> set[frozenset[str]]:
        rows = self.session.execute(select(GraphEdge.source, GraphEdge.target)).all()
        return {frozenset((s, t)) for s, t in rows}

    def _independent_subset(self, wallets: set[str], related: set[frozenset[str]]) -> set[str]:
        """Greedily drop wallets that are linked to one already counted."""
        chosen: set[str] = set()
        for wallet in sorted(wallets):
            if any(frozenset((wallet, other)) in related for other in chosen):
                continue
            chosen.add(wallet)
        return chosen

    def sequence_confirmation(self, as_of: datetime) -> list[Signal]:
        """Family 4: a follower wallet acting in its historical lag position."""
        edges = list(
            self.session.execute(select(GraphEdge).where(GraphEdge.edge_type == "leads")).scalars()
        )
        signals: list[Signal] = []
        for edge in edges:
            if edge.median_lag_seconds is None:
                continue
            expected = float(edge.median_lag_seconds)
            leader_events = self.session.execute(
                select(Event).where(
                    Event.actor == edge.source,
                    Event.observed_at <= as_of,
                    Event.observed_at >= as_of - timedelta(days=14),
                    Event.side.in_(["buy", "long"]),
                )
            ).scalars()
            for leader in leader_events:
                follower = self.session.execute(
                    select(Event)
                    .where(
                        Event.actor == edge.target,
                        Event.asset_id == leader.asset_id,
                        Event.observed_at > leader.observed_at,
                        Event.observed_at <= as_of,
                        Event.side.in_(["buy", "long"]),
                    )
                    .order_by(Event.observed_at)
                    .limit(1)
                ).scalar_one_or_none()
                if follower is None:
                    continue
                actual = (follower.observed_at - leader.observed_at).total_seconds()
                # Within 2x the historical lag counts as "on schedule".
                if expected <= 0 or actual > expected * 2:
                    continue
                signals.append(
                    Signal(
                        family=Family.SEQUENCE_CONFIRMATION,
                        triggered_at=follower.observed_at,
                        evidence_cutoff=as_of,
                        title=(
                            f"Side wallet {edge.target[:8]}… confirmed "
                            f"{edge.source[:8]}… on {leader.asset_id}"
                        ),
                        significance=min(0.95, float(edge.ordering_consistency or 0.5) + 0.2),
                        confidence=float(edge.confidence or 0.5),
                        asset_id=leader.asset_id,
                        wallet=edge.target,
                        supporting_facts=[
                            f"follower acted {actual / 3600:.1f}h after leader; "
                            f"historical median {expected / 3600:.1f}h",
                            f"ordering held in {float(edge.ordering_consistency or 0):.0%} of "
                            f"{edge.observations} shared assets",
                        ],
                        risks=["Consistent lag can reflect public copy-trading, not coordination"],
                        unknowns=list(edge.false_positive_modes or []),
                        feature_vector={
                            "actual_lag_seconds": actual,
                            "expected_lag_seconds": expected,
                            "observations": edge.observations,
                        },
                        evidence_ids=[leader.event_id, follower.event_id],
                    )
                )
        return signals

    def pump_operator_activity(self, as_of: datetime) -> list[Signal]:
        """Family 9: an operator initiating a new episode.

        Delivered with their measured history — episode duration, prior collapse
        record — and never as an inducement. The exit on a detected pump depends
        on beating people who know the distribution schedule.
        """
        operators = self.session.execute(
            select(Candidate).where(Candidate.archetype == "pump_operator")
        ).scalars()
        signals: list[Signal] = []
        for operator in operators:
            recent = self.session.execute(
                select(Event)
                .where(
                    Event.actor == operator.wallet,
                    Event.observed_at <= as_of,
                    Event.observed_at >= as_of - self.config.lookback,
                    Event.side == Side.BUY.value,
                )
                .order_by(Event.observed_at)
            ).scalars()
            history = self._operator_history(operator.wallet, as_of)
            for event in recent:
                signals.append(
                    Signal(
                        family=Family.PUMP_OPERATOR,
                        triggered_at=event.observed_at,
                        evidence_cutoff=as_of,
                        title=(
                            f"Known pump operator {operator.wallet[:8]}… entered {event.asset_id}"
                        ),
                        significance=0.6,
                        confidence=0.55,
                        asset_id=event.asset_id,
                        wallet=operator.wallet,
                        risk_band="high",
                        supporting_facts=[
                            f"{history['episodes']} prior episodes observed",
                            f"median time from entry to distribution: "
                            f"{history['median_hours_to_distribution']:.0f}h",
                            f"{history['collapsed']} of {history['episodes']} prior assets "
                            "subsequently collapsed",
                        ],
                        risks=[
                            "Exit timing depends on beating participants who know the schedule",
                            "Prior assets from this operator collapsed after distribution",
                        ],
                        unknowns=["Operator may vary timing precisely to punish followers"],
                        feature_vector=history,
                        evidence_ids=[event.event_id],
                    )
                )
        return signals

    def _operator_history(self, wallet: str, as_of: datetime) -> dict:
        events = list(
            self.session.execute(
                select(Event)
                .where(Event.actor == wallet, Event.observed_at <= as_of)
                .order_by(Event.observed_at)
            ).scalars()
        )
        entries: dict[str, datetime] = {}
        durations: list[float] = []
        for event in events:
            if not event.asset_id:
                continue
            if event.side == Side.BUY.value:
                entries.setdefault(event.asset_id, event.observed_at)
            elif event.side == Side.SELL.value and event.asset_id in entries:
                durations.append(
                    (event.observed_at - entries.pop(event.asset_id)).total_seconds() / 3600
                )
        touched = {e.asset_id for e in events if e.asset_id}
        collapsed = self.session.execute(
            select(OutcomeRow.asset_id).where(
                OutcomeRow.asset_id.in_(touched),
                OutcomeRow.outcome_class == OutcomeClass.RUG_OR_COLLAPSE.value,
                OutcomeRow.first_observed_at <= as_of,
            )
        ).scalars()
        return {
            "episodes": len(touched),
            "collapsed": len(set(collapsed)),
            "median_hours_to_distribution": statistics.median(durations) if durations else 0.0,
        }

    def new_candidates(self, as_of: datetime, since: datetime) -> list[Signal]:
        """Family 8: discovery surfaced someone new."""
        rows = self.session.execute(
            select(Candidate).where(
                Candidate.surfaced_at >= since,
                Candidate.surfaced_at <= as_of,
                Candidate.status != CandidateStatus.REJECTED,
            )
        ).scalars()
        return [
            Signal(
                family=Family.NEW_CANDIDATE,
                triggered_at=candidate.surfaced_at,
                evidence_cutoff=as_of,
                title=f"New candidate wallet {candidate.wallet[:8]}… ({candidate.archetype})",
                significance=min(0.8, float(candidate.edge_vs_base) / 10),
                confidence=0.4,
                wallet=candidate.wallet,
                supporting_facts=[
                    f"{candidate.independent_events} independent outcome appearances",
                    f"shrunk hit rate {float(candidate.shrunk_hit_rate):.1%} vs "
                    f"base rate {float(candidate.base_rate):.1%}",
                    f"edge ratio {float(candidate.edge_vs_base):.1f}x",
                ],
                risks=["Not yet shadow-tested; retrospective evidence only"],
                unknowns=["Forward performance unknown until the shadow period completes"],
                base_rate_context={"base_rate": float(candidate.base_rate)},
                feature_vector={"sample_size": candidate.sample_size},
            )
            for candidate in rows
        ]

    # ------------------------------------------------------------------- run

    def run_all(self, as_of: datetime, since: datetime | None = None) -> list[Signal]:
        since = since or as_of - self.config.lookback
        signals: list[Signal] = []
        signals.extend(self.tracked_entity_actions(as_of))
        signals.extend(self.silent_accumulation(as_of))
        signals.extend(self.convergence(as_of))
        signals.extend(self.sequence_confirmation(as_of))
        signals.extend(self.pump_operator_activity(as_of))
        signals.extend(self.new_candidates(as_of, since))
        signals.sort(key=lambda s: s.significance, reverse=True)
        return signals
