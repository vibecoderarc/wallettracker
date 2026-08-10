"""Playbook / sequence engine (spec §7).

Insider behaviour is frequently procedural rather than discretionary: probe,
abort, dormancy, resting bid, real entry, side-wallet confirmation, event. A
point-in-time single-event detector cannot express that, which is why v1's
signal families would have produced "a wallet bought a token" and nothing more.

The alert this module exists to produce is:

    Entity X entered stage 4 of its 5-stage sequence on asset Y. Historically
    this stage preceded a listing by 2-4 weeks in 6 of 8 prior sequences.
    Two prior sequences aborted at this stage.

The abort count is not a footnote. An entity that opens, aborts, and returns
weeks later is displaying its actual pattern, and a system that only reports the
successes is lying by omission.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from alphagraph.core.events import EventType, Side
from alphagraph.core.outcomes import PREDICTIVE_CLASSES
from alphagraph.db.models import Event, OutcomeRow, Playbook, PlaybookState

PLAYBOOK_VERSION = "v1"


class Stage:
    DORMANT = "dormant"
    PROBING = "probing"
    ABORTED = "aborted"
    BIDDING = "bidding"
    ACCUMULATING = "accumulating"
    CONFIRMING = "confirming"
    DISTRIBUTING = "distributing"


#: Canonical ordering used to report "stage N of M".
STAGE_ORDER = [
    Stage.DORMANT,
    Stage.PROBING,
    Stage.BIDDING,
    Stage.ACCUMULATING,
    Stage.CONFIRMING,
    Stage.DISTRIBUTING,
]


@dataclass(frozen=True, slots=True)
class PlaybookConfig:
    #: A position under this fraction of the wallet's median size is a probe,
    #: not a real entry. Relative, never absolute — a $500 probe from a wallet
    #: that normally trades $50k is the signal, and an absolute floor deletes it.
    probe_size_ratio: float = 0.15
    #: Window in which stages are considered part of one sequence.
    sequence_window: timedelta = timedelta(days=45)
    min_sequences_to_publish: int = 3


@dataclass
class StageObservation:
    stage: str
    at: datetime
    event_id: str
    detail: str = ""


@dataclass
class Sequence:
    """One observed run through an entity's procedure on a single asset."""

    entity_id: str
    asset_id: str
    observations: list[StageObservation] = field(default_factory=list)
    completed: bool = False
    aborted: bool = False
    outcome_class: str | None = None
    outcome_t0: datetime | None = None

    @property
    def stages(self) -> list[str]:
        seen: list[str] = []
        for observation in self.observations:
            if observation.stage not in seen:
                seen.append(observation.stage)
        return seen

    @property
    def started_at(self) -> datetime | None:
        return self.observations[0].at if self.observations else None

    def lead_time_days(self) -> float | None:
        if self.outcome_t0 is None or not self.observations:
            return None
        return (self.outcome_t0 - self.observations[0].at).total_seconds() / 86400


def classify_event(event: Event, median_size: float) -> str | None:
    """Map one event onto a playbook stage."""
    if event.event_type == EventType.ORDER_PLACED.value and not event.filled:
        # A resting bid below market that never fills. In the CASHCAT sequence
        # this was the loudest public signal and it moved no funds at all.
        return Stage.BIDDING
    if event.event_type == EventType.LIQUIDATION.value:
        return Stage.ABORTED
    if event.event_type in {EventType.POSITION_CLOSED.value}:
        return Stage.ABORTED

    size = float(event.usd_value) if event.usd_value is not None else 0.0
    is_acquire = (event.event_type == EventType.SWAP.value and event.side == Side.BUY.value) or (
        event.event_type in {EventType.POSITION_OPENED.value, EventType.POSITION_INCREASED.value}
    )
    if is_acquire:
        if median_size > 0 and size < median_size * PlaybookConfig().probe_size_ratio:
            return Stage.PROBING
        return Stage.ACCUMULATING
    if event.event_type == EventType.SWAP.value and event.side == Side.SELL.value:
        return Stage.DISTRIBUTING
    return None


class PlaybookEngine:
    def __init__(self, session: Session, config: PlaybookConfig | None = None) -> None:
        self.session = session
        self.config = config or PlaybookConfig()

    # ------------------------------------------------------------------ mine

    def mine(self, entity_id: str, wallets: list[str], as_of: datetime) -> list[Sequence]:
        """Reconstruct historical sequences for an entity, per asset."""
        events = list(
            self.session.execute(
                select(Event)
                .where(Event.actor.in_(wallets), Event.observed_at <= as_of)
                .order_by(Event.observed_at)
            ).scalars()
        )
        if not events:
            return []

        sizes = [float(e.usd_value) for e in events if e.usd_value is not None]
        median_size = statistics.median(sizes) if sizes else 0.0

        by_asset: dict[str, list[Event]] = defaultdict(list)
        for event in events:
            if event.asset_id:
                by_asset[event.asset_id].append(event)

        outcomes = self._outcomes(as_of)
        sequences: list[Sequence] = []

        for asset_id, asset_events in by_asset.items():
            sequence = Sequence(entity_id=entity_id, asset_id=asset_id)
            for event in asset_events:
                stage = classify_event(event, median_size)
                if stage is None:
                    continue
                # The primary wallet leads; a different member acting after the
                # primary is the confirmation stage, not another accumulation.
                if stage == Stage.ACCUMULATING and event.actor != wallets[0]:
                    stage = Stage.CONFIRMING
                sequence.observations.append(
                    StageObservation(
                        stage=stage,
                        at=event.observed_at,
                        event_id=event.event_id,
                        detail=f"{event.actor[:8]} {event.event_type}",
                    )
                )
            if not sequence.observations:
                continue

            relevant = [
                row
                for row in outcomes.get(asset_id, [])
                if row.outcome_class in {c.value for c in PREDICTIVE_CLASSES}
            ]
            if relevant:
                first = min(relevant, key=lambda r: r.t0)
                sequence.outcome_class = first.outcome_class
                sequence.outcome_t0 = first.t0
                sequence.completed = True
            else:
                sequence.aborted = Stage.ABORTED in sequence.stages
            sequences.append(sequence)

        return sequences

    def _outcomes(self, as_of: datetime) -> dict[str, list[OutcomeRow]]:
        rows = self.session.execute(
            select(OutcomeRow).where(OutcomeRow.first_observed_at <= as_of)
        ).scalars()
        grouped: dict[str, list[OutcomeRow]] = defaultdict(list)
        for row in rows:
            grouped[row.asset_id].append(row)
        return grouped

    # --------------------------------------------------------------- publish

    def build_playbook(self, entity_id: str, sequences: list[Sequence]) -> Playbook | None:
        """Summarise mined sequences into a proposed playbook.

        Returns None below `min_sequences_to_publish`. A pattern mined from two
        sequences is a coincidence with extra steps, and publishing it would
        manufacture false confidence in exactly the place it does most damage.
        """
        completed = [s for s in sequences if s.completed]
        aborted = [s for s in sequences if s.aborted and not s.completed]
        if len(completed) < self.config.min_sequences_to_publish:
            return None

        stage_counts: dict[str, int] = defaultdict(int)
        for sequence in completed:
            for stage in sequence.stages:
                stage_counts[stage] += 1
        stages = [s for s in STAGE_ORDER if stage_counts.get(s, 0) >= len(completed) * 0.5]

        lags: dict[str, list[float]] = defaultdict(list)
        for sequence in completed:
            if sequence.outcome_t0 is None:
                continue
            first_at: dict[str, datetime] = {}
            for observation in sequence.observations:
                first_at.setdefault(observation.stage, observation.at)
            for stage, at in first_at.items():
                lags[stage].append((sequence.outcome_t0 - at).total_seconds() / 86400)

        playbook = Playbook(
            entity_id=entity_id,
            version=PLAYBOOK_VERSION,
            stages=stages,
            supporting_sequences=len(completed),
            aborted_sequences=len(aborted),
            median_stage_lags={
                stage: round(statistics.median(values), 2) for stage, values in lags.items()
            },
            confirmed_by_operator=False,
        )
        self.session.add(playbook)
        self.session.flush()
        return playbook

    # ----------------------------------------------------------- live state

    def current_stage(
        self, entity_id: str, wallets: list[str], asset_id: str, as_of: datetime
    ) -> PlaybookState | None:
        events = list(
            self.session.execute(
                select(Event)
                .where(
                    Event.actor.in_(wallets),
                    Event.asset_id == asset_id,
                    Event.observed_at <= as_of,
                    Event.observed_at >= as_of - self.config.sequence_window,
                )
                .order_by(Event.observed_at)
            ).scalars()
        )
        if not events:
            return None
        sizes = [float(e.usd_value) for e in events if e.usd_value is not None]
        median_size = statistics.median(sizes) if sizes else 0.0

        stage: str | None = None
        entered_at: datetime | None = None
        for event in events:
            candidate = classify_event(event, median_size)
            if candidate is None:
                continue
            if candidate == Stage.ACCUMULATING and event.actor != wallets[0]:
                candidate = Stage.CONFIRMING
            stage, entered_at = candidate, event.observed_at

        if stage is None or entered_at is None:
            return None
        index = STAGE_ORDER.index(stage) + 1 if stage in STAGE_ORDER else 0
        state = PlaybookState(
            entity_id=entity_id,
            asset_id=asset_id,
            stage=stage,
            stage_index=index,
            entered_at=entered_at,
            confidence=0.6,
        )
        self.session.add(state)
        self.session.flush()
        return state

    def stage_base_rate(self, playbook: Playbook, stage: str) -> tuple[int, int]:
        """(sequences reaching this stage that completed, total sequences).

        Feeds the "6 of 8 prior sequences" language in alerts. Reported with the
        abort count so the failure mode is always visible next to the success.
        """
        total = playbook.supporting_sequences + playbook.aborted_sequences
        return playbook.supporting_sequences, total
